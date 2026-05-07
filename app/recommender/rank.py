"""精排 — 多因子加权打分，支持路线走廊感知."""
import math
from app.algorithms.geo import haversine


def _corridor_distance(lat: float, lng: float,
                       o_lat: float, o_lng: float,
                       d_lat: float, d_lng: float) -> int:
    """计算点到 O→D 线段的最短距离（米）."""
    dx = d_lng - o_lng
    dy = d_lat - o_lat
    seg_len2 = dx * dx + dy * dy
    if seg_len2 < 1e-12:
        return haversine(lat, lng, o_lat, o_lng)
    # 投影参数 t，钳位到 [0, 1]
    t = max(0.0, min(1.0, ((lng - o_lng) * dx + (lat - o_lat) * dy) / seg_len2))
    proj_lng = o_lng + t * dx
    proj_lat = o_lat + t * dy
    return haversine(lat, lng, proj_lat, proj_lng)


def _route_progress(lat: float, lng: float,
                    o_lat: float, o_lng: float,
                    d_lat: float, d_lng: float) -> float:
    """计算 POI 在 O→D 方向上的进度 [0,1]，越均匀分布越好."""
    dx = d_lng - o_lng
    dy = d_lat - o_lat
    seg_len2 = dx * dx + dy * dy
    if seg_len2 < 1e-12:
        return 0.5
    t = ((lng - o_lng) * dx + (lat - o_lat) * dy) / seg_len2
    return max(0.0, min(1.0, t))


def score_poi(poi: dict, o_lat: float, o_lng: float,
              d_lat: float = None, d_lng: float = None,
              user_prefs: dict = None,
              already_picked: list = None) -> float:
    """对单个 POI 打分 (0~1)，支持路线走廊感知.

    因子:
        - 评分 (35%): rating / 5.0
        - 走廊距离 (30%): 距 O→D 线段的高斯衰减 (σ=5km)
        - 品类匹配 (25%): 用户兴趣 + 目标品类匹配
        - 价格匹配 (10%): 价格区间匹配
        - 多样性 (+0.05): 与已选 POI 不同品类加分
    """
    user_prefs = user_prefs or {}
    score = 0.0

    # 评分因子 (35%)
    rating = poi.get("rating") or 3.0
    score += 0.35 * (rating / 5.0)

    # 走廊距离 (30%, σ=5km 适应城市尺度)
    if d_lat is not None and d_lng is not None:
        d = _corridor_distance(poi["lat"], poi["lng"], o_lat, o_lng, d_lat, d_lng)
    else:
        d = haversine(o_lat, o_lng, poi["lat"], poi["lng"])
    dist_factor = math.exp(-(d ** 2) / (2 * 5000 ** 2))
    score += 0.30 * dist_factor

    # 品类匹配 (25%)
    interests = user_prefs.get("interests", [])
    target_cats = user_prefs.get("target_categories", [])
    all_cats = interests + target_cats
    if all_cats:
        cat = poi.get("category", "") or ""
        subcat = poi.get("subcategory", "") or ""
        combined = f"{cat} {subcat}"
        matches = sum(1 for kw in all_cats if kw in combined)
        score += 0.25 * min(matches / max(len(all_cats), 1), 1.0)

    # 价格匹配 (10%)
    budget = user_prefs.get("budget_level", "medium")
    price = poi.get("price_per_person") or 0
    budget_ranges = {"low": (0, 50), "medium": (30, 150), "high": (80, 9999)}
    lo, hi = budget_ranges.get(budget, (0, 9999))
    if lo <= price <= hi:
        score += 0.10
    elif price > 0:
        score += 0.05

    # 多样性加成: 与已选 POI 不同品类 +0.05
    if already_picked:
        picked_cats = {p.get("category", "") for p in already_picked}
        if poi.get("category", "") not in picked_cats:
            score += 0.05

    return score


def rank_candidates(candidates: list, o_lat: float, o_lng: float,
                    d_lat: float = None, d_lng: float = None,
                    user_prefs: dict = None, top_k: int = 10) -> list:
    """对候选集打分排序，带多样性贪心选择."""
    if not candidates:
        return []

    user_prefs = user_prefs or {}
    scored = []
    for p in candidates:
        s = score_poi(p, o_lat, o_lng, d_lat, d_lng, user_prefs)
        scored.append({**p, "_rank_score": s})
    scored.sort(key=lambda x: -x["_rank_score"])

    # 贪心多样性：逐步选择，每次重算已选集合的多样性加成
    if top_k >= len(scored):
        return scored

    picked = []
    remaining = scored[:]
    while len(picked) < top_k and remaining:
        best = remaining[0]
        best_idx = 0
        for i, p in enumerate(remaining):
            s = score_poi(p, o_lat, o_lng, d_lat, d_lng, user_prefs, already_picked=picked)
            if s > best.get("_div_score", best["_rank_score"]):
                best = {**p, "_div_score": s}
                best_idx = i
        picked.append(best)
        remaining.pop(best_idx)

    return picked
