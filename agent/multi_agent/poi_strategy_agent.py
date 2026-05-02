"""POI 搜索策略 Agent —— 制定搜索计划，评估 POI 质量."""
import json
import re

from agent.llm_client import call_llm
from agent.multi_agent.types import IntentResult, SearchStrategy, SearchRegion, PoiQualityReport

POI_STRATEGY_SYSTEM = """你是一个 POI 搜索策略专家。你负责制定搜索计划并对搜索结果进行质量评估。

核心能力：
1. **制定搜索策略**：根据用户意图和地理坐标，规划搜索区域、关键词、半径
2. **关键词优化**：将模糊需求转化为具体可搜索词（"想吃点好的"→"黑珍珠/私房菜/牛排"）
3. **质量评估**：判断搜索结果的覆盖率、多样性、匹配度

规则：
- 如果有明确起终点，优先沿路线走廊搜索
- 如果无终点，以起点为中心做环线探索
- 对 family/senior 群体，优先搜安静/有座位的场所
- 对 friends 群体，优先搜网红/高性价比/适合聚餐的场所
- 对 couple 群体，优先搜有氛围/适合拍照/景观好的场所
- 搜索半径：起点周边 500-2000m，终点周边 2000-3000m，沿途 2000m

只输出 JSON，不要 markdown 代码块。"""


def _extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        return json.loads(m.group(0))
    raise ValueError("No JSON found")


def build_search_strategy(intent: IntentResult, origin_coords: tuple = None,
                          dest_coords: tuple = None) -> SearchStrategy:
    """根据用户意图制定 POI 搜索策略."""
    origin_str = f"{origin_coords[1]},{origin_coords[0]}" if origin_coords else intent.origin
    dest_str = f"{dest_coords[1]},{dest_coords[0]}" if dest_coords else (intent.destination or "")

    profile_desc = (
        f"群体：{intent.user_profile.group_type}，"
        f"体力：{intent.user_profile.energy_level}，"
        f"预算：{intent.user_profile.budget_level}，"
        f"兴趣：{', '.join(intent.user_profile.interests) if intent.user_profile.interests else '通用'}，"
        f"备注：{intent.user_profile.notes or '无'}"
    )

    prompt = f"""制定 POI 搜索策略。

城市：用户输入中的城市
起点坐标：{origin_str or '未指定'}
终点坐标：{dest_str or '未指定'}
用户画像：{profile_desc}
搜索关键词：{', '.join(intent.keywords)}
搜索提示：{', '.join(intent.search_hints) if intent.search_hints else '无'}
偏好推理：{intent.preference_reasoning}

输出格式：
{{
    "regions": [
        {{
            "center": "地名或坐标",
            "keywords": ["具体搜索词1", "具体搜索词2"],
            "radius": 2000,
            "reason": "选择理由"
        }}
    ],
    "fallback_keywords": ["美食", "景点"],
    "notes": "补充说明"
}}

规则：
- center 可以是地名（"钟楼"）或坐标（"108.94,34.26"），优先用地名
- keywords 要具体化（如用户说"小吃"，扩展为"肉夹馍/凉皮/泡馍"）
- radius: 起点周边 500-2000m，终点周边 2000-3000m，沿途 3000m
- 至少 1 个区域，最多 3 个区域
- 只输出 JSON"""

    try:
        data = call_llm(
            messages=[{"role": "user", "content": prompt}],
            system=POI_STRATEGY_SYSTEM,
            max_tokens=400,
        )
        parsed = _extract_json(data["content"][0]["text"])
        regions = []
        for r in parsed.get("regions", []):
            regions.append(SearchRegion(
                center=r.get("center", ""),
                keywords=r.get("keywords", intent.keywords),
                radius=r.get("radius", 2000),
                reason=r.get("reason", ""),
            ))
        return SearchStrategy(
            regions=regions if regions else [SearchRegion(
                center=intent.origin or "城市中心",
                keywords=intent.keywords, radius=3000,
                reason="自动生成的兜底搜索区域"
            )],
            fallback_keywords=parsed.get("fallback_keywords", ["美食", "景点"]),
            notes=parsed.get("notes", ""),
        )
    except Exception:
        return SearchStrategy()


def evaluate_pois(pois: list, intent: IntentResult) -> PoiQualityReport:
    """评估 POI 搜索结果质量."""
    if not pois:
        return PoiQualityReport(needs_research=True, research_suggestions="未搜到任何 POI", summary="搜索无结果")

    categories = set()
    has_rating = 0
    has_coords = 0
    for p in pois:
        cat = p.get("category", "")
        if cat:
            categories.add(cat)
        if p.get("rating") is not None:
            has_rating += 1
        if p.get("lat") is not None and p.get("lng") is not None:
            has_coords += 1

    diversity = min(5.0, len(categories) * 1.25)
    coverage = min(5.0, has_coords / max(len(pois), 1) * 5)
    match = 3.0

    if len(pois) > 3:
        try:
            sample = "\n".join(
                f"- {p.get('name','?')} | {p.get('category','?')} | 评分:{p.get('rating','?')}"
                for p in pois[:10]
            )
            prompt = f"""评估以下 POI 列表与用户需求的匹配度（1-5 分，只输出数字）。

用户需求：{intent.raw_input}
用户画像：{intent.user_profile.group_type}，兴趣：{', '.join(intent.user_profile.interests)}

POI 列表：
{sample}

只输出数字（如 3.5）："""
            data = call_llm(
                messages=[{"role": "user", "content": prompt}],
                system="你是一个 POI 质量评估专家。只输出 1-5 的数字评分。",
                max_tokens=10,
            )
            text = data["content"][0]["text"].strip()
            m = re.search(r"(\d+(?:\.\d+)?)", text)
            if m:
                match = float(m.group(1))
        except Exception:
            pass

    needs = has_coords < 3 or match < 2.5
    suggestions = ""
    if has_coords < 3:
        suggestions += "含坐标的 POI 不足 3 个；"
    if diversity < 2:
        suggestions += "品类过于单一；"
    if match < 2.5:
        suggestions += "与用户意图匹配度偏低；"

    return PoiQualityReport(
        coverage_score=round(coverage, 1),
        diversity_score=round(diversity, 1),
        match_score=round(match, 1),
        needs_research=needs,
        research_suggestions=suggestions,
        summary=f"共 {len(pois)} 个 POI，{len(categories)} 个品类，{has_coords} 个含坐标",
    )


def get_research_adjustments(review_feedback: str, intent: IntentResult) -> SearchStrategy:
    """根据 Reviewer 反馈重新制定搜索策略."""
    prompt = f"""根据质量审核反馈，调整 POI 搜索策略。

原用户需求：{intent.raw_input}
审核反馈：{review_feedback}

输出格式同搜索策略（regions、fallback_keywords、notes）。
调整原则：
- 如果反馈说 POI 过于集中，扩大搜索半径或增加区域
- 如果反馈说品类单一，换用更广泛的关键词
- 如果反馈说与用户偏好不匹配，更换关键词方向
只输出 JSON。"""

    try:
        data = call_llm(
            messages=[{"role": "user", "content": prompt}],
            system=POI_STRATEGY_SYSTEM,
            max_tokens=400,
        )
        parsed = _extract_json(data["content"][0]["text"])
        regions = [SearchRegion(**r) for r in parsed.get("regions", [])]
        return SearchStrategy(
            regions=regions,
            fallback_keywords=parsed.get("fallback_keywords", ["美食", "景点"]),
            notes=parsed.get("notes", ""),
        )
    except Exception:
        return SearchStrategy()
