"""Orchestrator — 多智能体协作主控，管理 Plan-Execute-Review-Refine 流程"""
import json
import re
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from agent.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from agent.models import UserIntent

# 复用现有工具层（不变）
from agent.tools.poi import search_poi, search_around, search_along_route, geocode, robust_geocode
from agent.tools.graph_planner import build_graph, shortest_path, _haversine

# 复用现有 core.py 的部分函数
from agent.core import (
    _extract_city, _infer_city_from_geocode, _progress,
    _build_mermaid_from_path, _build_route_html, _call_api,
    AgentSession,
)

# 地名正则（从 core.py run_agent 中提取，用于 geocode 回退匹配）
_PLACE_RE = re.compile(
    r"[一-鿿]{2,6}(?:地铁站|轻轨站|高铁站|火车站|汽车站|公交站|"
    r"路|街|巷|道|里|胡同|园|公园|广场|大厦|商场|购物中心|门|楼|塔|"
    r"景区|博物馆|图书馆|医院|学校|大学|学院|机场|码头)"
)

# 多智能体
from agent.multi_agent.intent_agent import run_intent_agent
from agent.multi_agent.poi_strategy_agent import build_search_strategy, evaluate_pois, get_research_adjustments
from agent.multi_agent.narrator_agent import run_narrator, NarrationContext
from agent.multi_agent.reviewer_agent import run_reviewer

MAX_REVIEW_LOOPS = 2


def _execute_search(strategy_regions: list, city: str,
                    origin_coords=None, dest_coords=None) -> list:
    """执行搜索策略，返回 POI 列表"""
    all_pois = []
    import json as _json

    # 关键词规范化映射（从原 core.py 迁移）
    _KW_NORMALIZE = {
        "吃": "美食", "好吃的": "美食", "喝": "咖啡,茶饮", "玩": "景点,公园",
        "逛": "商场,购物", "宵夜": "小吃,烧烤,火锅", "深夜": "小吃,烧烤,火锅",
        "打卡": "景点,网红", "拍照": "景点,网红",
    }

    for region in strategy_regions:
        # 规范化关键词
        normalized_kws = []
        for k in region.keywords:
            if k in _KW_NORMALIZE:
                normalized_kws.extend(_KW_NORMALIZE[k].split(","))
            else:
                found = False
                for key, val in _KW_NORMALIZE.items():
                    if key in k:
                        normalized_kws.extend(val.split(","))
                        found = True
                        break
                if not found:
                    normalized_kws.append(k)
        normalized_kws = list(dict.fromkeys(normalized_kws))

        # 确定搜索方式
        use_around = False
        loc_str = ""
        try:
            gc = _json.loads(geocode(region.center, city))
            if "lng" in gc and "lat" in gc:
                loc_str = f"{gc['lng']},{gc['lat']}"
                use_around = True
        except Exception:
            pass

        for kw in normalized_kws[:3]:  # 每个区域最多 3 个关键词
            try:
                if use_around:
                    r = search_around(loc_str, kw, radius=min(region.radius, 5000), limit=10)
                else:
                    r = search_poi(keywords=kw, location=city, limit=10)
            except Exception:
                r = search_poi(keywords=kw, location=city, limit=10)

            pois = _json.loads(r)
            if isinstance(pois, list):
                _progress("   →", f"「{region.center}」搜到 {len(pois)} 个「{kw}」")
                all_pois.extend(pois)
            time.sleep(0.05)

    # 沿途搜索（如果有起终点坐标）
    if origin_coords and dest_coords:
        o_str = f"{origin_coords[1]},{origin_coords[0]}"
        d_str = f"{dest_coords[1]},{dest_coords[0]}"
        for region in strategy_regions:
            for kw in region.keywords[:2]:
                try:
                    r = search_along_route(o_str, d_str, kw, radius=2000, limit=10)
                    pois = _json.loads(r)
                    if isinstance(pois, list):
                        all_pois.extend(pois)
                except Exception:
                    pass
                time.sleep(0.05)
    elif origin_coords:
        o_str = f"{origin_coords[1]},{origin_coords[0]}"
        for region in strategy_regions:
            for kw in region.keywords[:2]:
                try:
                    r = search_around(o_str, kw, radius=5000, limit=10)
                    pois = _json.loads(r)
                    if isinstance(pois, list):
                        all_pois.extend(pois)
                except Exception:
                    pass
                time.sleep(0.05)
    elif dest_coords:
        d_str = f"{dest_coords[1]},{dest_coords[0]}"
        for region in strategy_regions:
            for kw in region.keywords[:2]:
                try:
                    r = search_around(d_str, kw, radius=5000, limit=10)
                    pois = _json.loads(r)
                    if isinstance(pois, list):
                        all_pois.extend(pois)
                except Exception:
                    pass
                time.sleep(0.05)

    # 去重
    seen = set()
    unique = []
    for p in all_pois:
        n = p.get("name", "")
        if n and n not in seen:
            seen.add(n)
            unique.append(p)
    return unique


def _filter_pois(all_pois: list, origin_name: str, dest_name: str,
                 origin_coords=None, dest_coords=None) -> list:
    """过滤 POI：黑名单 + 去重 + 距起终点过近"""
    _BANNED = ["打印", "复印", "图文", "广告", "快印", "印刷", "维修", "洗车", "药店", "中介"]
    filtered = []
    for p in all_pois:
        cat = p.get("category", "") + p.get("type", "")
        if any(b in cat for b in _BANNED):
            continue
        if p.get("lat") is None or p.get("lng") is None:
            continue
        filtered.append(p)

    # 过滤距起点过近
    if origin_coords:
        f2 = []
        for p in filtered:
            d = _haversine(origin_coords[0], origin_coords[1], p["lat"], p["lng"])
            if d >= 100 and origin_name not in p.get("name", ""):
                f2.append(p)
        filtered = f2
    # 过滤距终点过近
    if dest_coords and dest_name:
        f2 = []
        for p in filtered:
            d = _haversine(dest_coords[0], dest_coords[1], p["lat"], p["lng"])
            if d >= 100 and dest_name not in p.get("name", ""):
                f2.append(p)
        filtered = f2

    return filtered


def _run_route_engine(origin_coords, valid_pois: list, dest_coords, num_stops: int) -> dict:
    """运行算法引擎：建图 + 最短路径"""
    if origin_coords and valid_pois:
        nodes, graph = build_graph(origin_coords, valid_pois, dest_coords)
        return shortest_path(graph, nodes, num_stops)
    elif dest_coords and valid_pois:
        nodes, graph = build_graph(dest_coords, valid_pois, dest_coords)
        return shortest_path(graph, nodes, num_stops)
    return None


def run_multi_agent(user_input: str, session: AgentSession = None) -> tuple:
    """多智能体协同路线规划入口 — 返回 (回复文本, AgentSession)"""
    is_new = session is None or not session.city

    if is_new:
        if session is None:
            session = AgentSession()

        # ── Phase 1: PLAN ──────────────────────────────
        _progress("🤖", "多智能体协同规划启动")

        # 1a. 提取城市
        city = _extract_city(user_input, session.default_city)
        intent_result = None
        if not city:
            intent_result = run_intent_agent(user_input, "")
            origin_pre = intent_result.origin
            city = _infer_city_from_geocode(origin_pre)
        if not city:
            return "请问您在哪个城市？我需要先知道城市才能为你查找地点和规划路线。", session
        session.city = city
        _progress("📍", f"城市：{city}")

        # 1b. Intent Agent：深度意图理解
        _progress("🧠", "Intent Agent 深度解析出行意图")
        if intent_result is None:
            intent_result = run_intent_agent(user_input, city)
        _progress("   →", f"起点：{intent_result.origin or '未指定'}")
        _progress("   →", f"终点：{intent_result.destination or '由路线决定'}")
        _progress("   →", f"群体：{intent_result.user_profile.group_type}，"
                  f"体力：{intent_result.user_profile.energy_level}")
        if intent_result.preference_reasoning:
            _progress("   →", f"推理：{intent_result.preference_reasoning}")

        # 1c. 地理编码（复用 robust_geocode 多层兜底）
        _progress("📍", "解析起终点坐标")
        origin_name = intent_result.origin
        dest_name = intent_result.destination or ""
        origin_coords = dest_coords = None

        if origin_name:
            lat, lng = robust_geocode(origin_name, city)
            if lat is not None:
                origin_coords = (lat, lng)
                _progress("   →", f"起点坐标：{lng},{lat}")
            else:
                matches = _PLACE_RE.findall(user_input)
                for m in matches:
                    lat, lng = robust_geocode(m, city)
                    if lat is not None:
                        origin_name = m
                        origin_coords = (lat, lng)
                        break
                if origin_coords is None:
                    _progress("⚠️", "起点未能解析")

        if dest_name:
            lat, lng = robust_geocode(dest_name, city)
            if lat is not None:
                dest_coords = (lat, lng)
                _progress("   →", f"终点坐标：{lng},{lat}")
            else:
                matches = _PLACE_RE.findall(user_input)
                for m in matches:
                    if m == origin_name:
                        continue
                    lat, lng = robust_geocode(m, city)
                    if lat is not None:
                        dest_name = m
                        dest_coords = (lat, lng)
                        break
                if dest_coords is None:
                    _progress("⚠️", "终点未能解析")

        # 1d. POI Strategy Agent：制定搜索策略 + 执行
        _progress("🎯", "POI Strategy Agent 制定搜索策略")
        strategy = build_search_strategy(intent_result, origin_coords, dest_coords)
        for r in strategy.regions:
            _progress("   →", f"{r.center} | {', '.join(r.keywords)} | {r.radius}m | {r.reason}")

        _progress("🔍", "执行 POI 搜索")
        all_pois = _execute_search(strategy.regions, city, origin_coords, dest_coords)

        # 兜底搜索
        if len(all_pois) < 3:
            _progress("⚠️", "搜索结果不足，全城兜底搜索")
            for kw in strategy.fallback_keywords:
                r = json.loads(search_poi(keywords=kw, location=city, limit=10))
                if isinstance(r, list):
                    all_pois.extend(r)

        # 过滤
        valid_pois = _filter_pois(all_pois, origin_name, dest_name, origin_coords, dest_coords)
        _progress("✅", f"共获取 {len(all_pois)} 个 POI，{len(valid_pois)} 个含坐标可建图")

        # 1e. 评估 POI 质量
        quality = evaluate_pois(valid_pois, intent_result)
        _progress("📊", f"POI 质量评估：{quality.summary}")
        if quality.match_score < 3:
            _progress("   →", f"匹配度偏低 ({quality.match_score})，但仍继续规划")

        # 1f. 运行路线算法引擎
        _progress("🗺️", "Route Engine 构建路线图")
        num_stops = min(intent_result.num_stops, 5)
        path_result = _run_route_engine(origin_coords, valid_pois, dest_coords, num_stops)

        if path_result is None:
            _progress("⚠️", "坐标不足，跳过图规划")
            # 降级：直接用 LLM 生成
            narration = _call_api(
                messages=[{
                    "role": "user",
                    "content": f"城市：{city}\n需求：{user_input}\n请规划路线。"
                }],
                system="你是一个路线规划助手。",
                max_tokens=1500,
            )
            try:
                return narration["content"][0]["text"], session
            except Exception:
                return "抱歉，路线暂时无法生成，请提供更多信息（如具体的起点或区域）。", session

        # ── Phase 2: NARRATE ───────────────────────────
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

        # ── Phase 3: REVIEW & REFINE ───────────────────
        review_loops = 0
        review_result = None

        while review_loops < MAX_REVIEW_LOOPS:
            _progress("🔍", f"Reviewer Agent 审核路线（第{review_loops + 1}轮）")
            review_result = run_reviewer(
                start_name=origin_name or "起点",
                dest_name=dest_name,
                city=city,
                user_input=user_input,
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
            if review_result.retry_suggestions:
                _progress("   →", f"建议：{review_result.retry_suggestions}")

            # 调整策略
            adjusted = get_research_adjustments(review_result.retry_suggestions, intent_result)
            if adjusted.regions:
                new_pois = _execute_search(adjusted.regions, city, origin_coords, dest_coords)
                all_pois.extend(new_pois)
                valid_pois = _filter_pois(all_pois, origin_name, dest_name, origin_coords, dest_coords)
                _progress("   →", f"补搜后共 {len(valid_pois)} 个有效 POI")

                new_path = _run_route_engine(origin_coords, valid_pois, dest_coords, num_stops)
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

        # ── Phase 4: OUTPUT ─────────────────────────────
        _progress("✅", "路线规划完成")

        # 保存 session 状态
        session.all_pois = all_pois
        session.start_name = origin_name or "起点"
        session.dest_name = dest_name
        session.path_result = path_result
        session.stop_names = [
            s["to"] for s in path_result["segments"]
            if s["to"] not in (origin_name, dest_name, "终点")
        ] if path_result else []

        # Mermaid 图
        mermaid = ""
        if path_result and session.stop_names:
            mermaid = _build_mermaid_from_path(session.start_name, path_result, session.stop_names)
            if mermaid:
                narration += f"\n\n---\n\n```mermaid\n{mermaid}\n```"
                md_path = Path(__file__).parent.parent.parent / "route_output.md"
                md_path.write_text(f"```mermaid\n{mermaid}\n```", encoding="utf-8")
                _progress("🗺️", f"路线图已保存")

        # HTML 地图
        html = _build_route_html(
            session.stop_names, session.all_pois, session.distance_info, city,
            user_input, session.start_name,
            start_coords=origin_coords,
            dest_name=session.dest_name,
            dest_coords=dest_coords,
        )
        if html:
            html_path = Path(__file__).parent.parent.parent / "route_output.html"
            html_path.write_text(html, encoding="utf-8")
            _progress("🗺️", f"交互地图已保存")

        response = narration

    else:
        # 多轮对话（后续实现）
        city = session.city
        response = "多轮对话功能尚未在多智能体模式中实现。"

    return response, session
