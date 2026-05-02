"""修改意图识别 Agent —— 多轮对话中判断用户想改什么.

策略：规则匹配为主（快速、稳定），LLM 兜底（处理复杂/模糊表达）.
"""

import json
import re

from pydantic import BaseModel, Field

from agent.llm_client import call_llm

# ── 修改意图数据结构 ────────────────────────────────

class ModificationIntent(BaseModel):
    """单次修改意图."""
    change_type: str = "none"          # change_origin / change_destination / change_keywords /
                                       # change_num_stops / change_preferences / change_poi_location /
                                       # adjust_constraint / new_route / none
    params: dict = Field(default_factory=dict)  # 修改参数（如 {"origin": "钟楼"}）
    confidence: float = 1.0            # 0-1，规则匹配 1.0，LLM 兜底可能 <1.0
    reasoning: str = ""                # 推理说明


# ── 规则引擎 ────────────────────────────────────────

# 起点修改
_ORIGIN_PATTERNS = [
    (r"(?:起点|出发地?|从|在)(?:改成?|换成?|换到|改为?|变更为?|变到)?[「「]?(.{2,20}?)[」」]?(?:出发|走|开始|作为起点)", "change_origin"),
    (r"(?:从|在)(.{2,20})(?:出发|走|开始)", "change_origin"),
    (r"(?:换|改|变)(?:个?|一下?)?起点.*?(?:到|为|成)?(.{2,20})", "change_origin"),
]

#  终点修改
_DEST_PATTERNS = [
    (r"(?:终点|目的地?|去)(?:改成?|换成?|换到|改为?|变更为?)?[「「]?(.{2,20}?)[」」]?(?:吧|呢)?$", "change_destination"),
    (r"(?:改|换|变)(?:个?|一下?)?(?:终点|目的地?).*?(?:到|为|成)?(.{2,20})", "change_destination"),
    (r"去(.{2,20})(?:吧|算了|好了|就行了)", "change_destination"),
    (r"(?:改成?|换成?|换到|改为?)去(.{2,20})", "change_destination"),
]

# 关键词/口味修改
_KEYWORD_PATTERNS = [
    (r"(?:想|要|换|改|变)(?:吃|喝|玩|逛|找|尝)(.{1,10})", "change_keywords"),
    (r"(?:不要|不吃|不想|去掉|别).{0,5}(.{1,10})", "change_keywords"),
    (r"(?:换成?|改[成为]?|变更为?)(?:搜索|关键词|品类).{0,5}(.{1,10})", "change_keywords"),
    (r"(?:找|搜)(?:个?|一下?)?(.{1,10})(?:的|吧|啊)?", "change_keywords"),
]

# 站点数量修改
_NUM_STOPS_PATTERNS = [
    (r"(?:再加?|多[加去]|增加)\s*(\d+|[一二三四五六七八九十])\s*[个站]", "change_num_stops"),
    (r"(?:少[去加]|减少|去掉|减[少到])\s*(\d+|[一二三四五六七八九十])\s*[个站]", "change_num_stops"),
    (r"(?:只要|只要去|就去)\s*(\d+|[一二三四五六七八九十])\s*[个站]", "change_num_stops"),
]

# 体力/预算等偏好修改
_PREFERENCE_PATTERNS = [
    (r"少走[点些]?路|不想走|腿[脚腿]|太[远累]|走[不]动|不能走|太晒", "change_preferences"),
    (r"便宜[点些]?|省钱|预算|人均.{0,3}[低少小]|不要太贵", "change_preferences"),
    (r"带[着了]?[老爸妈孩子孙爷奶外]|有[老爸妈孩子孙爷奶外]|亲子|遛娃", "change_preferences"),
    (r"体力|能[力量]|精神|暴走|慢慢逛|悠闲|轻松|紧凑", "change_preferences"),
]

# POI 位置调整
_POI_LOCATION_PATTERNS = [
    (r"(?:离|在|靠近|挨着)(.{2,20}?)(?:近|附近|周边)", "change_poi_location"),
    (r"(?:换|改)[到成]?.{0,10}(?:离|在|靠)(.{2,20})近", "change_poi_location"),
]

# 时间/距离约束
_CONSTRAINT_PATTERNS = [
    (r"(\d+(?:\.\d+)?)\s*(?:小时|h|H)(?:以内|之内|左右|搞定|完成)", "adjust_constraint"),
    (r"控制[在到]?\s*(\d+(?:\.\d+)?)\s*(?:小时|h|H)", "adjust_constraint"),
    (r"不要太远|近一点|控制在.{0,5}(?:km|公里|米|m)", "adjust_constraint"),
]

# 全新路线
_NEW_ROUTE_PATTERNS = [
    (r"(?:重新|再来|换一[条个]|从头|全新).{0,5}(?:规划|路线|来|算)", "new_route"),
    (r"不要(?:这个|这条|这个路线)|换条路线|换一个方案", "new_route"),
]

_ALL_RULES = (
    _ORIGIN_PATTERNS + _DEST_PATTERNS + _KEYWORD_PATTERNS +
    _NUM_STOPS_PATTERNS + _PREFERENCE_PATTERNS + _POI_LOCATION_PATTERNS +
    _CONSTRAINT_PATTERNS + _NEW_ROUTE_PATTERNS
)

# 数字映射
_NUM_MAP = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _extract_num(text: str) -> int | None:
    if text.isdigit():
        return int(text)
    return _NUM_MAP.get(text)


def detect_by_rules(user_input: str) -> ModificationIntent | None:
    """规则匹配修改意图。返回 None 表示规则未命中，需要 LLM 兜底."""
    for pattern, change_type in _ALL_RULES:
        m = re.search(pattern, user_input)
        if m:
            params = {}
            captured = m.group(1).strip() if m.lastindex else ""

            if change_type == "change_origin":
                params["origin"] = captured
            elif change_type == "change_destination":
                params["destination"] = captured
            elif change_type == "change_keywords":
                params["keywords"] = captured
            elif change_type == "change_num_stops":
                num = _extract_num(captured)
                if num is not None:
                    params["num_stops"] = num
            elif change_type == "change_preferences":
                params["preference_hint"] = user_input  # 整句传给 LLM 解析
            elif change_type == "change_poi_location":
                params["anchor"] = captured
            elif change_type == "adjust_constraint":
                try:
                    params["time_budget_hours"] = float(captured)
                except ValueError:
                    params["constraint_hint"] = user_input
            elif change_type == "new_route":
                pass

            return ModificationIntent(
                change_type=change_type,
                params=params,
                confidence=1.0,
                reasoning=f"规则匹配: {pattern}",
            )

    return None


# ── LLM 兜底 ─────────────────────────────────────────

_MODIFIER_SYSTEM = """你是一个路线修改意图识别专家。用户的输入可能涉及修改已有路线方案的某个方面。

请分析用户意图，归入以下类型之一：
- change_origin: 改起点
- change_destination: 改终点
- change_keywords: 改搜索关键词/口味/品类
- change_num_stops: 改站点数量
- change_preferences: 改偏好（体力/预算/群体）
- change_poi_location: 换个区域的 POI
- adjust_constraint: 调整时间/距离约束
- new_route: 完全重新规划
- none: 以上都不是，可能是全新出行需求

只输出 JSON，格式：{"change_type": "...", "params": {...}, "reasoning": "..."}"""


def detect_by_llm(user_input: str, current_context: dict) -> ModificationIntent:
    """LLM 兜底识别修改意图."""
    context_desc = (
        f"当前城市：{current_context.get('city', '未知')}\n"
        f"当前起点：{current_context.get('origin', '未指定')}\n"
        f"当前终点：{current_context.get('destination', '未指定')}\n"
        f"当前站点数：{current_context.get('num_stops', '未指定')}\n"
        f"当前关键词：{current_context.get('keywords', '未指定')}\n"
        f"当前偏好：{current_context.get('preferences', {})}\n"
    )

    prompt = f"""分析以下用户输入，判断 ta 想修改路线的哪个方面。

【当前路线上下文】
{context_desc}

【用户输入】
{user_input}

请输出 JSON。"""

    try:
        data = call_llm(
            messages=[{"role": "user", "content": prompt}],
            system=_MODIFIER_SYSTEM,
            max_tokens=200,
        )
        text = data["content"][0]["text"].strip()
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            parsed = json.loads(m.group(0))
            return ModificationIntent(
                change_type=parsed.get("change_type", "none"),
                params=parsed.get("params", {}),
                confidence=0.7,
                reasoning=parsed.get("reasoning", ""),
            )
    except Exception:
        pass

    return ModificationIntent(change_type="none", confidence=0.0)


# ── 主入口 ──────────────────────────────────────────

def detect_modification(user_input: str, current_context: dict = None) -> ModificationIntent:
    """检测用户输入是否为路线修改意图.

    优先规则匹配，未命中时 LLM 兜底。

    Args:
        user_input: 用户当前输入
        current_context: 当前路线上下文（city, origin, destination 等），用于 LLM 兜底

    Returns:
        ModificationIntent，change_type="none" 表示新需求而非修改
    """
    if current_context is None:
        current_context = {}

    # 优先规则匹配
    result = detect_by_rules(user_input)
    if result is not None:
        return result

    # LLM 兜底
    return detect_by_llm(user_input, current_context)
