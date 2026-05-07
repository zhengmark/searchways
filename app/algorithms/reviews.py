import json
import requests
from app.config import AMAP_API_KEY

TOOL_DEFINITION = {
    "name": "fetch_reviews",
    "description": "获取某个 POI 的用户评价摘要，用于判断该地点是否符合用户偏好",
    "input_schema": {
        "type": "object",
        "properties": {
            "poi_name": {
                "type": "string",
                "description": "POI 名称",
            },
            "location": {
                "type": "string",
                "description": "POI 所在城市或区域",
            },
            "focus": {
                "type": "string",
                "description": "关注维度，如「适合老人」「环境安静」「性价比高」",
            },
        },
        "required": ["poi_name", "location"],
    },
}

AMAP_PLACE_TEXT_API = "https://restapi.amap.com/v3/place/text"


def fetch_reviews(poi_name: str, location: str, focus: str = "") -> str:
    if not AMAP_API_KEY or AMAP_API_KEY == "your-amap-key-here":
        return json.dumps({"error": "高德 API Key 未配置"}, ensure_ascii=False)

    try:
        params = {
            "key": AMAP_API_KEY,
            "keywords": poi_name,
            "city": location,
            "offset": 5,
            "extensions": "all",
        }
        resp = requests.get(AMAP_PLACE_TEXT_API, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

    except requests.Timeout:
        return json.dumps({"error": "高德 API 请求超时"}, ensure_ascii=False)
    except requests.ConnectionError:
        return json.dumps({"error": "无法连接到高德 API"}, ensure_ascii=False)
    except requests.RequestException as e:
        return json.dumps({"error": f"请求失败: {str(e)}"}, ensure_ascii=False)

    if data.get("status") != "1":
        return json.dumps({"error": f"高德 API 返回错误: {data.get('info', '未知')}"}, ensure_ascii=False)

    # 找到名字最匹配的 POI
    pois = data.get("pois", [])
    target = None
    for poi in pois:
        if poi_name in poi.get("name", ""):
            target = poi
            break
    if not target and pois:
        target = pois[0]
    if not target:
        return json.dumps({"error": f"未找到「{poi_name}」的信息"}, ensure_ascii=False)

    biz = target.get("biz_ext", {}) or {}
    tag = target.get("tag", "") or ""
    photos = target.get("photos", []) or []

    result = {
        "poi_name": target.get("name", poi_name),
        "address": target.get("address", ""),
        "avg_rating": biz.get("rating"),
        "price_per_person": biz.get("cost"),
        "open_time": biz.get("opentime2", biz.get("open_time", "")),
        "tags": [t.strip() for t in tag.split(",") if t.strip()] if tag else [],
        "phone": target.get("tel", ""),
        "photo_count": len(photos),
    }

    return json.dumps(result, ensure_ascii=False)
