"""Geometry utilities — haversine distance and projection."""

import math


def haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> int:
    """两点间直线距离（米）."""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return int(2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a)))


def project_ratio(lat: float, lng: float, origin: dict, dest: dict) -> float:
    """将 POI 投影到起点→终点连线上，返回 [0,1] 比例（0=起点, 1=终点）."""
    dx = dest["lng"] - origin["lng"]
    dy = dest["lat"] - origin["lat"]
    denom = dx * dx + dy * dy
    if denom < 1e-12:
        return 0.5
    t = ((lng - origin["lng"]) * dx + (lat - origin["lat"]) * dy) / denom
    return max(0.0, min(1.0, t))
