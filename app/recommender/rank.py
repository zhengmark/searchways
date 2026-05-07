"""精排 — 多因子加权打分."""
import math
from app.algorithms.geo import haversine


def score_poi(poi: dict, ref_lat: float, ref_lng: float,
              user_prefs: dict = None) -> float:
    """对单个 POI 打分 (0~1).

    因子:
        - 评分 (40%): rating / 5.0
        - 距离衰减 (30%): 距离 ref 点的高斯衰减
        - 品类匹配 (20%): 用户兴趣匹配
        - 价格匹配 (10%): 价格区间匹配
    """
    user_prefs = user_prefs or {}
    score = 0.0

    # 评分因子
    rating = poi.get("rating") or 3.0
    score += 0.4 * (rating / 5.0)

    # 距离衰减 (sigma = 2km)
    d = haversine(ref_lat, ref_lng, poi["lat"], poi["lng"])
    dist_factor = math.exp(-(d ** 2) / (2 * 2000 ** 2))
    score += 0.3 * dist_factor

    # 品类匹配
    interests = user_prefs.get("interests", [])
    if interests:
        cat = poi.get("category", "")
        matches = sum(1 for kw in interests if kw in cat)
        score += 0.2 * min(matches / max(len(interests), 1), 1.0)

    # 价格匹配
    budget = user_prefs.get("budget_level", "medium")
    price = poi.get("price_per_person") or 0
    budget_ranges = {"low": (0, 50), "medium": (30, 150), "high": (80, 9999)}
    lo, hi = budget_ranges.get(budget, (0, 9999))
    if lo <= price <= hi:
        score += 0.1
    elif price > 0:
        score += 0.05

    return score


def rank_candidates(candidates: list, ref_lat: float, ref_lng: float,
                    user_prefs: dict = None, top_k: int = 10) -> list:
    """对候选集打分排序."""
    scored = []
    for p in candidates:
        s = score_poi(p, ref_lat, ref_lng, user_prefs)
        scored.append({**p, "_rank_score": s})

    scored.sort(key=lambda x: -x["_rank_score"])
    return scored[:top_k]
