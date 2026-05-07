"""多路召回 — 地理簇 + 品类 + 高评分 三路召回候选 POI."""
from app.algorithms.geo import haversine
from app.clustering.geo_cluster import find_nearest_cluster


def recall_by_geo_cluster(lat: float, lng: float, clusters: list[dict],
                          radius_m: float = 3000, top_k: int = 20) -> list:
    """从最近的地理簇召回候选 POI."""
    cluster = find_nearest_cluster(lat, lng, clusters)
    if not cluster:
        return []

    candidates = []
    for p in cluster["pois"]:
        d = haversine(lat, lng, p["lat"], p["lng"])
        if d <= radius_m:
            candidates.append({**p, "_recall_score": 1.0 - d / radius_m})
            candidates.sort(key=lambda x: -x["_recall_score"])
    return candidates[:top_k]


def recall_by_category(pois: list, target_categories: list, top_k: int = 30) -> list:
    """按品类召回，匹配越多越好."""
    if not target_categories:
        return sorted(pois, key=lambda p: -(p.get("rating") or 0))[:top_k]

    scored = []
    for p in pois:
        cat = p.get("category", "")
        hits = sum(1 for tc in target_categories if tc in cat)
        if hits > 0:
            scored.append({**p, "_recall_score": hits * 0.3 + (p.get("rating") or 0) * 0.1})

    scored.sort(key=lambda x: -x["_recall_score"])
    return scored[:top_k]


def recall_by_rating(pois: list, min_rating: float = 4.0, top_k: int = 20) -> list:
    """高评分召回."""
    candidates = [
        {**p, "_recall_score": (p.get("rating") or 0) / 5.0}
        for p in pois
        if (p.get("rating") or 0) >= min_rating
    ]
    candidates.sort(key=lambda x: -x["_recall_score"])
    return candidates[:top_k]


def multi_recall(pois: list, lat: float, lng: float, clusters: list[dict],
                 target_categories: list = None,
                 radius_m: float = 3000, total_k: int = 50) -> list:
    """三路召回 + 合并去重."""
    seen = set()
    results = []

    # 路1: 地理簇
    for p in recall_by_geo_cluster(lat, lng, clusters, radius_m, top_k=total_k // 2):
        if p["name"] not in seen:
            seen.add(p["name"])
            p["_recall_channel"] = "geo"
            results.append(p)

    # 路2: 品类匹配
    for p in recall_by_category(pois, target_categories or [], top_k=total_k // 2):
        if p["name"] not in seen:
            seen.add(p["name"])
            p["_recall_channel"] = "category"
            results.append(p)

    # 路3: 高评分
    for p in recall_by_rating(pois, min_rating=4.2, top_k=total_k // 3):
        if p["name"] not in seen:
            seen.add(p["name"])
            p["_recall_channel"] = "rating"
            results.append(p)

    return results[:total_k]
