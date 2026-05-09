# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

美团 AI 黑客松项目 —— "现在就出发：AI 本地路线智能规划"。用户用自然语言描述出行需求，统一 Agent（LLM 工具调用循环）自动规划路线，输出 LLM 解说 + Mermaid 路线图 + Leaflet 交互地图。

完整上下文见 `docs/CONTEXT.md`、`docs/ARCHITECTURE.md`、`docs/test-report-v2.md`、`docs/optimization-plan.md`。

## Quick reference

```bash
# Install deps
pip install -r requirements.txt

# Web service (main interface)
python3 -m uvicorn web.server:app --host 0.0.0.0 --port 8000

# CLI multi-turn
python3 main.py

# Single route
python3 -c "
import sys; sys.path.insert(0, '.')
from app.core.orchestrator import run_multi_agent
result, s = run_multi_agent('从丈八六路地铁站出发到浐灞玩', user_id='default')
print(result)
"

# Multi-turn
python3 -c "
import sys; sys.path.insert(0, '.')
from app.core.orchestrator import run_multi_agent
result, s = run_multi_agent('从丈八六路出发去钟楼吃', user_id='default')
result2, s = run_multi_agent('不去钟楼了，去大雁塔', session=s, user_id='default')
print(result2)
"

# Auth API test
curl -s -X POST http://localhost:8000/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"testuser","password":"test123"}'

# 10-user multi-turn test (~6-8 min)
python3 tests/test_10users_v3.py

# View output
cat data/output/route_output.md
```

Config via `.env`: `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` (now `LongCat-Flash-Lite`), `AMAP_API_KEY`, `JWT_SECRET`.

## Architecture

### Core flow (unified agent — 罗斯方案)

```
用户输入 → orchestrator.run_multi_agent()
               │
               ├─ 新路线 → route_agent.run_unified_agent()
               └─ 多轮    → route_agent.run_unified_agent(session=...)
                              │
               LLM 工具调用循环 (≤12 iterations):
                 geocode → query_clusters → build_route → 解说
```

### Directory structure

```
my-first-app/
├── main.py                       # CLI entry
├── app/
│   ├── config.py                 # .env → API keys
│   ├── llm_client.py             # ★ _retry_request (3次指数退避) + call_llm/call_llm_with_tools
│   ├── auth.py                   # ★ bcrypt + JWT + get_current_user_optional
│   ├── models.py                 # Pydantic: POI, RouteStop, Route, UserIntent
│   ├── user_profile.py           # UserProfileManager — per-user JSON (256KB max)
│   ├── core/
│   │   ├── types.py              # Agent 间数据结构
│   │   ├── orchestrator.py       # ★ 主控入口 (27 行)
│   │   ├── route_agent.py        # ★ 统一 Agent — 工具调用循环 + 解说一致性 + 约束保留
│   │   ├── intent_agent.py       # 保留（未被引用）
│   │   └── narrator_agent.py     # 保留（未被引用）
│   ├── pipeline/
│   │   ├── cluster_tools.py     # ★ 3 工具 + 去重缓存 + 关键词映射(37条) + 黑名单(19词)
│   │   ├── poi_pipeline.py      # 保留（未被引用）
│   │   └── route_pipeline.py    # 保留（未被引用）
│   ├── providers/
│   │   ├── base.py              # POIProvider ABC
│   │   └── amap_provider.py     # Amap API (geocode, search, walking)
│   ├── algorithms/
│   │   ├── geo.py               # haversine, project_ratio
│   │   ├── graph_planner.py     # build_graph (K近邻+预剪枝) + shortest_path (多目标评分)
│   │   ├── poi_filter.py        # POI dedup/filter
│   │   └── routing.py           # Amap walking API
│   ├── clustering/              # 预计算聚类（DB 模式）
│   ├── recommender/             # 推荐引擎（四路召回+走廊精排+多样性贪心）
│   └── shared/
│       ├── constants.py         # 关键词规范化, 品类黑名单, 占位符
│       └── utils.py             # AgentSession (typed attrs), _extract_city, _build_route_html
├── db/
│   ├── poi.db                   # SQLite 14,222 条西安 POI
│   ├── auth.db                  # ★ 用户账号 (users 表)
│   ├── connection.py            # SQLite WAL + context manager
│   ├── cluster.py               # query_corridor_clusters
│   └── repository.py            # _row_to_dict
├── web/
│   ├── server.py                # ★ FastAPI (6 endpoints: SSE plan, chat, auth×3, health)
│   ├── routes/auth.py           # ★ /api/auth/{register,login,me}
│   ├── templates/index.html     # SPA + 登录弹窗 + 输入区锁定
│   └── static/
│       ├── css/auth.css         # 登录/注册弹窗样式
│       └── js/auth.js           # 前端认证 (JWT localStorage + authFetch)
├── tests/
│   ├── test_10users.py          # 第一轮 10 用户测试
│   ├── test_10users_v2.py       # 第二轮 (API 中断)
│   ├── test_10users_v3.py       # ★ 第三轮 (全修复, 8/10 良好)
│   ├── test_graph.py            # 单条路线集成
│   └── test_user_scenarios.py   # 3 画像快速回归
├── docs/
│   ├── CONTEXT.md               # 项目上下文
│   ├── ARCHITECTURE.md          # 架构文档
│   ├── optimization-plan.md     # 优化方案 (6+6+5 = 17项)
│   └── test-report-v2.md        # 测试报告
└── data/
    ├── users/{user_id}.json      # 用户画像
    └── output/                   # route_output.md/.html + test JSONs
```

## API endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/` | — | SPA 页面 |
| GET | `/api/health` | — | 健康检查 |
| POST | `/api/auth/register` | — | 注册 → `{token, username}` |
| POST | `/api/auth/login` | — | 登录 → `{token, username}` |
| GET | `/api/auth/me` | Bearer | 验证 token → `{username}` |
| POST | `/api/plan/stream` | opt | SSE 流式规划 |
| POST | `/api/plan` | opt | 同步规划 |
| POST | `/api/chat` | opt | 多轮修改 |

Auth is optional (`get_current_user_optional`) — without token, falls back to `user_id="default"`.

## Key design decisions

- **Unified agent tool-calling**: Single LLM call with geocode → query_clusters → build_route tool loop. Replaced old 6-step multi-agent serial pipeline. Net -824 lines.
- **Algorithm picks routes, LLM narrates**: Routing decisions are algorithmic (KNN graph + multi-objective scoring); LLM handles intent parsing and narration.
- **Cluster-based recall**: Pre-computed 995 geo clusters — query_corridor_clusters returns top clusters in corridor, LLM selects, build_route constructs actual path.
- **Category matching**: 37 keyword→category mappings + 19-word POI name blacklist (KTV/棋牌/手机店 etc.). Clusters scored by relevance, irrelevant ones filtered.
- **Constraint preservation**: Multi-turn conversations preserve unchanged constraints (keywords, budget) via session persistence + prompt rules.
- **Per-user JSON files**: 256KB max with binary-search compression. Each user gets independent `data/users/{username}.json`.

## Critical conventions

### Python falsy — use `is not None`, never truthiness

Applies everywhere: coordinates (0.0), API responses, database rows.
**New case**: `requests.Response.__bool__()` returns `False` for 4xx/5xx. Always check `if resp is not None`, never `if resp`.

### Substring matching for place names

Use continuous substring (`name in tip_name or tip_name in name`), not character overlap ratio.

### Integer division

Always use `round(x / 60)` for minute conversion, never `// 60`.

### Dedup cache for tool calls

`cluster_tools.py` has a 30s TTL dedup cache. Same parameters (cluster_ids+num_stops for build_route, coords+keywords for query_clusters) return cached results. Prevents LLM from retrying identical calls.

### .env variables

- `LLM_MODEL=LongCat-Flash-Lite` (was `LongCat-Flash-Chat`)
- `JWT_SECRET` — override in production, default `"jwt-secret-change-me"`
- `config.py` reads `LLM_API_KEY`/`LLM_MODEL`, not `OPENAI_*`

### Tests are integration tests

All tests call live APIs (LLM + Amap). Running tests consumes API quota. `LongCat-Flash-Lite` has daily usage limits.
