"""输出管线 — 解说 + 审核 + Refine + Mermaid/HTML 生成 + 画像保存."""
import time
from pathlib import Path

from app.llm_client import call_llm
from app.user_profile import UserProfileManager

from app.core.narrator_agent import run_narrator, NarrationContext
from app.core.reviewer_agent import run_reviewer
from app.core.poi_strategy_agent import get_research_adjustments

from app.config import USE_POI_DB
from app.algorithms.poi_filter import deduplicate_by_name

from app.shared.utils import _progress, _build_mermaid_from_path, _build_route_html, AgentSession

from .poi_pipeline import recommend_pois_from_db, execute_poi_search, filter_and_validate
from .route_pipeline import run_route_engine

_PROJECT_ROOT = Path(__file__).parent.parent.parent
MAX_REVIEW_LOOPS = 2


def narrate_review_output(
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
        if USE_POI_DB:
            new_pois = recommend_pois_from_db(origin_coords, dest_coords, intent_result, city)
            all_pois.extend(new_pois)
            all_pois = deduplicate_by_name(all_pois)
            valid_pois = filter_and_validate(all_pois, origin_name, dest_name, origin_coords, dest_coords)
            _progress("   →", f"补搜后共 {len(valid_pois)} 个有效 POI")
        else:
            adjusted = get_research_adjustments(review_result.retry_suggestions, intent_result)
            if adjusted.regions:
                new_pois = execute_poi_search(adjusted.regions, city, origin_coords, dest_coords)
                all_pois.extend(new_pois)
                all_pois = deduplicate_by_name(all_pois)
                valid_pois = filter_and_validate(all_pois, origin_name, dest_name, origin_coords, dest_coords)
                _progress("   →", f"补搜后共 {len(valid_pois)} 个有效 POI")
        new_path = run_route_engine(origin_coords, valid_pois, dest_coords, num_stops,
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
    session.origin_coords = origin_coords
    session.dest_coords = dest_coords
    session.num_stops = num_stops
    session.keywords = keywords
    session.last_user_input = user_input
    session.review_score = review_result.overall_score if review_result else 0

    # Mermaid
    if path_result and stop_names:
        mermaid = _build_mermaid_from_path(session.start_name, path_result, stop_names)
        if mermaid:
            narration += f"\n\n---\n\n```mermaid\n{mermaid}\n```"
            md_path = _PROJECT_ROOT / "data" / "output" / "route_output.md"
            md_path.write_text(f"```mermaid\n{mermaid}\n```", encoding="utf-8")
            _progress("🗺️", "路线图已保存")

    # HTML
    html = _build_route_html(
        stop_names, all_pois, session.distance_info, city,
        user_input, session.start_name,
        start_coords=origin_coords, dest_name=dest_name, dest_coords=dest_coords,
    )
    if html:
        html_path = _PROJECT_ROOT / "data" / "output" / "route_output.html"
        html_path.write_text(html, encoding="utf-8")
        _progress("🗺️", "交互地图已保存")

    # ── 保存用户画像 ────────────────────────────────
    if origin_name:
        profile_mgr.add_to_favorites("origins", origin_name)
    if dest_name:
        profile_mgr.add_to_favorites("destinations", dest_name)
    for s in stop_names:
        profile_mgr.add_to_favorites("pois", s)
    for kw in (keywords.split(",") if isinstance(keywords, str) else keywords):
        profile_mgr.add_to_favorites("keywords", kw.strip())

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
