"""统一路线 Agent — LLM 工具调用替代多 Agent 串行.

罗斯方案 Step 2: 单次 LLM 调用 + 工具循环，合并 intent → poi_strategy → narrator → reviewer.
"""

import json
import re
import time

from app.llm_client import call_llm_with_tools, tool_result_message, extract_text, extract_tool_uses
from app.pipeline.cluster_tools import TOOL_DEFINITIONS, execute_tool
from app.pipeline.constraint_checker import check_constraints, extract_constraints
from app.shared.utils import _extract_city, _progress, _build_route_html, AgentSession
from app.user_profile import UserProfileManager

_MAX_TOOL_ITERATIONS = 12

_SYSTEM_PROMPT = """你是一个本地路线规划助手「出发酱」。你的任务是用工具逐步规划一条从起点到终点的本地游玩路线，最后给用户一个简洁的路线预览。

## 工作流程

每一步按顺序执行，不要跳过：

1. **理解需求** — 从用户输入中提取：起点、终点、偏好关键词、预算（低/中/高）、期望停靠站数、可用时间
2. **geocode** — 将起点和终点地名解析为经纬度。如果用户没有指定终点，可以只 geocode 起点
3. **query_clusters** — 用起终点坐标查询沿途的 POI 聚簇。根据用户的偏好关键词和预算来过滤。如果用户没有明确说偏好，用 keywords=["美食", "景点"]。**如果用户是无目的探索（不知道去哪玩/随便逛逛），用 keywords=["著名景点","必去","热门","美食","景点"]，并优先选择 cluster_id=-2（城市热门景点推荐）和 source="amap" 的簇**。**重要**：如果用户提到了特定的地名（如"回民街""大唐不夜城""大雁塔"），在 query_clusters 的 keywords 中要加入该地名的典型关键词。例如回民街 → keywords=["小吃","夜市","美食","清真"]，大唐不夜城 → keywords=["夜景","步行街","拍照","美食"]
4. **挑选聚簇** — 从返回的簇中选 5-8 个最合适的（为后续交互式编辑提供更多候选）。考虑：
   - **热门景点优先**：如果结果中有 cluster_id=-2（source="famous"）或 source="amap" 的簇，优先选择，这些是系统根据城市热门景点/高德数据筛选的高质量推荐
   - **空间均匀分布（最重要）**：每个簇有 projection 字段（0=起点, 1=终点）。必须确保全程均匀覆盖：projection 0.0-0.2（起点附近）至少1个 → 0.2-0.4 至少1个 → 0.4-0.6 至少1个 → 0.6-0.8 至少1个 → 0.8-1.0（终点附近）至少1个。**禁止**所有簇的 projection 都集中在某个区间（如全在 0.7-1.0）
   - 品类匹配：簇的 top_cats 是否匹配用户需求。如果用户找"美食"，不要选 top_cats 中有"购物""休闲娱乐"的簇。优先选 top_cats 中包含目标品类的簇
   - **检查 top_poi_names**：必须检查每个簇的 top_poi_names，确保这些 POI 名称和品类确实匹配用户需求。如果 top_poi_names 中的名字看起来不像目标品类（如找"美食"但 top_poi_names 是"XXKTV""XX艺术空间"），不要选这个簇
   - **用户指定地点优先**：如果用户明确提到了某个地名（如"回民街""大唐不夜城"），优先选该地名附近的簇（簇的 name 或 top_poi_names 中包含该地名或其特征的）
   - 评分和价格：是否符合用户的预算和品质期望
   - 多样性：避免选品类完全相同的簇（如全是火锅）
   - num_stops 应等于选中的 cluster_ids 数量（每个簇最多产出一个站）
5. **build_route** — 用选定的 cluster_ids 构建实际路线。**此步骤不可跳过**，必须拿到真实路线数据后才能开始解说。build_route 的参数 cluster_ids 必须是从 query_clusters 结果中实际出现的 cluster_id。**不要对同一组 cluster_ids 重复调用 build_route（只是换排列顺序不算新参数），这样只会浪费时间和 API 配额**
6. **路线预览** — build_route 成功返回后，写 2-3 句简洁的路线预览（概述 + 途经站点名 + 总时长），不要写长篇解说。**不要写 Mermaid 图。** 详细解说和可视化将在用户确认路线后由系统自动生成

## 强制工具调用规则（最重要）

**任何出行需求都必须调用工具规划路线。** 即使信息不完整（无起点、无偏好、需求模糊），也必须使用默认值调用工具。**禁止**只输出文字建议而不调工具。

唯一可以只输出文字的情况：
- 用户不是要规划路线（如纯闲聊、询问系统功能）
- 用户连城市名都没提供（可以反问一次）

## 极简输入处理规则

如果用户输入极其简短（如"西安 吃"、"北京 玩"），**不要反问用户**。直接使用默认值开始规划：
- 无起点 → geocode 城市名本身作为起点（即城市中心）
- 无终点 → 只能设 origin，不设 dest（做起点周边的环线探索）
- 无关键词 → 用 ["美食", "景点"] 作为默认
- 无预算 → 用 "medium"
**唯一例外**：如果用户连城市名都没提供，可以反问一次

## 模糊探索请求处理（重要）

如果用户表达了无明确偏好的探索意图（如"不知道去哪玩"、"随便逛逛"、"推荐一下"、"有什么好玩的"、"想去转转"、"不想去太远的"），你应该：

1. 先用城市名 geocode 获取城市中心坐标
2. 使用 keywords=["著名景点","必去","热门","美食","景点"] 调用 query_clusters
3. 系统会自动注入该城市的热门景点推荐（cluster_id=-2, source="famous"）
4. **优先选择 cluster_id=-2（城市热门景点推荐）和 source="amap" 的簇**
5. 如果结果中出现了该城市的标志性景点，务必选入路线
6. 在路线预览中用推荐口吻介绍（如"西安必去的几个地方..."，列出标志性景点名）

## 路线预览要求

build_route 成功后的预览只需包含：
- **一句话概述**：路线特色
- **站点列表**：列出各站名称 + 简要说明（每站 1 句）
- **基本信息**：总耗时、总距离
- **不要写 Mermaid 图**，也不要说"如果你想调整..."之类的引导语（系统会自动处理）

## 解说数据一致性（极其重要）

预览中提到的每个 POI 名称，**必须是 build_route 返回结果中 stops[].name 的精确值**。禁止：
- 编造 build_route 返回结果中不存在的 POI 名称
- 用训练数据中的"常识"替换实际路线（如实际路线是 KTV，不能说成"老字号餐厅"）

如果你发现 build_route 返回的 POI 与用户需求严重不匹配，**如实告诉用户**："抱歉！目前在这些区域找不到完全匹配的 POI。要不要试试放宽关键词或预算？" —— 而不要编造假数据。

## 关键词匹配强制规则

1. **最多 2 次 query_clusters**：如果已有 2 次 query_clusters 但 keyword_match 都 < 0.5，**不要再查第三次**。直接用现有结果中最好的簇调用 build_route。第三次留给万能的 美食,景点 兜底搜索
2. **必须调用 build_route**：只要 query_clusters 返回了簇（哪怕只有 1 个且 keyword_match=0），必须选最好的调用 build_route。**禁止无限重试 query_clusters**。路线预览中如实告诉用户：数据有限，以下是目前找到的最佳选择
3. **stop 名称合规检查**：build_route 成功后，检查 stop 名称是否明显违反约束。如用户要素食但 stop 含火锅→ 在预览中如实说明
4. **关键词重试策略**：第 1 次用用户原词 → 第 2 次扩展近义词（素食→健康餐/轻食，健身房→运动/体育，包间→中餐/火锅/海鲜） → 第 3 次用 [美食, 景点] 兜底
5. **高德补搜优先**：如果 query_clusters 返回中包含 cluster_id=-1 且 source="amap" 的条目，**优先选它**。系统用高德 API 直接搜索的 POI 质量通常比本地 DB 好。特别是用户要素食/轻食/健身房/包间/SPA 等本地 DB 覆盖差的品类时，cluster_id=-1 往往是最准确的
6. **预算约束优先**：build_route 的 budget 参数必须与用户需求一致。穷游→low，高档→high
## 多轮对话与冲突解决

如果对话历史中有之前的路线信息，用户的新输入可能是在修改：
- 终点改变（"去大雁塔"）→ 已 geocode 过的坐标可以复用，不需要重复 geocode
- 约束改变（"1小时"）→ 以最新为准，调整站点数
- 偏好升级（"要高档"）→ 合并为新偏好
- 彻底推翻（"不要这个方案"）→ 完全重新规划
- 自相矛盾（"便宜"+"米其林"）→ 友善指出，反问用户澄清

**约束保留规则（重要）**：如果用户只修改了部分约束（如只改时间、不改偏好），未被明确推翻的约束应该保留。例如：
- 上一轮 keywords=["风景","骑行"]，本轮用户说"缩短到2小时"→ 仍用 keywords=["风景","骑行"]
- 上一轮 budget="low"，本轮用户说"加个拍照的地方" → 仍用 budget="low"
- 用户说了"不要商场" → 后续所有轮次都应排除商场
- 只有用户明确推翻时才改变（如"不想看风景了，找吃的"）

## 风格指南

- 语气轻松友好但不油腻，像旅行达人在给朋友建议
- 不说「根据算法」或「系统显示」之类的词
- 路线不可行时（工具返回 error），如实告知并给出替代建议
- 用中文回复

## 重要规则

- **必须调用 build_route**：选定聚簇后，必须调用 build_route 获取真实路线数据，不能凭空编造
- **query_clusters 最多调 2 次**：如果第一次结果不理想，可以换关键词再试一次。如果 2 次都不够好，就在现有结果中选最好的，不要反复重试
- **build_route 最多调 2 次**：第一次用初步选的簇，如果结果不理想（如 stops 与用户提到的地方无关），可以换一组簇再试一次。但如果两次都不行，不要反复重试
- **工具调用总数控制在 6 次以内**：geocode(1-2次) + query_clusters(1-2次) + build_route(1-2次) = 总调用 ≤ 6 次
- **使用真实数据预览**：预览中的交通方式、耗时必须来自 build_route 返回值
- **用户明确提到的地名必须出现在路线中**：如果用户说了"去回民街""去大雁塔"，那么该地名对应的 POI 必须作为 stop 或终点出现在最终 stops 中。如果 query_clusters 返回的簇里没有覆盖该地名，要在 build_route 之前先 geocode 该地点，然后用它的坐标调 query_clusters
- **选 5-8 个簇**：让后续交互式编辑有足够候选 POI。但 num_stops 不要超过 cluster_ids 数量"""



def run_unified_agent(user_input: str, session: AgentSession = None,
                      user_id: str = "default",
                      progress_callback=None) -> tuple:
    """统一 Agent 入口 — 工具调用循环."""
    if session is None:
        session = AgentSession()

    _p = lambda emoji, msg: _progress(emoji, msg, callback=progress_callback)

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

    # Agent 状态（工具之间共享）— 多轮对话时从 session 恢复
    agent_state = {
        "origin_coords": session.origin_coords,
        "dest_coords": session.dest_coords,
        "dest_name": session.dest_name or "",
        "start_name": session.start_name or "",
        "city": session.city or "",
    }

    # 如果 session 中已有坐标，提示 LLM 可以跳过 geocode
    if agent_state["origin_coords"] and agent_state["start_name"]:
        _p("📍", f"起点已缓存：{agent_state['start_name']}")

    _p("🤖", "统一 Agent 启动（工具调用模式）")

    # 工具调用预算 + 超时保护
    _BUDGET = {"geocode": 2, "query_clusters": 3, "build_route": 2}
    _budget_used = {"geocode": 0, "query_clusters": 0, "build_route": 0}
    _start_time = time.time()
    _TIMEOUT = 90  # 秒，超过后强制收束

    narration = ""
    for iteration in range(_MAX_TOOL_ITERATIONS):
        elapsed = time.time() - _start_time

        # 超时保护：超过 90s 且有簇数据 → 强制 build_route 并结束
        if elapsed > _TIMEOUT and agent_state.get("last_clusters"):
            _p("⏰", f"超时保护({elapsed:.0f}s)，强制收束")
            try:
                result_json = execute_tool("build_route", {
                    "cluster_ids": agent_state["last_clusters"][:3],
                    "num_stops": min(3, len(agent_state["last_clusters"])),
                }, agent_state)
            except Exception:
                pass
            if agent_state.get("path_result"):
                narration = "路线已生成（超时自动完成），请查看下方详情。"
                _p("✅", "超时收束完成")
                break
            else:
                # 最后一次尝试：直接用图规划
                try:
                    from app.pipeline.cluster_tools import tool_build_route
                    oc = agent_state.get("origin_coords")
                    dc = agent_state.get("dest_coords")
                    if oc:
                        r = tool_build_route(
                            agent_state["last_clusters"][:3],
                            min(3, len(agent_state["last_clusters"])),
                            origin_coords=oc, dest_coords=dc,
                            dest_name=agent_state.get("dest_name", ""),
                        )
                        if r.get("success") and r.get("stops"):
                            agent_state["stop_names"] = [s["name"] for s in r["stops"]]
                            agent_state["path_result"] = r
                            narration = "路线已生成（超时自动完成），请查看下方详情。"
                            _p("✅", "超时收束完成")
                            break
                except Exception:
                    pass
                narration = "抱歉，规划超时且无法自动完成。请尝试简化需求（如指定更明确的地点）。"
                break

        try:
            response = call_llm_with_tools(
                messages, top_cats_short,
                system=_SYSTEM_PROMPT,
                max_tokens=3000,
            )
        except Exception as e:
            _p("⚠️", f"LLM 调用失败: {e}")
            return f"抱歉，AI 服务暂时不可用：{e}", session

        content = response.get("content", [])
        text = extract_text(content)
        tool_uses = extract_tool_uses(content)

        # 无工具调用 → LLM 输出最终解说
        if not tool_uses:
            narration = text
            _p("✅", "路线规划完成")
            break

        # 有工具调用 → 检查预算后执行
        for tu in tool_uses:
            name = tu.get("name", "")
            tool_id = tu.get("id", "")
            inp = tu.get("input", {})

            # 预算检查
            if _budget_used.get(name, 0) >= _BUDGET.get(name, 1):
                _p("⏭️", f"跳过 {name}（预算耗尽：{_budget_used[name]}/{_BUDGET[name]}）")
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": tool_id,
                     "content": '{"skipped":true,"reason":"该工具调用次数已达上限，请使用已有数据继续"}'
                    }
                ]})
                continue

            _budget_used[name] = _budget_used.get(name, 0) + 1
            _p("🔧", f"调用工具：{name}({_brief_input(name, inp)}) "
                      f"[{_budget_used[name]}/{_BUDGET.get(name, '∞')}]")

            result_json = execute_tool(name, inp, agent_state)

            # query_clusters 成功后缓存 cluster_ids
            if name == "query_clusters":
                try:
                    r = json.loads(result_json)
                    if r.get("success") and r.get("clusters"):
                        agent_state["last_clusters"] = [c["cluster_id"] for c in r["clusters"][:5]]
                except Exception:
                    pass

            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tool_id, "content": result_json}
            ]})
    else:
        _p("⚠️", f"工具调用超过 {_MAX_TOOL_ITERATIONS} 轮，强制终止")
        # 尝试用缓存的簇构建
        if not narration and agent_state.get("last_clusters") and not agent_state.get("path_result"):
            result_json = execute_tool("build_route", {
                "cluster_ids": agent_state["last_clusters"][:3],
                "num_stops": min(3, len(agent_state["last_clusters"])),
            }, agent_state)
        if not narration:
            narration = "抱歉，路线规划超时了。请尝试简化需求（如指定更明确的地点或偏好）。"

    # 输出侧约束校验
    user_constraints = extract_constraints(user_input)
    violations, severity = check_constraints(
        session.stop_names or agent_state.get("stop_names", []),
        narration,
        agent_state.get("all_pois", []),
        user_constraints,
    )
    if violations:
        _p("⚠️", f"约束违规({severity}): {'; '.join(violations[:3])}")
    # 严重违规时在解说末尾追加警告
    if severity == "high" and violations:
        narration += "\n\n---\n⚠️ **注意**：当前路线可能存在以下问题，建议确认：\n"
        for v in violations[:3]:
            narration += f"- {v}\n"

    # 从 narration 中提取 mermaid 代码
    mermaid = _extract_mermaid(narration)

    # 填充 session 状态
    _finalize_session(session, agent_state, user_input, narration, violations)

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
    if session.origin_coords:
        parts.append(f"起点坐标已缓存：({session.origin_coords[0]:.4f}, {session.origin_coords[1]:.4f})，如地点未变可跳过 geocode")
    if session.dest_name:
        parts.append(f"上次终点：{session.dest_name}")
    if session.dest_coords:
        parts.append(f"终点坐标已缓存：({session.dest_coords[0]:.4f}, {session.dest_coords[1]:.4f})，如地点未变可跳过 geocode")
    if session.stop_names:
        parts.append(f"上次途经：{' → '.join(session.stop_names)}")
    if session.keywords:
        parts.append(f"上次偏好：{session.keywords}")
    if getattr(session, 'budget', None):
        parts.append(f"上次预算：{session.budget}")
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

    # 注入学习到的用户偏好
    try:
        pref_ctx = profile_mgr.get_preference_context()
        if pref_ctx:
            parts.append(f"用户偏好（从历史路线学习）：{pref_ctx}")
    except Exception:
        pass

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
                      user_input: str, narration: str, violations: list = None):
    """将 agent_state 中的信息同步回 AgentSession."""
    session.last_user_input = user_input
    if violations:
        session.violations = violations  # 存到 session 供前端展示

    # 解说/数据一致性后校验：检测 narration 提到的 POI 名是否在 stops 中
    stop_names = agent_state.get("stop_names", [])
    if stop_names and narration:
        missing = [s for s in stop_names if s not in narration]
        if len(missing) >= len(stop_names) // 2:
            _progress("⚠️", "解说可能未使用真实路线数据")

    # 用户明确提到的地名是否出现在路线中
    dest_name = agent_state.get("dest_name", "")
    if dest_name and stop_names:
        # 检查终点名或其关键词是否出现在 stops 中
        dest_keywords = [dest_name, dest_name.replace("街", ""), dest_name.replace("路", "")]
        dest_in_stops = any(
            any(dk in s or s in dk for dk in dest_keywords)
            for s in stop_names
        )
        if not dest_in_stops and len(dest_name) > 2:
            _progress("⚠️", f"用户提到的'{dest_name}'未出现在路线stops中")

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

    # 保存关键词和预算到 session，供下轮多轮对话复用
    if agent_state.get("last_keywords"):
        session.keywords = agent_state["last_keywords"]
    if agent_state.get("last_budget"):
        session.budget = agent_state["last_budget"]

    # 设置 all_pois（优先从 agent_state）
    if agent_state.get("all_pois"):
        session.all_pois = agent_state["all_pois"]

    # 同步走廊数据到 session
    corridor_data = agent_state.get("corridor_data")
    if corridor_data:
        session.corridor_pois = corridor_data.get("corridor_pois", [])
        session.corridor_clusters = corridor_data.get("cluster_markers", [])
        session.corridor_shape = corridor_data.get("corridor_shape", [])

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
