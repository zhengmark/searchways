"""POIRepository — SQLite 实现 POIProvider 接口，用 Haversine 替代 PostGIS."""
import math
import time
from typing import Optional

from app.providers.base import POIProvider
from app.providers.amap_provider import geocode as amap_geocode, robust_geocode as amap_robust_geocode, AmapAPIError
from app.algorithms.geo import haversine
from db.connection import get_conn, get_db_path


# 关键词 → 品类映射（兼容"美食"→餐饮、"景点"→景点等）
_KW_TO_CATEGORY = {
    "美食": "餐饮", "吃": "餐饮", "好吃的": "餐饮", "火锅": "餐饮", "咖啡": "餐饮",
    "小吃": "餐饮", "烧烤": "餐饮", "奶茶": "餐饮", "茶饮": "餐饮", "餐厅": "餐饮",
    "景点": "景点", "公园": "景点", "风景": "景点", "博物馆": "景点", "寺庙": "景点",
    "购物": "购物", "商场": "购物", "逛街": "购物", "超市": "购物",
    "休闲": "休闲", "娱乐": "休闲", "KTV": "休闲", "影院": "休闲", "酒吧": "休闲",
    "文化": "文化", "图书馆": "文化", "美术馆": "文化", "剧院": "文化",
}

# 每度纬度 ≈ 111.32 km
_LAT_PER_KM = 1.0 / 111.32


def _lng_per_km(lat: float) -> float:
    """每度经度对应的公里数（随纬度变化）."""
    return 1.0 / (111.32 * math.cos(math.radians(lat)))


def _bbox(lat: float, lng: float, radius_km: float) -> tuple:
    """返回 (lat_min, lat_max, lng_min, lng_max)."""
    dlat = radius_km * _LAT_PER_KM
    dlng = radius_km * _lng_per_km(lat)
    return (lat - dlat, lat + dlat, lng - dlng, lng + dlng)


def _parse_location(location: str) -> tuple:
    """解析 "lng,lat" 字符串 → (lat, lng) 或 (None, None)."""
    if not location or "," not in location:
        return None, None
    try:
        parts = location.split(",")
        lng, lat = float(parts[0]), float(parts[1])
        return lat, lng
    except (ValueError, IndexError):
        return None, None


def _build_search_clauses(keywords: str) -> tuple:
    """构建搜索子句：(clauses_list, params_list)，同时覆盖 name/subcategory/category."""
    kws = [kw.strip() for kw in keywords.replace(",", " ").split() if kw.strip()]
    if not kws:
        return [], []

    clauses = []
    params = []
    for kw in kws[:5]:
        pattern = f"%{kw}%"
        clauses.append("(name LIKE ? OR subcategory LIKE ?)")
        params.extend([pattern, pattern])
        # 关键词到品类映射
        cat = _KW_TO_CATEGORY.get(kw)
        if cat:
            clauses.append("category = ?")
            params.append(cat)

    # OR 连接（宽松匹配），外层 AND
    return [f"({' OR '.join(clauses)})"], params

def _search_text(conn, keywords: str, city: str, limit: int) -> list:
    """城市 + 关键词文本搜索（匹配 name/subcategory/category）."""
    clause_list, params = _build_search_clauses(keywords)
    if not clause_list:
        return []

    sql = f"""SELECT amap_id, name, address, category, subcategory, lat, lng, rating, price_per_person, city, district
              FROM pois
              WHERE city = ? AND {clause_list[0]}
              ORDER BY rating DESC NULLS LAST
              LIMIT ?"""
    rows = conn.execute(sql, [city] + params + [limit]).fetchall()
    return [_row_to_dict(r) for r in rows]


def _search_around(conn, lat: float, lng: float, keywords: str, radius: int, limit: int) -> list:
    """周边搜索：边界框 + Haversine 精筛."""
    radius_km = min(radius, 50000) / 1000.0
    lat_min, lat_max, lng_min, lng_max = _bbox(lat, lng, radius_km * 1.05)

    clause_list, kw_params = _build_search_clauses(keywords)
    text_where = clause_list[0] if clause_list else "1=1"
    sql = f"""SELECT amap_id, name, address, category, subcategory, lat, lng, rating, price_per_person, city, district
              FROM pois
              WHERE lat BETWEEN ? AND ? AND lng BETWEEN ? AND ? AND ({text_where})
              ORDER BY rating DESC NULLS LAST
              LIMIT ?"""
    rows = conn.execute(sql, [lat_min, lat_max, lng_min, lng_max] + kw_params + [limit * 3]).fetchall()

    results = []
    for r in rows:
        d = haversine(lat, lng, r["lat"], r["lng"])  # 返回米
        if d <= radius:
            item = _row_to_dict(r)
            item["distance"] = d
            results.append(item)

    return results[:limit]


def _search_along_route(conn, origin: str, dest: str, keywords: str, radius: int, limit: int) -> list:
    """沿途搜索：走廊边界框 + 点到线段距离精筛."""
    o_lat, o_lng = _parse_location(origin)
    d_lat, d_lng = _parse_location(dest)
    if o_lat is None or d_lat is None:
        return []

    radius_km = min(radius, 50000) / 1000.0
    lat_min = min(o_lat, d_lat) - radius_km * _LAT_PER_KM
    lat_max = max(o_lat, d_lat) + radius_km * _LAT_PER_KM
    lng_min = min(o_lng, d_lng) - radius_km * _lng_per_km((o_lat + d_lat) / 2)
    lng_max = max(o_lng, d_lng) + radius_km * _lng_per_km((o_lat + d_lat) / 2)

    clause_list, kw_params = _build_search_clauses(keywords)
    text_where = clause_list[0] if clause_list else "1=1"
    sql = f"""SELECT amap_id, name, address, category, subcategory, lat, lng, rating, price_per_person, city, district
              FROM pois
              WHERE lat BETWEEN ? AND ? AND lng BETWEEN ? AND ? AND ({text_where})
              ORDER BY rating DESC NULLS LAST
              LIMIT ?"""
    rows = conn.execute(sql, [lat_min, lat_max, lng_min, lng_max] + kw_params + [limit * 5]).fetchall()

    # 点到线段的垂直距离
    def _dist_to_segment(px, py):
        dx = d_lng - o_lng
        dy = d_lat - o_lat
        seg_len2 = dx * dx + dy * dy
        if seg_len2 == 0:
            return haversine(py, px, o_lat, o_lng)
        t = max(0, min(1, ((px - o_lng) * dx + (py - o_lat) * dy) / seg_len2))
        proj_lng = o_lng + t * dx
        proj_lat = o_lat + t * dy
        return haversine(py, px, proj_lat, proj_lng)

    results = []
    for r in rows:
        d = _dist_to_segment(r["lng"], r["lat"])  # haversine 返回米
        if d <= radius:
            item = _row_to_dict(r)
            item["distance"] = d
            results.append(item)

    results.sort(key=lambda x: x.get("distance", 99999))
    return results[:limit]


def _row_to_dict(row) -> dict:
    """sqlite3.Row → dict."""
    return {
        "amap_id": row["amap_id"],
        "name": row["name"],
        "address": row["address"] or "",
        "category": row["category"],
        "subcategory": row["subcategory"] or "",
        "lat": row["lat"],
        "lng": row["lng"],
        "rating": row["rating"],
        "price_per_person": row["price_per_person"],
        "city": row["city"] or "",
        "district": row["district"] or "",
    }


class POIRepository(POIProvider):
    """SQLite POI 数据访问实现."""

    def __init__(self, db_path: str = None):
        self._db_path = db_path or get_db_path()

    # ── POIProvider 接口实现 ──────────────────────────

    def search_poi(self, keywords: str, location: str, radius_km: float = 3, limit: int = 5) -> list:
        with get_conn(self._db_path) as conn:
            return _search_text(conn, keywords, location, limit)

    def search_around(self, location: str, keywords: str, radius: int = 3000, limit: int = 10) -> list:
        lat, lng = _parse_location(location)
        if lat is None:
            return []
        with get_conn(self._db_path) as conn:
            return _search_around(conn, lat, lng, keywords, radius, limit)

    def search_along_route(self, origin: str, destination: str, keywords: str,
                           radius: int = 3000, limit: int = 20) -> list:
        with get_conn(self._db_path) as conn:
            return _search_along_route(conn, origin, destination, keywords, radius, limit)

    def geocode(self, address: str, city: str = "") -> dict:
        """geocode 委托高德 API（POI 表覆盖不了地址解析）."""
        return amap_geocode(address, city)

    def robust_geocode(self, name: str, city: str) -> tuple:
        """robust_geocode 委托高德 API."""
        return amap_robust_geocode(name, city)

    # ── 扩展方法 ──────────────────────────────────────

    def stats(self) -> dict:
        """返回数据库统计信息."""
        with get_conn(self._db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM pois").fetchone()[0]
            by_cat = conn.execute(
                "SELECT category, COUNT(*) FROM pois GROUP BY category ORDER BY COUNT(*) DESC"
            ).fetchall()
            return {
                "total": total,
                "by_category": {r[0]: r[1] for r in by_cat},
            }

    def search_by_name(self, name: str, limit: int = 10) -> list:
        """按名称模糊搜索."""
        with get_conn(self._db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM pois WHERE name LIKE ? LIMIT ?",
                (f"%{name}%", limit),
            ).fetchall()
            return [_row_to_dict(r) for r in rows]
