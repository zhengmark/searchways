"""高德 API 封装 — 所有函数直接返回 Python 对象，异常用 AmapAPIError."""
import json
import time
import requests
from agent.config import AMAP_API_KEY

AMAP_PLACE_TEXT_API = "https://restapi.amap.com/v3/place/text"
AMAP_PLACE_AROUND_API = "https://restapi.amap.com/v3/place/around"
AMAP_GEOCODE_API = "https://restapi.amap.com/v3/geocode/geo"
AMAP_INPUT_TIPS_API = "https://restapi.amap.com/v3/assistant/inputtips"


class AmapAPIError(Exception):
    """高德 API 错误，包含可读消息."""
    def __init__(self, message: str, raw: dict = None):
        super().__init__(message)
        self.raw = raw or {}


def _check_key():
    if not AMAP_API_KEY or AMAP_API_KEY == "your-amap-key-here":
        raise AmapAPIError("高德 API Key 未配置，请在 .env 中设置 AMAP_API_KEY")


def _safe_float(biz_ext, field):
    """安全地从 biz_ext 中提取数值字段."""
    if not isinstance(biz_ext, dict):
        return None
    val = biz_ext.get(field)
    if val is None or val == "" or val == "[]":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _parse_location(loc: str) -> tuple:
    """解析高德 location 字符串 "lng,lat" → (lat, lng)，无效返回 (None, None)."""
    if loc and loc != "0,0" and "," in loc:
        lng_str, lat_str = loc.split(",")
        return float(lat_str), float(lng_str)
    return None, None


def _build_poi_dict(poi: dict) -> dict:
    """将高德原始 POI 转为统一的 dict 格式."""
    lat, lng = _parse_location(poi.get("location", ""))
    return {
        "name": poi.get("name", ""),
        "address": poi.get("address", ""),
        "category": poi.get("type", ""),
        "lat": lat,
        "lng": lng,
        "rating": _safe_float(poi.get("biz_ext"), "rating"),
        "price_per_person": _safe_float(poi.get("biz_ext"), "cost"),
        "distance": poi.get("distance"),
    }


def search_poi(keywords: str, location: str, radius_km: float = 3, limit: int = 5) -> list:
    """在指定区域搜索 POI，返回 POI dict 列表."""
    _check_key()
    try:
        params = {
            "key": AMAP_API_KEY,
            "keywords": keywords,
            "city": location,
            "offset": min(limit, 25),
            "extensions": "all",
        }
        resp = requests.get(AMAP_PLACE_TEXT_API, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.Timeout:
        raise AmapAPIError("高德 API 请求超时，请稍后重试")
    except requests.ConnectionError:
        raise AmapAPIError("无法连接到高德 API，请检查网络")
    except requests.RequestException as e:
        raise AmapAPIError(f"高德 API 请求失败: {e}")

    if data.get("status") != "1":
        info = data.get("info", "未知错误")
        if "INVALID_USER_KEY" in info:
            raise AmapAPIError("高德 API Key 无效，请在 .env 中检查 AMAP_API_KEY")
        raise AmapAPIError(f"高德 API 返回错误: {info}")

    return [_build_poi_dict(p) for p in data.get("pois", [])[:limit]]


def geocode(address: str, city: str = "") -> dict:
    """地址转经纬度，返回 {"lng", "lat", "city", "district", "province"}."""
    _check_key()
    try:
        params = {"key": AMAP_API_KEY, "address": address, "city": city}
        resp = requests.get(AMAP_GEOCODE_API, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.Timeout:
        raise AmapAPIError("高德地理编码请求超时")
    except requests.ConnectionError:
        raise AmapAPIError("无法连接到高德 API")
    except requests.RequestException as e:
        raise AmapAPIError(f"请求失败: {e}")

    if data.get("status") != "1" or not data.get("geocodes"):
        raise AmapAPIError(f"未找到「{address}」的坐标信息")

    location = data["geocodes"][0].get("location", "")
    if "," not in location:
        raise AmapAPIError("坐标格式异常")
    lng, lat = location.split(",")
    g = data["geocodes"][0]
    return {
        "lng": float(lng), "lat": float(lat),
        "city": g.get("city", "").rstrip("市"),
        "district": g.get("district", ""),
        "province": g.get("province", ""),
    }


def input_tips(keywords: str, city: str = "", limit: int = 5) -> list:
    """输入提示，返回建议列表."""
    try:
        params = {"key": AMAP_API_KEY, "keywords": keywords, "datatype": "all"}
        if city:
            params["city"] = city
        resp = requests.get(AMAP_INPUT_TIPS_API, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []

    results = []
    for t in data.get("tips", [])[:limit]:
        loc = t.get("location", "")
        if loc and "," in loc and loc != "0,0":
            lng_str, lat_str = loc.split(",")
            results.append({
                "name": t.get("name", ""),
                "lng": float(lng_str), "lat": float(lat_str),
                "address": t.get("address", ""),
                "district": t.get("district", ""),
            })
    return results


def robust_geocode(name: str, city: str) -> tuple:
    """
    带多层兜底的 geocode，返回 (lat, lng) 或 (None, None).

    Layer 1: 原始名称 + city
    Layer 2: input_tips 模糊匹配（子串校验防跨地名错配）
    Layer 3: name + "地铁站" / "站"
    Layer 4: city + name（仅 ≥3 字）
    """
    # Layer 1
    try:
        gc = geocode(name, city)
        if "lng" in gc:
            return gc["lat"], gc["lng"]
    except AmapAPIError:
        pass
    time.sleep(0.02)

    # Layer 2: input_tips 子串匹配
    try:
        tips = input_tips(name, city, limit=5)
        for tip in tips:
            tip_name = tip.get("name", "")
            if name in tip_name or tip_name in name:
                return tip["lat"], tip["lng"]
    except Exception:
        pass
    time.sleep(0.02)

    # Layer 3: 加后缀
    for suffix in ["地铁站", "站"]:
        try:
            gc = geocode(f"{name}{suffix}", city)
            if "lng" in gc:
                return gc["lat"], gc["lng"]
        except AmapAPIError:
            pass
        time.sleep(0.02)

    # Layer 4: 城市前缀
    if len(name) >= 3:
        try:
            gc = geocode(f"{city}{name}", city)
            if "lng" in gc:
                return gc["lat"], gc["lng"]
        except AmapAPIError:
            pass

    return None, None


def search_around(location: str, keywords: str, radius: int = 3000, limit: int = 10) -> list:
    """周边搜索，返回 POI dict 列表."""
    _check_key()
    try:
        params = {
            "key": AMAP_API_KEY,
            "location": location,
            "keywords": keywords,
            "radius": min(radius, 50000),
            "offset": min(limit, 25),
            "extensions": "all",
        }
        resp = requests.get(AMAP_PLACE_AROUND_API, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.Timeout:
        raise AmapAPIError("高德周边搜索请求超时")
    except requests.ConnectionError:
        raise AmapAPIError("无法连接到高德 API")
    except requests.RequestException as e:
        raise AmapAPIError(f"请求失败: {e}")

    if data.get("status") != "1":
        raise AmapAPIError(f"高德 API 返回错误: {data.get('info', '未知')}")

    return [_build_poi_dict(p) for p in data.get("pois", [])[:limit]]


def search_along_route(origin: str, destination: str, keywords: str,
                       radius: int = 3000, limit: int = 20) -> list:
    """沿途搜索：采样 4 点做周边搜索并合并去重."""
    try:
        o_lng, o_lat = (float(x) for x in origin.split(","))
        d_lng, d_lat = (float(x) for x in destination.split(","))
    except (ValueError, AttributeError):
        raise AmapAPIError("坐标格式不正确，需要 'lng,lat' 格式")

    pts = [
        origin,
        f"{o_lng + (d_lng - o_lng) * 0.33:.6f},{o_lat + (d_lat - o_lat) * 0.33:.6f}",
        f"{o_lng + (d_lng - o_lng) * 0.66:.6f},{o_lat + (d_lat - o_lat) * 0.66:.6f}",
        destination,
    ]

    all_pois = []
    per = max(limit // len(pts), 1)
    for pt in pts:
        try:
            all_pois.extend(search_around(pt, keywords, radius, per))
        except AmapAPIError:
            pass

    seen = set()
    unique = []
    for p in all_pois:
        name = p.get("name", "")
        if name and name not in seen:
            seen.add(name)
            unique.append(p)
    return unique[:limit]
