# 架构文档 — "现在就出发" AI 本地路线智能规划

> 美团 AI 黑客松项目。用户用自然语言描述出行需求，Agent 自动规划 POI 路线，输出 LLM 解说 + Mermaid 路线图 + Leaflet 交互地图。

---

## 1. 快速开始

```bash
pip install -r requirements.txt    # 安装依赖
cp .env.example .env               # 编辑填入 LLM_API_KEY 和 AMAP_API_KEY

# CLI 多轮对话
python main.py

# 命令行跑一条
python3 -c "
import sys; sys.path.insert(0, '.')
from app.core.orchestrator import run_multi_agent
result, s = run_multi_agent('从丈八六路地铁站出发到浐灞玩', user_id='default')
print(result)
"

# Web 服务
python3 -m uvicorn web.server:app --host 0.0.0.0 --port 8000

# 运行测试（所有测试调真实 API）
python3 tests/test_user_scenarios.py    # 3 画像快速回归
python3 tests/test_ux_deep.py           # 5 画像深度体验
python3 tests/test_graph.py             # 单条路线集成测试
```

配置 `.env`：

| 变量 | 用途 | 值 |
|------|------|-----|
| `LLM_API_KEY` | LLM API Key | LongCat / Anthropic |
| `LLM_BASE_URL` | LLM API 地址 | `https://api.longcat.chat/anthropic` |
| `LLM_MODEL` | 模型 | `LongCat-Flash-Chat` |
| `AMAP_API_KEY` | 高德开放平台 Key | — |
| `USE_POI_DB` | 启用本地 POI DB | `true` / `false`（默认） |

---

## 2. 目录结构

```
my-first-app/
├── main.py                       # CLI 入口
├── app/                          # 主 Python 包
│   ├── config.py                 # 读取 .env 导出常量
│   ├── llm_client.py             # 共享 LLM 客户端（全 Agent 单点复用）
│   ├── models.py                 # Pydantic: POI, RouteStop, Route, UserIntent
│   ├── user_profile.py           # 用户画像管理器（per-user JSON 持久化）
│   │
│   ├── core/                     # 多 Agent 编排
│   │   ├── types.py              # Agent 间通信数据结构（IntentResult, ReviewResult 等）
│   │   ├── orchestrator.py       # ★ 主控入口，调用统一 Agent
│   │   ├── route_agent.py        # ★ 统一路线 Agent（罗斯方案 — 工具调用循环）
│   │   ├── intent_agent.py       # 意图 Agent — LLM 深度解析 + 画像推理
│   │   └── narrator_agent.py     # 解说 Agent — 个性化路线叙述（独立调用）
│   │
│   ├── providers/                # 数据源抽象
│   │   ├── base.py               # POIProvider 抽象接口
│   │   └── amap_provider.py      # 高德 API 封装（search_poi, geocode, robust_geocode 等）
│   │
│   ├── algorithms/               # 纯算法
│   │   ├── geo.py                # haversine(), project_ratio()
│   │   ├── graph_planner.py      # ★ build_graph() + shortest_path()
│   │   ├── poi_filter.py         # POI 过滤（去重/品类/坐标/距离）
│   │   ├── routing.py            # 高德步行距离 API
│   │   └── reviews.py            # 点评 API 骨架
│   │
│   ├── clustering/               # 离线聚类（DB 模式用）
│   ├── recommender/              # 推荐引擎
│   └── shared/                   # 共享工具
│       ├── constants.py          # 关键词映射、品类黑名单、占位符
│       └── utils.py              # AgentSession, _extract_city, _build_mermaid, _build_route_html
│
├── db/                           # 本地 POI 数据库
├── web/                          # Web 前端
│   ├── server.py                 # ★ FastAPI 服务（/api/plan/stream SSE, /api/chat）
│   ├── static/                   # CSS + Leaflet 静态资源
│   └── templates/index.html      # 单页应用
├── tests/                        # 集成测试
├── data/
│   ├── users/{user_id}.json      # 用户画像持久化
│   └── output/                   # route_output.md / .html
├── docs/                         # 文档
└── scripts/                      # 工具脚本
```

---

## 3. 核心流程

### 3.1 完整规划流程（新路线）

```
用户输入 "从西安北站出发去曲江玩，3小时"
  │
  ├─ 1. 城市提取      _extract_city() → regex 匹配 300+ 城市列表
  ├─ 2. Intent Agent  LLM 深度解析 → IntentResult {origin, destination, keywords, UserProfile, ...}
  ├─ 3. 地理编码      robust_geocode() 起终点 → 4 层兜底 + 正则回退
  │
  ├─ 4. 统一路线 Agent (`route_agent.run_unified_agent`) → 工具调用循环
  │      ├─ geocode 工具              → 地名 → 经纬度
  │      ├─ query_clusters 工具       → 聚类召回 POI（由 cluster_tools 执行）
  │      └─ build_route 工具          → 路径构建 + 交通计算
  │
  ├─ 5. LLM 输出      个性化解说 + Mermaid 路线图（统一 Agent 内生成）
  │
  └─ 6. Output        文本 + Mermaid 图 → route_output.md
                       Leaflet 交互地图 → route_output.html
                       用户画像 → data/users/{user_id}.json
```

### 3.2 多轮对话流程

```
用户 "加入大兴善寺"
  │
  ├─ 统一 Agent 接收对话历史（上一次的起点/终点/途经站/偏好）
  ├─ LLM 理解修改意图（通过上下文注入，无需独立 modifier agent）
  └─ 工具调用循环自动重规划（geocode 新地点 → query_clusters → build_route → 解说）
```

---

## 4. 关键设计决策

### 4.1 算法选路径，LLM 做表达

LLM 对空间关系/距离/交通时间无感知。路径选择全由算法完成（haversine + 步行 API + 投影分段），LLM 只负责意图解析和个性化解说。

### 4.2 投影分段选取（解决 POI 扎堆）

POI 投影到起终点连线上 → 均分 N 段 → 每段独立选取（品类多样优先，评分次之，500m 内互斥）。避免了基于"最近邻"的贪心算法全部扎堆起点的固有问题。

### 4.3 统一 Agent 上下文注入

多轮对话通过将上一次路线上下文注入 system prompt 实现，LLM 自行判断修改意图并重规划。替代旧方案中独立的 modifier_agent 规则检测 + 增量分支。

### 4.4 Python falsy 显式检查

高德 API 对无坐标 POI 返回 `location="0,0"`，`float("0")=0.0` 是 Python falsy。所有坐标判空必须用 `is not None`，否则误杀有效数据（赤道附近有真实 0.0 坐标）。

### 4.5 地名子串匹配

用 `name in tip_name or tip_name in name` 而非字符重叠率。字符重叠法下"四路地铁站"会误配到"凤城四路店"。

---

## 5. Web 前端

FastAPI 单页应用，端口 8000，两个核心 API：

| 端点 | 说明 |
|------|------|
| `POST /api/plan/stream` | SSE 流式规划 — 推送进度事件 + 最终结果 |
| `POST /api/chat` | 多轮对话修改 — 返回 JSON 结果 |
| `GET /api/health` | 健康检查 |

前端使用 Leaflet.js + Mermaid.js 渲染交互地图和路线图。地图通过 `position: relative; z-index: 0` 隔离 Leaflet 内部高 z-index，避免覆盖 sticky 输入框。

---

## 6. 扩展指南

### 添加新的 POI 数据源

在 `app/providers/` 下实现 `POIProvider` 抽象接口（参考 `amap_provider.py`），然后在 `orchestrator.py` 中切换。

### 添加新的工具

1. `app/pipeline/cluster_tools.py` → `TOOL_DEFINITIONS` 添加工具定义
2. `app/pipeline/cluster_tools.py` → `execute_tool()` 添加执行逻辑
3. `app/core/route_agent.py` → `_SYSTEM_PROMPT` 补充使用说明

### 接入登录系统

`UserProfileManager(user_id=login_state.user_id)` — 只需改 user_id 参数。

### 启用本地 POI DB

在 `.env` 设 `USE_POI_DB=true`。走预计算聚类 + 推荐引擎路径，跳过 3 次 LLM 调用 + N 次 Amap 搜索，规划时间减半。

---

## 7. 测试

所有测试均为集成测试（调真实 API），运行会消耗 LLM + 高德 API 配额。

| 文件 | 说明 |
|------|------|
| `tests/test_user_scenarios.py` | 3 画像快速回归 |
| `tests/test_ux_deep.py` | 5 画像深度体验（评分卡） |
| `tests/test_graph.py` | 单条路线集成 |
| `tests/test_longcat.py` | LLM API 连通性 |
| `tests/test_amap.py` | 高德 API 连通性 |
| `tests/test_routing.py` | 步行 API 连通性 |

---

*最后更新：2026-05-08*
