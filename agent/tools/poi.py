import json
import requests
from agent.config import AMAP_API_KEY

TOOL_DEFINITION = {
    "name": "search_poi",
    "description": "在指定区域搜索兴趣点（餐厅、景点、咖啡馆等），返回 POI 列表及基本信息",
    "input_schema": {
        "type": "object",
        "properties": {
            "keywords": {
                "type": "string",
                "description": "搜索关键词，如「咖啡馆」「公园」「火锅」",
            },
            "location": {
                "type": "string",
                "description": "城市名或区域名，如「北京」「北京朝阳区」",
            },
            "radius_km": {
                "type": "number",
                "description": "搜索半径（公里），默认 3（仅当 location 传了经纬度时生效）",
            },
            "limit": {
                "type": "integer",
                "description": "返回数量上限，默认 5",
            },
        },
        "required": ["keywords", "location"],
    },
}

AMAP_PLACE_TEXT_API = "https://restapi.amap.com/v3/place/text"
AMAP_PLACE_AROUND_API = "https://restapi.amap.com/v3/place/around"
AMAP_GEOCODE_API = "https://restapi.amap.com/v3/geocode/geo"


def _safe_float(biz_ext, field):
    """安全地从 biz_ext 中提取数值字段，应对 None/list/dict 各种情况"""
    if not isinstance(biz_ext, dict):
        return None
    val = biz_ext.get(field)
    if val is None or val == "" or val == "[]":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def search_poi(keywords: str, location: str, radius_km: float = 3, limit: int = 5) -> str:
    if not AMAP_API_KEY or AMAP_API_KEY == "your-amap-key-here":
        return json.dumps({"error": "高德 API Key 未配置，请在 .env 中设置 AMAP_API_KEY"}, ensure_ascii=False)

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
        return json.dumps({"error": "高德 API 请求超时，请稍后重试"}, ensure_ascii=False)
    except requests.ConnectionError:
        return json.dumps({"error": "无法连接到高德 API，请检查网络"}, ensure_ascii=False)
    except requests.RequestException as e:
        return json.dumps({"error": f"高德 API 请求失败: {str(e)}"}, ensure_ascii=False)

    if data.get("status") != "1":
        info = data.get("info", "未知错误")
        if "INVALID_USER_KEY" in info:
            return json.dumps({"error": "高德 API Key 无效，请在 .env 中检查 AMAP_API_KEY"}, ensure_ascii=False)
        return json.dumps({"error": f"高德 API 返回错误: {info}"}, ensure_ascii=False)

    pois = data.get("pois", [])
    if not pois:
        return json.dumps({"error": f"在「{location}」未找到「{keywords}」相关的地点"}, ensure_ascii=False)

    results = []
    for poi in pois[:limit]:
        loc = poi.get("location", "")
        if loc and loc != "0,0" and "," in loc:
            lng_str, lat_str = loc.split(",")
            lat, lng = float(lat_str), float(lng_str)
        else:
            lat, lng = None, None
        results.append({
            "name": poi.get("name", ""),
            "address": poi.get("address", ""),
            "category": poi.get("type", ""),
            "lat": lat,
            "lng": lng,
            "rating": _safe_float(poi.get("biz_ext"), "rating"),
            "price_per_person": _safe_float(poi.get("biz_ext"), "cost"),
        })

    return json.dumps(results, ensure_ascii=False)


def geocode(address: str, city: str = "") -> str:
    """地址转经纬度，返回 JSON {"lng": xxx, "lat": xxx}"""
    if not AMAP_API_KEY or AMAP_API_KEY == "your-amap-key-here":
        return json.dumps({"error": "高德 API Key 未配置"}, ensure_ascii=False)

    try:
        params = {"key": AMAP_API_KEY, "address": address, "city": city}
        resp = requests.get(AMAP_GEOCODE_API, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.Timeout:
        return json.dumps({"error": "高德地理编码请求超时"}, ensure_ascii=False)
    except requests.ConnectionError:
        return json.dumps({"error": "无法连接到高德 API"}, ensure_ascii=False)
    except requests.RequestException as e:
        return json.dumps({"error": f"请求失败: {str(e)}"}, ensure_ascii=False)

    if data.get("status") != "1" or not data.get("geocodes"):
        return json.dumps({"error": f"未找到「{address}」的坐标信息"}, ensure_ascii=False)

    location = data["geocodes"][0].get("location", "")
    if "," in location:
        lng, lat = location.split(",")
        g = data["geocodes"][0]
        return json.dumps({
            "lng": float(lng), "lat": float(lat),
            "city": g.get("city", "").rstrip("市"),
            "district": g.get("district", ""),
            "province": g.get("province", ""),
        }, ensure_ascii=False)
    return json.dumps({"error": "坐标格式异常"}, ensure_ascii=False)


AMAP_INPUT_TIPS_API = "https://restapi.amap.com/v3/assistant/inputtips"


def input_tips(keywords: str, city: str = "", limit: int = 5) -> str:
    """输入提示：根据部分名称返回完整 POI 建议列表，用于模糊 geocode 兜底。"""
    try:
        params = {"key": AMAP_API_KEY, "keywords": keywords, "datatype": "all"}
        if city:
            params["city"] = city
        resp = requests.get(AMAP_INPUT_TIPS_API, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return json.dumps({"error": "输入提示请求失败"}, ensure_ascii=False)

    tips = data.get("tips", [])
    results = []
    for t in tips[:limit]:
        loc = t.get("location", "")
        if loc and "," in loc and loc != "0,0":
            lng_str, lat_str = loc.split(",")
            results.append({"name": t.get("name", ""), "lng": float(lng_str), "lat": float(lat_str),
                            "address": t.get("address", ""), "district": t.get("district", "")})
    return json.dumps(results, ensure_ascii=False)


def robust_geocode(name: str, city: str) -> tuple:
    """
    带多层兜底的 geocode：
    1. 原始名称 + city
    2. Amap input tips 模糊匹配（带名称相似度校验）
    3. name + "地铁站" / "站"（应对漏掉后缀）
    4. city + name（仅当名称 ≥3 字，防误匹配）
    返回 (lat, lng) 或 (None, None)
    """
    import time

    # 第1层：原始名称
    try:
        gc = json.loads(geocode(name, city))
        if "lng" in gc:
            return gc["lat"], gc["lng"]
    except Exception:
        pass
    time.sleep(0.02)

    # 第2层：input tips 模糊匹配，用子串校验防跨地名错配
    try:
        tips = json.loads(input_tips(name, city, limit=5))
        if isinstance(tips, list):
            for tip in tips:
                tip_name = tip.get("name", "")
                # 必须是连续子串（「四路地铁站」⊆「丈八四路地铁站」而 ⊄「凤城四路」）
                if name in tip_name or tip_name in name:
                    return tip["lat"], tip["lng"]
    except Exception:
        pass
    time.sleep(0.02)

    # 第3层：加后缀
    for suffix in ["地铁站", "站"]:
        try:
            gc = json.loads(geocode(f"{name}{suffix}", city))
            if "lng" in gc:
                return gc["lat"], gc["lng"]
        except Exception:
            pass
        time.sleep(0.02)

    # 第4层：城市前缀（仅当名称已有足够信息量）
    if len(name) >= 3:
        try:
            gc = json.loads(geocode(f"{city}{name}", city))
            if "lng" in gc:
                return gc["lat"], gc["lng"]
        except Exception:
            pass

    return None, None


def search_around(location: str, keywords: str, radius: int = 3000, limit: int = 10) -> str:
    """
    周边搜索：在指定坐标附近搜索 POI。
    location 为 "lng,lat" 格式（如 "108.940,34.261"）。
    """
    if not AMAP_API_KEY or AMAP_API_KEY == "your-amap-key-here":
        return json.dumps({"error": "高德 API Key 未配置"}, ensure_ascii=False)

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
        return json.dumps({"error": "高德周边搜索请求超时"}, ensure_ascii=False)
    except requests.ConnectionError:
        return json.dumps({"error": "无法连接到高德 API"}, ensure_ascii=False)
    except requests.RequestException as e:
        return json.dumps({"error": f"请求失败: {str(e)}"}, ensure_ascii=False)

    if data.get("status") != "1":
        return json.dumps({"error": f"高德 API 返回错误: {data.get('info', '未知')}"}, ensure_ascii=False)

    pois = data.get("pois", [])
    if not pois:
        return json.dumps({"error": f"在指定位置附近未找到「{keywords}」"}, ensure_ascii=False)

    results = []
    for poi in pois[:limit]:
        loc = poi.get("location", "")
        if loc and loc != "0,0" and "," in loc:
            lng_str, lat_str = loc.split(",")
            lat, lng = float(lat_str), float(lng_str)
        else:
            lat, lng = None, None
        results.append({
            "name": poi.get("name", ""),
            "address": poi.get("address", ""),
            "category": poi.get("type", ""),
            "lat": lat,
            "lng": lng,
            "distance": poi.get("distance"),
            "rating": _safe_float(poi.get("biz_ext"), "rating"),
            "price_per_person": _safe_float(poi.get("biz_ext"), "cost"),
        })

    return json.dumps(results, ensure_ascii=False)


def search_along_route(origin: str, destination: str, keywords: str, radius: int = 3000, limit: int = 20) -> str:
    """
    沿途搜索：在路线沿线搜索 POI。
    origin/destination 为 "lng,lat" 格式坐标。
    通过采样起点、1/3、2/3、终点四个位置做周边搜索并合并结果。
    """
    try:
        o_lng, o_lat = (float(x) for x in origin.split(","))
        d_lng, d_lat = (float(x) for x in destination.split(","))
    except (ValueError, AttributeError):
        return json.dumps({"error": "坐标格式不正确，需要 'lng,lat' 格式"}, ensure_ascii=False)

    # 沿路取 4 个采样点：起点、1/3、2/3、终点
    pts = [
        origin,
        f"{o_lng + (d_lng - o_lng) * 0.33:.6f},{o_lat + (d_lat - o_lat) * 0.33:.6f}",
        f"{o_lng + (d_lng - o_lng) * 0.66:.6f},{o_lat + (d_lat - o_lat) * 0.66:.6f}",
        destination,
    ]

    all_pois = []
    per = limit // len(pts) + 1
    for pt in pts:
        result = search_around(pt, keywords, radius, per)
        data = json.loads(result)
        if isinstance(data, list):
            all_pois.extend(data)

    # 按名称去重
    seen = set()
    unique = []
    for p in all_pois:
        name = p.get("name", "")
        if name and name not in seen:
            seen.add(name)
            unique.append(p)

    return json.dumps(unique[:limit], ensure_ascii=False)
