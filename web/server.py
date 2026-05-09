"""FastAPI 网站服务 — 黑客松展示入口."""
import asyncio
import fcntl
import json
import os
import re
import sys
import time
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, field_validator

from app.auth import get_current_user_optional
from app.core.orchestrator import run_multi_agent
from app.shared import utils as shared_utils
from app.shared.utils import AgentSession, _build_mermaid_from_path
from app.algorithms.graph_planner import build_graph, shortest_path, pre_prune_pois
from app.algorithms.routing import get_route as routing_get_route, preview_connection
from app.models import (SelectPoiRequest, ConnectPoiRequest, ReorderRequest,
                        TransitQueryRequest)
from db.connection import get_conn

WEB_DIR = Path(__file__).parent
app = FastAPI(title="现在就出发")

app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))

# 注册认证路由
from web.routes.auth import router as auth_router
app.include_router(auth_router)

# ── 会话存储 — JSON 持久化 + TTL + 文件锁 ──

_SESSIONS_DIR = Path(__file__).parent.parent / "data" / "sessions"
_SESSION_TTL_SEC = int(os.getenv("SESSION_TTL_HOURS", "24")) * 3600
_SESSION_CLEANUP_INTERVAL = 1800  # 30 分钟

def _load_sessions() -> dict[str, AgentSession]:
    """从 JSON 文件恢复所有会话，跳过过期文件."""
    sessions = {}
    _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for f in _SESSIONS_DIR.glob("*.json"):
        # 跳过过期文件（启动时清理）
        if now - f.stat().st_mtime > _SESSION_TTL_SEC:
            try:
                f.unlink()
            except OSError:
                pass
            continue
        try:
            with open(f, "r", encoding="utf-8") as fh:
                sessions[f.stem] = AgentSession.from_dict(json.load(fh))
        except Exception:
            pass
    return sessions

def _save_session(session_id: str, session: AgentSession):
    """持久化单个会话到 JSON 文件（带文件锁防并发写）."""
    _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    filepath = _SESSIONS_DIR / f"{session_id}.json"
    with open(filepath, "w", encoding="utf-8") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            json.dump(session.to_dict(), f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)

def _cleanup_expired_sessions():
    """后台线程：定期清理过期 session."""
    while True:
        time.sleep(_SESSION_CLEANUP_INTERVAL)
        try:
            now = time.time()
            for f in _SESSIONS_DIR.glob("*.json"):
                if now - f.stat().st_mtime > _SESSION_TTL_SEC:
                    f.unlink()
        except Exception:
            pass

# 启动后台清理线程
_cleanup_thread = threading.Thread(target=_cleanup_expired_sessions, daemon=True)
_cleanup_thread.start()

sessions: dict[str, AgentSession] = _load_sessions()


# ── 请求校验异常处理 ───────────────────────

from fastapi.exceptions import RequestValidationError

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    errors = []
    for e in exc.errors():
        errors.append(f"{'.'.join(str(l) for l in e['loc'])}: {e['msg']}")
    return JSONResponse({"error": "; ".join(errors), "detail": errors}, status_code=422)


# ── 请求模型 ──────────────────────────────

class PlanRequest(BaseModel):
    query: str

    @field_validator("query")
    @classmethod
    def validate_query(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("查询不能为空")
        if len(v) > 500:
            raise ValueError("查询不能超过 500 个字符")
        return v

class ChatRequest(BaseModel):
    query: str
    session_id: str

    @field_validator("query")
    @classmethod
    def validate_query(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("查询不能为空")
        if len(v) > 500:
            raise ValueError("查询不能超过 500 个字符")
        return v

    @field_validator("session_id")
    @classmethod
    def validate_sid(cls, v: str) -> str:
        if not v or len(v) < 4 or not v.strip().isalnum():
            raise ValueError("会话 ID 无效")
        return v.strip()


# ── 响应构建 ──────────────────────────────

def _build_response(narration_text: str, session: AgentSession, session_id: str) -> dict:
    """从 orchestrator 返回值提取结构化响应."""
    # 分离解说文本和 Mermaid 代码块
    mermaid = ""
    narration = narration_text
    m = re.search(r"```mermaid\s*\n(.*?)\n```", narration_text, re.DOTALL)
    if m:
        mermaid = m.group(1).strip()
        narration = narration_text[:m.start()].strip()
        trailer = narration_text[m.end():].strip()
        if trailer:
            narration += "\n\n" + trailer

    # 构建 stops 列表
    stops = []
    path = session.path_result
    if path and path.get("segments"):
        stops.append({
            "name": session.start_name or "起点",
            "lat": None, "lng": None, "rating": None, "price": None, "address": "", "num": 0,
        })
        # 尝试填充起点坐标
        origin_coords = session.origin_coords
        if origin_coords:
            stops[0]["lat"] = origin_coords[0]
            stops[0]["lng"] = origin_coords[1]

        for i, seg in enumerate(path["segments"]):
            to_name = seg["to"]
            # 从 all_pois 查找坐标
            info = _find_poi(to_name, session.all_pois)
            lat = info.get("lat")
            lng = info.get("lng")
            # 终点坐标回退到 dest_coords
            dest_coords = session.dest_coords
            if lat is None and dest_coords and to_name == (session.dest_name or ""):
                lat, lng = dest_coords[0], dest_coords[1]
            stops.append({
                "name": to_name,
                "lat": lat, "lng": lng,
                "rating": info.get("rating"),
                "price": info.get("price_per_person"),
                "address": info.get("address", ""),
                "category": info.get("category", ""),
                "num": i + 1,
                "poi_id": info.get("poi_id", ""),
            })

    # 走廊数据（Phase 2 交互式编辑用）
    corridor = {}
    if session.corridor_pois or session.corridor_shape:
        corridor = {
            "corridor_pois": session.corridor_pois,
            "corridor_shape": session.corridor_shape,
            "cluster_markers": session.corridor_clusters,
        }

    return {
        "narration": narration,
        "mermaid": mermaid,
        "stops": stops,
        "segments": path.get("segments", []) if path else [],
        "session_id": session_id,
        "city": session.city,
        "total_duration_min": path["total_duration_min"] if path else 0,
        "total_distance_m": path.get("total_distance", 0) if path else 0,
        "review_score": session.review_score or None,
        "violations": getattr(session, "violations", []) or [],
        "dest_coords": list(session.dest_coords or ()) or None,
        **corridor,
    }


def _find_poi(name: str, pois: list) -> dict:
    """从 POI 列表中按名称查找."""
    if not pois:
        return {}
    for p in pois:
        if p.get("name") == name:
            return p
    for p in pois:
        if name in p.get("name", "") or p.get("name", "") in name:
            return p
    return {}


# 线程池（用于在后台线程跑同步 orchestrator）
_executor = ThreadPoolExecutor(max_workers=2)


# ── 页面 ──────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


# ── API ───────────────────────────────────

@app.get("/api/health")
async def health():
    status = {"status": "ok", "service": "现在就出发"}
    # DB 连通性
    try:
        from db.connection import get_conn
        with get_conn() as conn:
            conn.execute("SELECT 1")
        status["db"] = "ok"
    except Exception:
        status["db"] = "error"
    # Sessions 计数
    try:
        status["sessions"] = len(sessions)
    except Exception:
        pass
    return status


@app.post("/api/plan/stream")
async def api_plan_stream(req: PlanRequest, user: Optional[str] = Depends(get_current_user_optional)):
    """新建路线规划 — SSE 流式推送进度."""
    user_id = user or "default"
    session_id = uuid.uuid4().hex[:8]
    queue: asyncio.Queue = asyncio.Queue()

    def _on_progress(emoji: str, msg: str):
        """同步回调：把进度推入 asyncio 队列."""
        try:
            queue.put_nowait({"type": "progress", "emoji": emoji, "msg": msg})
        except asyncio.QueueFull:
            pass

    async def _event_generator():
        # 在后台线程运行 orchestrator
        loop = asyncio.get_event_loop()
        try:
            future = loop.run_in_executor(
                _executor,
                lambda: run_multi_agent(req.query, session=None, user_id=user_id,
                                        progress_callback=_on_progress),
            )
            # 边等 orchestrator 边推 SSE 进度事件
            while not future.done():
                try:
                    evt = await asyncio.wait_for(queue.get(), timeout=0.1)
                    yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    continue
            # 取最终结果
            result, session = future.result()
            sessions[session_id] = session
            _save_session(session_id, session)
            # 排空剩余进度事件
            while not queue.empty():
                try:
                    evt = queue.get_nowait()
                    yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
                except asyncio.QueueEmpty:
                    break
            # 推送最终结果
            final = _build_response(result, session, session_id)
            final["type"] = "result"
            yield f"data: {json.dumps(final, ensure_ascii=False)}\n\n"
        except Exception as e:
            err = {"type": "error", "error": str(e), "session_id": session_id}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/plan")
async def api_plan(req: PlanRequest, user: Optional[str] = Depends(get_current_user_optional)):
    """新建路线规划."""
    user_id = user or "default"
    session_id = uuid.uuid4().hex[:8]
    try:
        result, session = run_multi_agent(req.query, session=None, user_id=user_id)
        sessions[session_id] = session
        _save_session(session_id, session)
        return _build_response(result, session, session_id)
    except Exception as e:
        return JSONResponse({"error": str(e), "session_id": session_id}, status_code=500)


@app.post("/api/chat")
async def api_chat(req: ChatRequest, user: Optional[str] = Depends(get_current_user_optional)):
    """多轮对话修改已有路线."""
    user_id = user or "default"
    session = sessions.get(req.session_id)
    if session is None:
        return JSONResponse({"error": "会话不存在或已过期，请重新规划"}, status_code=404)
    try:
        result, session = run_multi_agent(req.query, session=session, user_id=user_id)
        sessions[req.session_id] = session
        _save_session(req.session_id, session)
        return _build_response(result, session, req.session_id)
    except Exception as e:
        return JSONResponse({"error": str(e), "session_id": req.session_id}, status_code=500)


# ── 用户画像 API ──────────────────────────

@app.get("/api/profile")
async def get_profile(user: Optional[str] = Depends(get_current_user_optional)):
    from app.user_profile import UserProfileManager
    mgr = UserProfileManager(user_id=user or "default")
    data = mgr.load()
    return {
        "username": user or "default",
        "profile": data.get("profile", {}),
        "favorites": data.get("favorites", {}),
        "history": data.get("history", [])[-10:],
    }

class ProfileUpdate(BaseModel):
    interests: list[str] = []
    notes: str = ""

@app.put("/api/profile")
async def update_profile(req: ProfileUpdate, user: Optional[str] = Depends(get_current_user_optional)):
    from app.user_profile import UserProfileManager
    from app.core.types import UserProfile
    mgr = UserProfileManager(user_id=user or "default")
    profile = UserProfile(interests=req.interests, notes=req.notes)
    mgr.update_profile(profile)
    return {"ok": True}


# ── 多方案对比 API ────────────────────────

@app.post("/api/plan/alternatives")
async def api_plan_alternatives(req: PlanRequest, user: Optional[str] = Depends(get_current_user_optional)):
    """生成 2-3 条路线供对比."""
    user_id = user or "default"
    session_id = uuid.uuid4().hex[:8]
    try:
        from app.core.route_agent import run_unified_agent
        # 跑第一条路线
        result, session = run_unified_agent(req.query, session=None, user_id=user_id)
        primary = _build_response(result, session, session_id)
        alternatives = [primary]

        # 如果第一轮有足够 cluster，生成备选
        last_clusters = getattr(session, "last_clusters_hint", [])
        if len(last_clusters) >= 5:
            # 备选1：取不同的 cluster 组合
            alt1_ids = last_clusters[2:5] if len(last_clusters) >= 5 else last_clusters
            from app.pipeline.cluster_tools import tool_build_route
            oc = session.origin_coords
            dc = session.dest_coords
            if oc:
                alt_result = tool_build_route(alt1_ids[:3], min(3, len(alt1_ids)),
                                              origin_coords=oc, dest_coords=dc,
                                              dest_name=session.dest_name or "")
                if alt_result.get("success") and alt_result.get("stops"):
                    alt_session = shared_utils.AgentSession()
                    alt_session.origin_coords = oc
                    alt_session.dest_coords = dc
                    alt_session.city = session.city
                    alt_session.all_pois = [{"name": s["name"], "category": s.get("category",""),
                                             "rating": s.get("rating"), "price_per_person": s.get("price_per_person"),
                                             "address": s.get("address",""), "lat": s.get("lat"), "lng": s.get("lng")}
                                            for s in alt_result["stops"]]
                    alt_session.path_result = {"segments": [], "total_duration_min": alt_result.get("total_duration_min", 0),
                                               "total_distance": alt_result.get("total_distance_m", 0)}
                    alt_session.stop_names = [s["name"] for s in alt_result["stops"]]
                    alt_narration = f"备选路线 ({len(alt_session.stop_names)}站, {alt_result.get('total_duration_min',0)}分钟)"
                    alternatives.append(_build_response(alt_narration, alt_session, session_id + "_alt1"))

        sessions[session_id] = session
        _save_session(session_id, session)
        return {"session_id": session_id, "alternatives": alternatives}
    except Exception as e:
        return JSONResponse({"error": str(e), "session_id": session_id}, status_code=500)


# ── 个性化推荐 API ────────────────────────

@app.get("/api/profile/suggestions")
async def get_suggestions(user: Optional[str] = Depends(get_current_user_optional)):
    """基于用户画像返回快捷推荐."""
    from app.user_profile import UserProfileManager
    mgr = UserProfileManager(user_id=user or "default")
    data = mgr.load()
    learned = data.get("learned", {})
    history = data.get("history", [])

    suggestions = []

    # 从历史生成"再来一次"
    if history:
        last = history[-1]
        if last.get("query"):
            suggestions.append({
                "type": "repeat",
                "label": f"再来一次：{last['query'][:30]}",
                "query": last["query"],
            })

    # 从偏好品类推荐
    cats = learned.get("preferred_cats", {})
    if cats:
        top = sorted(cats.items(), key=lambda x: x[1], reverse=True)[:2]
        for cat, _ in top:
            suggestions.append({
                "type": "preference",
                "label": f"逛逛{cat}",
                "query": f"推荐{cat}好去处",
            })

    # 从常用区域推荐
    districts = learned.get("preferred_districts", {})
    if districts:
        top = max(districts, key=districts.get)
        suggestions.append({
            "type": "district",
            "label": f"去{top}转转",
            "query": f"{top}附近逛逛",
        })

    cities = learned.get("cities", {})
    city = max(cities, key=cities.get) if cities else "西安"

    return {"suggestions": suggestions[:5], "city": city}


# ── 路线分享 API ──────────────────────────

@app.get("/api/share/{share_id}")
async def get_share(share_id: str):
    import json as _json
    share_file = Path(__file__).parent.parent / "data" / "shares" / f"{share_id}.json"
    if not share_file.exists():
        return JSONResponse({"error": "分享不存在或已过期"}, status_code=404)
    with open(share_file, "r") as f:
        return _json.load(f)

@app.post("/api/share")
async def create_share(req: PlanRequest, user: Optional[str] = Depends(get_current_user_optional)):
    import json as _json
    user_id = user or "default"
    share_id = uuid.uuid4().hex[:12]
    try:
        result, session = run_multi_agent(req.query, session=None, user_id=user_id)
        share_data = _build_response(result, session, share_id)
        share_data["user"] = user_id
        share_data["query"] = req.query

        share_dir = Path(__file__).parent.parent / "data" / "shares"
        share_dir.mkdir(parents=True, exist_ok=True)
        with open(share_dir / f"{share_id}.json", "w") as f:
            _json.dump(share_data, f, ensure_ascii=False)

        return {"share_id": share_id, "url": f"/share/{share_id}"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ═══════════════════════════════════════════════
# Phase 4: 交互式路线编辑 API
# ═══════════════════════════════════════════════

def _get_poi_by_id(poi_id: str) -> dict | None:
    """从 DB 加载单个 POI（ID 格式: cluster_id_rowid 或纯 rowid）."""
    if "_" in poi_id:
        row_id = poi_id.split("_", 1)[1]
    else:
        row_id = poi_id
    try:
        row_id = int(row_id)
    except (ValueError, TypeError):
        return None
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM pois WHERE id = ?", (row_id,)
            ).fetchone()
            if row:
                d = dict(row)
                d["poi_id"] = f"{d.get('cluster_id', 0)}_{d.get('id', 0)}"
                return d
    except Exception:
        pass
    return None


def _find_poi_in_corridor(poi_id: str, session: AgentSession) -> dict | None:
    """在 session.corridor_pois 中按 ID 查找 POI."""
    for p in session.corridor_pois:
        if p.get("id") == poi_id:
            return p
    return None


def _rebuild_route(session: AgentSession, num_stops: int = None) -> dict | None:
    """从 session 当前状态重建图 + 最短路径.

    排除 removed_poi_ids 中的 POI，优先使用 selected_poi_ids（强制入径）.
    """
    origin = session.origin_coords
    if not origin:
        return None

    dest = session.dest_coords
    if num_stops is None:
        num_stops = session.num_stops or 3

    # 收集当前 POI 集（排除已移除）
    removed_names = set()
    for rid in session.removed_poi_ids:
        p = _get_poi_by_id(rid)
        if p:
            removed_names.add(p["name"])

    pois = [p for p in session.all_pois if p.get("name") not in removed_names]

    # 将新选中的 POI 加入（如果不在列表中）
    for sid in session.selected_poi_ids:
        name_in_list = any(p.get("poi_id") == sid for p in pois)
        if not name_in_list:
            # 从 corridor 或 DB 获取
            p = _find_poi_in_corridor(sid, session) or _get_poi_by_id(sid)
            if p:
                p_copy = {
                    "name": p.get("name", ""),
                    "lat": p.get("lat"),
                    "lng": p.get("lng"),
                    "category": p.get("category", ""),
                    "rating": p.get("rating"),
                    "price_per_person": p.get("price_per_person"),
                    "address": p.get("address", ""),
                    "cluster_id": p.get("cluster_id", 0),
                    "poi_id": sid,
                }
                pois.append(p_copy)

    if not pois:
        return None

    # 预剪枝
    anchor_lat, anchor_lng = origin[0], origin[1]
    pois = pre_prune_pois(pois, max_pois=15, anchor_lat=anchor_lat, anchor_lng=anchor_lng)

    # 建图
    nodes, graph = build_graph(origin, pois, dest)

    # 选路径
    actual_stops = min(num_stops, len(pois))
    path = shortest_path(graph, nodes, actual_stops)

    if not path or not path.get("segments"):
        return None

    # 更新 session
    session.all_pois = pois
    session.nodes = nodes
    session.graph_data = {"nodes": nodes, "graph": graph}
    session.path_result = path

    return path


def _build_stop_list(session: AgentSession) -> list:
    """从 session 构建 stops 列表（同 _build_response 中的逻辑）."""
    stops = []
    path = session.path_result

    stops.append({
        "name": session.start_name or "起点",
        "lat": session.origin_coords[0] if session.origin_coords else None,
        "lng": session.origin_coords[1] if session.origin_coords else None,
        "rating": None, "price": None, "address": "", "num": 0, "poi_id": "",
    })

    if path and path.get("segments"):
        for i, seg in enumerate(path["segments"]):
            to_name = seg["to"]
            info = _find_poi(to_name, session.all_pois)
            lat = info.get("lat")
            lng = info.get("lng")
            dest_coords = session.dest_coords
            if lat is None and dest_coords and to_name == (session.dest_name or ""):
                lat, lng = dest_coords[0], dest_coords[1]
            stops.append({
                "name": to_name,
                "lat": lat, "lng": lng,
                "rating": info.get("rating"),
                "price": info.get("price_per_person"),
                "address": info.get("address", ""),
                "category": info.get("category", ""),
                "num": i + 1,
                "poi_id": info.get("poi_id", ""),
            })

    return stops


# ── GET /api/route/{session_id} ────────────────────

@app.get("/api/route/{session_id}")
async def get_route_detail(session_id: str):
    """返回会话完整上下文（交互编辑入口）."""
    session = sessions.get(session_id)
    if session is None:
        return JSONResponse({"error": "会话不存在或已过期"}, status_code=404)

    stops = _build_stop_list(session)
    path = session.path_result or {}

    return {
        "session_id": session_id,
        "city": session.city,
        "start_name": session.start_name,
        "dest_name": session.dest_name,
        "stops": stops,
        "segments": path.get("segments", []),
        "total_duration_min": path.get("total_duration_min", 0),
        "total_distance_m": path.get("total_distance", 0),
        "corridor_pois": session.corridor_pois,
        "corridor_shape": session.corridor_shape,
        "cluster_markers": session.corridor_clusters,
        "selected_poi_ids": session.selected_poi_ids,
        "removed_poi_ids": session.removed_poi_ids,
        "route_confirmed": session.route_confirmed,
        "keywords": session.keywords,
        "budget": session.budget,
        "num_stops": session.num_stops,
        "dest_coords": list(session.dest_coords or ()) or None,
    }


# ── POST /api/route/{session_id}/select-poi ─────────

@app.post("/api/route/{session_id}/select-poi")
async def select_poi(session_id: str, req: SelectPoiRequest):
    """用户选择一个走廊 POI 加入路线."""
    session = sessions.get(session_id)
    if session is None:
        return JSONResponse({"error": "会话不存在或已过期"}, status_code=404)

    poi_id = req.poi_id
    if poi_id not in session.selected_poi_ids:
        session.selected_poi_ids.append(poi_id)
    # 从 removed 列表移除（如果之前被删过）
    if poi_id in session.removed_poi_ids:
        session.removed_poi_ids.remove(poi_id)

    path = _rebuild_route(session)
    _save_session(session_id, session)

    if path is None:
        return JSONResponse({"error": "无法重建路线：当前 POI 集不足以构建有效路径"}, status_code=400)

    stops = _build_stop_list(session)
    return {
        "session_id": session_id,
        "stops": stops,
        "segments": path.get("segments", []),
        "total_duration_min": path.get("total_duration_min", 0),
        "total_distance_m": path.get("total_distance", 0),
        "selected_poi_ids": session.selected_poi_ids,
    }


# ── POST /api/route/{session_id}/remove-poi ─────────

@app.post("/api/route/{session_id}/remove-poi")
async def remove_poi(session_id: str, req: SelectPoiRequest):
    """从路线中移除一个 POI."""
    session = sessions.get(session_id)
    if session is None:
        return JSONResponse({"error": "会话不存在或已过期"}, status_code=404)

    poi_id = req.poi_id
    if poi_id in session.selected_poi_ids:
        session.selected_poi_ids.remove(poi_id)
    if poi_id not in session.removed_poi_ids:
        session.removed_poi_ids.append(poi_id)

    path = _rebuild_route(session)
    _save_session(session_id, session)

    if path is None:
        return JSONResponse({"error": "无法重建路线：当前 POI 集不足以构建有效路径"}, status_code=400)

    stops = _build_stop_list(session)
    return {
        "session_id": session_id,
        "stops": stops,
        "segments": path.get("segments", []),
        "total_duration_min": path.get("total_duration_min", 0),
        "total_distance_m": path.get("total_distance", 0),
        "removed_poi_ids": session.removed_poi_ids,
    }


# ── POST /api/route/{session_id}/connect ────────────

@app.post("/api/route/{session_id}/connect")
async def connect_pois(session_id: str, req: ConnectPoiRequest):
    """预览两个 POI 之间的交通连接."""
    session = sessions.get(session_id)
    if session is None:
        return JSONResponse({"error": "会话不存在或已过期"}, status_code=404)

    from_poi = (
        _find_poi_in_corridor(req.from_poi_id, session) or
        _get_poi_by_id(req.from_poi_id)
    )
    to_poi = (
        _find_poi_in_corridor(req.to_poi_id, session) or
        _get_poi_by_id(req.to_poi_id)
    )

    if not from_poi or not to_poi:
        return JSONResponse({"error": "POI 不存在"}, status_code=404)

    from_lat = from_poi.get("lat")
    from_lng = from_poi.get("lng")
    to_lat = to_poi.get("lat")
    to_lng = to_poi.get("lng")

    if None in (from_lat, from_lng, to_lat, to_lng):
        return JSONResponse({"error": "POI 坐标缺失"}, status_code=400)

    mode = req.mode if req.mode != "auto" else "auto"
    result = preview_connection(
        (from_lat, from_lng), (to_lat, to_lng),
        city=session.city or "西安"
    )
    if result is None:
        return JSONResponse({"error": "无法计算路线"}, status_code=500)

    return {
        "from_name": from_poi.get("name", ""),
        "to_name": to_poi.get("name", ""),
        "from_lat": from_lat, "from_lng": from_lng,
        "to_lat": to_lat, "to_lng": to_lng,
        **result,
    }


# ── POST /api/route/{session_id}/reorder ────────────

@app.post("/api/route/{session_id}/reorder")
async def reorder_stops(session_id: str, req: ReorderRequest):
    """重新排列站点顺序."""
    session = sessions.get(session_id)
    if session is None:
        return JSONResponse({"error": "会话不存在或已过期"}, status_code=404)

    # 更新选中的顺序
    session.selected_poi_ids = req.poi_ids

    # 重建路线（graph + shortest_path 按投影自动排序，同时优先选中 POI）
    path = _rebuild_route(session)
    _save_session(session_id, session)

    if path is None:
        return JSONResponse({"error": "无法重建路线：当前 POI 集不足以构建有效路径"}, status_code=400)

    stops = _build_stop_list(session)
    return {
        "session_id": session_id,
        "stops": stops,
        "segments": path.get("segments", []),
        "total_duration_min": path.get("total_duration_min", 0),
        "total_distance_m": path.get("total_distance", 0),
        "selected_poi_ids": session.selected_poi_ids,
    }


# ── POST /api/route/{session_id}/search-nearby ─────────

class SearchNearbyRequest(BaseModel):
    lat: float
    lng: float
    keywords: str = ""

@app.post("/api/route/{session_id}/search-nearby")
async def search_nearby(session_id: str, req: SearchNearbyRequest):
    """点击地图空白处，搜索附近 POI."""
    session = sessions.get(session_id)
    if session is None:
        return JSONResponse({"error": "会话不存在或已过期"}, status_code=404)

    from app.providers.amap_provider import search_around, reverse_geocode

    addr_info = reverse_geocode(req.lat, req.lng)
    location = f"{req.lng},{req.lat}"
    keywords = req.keywords or "美食|景点|购物|咖啡|公园"
    try:
        pois = search_around(location=location, keywords=keywords, radius=3000, limit=15)
    except Exception as e:
        return JSONResponse({"error": f"附近搜索失败: {e}"}, status_code=500)

    return {
        "session_id": session_id,
        "location": {"lat": req.lat, "lng": req.lng},
        "address": addr_info,
        "pois": pois[:15],
    }


# ── POST /api/route/{session_id}/add-custom ────────────

class AddCustomPoiRequest(BaseModel):
    lat: float
    lng: float
    name: str = ""

@app.post("/api/route/{session_id}/add-custom")
async def add_custom_poi(session_id: str, req: AddCustomPoiRequest):
    """地图点击或拖放后添加自定义 POI."""
    session = sessions.get(session_id)
    if session is None:
        return JSONResponse({"error": "会话不存在或已过期"}, status_code=404)

    from app.providers.amap_provider import reverse_geocode
    addr = reverse_geocode(req.lat, req.lng)
    name = req.name or addr.get("name", "自定义站点")

    custom_id = f"custom_{int(req.lat * 1e6)}_{int(req.lng * 1e6)}"
    poi_entry = {
        "name": name, "lat": req.lat, "lng": req.lng,
        "category": "自定义", "rating": None,
        "price_per_person": None, "address": addr.get("address", ""),
        "cluster_id": -3, "poi_id": custom_id,
    }
    session.all_pois.append(poi_entry)
    session.selected_poi_ids.append(custom_id)
    if custom_id in session.removed_poi_ids:
        session.removed_poi_ids.remove(custom_id)

    session.corridor_pois.append({
        "id": custom_id, "name": name, "lat": req.lat, "lng": req.lng,
        "category": "自定义", "rating": None, "price_per_person": None,
        "address": addr.get("address", ""), "cluster_id": -3,
        "projection_ratio": 0, "perpendicular_km": 0,
        "recommendation_reasons": {"structured": "手动添加", "user_need": "用户指定"},
        "selected": True,
    })

    path = _rebuild_route(session)
    _save_session(session_id, session)

    if path is None:
        return JSONResponse({"error": "无法重建路线：当前 POI 集不足以构建有效路径"}, status_code=400)

    stops = _build_stop_list(session)
    return {
        "session_id": session_id,
        "stops": stops,
        "segments": path.get("segments", []),
        "total_duration_min": path.get("total_duration_min", 0),
        "total_distance_m": path.get("total_distance", 0),
    }


# ── POST /api/route/{session_id}/confirm ────────────

@app.post("/api/route/{session_id}/confirm")
async def confirm_route(session_id: str, user: Optional[str] = Depends(get_current_user_optional)):
    """确认路线 — 生成详细解说 + Mermaid 图."""
    session = sessions.get(session_id)
    if session is None:
        return JSONResponse({"error": "会话不存在或已过期"}, status_code=404)

    if not session.path_result or not session.path_result.get("segments"):
        return JSONResponse({"error": "路线无有效站点"}, status_code=400)

    try:
        from app.core.narrator_agent import run_confirmation_narrator
        narration_data = run_confirmation_narrator(
            session,
            user_input=session.last_user_input or "",
        )
    except Exception:
        # LLM 调用失败时回退到服务端生成
        mermaid = _build_mermaid_from_path(
            session.start_name or "起点",
            session.path_result,
            session.stop_names,
        )
        narration_data = {
            "narration": f"路线已确认 ({session.path_result.get('total_duration_min', 0)} 分钟, "
                         f"{len(session.stop_names)} 站)",
            "mermaid": mermaid,
        }

    session.route_confirmed = True
    _save_session(session_id, session)

    # 从确认路线中学习用户偏好
    try:
        from app.user_profile import UserProfileManager
        mgr = UserProfileManager(user_id=user or "default")
        mgr.update_from_route(
            stops=session.all_pois,
            keywords=session.keywords,
            budget=session.budget,
            city=session.city,
        )
    except Exception:
        pass

    stops = _build_stop_list(session)
    path = session.path_result
    return {
        "session_id": session_id,
        "narration": narration_data["narration"],
        "mermaid": narration_data["mermaid"],
        "stops": stops,
        "segments": path.get("segments", []),
        "total_duration_min": path.get("total_duration_min", 0),
        "total_distance_m": path.get("total_distance", 0),
        "route_confirmed": True,
    }


# ── GET /api/route/{session_id}/transit ─────────────

@app.get("/api/route/{session_id}/transit")
async def query_transit(session_id: str,
                        from_lat: float, from_lng: float,
                        to_lat: float, to_lng: float,
                        mode: str = "auto"):
    """查询两点间交通路线（高频 hover 操作）."""
    session = sessions.get(session_id)
    if session is None:
        return JSONResponse({"error": "会话不存在或已过期"}, status_code=404)

    o_str = f"{from_lng},{from_lat}"
    d_str = f"{to_lng},{to_lat}"
    result = routing_get_route(o_str, d_str, mode=mode,
                               city=session.city or "西安")
    if result is None:
        return JSONResponse({"error": "无法查询路线"}, status_code=500)

    return {
        "from_lat": from_lat, "from_lng": from_lng,
        "to_lat": to_lat, "to_lng": to_lng,
        "mode": result["mode"],
        "distance": result["distance"],
        "duration": result["duration"],
        "cost": result["cost"],
        "steps": result["steps"],
    }
