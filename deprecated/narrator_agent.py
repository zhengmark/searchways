"""路线解说 Agent — Phase 2 用户确认后生成详细解说."""
from app.llm_client import call_llm
from app.core.types import NarrationContext, UserProfile
from app.shared.utils import AgentSession, _build_mermaid_from_path

_NARRATOR_SYSTEM = """你是一个出行路线解说员「出发酱」。用户已经在地图上手动确认了路线，你的任务是为这条确认后的路线生成详细友好的解说。

核心原则：
- 忠实于用户确认的路线，不修改站点顺序或交通方式
- 根据用户群体调整语气和内容侧重
- 让用户感受到"有人为 ta 用心规划"，而不是冷冰冰的数据

群体语气指南：
- family（家庭/老人/小孩）: 温馨、周到、强调"安全/舒适/有座位/无障碍/厕所位置"
- couple（情侣）: 浪漫、有氛围、"适合拍照/看日落/小酌/甜品"
- friends（朋友）: 活泼、实用、"性价比高/网红打卡/分量大/适合聚餐"
- solo（独自）: 自由、深度、"沉浸体验/隐藏宝藏/一人食友好"

结构要求：
1. 路线概览（一句话总结 + 总耗时/总距离）
2. 各段解说（每段：从哪出发 → 到哪、交通方式+耗时、推荐理由、小贴士）
3. 实用建议（最佳出发时间、注意事项）
4. 结尾（一句温馨祝福或期待）"""


def run_confirmation_narrator(session: AgentSession, user_input: str = "",
                               user_profile: dict = None) -> dict:
    """用户确认路线后生成详细解说 + Mermaid 图.

    Args:
        session: 已确认的 AgentSession（含 stops + path_result）
        user_input: 用户原始需求
        user_profile: 用户画像（interests, notes 等）

    Returns:
        {"narration": str, "mermaid": str}
    """
    stops = session.all_pois or []
    path = session.path_result or {}

    if not path.get("segments"):
        return _fallback_narration(session)

    # 构建路线描述
    segments_text = _build_segments_text(path["segments"])
    stops_text = _build_stops_text(stops)

    # 用户偏好
    interests = ""
    if user_profile:
        interests = ", ".join(user_profile.get("interests", []))
    notes = user_profile.get("notes", "") if user_profile else ""
    group_type = user_profile.get("group_type", "") if user_profile else ""

    prompt = f"""为用户确认的路线生成详细解说。

【起点】{session.start_name or '起点'}
【终点】{session.dest_name or '由路线自然结束'}
【城市】{session.city}
【用户需求】{user_input or '出行规划'}
【偏好】{interests or '无特殊偏好'}
【备注】{notes or '无'}
【群体】{group_type or 'solo'}

【路线站点】
{stops_text}

【各段交通】
{segments_text}

总耗时：{path.get('total_duration_min', 0)} 分钟
总距离：{path.get('total_distance', 0)} 米

请生成详细路线解说（Markdown 格式）。"""

    try:
        data = call_llm(
            messages=[{"role": "user", "content": prompt}],
            system=_NARRATOR_SYSTEM,
            max_tokens=2000,
        )
        narration = data["content"][0]["text"]

        # 服务端生成 Mermaid（不再从 LLM 提取）
        mermaid = _build_mermaid_from_path(
            session.start_name or "起点",
            path,
            session.stop_names,
        )

        return {"narration": narration, "mermaid": mermaid}

    except Exception:
        return _fallback_narration(session)


def _build_stops_text(stops: list) -> str:
    """构建站点列表文本."""
    lines = []
    for i, s in enumerate(stops):
        name = s.get("name", f"站{i+1}")
        cat = s.get("category", "")
        rating = s.get("rating", "")
        price = s.get("price_per_person", "")
        parts = [name]
        if cat:
            parts.append(f"[{cat}]")
        if rating:
            parts.append(f"★{rating}")
        if price:
            parts.append(f"¥{int(price)}")
        lines.append(f"{i+1}. {' '.join(parts)}")
    return "\n".join(lines)


def _build_segments_text(segments: list) -> str:
    """构建路段描述文本."""
    lines = []
    for s in segments:
        transport = s.get("transport", "步行")
        duration = s.get("duration", 0)
        distance = s.get("distance", 0)
        mins = round(duration / 60) if duration > 60 else max(1, round(duration / 60))
        dist_str = f"{distance}m" if distance < 1000 else f"{distance/1000:.1f}km"
        lines.append(f"{s['from']} → {s['to']}（{transport} {dist_str} 约{mins}分钟）")
    return "\n".join(lines)


def _fallback_narration(session: AgentSession) -> dict:
    """LLM 不可用时的兜底解说."""
    path = session.path_result or {}
    total_dur = path.get("total_duration_min", 0)
    total_dist = path.get("total_distance", 0)

    stops_text = _build_stops_text(session.all_pois or [])
    segments_text = _build_segments_text(path.get("segments", []))

    narration = f"""## 路线概览

为你规划了一条 {len(session.stop_names)} 站路线，总耗时约 {total_dur} 分钟，总距离约 {total_dist} 米。

## 站点列表

{stops_text}

## 各段交通

{segments_text}

## 小贴士

- 建议根据天气情况调整出行时间
- 高峰期建议提前出发预留额外时间"""

    mermaid = _build_mermaid_from_path(
        session.start_name or "起点",
        path,
        session.stop_names,
    )

    return {"narration": narration, "mermaid": mermaid}


# ── 旧接口（向后兼容）─────────────────────────────────

def run_narrator(context: NarrationContext) -> str:
    """旧接口：根据 NarrationContext 生成个性化路线解说."""
    if not context.path_segments:
        return "路线暂时无法生成，请提供更多信息。"

    path_lines = []
    for i, s in enumerate(context.path_segments):
        mins = round(s["duration"] / 60)
        d = s.get("distance", 0)
        d_str = f"{d}m" if d < 1000 else f"{d/1000:.1f}km"
        path_lines.append(
            f"{i+1}. {s['from']} → {s['to']}（{s['transport']} {d_str} 约{mins}分钟）"
        )
    path_desc = "\n".join(path_lines)

    tone = _tone_instruction(context.user_profile)

    prompt = f"""为用户解说以下路线。

【起点】{context.start_name}
【终点】{context.dest_name or '由路线自然结束'}
【城市】{context.city}
【用户原始需求】{context.user_input}
【用户群体】{context.user_profile.group_type}，{context.user_profile.notes or '无特殊需求'}

【算法最优路径】
{path_desc}
总耗时：{context.total_duration_min} 分钟
总距离：{context.total_distance_m} 米

语气要求：
{tone}

请生成个性化路线解说。"""

    try:
        data = call_llm(
            messages=[{"role": "user", "content": prompt}],
            system=_NARRATOR_SYSTEM,
            max_tokens=1500,
        )
        return data["content"][0]["text"]
    except Exception:
        return (
            f"为你规划了一条{len(context.path_segments)}站路线"
            f"（约{context.total_duration_min}分钟 / {context.total_distance_m}米）。\n\n"
            + path_desc
            + "\n\n（AI 解说暂时不可用，以上为路线核心数据）"
        )


def _tone_instruction(profile: UserProfile) -> str:
    """根据用户画像生成语气指令."""
    lines = []
    gt = profile.group_type

    if gt == "family":
        lines.append("- 语气温馨体贴，多用「您」和「妈妈」「孩子」「家人」")
        lines.append("- 重点标注：有无座位、台阶情况、卫生间位置、安静程度")
        lines.append("- 建议步速放慢，多安排休息点")
    elif gt == "couple":
        lines.append("- 语气浪漫轻松，提到「拍照好看」「适合小酌」「看日落」")
        lines.append("- 重点标注：氛围感、景观位、适合拍照的时间段")
    elif gt == "friends":
        lines.append("- 语气活泼亲切，用「小伙伴们」「一起」「嗨」等词")
        lines.append("- 重点标注：性价比、分量大小、网红程度、适不适合合影")
    elif gt == "solo":
        lines.append("- 语气自由随性，用「你可以慢悠悠」「一个人也能」等表达")
        lines.append("- 重点标注：一人食友好、安静角落、沉浸体验")

    if profile.energy_level == "low":
        lines.append("- 严格控制步行量，每段步行不能超过 15 分钟，多建议休息点")
    if profile.notes and "安静" in profile.notes:
        lines.append("- 优先提及安静、人少、非高峰时段的信息")
    if profile.notes and ("无障碍" in profile.notes or "轮椅" in profile.notes):
        lines.append("- 标注无障碍通道和电梯情况")

    return "\n".join(lines)
