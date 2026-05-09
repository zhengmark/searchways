"""聚类预计算 — 网格法对全量 POI 做地理簇标记，缓存到 DB.

用法:
    python3 -m db.cluster build           # 全量重算聚类
    python3 -m db.cluster stats           # 查看聚类统计
    python3 -m db.cluster query 34.26 108.94  # 查询坐标附近簇
    python3 -m db.cluster corridor 34.19 108.85 34.26 108.94  # 走廊簇查询

网格法: 0.01°×0.01°（约 1km），每格 ≥3 POI 即为一个簇。
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.algorithms.geo import haversine
from db.connection import get_conn

# 用户关键词 → 品类子串映射（用于聚簇过滤）
_KEYWORD_TO_SUBCAT = {
    "美食": ["餐饮", "中餐", "火锅", "烧烤", "小吃", "面馆", "日料", "西餐", "海鲜", "串串", "麻辣烫"],
    "咖啡": ["咖啡"],
    "茶饮": ["茶饮", "茶"],
    "奶茶": ["奶茶", "茶饮"],
    "火锅": ["火锅"],
    "烧烤": ["烧烤", "烤肉"],
    "小吃": ["小吃"],
    "面馆": ["面馆", "中餐"],
    "粤菜": ["粤菜", "中餐"],
    "川菜": ["川菜", "中餐"],
    "日料": ["日料", "日本"],
    "西餐": ["西餐", "牛排", "披萨"],
    "海鲜": ["海鲜"],
    "酒吧": ["酒吧", "酒"],
    "甜品": ["甜品", "蛋糕", "面包"],
    "景点": ["风景名胜", "旅游景点", "公园", "寺庙", "博物馆", "景点"],
    "公园": ["公园", "风景名胜"],
    "博物馆": ["博物馆"],
    "购物": ["购物", "商场", "购物中心", "服装", "专卖", "便利店"],
    "商场": ["商场", "购物中心"],
    "休闲": ["娱乐", "KTV", "影院", "剧场", "棋牌"],
    "KTV": ["KTV"],
    "影院": ["影院", "电影院", "剧场"],
    "图书馆": ["图书馆"],
    "书店": ["书店"],
    "文化": ["文化", "博物馆", "图书馆", "美术馆"],
    "酒店": ["酒店", "宾馆", "住宿"],
    "洗浴": ["洗浴", "足疗", "按摩"],
    "运动": ["运动", "健身", "游泳"],
    "夜市": ["夜市", "小吃", "烧烤", "火锅", "串串"],
    "清真": ["清真", "清真菜馆", "西北", "小吃", "泡馍", "烧烤"],
    "回民街": ["清真", "泡馍", "小吃", "夜市", "烧烤", "火锅", "串串"],
    "拍照": ["景点", "咖啡", "甜品", "公园", "图书馆"],
    "约会": ["咖啡", "甜品", "景点", "公园", "小吃", "西餐", "日料"],
    "安静": ["咖啡", "图书馆", "茶馆", "公园", "书店"],
    "泡馍": ["泡馍", "西北", "清真", "面馆", "中餐"],
}

# 预算范围
_BUDGET_RANGES = {"low": (0, 40), "medium": (30, 100), "high": (80, 9999)}

# 非旅游品类黑名单 — 只过滤所有品类都是无关杂项的簇
_NON_TOURIST_CATS = {
    "便民商店/便利店", "便民商店", "便利店", "烟酒专卖店",
    "专营店", "汽车", "维修", "中介", "房产", "打印", "图文", "广告",
    "快印", "印刷", "洗车", "药店", "诊所", "医院", "培训",
    "服装鞋帽皮具店", "棋牌室",
}


def _keyword_matches_subcats(keywords: list, top_cats: list, top_names: list) -> bool:
    """检查关键词列表是否匹配簇的品类或 POI 名称."""
    for kw in keywords:
        kw_lower = kw.lower()
        # 收集所有需要匹配的词：原始关键词 + 映射词
        mapped_terms = _KEYWORD_TO_SUBCAT.get(kw, [kw])
        all_terms = [kw] + mapped_terms
        for term in all_terms:
            term_lower = term.lower()
            # 检查品类匹配
            for cat in top_cats:
                if term_lower in cat.lower():
                    return True
            # 检查 POI 名称匹配（扩展：映射词也查名称）
            for name in top_names:
                if term_lower in name.lower():
                    return True
    return False


def _cell_key(lat: float, lng: float, resolution: float = 0.01) -> str:
    """计算 POI 所属网格 key."""
    col = int(lng / resolution)
    row = int(lat / resolution)
    return f"{row},{col}"


def _cell_center(key: str, resolution: float = 0.01) -> tuple:
    """从网格 key 计算中心坐标."""
    row, col = key.split(",")
    return (int(row) + 0.5) * resolution, (int(col) + 0.5) * resolution


def build_clusters(min_samples: int = 3, resolution: float = 0.01):
    """全量重算聚类：按网格分组 → 每个满员格为一个簇."""
    with get_conn() as conn:
        # 重置
        conn.execute("UPDATE pois SET cluster_id = NULL")

        # 加载所有有坐标的 POI
        rows = conn.execute(
            "SELECT id, lat, lng FROM pois WHERE lat IS NOT NULL AND lng IS NOT NULL"
        ).fetchall()
        print(f"加载 {len(rows)} 条 POI")

        # 按网格分组
        cells = {}
        for r in rows:
            key = _cell_key(r["lat"], r["lng"], resolution)
            cells.setdefault(key, []).append(r["id"])

        # 满员格 → 簇
        cluster_id = 0
        total_assigned = 0
        for key, ids in sorted(cells.items()):
            if len(ids) >= min_samples:
                cluster_id += 1
                placeholders = ",".join("?" * len(ids))
                conn.execute(
                    f"UPDATE pois SET cluster_id = ? WHERE id IN ({placeholders})",
                    [cluster_id] + ids,
                )
                total_assigned += len(ids)

        conn.commit()
        print(f"完成：{cluster_id} 个簇，覆盖 {total_assigned} 条 POI ({total_assigned/len(rows)*100:.1f}%)")

        # 保存簇元数据
        conn.execute("DROP TABLE IF EXISTS cluster_meta")
        conn.execute("""
            CREATE TABLE cluster_meta (
                cluster_id INTEGER PRIMARY KEY,
                center_lat REAL NOT NULL,
                center_lng REAL NOT NULL,
                size INTEGER NOT NULL
            )
        """)
        conn.execute("""
            INSERT INTO cluster_meta (cluster_id, center_lat, center_lng, size)
            SELECT cluster_id, AVG(lat), AVG(lng), COUNT(*)
            FROM pois WHERE cluster_id IS NOT NULL
            GROUP BY cluster_id
        """)
        conn.commit()


def query_clusters(lat: float, lng: float, limit: int = 10) -> list:
    """查询距离坐标最近的几个簇."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT cluster_id, center_lat, center_lng, size,
                   ((center_lat - ?) * (center_lat - ?) + (center_lng - ?) * (center_lng - ?)) AS dist2
            FROM cluster_meta
            ORDER BY dist2 ASC
            LIMIT ?
        """, (lat, lat, lng, lng, limit)).fetchall()
        return [dict(r) for r in rows]


def _project_ratio(lat, lng, o_lat, o_lng, d_lat, d_lng):
    """计算点在 OD 线段上的投影比例 0-1。0=起点, 1=终点."""
    if d_lat is None or d_lng is None:
        return 0.0
    dx = d_lat - o_lat
    dy = d_lng - o_lng
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return 0.0
    t = ((lat - o_lat) * dx + (lng - o_lng) * dy) / (dx * dx + dy * dy)
    return max(0.0, min(1.0, t))


def query_corridor_clusters(origin_lat: float, origin_lng: float,
                              dest_lat: float = None, dest_lng: float = None,
                              keywords: list = None, budget: str = None,
                              corridor_width_km: float = 6.0) -> list:
    """查询起终点走廊内的 POI 聚簇，返回 LLM 可理解的簇摘要.

    每个簇包含:
    - cluster_id, name (基于商圈/品类自动生成)
    - center_lat, center_lng
    - dist_from_origin_km
    - poi_count
    - top_cats (top 3 子品类)
    - avg_rating, avg_price
    - top_poi_names (top 3 最高评分 POI)

    过滤:
    - keywords: 只返回品类匹配的簇（如 ["美食", "咖啡"]）
    - budget: "low" / "medium" / "high"，匹配人均消费
    """
    _LAT_PER_KM = 1.0 / 111.32

    # 计算走廊边界框
    if dest_lat is not None and dest_lng is not None:
        lat_min = min(origin_lat, dest_lat) - corridor_width_km * _LAT_PER_KM
        lat_max = max(origin_lat, dest_lat) + corridor_width_km * _LAT_PER_KM
        mid_lat = (origin_lat + dest_lat) / 2
        lng_deg_per_km = 1.0 / (111.32 * math.cos(math.radians(mid_lat)))
        lng_min = min(origin_lng, dest_lng) - corridor_width_km * lng_deg_per_km
        lng_max = max(origin_lng, dest_lng) + corridor_width_km * lng_deg_per_km
    else:
        lat_span = corridor_width_km * _LAT_PER_KM
        lng_deg_per_km = 1.0 / (111.32 * math.cos(math.radians(origin_lat)))
        lng_span = corridor_width_km * lng_deg_per_km
        lat_min, lat_max = origin_lat - lat_span, origin_lat + lat_span
        lng_min, lng_max = origin_lng - lng_span, origin_lng + lng_span

    with get_conn() as conn:
        # 找到走廊 bbox 内的簇
        cluster_rows = conn.execute("""
            SELECT cluster_id, center_lat, center_lng, size
            FROM cluster_meta
            WHERE center_lat BETWEEN ? AND ?
              AND center_lng BETWEEN ? AND ?
            ORDER BY size DESC
            LIMIT 50
        """, (lat_min, lat_max, lng_min, lng_max)).fetchall()

        if not cluster_rows:
            return []

        cluster_ids = [r["cluster_id"] for r in cluster_rows]
        cmap = {r["cluster_id"]: dict(r) for r in cluster_rows}

        # 批量查询簇内 POI 摘要
        placeholders = ",".join("?" * len(cluster_ids))
        poi_rows = conn.execute(f"""
            SELECT cluster_id, subcategory, category,
                   AVG(rating) as avg_rating, AVG(price_per_person) as avg_price,
                   COUNT(*) as cnt
            FROM pois
            WHERE cluster_id IN ({placeholders})
              AND lat IS NOT NULL
            GROUP BY cluster_id
        """, cluster_ids).fetchall()

        # 每个簇的 top POI 名称
        top_pois = {}
        for cid in cluster_ids:
            names = conn.execute("""
                SELECT name FROM pois
                WHERE cluster_id = ? AND rating IS NOT NULL
                ORDER BY rating DESC LIMIT 3
            """, (cid,)).fetchall()
            top_pois[cid] = [r["name"] for r in names]

        # 每个簇的品类分布
        cat_rows = conn.execute(f"""
            SELECT cluster_id, subcategory, COUNT(*) as cnt
            FROM pois
            WHERE cluster_id IN ({placeholders})
              AND subcategory IS NOT NULL AND subcategory != ''
            GROUP BY cluster_id, subcategory
        """, cluster_ids).fetchall()

        # 每个簇的商圈（批量查询）
        district_rows = conn.execute(f"""
            SELECT cluster_id, district, COUNT(*) as cnt
            FROM pois
            WHERE cluster_id IN ({placeholders})
              AND district IS NOT NULL AND district != ''
            GROUP BY cluster_id, district
            ORDER BY cnt DESC
        """, cluster_ids).fetchall()

    # 按簇组织品类分布
    cat_by_cluster = {}
    for r in cat_rows:
        cat_by_cluster.setdefault(r["cluster_id"], []).append((r["subcategory"], r["cnt"]))

    # 按簇组织商圈（取最常见的）
    district_by_cluster = {}
    for r in district_rows:
        if r["cluster_id"] not in district_by_cluster:
            district_by_cluster[r["cluster_id"]] = r["district"]

    # 构建簇摘要
    results = []
    for row in poi_rows:
        cid = row["cluster_id"]
        meta = cmap.get(cid, {})
        center_lat = meta.get("center_lat", origin_lat)
        center_lng = meta.get("center_lng", origin_lng)

        dist_km = haversine(origin_lat, origin_lng, center_lat, center_lng) / 1000.0

        # Top 品类（取层级路径最后一段作为品类名）
        cats = cat_by_cluster.get(cid, [])
        cats.sort(key=lambda x: x[1], reverse=True)
        top_cats = [c[0].split(";")[-1] if ";" in c[0] else c[0] for c in cats[:3]]

        avg_price = row["avg_price"]
        avg_rating = row["avg_rating"]

        # 预算过滤
        if budget and budget in _BUDGET_RANGES and avg_price is not None:
            lo, hi = _BUDGET_RANGES[budget]
            if avg_price < lo or avg_price > hi:
                continue

        # 关键词过滤
        if keywords:
            cluster_names = top_pois.get(cid, [])
            if not _keyword_matches_subcats(keywords, top_cats, cluster_names):
                continue

        # 过滤全部品类都是无关杂项的簇
        if top_cats and all(tc in _NON_TOURIST_CATS for tc in top_cats):
            continue

        # 计算投影比例（点在路线上的位置：0=起点, 1=终点）
        proj = _project_ratio(center_lat, center_lng,
                              origin_lat, origin_lng,
                              dest_lat or origin_lat, dest_lng or origin_lng)

        # 自动生成簇名称
        cluster_name = _cluster_name(district_by_cluster.get(cid, ""), top_cats, top_pois.get(cid, []))

        results.append({
            "cluster_id": cid,
            "name": cluster_name,
            "center_lat": round(center_lat, 5),
            "center_lng": round(center_lng, 5),
            "dist_from_origin_km": round(dist_km, 1),
            "projection": round(proj, 2),
            "poi_count": row["cnt"],
            "top_cats": top_cats,
            "avg_rating": round(avg_rating, 1) if avg_rating else None,
            "avg_price": round(avg_price, 0) if avg_price else None,
            "top_poi_names": top_pois.get(cid, []),
        })

    # Sort by projection ratio to show even distribution along route
    results.sort(key=lambda x: x["projection"])
    return results


def _cluster_name(district: str, top_cats: list, top_names: list) -> str:
    """根据商圈+品类生成人类可读的簇名称."""
    if district:
        suffix = f"·{top_cats[0]}" if top_cats else ""
        return f"{district}{suffix}"
    if top_names:
        # 取第一个 POI 名前几个字
        name = top_names[0]
        for sep in ["(", "（", "·", "—", "-"]:
            if sep in name:
                name = name.split(sep)[0]
        suffix = f"·{top_cats[0]}" if top_cats else ""
        return f"{name[:8]}{suffix}"
    if top_cats:
        return top_cats[0]
    return "未知区域"


def cluster_stats():
    """输出聚类统计."""
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM pois").fetchone()[0]
        clustered = conn.execute(
            "SELECT COUNT(*) FROM pois WHERE cluster_id IS NOT NULL"
        ).fetchone()[0]
        n_clusters = conn.execute(
            "SELECT COUNT(DISTINCT cluster_id) FROM pois WHERE cluster_id IS NOT NULL"
        ).fetchone()[0]
        # 簇大小分布
        sizes = conn.execute("""
            SELECT cluster_id, COUNT(*) as sz FROM pois
            WHERE cluster_id IS NOT NULL GROUP BY cluster_id ORDER BY sz DESC
        """).fetchall()

        print(f"总 POI: {total}")
        print(f"已聚类: {clustered} ({clustered/total*100:.1f}%)")
        print(f"簇数量: {n_clusters}")
        if sizes:
            print(f"最大簇: {sizes[0][1]} POI (cluster_id={sizes[0][0]})")
            print(f"最小簇: {sizes[-1][1]} POI (cluster_id={sizes[-1][0]})")
            avg = sum(s[1] for s in sizes) / len(sizes)
            print(f"平均簇大小: {avg:.1f}")
            # 大小分布
            buckets = {"3-5": 0, "6-10": 0, "11-20": 0, "21-50": 0, "51+": 0}
            for _, sz in sizes:
                if sz <= 5: buckets["3-5"] += 1
                elif sz <= 10: buckets["6-10"] += 1
                elif sz <= 20: buckets["11-20"] += 1
                elif sz <= 50: buckets["21-50"] += 1
                else: buckets["51+"] += 1
            print(f"大小分布: {buckets}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if cmd == "build":
        build_clusters()
    elif cmd == "stats":
        cluster_stats()
    elif cmd == "query" and len(sys.argv) >= 4:
        lat, lng = float(sys.argv[2]), float(sys.argv[3])
        for c in query_clusters(lat, lng):
            print(f"  cluster_id={c['cluster_id']} size={c['size']} center=({c['center_lat']:.4f},{c['center_lng']:.4f})")
    elif cmd == "corridor" and len(sys.argv) >= 6:
        o_lat, o_lng = float(sys.argv[2]), float(sys.argv[3])
        d_lat, d_lng = float(sys.argv[4]), float(sys.argv[5])
        for c in query_corridor_clusters(o_lat, o_lng, d_lat, d_lng):
            print(f"  [{c['cluster_id']}] {c['name']} | {c['dist_from_origin_km']}km | "
                  f"{c['poi_count']}POI | {c['top_cats']} | ⭐{c['avg_rating']} | ¥{c['avg_price']}")
    else:
        print(__doc__)
