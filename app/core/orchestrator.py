"""Orchestrator — 多智能体协同主控，支持多轮对话 + 用户画像持久化.

流程:
  第1轮: 完整 PLAN → EXECUTE → REVIEW → NARRATE → OUTPUT
  第N轮: DETECT MODIFICATION → 只重跑受影响环节 → NARRATE → OUTPUT
  每轮结束: 保存用户画像 + session 到 users/{user_id}.json
"""

import json
import re
import time
from pathlib import Path

from app.llm_client import call_llm
from app.user_profile import UserProfileManager

from app.core.intent_agent import run_intent_agent
from app.core.poi_strategy_agent import build_search_strategy, evaluate_pois, get_research_adjustments
from app.core.narrator_agent import run_narrator, NarrationContext
from app.core.reviewer_agent import run_reviewer
from app.core.modifier_agent import detect_modification

from app.providers.amap_provider import search_poi, search_around, search_along_route, geocode, robust_geocode, AmapAPIError
from app.algorithms.graph_planner import build_graph, shortest_path, decide_transport
from app.algorithms.poi_filter import normalize_keywords, filter_by_category, filter_by_coords, filter_near_anchor, deduplicate_by_name
from app.algorithms.geo import haversine

from app.shared.utils import (
    _extract_city, _infer_city_from_geocode, _progress,
    _build_mermaid_from_path, _build_route_html, AgentSession,
)

MAX_REVIEW_LOOPS = 2

# 地名正则
_PLACE_RE = re.compile(
    r"[一-鿿]{2,6}(?:地铁站|轻轨站|高铁站|火车站|汽车站|公交站|"
    r"路|街|巷|道|里|胡同|园|公园|广场|大厦|商场|购物中心|门|楼|塔|"
    r"景区|博物馆|图书馆|医院|学校|大学|学院|机场|码头)"
)


# ── 内部辅助函数 ──────────────────────────────────────

def _execute_search(strategy_regions: list, city: str, origin_coords=None, dest_coords=None) -> list:
    """执行搜索策略，返回去重后的 POI 列表."""
    all_pois = []
    for region in strategy_regions:
        normalized_kws = normalize_keywords(region.keywords)
        if not normalized_kws:
            normalized_kws = ["美食", "景点"]

        use_around, loc_str = False, ""
        try:
            gc = geocode(region.center, city)
            if "lng" in gc and "lat" in gc:
                loc_str = f"{gc['lng']},{gc['lat']}"
                use_around = True
        except AmapAPIError:
            pass

        for kw in normalized_kws[:3]:
            try:
                if use_around:
                    pois = search_around(loc_str, kw, radius=min(region.radius, 5000), limit=10)
                else:
                    pois = search_poi(keywords=kw, location=city, limit=10)
            except AmapAPIError:
                continue
            _progress("   →", f"「{region.center}」搜到 {len(pois)} 个「{kw}」")
            all_pois.extend(pois)
            time.sleep(0.05)

    # 沿途补充搜索
    if origin_coords and dest_coords:
        o_str = f"{origin_coords[1]},{origin_coords[0]}"
        d_str = f"{dest_coords[1]},{dest_coords[0]}"
        for region in strategy_regions:
            for kw in region.keywords[:2]:
                try:
                    pois = search_along_route(o_str, d_str, kw, radius=2000, limit=10)
                    all_pois.extend(pois)
                except AmapAPIError:
                    pass
                time.sleep(0.05)
    elif origin_coords:
        o_str = f"{origin_coords[1]},{origin_coords[0]}"
        for region in strategy_regions:
            for kw in region.keywords[:2]:
                try:
                    pois = search_around(o_str, kw, radius=5000, limit=10)
                    all_pois.extend(pois)
                except AmapAPIError:
                    pass
                time.sleep(0.05)
    elif dest_coords:
        d_str = f"{dest_coords[1]},{dest_coords[0]}"
        for region in strategy_regions:
            for kw in region.keywords[:2]:
                try:
                    pois = search_around(d_str, kw, radius=5000, limit=10)
                    all_pois.extend(pois)
                except AmapAPIError:
                    pass
                time.sleep(0.05)

    return deduplicate_by_name(all_pois)


def _filter_and_validate(all_pois: list, origin_name: str, dest_name: str,
                         origin_coords=None, dest_coords=None) -> list:
    """过滤 POI."""
    filtered = filter_by_category(all_pois)
    filtered = filter_by_coords(filtered)
    filtered = filter_near_anchor(filtered, origin_coords, origin_name)
    filtered = filter_near_anchor(filtered, dest_coords, dest_name) if dest_coords and dest_name else filtered
    return filtered


def _run_route_engine(origin_coords, valid_pois: list, dest_coords, num_stops: int,
                      time_budget_hours: float = None) -> dict:
    """建图 → 最短路径，可选时间预算约束."""
    if not origin_coords and not dest_coords:
        return None
    if origin_coords and valid_pois:
        nodes, graph = build_graph(origin_coords, valid_pois, dest_coords)
        path = shortest_path(graph, nodes, num_stops)
    elif dest_coords and valid_pois:
        nodes, graph = build_graph(dest_coords, valid_pois, dest_coords)
        path = shortest_path(graph, nodes, num_stops)
    else:
        return None

    if path and time_budget_hours:
        budget_minutes = time_budget_hours * 60
        if path["total_duration_min"] > budget_minutes * 1.2:
            _progress("⏱️", f"总耗时 {path['total_duration_min']} 分超出预算 {budget_minutes} 分，自动减站")
            for reduced in range(num_stops - 1, 0, -1):
                nodes2, graph2 = build_graph(origin_coords or dest_coords, valid_pois, dest_coords)
                path2 = shortest_path(graph2, nodes2, reduced)
                if path2 and path2["total_duration_min"] <= budget_minutes * 1.1:
                    _progress("   →", f"缩减为 {reduced} 站，耗时 {path2['total_duration_min']} 分")
                    return path2
            _progress("   →", "即使缩减仍超出预算，使用当前最佳方案")
    return path


def _geocode_place(name: str, city: str, user_input: str = "", skip_names: list = None) -> tuple:
    """地理编码单个地名，失败时尝试从 user_input 中正则提取."""
    if not name:
        return None, ""
    skip_names = skip_names or []
    lat, lng = robust_geocode(name, city)
    if lat is not None:
        return (lat, lng), name
    # 正则回退
    for m in _PLACE_RE.findall(user_input):
        if m in skip_names:
            continue
        lat, lng = robust_geocode(m, city)
        if lat is not None:
            return (lat, lng), m
    return None, name


def _is_chat_message(user_input: str) -> bool:
    """判断是否为闲聊消息（非路线请求）."""
    chat_patterns = [
        r"^(你好|hi|hello|嗨|嘿|谢谢|感谢|ok|好的|嗯|哦|哈哈|是的|对|没错)[!！。.]*$",
        r"^(你是谁|你叫什么|你能做什么|帮助|help|what can you do)",
        r"^(\?|？)$",
    ]
    return any(re.match(p, user_input.strip(), re.IGNORECASE) for p in chat_patterns)


# ── 主入口 ────────────────────────────────────────────

def run_multi_agent(user_input: str, session: AgentSession = None,
                    user_id: str = "default") -> tuple:
    """多智能体协同路线规划，支持多轮对话 + 用户画像.

    Args:
        user_input: 用户当前输入
        session: AgentSession（内部使用，可传 None）
        user_id: 用户 ID（默认 "default"，接入登录系统后替换）

    Returns:
        (回复文本, AgentSession)
    """
    if session is None:
        session = AgentSession()

    # ── 加载用户画像 ─────────────────────────────────
    profile_mgr = UserProfileManager(user_id=user_id)
    user_data = profile_mgr.load()
    saved_session = user_data.get("session", {})

    # 闲聊消息直接回复
    if _is_chat_message(user_input):
        try:
            data = call_llm(
                messages=[{"role": "user", "content": user_input}],
                system="你是一个友好的路线规划助手。简短回复，不超过两句话。",
                max_tokens=100,
            )
            return data["content"][0]["text"], session
        except Exception:
            return "你好！我是路线规划助手，告诉我你想去哪里，我帮你规划。", session

    # ── 判断新路线还是修改 ───────────────────────────
    session_state = saved_session if not session.city else {
        "city": session.city,
        "origin": session.start_name,
        "origin_coords": getattr(session, '_origin_coords', None),
        "destination": session.dest_name,
        "dest_coords": getattr(session, '_dest_coords', None),
        "last_stops": session.stop_names,
        "num_stops": getattr(session, '_num_stops', 3),
        "keywords": getattr(session, '_keywords', "美食,景点"),
        "last_user_input": getattr(session, '_last_user_input', ""),
    }

    is_new_session = not session_state.get("city")
    modification = None

    if not is_new_session:
        current_context = {
            "city": session_state.get("city", ""),
            "origin": session_state.get("origin", ""),
            "destination": session_state.get("destination", ""),
            "num_stops": session_state.get("num_stops", 3),
            "keywords": session_state.get("keywords", "美食,景点"),
            "preferences": user_data.get("profile", {}),
        }
        modification = detect_modification(user_input, current_context)
        # new_route 或 none 视为全新规划
        if modification.change_type in ("new_route", "none"):
            is_new_session = True
            session = AgentSession()  # 重置 session
            if user_data.get("profile", {}).get("city"):
                session.default_city = user_data["profile"].get("default_city", "")

    # ═══════════════════════════════════════════════════
    # 完整规划流程（新路线 或 new_route）
    # ═══════════════════════════════════════════════════
    if is_new_session:
        return _run_full_plan(user_input, session, profile_mgr, user_data)

    # ═══════════════════════════════════════════════════
    # 多轮修改流程
    # ═══════════════════════════════════════════════════
    return _run_modification(user_input, session, profile_mgr, user_data,
                             session_state, modification)


# ── 完整规划 ──────────────────────────────────────────

def _run_full_plan(user_input: str, session: AgentSession,
                   profile_mgr: UserProfileManager, user_data: dict) -> tuple:
    """完整 6 步 PLAN 流程."""
    _progress("🤖", "多智能体协同规划启动")

    # 1. 提取城市
    city = _extract_city(user_input, session.default_city)
    intent_result = None
    if not city:
        intent_result = run_intent_agent(user_input, "")
        city = _infer_city_from_geocode(intent_result.origin)
    if not city:
        return "请问您在哪个城市？我需要先知道城市才能为你查找地点和规划路线。", session
    session.city = city
    _progress("📍", f"城市：{city}")

    # 2. 意向解析
    _progress("🧠", "Intent Agent 深度解析出行意图")
    if intent_result is None:
        intent_result = run_intent_agent(user_input, city)
    _progress("   →", f"起点：{intent_result.origin or '未指定'}")
    _progress("   →", f"终点：{intent_result.destination or '由路线决定'}")
    _progress("   →", f"群体：{intent_result.user_profile.group_type}，体力：{intent_result.user_profile.energy_level}")
    if intent_result.time_budget_hours:
        _progress("   →", f"时间预算：{intent_result.time_budget_hours} 小时")

    # 更新用户画像（从意图推理中学习偏好）
    if intent_result.preference_reasoning:
        profile_mgr.update_profile(intent_result.user_profile)

    # 3. 地理编码
    _progress("📍", "解析起终点坐标")
    origin_name = intent_result.origin or ""
    dest_name = intent_result.destination or ""
    origin_coords, origin_name = _geocode_place(origin_name, city, user_input)
    dest_coords, dest_name = _geocode_place(dest_name, city, user_input, skip_names=[origin_name])

    if origin_coords:
        _progress("   →", f"起点：{origin_coords[1]},{origin_coords[0]}")
    else:
        _progress("⚠️", "起点未能解析")
    if dest_coords:
        _progress("   →", f"终点：{dest_coords[1]},{dest_coords[0]}")
    elif dest_name:
        _progress("⚠️", "终点未能解析")

    # 4. POI 搜索
    _progress("🎯", "POI Strategy Agent 制定搜索策略")
    strategy = build_search_strategy(intent_result, origin_coords, dest_coords)
    for r in strategy.regions:
        _progress("   →", f"{r.center} | {', '.join(r.keywords)} | {r.radius}m | {r.reason}")

    _progress("🔍", "执行 POI 搜索")
    all_pois = _execute_search(strategy.regions, city, origin_coords, dest_coords)

    if len(all_pois) < 3:
        _progress("⚠️", "搜索结果不足，全城兜底搜索")
        for kw in strategy.fallback_keywords:
            try:
                pois = search_poi(keywords=kw, location=city, limit=10)
                all_pois.extend(pois)
            except AmapAPIError:
                pass
        all_pois = deduplicate_by_name(all_pois)

    valid_pois = _filter_and_validate(all_pois, origin_name, dest_name, origin_coords, dest_coords)
    _progress("✅", f"共获取 {len(all_pois)} 个 POI，{len(valid_pois)} 个含坐标可建图")

    # POI 质量评估 → 不足时补搜
    quality = evaluate_pois(valid_pois, intent_result)
    _progress("📊", f"POI 质量评估：{quality.summary}")
    if quality.needs_research and quality.research_suggestions:
        _progress("🔄", "POI 质量不足，自动补搜")
        adjusted = get_research_adjustments(quality.research_suggestions, intent_result)
        if adjusted.regions:
            new_pois = _execute_search(adjusted.regions, city, origin_coords, dest_coords)
            all_pois.extend(new_pois)
            all_pois = deduplicate_by_name(all_pois)
            valid_pois = _filter_and_validate(all_pois, origin_name, dest_name, origin_coords, dest_coords)
            _progress("✅", f"补搜后共 {len(valid_pois)} 个有效 POI")

    # 5. 路线算法
    _progress("🗺️", "Route Engine 构建路线图")
    num_stops = min(intent_result.num_stops, 5)
    stashed_origin_coords = origin_coords  # 保存用于 HTML map 输出
    path_result = _run_route_engine(origin_coords, valid_pois, dest_coords, num_stops,
                                    time_budget_hours=intent_result.time_budget_hours)

    if path_result is None:
        _progress("⚠️", "坐标不足，跳过图规划")
        try:
            data = call_llm(
                messages=[{"role": "user", "content": f"城市：{city}\n需求：{user_input}\n请规划路线。"}],
                system="你是一个路线规划助手。", max_tokens=1500,
            )
            return data["content"][0]["text"], session
        except Exception:
            return "抱歉，路线暂时无法生成，请提供更多信息（如具体的起点或区域）。", session

    stop_names = [
        s["to"] for s in path_result["segments"]
        if s["to"] not in (origin_name, dest_name, "终点")
    ]

    # 6. 解说 → 审核 → 输出（与修改流程共用）
    return _narrate_review_output(
        user_input, session, profile_mgr, user_data,
        origin_name, dest_name, city,
        origin_coords=stashed_origin_coords, dest_coords=dest_coords,
        all_pois=all_pois, valid_pois=valid_pois,
        path_result=path_result, stop_names=stop_names,
        num_stops=num_stops, keywords=",".join(intent_result.keywords),
        intent_result=intent_result,
    )


# ── 多轮修改 ──────────────────────────────────────────

def _run_modification(user_input: str, session: AgentSession,
                      profile_mgr: UserProfileManager, user_data: dict,
                      state: dict, mod: 'ModificationIntent') -> tuple:
    """增量修改已有路线（只重跑受影响环节）."""
    _progress("🔄", f"检测到修改意图：{mod.change_type}（{mod.reasoning}）")
    city = state.get("city", "")
    origin_name = state.get("origin", "")
    dest_name = state.get("destination", "")
    keywords = state.get("keywords", "美食,景点")
    num_stops = state.get("num_stops", 3)
    origin_coords = state.get("origin_coords")
    dest_coords = state.get("dest_coords")

    # 恢复 session 基本状态
    session.city = city
    session.start_name = origin_name

    need_research = False
    need_regraph = False
    need_renarrate = True

    # 初始化为 None，由各分支填充
    valid_pois = None
    all_pois = None

    change_type = mod.change_type
    params = mod.params

    if change_type == "change_origin":
        new_origin = params.get("origin", "")
        origin_coords, origin_name = _geocode_place(new_origin, city, user_input)
        _progress("   →", f"起点更新为：{origin_name}")
        need_research = True
        need_regraph = True

    elif change_type == "change_destination":
        new_dest = params.get("destination", "")
        dest_coords, dest_name = _geocode_place(new_dest, city, user_input, skip_names=[origin_name])
        _progress("   →", f"终点更新为：{dest_name}")
        need_research = True
        need_regraph = True

    elif change_type == "change_keywords":
        keywords = params.get("keywords", keywords)
        _progress("   →", f"搜索关键词更新为：{keywords}")
        need_research = True
        need_regraph = True

    elif change_type == "change_num_stops":
        new_num = params.get("num_stops")
        if new_num and 1 <= new_num <= 10:
            num_stops = new_num
            _progress("   →", f"站点数更新为：{num_stops}")
            need_regraph = True

    elif change_type == "change_preferences":
        # 更新画像
        from app.core.intent_agent import run_intent_agent
        updated_intent = run_intent_agent(user_input, city)
        profile_mgr.update_profile(updated_intent.user_profile)
        _progress("   →", f"偏好已更新：{updated_intent.preference_reasoning}")
        need_renarrate = True  # 解说语气需要调整

    elif change_type == "change_poi_location":
        anchor = params.get("anchor", "")
        _progress("   →", f"在「{anchor}」附近重新搜索")
        from app.core.poi_strategy_agent import SearchRegion, SearchStrategy
        anchor_strategy = SearchStrategy(regions=[
            SearchRegion(center=anchor, keywords=keywords.split(","), radius=2000, reason=f"用户指定「{anchor}」附近")
        ])
        all_pois = _execute_search(anchor_strategy.regions, city, origin_coords, dest_coords)
        valid_pois = _filter_and_validate(all_pois, origin_name, dest_name, origin_coords, dest_coords)
        _progress("   →", f"搜到 {len(valid_pois)} 个有效 POI")
        need_regraph = True

    elif change_type == "adjust_constraint":
        time_budget = params.get("time_budget_hours")
        _progress("   →", f"时间约束更新为：{time_budget} 小时")
        need_regraph = True

    # ── 执行受影响环节 ───────────────────────────────
    if need_research:
        _progress("🔍", "重新搜索 POI")
        # 用当前参数重建搜索策略
        from app.core.intent_agent import run_intent_agent
        temp_intent = run_intent_agent(
            f"{origin_name} → {dest_name}，搜索 {keywords}，{num_stops} 站", city
        )
        strategy = build_search_strategy(temp_intent, origin_coords, dest_coords)
        all_pois = _execute_search(strategy.regions, city, origin_coords, dest_coords)
        if len(all_pois) < 3:
            for kw in strategy.fallback_keywords:
                try:
                    all_pois.extend(search_poi(keywords=kw, location=city, limit=10))
                except AmapAPIError:
                    pass
            all_pois = deduplicate_by_name(all_pois)
        valid_pois = _filter_and_validate(all_pois, origin_name, dest_name, origin_coords, dest_coords)
        _progress("✅", f"搜索到 {len(valid_pois)} 个有效 POI")

    # 未重新搜索则复用 session 中的 POI
    if valid_pois is None:
        valid_pois = session.all_pois or []
    if all_pois is None:
        all_pois = valid_pois

    if need_regraph:
        _progress("🗺️", "重新构建路线")
        time_budget = mod.params.get("time_budget_hours") if change_type == "adjust_constraint" else None
        path_result = _run_route_engine(origin_coords, valid_pois, dest_coords, num_stops,
                                        time_budget_hours=time_budget)
        if path_result is None:
            return "抱歉，调整后未能生成有效路线。请尝试换一个地点或放宽条件。", session
        stop_names = [
            s["to"] for s in path_result["segments"]
            if s["to"] not in (origin_name, dest_name, "终点")
        ]
    else:
        # 重新加载已有结果
        path_result = session.path_result
        stop_names = session.stop_names
        if path_result is None:
            return "当前没有可修改的路线。请先规划一条新路线。", session

    # ── 解说 + 审核 + 输出 ───────────────────────────
    # 构造修改后的 IntentResult 给 narrator/reviewer
    from app.core.types import UserProfile, IntentResult
    up = user_data.get("profile", {})
    profile = UserProfile(
        group_type=up.get("group_type", "solo"),
        energy_level=up.get("energy_level", "medium"),
        budget_level=up.get("budget_level", "medium"),
        interests=up.get("interests", []),
        notes=up.get("notes", ""),
    )
    simple_intent = IntentResult(
        origin=origin_name, destination=dest_name,
        keywords=keywords.split(",") if isinstance(keywords, str) else [keywords],
        num_stops=num_stops, user_profile=profile,
        raw_input=user_input,
    )

    return _narrate_review_output(
        user_input, session, profile_mgr, user_data,
        origin_name, dest_name, city,
        origin_coords=origin_coords, dest_coords=dest_coords,
        all_pois=all_pois or [],
        valid_pois=valid_pois or [],
        path_result=path_result, stop_names=stop_names,
        num_stops=num_stops, keywords=keywords,
        intent_result=simple_intent,
    )


# ── 解说 + 审核 + 输出（共用）─────────────────────────

def _narrate_review_output(
    user_input: str, session: AgentSession,
    profile_mgr: UserProfileManager, user_data: dict,
    origin_name: str, dest_name: str, city: str,
    origin_coords, dest_coords,
    all_pois: list, valid_pois: list,
    path_result: dict, stop_names: list,
    num_stops: int, keywords: str,
    intent_result,
) -> tuple:
    """解说 → 审核 → Refine → HTML/Mermaid输出 → 保存画像."""

    # Phase 2: NARRATE
    _progress("📝", "Narrator Agent 生成个性化解说")
    context = NarrationContext(
        start_name=origin_name or "起点",
        dest_name=dest_name,
        city=city,
        user_input=user_input,
        path_segments=path_result["segments"],
        total_duration_min=path_result["total_duration_min"],
        total_distance_m=path_result["total_distance"],
        user_profile=intent_result.user_profile,
    )
    narration = run_narrator(context)

    # Phase 3: REVIEW & REFINE
    review_loops = 0
    review_result = None
    while review_loops < MAX_REVIEW_LOOPS:
        _progress("🔍", f"Reviewer Agent 审核路线（第{review_loops + 1}轮）")
        review_result = run_reviewer(
            start_name=origin_name or "起点", dest_name=dest_name,
            city=city, user_input=user_input,
            path_segments=path_result["segments"],
            total_duration_min=path_result["total_duration_min"],
            total_distance_m=path_result["total_distance"],
            user_profile=intent_result.user_profile,
            time_budget_hours=intent_result.time_budget_hours,
        )
        _progress("   →", f"评分：{review_result.overall_score}/5 — {review_result.summary}")
        for iss in review_result.issues:
            _progress("   →", f"[{iss.severity}] {iss.description}")

        if not review_result.needs_retry:
            _progress("✅", "审核通过")
            break

        _progress("🔄", "审核不通过，调整搜索策略重新规划...")
        adjusted = get_research_adjustments(review_result.retry_suggestions, intent_result)
        if adjusted.regions:
            new_pois = _execute_search(adjusted.regions, city, origin_coords, dest_coords)
            all_pois.extend(new_pois)
            all_pois = deduplicate_by_name(all_pois)
            valid_pois = _filter_and_validate(all_pois, origin_name, dest_name, origin_coords, dest_coords)
            _progress("   →", f"补搜后共 {len(valid_pois)} 个有效 POI")
            new_path = _run_route_engine(origin_coords, valid_pois, dest_coords, num_stops,
                                         time_budget_hours=intent_result.time_budget_hours)
            if new_path:
                path_result = new_path
                context.path_segments = path_result["segments"]
                context.total_duration_min = path_result["total_duration_min"]
                context.total_distance_m = path_result["total_distance"]
                narration = run_narrator(context)
        review_loops += 1
    else:
        if review_result:
            _progress("⚠️", f"已达最大审核轮数 ({MAX_REVIEW_LOOPS})，使用当前方案")

    # Phase 4: OUTPUT
    _progress("✅", "路线规划完成")

    # 保存 session 状态
    session.all_pois = all_pois
    session.start_name = origin_name or "起点"
    session.dest_name = dest_name
    session.path_result = path_result
    session.stop_names = stop_names
    session._origin_coords = origin_coords
    session._dest_coords = dest_coords
    session._num_stops = num_stops
    session._keywords = keywords
    session._last_user_input = user_input

    # Mermaid
    if path_result and stop_names:
        mermaid = _build_mermaid_from_path(session.start_name, path_result, stop_names)
        if mermaid:
            narration += f"\n\n---\n\n```mermaid\n{mermaid}\n```"
            md_path = Path(__file__).parent.parent.parent / "data" / "output" / "route_output.md"
            md_path.write_text(f"```mermaid\n{mermaid}\n```", encoding="utf-8")
            _progress("🗺️", "路线图已保存")

    # HTML
    html = _build_route_html(
        stop_names, all_pois, session.distance_info, city,
        user_input, session.start_name,
        start_coords=origin_coords, dest_name=dest_name, dest_coords=dest_coords,
    )
    if html:
        html_path = Path(__file__).parent.parent.parent / "data" / "output" / "route_output.html"
        html_path.write_text(html, encoding="utf-8")
        _progress("🗺️", "交互地图已保存")

    # ── 保存用户画像 ────────────────────────────────
    # 收藏
    if origin_name:
        profile_mgr.add_to_favorites("origins", origin_name)
    if dest_name:
        profile_mgr.add_to_favorites("destinations", dest_name)
    for s in stop_names:
        profile_mgr.add_to_favorites("pois", s)
    for kw in (keywords.split(",") if isinstance(keywords, str) else keywords):
        profile_mgr.add_to_favorites("keywords", kw.strip())

    # 历史
    profile_mgr.add_history({
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "user_input": user_input,
        "city": city,
        "origin": origin_name or "未指定",
        "destination": dest_name or "由路线决定",
        "stops": stop_names,
        "duration_min": path_result["total_duration_min"] if path_result else 0,
        "review_score": review_result.overall_score if review_result else 0,
    })

    # Session 持久化
    profile_mgr.save_session({
        "city": city,
        "origin": origin_name,
        "origin_coords": list(origin_coords) if origin_coords else None,
        "destination": dest_name,
        "dest_coords": list(dest_coords) if dest_coords else None,
        "last_stops": stop_names,
        "num_stops": num_stops,
        "keywords": keywords,
        "last_user_input": user_input,
    })

    return narration, session
