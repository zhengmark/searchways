# 现在就出发 — AI 本地路线智能规划

美团 AI 黑客松项目。**用户用自然语言描述出行需求，Agent 自动规划一条结合 POI 数据、评分和偏好的本地路线**，输出 LLM 个性化解说 + Mermaid 路线图 + Leaflet 交互地图。

```
用户: "从丈八六路去钟楼吃火锅，3小时，人均100以内"

Agent: geocode(丈八六路) → geocode(钟楼) → query_clusters(火锅, medium)
       → build_route([187, 317, 400]) → 解说 + Mermaid 图

输出: 一条3站火锅路线，附每站交通方式/耗时/推荐理由/人均消费，Mermaid路线图，交互地图
```

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 API Key
cp .env.example .env
# 编辑 .env，填入 LLM_API_KEY 和 AMAP_API_KEY

# 3. CLI 多轮对话
USE_POI_DB=true python3 main.py

# 4. Web 服务（http://localhost:8000）
USE_POI_DB=true uvicorn web.server:app --host 0.0.0.0 --port 8000

# 5. 命令行快速测试
USE_POI_DB=true python3 -c "
import sys; sys.path.insert(0, '.')
from app.core.orchestrator import run_multi_agent
result, s = run_multi_agent('从丈八六路地铁站出发到钟楼吃火锅')
print(result)
"
```

## 架构

```
用户输入
  │
  ▼
┌──────────────────────────────────────┐
│  orchestrator.py (27行)              │
│  统一入口 → run_unified_agent()       │
└──────────────────────────────────────┘
  │
  ▼
┌──────────────────────────────────────┐
│  route_agent.py (361行) ★核心        │
│                                      │
│  LLM (系统 prompt + 3 tools)         │
│    │                                 │
│    ├─ geocode(place, city)           │
│    │    → 高德API 4层兜底解析         │
│    │                                 │
│    ├─ query_clusters(origin, dest,   │
│    │    keywords, budget)            │
│    │    → SQLite 走廊聚簇摘要         │
│    │    → ~8ms，995个预计算簇         │
│    │                                 │
│    └─ build_route(cluster_ids,       │
│         num_stops)                   │
│         → graph_planner 建图+选路径   │
│         → ~6s，K=8近邻+预剪枝        │
│                                      │
│  LLM 生成: 个性化解说 + Mermaid 路线图 │
└──────────────────────────────────────┘
  │
  ▼
输出: 解说文本 + Mermaid图 + Leaflet交互地图 + 用户画像更新
```

**关键设计决策：**
- **LLM 管聚簇，算法管 POI** — 聚簇有语义标签（品类/价格/评分），LLM 可理解；具体 POI 的选择和路线计算由算法完成
- **全量重规划替代增量修改** — 多轮对话每轮重新规划，比增量修改更鲁棒
- **工具调用替代多 Agent 串行** — 1 次 LLM 调用 + 工具循环，替代原来 4 次 LLM 的 6 步串行管线

## 使用示例

### 单轮路线

```bash
USE_POI_DB=true python3 main.py
```

```
你: 从丈八六路地铁站出发到钟楼，想吃火锅，3小时，人均不超过100

出发酱: 🍲 火锅美食之旅

## 路线概览
总耗时约75分钟，精选3家高评分火锅店

### 第1站: 胡龙飞酸菜串串 — ⭐4.0 ¥47
🚌 公交/地铁 43分钟 | 酸菜串串+火锅，性价比之选

### 第2站: MOMOPARK·海底捞火锅 — ⭐4.1 ¥54  
🚕 打车 22分钟 | 品质服务，适合聚餐

### 第3站: 钟楼银泰·海底捞火锅 — ⭐4.0 ¥56
🚶 步行可达 | 吃完正好逛钟楼

💡 建议提前在海底捞公众号预约，避开晚高峰排队

```mermaid
flowchart LR
    classDef start fill:#10b981,color:#fff
    classDef mid fill:#3b82f6,color:#fff
    classDef end fill:#f59e0b,color:#fff
    N0(["丈八六路地铁站"]):::start
    N0 -->|"🚌 43分"| N1["🍲 胡龙飞酸菜串串"]:::mid
    N1 -->|"🚕 22分"| N2["🍲 MOMOPARK"]:::mid
    N2 -->|"🚶 10分"| N3["🏁 钟楼"]:::end
```
```

### 多轮对话

```python
from app.core.orchestrator import run_multi_agent

# 第1轮
result, s = run_multi_agent('从丈八六路出发去钟楼吃火锅')

# 第2轮: 改终点
result2, s = run_multi_agent('不去钟楼了，去大雁塔', session=s)

# 第3轮: 改偏好
result3, s = run_multi_agent('要高档的，换日料', session=s)
```

### Web 界面

```
http://localhost:8000

端点:
  GET  /                   HTML 单页应用
  GET  /api/health         健康检查
  POST /api/plan           新建路线
  POST /api/plan/stream    SSE 流式进度推送
  POST /api/chat           多轮对话修改
```

## 性能数据

| 指标 | 高德 API 模式 | 本地 DB 模式 |
|------|-------------|------------|
| POI 搜索 | ~1169ms (API) | ~8ms (SQLite) |
| 建图 | ~6s (步行API并发) | ~6s (相同) |
| 单轮总耗时 | ~30-60s | ~12-15s |
| POI 覆盖 | 西安全城 | 14,222 条 |
| 聚类簇 | — | 995 个 (93.9%覆盖率) |

**优化历程：**
- 建图: ~90s→~6s (~15x) — haversine 快速通道 + K=8 近邻 + 预剪枝 top-15
- 搜索: ~330ms→~8ms (~39x) — 网格法预计算 995 个聚类簇 + cluster_meta 缓存
- LLM: 4次→1次 — 工具调用替代多 Agent 串行

## 配置

| 变量 | 用途 | 默认值 |
|------|------|--------|
| `LLM_API_KEY` | LLM API Key | — |
| `LLM_BASE_URL` | LLM API 地址 | `https://api.longcat.chat/anthropic` |
| `LLM_MODEL` | 模型名 | `LongCat-Flash-Lite` |
| `AMAP_API_KEY` | 高德开放平台 Key | — |
| `USE_POI_DB` | 启用本地 POI 数据库 | `false` |

## 目录结构

```
my-first-app/
├── main.py                        # CLI 多轮对话入口
├── app/                           # 主 Python 包
│   ├── config.py                  # 环境变量读取
│   ├── llm_client.py              # LLM API 客户端（含 tool_use 支持）
│   ├── models.py                  # Pydantic 数据模型
│   ├── user_profile.py            # 用户画像持久化
│   ├── core/                      # 核心编排
│   │   ├── orchestrator.py        # 入口 (27行)
│   │   ├── route_agent.py         # ★ 统一 Agent — LLM 工具调用循环
│   │   └── types.py               # Agent 间通信类型
│   ├── pipeline/                  # 数据处理管线
│   │   ├── cluster_tools.py       # ★ 工具定义 + geocode/query_clusters/build_route 实现
│   │   ├── poi_pipeline.py        # POI 搜索 + DB 推荐
│   │   └── route_pipeline.py      # 地理编码 + 路线引擎
│   ├── providers/                 # 数据源抽象
│   │   ├── base.py                # POIProvider ABC 接口
│   │   ├── amap_provider.py       # 高德 API 封装
│   │   └── provider.py            # 灰度切换入口
│   ├── algorithms/                # 纯算法
│   │   ├── geo.py                 # Haversine / 投影比例
│   │   ├── graph_planner.py       # 建图 + 最短路径（K近邻+预剪枝）
│   │   ├── poi_filter.py          # POI 过滤（去重/品类/坐标）
│   │   └── routing.py             # 高德步行 API
│   ├── clustering/                # 离线聚类
│   │   ├── geo_cluster.py         # 地理聚类 (DBSCAN)
│   │   └── attr_cluster.py        # 属性聚类 (KMeans k=5)
│   ├── recommender/               # 推荐引擎
│   │   ├── recall.py              # 四路召回
│   │   ├── rank.py                # 走廊感知精排 + 多样性贪心
│   │   └── engine.py              # 统一推荐入口
│   └── shared/                    # 共享工具
│       ├── constants.py           # 关键词规范化 / 黑名单
│       └── utils.py               # 城市提取 / Mermaid / HTML / AgentSession
├── db/                            # 数据库层
│   ├── schema.py                  # 表结构
│   ├── connection.py              # SQLite 连接管理
│   ├── repository.py              # POIRepository — SQLite + Haversine
│   ├── cluster.py                 # 聚类预计算 + 走廊查询
│   ├── seed.py                    # 高德 API 数据灌入
│   └── maintenance.py             # 增量数据维护
├── web/                           # FastAPI 网站
│   ├── server.py                  # 5 端点 + SSE 流式
│   ├── templates/index.html       # 单页 UI
│   └── static/assets/leaflet/     # Leaflet CSS/JS
├── tests/                         # 集成测试
│   ├── test_user_scenarios.py     # 3 画像快速回归
│   ├── test_ux_deep.py            # 5 画像深度体验
│   ├── test_graph.py              # 单条路线集成测试
│   ├── test_benchmark.py          # DB vs Amap 性能基准
│   └── test_longcat.py            # LLM API 连通性
├── docs/                          # 文档
│   ├── CONTEXT.md                 # 项目上下文
│   └── ARCHITECTURE.md            # 架构文档
└── data/                          # 运行时数据
    ├── pois.db                    # SQLite POI 数据库 (14,222条)
    ├── users/                     # 用户画像 JSON
    └── output/                    # route_output.md / route_output.html
```

## 运行测试

```bash
# 所有测试调真实 API，会消耗配额
python3 tests/test_user_scenarios.py    # 3 画像快速回归
python3 tests/test_ux_deep.py           # 5 画像深度体验
python3 tests/test_graph.py             # 单条路线集成测试

# API 连通性
python3 tests/test_longcat.py           # LLM API
python3 tests/test_amap.py              # 高德 API

# 性能基准
USE_POI_DB=true python3 tests/test_benchmark.py

# 数据库维护
python3 -m db.cluster stats             # 聚类统计
python3 -m db.cluster build             # 全量重算聚类
python3 -m db.maintenance --task sync   # 同步 POI 数据
```

## 技术栈

| 层级 | 技术 |
|------|------|
| LLM | LongCat (Anthropic 兼容 API) — tool_use / system prompt |
| 地图 | 高德开放平台 — 地理编码 / POI 搜索 / 步行路径 |
| 数据库 | SQLite + Haversine 空间查询 — 14,222 条西安 POI |
| Web | FastAPI + Jinja2 + SSE 流式推送 |
| 前端 | Leaflet.js 交互地图 + Mermaid.js 路线图 |
| 聚类 | 网格法预计算 (995簇) + DBSCAN + KMeans |
| 推荐 | 四路召回 + 走廊感知精排 + MMR 多样性贪心 |
| 并发 | ThreadPoolExecutor — 步行 API 并行 + LLM 后台调用 |
