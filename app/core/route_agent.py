"""统一路线 Agent — LLM 工具调用替代多 Agent 串行.

罗斯方案 Step 2: 单次 LLM 调用 + 工具循环，合并 intent → poi_strategy → narrator → reviewer.
"""

import re

from app.llm_client import call_llm_with_tools, tool_result_message, extract_text, extract_tool_uses
from app.pipeline.cluster_tools import TOOL_DEFINITIONS, execute_tool
from app.shared.utils import _extract_city, _progress, _build_route_html, AgentSession
from app.user_profile import UserProfileManager

_MAX_TOOL_ITERATIONS = 12

_SYSTEM_PROMPT = """你是一个本地路线规划助手「出发酱」。你的任务是用工具逐步规划一条从起点到终点的本地游玩路线，最后给用户一个自然友好的解说。

## 工作流程

每一步按顺序执行，不要跳过：

1. **理解需求** — 从用户输入中提取：起点、终点、偏好关键词、预算（低/中/高）、期望停靠站数、可用时间
2. **geocode** — 将起点和终点地名解析为经纬度。如果用户没有指定终点，可以只 geocode 起点
3. **query_clusters** — 用起终点坐标查询沿途的 POI 聚簇。根据用户的偏好关键词和预算来过滤。如果用户没有明确说偏好，用 keywords=["美食", "景点"]
4. **挑选聚簇** — 从返回的簇中选 3-5 个最合适的。考虑：
   - 空间分布：沿途均匀分布，不要全挤在一起
   - 品类匹配：簇的 top_cats 是否匹配用户需求
   - 评分和价格：是否符合用户的预算和品质期望
   - 多样性：避免选 3 个品类完全相同的簇（如全是火锅）
   - num_stops 应等于选中的 cluster_ids 数量（每个簇最多产出一个站）
5. **build_route** — 用选定的 cluster_ids 构建实际路线。**此步骤不可跳过**，必须拿到真实路线数据后才能开始解说。build_route 的参数 cluster_ids 必须是从 query_clusters 结果中实际出现的 cluster_id
6. **解说** — 根据 build_route 返回的实际路线数据生成解说文字，包括每站的交通方式、耗时、距离

## 解说要求

解说要包含：
- **概述**：一句话总结路线特色
- **分站介绍**：每站写上名称、推荐理由、交通方式和耗时
- **小贴士**：给 1-2 条实用建议（最佳出发时间、注意事项等）
- **Mermaid 路线图**：用 ```mermaid 代码块输出 flowchart，格式如下：
  ```
  flowchart LR
      classDef start fill:#10b981,color:#fff
      classDef mid fill:#3b82f6,color:#fff
      classDef end fill:#f59e0b,color:#fff
      N0(["起点名"]):::start
      N0 -->|"🚶 15分"| N1["🍜 站1名"]:::mid
      N1 -->|"🚲 8分"| N2["☕ 站2名"]:::mid
      N2 -->|"🚶 10分"| N3["🏁 终点名"]:::end
  ```
  交通 emoji：🚶步行 🚲骑行 🚌公交/地铁 🚕打车
  站点 emoji 根据品类选择：🍲火锅 🍖烧烤 ☕咖啡 🍰甜品 🍜面馆 🍣日料 🍕西餐 🦐海鲜 🧋茶饮 🍸酒吧 🎡景点 🛍️购物 📖图书馆 🎬影院 🥣清淡

## 多轮对话与冲突解决

如果对话历史中有之前的路线信息，用户的新输入可能是在修改：
- 终点改变（"去大雁塔"）→ 以最新为准，重新 geocode
- 约束改变（"1小时"）→ 以最新为准，调整站点数
- 偏好升级（"要高档"）→ 合并为新偏好
- 彻底推翻（"不要这个方案"）→ 完全重新规划
- 自相矛盾（"便宜"+"米其林"）→ 友善指出，反问用户澄清

## 风格指南

- 语气轻松友好但不油腻，像旅行达人在给朋友建议
- 不说「根据算法」或「系统显示」之类的词
- 路线不可行时（工具返回 error），如实告知并给出替代建议
- 用中文回复

## 重要规则

- **必须调用 build_route**：选定聚簇后，必须调用 build_route 获取真实路线数据，不能凭空编造
- **query_clusters 最多调 2 次**：如果第一次结果不理想，可以换关键词再试一次。如果 2 次都不够好，就在现有结果中选最好的，不要反复重试
- **build_route 只调 1 次**：如果 build_route 返回 error，不要反复重试。直接告诉用户：「抱歉，目前在这些区域找不到完全匹配的 POI。要不要试试放宽关键词或预算？」
- **工具调用总数控制在 5 次以内**：geocode(1-2次) + query_clusters(1-2次) + build_route(1次) = 总调用 ≤ 5 次
- **使用真实数据解说**：解说中的交通方式、耗时必须来自 build_route 返回值
- **Mermaid 图也用真实数据**：图中的交通 emoji 和时间必须和 build_route 返回值一致"""


def run_unified_agent(user_input: str, session: AgentSession = None,
                      user_id: str = "default") -> tuple:
    """统一 Agent 入口 — 工具调用循环.

    Args:
        user_input: 用户当前输入
        session: AgentSession（支持多轮）
        user_id: 用户 ID

    Returns:
        (narration_text, AgentSession)
    """
    if session is None:
        session = AgentSession()

    profile_mgr = UserProfileManager(user_id=user_id)
    user_data = profile_mgr.load()

    # 精简 subcategory 显示（只保留最后一段）
    top_cats_short = []
    for t in TOOL_DEFINITIONS:
        tc = dict(t)
        if tc["name"] == "query_clusters":
            tc["description"] += " 注意：top_cats 是子品类简称（如「火锅店」「咖啡厅」），不是完整的层级路径。"
        top_cats_short.append(tc)

    # 构建消息
    messages = _build_messages(user_input, session, user_data)

    # Agent 状态（工具之间共享）
    agent_state = {
        "origin_coords": session.origin_coords,
        "dest_coords": session.dest_coords,
        "dest_name": session.dest_name or "",
    }

    _progress("🤖", "统一 Agent 启动（工具调用模式）")

    narration = ""
    for iteration in range(_MAX_TOOL_ITERATIONS):
        try:
            response = call_llm_with_tools(
                messages, top_cats_short,
                system=_SYSTEM_PROMPT,
                max_tokens=3000,
            )
        except Exception as e:
            _progress("⚠️", f"LLM 调用失败: {e}")
            return f"抱歉，AI 服务暂时不可用：{e}", session

        content = response.get("content", [])
        text = extract_text(content)
        tool_uses = extract_tool_uses(content)

        # 无工具调用 → LLM 输出最终解说
        if not tool_uses:
            narration = text
            _progress("✅", "路线规划完成")
            break

        # 有工具调用 → 执行并继续
        for tu in tool_uses:
            name = tu.get("name", "")
            tool_id = tu.get("id", "")
            inp = tu.get("input", {})
            _progress("🔧", f"调用工具：{name}({_brief_input(name, inp)})")

            result_json = execute_tool(name, inp, agent_state)
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tool_id, "content": result_json}
            ]})
    else:
        _progress("⚠️", f"工具调用超过 {_MAX_TOOL_ITERATIONS} 轮，强制终止")
        if not narration:
            narration = "抱歉，路线规划超时了。请尝试简化需求（如指定更明确的地点或偏好）。"

    # 从 narration 中提取 mermaid 代码
    mermaid = _extract_mermaid(narration)

    # 填充 session 状态
    _finalize_session(session, agent_state, user_input, narration)

    # 生成输出文件（与现有流程兼容）
    if session.path_result and mermaid:
        _write_output_files(session, narration, mermaid, user_input)

    # 保存用户 profile
    try:
        profile_mgr.save_session(session)
    except Exception:
        pass

    return narration, session


def _build_messages(user_input: str, session: AgentSession, user_data: dict) -> list:
    """构建初始消息列表."""
    messages = []

    # 注入多轮上下文
    if session.last_user_input and session.city:
        ctx = _build_context(session, user_data)
        messages.append({
            "role": "user",
            "content": f"【之前规划的路线参考】\n{ctx}\n\n【用户新输入】{user_input}\n\n请根据新输入重新规划。如果有冲突，以新输入为准。",
        })
    else:
        messages.append({"role": "user", "content": user_input})

    return messages


def _build_context(session: AgentSession, user_data: dict) -> str:
    """从 session 构建上下文摘要."""
    parts = [f"城市：{session.city}"]
    if session.start_name:
        parts.append(f"上次起点：{session.start_name}")
    if session.dest_name:
        parts.append(f"上次终点：{session.dest_name}")
    if session.stop_names:
        parts.append(f"上次途经：{' → '.join(session.stop_names)}")
    if session.keywords:
        parts.append(f"上次偏好：{session.keywords}")
    if session.num_stops:
        parts.append(f"上次站数：{session.num_stops}")
    if session.distance_info:
        parts.append(f"上次耗时：{session.distance_info}")

    profile = user_data.get("profile", {})
    if profile:
        notes = profile.get("notes", "")
        interests = profile.get("interests", [])
        if interests:
            parts.append(f"用户兴趣：{', '.join(interests)}")
        if notes:
            parts.append(f"用户备注：{notes}")

    return "\n".join(parts)


def _brief_input(tool_name: str, inp: dict) -> str:
    """生成工具调用的简要日志."""
    if tool_name == "geocode":
        return f"{inp.get('place', '')}"
    if tool_name == "query_clusters":
        kws = inp.get("keywords", [])
        budget = inp.get("budget", "")
        return f"keywords={kws}, budget={budget or 'any'}"
    if tool_name == "build_route":
        return f"clusters={inp.get('cluster_ids', [])}, stops={inp.get('num_stops', 3)}"
    return ""


def _extract_mermaid(text: str) -> str:
    """从解说文本中提取 mermaid 代码块."""
    m = re.search(r"```mermaid\s*\n(.*?)\n```", text, re.DOTALL)
    return m.group(1).strip() if m else ""


def _finalize_session(session: AgentSession, agent_state: dict,
                      user_input: str, narration: str):
    """将 agent_state 中的信息同步回 AgentSession."""
    session.last_user_input = user_input

    # 从 geocode 结果中提取地点名和坐标
    oc = agent_state.get("origin_coords")
    if oc:
        session.origin_coords = oc
    dc = agent_state.get("dest_coords")
    if dc:
        session.dest_coords = dc

    if agent_state.get("start_name"):
        session.start_name = agent_state["start_name"]
    if agent_state.get("dest_name"):
        session.dest_name = agent_state["dest_name"]

    # 提取城市（优先 agent_state，其次 user_input）
    city = agent_state.get("city") or _extract_city(user_input, getattr(session, 'default_city', ''))
    if city:
        session.city = city

    # 设置 all_pois（优先从 agent_state）
    if agent_state.get("all_pois"):
        session.all_pois = agent_state["all_pois"]

    # 从 build_route 结果中提取路线数据
    path_result = agent_state.get("path_result")
    if path_result and isinstance(path_result, dict) and path_result.get("stops"):
        stops = path_result["stops"]
        session.stop_names = [s["name"] for s in stops]
        session.num_stops = len(stops)

        segments = []
        for i, s in enumerate(stops):
            prev_name = stops[i - 1]["name"] if i > 0 else (session.start_name or "起点")
            segments.append({
                "from": prev_name,
                "to": s["name"],
                "transport": s.get("transport_from_prev", "步行"),
                "distance": s.get("distance_m", 0),
                "duration": s.get("duration_min", 0) * 60,
            })

        # 添加终点段（如果 dest_name 存在且不等于最后一站）
        dest_name = agent_state.get("dest_name") or ""
        last_name = stops[-1]["name"] if stops else ""
        if dest_name and dest_name != last_name:
            segments.append({
                "from": last_name,
                "to": dest_name,
                "transport": "步行",
                "distance": 500,
                "duration": 600,
            })

        total_dur = sum(s["duration"] for s in segments)
        total_dist = sum(s["distance"] for s in segments)

        session.path_result = {
            "segments": segments,
            "total_duration_min": round(total_dur / 60),
            "total_distance": total_dist,
        }
        session.distance_info = f"约{round(total_dur / 60)}分钟 / {total_dist}米"

        if not session.all_pois:
            session.all_pois = [
                {
                    "name": s["name"],
                    "category": s.get("category", ""),
                    "rating": s.get("rating"),
                    "price_per_person": s.get("price_per_person"),
                    "address": s.get("address", ""),
                }
                for s in stops
            ]

    # 评分
    total_stops = len(session.stop_names) if session.stop_names else 0
    if total_stops >= 2:
        session.review_score = 4.0
    elif total_stops >= 1:
        session.review_score = 3.0


def _write_output_files(session: AgentSession, narration: str,
                        mermaid: str, user_input: str):
    """生成 Mermaid 和 HTML 输出文件（与现有流程兼容）."""
    from pathlib import Path
    from app.shared.utils import _build_route_html

    output_dir = Path(__file__).parent.parent.parent / "data" / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Mermaid 文件
    md_path = output_dir / "route_output.md"
    md_content = f"# 路线规划\n\n{narration}\n\n```mermaid\n{mermaid}\n```\n"
    md_path.write_text(md_content, encoding="utf-8")

    # HTML 文件
    try:
        html = _build_route_html(
            stop_names=session.stop_names or [],
            pois=session.all_pois or [],
            distance_info=session.distance_info or "",
            city=session.city or "",
            user_input=user_input,
            start_name=session.start_name or "起点",
            start_coords=session.origin_coords,
            dest_name=session.dest_name or "",
            dest_coords=session.dest_coords,
        )
        if html:
            html_path = output_dir / "route_output.html"
            html_path.write_text(html, encoding="utf-8")
    except Exception:
        pass
