"""属性聚类 — 简化版 KMeans，按评分/价格/品类将 POI 分为相似群组."""
import random
import math


def attr_cluster(pois: list, k: int = 5, max_iters: int = 20) -> list[int]:
    """KMeans 按 (rating, price, category_hot) 属性聚类.

    Args:
        pois: 每个需含 rating, price_per_person, category 字段
        k: 簇数
        max_iters: 最大迭代次数

    Returns:
        labels: 每个 POI 的簇标签 (0..k-1)
    """
    n = len(pois)
    if n <= k:
        return list(range(n)) + [0] * (k - n)

    # 特征向量: [rating_norm, price_norm, cat_hash_norm]
    _CATS = ["餐饮", "购物", "景点", "休闲", "文化"]
    features = []
    for p in pois:
        rating = (p.get("rating") or 3.0) / 5.0
        price = min((p.get("price_per_person") or 50) / 200.0, 1.0)
        cat = p.get("category", "")
        cat_idx = next((i for i, c in enumerate(_CATS) if c in cat), len(_CATS))
        cat_norm = cat_idx / max(len(_CATS), 1)
        features.append([rating, price, cat_norm])

    # 随机初始化质心
    indices = list(range(n))
    random.shuffle(indices)
    centroids = [features[i] for i in indices[:k]]

    for _ in range(max_iters):
        # 分配
        labels = []
        for f in features:
            best_c, best_d = 0, float("inf")
            for ci, c in enumerate(centroids):
                d = sum((a - b) ** 2 for a, b in zip(f, c))
                if d < best_d:
                    best_d = d
                    best_c = ci
            labels.append(best_c)

        # 更新质心
        new_centroids = [[0.0] * len(features[0]) for _ in range(k)]
        counts = [0] * k
        for i, label in enumerate(labels):
            counts[label] += 1
            for j, v in enumerate(features[i]):
                new_centroids[label][j] += v

        moved = False
        for ci in range(k):
            if counts[ci] > 0:
                new_c = [v / counts[ci] for v in new_centroids[ci]]
                if any(abs(a - b) > 0.001 for a, b in zip(new_c, centroids[ci])):
                    moved = True
                centroids[ci] = new_c

        if not moved:
            break

    return labels


def attr_similarity(poi_a: dict, poi_b: dict) -> float:
    """计算两个 POI 的属性相似度 (0~1)."""
    score = 0.0
    # 品类匹配
    cat_a = poi_a.get("category", "")
    cat_b = poi_b.get("category", "")
    if cat_a and cat_b:
        cats_a = set(cat_a.split(";"))
        cats_b = set(cat_b.split(";"))
        overlap = len(cats_a & cats_b)
        if overlap > 0:
            score += 0.4 * overlap / max(len(cats_a | cats_b), 1)

    # 价格接近
    price_a = poi_a.get("price_per_person") or 50
    price_b = poi_b.get("price_per_person") or 50
    price_diff = abs(price_a - price_b)
    if price_diff < 20:
        score += 0.3
    elif price_diff < 50:
        score += 0.15

    # 评分接近
    rating_a = poi_a.get("rating") or 3.5
    rating_b = poi_b.get("rating") or 3.5
    if abs(rating_a - rating_b) < 0.5:
        score += 0.3
    elif abs(rating_a - rating_b) < 1.0:
        score += 0.15

    return min(score, 1.0)
