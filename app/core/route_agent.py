"""统一路线 Agent — LLM 工具调用替代多 Agent 串行.

罗斯方案 Step 2: 单次 LLM 调用 + 工具循环，合并 intent → poi_strategy → narrator → reviewer.
"""

import json
import time

from app.core.constraint_model import RouteConstraints
from app.llm_client import call_llm_with_tools, extract_text, extract_tool_uses
from app.pipeline.cluster_tools import TOOL_DEFINITIONS, execute_tool
from app.pipeline.constraint_checker import check_constraints, extract_constraints
from app.pipeline.input_enricher import InputEnricher
from app.shared.utils import (
    AgentSession,
    _build_route_html,
    _extract_city,
    _progress,
    extract_mermaid_from_text,
)
from app.user_profile import UserProfileManager

_MAX_TOOL_ITERATIONS = 16

_SYSTEM_PROMPT = """你是一个本地路线规划助手「出发酱」。用工具逐步规划路线，最后给出简洁预览。

**⚠️ 最优先规则：只要用户表达了出行/吃喝/游玩/逛逛/去哪/推荐/好无聊等意图，无论是否有城市名，必须立即默认城市=西安并调用工具规划路线。严禁反问或输出纯文字建议。仅"你好"/"谢谢"/"你能做什么"等纯社交语句可以不用工具。**

## 工作流程（严格按序，不可跳过）
1. **geocode** — 解析起终点为经纬度；无终点时可只 geocode 起点
2. **query_clusters** — 沿途 POI 聚簇查询。
   - 默认 keywords=["美食","景点"]；无目的探索用 ["著名景点","必去","热门","美食","景点"]
   - 用户提了地名 → keywords 加入该地名特征词（如回民街→["小吃","夜市","美食"]）
   - **优先选 cluster_id=-2（城市热门）和 source="amap" 的簇**
3. **挑选 5-8 个簇**（为交互式编辑留候选）：
   - **靠拢路线优先**：off_route_km < 2 的簇优先。off_route_km 越大越偏离路线，>3km 的不选
   - **空间均匀**：projection 0.0-0.2/0.2-0.4/0.4-0.6/0.6-0.8/0.8-1.0 各至少 1 个，禁止集中
   - **品类匹配**：top_cats 必须匹配用户需求，不匹配不选
   - **top_poi_names 检查**：名称不像目标品类的簇（如找美食但名含"KTV""棋牌""洗浴"）不选
   - **用户指定地点优先**：地名一致的簇优先选入
   - **终点必含**：如果用户指定了终点地名，务必在 projection 0.8-1.0 段选簇覆盖该区域
   - 多样性和预算匹配
4. **build_route** — 用选定的 cluster_ids 构建路线。**此步骤不可跳过**。参数必须是 query_clusters 实际返回的 cluster_id。不对同一组 id 重复调用（换顺序不算）
5. **路线预览** — build_route 成功后，**仅写 2-3 句**（概述 + 站点名 + 总时长），不写 Mermaid 图

## 强制规则（最重要）
- **并行调用工具**：每轮可同时调用多个工具。geocode 起终点一次调用（用 places 数组），减少轮次
- **工具预算**：geocode≤2, query_clusters≤2, build_route≤2, 总调用≤6
- **关键词不要反复重试**：query_clusters 会自动扩展关键词，不需要换词重试

## 解说数据一致性（极其重要）
- 预览中每个 POI 名必须是 build_route 返回 stops[].name 的**精确值**
- 禁止编造不存在的 POI，禁止用常识替换实际路线
- 数据与需求严重不匹配时如实告知，不造假

## 多轮对话
- 约束保留：未被明确推翻的约束继续生效（如改时间不改偏好→偏好保留）
- 冲突：自相矛盾时友善指出反问；cache 的坐标可复用
- 始终用中文回复，语气轻松友好，不说「根据算法」「系统显示」

## 最终解说格式（必须严格遵守）
生成最终解说时，必须基于 build_route 工具返回的真实数据：
- **站点列表**：按顺序列出 build_route 返回的所有 stop name，一个不漏
- **真实数据**：时长和距离必须使用 build_route 返回的 total_duration_min / total_distance，严禁自编数字
- **推荐理由**：每个站点附带 1 句简短推荐（参考该簇的 top_poi_names）
- **禁止虚构**：严禁提到不存在的站点、活动或数据
- **Mermaid 图**：所有站点（含起终点）必须出现在 mermaid 图中
"""


def run_unified_agent(
    user_input: str, session: AgentSession = None, user_id: str = "default", progress_callback=None
) -> tuple:
    """统一 Agent 入口 — 工具调用循环."""
    if session is None:
        session = AgentSession()

    def _p(emoji, msg):
        return _progress(emoji, msg, callback=progress_callback)

    profile_mgr = UserProfileManager(user_id=user_id)
    user_data = profile_mgr.load()

    # ── InputEnricher: resolve defaults before LLM sees user input ──
    enriched = InputEnricher.enrich(
        user_input,
        session_city=session.city or session.default_city or "西安",
        session_keywords=session.keywords if session.keywords != "美食,景点" else "",
    )
    session._last_enriched = enriched  # stash for _build_messages

    # 精简 subcategory 显示（只保留最后一段）
    top_cats_short = []
    for t in TOOL_DEFINITIONS:
        tc = dict(t)
        if tc["name"] == "query_clusters":
            tc["description"] += " 注意：top_cats 是子品类简称（如「火锅店」「咖啡厅」），不是完整的层级路径。"
        top_cats_short.append(tc)

    # 构建消息
    messages = _build_messages(user_input, session, user_data, profile_mgr)

    # Agent 状态（工具之间共享）— 多轮对话时从 session 恢复
    agent_state = {
        "origin_coords": session.origin_coords,
        "dest_coords": session.dest_coords,
        "dest_name": session.dest_name or "",
        "start_name": session.start_name or "",
        "city": session.city or "",
    }

    # Inject city from InputEnricher if not already set
    if not agent_state.get("city"):
        agent_state["city"] = enriched.city
    agent_state["_enriched"] = enriched

    # Initialize constraints from session or scratch
    if session.constraints is None:
        session.constraints = RouteConstraints()

    # Merge current input
    round_num = getattr(session, "_round_count", 0) + 1
    session._round_count = round_num
    session.constraints = session.constraints.merge(user_input, round_num=round_num)

    # Apply enriched data to fill gaps
    if enriched.budget_hint and not session.constraints.budget:
        session.constraints.budget = enriched.budget_hint
    if enriched.exclusions:
        for e in enriched.exclusions:
            if e not in session.constraints.exclusions:
                session.constraints.exclusions.append(e)

    # 如果 session 中已有坐标，提示 LLM 可以跳过 geocode
    if agent_state["origin_coords"] and agent_state["start_name"]:
        _p("📍", f"起点已缓存：{agent_state['start_name']}")

    _p("🤖", "统一 Agent 启动（工具调用模式）")

    # 工具调用预算 + 超时保护
    _BUDGET = {"geocode": 2, "query_clusters": 2, "build_route": 2}
    _budget_used = {"geocode": 0, "query_clusters": 0, "build_route": 0}
    _start_time = time.time()
    _TIMEOUT = 90  # 秒，超过后强制收束

    narration = ""
    for _iteration in range(_MAX_TOOL_ITERATIONS):
        elapsed = time.time() - _start_time

        # 超时保护：超过 90s 且有簇数据 → 强制 build_route 并结束
        if elapsed > _TIMEOUT and agent_state.get("last_clusters"):
            _p("⏰", f"超时保护({elapsed:.0f}s)，强制收束")
            try:
                result_json = execute_tool(
                    "build_route",
                    {
                        "cluster_ids": agent_state["last_clusters"][:3],
                        "num_stops": min(3, len(agent_state["last_clusters"])),
                    },
                    agent_state,
                )
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
                            origin_coords=oc,
                            dest_coords=dc,
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
                messages,
                top_cats_short,
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
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_id,
                                "content": '{"skipped":true,"reason":"该工具调用次数已达上限，请使用已有数据继续"}',
                            }
                        ],
                    }
                )
                continue

            _budget_used[name] = _budget_used.get(name, 0) + 1
            _p("🔧", f"调用工具：{name}({_brief_input(name, inp)}) [{_budget_used[name]}/{_BUDGET.get(name, '∞')}]")

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
            messages.append(
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": result_json}]}
            )
    else:
        _p("⚠️", f"工具调用超过 {_MAX_TOOL_ITERATIONS} 轮，强制终止")
        # 尝试用缓存的簇构建
        if not narration and agent_state.get("last_clusters") and not agent_state.get("path_result"):
            result_json = execute_tool(
                "build_route",
                {
                    "cluster_ids": agent_state["last_clusters"][:3],
                    "num_stops": min(3, len(agent_state["last_clusters"])),
                },
                agent_state,
            )
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

    # 解说对齐：确保引用真实路线数据
    narration = _align_narration(narration, agent_state)

    # 从 narration 中提取 mermaid 代码
    narration, mermaid = extract_mermaid_from_text(narration)

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


def _build_messages(user_input: str, session: AgentSession, user_data: dict, profile_mgr=None) -> list:
    """构建初始消息列表."""
    messages = []

    # 注入多轮上下文
    if session.last_user_input:
        ctx = _build_context(session, user_data, profile_mgr)
        constraint_block = ""
        if session.constraints and not session.constraints.is_empty():
            constraint_block = session.constraints.to_prompt_block()

        conflicts = session.constraints.get_conflicts(user_input) if session.constraints else []
        conflict_note = ""
        if conflicts:
            conflict_note = "⚠️ 约束冲突：" + "; ".join(conflicts) + " → 以本轮输入为准。\n\n"

        # 注入预处理结果（城市/偏好/排除）
        enriched = getattr(session, "_last_enriched", None)
        enriched_prefix = enriched.enriched_text + "\n\n" if enriched else ""

        messages.append(
            {
                "role": "user",
                "content": f"{enriched_prefix}{constraint_block}\n{conflict_note}## 路线参考\n{ctx}\n\n## 用户输入\n{user_input}\n\n请根据新输入重新规划。冲突以新输入为准。",
            }
        )
    else:
        enriched = getattr(session, "_last_enriched", None)
        if enriched:
            messages.append({"role": "user", "content": f"{enriched.enriched_text}\n\n用户输入：{user_input}"})
        else:
            messages.append({"role": "user", "content": user_input})

    return messages


def _build_context(session: AgentSession, user_data: dict, profile_mgr=None) -> str:
    """从 session 构建上下文摘要."""
    parts = [f"城市：{session.city}"]
    if session.start_name:
        parts.append(f"上次起点：{session.start_name}")
    if session.origin_coords:
        parts.append(
            f"起点坐标已缓存：({session.origin_coords[0]:.4f}, {session.origin_coords[1]:.4f})，如地点未变可跳过 geocode"
        )
    if session.dest_name:
        parts.append(f"上次终点：{session.dest_name}")
    if session.dest_coords:
        parts.append(
            f"终点坐标已缓存：({session.dest_coords[0]:.4f}, {session.dest_coords[1]:.4f})，如地点未变可跳过 geocode"
        )
    if session.stop_names:
        parts.append(f"上次途经：{' → '.join(session.stop_names)}")
    if session.keywords:
        parts.append(f"上次偏好：{session.keywords}")
    if getattr(session, "budget", None):
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

    # Inject structured constraints
    if session.constraints and not session.constraints.is_empty():
        c = session.constraints
        if c.budget:
            labels = {"low": "低(<40元)", "medium": "中(30-100元)", "high": "高(>80元)"}
            parts.append(f"约束-预算：{labels.get(c.budget, c.budget)}")
        if c.dietary:
            parts.append(f"约束-饮食：{', '.join(c.dietary)}")
        if c.exclusions:
            parts.append(f"约束-排除：{', '.join(c.exclusions)}")

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




def _align_narration(narration: str, agent_state: dict) -> str:
    """Post-process narration to ensure alignment with actual route data."""
    stops = agent_state.get("stop_names", [])
    path = agent_state.get("path_result", {})
    all_pois = agent_state.get("all_pois", [])

    if not stops or not narration:
        return narration

    # Smart partial matching: stop name in narration, or significant overlap
    def _is_mentioned(stop, text):
        if stop in text:
            return True
        # Check if a significant part of stop name appears in narration
        for prefix_len in [max(4, len(stop)*3//4), len(stop)//2, max(3, len(stop)//3)]:
            prefix = stop[:prefix_len]
            if len(prefix) >= 3 and prefix in text:
                return True
        # Check if stop contains a long enough substring from narration
        # (handles abbreviations like '老刘家泡馍' for '老刘家泡馍.陕西老字号(...)')
        stop_parts = stop.replace('.', ' ').replace('(', ' ').replace(')', ' ').split()
        # If any 4+ char part of stop appears in narration
        for part in stop_parts:
            if len(part) >= 4 and part in text:
                return True
        return False

    mentioned = [s for s in stops if _is_mentioned(s, narration)]
    missing = [s for s in stops if not _is_mentioned(s, narration)]

    if missing:
        lines = ["\n\n---\n## 📍 路线详情\n"]
        for i, stop in enumerate(stops, 1):
            poi_info = next((p for p in all_pois if p.get("name") == stop), {})
            cat = poi_info.get("category", "")
            rating = poi_info.get("rating", "")
            extra = ""
            if cat:
                extra = f" ({cat}"
                if rating:
                    extra += f" ⭐{rating}"
                extra += ")"
            lines.append(f"{i}. **{stop}**{extra}")

        duration = path.get("total_duration_min", 0)
        distance = path.get("total_distance", 0)
        if duration:
            lines.append(f"\n⏱ 总时长约 {duration} 分钟")
        if distance:
            lines.append(f"📏 总距离约 {distance/1000:.1f} 公里")

        narration = narration.rstrip() + "\n".join(lines)

    return narration


def _finalize_session(
    session: AgentSession, agent_state: dict, user_input: str, narration: str, violations: list = None
):
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

    # 用户明确提到的地名是否出现在路线中（优先用 constraints.must_include）
    must_places = []
    if hasattr(session, "constraints") and session.constraints:
        must_places = list(session.constraints.must_include)
    if not must_places:
        dest_name = agent_state.get("dest_name", "")
        if dest_name and len(dest_name) > 2:
            # 过滤掉纯城市名（西安/北京等）免得误报
            _CITY_NAMES = {
                "西安",
                "北京",
                "上海",
                "成都",
                "杭州",
                "深圳",
                "广州",
                "南京",
                "武汉",
                "重庆",
                "天津",
                "长沙",
            }
            if dest_name.rstrip("市") not in _CITY_NAMES:
                must_places = [dest_name]

    if must_places and stop_names:
        for mp in must_places:
            mp_keywords = [mp, mp.replace("街", ""), mp.replace("路", ""), mp.replace("·", "")]
            found = any(any(dk in s or s in dk for dk in mp_keywords) for s in stop_names)
            if not found and len(mp) > 2:
                _progress("⚠️", f"用户提到的'{mp}'未出现在路线stops中")

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
    city = agent_state.get("city") or _extract_city(user_input, getattr(session, "default_city", ""))
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
            segments.append(
                {
                    "from": prev_name,
                    "to": s["name"],
                    "transport": s.get("transport_from_prev", "步行"),
                    "distance": s.get("distance_m", 0),
                    "duration": s.get("duration_min", 0) * 60,
                }
            )

        # 添加终点段（如果 dest_name 存在且不等于最后一站）
        dest_name = agent_state.get("dest_name") or ""
        last_name = stops[-1]["name"] if stops else ""
        if dest_name and dest_name != last_name:
            segments.append(
                {
                    "from": last_name,
                    "to": dest_name,
                    "transport": "步行",
                    "distance": 500,
                    "duration": 600,
                }
            )

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

    # Merge agent_state constraints into session (additive, not overwrite)
    # session.constraints was already set in run_unified_agent; agent_state may have
    # additional constraint data from cluster_tools (e.g. enriched keywords/budget)
    ac = agent_state.get("constraints")
    if ac and session.constraints:
        # Only fill in gaps — don't overwrite user-set constraints
        if not session.constraints.budget and ac.budget:
            session.constraints.budget = ac.budget
        if not session.constraints.preferred_categories and ac.preferred_categories:
            session.constraints.preferred_categories = ac.preferred_categories


def _write_output_files(session: AgentSession, narration: str, mermaid: str, user_input: str):
    """生成 Mermaid 和 HTML 输出文件（与现有流程兼容）."""
    from pathlib import Path

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
