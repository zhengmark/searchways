# 现在就出发 — AI 本地路线智能规划

美团 AI 黑客松项目。**用户用自然语言描述出行需求，AI Agent 自动规划路线**，输出 LLM 解说 + Mermaid 路线图 + Leaflet 交互地图。

```
用户: "从钟楼出发去大雁塔逛逛"

Agent: geocode(钟楼,大雁塔) → query_clusters(景点,美食) → build_route(5簇,5站)
       → 垂距过滤 + 前向约束杜绝回头路 → 分段均衡确保终点覆盖

输出: 均匀分布的 6 站路线 + Mermaid图 + 交互地图 + 大雁塔终点
```

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 API Key
cp .env.example .env
# 编辑 .env，填入 LLM_API_KEY 和 AMAP_API_KEY

# 3. Web 服务（http://localhost:8000）
python3 -m uvicorn web.server:app --host 0.0.0.0 --port 8000

# 4. CLI 快速测试
python3 -c "
import sys; sys.path.insert(0, '.')
from app.core.orchestrator import run_multi_agent
result, s = run_multi_agent('从钟楼出发去大雁塔逛逛')
print(result)
"

# 5. 多轮对话
python3 -c "
from app.core.orchestrator import run_multi_agent
result, s = run_multi_agent('从丈八六路出发去钟楼吃')
result2, s = run_multi_agent('不去钟楼了，去大雁塔', session=s)
print(result2)
"
```

## 核心特性

### 智能路线规划
- **统一 Agent 工具调用** — 单次 LLM + geocode / query_clusters / build_route 工具循环
- **走廊感知 POI 推荐** — 15,719 条西安 POI，垂距过滤确保沿路线分布
- **前向约束路由** — 投影递增保证方向一致，杜绝回头路
- **分段均衡选取** — 5 段投影各至少 5 簇，终点附近不再空白
- **目的地自动包含** — 用户指定终点（如大雁塔）始终出现在路线末站
- **城市热门景点发现** — 模糊查询自动注入精选地标

### 交互式地图编辑
- **路段交通标注** — hover 显示交通方式 + 时间，不同颜色不同交通
- **推荐 POI 可选** — 虚线圆圈标记备选 POI，点击加入路线
- **拖拽排序** — 侧边栏 HTML5 DnD 自由调整游览顺序
- **颜色图例** — 地图左上角自动显示品类颜色图例
- **确认路线** — 确认后生成 LLM 个性化解说 + Mermaid 路线图

### 个性化 & 稳定性
- **用户画像学习** — 从完成路线自动学习品类/区域/预算偏好
- **InputEnricher 预处理** — 城市默认、关键词规范化、排除词提取
- **Session TTL** — 24 小时自动过期 + 后台清理
- **LLM 自动重试** — 指数退避 + 随机抖动 + 错误分类
- **Superpowers-ZH** — 20 个 AI 工作流技能（brainstorming / TDD / code review 等）

## 架构

```
用户输入
  │
  ▼
┌─────────────────────────────────────────────┐
│  InputEnricher 预处理                        │
│  · 城市默认（西安）、关键词规范化             │
│  · "逛"→景点、"吃"→美食、排除词提取          │
└─────────────────────────────────────────────┘
  │
  ▼
┌─────────────────────────────────────────────┐
│  orchestrator.py                            │
│  run_multi_agent() → run_unified_agent()    │
└─────────────────────────────────────────────┘
  │
  ▼
┌─────────────────────────────────────────────┐
│  route_agent.py ★核心                       │
│                                             │
│  LLM (系统 prompt + 3 tools)  ≤16轮调用    │
│    │                                        │
│    ├─ geocode(places, city)                 │
│    │    → 高德 API 批量地理编码              │
│    │    → geocode 缓存避免重复调用            │
│    │                                        │
│    ├─ query_clusters(origin, dest,          │
│    │    keywords, budget)                   │
│    │    → SQLite 走廊聚簇摘要 (1010簇)       │
│    │    → 垂距过滤 (偏离路线簇自动排除)       │
│    │    → 分段均衡 LIMIT (每段≥5簇)          │
│    │    → 热门景点自动注入                   │
│    │                                        │
│    └─ build_route(cluster_ids,              │
│         num_stops)                          │
│         → graph_planner 建图+选路径          │
│         → 前向约束 (projection 递增)         │
│         → 终点自动加入路线                   │
│                                             │
│  LLM 生成: 个性化解说                        │
└─────────────────────────────────────────────┘
  │
  ▼
┌─────────────────────────────────────────────┐
│  Web 交互式编辑                             │
│                                             │
│  ┌──────────┐  ┌───────────────────────┐    │
│  │ Leaflet   │  │  侧边栏               │    │
│  │ 地图      │  │  · 路线摘要           │    │
│  │ · 路线    │  │  · 站点列表(可拖拽)   │    │
│  │ · 推荐POI │  │  · 备选POI [+加入]    │    │
│  │ · 交通标注│  │  · [确认路线]          │    │
│  │ · 图例    │  │  · 聊天修改           │    │
│  └──────────┘  └───────────────────────┘    │
│                                             │
│  确认 → LLM解说 + Mermaid图 + 偏好学习       │
└─────────────────────────────────────────────┘
```

**关键设计决策：**
- **LLM 管聚簇，算法管 POI** — 聚簇有语义标签，LLM 可理解；具体 POI 和路线由算法计算
- **垂距过滤 + 前向约束** — 确保路线不偏离、不回头
- **全量 corridor 覆盖** — 用全部查询到的簇加载推荐 POI，而非 LLM 挑的 4-5 个
- **分段均衡 LIMIT** — 防止起点段簇挤占终点段
- **工具调用替代多 Agent 串行** — 1 次 LLM + 工具循环，替代原来 4 次 LLM 的 6 步串行

## API 端点

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/` | — | HTML 单页应用 |
| GET | `/api/health` | — | 健康检查（DB 状态 + session 数） |
| POST | `/api/plan` | opt | 同步路线规划 |
| POST | `/api/plan/stream` | opt | SSE 流式规划（实时进度） |
| POST | `/api/chat` | opt | 多轮对话修改路线 |
| POST | `/api/auth/register` | — | 注册 → token |
| POST | `/api/auth/login` | — | 登录 → token |
| GET | `/api/auth/me` | Bearer | 验证 token |
| GET | `/api/profile` | opt | 获取用户画像 |
| PUT | `/api/profile` | opt | 更新用户画像 |
| GET | `/api/profile/suggestions` | opt | 个性化推荐 |
| POST | `/api/plan/alternatives` | opt | 多方案对比 |
| GET | `/api/share/{id}` | — | 获取分享路线 |
| POST | `/api/share` | opt | 创建分享 |

## 配置

| 变量 | 用途 | 默认值 |
|------|------|--------|
| `LLM_API_KEY` | LLM API Key | — |
| `LLM_BASE_URL` | LLM API 地址 | `https://api.longcat.chat/anthropic` |
| `LLM_MODEL` | 模型名 | `LongCat-Flash-Lite` |
| `AMAP_API_KEY` | 高德开放平台 Key | — |
| `JWT_SECRET` | JWT 密钥 | `jwt-secret-change-me` |
| `SESSION_TTL_HOURS` | Session 过期时间 | 24 |

## 目录结构

```
my-first-app/
├── main.py                         # CLI 多轮对话入口
├── HERMES.md                       # Superpowers-ZH bootstrap
├── app/                            # 主 Python 包
│   ├── config.py                   # .env 读取
│   ├── llm_client.py               # LLM API（指数退避重试）
│   ├── models.py                   # Pydantic 数据模型
│   ├── user_profile.py             # 用户画像持久化
│   ├── auth.py                     # bcrypt + JWT 认证
│   ├── core/
│   │   ├── orchestrator.py         # 统一入口
│   │   ├── route_agent.py          # ★ 统一 Agent 工具调用循环
│   │   ├── constraint_model.py     # 约束模型
│   │   └── types.py                # Agent 间类型
│   ├── pipeline/
│   │   ├── cluster_tools.py        # ★ 3 工具 + 垂距惩罚 + 去重缓存
│   │   ├── input_enricher.py       # ★ 输入预处理（城市/关键词/排除）
│   │   ├── corridor_engine.py      # 走廊 POI 加载
│   │   └── constraint_checker.py   # 约束校验
│   ├── providers/
│   │   └── amap_provider.py        # 高德 API（搜索/地理编码/路线）
│   ├── algorithms/
│   │   ├── geo.py                  # Haversine / 投影
│   │   ├── graph_planner.py        # ★ 建图 + 最短路径 + 前向约束
│   │   ├── poi_filter.py           # POI 过滤去重
│   │   └── routing.py              # 高德路径 API
│   └── shared/
│       ├── constants.py            # 关键词/黑名单/精选地标
│       └── utils.py                # AgentSession / Mermaid / HTML
├── db/
│   ├── poi.db                      # SQLite 15,719 条西安 POI
│   ├── connection.py               # WAL + context manager
│   ├── cluster.py                  # ★ 聚类 + 走廊查询 + 垂距过滤 + 分段均衡
│   └── repository.py               # POI 查询
├── web/
│   ├── server.py                   # FastAPI 14 端点 + SSE + session 管理
│   ├── templates/index.html        # SPA 页面
│   └── static/
│       ├── css/                    # auth.css, style.css
│       └── js/                     # app.js, auth.js, map.js, route-editor.js
├── scripts/
│   ├── seed_mid_route_pois.py      # POI 批量补搜
│   ├── enrich_pois.py              # POI 数据丰富
│   └── deploy.sh                   # 一键部署
├── tests/                          # 集成测试
├── docs/                           # 文档
│   ├── CONTEXT.md                  # 项目上下文
│   ├── ARCHITECTURE.md             # 架构文档
│   ├── architecture-optimization-plan.md
│   └── test-report-v2.md
├── .hermes/                        # Hermes Agent 配置
│   ├── skills/                     # 20 个 Superpowers-ZH 技能
│   └── plans/                      # 实施计划
├── .env.example
├── requirements.txt
└── Dockerfile
```

## 运行测试

```bash
# 集成测试（调真实 API，消耗配额）
python3 tests/test_user_scenarios.py    # 场景回归
python3 tests/test_graph.py             # 单条路线集成
python3 tests/test_4round_rolling.py    # 4轮滚动鲁棒性测试

# 数据库维护
python3 -m db.cluster stats             # 聚类统计（1010簇, 94.6%覆盖）
python3 -m db.cluster build             # 全量重算聚类
```

## 技术栈

| 层级 | 技术 |
|------|------|
| LLM | DeepSeek / LongCat (Anthropic 兼容 API) — tool_use / system prompt |
| 地图 | 高德开放平台 — 地理编码 / POI 搜索 / 步行路径 |
| 数据库 | SQLite + Haversine — 15,719 条西安 POI / 1,010 个聚类簇 |
| Web | FastAPI + Jinja2 + SSE 流式推送 |
| 前端 | Leaflet.js + Mermaid.js + HTML5 DnD |
| 聚类 | 网格法预计算 + 走廊投影排序 + 垂距过滤 |
| 路由 | 加权图 + K近邻剪枝 + 多目标评分 + 前向约束 |
| AI 技能 | Superpowers-ZH (20 skills: brainstorming / TDD / code review 等) |
