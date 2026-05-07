# 架构文档 — "现在就出发" AI 路线规划

> 面向程序员的落地指南：项目怎么跑起来、数据在哪、接口有哪些、如何扩展。

---

## 1. 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 API Key
cp .env.example .env
# 编辑 .env，填入 LLM_API_KEY 和 AMAP_API_KEY

# 3. 启动 CLI
python main.py

# 4. 命令行跑一条路线
python3 -c "
import sys; sys.path.insert(0, '.')
from agent.multi_agent.orchestrator import run_multi_agent
result, session = run_multi_agent('从丈八六路地铁站出发去钟楼吃', user_id='default')
print(result)
"

# 5. 运行测试
python3 test_user_scenarios.py    # 3 用户画像快速回归
python3 test_ux_deep.py           # 5 用户画像深度体验
```

---

## 2. 目录结构

```
/root/my-first-app/
├── .env                          # API Key（不提交）
├── .env.example                  # .env 模板
├── requirements.txt              # Python 依赖
├── main.py                       # CLI 入口
├── ARCHITECTURE.md               # ← 本文档
├── CONTEXT.md                    # 踩坑记录 + 历史决策
│
├── agent/
│   ├── config.py                 # 读 .env，导出 LLM_API_KEY / AMAP_API_KEY
│   ├── llm_client.py             # ★ 共享 LLM 客户端（所有 Agent 共用）
│   ├── models.py                 # 公共 Pydantic 模型：POI, RouteStop, Route, UserIntent
│   ├── core.py                   # 旧 pipeline + 公共函数（_extract_city, _build_mermaid 等）
│   ├── user_profile.py           # ★ 用户画像管理器（持久化、压缩、session）
│   │
│   ├── multi_agent/              # ★ 多智能体架构
│   │   ├── types.py              # Agent 间通信数据结构
│   │   ├── orchestrator.py       # ★ 主控：Plan-Execute-Review-Refine + 多轮对话
│   │   ├── intent_agent.py       # 意图理解 Agent
│   │   ├── poi_strategy_agent.py # POI 搜索策略 + 质量评估 Agent
│   │   ├── narrator_agent.py     # 个性化解说 Agent
│   │   ├── reviewer_agent.py     # 路线质量审核 Agent
│   │   └── modifier_agent.py     # ★ 修改意图识别 Agent（多轮对话）
│   │
│   ├── tools/
│   │   ├── constants.py          # 共享常量（关键词映射、黑名单、占位符）
│   │   ├── geo.py                # 几何工具（haversine, project_ratio）
│   │   ├── poi.py                # ★ 高德 API 封装（search_poi, geocode, robust_geocode）
│   │   ├── poi_filter.py         # POI 过滤工具（去重、品类、坐标、距离）
│   │   ├── graph_planner.py      # ★ 图算法（build_graph + shortest_path）
│   │   ├── routing.py            # 步行距离计算（高德 walking API）
│   │   └── reviews.py            # 点评 API 骨架
│   │
│   └── static/                   # Leaflet HTML 模板
│
├── test/                         # 集成测试
├── users/                        # 用户画像文件（不提交）→ users/{user_id}.json
├── route_output.md               # 最后一次规划的 Mermaid 图
└── route_output.html             # 最后一次规划的 Leaflet 地图
```

---

## 3. 核心数据流

### 3.1 多智能体 pipeline（完整规划）

```
用户输入 "从丈八六路出发去浐灞玩，3小时"
  │
  ├─ 1. 城市提取      _extract_city() → regex 匹配 300+ 城市列表
  ├─ 2. Intent Agent   LLM 深度解析 → IntentResult
  │      ├─ origin / destination / keywords / num_stops
  │      ├─ UserProfile (group_type, energy_level, budget_level, interests)
  │      └─ time_budget_hours, search_hints
  ├─ 3. 地理编码       robust_geocode() → 4 层兜底 + 正则回退
  ├─ 4. POI Strategy   build_search_strategy() → 搜索区域 + 关键词
  │      └─ 执行搜索 → Amap text/around/along_route API
  │      └─ POI 质量评估 → 不足时自动补搜
  ├─ 5. Route Engine   build_graph() + shortest_path()
  │      └─ 全连接图 → 连线投影分段选取 → 200m 最小间距
  │      └─ 时间预算约束（超出 20% 自动减站）
  ├─ 6. Narrator Agent LLM 个性化解说
  ├─ 7. Reviewer Agent 质量审核（最多 2 轮 refine）
  │      └─ 不通过 → 调整搜索策略 → 重新规划
  └─ 8. Output
       ├─ 文本回复
       ├─ Mermaid 路线图 → route_output.md
       ├─ Leaflet 地图 → route_output.html
       └─ 保存用户画像 → users/{user_id}.json
```

### 3.2 多轮对话流程

```
用户第2轮输入 "不去钟楼了，换成大雁塔"
  │
  ├─ modifier_agent.detect_modification()
  │     ├─ 规则匹配优先（正则，8 类修改意图）
  │     └─ LLM 兜底（模糊表达）
  │
  ├─ 识别为 change_destination
  │     └─ 只重跑：geocode → POI 搜索 → 建图 → 解说 → 审核
  │
  ├─ 其他修改类型：
  │     ├─ change_origin        → geocode + 重新搜索 + 建图
  │     ├─ change_keywords      → 重新搜索 + 建图
  │     ├─ change_num_stops     → 只重建图
  │     ├─ change_preferences   → 更新画像 + 重新解说
  │     ├─ change_poi_location  → 新区域搜索 + 建图
  │     ├─ adjust_constraint    → 重新建图（时间约束）
  │     └─ new_route            → 完整重规划
  │
  └─ 每轮结束保存 session + history + favorites
```

---

## 4. 关键接口

### 4.1 主入口 —— `run_multi_agent()`

```python
from agent.multi_agent.orchestrator import run_multi_agent

# 新用户 / 第一轮对话
result_text, session = run_multi_agent(
    user_input="从丈八六路出发去钟楼吃",
    user_id="default",      # 后续接入登录后改为 login_state.user_id
)

# 多轮对话（同一 session）
result_text, session = run_multi_agent(
    user_input="不去钟楼了，去大雁塔",
    session=session,        # 传入上一轮的 session
    user_id="default",
)
```

### 4.2 用户画像管理 —— `UserProfileManager`

```python
from agent.user_profile import UserProfileManager

mgr = UserProfileManager(user_id="default")

# 读画像
data = mgr.load()           # → dict，文件不存在时自动创建默认画像

# 更新画像字段
from agent.multi_agent.types import UserProfile
profile = UserProfile(
    group_type="family",
    energy_level="low",
    interests=["美食", "亲子"],
    notes="带小孩，需要无障碍通道",
)
mgr.update_profile(profile)

# 收藏管理
mgr.add_to_favorites("pois", "回民街")
mgr.add_to_favorites("keywords", "咖啡")

# 历史记录
mgr.add_history({
    "user_input": "从丈八六路出发去浐灞玩",
    "city": "西安",
    "origin": "丈八六路地铁站",
    "destination": "浐灞",
    "stops": ["回民街", "大雁塔"],
    "duration_min": 58,
    "review_score": 4.5,
})

# 会话持久化（跨重启恢复多轮对话）
mgr.save_session({"city": "西安", "origin": "丈八六路", ...})
session_data = mgr.load_session()  # → dict | {}

# 重置用户所有数据
mgr.reset()
```

### 4.3 LLM 客户端 —— `call_llm()`

```python
from agent.llm_client import call_llm

response = call_llm(
    messages=[{"role": "user", "content": "你好"}],
    system="你是一个助手",
    max_tokens=200,
)
# → {"content": [{"text": "..."}], ...}
```

### 4.4 高德 API —— `agent/tools/poi.py`

```python
from agent.tools.poi import (
    search_poi, search_around, search_along_route,
    geocode, robust_geocode, input_tips, AmapAPIError,
)

# 搜索 POI（返回 list[dict]）
pois = search_poi(keywords="美食", location="西安", limit=10)

# 周边搜索
pois = search_around("108.94,34.26", "咖啡", radius=2000)

# 沿途搜索
pois = search_along_route("108.94,34.26", "109.01,34.31", "美食")

# 地理编码（4 层兜底）
lat, lng = robust_geocode("丈八四路地铁站", "西安")

# 异常处理
try:
    pois = search_poi(keywords="xxx", location="xxx")
except AmapAPIError as e:
    print(f"Amap 错误: {e}")
```

### 4.5 图算法 —— `agent/tools/graph_planner.py`

```python
from agent.tools.graph_planner import build_graph, shortest_path

# 建图
nodes, graph = build_graph(origin_coords, poi_list, dest_coords)
# 返回：nodes=[Node, ...], graph={(i,j): (distance_m, duration_s)}

# 最短路径（投影分段选取）
path = shortest_path(graph, nodes, num_stops=3)
# 返回：
# {
#     "segments": [{"from": "起点", "to": "回民街", "distance_m": 3200, "duration_min": 38, "transport": "公交"}, ...],
#     "total_duration_min": 58,
#     "total_distance": 12300,
# }
```

### 4.6 修改意图识别 —— `detect_modification()`

```python
from agent.multi_agent.modifier_agent import detect_modification, ModificationIntent

intent = detect_modification(
    user_input="不去钟楼了，去大雁塔",
    current_context={
        "city": "西安",
        "origin": "丈八六路",
        "destination": "钟楼",
        "num_stops": 3,
        "keywords": "美食,景点",
    },
)

# intent.change_type  → "change_destination"
# intent.params       → {"destination": "大雁塔"}
# intent.confidence   → 1.0（规则匹配）或 0.7（LLM 兜底）
# intent.reasoning    → "规则匹配: ..."
```

---

## 5. 数据存储

### 5.1 用户画像文件

**位置**: `users/{user_id}.json`（例如 `users/default.json`）

**完整结构**:

```json
{
  "user_id": "default",
  "created_at": "2026-05-02T10:00:00+00:00",
  "updated_at": "2026-05-02T12:30:00+00:00",
  "profile": {
    "group_type": "solo",
    "age_preference": "all",
    "energy_level": "medium",
    "budget_level": "medium",
    "interests": ["美食", "文化"],
    "notes": ""
  },
  "favorites": {
    "origins": ["丈八六路地铁站"],
    "destinations": ["钟楼"],
    "pois": ["回民街"],
    "keywords": ["美食", "咖啡"]
  },
  "history": [
    {
      "timestamp": "2026-05-02T10:30:00+00:00",
      "user_input": "从丈八六路出发去浐灞玩",
      "city": "西安",
      "origin": "丈八六路地铁站",
      "destination": "浐灞",
      "stops": ["回民街", "大雁塔"],
      "duration_min": 58,
      "review_score": 4.5
    }
  ],
  "session": {
    "city": "西安",
    "origin": "丈八六路地铁站",
    "origin_coords": [34.261, 108.940],
    "destination": "钟楼",
    "dest_coords": [34.260, 108.942],
    "last_stops": ["回民街", "大雁塔"],
    "num_stops": 3,
    "keywords": "美食,景点",
    "last_user_input": "从丈八六路出发去钟楼吃"
  }
}
```

### 5.2 压缩策略

- 文件上限 **256KB**（`_MAX_FILE_BYTES`）
- 超出时按时间保留最新历史记录（二分查找压缩边界）
- `profile` 和 `session` 不参与压缩（始终保留）
- 收藏列表每类最多 20 条
- 历史记录软上限 100 条

### 5.3 恢复上次会话

```python
mgr = UserProfileManager(user_id="default")
saved = mgr.load_session()
# saved = {"city": "西安", "origin": "丈八六路", ...}
# 可以据此恢复 session 状态，继续多轮对话
```

---

## 6. 配置

`.env` 文件：

| 变量 | 用途 | 示例 |
|------|------|------|
| `LLM_API_KEY` | LongCat / Anthropic API Key | `ak_2R67S24...` |
| `LLM_BASE_URL` | LLM API 地址 | `https://api.longcat.chat/anthropic` |
| `LLM_MODEL` | 模型名 | `LongCat-Flash-Lite` |
| `AMAP_API_KEY` | 高德开放平台 Key | `xxxx` |
| `DIANPING_APP_KEY` | 大众点评 API Key（占位） | — |
| `DIANPING_APP_SECRET` | 大众点评 Secret（占位） | — |

`agent/config.py` 读取这些变量，导出为大写下划线常量。

---

## 7. Agent 间通信协议

所有 Agent 通过 `agent/multi_agent/types.py` 中定义的 Pydantic 模型通信：

```
Intent Agent    → IntentResult (origin, destination, keywords, UserProfile, time_budget_hours)
POI Strategy    → SearchStrategy (List[SearchRegion]), PoiQualityReport
Route Engine    → dict (path_result from graph_planner)
Narrator Agent  ← NarrationContext (path info + user_profile)
Reviewer Agent  → ReviewResult (overall_score, List[ReviewIssue], needs_retry)
Modifier Agent  → ModificationIntent (change_type, params, confidence)
```

---

## 8. 如何扩展

### 8.1 接入登录系统

只需改一行：

```python
# 现在
mgr = UserProfileManager(user_id="default")

# 接入后
mgr = UserProfileManager(user_id=login_state.user_id)
```

`UserProfileManager` 已内置完整接口文档（见文件头部 docstring），无需修改任何其他代码。

### 8.2 添加新的修改意图类型

在 `agent/multi_agent/modifier_agent.py` 中：

1. 添加正则规则到 `_ALL_RULES`
2. 在 `detect_by_rules()` 中添加参数提取逻辑
3. 在 `orchestrator.py` 的 `_run_modification()` 中添加处理分支

### 8.3 添加新的 Agent

1. 在 `agent/multi_agent/` 下创建 `xxx_agent.py`
2. 在 `types.py` 中定义输入输出 Pydantic 模型
3. 在 `orchestrator.py` 中引入并编排到 pipeline

### 8.4 接入点评/美团 API

在 `agent/tools/reviews.py` 的 `fetch_reviews()` 骨架中实现，然后在 `poi_strategy_agent.py` 中调用，丰富 POI 评分来源。

---

## 9. 常用命令

```bash
# 运行 CLI
python main.py

# 测试
python3 test_user_scenarios.py    # 3 画像快速回归
python3 test_ux_deep.py           # 5 画像深度体验
python3 test_graph.py             # 单条路线集成测试
python3 test_longcat.py           # LLM API 连通性
python3 test_amap.py              # 高德 API 连通性

# 单独测试 geocode
python3 -c "
import sys; sys.path.insert(0, '.')
from agent.tools.poi import robust_geocode
print(robust_geocode('丈八四路地铁站', '西安'))
"

# 查看输出
cat route_output.md
python3 -m http.server 8000       # 浏览器打开 localhost:8000/route_output.html
```

---

*最后更新：2026-05-02*
