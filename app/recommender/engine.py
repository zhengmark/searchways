"""推荐引擎 — 统一推荐入口，整合召回+精排."""
from app.recommender.recall import multi_recall
from app.recommender.rank import rank_candidates


def recommend(pois: list, o_lat: float, o_lng: float,
              d_lat: float = None, d_lng: float = None,
              clusters: list[dict] = None,
              target_categories: list = None,
              user_prefs: dict = None,
              top_k: int = 10) -> list:
    """推荐 POI 列表.

    Args:
        pois: 全量 POI 列表
        o_lat, o_lng: 起点坐标
        d_lat, d_lng: 终点坐标（可选，提供时启用走廊感知打分）
        clusters: 预计算的地理聚类结果
        target_categories: 目标品类列表
        user_prefs: 用户偏好 {"interests": [...], "budget_level": "medium"}
        top_k: 返回数量

    Returns:
        排序后的 POI 列表
    """
    if not pois:
        return []

    # 将 target_categories 注入 user_prefs 供 rank 使用
    if target_categories:
        user_prefs = dict(user_prefs or {})
        user_prefs.setdefault("target_categories", [])
        user_prefs["target_categories"] = list(set(
            user_prefs.get("target_categories", []) + target_categories
        ))

    # 召回
    candidates = multi_recall(
        pois, o_lat, o_lng,
        d_lat=d_lat, d_lng=d_lng,
        clusters=clusters or [],
        target_categories=target_categories,
        total_k=max(top_k * 5, 30),
    )

    # 精排
    ranked = rank_candidates(
        candidates, o_lat, o_lng,
        d_lat=d_lat, d_lng=d_lng,
        user_prefs=user_prefs,
        top_k=top_k,
    )
    return ranked
