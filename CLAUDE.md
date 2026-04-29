# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

美团 AI 黑客松项目 —— "现在就出发：AI 本地路线智能规划"。用户用自然语言描述出行需求，Agent 自动规划一条结合 POI 数据、评分和偏好的本地路线，输出 LLM 解说 + Mermaid 路线图 + Leaflet 交互地图。

完整上下文见 `CONTEXT.md`（2070 行 Python，含架构决策、踩坑记录、待办事项）。

## Quick reference

```bash
# Install deps
pip install -r requirements.txt

# Run the agent (CLI)
python main.py

# Run a single route from command line
python3 -c "
import sys; sys.path.insert(0, '.')
from agent.core import run_agent, AgentSession
s = AgentSession(); s.default_city = '西安'
result, s = run_agent('从丈八六路地铁站出发到浐灞玩', s)
print(result)
"

# 5 画像深度体验测试（调 LLM + 高德 API，约 3-5 分钟）
python3 test_ux_deep.py

# 3 画像快速回归测试
python3 test_user_scenarios.py

# 单条路线集成测试
python3 test_graph.py

# API 连通性测试
python3 test_longcat.py    # LLM API
python3 test_amap.py       # 高德 API
python3 test_routing.py    # 高德步行 API

# View output files
cat route_output.md                     # Mermaid 路线图
python3 -m http.server 8000             # 浏览器访问 route_output.html
```

Config via `.env` (copy from `.env.example`): `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`, `AMAP_API_KEY`.

## Architecture

### Entry point

`main.py` — CLI loop, creates `AgentSession`, calls `run_agent()`.

### Core pipeline (`agent/core.py`, 1075 lines)

`run_agent()` is a 6-step pipeline:

1. **`_extract_city()`** — regex match against 300+ city list + `XX市` fallback
2. **`_parse_intent()`** — LLM extracts `{origin, destination, keywords, num_stops}` with post-parse validation (rejects placeholder values)
3. **`robust_geocode()`** — 4-layer fallback: geocode → input_tips (substring match) → +"地铁站" suffix → city+name prefix → regex fallback from raw input
4. **`_search_corridor_pois()`** — keyword normalization + `search_along_route` (4 sample points) + `search_around` (endpoint) + category blacklist filter
5. **`build_graph()` + `shortest_path()`** — fully-connected graph with walking API edges → projection-onto-route-line segment selection → 200m minimum spacing
6. LLM narration + Mermaid + Leaflet HTML output → writes `route_output.md` and `route_output.html`

### Key modules

| File | Purpose |
|------|---------|
| `agent/config.py` | Reads `.env`, exports API keys |
| `agent/models.py` | Pydantic models: `POI`, `RouteStop`, `Route`, `UserIntent` |
| `agent/tools/poi.py` | Amap API wrapper: `search_poi`, `search_around`, `search_along_route`, `geocode`, `robust_geocode` (4-layer fallback), `input_tips` |
| `agent/tools/graph_planner.py` | Graph algorithm: `build_graph` (fully-connected with ThreadPool), `shortest_path` (projection-based segment selection) |
| `agent/tools/routing.py` | Amap walking direction API → `{distance, duration}` |
| `agent/tools/reviews.py` | Amap POI detail lookup (skeleton — Dianping not yet integrated) |

### Design decisions (why, not what)

- **Algorithm picks routes, LLM narrates**: LLM has no spatial awareness. Routing decisions are algorithmic (projection-based segment selection); LLM only parses intent and generates natural language narration.
- **Projection-based segment selection, not Dijkstra/TSP**: POIs concentrate near the origin. Any greedy nearest-neighbor approach traps locally. POIs are projected onto the origin→destination line, then the line is divided into equal segments, picking the highest-rated POI per segment.
- **Walking API × speed factor for all transport modes**: Amap biking API has low coverage. All edges use walking API distance, then scale duration by `{步行:1×, 骑行:3×, 公交:2×, 打车:5×}`.

## Critical conventions

### Python falsy — use `is not None`, never truthiness

Highcharts API returns `location="0,0"` for POIs without coordinates. `float("0") = 0.0` is falsy. Always check `if x is not None` for lat/lng/rating fields. Use `None` as the sentinel for missing coordinates (not `0.0`).

### Substring matching for place names

Use continuous substring (`name in tip_name or tip_name in name`), not character overlap ratio. Character overlap causes cross-place false matches (e.g., "四路地铁站" matching "凤城四路" instead of "丈八四路").

### Integer division

Always use `round(x / 60)` for minute conversion, never `// 60` (floor division truncates — 30s → "0 minutes").

### `.env` variable names

Note the mismatch: `.env.example` uses `OPENAI_API_KEY`/`OPENAI_MODEL`, but `config.py` reads `LLM_API_KEY`/`LLM_MODEL`. The actual `.env` uses the `LLM_*` prefix.

### `index.js` is unrelated

The Express server in `index.js` is a separate experiment (hot-reload test). The main project is Python-only.

### Tests are integration tests

All tests call live APIs (LLM + Amap). There are no unit tests with mocks. Running tests consumes API quota.
