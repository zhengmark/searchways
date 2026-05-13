"""推荐理由引擎 — 为每个候选 POI 生成两部分推荐理由：结构化数据 + 用户需求匹配."""

from app.algorithms.geo import haversine

# 关键词 → 用户可读描述
_KW_DESCRIPTIONS = {
    "美食": "美食探索",
    "小吃": "地道小吃",
    "火锅": "火锅爱好者",
    "烧烤": "烧烤达人",
    "咖啡": "咖啡时光",
    "茶饮": "茶饮爱好者",
    "奶茶": "奶茶控",
    "甜品": "甜品控",
    "日料": "日料爱好者",
    "西餐": "西餐体验",
    "海鲜": "海鲜盛宴",
    "酒吧": "微醺时刻",
    "景点": "景点打卡",
    "公园": "公园漫步",
    "博物馆": "文化探索",
    "购物": "购物达人",
    "商场": "购物体验",
    "书店": "阅读时光",
    "图书馆": "安静阅读",
    "约会": "约会圣地",
    "拍照": "拍照出片",
    "安静": "静谧时光",
    "亲子": "亲子友好",
    "清真": "清真美食",
    "夜市": "夜市寻味",
    "面馆": "面食之旅",
    "泡馍": "泡馍体验",
    "穷游": "实惠之选",
    "带孩子": "亲子时光",
}

# 预算描述
_BUDGET_DESCRIPTIONS = {
    "low": "在您的经济预算范围内",
    "medium": "在您的中等预算范围内",
    "high": "符合您的高端体验需求",
}


# 距离评价
def _distance_eval(dist_km: float) -> str:
    if dist_km < 0.5:
        return "距起点步行可达"
    if dist_km < 1.5:
        return "距起点很近"
    if dist_km < 3:
        return "距起点适中"
    return "位于路线后半段"


def generate_poi_reasons(
    poi: dict, keywords: list[str], budget: str, origin_coords: tuple | None = None, user_profile: dict | None = None
) -> dict[str, str]:
    """为一个 POI 生成两部分推荐理由.

    Returns:
        {"structured": str, "user_need": str}
    """
    return {
        "structured": _structured_reason(poi, origin_coords),
        "user_need": _user_need_reason(poi, keywords, budget, origin_coords),
    }


def _structured_reason(poi: dict, origin_coords: tuple | None) -> str:
    """结构化理由：评分 | 人均 | 品类 | 距离."""
    parts = []

    rating = poi.get("rating")
    if rating is not None and rating > 0:
        parts.append(f"评分{round(rating, 1)}")

    price = poi.get("price_per_person")
    if price is not None and price > 0:
        parts.append(f"人均{int(price)}元")

    cat = poi.get("category", "")
    if cat:
        # 取最后一段品类名
        short_cat = cat.split(";")[-1] if ";" in cat else cat
        parts.append(short_cat)

    if origin_coords:
        poi_lat = poi.get("lat")
        poi_lng = poi.get("lng")
        if poi_lat is not None and poi_lng is not None:
            dist = haversine(origin_coords[0], origin_coords[1], poi_lat, poi_lng)
            parts.append(f"距起点{dist / 1000:.1f}km")

    return " | ".join(parts) if parts else ""


def _user_need_reason(poi: dict, keywords: list[str], budget: str, origin_coords: tuple | None) -> str:
    """用户需求匹配理由：为什么这个 POI 适合你."""
    reasons = []

    # 品类匹配关键词
    cat = poi.get("category", "").lower()
    name = poi.get("name", "").lower()
    text = cat + " " + name

    for kw in keywords or []:
        if kw in _KW_DESCRIPTIONS and _keyword_hit(kw, text):
            reasons.append(f"完美匹配您的「{_KW_DESCRIPTIONS[kw]}」需求")

    # 评分亮点
    rating = poi.get("rating")
    if rating is not None and rating >= 4.5:
        reasons.append("该区域评分最高的选择之一")
    elif rating is not None and rating >= 4.0:
        reasons.append("口碑优秀的高评分选择")

    # 价格匹配
    price = poi.get("price_per_person")
    if budget and budget in _BUDGET_DESCRIPTIONS and price is not None:
        lo = {"low": 0, "medium": 30, "high": 80}.get(budget, 0)
        hi = {"low": 50, "medium": 150, "high": 9999}.get(budget, 9999)
        if lo <= price <= hi:
            reasons.append(_BUDGET_DESCRIPTIONS[budget])

    # 距离评价
    if origin_coords:
        poi_lat = poi.get("lat")
        poi_lng = poi.get("lng")
        if poi_lat is not None and poi_lng is not None:
            dist = haversine(origin_coords[0], origin_coords[1], poi_lat, poi_lng) / 1000
            reasons.append(_distance_eval(dist))

    # 去重
    unique = list(dict.fromkeys(reasons))
    return "；".join(unique[:3]) if unique else "路线沿途推荐选择"


def _keyword_hit(kw: str, text: str) -> bool:
    """检查关键词是否命中 POI 文本."""
    # 直接子串匹配
    if kw in text:
        return True
    # 扩展词匹配
    expanded = {
        "美食": ["餐饮", "中餐", "火锅", "烧烤", "小吃", "面", "泡馍", "凉皮", "串串", "麻辣烫", "陕菜", "西北菜"],
        "咖啡": ["咖啡", "café"],
        "小吃": ["小吃", "串串", "麻辣烫", "凉皮", "肉夹馍", "泡馍"],
        "日料": ["日料", "寿司", "刺身", "居酒屋", "日式"],
        "西餐": ["西餐", "牛排", "披萨", "意面", "汉堡"],
        "景点": ["景点", "风景", "公园", "博物馆", "寺庙", "城墙", "塔", "遗址"],
        "购物": ["购物", "商场", "购物中心", "专卖"],
        "拍照": ["景点", "公园", "咖啡", "图书馆", "博物馆", "城墙"],
        "亲子": ["公园", "博物馆", "动物园", "游乐园", "图书馆"],
        "约会": ["咖啡", "甜品", "公园", "西餐", "日料", "酒吧"],
    }
    for ext_kw in expanded.get(kw, []):
        if ext_kw in text:
            return True
    return False
