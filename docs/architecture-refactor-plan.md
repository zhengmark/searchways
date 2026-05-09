# 架构大改：交互式路线规划器 — 修改文档

> **实施进度**: Phase 1 ✅ | Phase 2 ✅ | Phase 3 ⏳ | Phase 4 ⏳ | Phase 5 ⏳ | Phase 6 ✅

## 概述

将当前"AI 单次出结果 → 展示"升级为"AI 建议 → 用户交互式编辑 → 确认"模式。

---

## Phase 1: 基础设施修复（不用改架构，先修坑）

### 1.1 `app/algorithms/poi_filter.py` — 修复 ImportError

**改什么**：文件末尾加一行别名

```python
dedup_pois = deduplicate_by_name
```

**为什么**：`cluster_tools.py` 第 287 行 `from app.algorithms.poi_filter import dedup_pois` 导入的函数不存在（实际函数叫 `deduplicate_by_name`），Amap fallback 路径触发时直接崩溃。阿里命名风格变动遗留的 bug。

**影响范围**：零风险，只是一个别名。

---

### 1.2 `app/shared/utils.py` — 修复 `_progress_callback` 并发竞态

**改什么**：把全局变量 `_progress_callback = None` 和 `_progress()` 改为通过函数参数传递：

```python
# 旧（全局变量，并发不安全）
_progress_callback = None
def _progress(emoji, msg):
    if _progress_callback:
        _progress_callback(emoji, msg)

# 新（参数传递，线程安全）
def _progress(emoji, msg, callback=None):
    if callback:
        callback(emoji, msg)
    else:
        print(f"  {emoji}  {msg}")
```

同时在 `route_agent.py` 的 `run_unified_agent()` 签名中增加 `progress_callback=None` 参数，透传给各处 `_progress()` 调用。

**为什么**：当前 `server.py` 把回调函数写入模块级全局变量，`ThreadPoolExecutor(max_workers=2)` 下两个并发 SSE 请求会相互覆盖对方的回调，导致 A 用户的进度推送到 B 用户的 SSE 流。这不是"可能出问题"，而是两个用户同时用就必然出问题。

**影响范围**：需要改动 `utils.py`、`route_agent.py`、`cluster_tools.py`、`orchestrator.py`、`server.py` 中所有 `_progress()` 调用点。

---

### 1.3 `web/server.py` — pickle → JSON 序列化

**改什么**：
- `AgentSession` 增加 `to_dict()` / `from_dict()` 方法
- `_load_sessions()` 和保存逻辑从 `pickle.load/dump` 改为 `json.load/dump`
- 文件扩展名从 `.pkl` 改为 `.json`

**为什么**：
1. pickle 不安全（反序列化可以执行任意代码）
2. pickle 跨 Python 版本不兼容（升级 Python 后旧 session 全部失效）
3. pickle 不可调试（无法直接查看 session 内容）
4. `AgentSession` 全部字段都是可 JSON 序列化的基础类型（str/int/float/list/dict/tuple），不需要 pickle

**影响范围**：`utils.py` 的 `AgentSession` + `server.py` 的 session 读写。外部接口不变。

---

### 1.4 AgentSession 扩展

**文件**: `app/shared/utils.py`

**改什么**：在 `AgentSession.__init__` 中新增以下字段：

```python
# 走廊数据（Phase 2 产出）
self.corridor_pois: list = []         # 走廊内所有候选 POI
self.corridor_clusters: list = []     # 簇中心坐标（画椭圆用）
self.corridor_shape: list = []        # 类椭圆包络多边形 [[lat,lng],...]

# 用户交互状态
self.selected_poi_ids: list = []      # 用户确认选中的 POI ID
self.removed_poi_ids: list = []       # 用户主动移除的 POI ID
self.route_confirmed: bool = False    # 是否已点"确认路线"

# 性能优化
self.graph_data: dict = None          # 序列化图（避免重复 ~80 次 API 调用）

# 推荐理由
self.recommendation_reasons: dict = {}  # {poi_id: {structured, user_need}}

# 交通偏好
self.transit_preferences: dict = {}   # {mode: str, avoid_transfers: bool}
```

**为什么**：Phase 2-4 全靠这些字段在前后端之间传递交互状态。不加这些字段，后续每个新功能都得另起存储。

**影响范围**：`utils.py` 的 `AgentSession.__init__` + `to_dict/from_dict`。现有逻辑完全兼容（新字段有默认值）。

---

### 1.5 `app/models.py` — 新增 Pydantic 模型

**改什么**：文件末尾新增：

```python
class CorridorPoi(BaseModel):
    """走廊候选 POI"""
    id: str
    name: str
    lat: float
    lng: float
    category: str = ""
    rating: float | None = None
    price_per_person: float | None = None
    address: str = ""
    cluster_id: int = 0
    projection_ratio: float = 0.0   # 沿 OD 轴投影比例 [0,1]
    perpendicular_km: float = 0.0   # 偏离 OD 轴距离(km)
    recommendation_reasons: dict = {}  # {structured: str, user_need: str}
    selected: bool = False

class SelectPoiRequest(BaseModel):
    poi_id: str

class ConnectPoiRequest(BaseModel):
    from_poi_id: str
    to_poi_id: str
    mode: str = "auto"  # walk | bike | transit | drive | auto

class ReorderRequest(BaseModel):
    poi_ids: list[str]  # 新的顺序

class TransitQueryRequest(BaseModel):
    from_lat: float
    from_lng: float
    to_lat: float
    to_lng: float
```

**为什么**：FastAPI 依赖 Pydantic 做请求验证和响应序列化。Phase 4 的新端点需要这些模型。

**影响范围**：纯新增，不影响现有模型。

---

## Phase 2: 走廊引擎

### 2.1 新文件 `app/pipeline/corridor_engine.py`

**为什么新建这个文件**：当前 `query_clusters` 只返回簇级别的摘要（每个簇 10 个聚合字段），前端拿不到单个 POI 的坐标/评分/价格。`build_route` 虽然是算法选 POI，但它把"落选的 POI"丢掉了。需要一个专门模块负责：
1. 加载簇内全部 POI
2. 计算它们在 OD 轴上的投影位置（用于前端按位置排列）
3. 计算走廊包络形状（那个"类椭圆"）
4. 调用推荐理由引擎

**核心函数**：

```python
def build_corridor(
    origin_coords: tuple[float, float],
    dest_coords: tuple[float, float],
    cluster_ids: list[int],
    keywords: list[str] = None,
    budget: str = None,
    corridor_width_km: float = 5.0,
) -> dict:
```

**内部逻辑**：
1. `SELECT * FROM pois WHERE cluster_id IN (...)` 加载选中簇的全部 POI
2. 对每个 POI 用 `geo.project_ratio()` 算投影比例（0=靠近起点，1=靠近终点）
3. 用 `geo.haversine()` + 投影几何算偏离 OD 轴的垂直距离
4. 调用 `reason_engine.generate_poi_reasons()` 生成每个 POI 的推荐理由
5. 调用 `compute_corridor_shape()` 计算包络多边形
6. 返回 `{corridor_pois, cluster_markers, corridor_shape}`

**为什么用 `project_ratio`**：前端可以根据这个值把 POI 从左到右（沿 OD 方向）排列，形成自然的"沿路分布"视觉效果。

```python
def compute_corridor_shape(
    cluster_centers: list[dict],
    origin: tuple[float, float],
    dest: tuple[float, float],
    padding_km: float = 2.0,
) -> list[list[float]]:
```

**逻辑**：取所有簇中心在 OD 轴上的投影，构建一个围绕 OD 线的缓冲矩形/多边形。不是数学意义上的椭圆拟合，而是空间上自然形成椭圆视觉效果（簇中心散布在 OD 线两侧 + 缓冲膨胀）。

**为什么不是真椭圆拟合**：真椭圆拟合（最小包围椭圆/MBE）算法复杂度高且不稳定（簇中心可能共线）。缓冲矩形在视觉上已经足够"类椭圆"，Leaflet 渲染也简单。

---

### 2.2 新文件 `app/pipeline/reason_engine.py`

**为什么新建这个文件**：推荐理由是交互的核心——用户悬停 POI 时看到的信息决定了是否选择它。理由分两部分：
- **结构化理由**：算法直接算出来的客观数据（距离/评分/价格/品类）
- **用户需求理由**：这个 POI 为什么匹配你（关键词匹配 + 预算匹配）

**核心函数**：

```python
def generate_poi_reasons(
    poi: dict,
    keywords: list[str],
    budget: str,
    origin_coords: tuple[float, float],
    user_profile: dict = None,
) -> dict[str, str]:
```

**结构化理由模板**：
```
"评分4.5 | 人均80元 | 陕菜 > 泡馍 | 距起点1.2km"
```

**用户需求理由模板**（基于规则匹配，不用 LLM）：
```
"完美匹配您的'美食'偏好，是回民街区域评分最高的泡馍馆"
"适合亲子的室内场所，人均在您的预算范围内"
"评分虽然不高但距起点最近，适合快速补给"
```

**为什么不用 LLM 生成理由**：hover tooltip 需要 <100ms 延迟。LLM 调用至少 1-3 秒。模板匹配在这个场景下足够好。后续可批量 LLM 增强。

**匹配规则**（复用 `cluster_tools.py` 中已有的 `_KEYWORD_CATEGORY_MAP` 和黑名单逻辑）：
- 关键词命中 POI 品类 → "匹配您的'{关键词}'偏好"
- 评分 > 4.5 → "该区域评分最高的{品类}"
- 价格在预算范围内 → "在您的{预算}预算范围内"
- 距起点 < 1km → "距离起点最近的选择之一"

---

### 2.3 修改 `app/pipeline/cluster_tools.py`

**改什么**：在 `tool_build_route()` 成功返回后，追加调用：

```python
# 原代码返回前，新增：
from app.pipeline.corridor_engine import build_corridor
corridor_data = build_corridor(
    origin_coords, dest_coords, cluster_ids, keywords, budget
)
agent_state["corridor_data"] = corridor_data
```

**为什么**：最小化对现有 LLM 工具调用循环的侵入。`agent_state` 是跨工具调用共享的 dict，`corridor_data` 随着 `agent_state` 最终存入 session。

---

## Phase 3: 交通 API 接入

### 3.1 `app/providers/amap_provider.py` — 新增 3 个 API 函数

**为什么**：当前系统只用步行 API。骑行/公交/驾车的时间全部是 `步行时间 / 速度系数`（比如公交 = 步行 / 2），完全不真实。Amap 的公交/骑行/驾车 API 使用同一个 Key，不需要额外注册。

**新增**：

```python
AMAP_TRANSIT_API = "https://restapi.amap.com/v3/direction/transit/integrated"
AMAP_BIKING_API  = "https://restapi.amap.com/v4/direction/bicycling"
AMAP_DRIVING_API = "https://restapi.amap.com/v3/direction/driving"

def transit_route(origin: str, destination: str, city: str = "西安",
                  strategy: int = 0) -> dict | None:
    """
    origin/destination: "lng,lat"
    strategy: 0=最快, 1=最少换乘, 2=最少步行, 5=不坐地铁, 6=优先地铁
    返回:
      {"success": True, "distance": int(m), "duration": int(sec),
       "cost": float(元), "steps": [
         {"mode": "bus"/"metro"/"walk", "line_name": str,
          "start_stop": str, "end_stop": str,
          "station_count": int, "duration": int(sec)}, ...]}
    """

def biking_route(origin: str, destination: str) -> dict | None:
    """同上结构，返回骑行路线"""

def driving_route(origin: str, destination: str) -> dict | None:
    """同上结构，返回驾车路线（含 toll/cost）"""
```

每个函数封装 `_retry_request` 和错误处理，失败返回 `None`（调用方降级到速度系数估算）。

**关于"真实时刻表"的说明**：Amap 标准 transit API 返回的是基于静态时刻表的**估算时间**，不是实时 GPS 位置。要做到"下一班 3 分钟后到站"，需要 Amap 企业级公交 API（`/v3/bus/realtime`），需要企业 Key。代码中把 API 调用封装为可替换的接口，后续换 Key 只需改一行 URL。

---

### 3.2 `app/algorithms/routing.py` — 重构为多模式路由

**改什么**：
1. 重命名 `walk_distance()` → `get_walking_route()`，统一返回格式
2. 新增 `get_transit_route()`、`get_biking_route()`、`get_driving_route()` 封装
3. 新增 `preview_connection(origin_coords, dest_coords) -> dict`（两点连线预览，Phase 4 使用）
4. 新增 API 调用缓存（`@functools.lru_cache` 或 dict memoize 按坐标对，30s TTL）

**`preview_connection()` 逻辑**：
1. 算 haversine 距离
2. 根据距离自动决定交通模式
3. 调用对应 API
4. 返回 `{mode, distance, duration, steps, cost}`

**为什么**：前端的"连接两个 POI"交互需要即时交通信息。这个函数被 Phase 4 的 `/api/route/{sid}/connect` 端点调用。

---

### 3.3 `app/algorithms/graph_planner.py` — 替换速度系数

**改什么**：`build_graph()` 中的 `_fetch()` 函数（当前为每个节点对调用 `walk_distance()` 再用 `_SPEED_FACTOR` 缩放）：

```python
# 旧逻辑
walk_result = walk_distance(origin_str, dest_str)
if walk_result:
    walk_duration = walk_result["duration"]
    transit_duration = int(walk_duration / 2)  # 完全不准确

# 新逻辑
distance = haversine(...)
mode = decide_transport(distance)
if mode == "步行":
    result = get_walking_route(origin_str, dest_str)
elif mode == "骑行":
    result = get_biking_route(origin_str, dest_str)
elif mode == "公交/地铁":
    result = get_transit_route(origin_str, dest_str)
else:
    result = get_driving_route(origin_str, dest_str)
```

**缓存**：每个坐标对只调一次 API，结果按 `(lng1,lat1,lng2,lat2)` memoize。

**API 调用量控制**：`_K_NEAREST = 8`（每个节点最多 8 条边调 API）保留不变。距离 > 3km 的边仍用 haversine 估算 + 1.4 倍系数（这些边的交通模式不再用速度系数，而是直接用 haversine * 1.4 作为驾车估算）。

**为什么**：关键改动。当前"公交 = 步行 / 2"的估算偏差可达 3-5 倍（比如步行 30 分钟的路，公交可能 10 分钟也可能 40 分钟取决于线路）。真实 API 给出有换乘信息的具体路线，对用户更有用。

---

## Phase 4: 新 API 端点

### `web/server.py` — 新增 7 个端点

#### 4.1 `GET /api/route/{session_id}`

**逻辑**：加载 session，返回 `{session_id, city, start_name, dest_name, stops, stats, corridor_pois, corridor_shape, route_confirmed}`

**为什么**：前端进入编辑模式后需要完整上下文。

#### 4.2 `POST /api/route/{session_id}/select-poi`

**入参**：`{poi_id: str}`
**逻辑**：
1. 加载 session，从 DB 获取 POI 完整信息
2. 加入 `session.selected_poi_ids`
3. 重新跑 `shortest_path()`（复用 `session.graph_data` 避免重建图）
4. 保存 session，返回 `{stops, segments, stats}`

**为什么**：用户点击一个 POI 后，路线要实时更新。如果重建图（~80 次 API 调用）需要 10+ 秒，复用序列化图可以毫秒级完成。

#### 4.3 `POST /api/route/{session_id}/remove-poi`

**入参**：`{poi_id: str}`
**逻辑**：同 select-poi 但反向，从 `selected_poi_ids` 移除，加入 `removed_poi_ids`。

**为什么**：对等操作。`removed_poi_ids` 独立记录避免用户反复选/删同一 POI 时的状态丢失。

#### 4.4 `POST /api/route/{session_id}/connect`

**入参**：`{from_poi_id, to_poi_id, mode}`
**逻辑**：
1. 从 DB/缓存获取两个 POI 的坐标
2. 调用 `routing.preview_connection(coords1, coords2)`
3. 返回 `{from_name, to_name, mode, distance, duration, steps, cost}`

**为什么**：用户在地图上把两个 POI 连起来看交通方式。

#### 4.5 `POST /api/route/{session_id}/reorder`

**入参**：`{poi_ids: [str, ...]}`
**逻辑**：重新排列 `session.selected_poi_ids` 顺序，重新跑 `shortest_path()`。

**为什么**：前端侧边栏拖拽重排序 stop 列表。

#### 4.6 `POST /api/route/{session_id}/confirm`

**入参**：无（或 `{notes: str}` 额外备注）
**逻辑**：
1. 从 session 取最终 stops 列表
2. 调用 narrator agent（单次 LLM，无工具调用）
3. 服务端从 stops+segments 数据直接生成 Mermaid 图
4. 设 `session.route_confirmed = True`
5. 返回 `{narration, mermaid, stops, segments, stats}`

**为什么**：确认后的解说是一次性的、无交互的 LLM 调用，不需要工具循环。Mermaid 从数据生成比从 LLM 输出正则提取更可靠。

#### 4.7 `GET /api/route/{session_id}/transit`

**Query params**: `from_lat, from_lng, to_lat, to_lng, mode=auto`
**逻辑**：调用对应 Amap API，返回交通详情。

**为什么**：前端悬停连接线时需要查询交通信息。独立端点因为这是一个高频操作（每次 hover 不同连线都要查）。

#### 4.8 修改 `_build_response()` 和 SSE

`POST /api/plan/stream` 的结果事件中增加 `corridor_pois`、`corridor_shape`、`cluster_markers` 字段（当 session 中存在这些数据时）。

---

## Phase 5: 前端重构

### 5.1 为什么拆文件

当前 `web/templates/index.html` 有 ~400 行内联 JS，全部变量是全局的（`map`、`sessionId`、`lastResultData`、`progressCount`）。交互式地图需要管理 20+ 个状态变量，全局变量方式不可维护。

拆成 ES 模块：
- 每个文件一个职责
- `app.js` 集中管理状态
- 模块间通过 `AppState` 对象通信

### 5.2 文件创建计划

#### `web/static/js/utils.js` (~50行)

从 `index.html` 抽出：`$()` 选择器、`renderMarkdown()`、`formatDuration()`、`formatDistance()`。

#### `web/static/js/app.js` (~100行)

集中状态管理：

```javascript
export const AppState = {
    sessionId: null,
    username: null,
    route: { stops: [], segments: [], stats: {} },
    corridor: { pois: [], clusterMarkers: [], shape: null },
    interaction: {
        hoveredPoiId: null,
        selectedPoiIds: new Set(),
        connectionMode: false,
        connectionFrom: null,
    },
    ui: { mode: 'idle' },  // 'idle' | 'planning' | 'editing' | 'confirmed'
};

export function updateState(path, value) {
    // 深路径更新 + 触发相关模块 re-render
}
```

事件绑定：Plan 按钮、快速标签、Enter 键、历史列表。

#### `web/static/js/ui.js` (~80行)

从 `index.html` 抽出：进度条动画、骨架屏、统计栏更新、违规卡片、错误提示、模态弹窗。

#### `web/static/js/mermaid-renderer.js` (~30行)

从 `index.html` 抽出：`renderMermaid(code)`。

#### `web/static/js/chat.js` (~80行)

从 `index.html` 抽出：SSE 流读取、`planRoute()`、`modifyRoute()`。

**改进**：SSE 结果处理中增加 corridor data 检测，有则调用 `corridor.js` 进入编辑模式。

#### `web/static/js/map.js` (~200行)

核心地图控制器：

```javascript
let map = null;  // Leaflet 实例（模块作用域）

export function initMap(containerId)     // 初始化/返回单例
export function clearAllLayers()         // 清除所有覆盖物
export function renderStops(stops)       // 起/终/中停点标记
export function renderRoutePolyline(segments) // 路线折线
export function fitBounds(coords)        // 自适应缩放
export function getMap()                 // 获取实例
```

**改动点**：
- 标记增加 `click` 和 `mouseover/mouseout` 事件（poiId 存在 data 属性中）
- 线段增加 hover tooltip

#### `web/static/js/corridor.js` (~150行)

走廊层渲染：

```javascript
export function renderCorridorShape(shapeCoords)  // 半透明多边形
export function renderClusterMarkers(clusters)    // 簇中心大圆
export function renderCorridorPois(pois, onHover, onClick) // POI 小圆
export function highlightPoi(poiId)               // 悬停高亮
export function selectPoi(poiId)                  // 选中样式
export function showPoiTooltip(poi, latlng)       // 富 tooltip
```

**POI 颜色编码**（按品类）：
- 餐饮 → `#ef4444` 红
- 咖啡/茶馆 → `#a0522d` 棕
- 景点/文化 → `#22c55e` 绿
- 购物/商场 → `#3b82f6` 蓝
- 其他 → `#6b7280` 灰

**Tooltip 内容**（自定义 Leaflet popup 或 `L.tooltip`）：
```
┌─────────────────────────────┐
│ ★ 4.5  回民街老米家泡馍       │
│ 评分4.5 | 人均80元 | 陕菜     │
│                             │
│ 完美匹配您的'美食'偏好，      │
│ 是回民街区域评分最高的泡馍馆   │
│                             │
│ [加入路线]  [从这里开始连线]   │
└─────────────────────────────┘
```

#### `web/static/js/route-editor.js` (~100行)

路线编辑逻辑：

```javascript
export function enableEditMode(sessionId, corridorData)
export function onPoiClick(poiId)
export function onRemovePoi(poiId)
export function toggleConnectionMode()
export function onConfirmRoute()
export function onReorder(newOrder)
export function updateSidebar(stops, segments, stats)
```

**连线模式交互**：用户点击"连线模式"按钮 → 地图光标变 crosshair → 点第一个 POI → 点第二个 POI → 调 `/api/route/{sid}/connect` → 画虚线 + 显示交通信息 tooltip。

### 5.3 HTML 结构重写

**文件**: `web/templates/index.html`

**主要变化**：
1. 移除所有内联 `<script>`，替换为 `<script type="module" src="/static/js/app.js">`
2. 结果区从两栏网格改为全屏仪表盘布局
3. 新增侧边栏：路线摘要 + stop 列表 + 按钮组
4. 新增确认后的全宽解说面板

**改后的 DOM 骨架**：
```html
<div id="app">
  <!-- 头部 + 登录按钮 -->
  <header>...</header>

  <!-- 输入区（可折叠） -->
  <section id="inputArea">
    <textarea id="userInput" ...>
    <div id="quickTags">...</div>
    <button id="planBtn">规划</button>
  </section>

  <!-- 进度条 -->
  <div id="progressBar">...</div>
  <!-- 骨架屏 -->
  <div id="skeletonLoading">...</div>

  <!-- ★ 编辑模式：全屏仪表盘 -->
  <section id="editorArea" class="editor-layout" style="display:none">
    <div id="mapContainer" class="map-main"></div>
    <aside id="sidebar" class="sidebar">
      <div id="routeSummary">...</div>
      <ul id="stopList"></ul>
      <div id="routeStats">...</div>
      <div id="editorToolbar">
        <button id="connectionModeBtn">连线模式</button>
        <button id="confirmBtn">确认路线</button>
      </div>
      <div id="chatInput">...</div>
    </aside>
  </section>

  <!-- 确认模式：结果展示 -->
  <section id="confirmedArea" class="confirmed-layout" style="display:none">
    <div id="confirmedMap" class="map-final"></div>
    <div id="narrationPanel">...</div>
    <div id="mermaidPanel">...</div>
    <div id="finalStats">...</div>
  </section>

  <!-- 历史列表 -->
  <section id="historyArea">...</section>
</div>
```

### 5.4 `web/static/css/style.css` — 新增样式

**新增的主要内容**：
- `.editor-layout`：`display: grid; grid-template-columns: 65fr 35fr; height: calc(100vh - 200px);`
- `.map-main`：地图占满左栏，`min-height: 100%`
- `.sidebar`：右侧可滚动面板
- `.corridor-poi-marker` / `.corridor-poi-selected`：POI 标记状态样式
- `.connection-line`：连线虚线样式（动画虚线）
- `.poi-tooltip`：自定义 popup 样式（比默认 Leaflet popup 更大，有按钮）
- `.confirmed-layout`：确认后的全宽布局
- 响应式：<768px 侧边栏移到地图下方

---

## Phase 6: LLM Prompt 改造

### 6.1 `app/core/route_agent.py` — System Prompt 重写

**为什么改**：当前 prompt 让 LLM 在 `build_route` 成功后直接写详细解说。现在分为两阶段：
- Phase 1 只出路线预览（2-3 句）
- Phase 2（用户确认后）出详细解说

**Phase 1 Prompt 核心改动**：

```
OLD: 选 3-5 个簇 → build_route → 详细解说
NEW: 选 5-8 个簇 → build_route → 2-3句预览

规则变更：
- query_clusters 可以调 3 次（不变，但要求更多簇）
- build_route 调用后只返回 brief_preview（2-3句描述路线概要）
- 不再写长解说、不再写 Mermaid 图
- 必须保留所有 query_clusters 返回的簇信息（因为前端要展示全部候选 POI）
```

**具体改动位置**：`route_agent.py` 的 `_SYSTEM_PROMPT` 字符串（约 150 行）。

### 6.2 `app/core/narrator_agent.py` — 确认解说 Agent

**当前状态**：文件存在（~300 行）但未被当前流程调用。需要在旧逻辑基础上重写。

**改造内容**：
- 输入：已确认的 route（stops + segments + stats）+ 用户原始需求
- 输出：详细解说（Markdown）+ 实用贴士
- 不调工具，单次 `call_llm()`（无 `call_llm_with_tools`）
- Mermaid 图由服务端从 segments 数据直接生成，不再从 LLM 输出正则提取

**为什么不从 LLM 输出提取 Mermaid**：当前 `_build_response` 用正则 `r"```mermaid\s*\n(.*?)\n```"` 提取 Mermaid。确认后的路线结构已经完全确定，服务端可以直接拼 Mermaid 代码，比 LLM 输出更可靠。

---

## 实施顺序

| 顺序 | Phase | 依赖 | 风险 |
|------|-------|------|------|
| 1 | Phase 1 | 无 | 低（修 bug，扩结构） |
| 2 | Phase 2 | Phase 1 | 中（新算法需测试） |
| 3 | Phase 3 | Phase 1 | 中（Amap API 可用性） |
| 4 | Phase 4 | Phase 1+2 | 中（端点逻辑正确性） |
| 5 | Phase 5 | Phase 2+4 | 高（前端重构面大） |
| 6 | Phase 6 | Phase 4 | 低（prompt 调整） |

Phase 2 和 Phase 3 可并行开发（互不依赖）。

---

## 回归风险最大的改动

按风险从高到低：

1. **前端重构**（Phase 5）：~700 行新 JS + HTML 重写，整个交互面改变
2. **graph_planner 交通 API 替换**（Phase 3.3）：建图速度从 ~8s 可能变成 ~15s（多了 API 调用），需要缓存策略
3. **pickle → JSON**（Phase 1.3）：可能丢失某些 pickle 才能序列化的类型
4. **system prompt 重写**（Phase 6.1）：可能影响路线质量评分
