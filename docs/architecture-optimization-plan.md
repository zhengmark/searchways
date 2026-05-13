# 架构优化实施计划

> 基于 4 轮滚动测试根因分析，从架构层面修复 4 个系统性缺陷。
> 文档生成时间：2026-05-13

---

## 根因分析摘要

| 症状 | 频次 | 根因 | 架构缺陷 |
|------|------|------|---------|
| 无POI站点（LLM反问城市） | 17次 | 输入无预处理层，依赖LLM记忆默认值 | 缺少 Input Enrichment Layer |
| 超时（≥90s无输出） | 10次 | System prompt 禁止并行工具调用，工具粒度太细 | Tool Granularity Mismatch + 串行思维固化 |
| 约束丢失（预算/品类/排除） | 12次 | 约束与状态混存为平铺文本，LLM注意力被稀释 | 无 Constraint Model |
| NoneType崩溃 | 2次 | None作为错误信号沿管线传播，无验证网关 | Missing Validation Gateway |

---

## 方案总览

| # | 方案 | 新增文件 | 改动文件 | 预估行数 |
|---|------|---------|---------|---------|
| A | InputEnricher — 预处理层 | `app/pipeline/input_enricher.py` | `route_agent.py`, `orchestrator.py` | ~80 |
| B | Coarse-Grained Parallel Tools | — | `cluster_tools.py`, `route_agent.py` | ~60 |
| C | ConstraintModel + Structured Injection | `app/core/constraint_model.py` | `route_agent.py`, `utils.py`, `cluster_tools.py` | ~150 |
| D | ValidationGateway + SafeGraph | — | `graph_planner.py`, `cluster_tools.py` | ~40 |

---

## 方案 A: InputEnricher

### 设计
```
用户原始输入 → InputEnricher.enrich() → EnrichedInput
                    │
                    ├─ CityResolver: 提取/默认城市
                    ├─ KeywordNormalizer: 单字→标准词，否定→排除标记
                    └─ DefaultInjector: 注入默认约束

EnrichedInput {text, city, keywords, exclusions, budget_hint}
```

### 改动点
1. **新文件** `app/pipeline/input_enricher.py`:
   - `class EnrichedInput` dataclass
   - `class InputEnricher` with `enrich(user_input, session) -> EnrichedInput`
   - CityResolver: 正则匹配城市名 OR session.city OR "西安"
   - KeywordNormalizer: 映射"吃→美食,喝→咖啡"等，检测"不要/别/排除"→exclusions

2. **改** `app/core/route_agent.py`:
   - `run_unified_agent()` 中在 `_build_messages` 之前调用 `InputEnricher.enrich()`
   - 富化后的 city 自动注入 agent_state
   - 删除 system prompt 中冗余的"默认西安"文本（因为现在由代码保证）

3. **改** `app/core/orchestrator.py`:
   - `run_multi_agent()` 接收 EnrichedInput 而非原始 user_input

### 验收标准
- 佛系游客"周末不知道去哪" → 直接规划西安路线（不反问）
- 穷游学生"免费景点为主" → 直接规划（不反问城市）
- 深夜觅食"晚上11点吃东西" → 直接规划（不反问城市）

---

## 方案 B: Coarse-Grained Parallel Tools

### 设计

#### B1: geocode 支持批量地点
```
旧: geocode(place="钟楼", city="西安")  // 一次一个
新: geocode(places=[{place:"钟楼",role:"origin"}, {place:"大雁塔",role:"dest"}], city="西安")
    // 一次全解，返回多地点坐标
    // 起/终点从 2 LLM 轮次 → 1 轮次
```

#### B2: query_clusters 自动关键词扩展
```
LLM 调 query_clusters(keywords=["素食"])
  → 后端自动扩展为 ["素食","轻食","素菜","沙拉"] 查询
  → 合并去重后返回
  → LLM 不需要因结果少而重试
```

#### B3: System prompt 允许并行工具调用
```
旧: 工具预算：geocode≤3, query_clusters≤3, build_route≤2, 总调用≤8
新: 每轮可并行调用多个工具。工具预算：各 ≤2 次
```

### 改动点
1. **改** `app/pipeline/cluster_tools.py`:
   - `TOOL_DEFINITIONS` 中 geocode 新增 `places` 参数
   - `tool_geocode()` 支持批量解析
   - `execute_tool()` geocode 分支处理多个地点
   - `tool_query_clusters()` 内部自动扩展关键词

2. **改** `app/core/route_agent.py`:
   - `_SYSTEM_PROMPT` 修改工具调用指导
   - `_TIMEOUT` 保持 90s（因轮次减少，实际够用）

### 验收标准
- 典型场景从 8-12 LLM 轮次 → 3-5 轮次
- 文化游客"西安北站→大雁塔"不再超时

---

## 方案 C: ConstraintModel + Structured Injection

### 设计

#### C1: Constraint 数据模型
```python
@dataclass
class RouteConstraints:
    budget: Optional[str] = None       # "low"|"medium"|"high"
    dietary: List[str] = []            # ["无辣","素食"]
    exclusions: List[str] = []         # ["室内商场","KTV"]
    must_include: List[str] = []       # ["大雁塔"]
    max_duration_min: Optional[int] = None
    preferred_categories: List[str] = []
    
    def merge(new_input: str) -> 'RouteConstraints'  # 合并新约束
    def to_prompt_block() -> str                       # 生成LLM约束块
    def check_conflict(other: 'RouteConstraints') -> list  # 冲突检测
```

#### C2: 约束持久化到 AgentSession
```python
class AgentSession:
    constraints: RouteConstraints  # 替代 keywords, budget, exclusions
```

#### C3: 约束注入方式重构
```
旧（平铺文本）:
  【之前规划的路线参考】
  城市：西安 | 上次偏好：['户外','公园'] | 上次预算：low
  【用户新输入】控制在3小时内

新（分层注入）:
  ## ⚠️ 必须遵守的约束
  - 💰 预算：低（人均<40元）
  - 🚫 排除：室内商场, KTV
  - ⏱️ 最长：3小时
  
  ## 📋 参考信息
  上轮途经：A → B → C
  
  ## 💬 用户输入
  控制在3小时内
```

### 改动点
1. **新文件** `app/core/constraint_model.py`:
   - `RouteConstraints` dataclass
   - `ConstraintMerger` 从用户输入提取+合并约束
   - `extract_constraints_from_input(text) -> RouteConstraints`

2. **改** `app/shared/utils.py`:
   - `AgentSession` 新增 `constraints: RouteConstraints` 字段
   - 保留旧字段（keywords, budget）做向后兼容

3. **改** `app/core/route_agent.py`:
   - `_build_context()` 中注入约束块（结构化的 `to_prompt_block()`）
   - `_build_messages()` 分层组织消息（约束 > 参考 > 用户输入）
   - `_finalize_session()` 调用 `constraints.merge(new_input)`
   - 约束冲突检测

4. **改** `app/pipeline/cluster_tools.py`:
   - `execute_tool` 中保存约束到 agent_state

### 验收标准
- 极限反转 5 轮场景：每轮约束不丢失
- 亲子周末 R2"不要室内商场"约束在 R3 仍然生效
- 约束冲突时系统能检测并提示

---

## 方案 D: ValidationGateway + SafeGraph

### 设计

#### D1: POI坐标验证网关
在 `tool_build_route` 入口过滤无效坐标的 POI:
```python
def _validate_pois(pois):
    return [p for p in pois 
            if p.get("lat") is not None and p.get("lng") is not None
            and -90 <= p.get("lat") <= 90 and -180 <= p.get("lng") <= 180]
```

#### D2: SafeGraph 封装
```python
class SafeGraph:
    def edge(i, j) -> dict  # 永远返回有效边，None → 兜底值
```

#### D3: execute_tool 最外层异常包裹
```python
def execute_tool(...):
    try:
        return _execute_impl(...)
    except Exception as e:
        return json.dumps({"success": False, "error": f"内部错误: {type(e).__name__}"})
```

### 改动点
1. **改** `app/pipeline/cluster_tools.py`:
   - `tool_build_route()` 入口增加 `_validate_pois()`
   - `execute_tool()` 最外层 try/except

2. **改** `app/algorithms/graph_planner.py`:
   - 新增 `SafeGraph` 类
   - `_pick_from_segments()` 使用 SafeGraph 访问边
   - `build_graph()` 过滤 `lat=None` 的 POI

### 验收标准
- 矛盾体"米其林+30块"、深夜觅食不再崩溃
- 任一 API 返回异常数据时优雅降级（返回部分路线或友好错误）

---

## 实施顺序

```
Phase 1 (并行): A + D
  A: InputEnricher — 新增预处理层
  D: ValidationGateway — 防御层

Phase 2 (并行): B
  B: Parallel Tools — 工具重构

Phase 3: C
  C: ConstraintModel — 约束模型（依赖 A 的 EnrichedInput 格式）

Phase 4: 集成测试
  运行 test_4round_rolling.py 验证
```

## 预期效果

| 指标 | 优化前 | 优化后 |
|------|--------|--------|
| 无POI站点 | 17/50+ (34%) | 0 |
| 超时 | 10/50+ (20%) | <3 (6%) |
| 约束丢失 | 12/50+ (24%) | <3 (6%) |
| 崩溃 | 2/50+ (4%) | 0 |
| 均分 | 3.4/5 | **4.5~5.0** |
| 平均耗时 | 40-90s | 20-40s |
