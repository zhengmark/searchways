"""Shared POI filtering logic — used by both old and new pipeline."""
from app.shared.constants import CATEGORY_BLACKLIST
from app.algorithms.geo import haversine


def normalize_keywords(kw_list: list) -> list:
    """规范化搜索关键词：单字泛词 → 具体可搜索词，去重保序."""
    from app.shared.constants import KW_NORMALIZE
    normalized = []
    for k in kw_list:
        if k in KW_NORMALIZE:
            normalized.extend(KW_NORMALIZE[k].split(","))
        else:
            found = False
            for key, val in KW_NORMALIZE.items():
                if key in k:
                    normalized.extend(val.split(","))
                    found = True
                    break
            if not found:
                normalized.append(k)
    return list(dict.fromkeys(normalized))


def filter_by_category(pois: list) -> list:
    """过滤掉品类黑名单中的 POI（同时检查 category, type, 和 name）."""
    return [
        p for p in pois
        if not any(b in (p.get("category", "") + p.get("type", "") + p.get("name", ""))
                   for b in CATEGORY_BLACKLIST)
    ]


def filter_by_coords(pois: list) -> list:
    """过滤掉没有有效坐标的 POI."""
    return [
        p for p in pois
        if p.get("lat") is not None and p.get("lng") is not None
    ]


def filter_near_anchor(pois: list, anchor_coords: tuple, anchor_name: str = "",
                       min_distance: int = 100) -> list:
    """过滤掉距锚点过近或同名的 POI（避免起终点自己入选）."""
    if not anchor_coords:
        return pois
    result = []
    for p in pois:
        d = haversine(anchor_coords[0], anchor_coords[1], p["lat"], p["lng"])
        if d >= min_distance and anchor_name not in p.get("name", ""):
            result.append(p)
    return result


def deduplicate_by_name(pois: list) -> list:
    """按名称去重，保序."""
    seen = set()
    unique = []
    for p in pois:
        n = p.get("name", "")
        if n and n not in seen:
            seen.add(n)
            unique.append(p)
    return unique


dedup_pois = deduplicate_by_name  # cluster_tools.py 兼容别名
