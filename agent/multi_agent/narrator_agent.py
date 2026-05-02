"""路线解说 Agent —— 根据用户画像生成个性化路线叙事."""
from agent.llm_client import call_llm
from agent.multi_agent.types import NarrationContext, UserProfile

NARRATOR_SYSTEM = """你是一个出行路线解说员。你会收到算法计算好的最优路径，用通俗友好的语言向用户解释。

核心原则：
- 忠实于算法结果，不修改站点顺序或交通工具
- 根据用户群体调整语气和内容侧重
- 让用户感受到"有人为 ta 用心规划"，而不是冷冰冰的数据

群体语气指南：
- family（家庭/老人/小孩）: 温馨、周到、强调"安全/舒适/有座位/无障碍/厕所位置"
- couple（情侣）: 浪漫、有氛围、"适合拍照/看日落/小酌/甜品"
- friends（朋友）: 活泼、实用、"性价比高/网红打卡/分量大/适合聚餐"
- solo（独自）: 自由、深度、"沉浸体验/隐藏宝藏/一人食友好"

结构要求：
1. 路线概览（一句话总结）
2. 各段解说（每段：交通工具理由 + 站点推荐菜/看点 + 小贴士）
3. 实用建议（最佳出发时间、注意事项、替代方案）
4. 结尾（一句温馨祝福或期待）"""


def _tone_instruction(profile: UserProfile) -> str:
    """根据用户画像生成语气指令."""
    lines = []
    gt = profile.group_type

    if gt == 'family':
        lines.append('- 语气温馨体贴，多用「您」和「妈妈」「孩子」「家人」')
        lines.append('- 重点标注：有无座位、台阶情况、卫生间位置、安静程度')
        lines.append('- 建议步速放慢，多安排休息点')
    elif gt == 'couple':
        lines.append('- 语气浪漫轻松，提到「拍照好看」「适合小酌」「看日落」')
        lines.append('- 重点标注：氛围感、景观位、适合拍照的时间段')
    elif gt == 'friends':
        lines.append('- 语气活泼亲切，用「小伙伴们」「一起」「嗨」等词')
        lines.append('- 重点标注：性价比、分量大小、网红程度、适不适合合影')
    elif gt == 'solo':
        lines.append('- 语气自由随性，用「你可以慢悠悠」「一个人也能」等表达')
        lines.append('- 重点标注：一人食友好、安静角落、沉浸体验')

    if profile.energy_level == "low":
        lines.append("- 严格控制步行量，每段步行不能超过 15 分钟，多建议休息点")
    if profile.notes and "安静" in profile.notes:
        lines.append("- 优先提及安静、人少、非高峰时段的信息")
    if profile.notes and ("无障碍" in profile.notes or "轮椅" in profile.notes):
        lines.append("- 标注无障碍通道和电梯情况")

    return "\n".join(lines)


def run_narrator(context: NarrationContext) -> str:
    """根据用户画像生成个性化路线解说."""
    if not context.path_segments:
        return "路线暂时无法生成，请提供更多信息。"

    path_lines = []
    for i, s in enumerate(context.path_segments):
        mins = round(s["duration"] / 60)
        d = s["distance"]
        d_str = f"{d}m" if d < 1000 else f"{d/1000:.1f}km"
        path_lines.append(f"{i+1}. {s['from']} → {s['to']}（{s['transport']} {d_str} 约{mins}分钟）")
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
            system=NARRATOR_SYSTEM,
            max_tokens=1500,
        )
        return data["content"][0]["text"]
    except Exception:
        return (
            f"为你规划了一条{len(context.path_segments)}站路线"
            f"（约{context.total_duration_min}分钟 / {context.total_distance_m}米）。\n\n"
            + path_desc +
            "\n\n（AI 解说暂时不可用，以上为路线核心数据）"
        )
