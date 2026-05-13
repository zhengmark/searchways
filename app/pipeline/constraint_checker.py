"""输出侧约束校验 — LLM 生成路线后检测违规并标记."""


# ── 违规检测规则 ──────────────────────────────────


def check_constraints(stops: list, narration: str, pois: list, user_constraints: dict = None) -> tuple:
    """检测路线是否违反用户约束.

    Args:
        stops: 站点名列表
        narration: LLM 解说文本
        pois: POI 详情列表 [{"name", "category", "rating", "price_per_person", ...}]
        user_constraints: 用户约束 {"diet": ["无辣","素食"...], "pace": "slow",
                                   "budget": "high", "interests": ["博物馆"...]}

    Returns:
        (violations, severity) — violations 列表, severity: "high"/"medium"/"low"/"none"
    """
    if not user_constraints:
        return ([], "none")

    violations = []
    combined = " ".join(stops) + " " + narration

    diet = user_constraints.get("diet", [])
    for d in diet:
        v = _check_diet(d, combined, stops, pois)
        if v:
            violations.append(v)

    pace = user_constraints.get("pace", "")
    if pace == "slow":
        if len(stops) > 3:
            violations.append(("高", f"站点过多({len(stops)}个)，不适合慢节奏/少走路需求"))
        # 检查是否有需要大量走路的场所
        for s in stops:
            if any(w in s for w in ["大唐芙蓉园", "大明宫", "城墙", "曲江池"]):
                violations.append(("中", f"含大面积景区'{s[:10]}'，可能需大量走路"))

    budget = user_constraints.get("budget", "")
    if budget == "high":
        cheap_pois = [
            p for p in pois if (p.get("price_per_person") or 999) < 100 and "咖啡" not in p.get("category", "")
        ]
        if len(cheap_pois) > len(pois) // 2:
            violations.append(("中", "半数以上POI人均<100，不满足高档需求"))
    elif budget == "low":
        expensive = [p for p in pois if (p.get("price_per_person") or 0) > 80]
        if len(expensive) > len(pois) // 2:
            violations.append(("中", "半数以上POI人均>80，超出低预算"))

    interests = user_constraints.get("interests", [])
    for interest in interests:
        if interest == "博物馆":
            if not any(w in combined for w in ["博物馆", "遗址", "古迹", "文物", "历史"]):
                violations.append(("高", "路线缺少博物馆/文化古迹"))
        if interest == "拍照":
            if not any(w in combined for w in ["景", "公园", "花", "展", "咖啡", "艺术", "寺"]):
                violations.append(("低", "路线可能缺少拍照打卡点"))
        if interest == "安静":
            if any(w in combined for w in ["KTV", "火锅", "广场舞", "夜市"]):
                violations.append(("中", "含嘈杂场所，不适合安静需求"))

    # 决定严重度
    severe_count = sum(1 for v in violations if v[0] == "高")
    if severe_count >= 2:
        severity = "high"
    elif severe_count >= 1 or len(violations) >= 3:
        severity = "medium"
    elif violations:
        severity = "low"
    else:
        severity = "none"

    return ([v[1] for v in violations], severity)


# ── 饮食约束检测 ──────────────────────────────────

_FORBIDDEN_KEYWORDS = {
    "无辣": ["火锅", "川菜", "麻辣", "辣椒", "水煮鱼", "毛血旺", "串串", "冒菜", "螺蛳粉"],
    "无冰": ["冰淇淋", "冰沙", "冰镇", "冷饮", "冰粉"],
    "无生冷": ["生鱼片", "刺身", "寿司", "凉皮", "沙拉", "冰"],
    "素食": ["肉", "鸡", "鱼", "虾", "蟹", "牛", "猪", "羊", "蛋", "奶", "海鲜", "火锅", "烧烤", "串"],
    "无油炸": ["炸鸡", "炸", "烧烤", "烤串", "烤鱼"],
    "清真": ["猪肉", "猪", "酒"],
}

_REQUIRED_KEYWORDS = {
    "高蛋白": ["鸡", "牛", "鱼", "虾", "蛋", "豆", "肉", "奶"],
    "甜品": ["甜", "奶茶", "糖", "蛋糕", "咖啡", "冰淇淋", "冰"],
    "喝茶": ["茶", "棋", "公园", "书吧"],
}


def _check_diet(diet_type: str, combined: str, stops: list, pois: list):
    """检查单个饮食约束."""
    # 禁止词
    forbidden = _FORBIDDEN_KEYWORDS.get(diet_type, [])
    for fw in forbidden:
        if fw in combined:
            # 检查是否在 stops 名称中（不在解说中的话可能是 LLM 脑补）
            in_stops = any(fw in s for s in stops)
            if in_stops:
                return ("高", f"含'{fw}'({_find_src(fw, stops)[:20]}): 违反{diet_type}")
            return ("低", f"解说提及'{fw}': 可能违反{diet_type}")

    # 必需词
    required = _REQUIRED_KEYWORDS.get(diet_type, [])
    if required:
        found = any(rw in combined for rw in required)
        if not found:
            return ("中", "缺少{}所需食物(期望:{})".format(diet_type, ",".join(required[:3])))

    # 特殊检查
    if diet_type == "要卫生间":
        has_mall = any(w in s for s in stops for w in ["商场", "购物", "广场", "大厦", "中心"])
        if not has_mall:
            return ("低", "无商场/购物中心，卫生间可能不便")
    if diet_type == "孕妇":
        if any(w in combined for w in ["辣", "火锅", "酒", "烟"]):
            return ("高", "含孕妇不适宜的辣/酒/烟")

    return None


def _find_src(keyword: str, stops: list) -> str:
    for s in stops:
        if keyword in s:
            return s
    return keyword


# ── 从用户输入提取约束 ──────────────────────────────

_CONSTRAINT_PATTERNS = {
    "无辣": ["不辣", "不吃辣", "不能吃辣", "不要辣", "微辣都不要", "无辣", "免辣", "不能辣", "不要吃辣"],
    "无冰": ["不冰", "不吃冰", "不能吃冰", "不喝冰", "不要冰", "不能冰"],
    "无生冷": ["不生吃", "不吃生", "不能吃生冷", "不吃凉的", "生冷", "不吃生冷"],
    "素食": ["素食", "不吃肉", "纯素", "不吃荤", "吃素", "素菜"],
    "无油炸": ["不油炸", "不吃油炸", "不吃炸", "不吃烧烤", "不烧烤"],
    "高蛋白": ["高蛋白", "健身餐", "低卡", "轻食", "健康餐"],
    "甜品": ["甜品", "奶茶", "甜的", "吃甜", "蛋糕", "糖水"],
    "喝茶": ["喝茶", "下棋", "茶社", "茶馆", "喝热茶"],
    "清真": ["清真", "回民", "halal"],
    "安静": ["安静", "不吵", "舒服坐下", "坐着", "不闹"],
    "拍照": ["拍照", "打卡", "发朋友圈", "好看", "网红"],
    "宵夜": ["宵夜", "深夜", "凌晨", "半夜", "晚上11", "晚上12", "晚一点吃"],
}

_PACE_PATTERNS = {
    "slow": [
        "不走",
        "膝盖",
        "腿脚",
        "不能走",
        "走不动",
        "少走",
        "走10分钟",
        "15分钟",
        "孕妇",
        "怀孕",
        "老人",
        "退休",
        "残障",
        "无障碍",
        "电梯",
        "不爬楼梯",
    ],
    "fast": ["快速", "赶时间", "速览", "高效"],
}

_BUDGET_PATTERNS = {
    "low": [
        "便宜",
        "40以内",
        "50以内",
        "30以内",
        "穷",
        "省钱",
        "免费",
        "低预算",
        "预算有限",
        "人均30",
        "人均40",
        "人均50",
        "不要太贵",
        "不贵",
        "平民",
        "经济实惠",
        "划算",
    ],
    "high": [
        "高档",
        "商务",
        "宴请",
        "包间",
        "人均200",
        "人均150",
        "米其林",
        "奢华",
        "吃顿好的",
        "贵的",
        "要面子",
        "请客",
        "客户",
        "重要",
        "体面",
    ],
}


def extract_constraints(user_input: str) -> dict:
    """从用户自然语言输入中提取约束."""
    diet = []
    for cat, patterns in _CONSTRAINT_PATTERNS.items():
        if any(p in user_input for p in patterns):
            diet.append(cat)

    pace = ""
    for p, patterns in _PACE_PATTERNS.items():
        if any(pat in user_input for pat in patterns):
            pace = p
            break
    # slow 优先级高，默认无 pace 约束
    if not pace:
        # 检查是否有步行相关关键词
        if any(w in user_input for w in ["走", "步行", "逛", "散步"]):
            pace = "normal"

    budget = ""
    for b, patterns in _BUDGET_PATTERNS.items():
        if any(pat in user_input for pat in patterns):
            budget = b
            break

    interests = []
    if any(w in user_input for w in ["博物馆", "古迹", "历史", "文化"]):
        interests.append("博物馆")
    if any(w in user_input for w in ["拍照", "打卡", "朋友圈", "好看", "网红"]):
        interests.append("拍照")
    if any(w in user_input for w in ["安静", "不吵", "舒服坐下"]):
        interests.append("安静")
    if any(w in user_input for w in ["孩子", "儿子", "女儿", "小孩", "儿童"]):
        interests.append("亲子")

    return {"diet": diet, "pace": pace, "budget": budget, "interests": interests}
