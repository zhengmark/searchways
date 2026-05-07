"""FastAPI 网站服务 — 黑客松展示入口."""
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.core.orchestrator import run_multi_agent
from app.shared.utils import AgentSession

WEB_DIR = Path(__file__).parent
app = FastAPI(title="现在就出发")

app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))

# 会话存储（黑客松简化版，生产环境应换 Redis）
sessions: dict[str, AgentSession] = {}


# ── 请求模型 ──────────────────────────────

class PlanRequest(BaseModel):
    query: str

class ChatRequest(BaseModel):
    query: str
    session_id: str


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
        origin_coords = getattr(session, '_origin_coords', None)
        if origin_coords:
            stops[0]["lat"] = origin_coords[0]
            stops[0]["lng"] = origin_coords[1]

        for i, seg in enumerate(path["segments"]):
            to_name = seg["to"]
            # 从 all_pois 查找坐标
            info = _find_poi(to_name, session.all_pois)
            stops.append({
                "name": to_name,
                "lat": info.get("lat"), "lng": info.get("lng"),
                "rating": info.get("rating"),
                "price": info.get("price_per_person"),
                "address": info.get("address", ""),
                "category": info.get("category", ""),
                "num": i + 1,
            })

    return {
        "narration": narration,
        "mermaid": mermaid,
        "stops": stops,
        "session_id": session_id,
        "city": session.city,
        "total_duration_min": path["total_duration_min"] if path else 0,
        "total_distance_m": path.get("total_distance", 0) if path else 0,
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


# ── 页面 ──────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


# ── API ───────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "现在就出发"}


@app.post("/api/plan")
async def api_plan(req: PlanRequest):
    """新建路线规划."""
    session_id = uuid.uuid4().hex[:8]
    try:
        result, session = run_multi_agent(req.query, session=None, user_id="default")
        sessions[session_id] = session
        return _build_response(result, session, session_id)
    except Exception as e:
        return JSONResponse({"error": str(e), "session_id": session_id}, status_code=500)


@app.post("/api/chat")
async def api_chat(req: ChatRequest):
    """多轮对话修改已有路线."""
    session = sessions.get(req.session_id)
    if session is None:
        return JSONResponse({"error": "会话不存在或已过期，请重新规划"}, status_code=404)
    try:
        result, session = run_multi_agent(req.query, session=session, user_id="default")
        sessions[req.session_id] = session
        return _build_response(result, session, req.session_id)
    except Exception as e:
        return JSONResponse({"error": str(e), "session_id": req.session_id}, status_code=500)
