"""地理聚类 — 简化版 DBSCAN，按经纬度将 POI 聚为"热区"."""

from app.algorithms.geo import haversine


def geo_cluster(pois: list, eps_meters: float = 500, min_samples: int = 3) -> list[dict]:
    """DBSCAN 地理聚类，返回每个 POI 的 cluster_id 和簇中心.

    Args:
        pois: [{"lat": ..., "lng": ..., ...}, ...]
        eps_meters: 邻域半径（米）
        min_samples: 最小簇大小

    Returns:
        [{"cluster_id": int, "center_lat": float, "center_lng": float, "size": int, "pois": [...]}, ...]
    """
    n = len(pois)
    if n == 0:
        return []

    # 邻域查找
    def _neighbors(i):
        return [
            j
            for j in range(n)
            if j != i and haversine(pois[i]["lat"], pois[i]["lng"], pois[j]["lat"], pois[j]["lng"]) <= eps_meters
        ]

    labels = [-1] * n  # -1 = 噪声
    cluster_id = 0

    for i in range(n):
        if labels[i] != -1:
            continue
        neigh = _neighbors(i)
        if len(neigh) < min_samples:
            labels[i] = -1  # 噪声点
            continue

        # 扩展簇
        labels[i] = cluster_id
        queue = list(neigh)
        idx = 0
        while idx < len(queue):
            j = queue[idx]
            idx += 1
            if labels[j] == -1:
                labels[j] = cluster_id
                n2 = _neighbors(j)
                if len(n2) >= min_samples:
                    queue.extend(n2)
            elif labels[j] == -1:
                labels[j] = cluster_id
        cluster_id += 1

    # 聚合簇信息
    clusters = {}
    for i, cid in enumerate(labels):
        if cid == -1:
            continue
        if cid not in clusters:
            clusters[cid] = {"pois": [], "lats": [], "lngs": []}
        clusters[cid]["pois"].append(pois[i])
        clusters[cid]["lats"].append(pois[i]["lat"])
        clusters[cid]["lngs"].append(pois[i]["lng"])

    return [
        {
            "cluster_id": cid,
            "center_lat": sum(info["lats"]) / len(info["lats"]),
            "center_lng": sum(info["lngs"]) / len(info["lngs"]),
            "size": len(info["pois"]),
            "pois": info["pois"],
        }
        for cid, info in clusters.items()
    ]


def find_nearest_cluster(lat: float, lng: float, clusters: list[dict]) -> dict | None:
    """找到距离给定坐标最近的簇."""
    if not clusters:
        return None
    best, best_dist = None, float("inf")
    for c in clusters:
        d = haversine(lat, lng, c["center_lat"], c["center_lng"])
        if d < best_dist:
            best_dist = d
            best = c
    return best
