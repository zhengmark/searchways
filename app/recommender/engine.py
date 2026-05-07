"""推荐引擎 — 统一推荐入口，整合召回+精排."""
from app.recommender.recall import multi_recall
from app.recommender.rank import rank_candidates


def recommend(pois: list, lat: float, lng: float,
              clusters: list[dict] = None,
              target_categories: list = None,
              user_prefs: dict = None,
              top_k: int = 10) -> list:
    """推荐 POI 列表.

    Args:
        pois: 全量 POI 列表
        lat, lng: 参考点坐标
        clusters: 预计算的地理聚类结果
        target_categories: 目标品类列表
        user_prefs: 用户偏好 {"interests": [...], "budget_level": "medium"}
        top_k: 返回数量

    Returns:
        排序后的 POI 列表
    """
    if not pois:
        return []

    # 召回
    candidates = multi_recall(
        pois, lat, lng,
        clusters=clusters or [],
        target_categories=target_categories,
        total_k=max(top_k * 5, 30),
    )

    # 精排
    ranked = rank_candidates(candidates, lat, lng, user_prefs, top_k=top_k)
    return ranked
