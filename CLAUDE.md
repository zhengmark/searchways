# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

美团 AI 黑客松项目 —— "现在就出发：AI 本地路线智能规划"。用户用自然语言描述出行需求，Agent 自动规划一条结合 POI 数据、评分和偏好的本地路线，输出 LLM 解说 + Mermaid 路线图 + Leaflet 交互地图。

完整上下文见 `docs/CONTEXT.md` 和 `docs/ARCHITECTURE.md`。

## Quick reference

```bash
# Install deps
pip install -r requirements.txt

# Run the agent (CLI, multi-turn)
python main.py

# Run a single route (multi-agent pipeline)
python3 -c "
import sys; sys.path.insert(0, '.')
from app.core.orchestrator import run_multi_agent
result, session = run_multi_agent('从丈八六路地铁站出发到浐灞玩', user_id='default')
print(result)
"

# Multi-turn example
python3 -c "
import sys; sys.path.insert(0, '.')
from app.core.orchestrator import run_multi_agent
result, s = run_multi_agent('从丈八六路出发去钟楼吃', user_id='default')
result2, s = run_multi_agent('不去钟楼了，去大雁塔', session=s, user_id='default')
print(result2)
"

# 5 画像深度体验测试
python3 tests/test_ux_deep.py

# 3 画像快速回归测试
python3 tests/test_user_scenarios.py

# 单条路线集成测试
python3 tests/test_graph.py

# API 连通性测试
python3 tests/test_longcat.py    # LLM API
python3 tests/test_amap.py       # 高德 API
python3 tests/test_routing.py    # 高德步行 API

# View output files
cat data/output/route_output.md              # Mermaid 路线图
python3 -m http.server 8000                  # 浏览器访问 data/output/route_output.html
```

Config via `.env` (copy from `.env.example`): `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`, `AMAP_API_KEY`.

## Architecture

### Directory structure

```
my-first-app/
├── main.py                     # CLI entry
├── app/                        # Main Python package
│   ├── config.py               # Reads .env, exports API keys
│   ├── llm_client.py           # Shared LLM client (single source of truth)
│   ├── models.py               # Pydantic models: POI, RouteStop, Route, UserIntent
│   ├── user_profile.py         # UserProfileManager — per-user JSON persistence
│   ├── core/                   # Multi-agent orchestration
│   │   ├── types.py            # Agent communication data structures
│   │   ├── orchestrator.py     # Main controller — Plan-Execute-Review-Refine + multi-turn
│   │   ├── intent_agent.py     # Intent Agent — deep LLM parsing + user profile inference
│   │   ├── poi_strategy_agent.py # POI Strategy Agent — search planning + quality evaluation
│   │   ├── narrator_agent.py   # Narrator Agent — personalized route narration
│   │   ├── reviewer_agent.py   # Reviewer Agent — quality audit
│   │   └── modifier_agent.py   # Modifier Agent — rule+LLM modification intent detection
│   ├── providers/              # Data source abstraction
│   │   ├── base.py             # POIProvider abstract interface
│   │   └── amap_provider.py    # Amap API wrapper (search_poi, geocode, robust_geocode, etc.)
│   ├── algorithms/             # Pure algorithms
│   │   ├── geo.py              # haversine(), project_ratio()
│   │   ├── graph_planner.py    # build_graph() + shortest_path()
│   │   ├── poi_filter.py       # POI filtering (dedup, category, coords, distance)
│   │   ├── routing.py          # Amap walking direction API
│   │   └── reviews.py          # Amap POI detail lookup (skeleton)
│   ├── clustering/             # Offline clustering (PLACEHOLDER)
│   ├── recommender/            # Recommendation engine (PLACEHOLDER)
│   └── shared/                 # Shared utilities
│       ├── constants.py        # Keyword normalization, category blacklist, placeholders
│       └── utils.py            # AgentSession, _extract_city, _progress, _build_mermaid, _build_route_html
├── db/                         # Database layer (PLACEHOLDER)
├── web/                        # Website (PLACEHOLDER)
│   ├── static/assets/leaflet/  # Leaflet.js CSS/JS
│   └── templates/
├── tests/                      # Integration tests
├── docs/                       # Documentation (ARCHITECTURE.md, CONTEXT.md)
├── data/                       # Runtime data
│   ├── users/                  # User profile JSON files
│   └── output/                 # route_output.md, route_output.html
└── scripts/                    # Utility scripts
```

### Entry point

`main.py` — CLI loop, calls `run_multi_agent()` from `app/core/orchestrator.py`.

### Multi-agent pipeline (`app/core/orchestrator.py`)

`run_multi_agent()` orchestrates a **Plan-Execute-Review-Refine** loop:

1. **Intent Agent** (`intent_agent.py`) — LLM deep intent parsing: origin/destination/keywords/num_stops + UserProfile inference
2. **Geocode** — `robust_geocode()` 4-layer fallback for origin/destination coordinates
3. **POI Strategy Agent** (`poi_strategy_agent.py`) — generates search regions + keyword strategy → executes via Amap APIs → quality evaluation → auto re-search if insufficient
4. **Route Engine** — `build_graph()` fully-connected graph with walking API edges → `shortest_path()` projection-based segment selection → time budget constraint
5. **Narrator Agent** (`narrator_agent.py`) — personalized natural language narration
6. **Reviewer Agent** (`reviewer_agent.py`) — quality audit → up to 2 refine loops
7. **Output** — text reply + Mermaid diagram → `data/output/route_output.md` + Leaflet map → `data/output/route_output.html` + user profile → `data/users/{user_id}.json`

### Multi-turn dialogue

- **Modifier Agent** (`modifier_agent.py`) — rule-based regex (8 change types) + LLM fallback
- **Partial re-planning** — only re-runs affected pipeline stages
- **Session persistence** — `UserProfileManager.save_session()` stores current state
- **User profile** — `data/users/{user_id}.json` accumulates favorites, history, inferred preferences

## Design decisions

- **Algorithm picks routes, LLM narrates**: LLM has no spatial awareness. Routing decisions are algorithmic; LLM only parses intent and generates narration.
- **Projection-based segment selection**: POIs projected onto origin→destination line, divided into equal segments, highest-rated POI per segment selected.
- **Walking API × speed factor**: All edges use walking API distance, scale duration by `{步行:1×, 骑行:3×, 公交:2×, 打车:5×}`.
- **Rule-first modification detection, LLM fallback**: 8 regex categories cover ~90% of intents; LLM handles ambiguous expressions.
- **Per-user JSON files with 256KB compression**: Binary-search compression keeps history under limit.

## Critical conventions

### Python falsy — use `is not None`, never truthiness

Amap API returns `location="0,0"` for POIs without coordinates. `float("0") = 0.0` is falsy. Always check `if x is not None`.

### Substring matching for place names

Use continuous substring (`name in tip_name or tip_name in name`), not character overlap ratio.

### Integer division

Always use `round(x / 60)` for minute conversion, never `// 60`.

### `.env` variable names

`.env.example` uses `OPENAI_API_KEY`/`OPENAI_MODEL`, but `config.py` reads `LLM_API_KEY`/`LLM_MODEL`. The actual `.env` uses the `LLM_*` prefix.

### Tests are integration tests

All tests call live APIs (LLM + Amap). There are no unit tests with mocks. Running tests consumes API quota.
