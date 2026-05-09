# 现在就出发 — AI 本地路线智能规划

美团 AI 黑客松项目。**用户用自然语言描述出行需求，AI Agent 自动规划路线**，提供交互式地图编辑 + 个性化推荐 + LLM 解说 + Mermaid 路线图。

```
用户: "从西安北站到曲江转一圈，想吃美食看景点，半天时间"

Agent: geocode(西安北站) → geocode(曲江) → query_clusters(美食, 景点)
       → build_route([5个均匀分布的簇]) → 地图交互编辑 → 确认路线 → 个性化解说

输出: 均匀分布的路线 + 推荐备选POI + 交通方式标注 + Mermaid图 + 交互地图
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

# 4. Docker 一键部署
./scripts/deploy.sh

# 5. CLI 快速测试
python3 -c "
import sys; sys.path.insert(0, '.')
from app.core.orchestrator import run_multi_agent
result, s = run_multi_agent('从西安北站到曲江转一圈')
print(result)
"
```

## 核心特性

### 智能路线规划
- **统一 Agent 工具调用** — 单次 LLM 调用 + geocode/query_clusters/build_route 工具循环
- **走廊感知 POI 推荐** — 1630+ 个 POI 沿路线走廊均匀分布，分 5 段各取 top 5 推荐
- **城市热门景点发现** — 模糊查询自动注入高德热门景点 + 精选地标兜底
- **模糊查询友好** — "不知道去哪玩"自动推荐城市必去景点

### 交互式地图编辑
- **路段交通标注** — hover 显示交通方式(🚶/🚇/🚌)+ 时间，不同颜色不同交通
- **推荐 POI 可选** — 虚线圆圈标记备选 POI，点击加入路线
- **拖拽排序** — 侧边栏 HTML5 DnD 自由调整游览顺序
- **颜色图例** — 地图左上角自动显示品类颜色图例
- **确认路线** — 确认后生成 LLM 个性化解说 + Mermaid 路线图

### 个性化 & 稳定性
- **用户画像学习** — 从完成路线自动学习品类/区域/预算偏好
- **偏好注入** — 用户偏好自动注入 LLM context，越用越准
- **Session TTL** — 24 小时自动过期 + 后台清理
- **LLM 自动重试** — 指数退避 + 随机抖动 + 错误分类
- **输入校验** — 空查询/无效参数统一返回 422

## 架构

```
用户输入
  │
  ▼
┌─────────────────────────────────────────┐
│  orchestrator.py                        │
│  统一入口 → run_unified_agent()          │
└─────────────────────────────────────────┘
  │
  ▼
┌─────────────────────────────────────────┐
│  route_agent.py ★核心                    │
│                                         │
│  LLM (系统 prompt + 3 tools)            │
│    │                                    │
│    ├─ geocode(place, city)              │
│    │    → 高德API 4层兜底解析             │
│    │                                    │
│    ├─ query_clusters(origin, dest,      │
│    │    keywords, budget)               │
│    │    → SQLite 走廊聚簇摘要            │
│    │    → projection 排序确保均匀分布     │
│    │    → 热门景点自动注入               │
│    │                                    │
│    └─ build_route(cluster_ids,          │
│         num_stops)                      │
│         → graph_planner 建图+选路径      │
│         → corridor_engine 加载全量 POI   │
│                                         │
│  LLM 生成: 个性化解说                    │
└─────────────────────────────────────────┘
  │
  ▼
┌─────────────────────────────────────────┐
│  Web 交互式编辑                          │
│                                         │
│  ┌──────────┐  ┌───────────────────┐    │
│  │ Leaflet   │  │  侧边栏            │    │
│  │ 地图      │  │  · 路线摘要        │    │
│  │ · 路线    │  │  · 站点列表(可拖拽) │    │
│  │ · 推荐POI │  │  · 备选POI [+加入]  │    │
│  │ · 交通标注│  │  · [确认路线]       │    │
│  │ · 图例    │  │  · 聊天修改         │    │
│  └──────────┘  └───────────────────┘    │
│                                         │
│  确认 → LLM解说 + Mermaid图 + 偏好学习   │
└─────────────────────────────────────────┘
```

**关键设计决策：**
- **LLM 管聚簇，算法管 POI** — 聚簇有语义标签（品类/价格/评分），LLM 可理解；具体 POI 选择和路线计算由算法完成
- **全量 corridor 覆盖** — 用全部查询到的簇（~31 个）加载推荐 POI，而非 LLM 选的 4-5 个
- **分段均匀选取** — 路线分 5 个投影段，每段各取 top 5 推荐 POI
- **全量重规划替代增量修改** — 多轮对话每轮重新规划，比增量修改更鲁棒
- **工具调用替代多 Agent 串行** — 1 次 LLM 调用 + 工具循环，替代原来 4 次 LLM 的 6 步串行管线

## API 端点

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/` | — | HTML 单页应用 |
| GET | `/api/health` | — | 健康检查（DB 状态 + session 数） |
| POST | `/api/plan` | opt | 同步路线规划 |
| POST | `/api/plan/stream` | opt | SSE 流式规划（实时进度） |
| POST | `/api/chat` | opt | 多轮对话修改路线 |
| GET | `/api/route/{id}` | — | 获取路线详情 |
| POST | `/api/route/{id}/select-poi` | — | 添加 POI 到路线 |
| POST | `/api/route/{id}/remove-poi` | — | 从路线移除 POI |
| POST | `/api/route/{id}/reorder` | — | 重排站点顺序 |
| POST | `/api/route/{id}/confirm` | opt | 确认路线（生成解说 + 学习偏好） |
| POST | `/api/route/{id}/search-nearby` | — | 地图点击搜索周边 POI |
| POST | `/api/route/{id}/add-custom` | — | 添加自定义站点 |
| GET | `/api/profile` | opt | 获取用户画像 |
| PUT | `/api/profile` | opt | 更新用户画像 |
| GET | `/api/profile/suggestions` | opt | 个性化推荐 |
| POST | `/api/auth/register` | — | 注册 |
| POST | `/api/auth/login` | — | 登录 |
| GET | `/api/auth/me` | Bearer | 验证 token |

## 使用示例

### Web 交互式规划

```
http://localhost:8000

1. 在输入框输入"从西安北站到曲江转一圈" → 点击规划
2. 地图显示路线（彩色分段 + 交通标注）+ 推荐备选 POI（虚线圆圈）
3. hover 路段 → 显示"🚇 地铁2号线 20min"
4. 点击推荐 POI → 加入路线
5. 拖拽侧边栏站点 → 调整顺序
6. 点击"确认路线" → LLM 个性化解说
```

### Python 多轮对话

```python
from app.core.orchestrator import run_multi_agent

# 第1轮
result, s = run_multi_agent('从丈八六路出发去钟楼吃火锅')

# 第2轮: 改终点
result2, s = run_multi_agent('不去钟楼了，去大雁塔', session=s)

# 第3轮: 改偏好
result3, s = run_multi_agent('要高档的，换日料', session=s)
```

## 配置

| 变量 | 用途 | 默认值 |
|------|------|--------|
| `LLM_API_KEY` | LLM API Key | — |
| `LLM_BASE_URL` | LLM API 地址 | `https://api.longcat.chat/anthropic` |
| `LLM_MODEL` | 模型名 | `LongCat-Flash-Lite` |
| `AMAP_API_KEY` | 高德开放平台 Key | — |
| `JWT_SECRET` | JWT 密钥 | `jwt-secret-change-me` |
| `SESSION_TTL_HOURS` | Session 过期时间 | 24 |
| `LOG_LEVEL` | 日志级别 | info |
| `PORT` | 服务端口 | 8000 |

## 目录结构

```
my-first-app/
├── main.py                        # CLI 多轮对话入口
├── app/                           # 主 Python 包
│   ├── config.py                  # 环境变量读取
│   ├── llm_client.py              # LLM API 客户端（自动重试+抖动）
│   ├── models.py                  # Pydantic 数据模型
│   ├── user_profile.py            # 用户画像持久化 + 偏好学习
│   ├── core/                      # 核心编排
│   │   ├── orchestrator.py        # 入口
│   │   ├── route_agent.py         # ★ 统一 Agent — LLM 工具调用循环
│   │   └── types.py               # Agent 间通信类型
│   ├── pipeline/                  # 数据处理管线
│   │   ├── cluster_tools.py       # ★ 工具定义 + 实现 + 热门景点注入
│   │   ├── corridor_engine.py     # ★ 走廊 POI 加载 + 投影 + 包络
│   │   ├── reason_engine.py       # POI 推荐理由生成
│   │   └── constraint_checker.py  # 约束校验
│   ├── providers/                 # 数据源
│   │   └── amap_provider.py       # 高德 API（搜索/地理编码/逆地理/路线）
│   ├── algorithms/                # 纯算法
│   │   ├── geo.py                 # Haversine / 投影比例
│   │   ├── graph_planner.py       # 建图 + 最短路径
│   │   ├── poi_filter.py          # POI 过滤去重
│   │   └── routing.py             # 高德步行/公交/骑行/驾车 API
│   └── shared/                    # 共享工具
│       ├── constants.py           # 关键词规范化 / 黑名单 / 城市地标
│       └── utils.py               # 城市提取 / Mermaid / AgentSession
├── db/                            # 数据库层
│   ├── connection.py              # SQLite WAL + context manager
│   ├── cluster.py                 # 聚类预计算 + 走廊查询 + 投影排序
│   └── repository.py              # POI 查询
├── web/                           # FastAPI 网站
│   ├── server.py                  # 18 端点 + SSE + session 管理 + 偏好学习
│   ├── templates/index.html       # 单页 UI
│   └── static/
│       ├── css/style.css          # 完整样式（图例/拖拽/错误/弹窗）
│       └── js/
│           ├── app.js             # 主控制器
│           ├── map.js             # ★ Leaflet 地图（分段渲染+图例+hover tooltip）
│           ├── route-editor.js    # ★ 交互编辑（拖拽排序/POI选取/确认路线）
│           ├── corridor.js        # 走廊渲染
│           └── utils.js           # 工具函数
├── tests/                         # 测试
├── docs/                          # 文档
│   ├── CONTEXT.md                 # 项目上下文
│   ├── ARCHITECTURE.md            # 架构文档
│   ├── landing-optimization.md    # ★ 落地优化报告 (2026-05-09)
│   └── claude-code-modes.md       # Claude Code 模式指南
├── scripts/
│   └── deploy.sh                  # 一键部署脚本
├── Dockerfile                     # Docker 镜像
├── docker-compose.yml             # 容器编排
└── data/                          # 运行时数据
    ├── pois.db                    # SQLite POI (14,222条)
    ├── sessions/                  # 会话持久化
    ├── users/                     # 用户画像 + 偏好学习
    └── output/                    # 路线输出文件
```

## 运行测试

```bash
# 所有测试调真实 API，会消耗配额
python3 tests/test_user_scenarios.py    # 场景回归
python3 tests/test_phase5_eval.py       # 综合评估（8用户×多轮）
python3 tests/test_friends.py           # 特殊需求测试（10用户）
python3 tests/test_graph.py             # 单条路线集成测试
python3 tests/test_benchmark.py         # DB vs Amap 性能基准

# 数据库维护
python3 -m db.cluster stats             # 聚类统计
python3 -m db.cluster build             # 全量重算聚类
```

## 技术栈

| 层级 | 技术 |
|------|------|
| LLM | LongCat (Anthropic 兼容 API) — tool_use / system prompt / 自动重试 |
| 地图 | 高德开放平台 — 地理编码 / POI 搜索 / 步行路径 / 逆地理编码 |
| 数据库 | SQLite + Haversine — 14,222 条西安 POI / 995 个聚类簇 |
| Web | FastAPI + Jinja2 + SSE 流式推送 + fcntl 文件锁 |
| 前端 | Leaflet.js 地图 + Mermaid.js + HTML5 DnD |
| 聚类 | 网格法预计算 + 走廊投影排序 + 杂项过滤 |
| 部署 | Docker + docker-compose + 一键部署 + 健康检查 |
| 个性化 | 画像学习 (品类/区域/预算) + LLM context 注入 |
