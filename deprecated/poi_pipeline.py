"""POI 获取管线 — 高德 API 搜索 + 本地 DB 推荐 + 过滤."""
import math
import time

from app.config import USE_POI_DB
from app.providers.amap_provider import geocode, AmapAPIError
from app.providers.provider import search_poi, search_around, search_along_route
from app.algorithms.poi_filter import normalize_keywords, filter_by_category, filter_by_coords, filter_near_anchor, deduplicate_by_name
from app.shared.utils import _progress

from db.connection import get_conn
from db.repository import _row_to_dict
from db.cluster import query_clusters
from app.recommender.engine import recommend
from app.shared.constants import KW_NORMALIZE


def execute_poi_search(strategy_regions: list, city: str,
                       origin_coords=None, dest_coords=None) -> list:
    """执行搜索策略，返回去重后的 POI 列表."""
    all_pois = []
    for region in strategy_regions:
        normalized_kws = normalize_keywords(region.keywords)
        if not normalized_kws:
            normalized_kws = ["美食", "景点"]

        use_around, loc_str = False, ""
        try:
            gc = geocode(region.center, city)
            if "lng" in gc and "lat" in gc:
                loc_str = f"{gc['lng']},{gc['lat']}"
                use_around = True
        except AmapAPIError:
            pass

        for kw in normalized_kws[:3]:
            try:
                if use_around:
                    pois = search_around(loc_str, kw, radius=min(region.radius, 5000), limit=10)
                else:
                    pois = search_poi(keywords=kw, location=city, limit=10)
            except AmapAPIError:
                continue
            _progress("   →", f"「{region.center}」搜到 {len(pois)} 个「{kw}」")
            all_pois.extend(pois)
            time.sleep(0.05)

    # 沿途补充搜索
    if origin_coords and dest_coords:
        o_str = f"{origin_coords[1]},{origin_coords[0]}"
        d_str = f"{dest_coords[1]},{dest_coords[0]}"
        for region in strategy_regions:
            for kw in region.keywords[:2]:
                try:
                    pois = search_along_route(o_str, d_str, kw, radius=2000, limit=10)
                    all_pois.extend(pois)
                except AmapAPIError:
                    pass
                time.sleep(0.05)
    elif origin_coords:
        o_str = f"{origin_coords[1]},{origin_coords[0]}"
        for region in strategy_regions:
            for kw in region.keywords[:2]:
                try:
                    pois = search_around(o_str, kw, radius=5000, limit=10)
                    all_pois.extend(pois)
                except AmapAPIError:
                    pass
                time.sleep(0.05)
    elif dest_coords:
        d_str = f"{dest_coords[1]},{dest_coords[0]}"
        for region in strategy_regions:
            for kw in region.keywords[:2]:
                try:
                    pois = search_around(d_str, kw, radius=5000, limit=10)
                    all_pois.extend(pois)
                except AmapAPIError:
                    pass
                time.sleep(0.05)

    return deduplicate_by_name(all_pois)


def recommend_pois_from_db(origin_coords, dest_coords, intent_result,
                           city: str = "") -> list:
    """使用预计算聚类 + 推荐引擎从本地 DB 召回 + 排序 POI."""
    if not origin_coords and not dest_coords:
        return []

    o_lat = origin_coords[0] if origin_coords else dest_coords[0]
    o_lng = origin_coords[1] if origin_coords else dest_coords[1]
    d_lat = dest_coords[0] if dest_coords else None
    d_lng = dest_coords[1] if dest_coords else None

    with get_conn() as conn:
        seen_amap = set()
        all_rows = []

        def _add_rows(rows):
            for r in rows:
                if r["amap_id"] not in seen_amap:
                    seen_amap.add(r["amap_id"])
                    all_rows.append(r)

        # 1. 起点附近预计算簇
        for c in query_clusters(o_lat, o_lng, limit=5):
            c_rows = conn.execute(
                "SELECT * FROM pois WHERE cluster_id = ? AND lat IS NOT NULL LIMIT 60",
                (c["cluster_id"],),
            ).fetchall()
            _add_rows(c_rows)

        # 2. 终点附近预计算簇
        if d_lat is not None:
            for c in query_clusters(d_lat, d_lng, limit=5):
                c_rows = conn.execute(
                    "SELECT * FROM pois WHERE cluster_id = ? AND lat IS NOT NULL LIMIT 60",
                    (c["cluster_id"],),
                ).fetchall()
                _add_rows(c_rows)

        # 3. 走廊 bbox
        margin_km = 5.0
        if d_lat is not None:
            lat_min = min(o_lat, d_lat) - margin_km / 111.32
            lat_max = max(o_lat, d_lat) + margin_km / 111.32
            mid_lat = (o_lat + d_lat) / 2
            lng_span = margin_km / (111.32 * math.cos(math.radians(mid_lat)))
            lng_min = min(o_lng, d_lng) - lng_span
            lng_max = max(o_lng, d_lng) + lng_span
        else:
            lat_span = margin_km / 111.32
            lng_span = margin_km / (111.32 * math.cos(math.radians(o_lat)))
            lat_min, lat_max = o_lat - lat_span, o_lat + lat_span
            lng_min, lng_max = o_lng - lng_span, o_lng + lng_span

        corridor_rows = conn.execute("""
            SELECT * FROM pois
            WHERE lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?
              AND lat IS NOT NULL
            ORDER BY rating DESC NULLS LAST
            LIMIT 300
        """, (lat_min, lat_max, lng_min, lng_max)).fetchall()
        _add_rows(corridor_rows)

        pois = [_row_to_dict(r) for r in all_rows]

    n_clusters = len({p.get("cluster_id") for p in pois if p.get("cluster_id") is not None})
    _progress("📊", f"从 DB 加载 {len(pois)} 个 POI（{n_clusters} 个预计算簇）")

    # 按预计算 cluster_id 构造簇
    clusters_by_id = {}
    for p in pois:
        cid = p.get("cluster_id")
        if cid is not None:
            if cid not in clusters_by_id:
                clusters_by_id[cid] = {"pois": [], "lats": [], "lngs": []}
            clusters_by_id[cid]["pois"].append(p)
            clusters_by_id[cid]["lats"].append(p["lat"])
            clusters_by_id[cid]["lngs"].append(p["lng"])

    clusters = [
        {
            "cluster_id": cid,
            "center_lat": sum(c["lats"]) / len(c["lats"]),
            "center_lng": sum(c["lngs"]) / len(c["lngs"]),
            "size": len(c["pois"]),
            "pois": c["pois"],
        }
        for cid, c in clusters_by_id.items()
        if len(c["pois"]) >= 2
    ]
    _progress("   →", f"{len(clusters)} 个预计算簇（无需实时聚类）")

    # 推荐引擎
    up = intent_result.user_profile
    user_prefs = {
        "interests": up.interests or intent_result.keywords or [],
        "budget_level": up.budget_level,
        "energy_level": up.energy_level,
        "group_type": up.group_type,
    }
    target_cats = []
    for kw in (intent_result.keywords or []):
        expanded = KW_NORMALIZE.get(kw, kw)
        target_cats.extend(expanded.split(","))

    ranked = recommend(
        pois, o_lat, o_lng,
        d_lat=d_lat, d_lng=d_lng,
        clusters=clusters,
        target_categories=target_cats or ["美食", "景点"],
        user_prefs=user_prefs,
        top_k=min(50, len(pois)),
    )
    _progress("✅", f"推荐引擎返回 {len(ranked)} 个 POI")
    return ranked


def filter_and_validate(all_pois: list, origin_name: str, dest_name: str,
                        origin_coords=None, dest_coords=None) -> list:
    """过滤 POI."""
    filtered = filter_by_category(all_pois)
    filtered = filter_by_coords(filtered)
    filtered = filter_near_anchor(filtered, origin_coords, origin_name)
    if dest_coords and dest_name:
        filtered = filter_near_anchor(filtered, dest_coords, dest_name)
    return filtered
