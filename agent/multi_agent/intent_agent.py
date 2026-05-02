"""意图理解 Agent —— 深度解析用户出行需求，推断用户画像."""
import json
import re

from agent.llm_client import call_llm
from agent.multi_agent.types import IntentResult, UserProfile
from agent.tools.constants import INTENT_PLACEHOLDERS

INTENT_SYSTEM_PROMPT = """你是一个出行意图分析专家。你的任务是深度理解用户的出行需求，而不仅仅是提取关键词。

你需要做到：
1. **提取显式信息**：起点、终点、关键词、站点数量
2. **推断隐含偏好**：根据用户描述推断群体类型、体力水平、预算、兴趣偏好
3. **补充搜索提示**：给下游搜索 Agent 提供具体的搜索方向（如"优先找安静的老字号茶馆"）

群体类型推断规则：
- "带我妈/奶奶/爷爷/父母/老人" → family + 安静偏好 + 低步行 + 无障碍需求
- "带孩子/遛娃/亲子" → family + 亲子友好 + 有活动空间
- "和女朋友/约会" → couple + 浪漫/有氛围/适合拍照
- "和朋友/兄弟/闺蜜/聚餐" → friends + 社交/网红/性价比
- "一个人/独自/一人游" → solo + 深度/探索/自由

体力水平推断：
- "不想走太多路/腿脚不便/不能走远" → low
- "徒步/暴走/多走" → high
- 未提及 → medium

只输出 JSON，不要 markdown 代码块。"""


def run_intent_agent(user_input: str, city: str) -> IntentResult:
    """深度解析用户出行意图."""
    prompt = f"""分析以下出行需求，输出结构化 JSON。

城市：{city}
用户需求：{user_input}

输出格式：
{{
    "origin": "起点地名（保留原文完整名称，不可截断或缩写）",
    "destination": "终点地名或空",
    "keywords": ["关键词1", "关键词2"],
    "num_stops": 3,
    "date": "日期或空",
    "time_budget_hours": 3.0,
    "user_profile": {{
        "group_type": "solo/couple/family/friends",
        "age_preference": "all/young/middle/senior",
        "energy_level": "low/medium/high",
        "budget_level": "low/medium/high",
        "interests": ["美食", "文化", "自然"],
        "notes": "补充说明（如安静偏好、无障碍需求）"
    }},
    "preference_reasoning": "一句话说明推断依据",
    "search_hints": ["搜索提示1", "搜索提示2"]
}}

重要规则：
- origin/destination 必须保留用户输入中的完整地名原文，禁止截断或缩写
- num_stops: 把用户提到的数量加起来（如「2个餐厅+最后去1个夜景」→3）。精确提取数字，用户没说时填 3
- keywords 必须是具体可搜索词，不要用「有特色的地方」这种模糊词或单字「吃」「喝」
- 用户说"随便逛逛/什么都行/无所谓"时，keywords 填 ["美食", "景点"]，不要留空
- 只输出 JSON"""

    try:
        data = call_llm(
            messages=[{"role": "user", "content": prompt}],
            system=INTENT_SYSTEM_PROMPT,
            max_tokens=400,
        )
        text = data["content"][0]["text"].strip()
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise ValueError("No JSON found in response")
        parsed = json.loads(m.group(0))

        if parsed.get("origin") in INTENT_PLACEHOLDERS:
            parsed["origin"] = ""
        if parsed.get("destination") in INTENT_PLACEHOLDERS:
            parsed["destination"] = ""
        kw = parsed.get("keywords", [])
        if not kw or all(k in INTENT_PLACEHOLDERS for k in kw):
            parsed["keywords"] = ["美食", "景点"]
        if isinstance(parsed.get("num_stops"), int) and 0 < parsed["num_stops"] <= 10:
            pass
        else:
            parsed["num_stops"] = 3

        up = parsed.get("user_profile", {})
        profile = UserProfile(
            group_type=up.get("group_type", "solo"),
            age_preference=up.get("age_preference", "all"),
            energy_level=up.get("energy_level", "medium"),
            budget_level=up.get("budget_level", "medium"),
            interests=up.get("interests", []),
            notes=up.get("notes", ""),
        )

        return IntentResult(
            origin=parsed.get("origin", ""),
            destination=parsed.get("destination"),
            date=parsed.get("date"),
            time_budget_hours=parsed.get("time_budget_hours"),
            keywords=parsed["keywords"] if isinstance(parsed["keywords"], list) else [parsed["keywords"]],
            num_stops=parsed["num_stops"],
            user_profile=profile,
            preference_reasoning=parsed.get("preference_reasoning", ""),
            search_hints=parsed.get("search_hints", []),
            raw_input=user_input,
        )
    except Exception:
        return IntentResult(
            origin="", destination=None,
            keywords=["美食", "景点"], num_stops=3,
            user_profile=UserProfile(),
            search_hints=["全城搜索美食和景点"],
            raw_input=user_input,
        )
