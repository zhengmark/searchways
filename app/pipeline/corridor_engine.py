"""走廊引擎 — 加载候选 POI、计算走廊形状、生成推荐理由.

提供 build_corridor()，在 LLM 选定簇之后、用户交互编辑之前调用.
输出 corridor_pois + corridor_shape + cluster_markers 供前端渲染.
"""

import math as _math

from app.algorithms.geo import haversine, project_ratio
from app.pipeline.reason_engine import generate_poi_reasons
from db.connection import get_conn

# 公里 → 度换算
_LAT_PER_KM = 1.0 / 111.32


def _lng_per_km(lat: float) -> float:
    return 1.0 / (111.32 * _math.cos(_math.radians(lat)))


def _row_to_dict(row) -> dict:
    """sqlite3.Row → dict."""
    return dict(row)


def build_corridor(
    origin_coords: tuple[float, float],
    dest_coords: tuple[float, float] | None,
    cluster_ids: list[int],
    keywords: list[str] = None,
    budget: str = None,
    corridor_width_km: float = 5.0,
) -> dict:
    """构建走廊数据：加载 POI + 计算元数据 + 生成推荐理由.

    Returns:
        {"corridor_pois": [...], "cluster_markers": [...], "corridor_shape": [[lat,lng],...]}
    """
    if not cluster_ids:
        return {"corridor_pois": [], "cluster_markers": [], "corridor_shape": []}

    db_ids = [c for c in cluster_ids if c != -1]

    # ── 加载簇内全部 POI ──
    pois = []
    cluster_centers_map = {}

    if db_ids:
        with get_conn() as conn:
            placeholders = ",".join("?" * len(db_ids))

            # 簇元数据（中心坐标）
            meta_rows = conn.execute(
                f"""
                SELECT cluster_id, center_lat, center_lng, size
                FROM cluster_meta
                WHERE cluster_id IN ({placeholders})
            """,
                db_ids,
            ).fetchall()
            for r in meta_rows:
                cluster_centers_map[r["cluster_id"]] = {
                    "lat": r["center_lat"],
                    "lng": r["center_lng"],
                    "size": r["size"],
                }

            # POI 全量加载
            poi_rows = conn.execute(
                f"""
                SELECT id, name, lat, lng, category, subcategory,
                       rating, price_per_person, address, cluster_id, district
                FROM pois
                WHERE cluster_id IN ({placeholders})
                  AND lat IS NOT NULL
                ORDER BY rating DESC NULLS LAST
            """,
                db_ids,
            ).fetchall()

        pois = [_row_to_dict(r) for r in poi_rows]

    if not pois:
        return {"corridor_pois": [], "cluster_markers": [], "corridor_shape": []}

    # ── 计算每个 POI 的投影和垂直距离 ──
    origin = {"lat": origin_coords[0], "lng": origin_coords[1]}
    dest = None
    if dest_coords:
        dest = {"lat": dest_coords[0], "lng": dest_coords[1]}

    for p in pois:
        if dest:
            p["projection_ratio"] = round(project_ratio(p["lat"], p["lng"], origin, dest), 3)
            p["perpendicular_km"] = round(_perpendicular_distance(p["lat"], p["lng"], origin, dest), 2)
        else:
            # 无终点：仅算距起点距离用来模拟投影
            d = haversine(origin_coords[0], origin_coords[1], p["lat"], p["lng"])
            p["projection_ratio"] = round(min(d / 10000, 1.0), 3)
            p["perpendicular_km"] = 0.0

        # 生成推荐理由
        p["recommendation_reasons"] = generate_poi_reasons(p, keywords or [], budget, origin_coords)

        # 分配唯一 ID
        p["poi_id"] = f"{p.get('cluster_id', 0)}_{p.get('id', 0)}"

    # ── 构建簇标记列表 ──
    cluster_markers = []
    for cid in db_ids:
        meta = cluster_centers_map.get(cid)
        if meta:
            cluster_markers.append(
                {
                    "cluster_id": cid,
                    "lat": meta["lat"],
                    "lng": meta["lng"],
                    "name": _cluster_label(cid, pois),
                    "poi_count": meta.get("size", 0),
                }
            )

    # ── 计算走廊形状 ──
    # 收集所有簇中心
    centers = [(m["lat"], m["lng"]) for m in cluster_markers]
    if dest_coords:
        corridor_shape = compute_corridor_shape(centers, origin_coords, dest_coords, padding_km=corridor_width_km / 2)
    else:
        # 无终点时：以起点为中心生成圆形包络（半径取簇中心最大距离 + padding）
        max_dist = 3.0  # 默认 3km
        for clat, clng in centers:
            d = haversine(origin_coords[0], origin_coords[1], clat, clng) / 1000.0
            if d > max_dist:
                max_dist = d
        radius_deg = (max_dist + corridor_width_km / 2) * _LAT_PER_KM
        o_lat, o_lng = origin_coords
        steps = 12
        corridor_shape = []
        for i in range(steps + 1):
            angle = 2 * _math.pi * i / steps
            corridor_shape.append(
                [
                    o_lat + radius_deg * _math.sin(angle),
                    o_lng + radius_deg * _math.cos(angle) / _math.cos(_math.radians(o_lat)),
                ]
            )

    return {
        "corridor_pois": _build_corridor_poi_dicts(pois),
        "cluster_markers": cluster_markers,
        "corridor_shape": corridor_shape,
    }


def _build_corridor_poi_dicts(pois: list) -> list[dict]:
    """转换为前端友好的 POI 字典列表."""
    result = []
    for p in pois:
        result.append(
            {
                "id": p.get("poi_id", ""),
                "name": p.get("name", ""),
                "lat": p.get("lat"),
                "lng": p.get("lng"),
                "category": p.get("category", ""),
                "rating": p.get("rating"),
                "price_per_person": p.get("price_per_person"),
                "address": p.get("address", ""),
                "cluster_id": p.get("cluster_id", 0),
                "projection_ratio": p.get("projection_ratio", 0),
                "perpendicular_km": p.get("perpendicular_km", 0),
                "recommendation_reasons": p.get("recommendation_reasons", {}),
                "selected": False,
            }
        )
    return result


def _cluster_label(cluster_id: int, pois: list) -> str:
    """从簇内 POI 生成简短标签."""
    cluster_pois = [p for p in pois if p.get("cluster_id") == cluster_id]
    if not cluster_pois:
        return f"区域{cluster_id}"

    # 取最常见的子品类
    cats = {}
    for p in cluster_pois:
        cat = p.get("subcategory", "") or p.get("category", "")
        if cat:
            short = cat.split(";")[-1] if ";" in cat else cat
            cats[short] = cats.get(short, 0) + 1
    top_cat = max(cats, key=cats.get) if cats else ""

    # 取第一个 POI 名的前几个字
    names = sorted(cluster_pois, key=lambda x: x.get("rating") or 0, reverse=True)
    first_name = names[0].get("name", "") if names else ""
    for sep in ["(", "（", "·", "—", "-"]:
        if sep in first_name:
            first_name = first_name.split(sep)[0]
    short_name = first_name[:6]

    district = cluster_pois[0].get("district", "")
    if district:
        suffix = f"·{top_cat}" if top_cat else ""
        return f"{district}{suffix}"
    if short_name:
        suffix = f"·{top_cat}" if top_cat else ""
        return f"{short_name}{suffix}"
    return top_cat or f"区域{cluster_id}"


def _perpendicular_distance(lat: float, lng: float, origin: dict, dest: dict) -> float:
    """计算点到 OD 线段的垂直距离（km）."""
    # 投影后的点
    t = project_ratio(lat, lng, origin, dest)
    proj_lat = origin["lat"] + t * (dest["lat"] - origin["lat"])
    proj_lng = origin["lng"] + t * (dest["lng"] - origin["lng"])
    return haversine(lat, lng, proj_lat, proj_lng) / 1000.0


def compute_corridor_shape(
    cluster_centers: list[tuple[float, float]],
    origin: tuple[float, float],
    dest: tuple[float, float] | None,
    padding_km: float = 2.5,
) -> list[list[float]]:
    """计算走廊包络多边形（GeoJSON 格式）.

    围绕起终点连线构建缓冲矩形，宽度取簇中心最大偏离距离 + padding.
    """
    if not dest:
        return []

    o_lat, o_lng = origin
    d_lat, d_lng = dest

    mid_lat = (o_lat + d_lat) / 2

    # OD 方向向量（度）
    dlat, dlng = d_lat - o_lat, d_lng - o_lng
    # 转为 km
    dlat_km = dlat * 111.32
    dng_per_km = 1.0 / _lng_per_km(mid_lat) if abs(_math.cos(_math.radians(mid_lat))) > 0.01 else 111.32
    dlng_km = dlng / dng_per_km
    length_km = _math.sqrt(dlat_km**2 + dlng_km**2)
    if length_km < 0.001:
        return []

    # 单位向量（km 空间）
    u_lat = dlat_km / length_km
    u_lng = dlng_km / length_km

    # 垂直单位向量（逆时针旋转 90°）
    p_lat = -u_lng
    p_lng = u_lat

    # 半宽：取簇中心最大垂直距离 + padding
    max_perp = padding_km
    origin_p = {"lat": o_lat, "lng": o_lng}
    dest_p = {"lat": d_lat, "lng": d_lng}
    for clat, clng in cluster_centers:
        perp = _perpendicular_distance(clat, clng, origin_p, dest_p)
        if perp > max_perp:
            max_perp = perp
    half_w = max_perp + padding_km

    # 四个角点（在 lat/lng 空间）
    def _offset(lat, lng, dlat_off, dlng_off):
        return [
            lat + dlat_off * _LAT_PER_KM,
            lng + dlng_off * _lng_per_km(mid_lat),
        ]

    # 上半部分（逆时针方向）
    top_left = _offset(o_lat, o_lng, p_lat * half_w, p_lng * half_w)
    top_right = _offset(d_lat, d_lng, p_lat * half_w, p_lng * half_w)
    # 下半部分
    bot_right = _offset(d_lat, d_lng, -p_lat * half_w, -p_lng * half_w)
    bot_left = _offset(o_lat, o_lng, -p_lat * half_w, -p_lng * half_w)

    # 形成闭合多边形
    return [top_left, top_right, bot_right, bot_left, top_left]
