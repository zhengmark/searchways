# 项目上下文摘要 — "现在就出发" AI 本地路线智能规划

> 保存此文件以便新对话快速接手。上下文过长时可让 Claude 直接读本文件。

---

## 1. 项目概述

美团 AI 黑客松项目：**用户用自然语言说一句出行想法（"从丈八六路地铁站出发到浐灞玩"），系统自动解析起终点、搜索沿途 POI、计算空间均匀的最优路径、输出 LLM 解说 + Mermaid 路线图 + Leaflet 交互地图。**

一句话价值主张：**让 AI 在信息高度不确定的条件下，输出可用的结构化路线。**

---

## 2. 当前进度

| 模块 | 文件 | 状态 | 说明 |
|------|------|------|------|
| LLM 意图解析 | `multi_agent/intent_agent.py` | ✅ 完成 | 深度意图解析 + UserProfile 推理 + 搜索提示 |
| 地理编码 | `poi.py:robust_geocode` | ✅ 完成 | 4 层递进兜底 + 正则回退 + 子串校验 |
| POI 搜索 | `poi.py:search_poi/around/along_route` | ✅ 完成 | 高德 text/around 双 API + 走廊 4 点采样 + 返回 Python 对象 |
| POI 搜索策略 | `multi_agent/poi_strategy_agent.py` | ✅ 完成 | LLM 制定搜索区域 + 关键词 + 质量评估 + 自动补搜 |
| 图构建 | `graph_planner.py:build_graph` | ✅ 完成 | 全连接邻接矩阵，ThreadPool 并发，步行 API + haversine 兜底 |
| 路径规划 | `graph_planner.py:shortest_path` | ✅ 完成 | 连线投影分段选取（有终点）/ 距离带分层（无终点）+ 时间预算约束 |
| 路线解说 | `multi_agent/narrator_agent.py` | ✅ 完成 | 个性化解说，基于 UserProfile 调整语气和推荐角度 |
| 路线审核 | `multi_agent/reviewer_agent.py` | ✅ 完成 | 质量审核（覆盖/多样/匹配/时间），最多 2 轮 refine |
| Mermaid 图 | `core.py:_build_mermaid_from_path` | ✅ 完成 | 含交通工具 emoji + 颜色编码 |
| Leaflet 地图 | `core.py:_build_route_html` | ✅ 完成 | 起终点 + 中间站标记 + 路线连线 |
| 点评 API | `reviews.py` | 🔶 骨架 | 定义了 `fetch_reviews` tool，实际未接入 |
| 多用户测试 | `test_ux_deep.py` / `test_user_scenarios.py` | ✅ 完成 | 5 画像自动化评分卡，3 画像快速回归 |
| 多轮对话 | `multi_agent/orchestrator.py` + `modifier_agent.py` | ✅ 完成 | 规则+LLM 修改意图识别，增量重规划，session 持久化 |
| 用户画像 | `user_profile.py` | ✅ 完成 | per-user JSON 持久化，256KB 压缩，favorites/history/session |
| 移动端适配 | — | ❌ 未开始 | — |

---

## 3. 技术架构

### 技术栈

| 层 | 选型 |
|----|------|
| LLM | LongCat Flash Chat（Anthropic API 兼容，`https://api.longcat.chat/anthropic`） |
| 地图/POI | 高德开放平台 Web API（3 个 endpoint：place/text, place/around, direction/walking, geocode/geo, assistant/inputtips） |
| 地图渲染 | Leaflet.js 1.9.4 + OpenStreetMap tiles（CDN 加载） |
| 图计算 | Python 自研，haversine 公式 + ThreadPoolExecutor 并发 |
| 环境变量 | `.env` 文件，python-dotenv 加载 |

### 目录结构

```
/root/my-first-app/
├── .env                          # API Key（LLM + 高德 + 点评占位）
├── agent/
│   ├── __init__.py
│   ├── config.py                 # 读取 .env，导出 LLM_API_KEY/AMAP_API_KEY 等
│   ├── llm_client.py             # ★ 共享 LLM 客户端（单点 call_llm，全 Agent 复用）
│   ├── models.py                 # Pydantic 数据模型（POI, RouteStop, Route, UserIntent）
│   ├── core.py                   # 公共函数（_extract_city, _build_mermaid, _build_route_html, AgentSession）
│   ├── user_profile.py           # ★ 用户画像管理器（持久化、256KB 压缩、session 恢复）
│   ├── multi_agent/
│   │   ├── __init__.py
│   │   ├── types.py              # Agent 间通信数据结构
│   │   ├── orchestrator.py       # ★ 多智能体主控 Plan-Execute-Review-Refine + 多轮对话
│   │   ├── intent_agent.py       # 意图理解 Agent（深度解析 + 画像推理）
│   │   ├── poi_strategy_agent.py # POI 搜索策略 + 质量评估 Agent
│   │   ├── reviewer_agent.py     # 路线质量审核 Agent（含时间预算校验）
│   │   ├── narrator_agent.py     # 个性化路线解说 Agent
│   │   └── modifier_agent.py     # ★ 修改意图识别 Agent（8 类正则规则 + LLM 兜底）
│   └── tools/
│       ├── __init__.py
│       ├── constants.py          # 共享常量（关键词映射/黑名单/占位符）
│       ├── geo.py                # 几何工具（haversine, project_ratio）
│       ├── poi.py                # ★ 高德 API 封装：直接返回 Python 对象，异常用 AmapAPIError
│       ├── poi_filter.py         # POI 过滤工具（去重/品类/坐标/距离）
│       ├── graph_planner.py      # ★ 图算法：build_graph + shortest_path
│       ├── routing.py            # 步行距离计算（高德 walking API）
│       └── reviews.py            # 点评 API 骨架
├── users/                        # 用户画像文件（不提交）→ users/{user_id}.json
├── test/                         # 集成测试
├── test_user_scenarios.py        # 3 画像快速回归测试，124 行
├── test_graph.py                 # 单条路线集成测试
├── test_routing.py               # 步行 API 单测
├── test_amap.py                  # 高德 API 连通性测试
├── test_longcat.py               # LongCat API 连通性测试
├── ARCHITECTURE.md               # 开发者落地文档（接口、数据流、扩展指南）
├── route_output.md               # 最后一次运行的 Mermaid 图
└── route_output.html             # 最后一次运行的 Leaflet 地图
```

### 核心数据流（`run_multi_agent` Plan-Execute-Review-Refine）

```
用户输入 "从西安丈八六路地铁站出发到浐灞玩，3小时"
  │
  ├─ 判断：新路线 or 修改已有路线
  │     ├─ 新会话 / new_route → 完整 PLAN 流程
  │     └─ 有历史 session → modifier_agent.detect_modification() → 增量修改
  │
  ├─ 【完整规划 PLAN】
  ├─ 1. _extract_city()     → 城市名（正则匹配 300+ 城市列表 + "XX市" 兜底）
  ├─ 2. Intent Agent        → LLM 深度解析 {origin, destination, keywords, num_stops, time_budget_hours}
  │      └─ 同时推理 UserProfile（group_type, energy_level, budget, interests, notes）
  │      └─ 占位符校验：拒绝 "起点地名""搜索关键词逗号分隔" 等空壳值
  ├─ 3. robust_geocode()    → 起终点坐标（4 层兜底 + 正则回退）
  │      └─ Layer 1: geocode(原始名称)  → Layer 2: input_tips 子串匹配
  │      └─ Layer 3: geocode(名称+地铁站) → Layer 4: geocode(城市+名称)
  │      └─ Regex fallback: 从原始输入提取 [地名模式] 重新 geocode
  ├─ 4. POI Strategy Agent   → LLM 制定搜索策略（区域 + 关键词 + 半径 + 原因）
  │      └─ 执行搜索 → Amap text/around/along_route API
  │      └─ POI 质量评估 → 不足时自动补搜（关键词规范化 + 品类黑名单过滤）
  ├─ 5. Route Engine         → build_graph() + shortest_path()
  │      └─ 全连接邻接矩阵 → 连线投影分段选取 → 200m 最小间距过滤
  │      └─ 时间预算约束：超出 20% 自动减站
  ├─ 6. Narrator Agent       → LLM 个性化解说（基于 UserProfile 调整语气）
  ├─ 7. Reviewer Agent       → 质量审核（覆盖/多样/匹配/时间），最多 2 轮 refine
  │      └─ 不通过 → 调整搜索策略 → 重新搜索 → 重新规划
  └─ 8. Output
       ├─ 文本回复 + Mermaid → route_output.md
       ├─ Leaflet 地图 → route_output.html
       └─ 保存用户画像 → users/{user_id}.json（profile + favorites + history + session）

【多轮修改 MODIFY】
  用户 "不去钟楼了，去大雁塔"
  ├─ modifier_agent.detect_modification()
  │     ├─ 8 类正则规则优先（快速、确定性）
  │     └─ LLM 兜底（模糊/复合表达）
  ├─ 识别为 change_destination → 只重跑受影响环节
  │     └─ geocode → POI 搜索 → 建图 → 解说 → 审核
  ├─ 其他修改类型：
  │     ├─ change_origin        → geocode + 重新搜索 + 建图
  │     ├─ change_keywords      → 重新搜索 + 建图
  │     ├─ change_num_stops     → 只重建图
  │     ├─ change_preferences   → 更新画像 + 重新解说
  │     ├─ change_poi_location  → 新区域搜索 + 建图
  │     ├─ adjust_constraint    → 重新建图（时间约束）
  │     └─ new_route            → 完整重规划
  └─ 每轮结束：保存 session + history + favorites 到 users/{user_id}.json
```

---

## 4. 重要决策

### 4.1 路径算法：连线投影 + 分段选取

**选择**：POI 投影到起终点连线上得 [0,1] 比例 → 均分 N 段 → 每段取评分最高者（200m 内互斥）。

**为什么不**：
- Dijkstra / MST：边权重基于步行 API 耗时，不是欧几里得距离；TSP 变种是 NP-hard
- LLM 直接选点：LLM 无空间感知，会选出扎堆在起点 37m 内的 3 个站点

### 4.2 LLM 角色：意图解析 + 解说，不参与路径决策

**选择**：算法算路径，LLM 做理解和表达。

**为什么**：LLM 擅长理解和生成自然语言，但对空间关系 / 距离 / 交通时间完全没有感知。让 LLM 选 POI 排路线是失控的。

### 4.3 交通工具时间：步行 API × 速度系数

**选择**：所有边用高德步行 API 测距，然后按 `{步行:1×, 骑行:3×, 公交:2×, 打车:5×}` 缩放耗时。

**为什么不**：
- 骑行 API：高德骑行方向 API 覆盖率低，很多城市无数据
- 公交 API：需额外 Key，响应慢（2-5 秒/次），全连接图 N 个节点需要 N×(N-1)/2 次调用

### 4.4 Python falsy 显式检查

**选择**：所有坐标判空用 `if x is not None` 而非 `if x`。

**为什么**：高德 API 对无坐标 POI 返回 `location="0,0"`，`float("0") = 0.0` 是 Python falsy 值。`if 0.0` 会误杀有效数据（赤道几内亚外海）。修复后 POI 有坐标率从 12.5% 升至 57%。

### 4.5 地名匹配：连续子串 > 字符重叠

**选择**：`name in tip_name or tip_name in name` 而非计算字符重叠率。

**为什么**：字符重叠校验下，"四路地铁站" 的 5 个字符分散出现在 "仟那千寻酒店(西安霸城门地铁站凤城四路店)" 中 → 匹配到凤城四路而非丈八四路。子串校验可杜绝跨地名误配。

### 4.6 多轮修改：规则优先 + LLM 兜底

**选择**：修改意图识别优先用正则规则匹配，未命中时才调用 LLM。

**为什么不**：
- 纯 LLM：每次修改意图识别都调 LLM 会增加 1-2 秒延迟 + API 费用
- 纯规则：难以覆盖 "换一种风格吧""感觉不太对" 等模糊表达
- 混合策略下，规则覆盖 ~90% 常见修改（改起点/终点/关键词/站点数等），LLM 处理剩余 ~10%

### 4.7 用户画像：单文件 JSON + 大小压缩

**选择**：每个用户一个 JSON 文件（`users/{user_id}.json`），文件大小上限 256KB，按时间保留最新历史。

**为什么不**：
- SQLite：多一个依赖，对每条记录几百字节的数据过度设计
- 纯内存：重启丢失，无法跨会话学习用户偏好
- 不限大小：多轮对话累积的历史记录可能膨胀到 MB 级别
- 压缩策略：二分查找保留条数，profile 和 session 永不压缩

### 4.8 无起点/无终点的降级模式

**初版**（origin-centric）：无起点 → 整条路线不生成，降级到 LLM 自由发挥。

**终版**（双向）：
- 无起点有终点 → destination-centric 模式：以终点为锚搜索周边做环线
- 无终点有起点 → origin-centric 模式：以起点为中心用距离带分层

---

## 5. 踩过的坑

### 🕳️ 1: POI 扎堆起点（贪心最近邻陷阱）
- **现象**：3 个站点全在起点 37m 内，然后一跳 5km 到终点
- **放弃方案**：最近邻 + 前进方向奖励 + 多样性惩罚 → 仍然扎堆
- **采纳方案**：连线投影 + 分段选取（见 4.1）
- **关键教训**：`search_along_route` 搜到的 POI 高度集中在起点周边，任何基于"距当前节点最近"的贪心都跳不出局部最优

### 🕳️ 2: Python falsy 吞掉坐标 0.0
- **现象**：17 个 POI 只有 2 个"有坐标"，深夜觅食场景几乎无可用 POI
- **根因**：`if p.get("lat")` 对 0.0 返回 False
- **修复**：poi.py 返回 `None` 而非 `0.0`；core.py 全部用 `is not None` 判断
- **教训**：语义边界值用 sentinel（None），不要依赖 falsy/truthy

### 🕳️ 3: 地名截断 → 跨城错配
- **现象**："丈八四路地铁站" → LLM 截断为"四路地铁站" → geocode 到凤城四路（西安另一端）
- **放弃方案**：单层 geocode + 字符重叠校验（跨地名仍误配）
- **采纳方案**：4 层递进兜底 + 正则回退 + 子串校验（详见 4.5）
- **教训**：防御的核心在源头（LLM prompt），事后修复有不可恢复的边界

### 🕳️ 4: 交通工具标记与耗时脱节
- **现象**：骑行 3km 显示 37 分钟（实际约 10 分钟）
- **根因**：`_fetch` 只调步行 API，`decide_transport` 标记"骑行"后 duration 仍是步行秒数
- **修复**：`_fetch` 内按速度系数缩放 duration

### 🕳️ 5: `// 60` 截断
- **现象**：30 秒路段显示"0 分钟"
- **根因**：Python `//` 是 floor division
- **修复**：全部改为 `round(x / 60)`

### 🕳️ 6: LLM JSON 占位符
- **现象**：LLM 返回 `{"origin": "起点地名", "keywords": "搜索关键词逗号分隔"}`
- **修复**：`_parse_intent` 加 post-parse 校验层，拒掉已知占位符

### 🕳️ 7: "打卡" → 打印店
- **现象**：搜索"打卡"时 Amap 返回"XX 图文快印"
- **修复**：品类黑名单 `["打印","复印","图文","广告","快印","印刷","维修","洗车","药店","中介"]`

---

## 6. 当前卡点

| 卡点 | 严重程度 | 详情 |
|------|---------|------|
| 长尾关键词 POI 覆盖率低 | 中 | "深夜宵夜""小吃" 等词 Amap 返回大量无坐标/低质量 POI。已通过关键词规范化缓解（宵夜→小吃烧烤火锅），但未根本解决 |
| 极简输入体验差 | 低 | "北京 吃" 只能全城中心兜底，3 个 POI 挤在 1.4km 内 |
| 建图性能 | 低 | 49 POI 全连接建图耗时约 90 秒（O(n²) 步行 API 调用），大搜索量场景卡 |
| 地名截断残余风险 | 低 | 如果 LLM 截断且 input_tips 不返回含正确名称的候选项，无法恢复 |
| 多轮对话 LLM 兜底质量 | 低 | modifier_agent 的 LLM 兜底目前使用 fast model，复杂表达可能误判；规则覆盖 ~90% 场景 |

---

## 7. 待办事项（按优先级）

| 优先级 | 事项 | 说明 |
|--------|------|------|
| P0 | 接入点评/美团 API 补搜 | 填补长尾关键词 POI 覆盖率缺口 |
| P1 | 建图前预剪枝 | POI >20 个时，先按评分 + 距起终点距离排序，取 top-15 再建全连接图 |
| P1 | 接入用户登录系统 | `UserProfileManager` 已留好接口，只需改 `user_id` 参数 |
| P2 | 极简输入个性化 | 利用 IP 定位 / 历史偏好推断起终点 |
| P2 | LLM 输出侧地名校验 | 对比 LLM 返回的 origin/dest 与用户原始输入，不匹配时触发正则回退 |
| P3 | asyncio 替代 ThreadPool | 图构建和 POI 搜索可改为协程，减少线程开销 |
| P3 | PWA + 移动端适配 | Leaflet 响应式 + Service Worker 离线 |
| — | 单元测试 | 目前只有集成测试，`graph_planner` 和 `poi` 缺单测 |

已完成（本轮）:
- ✅ 多轮对话（modifier_agent + orchestrator 双分支）
- ✅ 用户画像持久化（UserProfileManager + 256KB 压缩 + session 恢复）
- ✅ 时间预算约束落地（超出 20% 自动减站 + Reviewer 审核）
- ✅ POI 质量评估闭环（evaluate_pois → 自动补搜 → 重评估）
- ✅ 消除重复代码（llm_client.py, constants.py, geo.py, poi_filter.py）

---

## 8. 关键命令

```bash
# ── 运行测试 ──────────────────────────────────

# 5 画像深度体验测试（约 3-5 分钟，调 LLM + 高德 API）
python3 test_ux_deep.py

# 3 画像快速回归测试
python3 test_user_scenarios.py

# 单条路线集成测试（西安铁塔寺路 → 钟楼）
python3 test_graph.py

# API 连通性测试
python3 test_longcat.py       # LLM API
python3 test_amap.py          # 高德 API
python3 test_routing.py       # 高德步行 API

# ── 手动跑一条路线（多智能体 pipeline） ─────────

python3 -c "
import sys; sys.path.insert(0, '.')
from agent.multi_agent.orchestrator import run_multi_agent
result, s = run_multi_agent('从丈八六路地铁站出发到浐灞玩', user_id='default')
print(result)
"

# ── 测试多轮对话 ──────────────────────────────

python3 -c "
import sys; sys.path.insert(0, '.')
from agent.multi_agent.orchestrator import run_multi_agent
result, s = run_multi_agent('从丈八六路出发去钟楼吃', user_id='default')
result2, s = run_multi_agent('不去钟楼了，去大雁塔', session=s, user_id='default')
print(result2)
"

# ── 查看输出文件 ───────────────────────────────

cat route_output.md           # Mermaid 路线图
python3 -m http.server 8000   # 然后浏览器打开 http://localhost:8000/route_output.html

# ── 单独测试 geocode 兜底链 ─────────────────────

python3 -c "
import sys; sys.path.insert(0, '.')
from agent.tools.poi import robust_geocode
print(robust_geocode('丈八四路地铁站', '西安'))
print(robust_geocode('四路地铁站', '西安'))  # 截断后仍能正确恢复
"

# ── 查看用户画像 ───────────────────────────────

cat users/default.json | python3 -m json.tool
```

---

## 附录：环境变量

| 变量 | 用途 | 状态 |
|------|------|------|
| `LLM_API_KEY` | LongCat API Key | ✅ 已配置 |
| `LLM_BASE_URL` | `https://api.longcat.chat/anthropic` | ✅ |
| `LLM_MODEL` | `LongCat-Flash-Chat` | ✅ |
| `AMAP_API_KEY` | 高德开放平台 Key | ✅ 已配置 |
| `DIANPING_APP_KEY` | 大众点评 API | ❌ 占位值 |
| `DIANPING_APP_SECRET` | 大众点评 Secret | ❌ 占位值 |

---

*生成时间：2026-04-27 | 更新：2026-05-02（多轮对话 + 用户画像）| 代码总计 ~3250 行 Python*
