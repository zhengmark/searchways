"""多模式路线规划 — 步行 / 骑行 / 公交地铁 / 驾车，含缓存."""

import time
import threading
from app.providers.amap_provider import (
    get_walking_route, transit_route, biking_route, driving_route,
)


# ── 缓存 ──────────────────────────────────────────

_cache = {}          # key → (expires_at, result)
_cache_lock = threading.Lock()
_CACHE_TTL = 30      # 秒


def _cache_key(lng1: float, lat1: float, lng2: float, lat2: float,
               mode: str) -> str:
    """按 ~11m 精度（4 位小数）生成坐标对缓存键."""
    return f"{lng1:.4f},{lat1:.4f}|{lng2:.4f},{lat2:.4f}|{mode}"


def _cached_get(ckey: str) -> dict | None:
    now = time.monotonic()
    with _cache_lock:
        entry = _cache.get(ckey)
        if entry and now < entry[0]:
            return entry[1]
        if entry:
            del _cache[ckey]
    return None


def _cache_put(ckey: str, result: dict):
    with _cache_lock:
        _cache[ckey] = (time.monotonic() + _CACHE_TTL, result)
        # 限制缓存大小
        if len(_cache) > 200:
            oldest = min(_cache.items(), key=lambda x: x[1][0])
            del _cache[oldest[0]]


# ── 交通模式决策 ──────────────────────────────────

def decide_transport(distance_meters: float) -> str:
    """根据 haversine 直线距离决定推荐交通方式."""
    if distance_meters < 800:
        return "步行"
    if distance_meters <= 3000:
        return "骑行"
    if distance_meters <= 8000:
        return "公交/地铁"
    return "驾车"


# ── 统一路线接口 ──────────────────────────────────

def get_route(origin: str, destination: str, mode: str = "auto",
              city: str = "西安") -> dict | None:
    """获取两点间路线（自动选模式或指定模式），含缓存.

    Args:
        origin: "lng,lat"
        destination: "lng,lat"
        mode: "auto" | "步行" | "骑行" | "公交/地铁" | "驾车"
        city: 城市名（公交模式使用）

    Returns:
        {"distance": int(m), "duration": int(sec), "mode": str, "cost": float, "steps": [...]}
        失败返回 None
    """
    # 解析坐标用于缓存键
    try:
        o_lng, o_lat = (float(x) for x in origin.split(","))
        d_lng, d_lat = (float(x) for x in destination.split(","))
    except (ValueError, AttributeError):
        return None

    if mode == "auto":
        from app.algorithms.geo import haversine
        dist = haversine(o_lat, o_lng, d_lat, d_lng)
        mode = decide_transport(dist)

    ckey = _cache_key(o_lng, o_lat, d_lng, d_lat, mode)
    cached = _cached_get(ckey)
    if cached is not None:
        return cached

    result = None
    if mode == "步行":
        result = get_walking_route(origin, destination)
    elif mode == "骑行":
        result = biking_route(origin, destination)
    elif mode == "公交/地铁":
        result = transit_route(origin, destination, city)
    elif mode == "驾车":
        result = driving_route(origin, destination)

    if result is not None:
        _cache_put(ckey, result)

    return result


# ── 向后兼容 ──────────────────────────────────────

def walk_distance(origin: str, destination: str) -> dict | None:
    """向后兼容的步行距离接口.

    返回 {"distance": int(m), "duration": int(sec)} 或 None.
    """
    result = get_route(origin, destination, mode="步行")
    if result is None:
        return None
    return {"distance": result["distance"], "duration": result["duration"]}


# ── 两点预览连线 ──────────────────────────────────

def preview_connection(origin_coords: tuple, dest_coords: tuple,
                       city: str = "西安") -> dict | None:
    """获取两点间交通详情（用于前端连线预览）.

    Args:
        origin_coords: (lat, lng)
        dest_coords: (lat, lng)

    Returns:
        {"from_name", "to_name", "mode", "distance", "duration", "cost", "steps"}
    """
    o_str = f"{origin_coords[1]},{origin_coords[0]}"
    d_str = f"{dest_coords[1]},{dest_coords[0]}"

    from app.algorithms.geo import haversine
    dist = haversine(origin_coords[0], origin_coords[1],
                     dest_coords[0], dest_coords[1])
    mode = decide_transport(dist)

    result = get_route(o_str, d_str, mode=mode, city=city)
    if result is None:
        return None

    return {
        "from_lat": origin_coords[0], "from_lng": origin_coords[1],
        "to_lat": dest_coords[0], "to_lng": dest_coords[1],
        "mode": result["mode"],
        "distance": result["distance"],
        "duration": result["duration"],
        "cost": result["cost"],
        "steps": result["steps"],
    }
