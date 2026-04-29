"""质量审核 Agent —— 审核路线合理性，发现问题并给出修正建议"""
import json
import re

from agent.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from agent.multi_agent.types import ReviewResult, ReviewIssue, UserProfile

API_URL = f"{LLM_BASE_URL.rstrip('/')}/v1/messages"

REVIEWER_SYSTEM = """你是一个路线质量审核员。你的任务是快速审核路线方案的合理性。

检查维度：
1. **距离合理性**：单段步行距离 > 3km 但标记为"步行"？→ warn
2. **POI 多样性**：3 个站全是同品类（如全是火锅）？→ suggest 替换
3. **总耗时**：超过用户时间预算？→ warn
4. **用户适配**：老年/低体力群体但有长距离步行段？→ suggest 调整
5. **空间分布**：POI 扎堆在 500m 内？→ suggest 扩大搜索

评分标准（1-5）：
- 5: 完美适配用户需求
- 4: 小问题但不影响体验
- 3: 有几个可改进点
- 2: 有明显缺陷需要返工
- 1: 完全不合理

只输出 JSON，不要 markdown 代码块。"""


def _call_api(messages: list, system: str = None, max_tokens: int = 4096) -> dict:
    import requests
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LLM_API_KEY}",
        "anthropic-version": "2023-06-01",
    }
    payload = {"model": LLM_MODEL, "max_tokens": max_tokens, "messages": messages}
    if system:
        payload["system"] = system
    resp = requests.post(API_URL, headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json()


def _extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        return json.loads(m.group(0))
    raise ValueError("No JSON found")


def run_reviewer(
    start_name: str, dest_name: str, city: str, user_input: str,
    path_segments: list, total_duration_min: int, total_distance_m: int,
    user_profile: UserProfile, time_budget_hours: float = None,
) -> ReviewResult:
    """审核路线质量，返回评分及修正建议"""

    # 构建路线描述
    lines = []
    for i, s in enumerate(path_segments):
        mins = round(s["duration"] / 60)
        d = s["distance"]
        d_str = f"{d}m" if d < 1000 else f"{d/1000:.1f}km"
        lines.append(f"{i+1}. {s['from']} → {s['to']} | {s['transport']} | {d_str} | {mins}分钟")
    route_desc = "\n".join(lines)

    time_info = f"用户时间预算：{time_budget_hours}小时" if time_budget_hours else "无明确时间限制"

    prompt = f"""审核以下路线方案。

【城市】{city}
【用户需求】{user_input}
【用户画像】群体：{user_profile.group_type}，体力：{user_profile.energy_level}，备注：{user_profile.notes or '无'}
【{time_info}】

【路线】
起点：{start_name}
终点：{dest_name or '由路线自然结束'}
各段：
{route_desc}
总耗时：{total_duration_min}分钟
总距离：{total_distance_m}米

输出格式：
{{
    "overall_score": 4.0,
    "issues": [
        {{
            "severity": "low/medium/high",
            "category": "distance/diversity/time_budget/user_fit/spacing",
            "description": "问题描述",
            "suggestion": "修改建议"
        }}
    ],
    "needs_retry": false,
    "retry_suggestions": "如果 needs_retry=true，给出具体的重新搜索建议",
    "summary": "一句话审核总结"
}}

规则：
- needs_retry=true 仅当 overall_score < 3 或存在 high severity 问题时
- retry_suggestions 要具体到"搜索 XX 区域 XX 关键词"的程度
- 只输出 JSON"""

    try:
        data = _call_api(
            messages=[{"role": "user", "content": prompt}],
            system=REVIEWER_SYSTEM,
            max_tokens=500,
        )
        parsed = _extract_json(data["content"][0]["text"])

        issues = []
        for iss in parsed.get("issues", []):
            issues.append(ReviewIssue(
                severity=iss.get("severity", "low"),
                category=iss.get("category", ""),
                description=iss.get("description", ""),
                suggestion=iss.get("suggestion", ""),
            ))

        return ReviewResult(
            overall_score=parsed.get("overall_score", 3.0),
            issues=issues,
            needs_retry=parsed.get("needs_retry", False),
            retry_suggestions=parsed.get("retry_suggestions", ""),
            summary=parsed.get("summary", ""),
        )
    except Exception:
        return ReviewResult(summary="审核服务暂不可用")
