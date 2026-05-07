"""Orchestrator — 多智能体协同主控，支持多轮对话 + 用户画像持久化.

流程:
  第1轮: 完整 PLAN → EXECUTE → REVIEW → NARRATE → OUTPUT
  第N轮: DETECT MODIFICATION → 只重跑受影响环节 → NARRATE → OUTPUT
  每轮结束: 保存用户画像 + session 到 users/{user_id}.json
"""

import re

from app.llm_client import call_llm
from app.user_profile import UserProfileManager

from app.core.intent_agent import run_intent_agent
from app.core.poi_strategy_agent import (
    build_search_strategy, evaluate_pois, get_research_adjustments,
    SearchRegion, SearchStrategy,
)
from app.core.modifier_agent import detect_modification
from app.core.types import IntentResult, UserProfile

from app.config import USE_POI_DB
from app.providers.amap_provider import AmapAPIError
from app.providers.provider import search_poi
from app.algorithms.poi_filter import deduplicate_by_name

from app.shared.utils import (
    _extract_city, _infer_city_from_geocode, _progress, AgentSession,
)

from app.pipeline.poi_pipeline import execute_poi_search, recommend_pois_from_db, filter_and_validate
from app.pipeline.route_pipeline import geocode_place, run_route_engine
from app.pipeline.output_pipeline import narrate_review_output


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
        "origin_coords": session.origin_coords,
        "destination": session.dest_name,
        "dest_coords": session.dest_coords,
        "last_stops": session.stop_names,
        "num_stops": session.num_stops,
        "keywords": session.keywords,
        "last_user_input": session.last_user_input,
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
        if modification.change_type in ("new_route", "none"):
            is_new_session = True
            session = AgentSession()
            if user_data.get("profile", {}).get("city"):
                session.default_city = user_data["profile"].get("default_city", "")

    if is_new_session:
        return _run_full_plan(user_input, session, profile_mgr, user_data)

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

    if intent_result.preference_reasoning:
        profile_mgr.update_profile(intent_result.user_profile)

    # 3. 地理编码
    _progress("📍", "解析起终点坐标")
    origin_name = intent_result.origin or ""
    dest_name = intent_result.destination or ""
    origin_coords, origin_name = geocode_place(origin_name, city, user_input)
    dest_coords, dest_name = geocode_place(dest_name, city, user_input, skip_names=[origin_name])

    if origin_coords:
        _progress("   →", f"起点：{origin_coords[1]},{origin_coords[0]}")
    else:
        _progress("⚠️", "起点未能解析")
    if dest_coords:
        _progress("   →", f"终点：{dest_coords[1]},{dest_coords[0]}")
    elif dest_name:
        _progress("⚠️", "终点未能解析")

    # 4. POI 获取（DB 推荐引擎 或 高德 API 搜索）
    if USE_POI_DB:
        _progress("🎯", "推荐引擎（本地 DB）")
        all_pois = recommend_pois_from_db(origin_coords, dest_coords, intent_result, city)
        valid_pois = filter_and_validate(all_pois, origin_name, dest_name, origin_coords, dest_coords)
        _progress("✅", f"推荐引擎返回 {len(all_pois)} 个 POI，{len(valid_pois)} 个含坐标可建图")
    else:
        _progress("🎯", "POI Strategy Agent 制定搜索策略")
        strategy = build_search_strategy(intent_result, origin_coords, dest_coords)
        for r in strategy.regions:
            _progress("   →", f"{r.center} | {', '.join(r.keywords)} | {r.radius}m | {r.reason}")

        _progress("🔍", "执行 POI 搜索")
        all_pois = execute_poi_search(strategy.regions, city, origin_coords, dest_coords)

        if len(all_pois) < 3:
            _progress("⚠️", "搜索结果不足，全城兜底搜索")
            for kw in strategy.fallback_keywords:
                try:
                    pois = search_poi(keywords=kw, location=city, limit=10)
                    all_pois.extend(pois)
                except AmapAPIError:
                    pass
            all_pois = deduplicate_by_name(all_pois)

        valid_pois = filter_and_validate(all_pois, origin_name, dest_name, origin_coords, dest_coords)
        _progress("✅", f"共获取 {len(all_pois)} 个 POI，{len(valid_pois)} 个含坐标可建图")

        # POI 质量评估 → 不足时补搜
        quality = evaluate_pois(valid_pois, intent_result)
        _progress("📊", f"POI 质量评估：{quality.summary}")
        if quality.needs_research and quality.research_suggestions:
            _progress("🔄", "POI 质量不足，自动补搜")
            adjusted = get_research_adjustments(quality.research_suggestions, intent_result)
            if adjusted.regions:
                new_pois = execute_poi_search(adjusted.regions, city, origin_coords, dest_coords)
                all_pois.extend(new_pois)
                all_pois = deduplicate_by_name(all_pois)
                valid_pois = filter_and_validate(all_pois, origin_name, dest_name, origin_coords, dest_coords)
                _progress("✅", f"补搜后共 {len(valid_pois)} 个有效 POI")

    # 5. 路线算法
    _progress("🗺️", "Route Engine 构建路线图")
    num_stops = min(intent_result.num_stops, 5)
    stashed_origin_coords = origin_coords
    path_result = run_route_engine(origin_coords, valid_pois, dest_coords, num_stops,
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

    # 6. 解说 → 审核 → 输出
    return narrate_review_output(
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
                      state: dict, mod) -> tuple:
    """增量修改已有路线（只重跑受影响环节）."""
    _progress("🔄", f"检测到修改意图：{mod.change_type}（{mod.reasoning}）")
    city = state.get("city", "")
    origin_name = state.get("origin", "")
    dest_name = state.get("destination", "")
    keywords = state.get("keywords", "美食,景点")
    num_stops = state.get("num_stops", 3)
    origin_coords = state.get("origin_coords")
    dest_coords = state.get("dest_coords")

    session.city = city
    session.start_name = origin_name

    need_research = False
    need_regraph = False

    valid_pois = None
    all_pois = None

    change_type = mod.change_type
    params = mod.params

    if change_type == "change_origin":
        new_origin = params.get("origin", "")
        origin_coords, origin_name = geocode_place(new_origin, city, user_input)
        _progress("   →", f"起点更新为：{origin_name}")
        need_research = True
        need_regraph = True

    elif change_type == "change_destination":
        new_dest = params.get("destination", "")
        dest_coords, dest_name = geocode_place(new_dest, city, user_input, skip_names=[origin_name])
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
        updated_intent = run_intent_agent(user_input, city)
        profile_mgr.update_profile(updated_intent.user_profile)
        _progress("   →", f"偏好已更新：{updated_intent.preference_reasoning}")

    elif change_type == "add_poi":
        poi_name = params.get("poi_name", "")
        _progress("   →", f"将「{poi_name}」加入路线中途")
        num_stops = min(num_stops + 1, 8)
        poi_coords, poi_name = geocode_place(poi_name, city, user_input)
        if poi_coords:
            existing = [p for p in (valid_pois or session.all_pois or [])]
            existing.append({
                "name": poi_name,
                "lat": poi_coords[0], "lng": poi_coords[1],
                "rating": 4.5,
                "price_per_person": None,
                "category": "用户指定",
                "address": "",
            })
            valid_pois = existing
            all_pois = existing
            need_regraph = True
        else:
            _progress("⚠️", f"未能解析「{poi_name}」，将按关键词搜索")
            anchor_strategy = SearchStrategy(regions=[
                SearchRegion(center=poi_name, keywords=[poi_name], radius=2000, reason=f"用户指定「{poi_name}」")
            ])
            all_pois = execute_poi_search(anchor_strategy.regions, city, origin_coords, dest_coords)
            valid_pois = filter_and_validate(all_pois, origin_name, dest_name, origin_coords, dest_coords)
            _progress("   →", f"搜到 {len(valid_pois)} 个有效 POI")
            need_regraph = True

    elif change_type == "change_poi_location":
        anchor = params.get("anchor", "")
        _progress("   →", f"在「{anchor}」附近重新搜索")
        if USE_POI_DB:
            anchor_intent = run_intent_agent(
                f"在{anchor}附近找 {keywords}", city
            )
            all_pois = recommend_pois_from_db(origin_coords, dest_coords, anchor_intent, city)
        else:
            anchor_strategy = SearchStrategy(regions=[
                SearchRegion(center=anchor, keywords=keywords.split(","), radius=2000, reason=f"用户指定「{anchor}」附近")
            ])
            all_pois = execute_poi_search(anchor_strategy.regions, city, origin_coords, dest_coords)
        valid_pois = filter_and_validate(all_pois, origin_name, dest_name, origin_coords, dest_coords)
        _progress("   →", f"搜到 {len(valid_pois)} 个有效 POI")
        need_regraph = True

    elif change_type == "adjust_constraint":
        time_budget = params.get("time_budget_hours")
        _progress("   →", f"时间约束更新为：{time_budget} 小时")
        need_regraph = True

    # ── 执行受影响环节 ───────────────────────────────
    if need_research:
        _progress("🔍", "重新搜索 POI")
        if USE_POI_DB:
            temp_intent = run_intent_agent(
                f"{origin_name} → {dest_name}，搜索 {keywords}，{num_stops} 站", city
            )
            all_pois = recommend_pois_from_db(origin_coords, dest_coords, temp_intent, city)
            valid_pois = filter_and_validate(all_pois, origin_name, dest_name, origin_coords, dest_coords)
        else:
            temp_intent = run_intent_agent(
                f"{origin_name} → {dest_name}，搜索 {keywords}，{num_stops} 站", city
            )
            strategy = build_search_strategy(temp_intent, origin_coords, dest_coords)
            all_pois = execute_poi_search(strategy.regions, city, origin_coords, dest_coords)
            if len(all_pois) < 3:
                for kw in strategy.fallback_keywords:
                    try:
                        all_pois.extend(search_poi(keywords=kw, location=city, limit=10))
                    except AmapAPIError:
                        pass
                all_pois = deduplicate_by_name(all_pois)
            valid_pois = filter_and_validate(all_pois, origin_name, dest_name, origin_coords, dest_coords)
        _progress("✅", f"搜索到 {len(valid_pois)} 个有效 POI")

    if valid_pois is None:
        valid_pois = session.all_pois or []
    if all_pois is None:
        all_pois = valid_pois

    if need_regraph:
        _progress("🗺️", "重新构建路线")
        time_budget = mod.params.get("time_budget_hours") if change_type == "adjust_constraint" else None
        path_result = run_route_engine(origin_coords, valid_pois, dest_coords, num_stops,
                                        time_budget_hours=time_budget)
        if path_result is None:
            return "抱歉，调整后未能生成有效路线。请尝试换一个地点或放宽条件。", session
        stop_names = [
            s["to"] for s in path_result["segments"]
            if s["to"] not in (origin_name, dest_name, "终点")
        ]
    else:
        path_result = session.path_result
        stop_names = session.stop_names
        if path_result is None:
            return "当前没有可修改的路线。请先规划一条新路线。", session

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

    return narrate_review_output(
        user_input, session, profile_mgr, user_data,
        origin_name, dest_name, city,
        origin_coords=origin_coords, dest_coords=dest_coords,
        all_pois=all_pois or [],
        valid_pois=valid_pois or [],
        path_result=path_result, stop_names=stop_names,
        num_stops=num_stops, keywords=keywords,
        intent_result=simple_intent,
    )
