import json
import requests
from agent.config import AMAP_API_KEY

AMAP_WALKING_API = "https://restapi.amap.com/v3/direction/walking"


def walk_distance(origin: str, destination: str) -> dict | None:
    """
    计算两点之间的步行距离和时间。
    origin/destination: "lng,lat" 格式（如 "108.940,34.261"）
    返回 {"distance": 米, "duration": 秒}，失败返回 None
    """
    if not AMAP_API_KEY or AMAP_API_KEY == "your-amap-key-here":
        return None

    params = {
        "key": AMAP_API_KEY,
        "origin": origin,
        "destination": destination,
        "type": "1",
    }
    try:
        resp = requests.get(AMAP_WALKING_API, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException:
        return None

    if data.get("status") != "1":
        return None

    paths = data.get("route", {}).get("paths")
    if not paths:
        return None

    path = paths[0]
    return {
        "distance": int(path.get("distance", 0)),
        "duration": int(path.get("duration", 0)),
    }
