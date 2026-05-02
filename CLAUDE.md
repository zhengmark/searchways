# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

美团 AI 黑客松项目 —— "现在就出发：AI 本地路线智能规划"。用户用自然语言描述出行需求，Agent 自动规划一条结合 POI 数据、评分和偏好的本地路线，输出 LLM 解说 + Mermaid 路线图 + Leaflet 交互地图。

完整上下文见 `CONTEXT.md` 和 `ARCHITECTURE.md`。

## Quick reference

```bash
# Install deps
pip install -r requirements.txt

# Run the agent (CLI, multi-turn)
python main.py

# Run a single route (multi-agent pipeline)
python3 -c "
import sys; sys.path.insert(0, '.')
from agent.multi_agent.orchestrator import run_multi_agent
result, session = run_multi_agent('从丈八六路地铁站出发到浐灞玩', user_id='default')
print(result)
"

# Multi-turn example
python3 -c "
import sys; sys.path.insert(0, '.')
from agent.multi_agent.orchestrator import run_multi_agent
result, s = run_multi_agent('从丈八六路出发去钟楼吃', user_id='default')
result2, s = run_multi_agent('不去钟楼了，去大雁塔', session=s, user_id='default')
print(result2)
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

`main.py` — CLI loop, calls `run_multi_agent()` from `agent/multi_agent/orchestrator.py`.

### Multi-agent pipeline (`agent/multi_agent/orchestrator.py`)

`run_multi_agent()` orchestrates a **Plan-Execute-Review-Refine** loop:

1. **Intent Agent** (`intent_agent.py`) — LLM deep intent parsing: origin/destination/keywords/num_stops + UserProfile inference (group_type, energy_level, budget, interests) + time_budget_hours
2. **Geocode** — `robust_geocode()` 4-layer fallback for origin/destination coordinates
3. **POI Strategy Agent** (`poi_strategy_agent.py`) — generates search regions + keyword strategy → executes via Amap APIs → quality evaluation → auto re-search if insufficient
4. **Route Engine** — `build_graph()` fully-connected graph with walking API edges → `shortest_path()` projection-based segment selection → time budget constraint (auto-reduce stops if >20% over)
5. **Narrator Agent** (`narrator_agent.py`) — personalized natural language narration
6. **Reviewer Agent** (`reviewer_agent.py`) — quality audit (coverage, diversity, spacing, user fit, time budget) → up to 2 refine loops
7. **Output** — text reply + Mermaid diagram → `route_output.md` + Leaflet map → `route_output.html` + user profile persistence → `users/{user_id}.json`

### Multi-turn dialogue

- **Modifier Agent** (`modifier_agent.py`) — rule-based regex (8 change types) + LLM fallback to detect what the user wants to change
- **Partial re-planning** — only re-runs affected pipeline stages (e.g. change_destination → geocode + search + graph, change_preferences → update profile + re-narrate)
- **Session persistence** — `UserProfileManager.save_session()` stores current state for cross-restart resume
- **User profile** — `users/{user_id}.json` accumulates favorites, history, and inferred preferences

### Key modules

| File | Purpose |
|------|---------|
| `agent/config.py` | Reads `.env`, exports API keys |
| `agent/llm_client.py` | Shared LLM client (`call_llm()`) — single source of truth |
| `agent/models.py` | Pydantic models: `POI`, `RouteStop`, `Route`, `UserIntent` |
| `agent/core.py` | Public functions: `_extract_city`, `_build_mermaid_from_path`, `_build_route_html`, `AgentSession` |
| `agent/user_profile.py` | `UserProfileManager` — per-user JSON persistence, history compression (256KB), session save/load |
| `agent/multi_agent/types.py` | Agent communication: `IntentResult`, `UserProfile`, `SearchStrategy`, `PoiQualityReport`, `ReviewResult`, `NarrationContext` |
| `agent/multi_agent/orchestrator.py` | Main controller — Plan-Execute-Review-Refine + multi-turn dispatch + profile persistence |
| `agent/multi_agent/intent_agent.py` | Intent Agent — deep LLM parsing + user profile inference |
| `agent/multi_agent/poi_strategy_agent.py` | POI Strategy Agent — search region planning + quality evaluation |
| `agent/multi_agent/narrator_agent.py` | Narrator Agent — personalized route narration |
| `agent/multi_agent/reviewer_agent.py` | Reviewer Agent — quality audit with time budget auto-detection |
| `agent/multi_agent/modifier_agent.py` | Modifier Agent — rule+LLM modification intent detection for multi-turn |
| `agent/tools/poi.py` | Amap API wrapper: `search_poi`, `search_around`, `search_along_route`, `geocode`, `robust_geocode` (4-layer fallback), `input_tips` |
| `agent/tools/graph_planner.py` | Graph algorithm: `build_graph` (fully-connected with ThreadPool), `shortest_path` (projection-based segment selection) |
| `agent/tools/routing.py` | Amap walking direction API → `{distance, duration}` |
| `agent/tools/reviews.py` | Amap POI detail lookup (skeleton — Dianping not yet integrated) |
| `agent/tools/constants.py` | Shared constants: keyword normalization map, category blacklist, intent placeholders |
| `agent/tools/geo.py` | Geometry: `haversine()`, `project_ratio()` |
| `agent/tools/poi_filter.py` | POI filtering: `normalize_keywords()`, `filter_by_category()`, `filter_by_coords()`, `filter_near_anchor()`, `deduplicate_by_name()` |

### Design decisions (why, not what)

- **Algorithm picks routes, LLM narrates**: LLM has no spatial awareness. Routing decisions are algorithmic (projection-based segment selection); LLM only parses intent and generates natural language narration.
- **Projection-based segment selection, not Dijkstra/TSP**: POIs concentrate near the origin. Any greedy nearest-neighbor approach traps locally. POIs are projected onto the origin→destination line, then the line is divided into equal segments, picking the highest-rated POI per segment.
- **Walking API × speed factor for all transport modes**: Amap biking API has low coverage. All edges use walking API distance, then scale duration by `{步行:1×, 骑行:3×, 公交:2×, 打车:5×}`.
- **Rule-first modification detection, LLM fallback**: 8 categories of regex patterns cover ~90% of modification intents (fast, deterministic). LLM handles ambiguous/compound expressions.
- **Per-user JSON files with 256KB compression**: One file per user (`users/{user_id}.json`), binary-search compression keeps history while staying under limit. Session and profile are never compressed.

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
