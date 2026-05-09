# 现存问题解决方案

> 基于 4 轮测试（40 用户 / 80+ 轮对话）发现的系统性问题

---

## 问题全景

```
           ┌──────────────────────────────────────┐
           │           POI 数据质量 (根因)          │
           │  品类缺失 · 标签不准 · 价格偏差 · 无辅助信息 │
           └──────────────┬───────────────────────┘
                          │
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                  ▼
  素食/健身/商务       KTV/棋牌/小米       孕妇/老人/残障
  查询返回空站        混入正常路线         无数据支撑
```

---

## 方案一：高德 API 补搜（轻量，当天可上）

### 思路
本地 POI DB 查不到时，fallback 到高德 `place/text` API 补充搜索。系统已经有了 Amap 集成（`app/providers/amap_provider.py`），只需加一个补搜逻辑。

### 具体做法

在 `tool_query_clusters` 返回结果不足时（total < 3 或 keyword_match 全为 0），自动触发高德关键词搜索：

```python
# cluster_tools.py 新增
def _amap_fallback_search(lat, lng, keywords, city="西安"):
    """高德 API 补搜 — 当本地 DB 结果不足时"""
    from app.providers.amap_provider import search_poi
    results = []
    for kw in keywords[:3]:
        pois = search_poi(keywords=kw, city=city, 
                         location=f"{lng},{lat}", radius=5000)
        results.extend(pois)
    return results  # 返回高德 POI，带品类/评分/价格
```

**效果预估**：
- 阿强(商务宴请) 1.0 → 3.5+：高德有大量"商务餐厅""宴会厅"结果
- 大力(健身餐) 4.8→5.0：高德搜"轻食""沙拉"有数据
- 老刘(博物馆) 2.2→4.0：高德博物馆数据完整

**代价**：每次补搜消耗高德 API 配额（日限额通常 5000 次）

---

## 方案二：接入点评/美团 API（中期，效果最好）

### 思路
大众点评有真实的用户评分、人均价格、营业时间、标签（"有包间""无障碍""适合情侣"等）。这些数据正好补上本地 DB 的缺口。

### 具体做法

1. 注册点评开放平台，申请 API 权限
2. 在 `app/providers/` 下实现 `dianping_provider.py`：
   ```python
   class DianpingProvider:
       def search_business(self, keyword, lat, lng, filters=None):
           """搜索商户 — 过滤条件：人均价格、评分、标签"""
       
       def get_business_detail(self, biz_id):
           """获取商户详情 — 包含无障碍/停车/包间/营业时间"""
   ```
3. 在 `tool_query_clusters` 结果不足时，调用点评 API 补充
4. 点评返回的标签可直接用于约束校验（"有包间""无辣""素食"）

### 关键数据字段

| 点评字段 | 解决什么问题 |
|----------|-------------|
| `avg_price` | 商务宴请人均筛选 |
| `categories` | 素食/轻食/按摩/SPA 精准品类 |
| `tags` | "有包间""无障碍""适合情侣""深夜营业" |
| `business_hours` | "晚上11点还有营业的" |
| `photos` | "拍照好看"的前端展示 |
| `review_count` | 人气验证 |

**效果预估**：品类匹配 +30%，约束满足率 +40%

**代价**：需要企业认证，API 有调用配额

---

## 方案三：输出侧校验层（轻量，立即可做）

### 思路
不在输入端过滤，而在 LLM 输出路线后加一层规则校验。发现违规就标记、降权、或触发补搜重规划。

### 架构

```
LLM 输出 narration + stops
       │
       ▼
┌─────────────────────┐
│  ConstraintChecker   │  ← 新增模块
│                     │
│  输入: stops, user_profile, narration
│  输出: violations[], severity
│                     │
│  规则:               │
│  1. 无辣 → stop含"火锅"→ violation
│  2. 孕妇 → stops>3 → violation  
│  3. 商务 → avg_price<150 → violation
│  4. 素食 → 餐厅含"肉/鸡/鱼"→ violation
│  5. 博物馆 → 无博物馆/古迹 → violation
│  6. 无障碍 → (暂不可校验，需外部数据)
│                     │
│  如果 violations≥2 且 severity=high
│     → 自动触发重规划(带 violation 反馈)
│  如果 violations<2
│     → 在解说末尾加 ⚠️ 提示
└─────────────────────┘
```

### 实现位置

新增 `app/pipeline/constraint_checker.py`，在 `route_agent.py` 的 `_finalize_session` 中调用。

**效果预估**：违规检出率 90%+，用户不会看到明显不合适的推荐

---

## 方案四：组团场景的冲突调和（中期）

### 思路
多人出行时，系统需要显式地找"交集"而非靠 LLM 直觉。这可以用规则引擎实现。

### 做法

```python
# 输入：多个 UserProfile
profiles = [
    {"diet": "无辣+无冰+无生冷"},       # 小美
    {"diet": "高蛋白+无油炸+无糖"},      # 大力  
    {"diet": "素食+无肉无蛋无奶"},       # 阿琳
]

# 取交集
food_intersection = intersect_diets(profiles)
# → ["清淡汤面", "沙拉吧", "商场food court", "茶餐厅"]

activity_intersection = intersect_activities(profiles)  
# → ["公园散步", "看电影", "逛商场", "书店"]
```

然后在 `query_clusters` 时用交集关键词搜索，而非用每个人的关键词分别搜。

### 实现位置

新模块 `app/algorithms/group_planner.py`：
```python
def plan_for_group(user_profiles: list, origin, destination) -> GroupRoute:
    """显式调和多人需求"""
    diet = intersect_dietary([p.diet for p in user_profiles])
    activity = intersect_activities([p.interests for p in user_profiles])
    pace = min_pace([p.pace for p in user_profiles])  # 取最慢的
    budget = max_budget([p.budget for p in user_profiles])  # 取最高的
    
    # 用调和后的参数规划
    return run_unified_agent_with_profile(GroupProfile(diet, activity, pace, budget))
```

**效果预估**：组团场景 3.0 → 4.0+

---

## 方案五：工具调用优化（轻量，立即可做）

### 5.1 工具调用预算分配
当前问题：LLM 12轮全用完还没结果。

```python
# 硬预算分配
BUDGET = {
    "geocode": 2,        # 起终点各一次，禁止重复
    "query_clusters": 3,  
    "build_route": 1,     # 只调一次
}
# 每步超预算 → 用当前已有结果继续
```

### 5.2 超时保护
```python
# 超过 60秒还没有 build_route → 强制用已有簇构建
if elapsed > 60 and not agent_state.get("path_result"):
    clusters = agent_state.get("last_clusters", [])
    if clusters:
        force_build_route(clusters[:3])
```

### 5.3 geocode 去重
```python
# 同一个地名 geocode 过 → 30s 内不重复调
# 同城市不同地名 → 用 input_tips 而非重新 geocode
```

**效果预估**：工具调用浪费 -60%，平均耗时 -35%

---

## 方案六：补充 POI 品类覆盖率（一次性）

### 用高德 API 批量补充

跑一次全量补充脚本，针对系统已知的品类缺口：

```bash
# 用高德 API 搜西安的以下关键词，结果写入 POI DB
keywords = [
    "素食", "轻食", "沙拉", "健身餐", "有机餐厅",
    "商务餐厅", "宴会厅", "私房菜", "包间",
    "按摩", "SPA", "足疗", "推拿",
    "书店", "独立书店", "花店",
    "无障碍", "母婴室",  # 这些高德没有但可以作为标签
]
```

---

## 推荐实施路线

```
Week 1（立即可做）:
  ├─ 方案一: 高德补搜（3小时）
  ├─ 方案三: 输出校验层（4小时）
  └─ 方案五: 工具调用优化（2小时）
     预期效果: 整体评分 4.1 → 4.5, 阿强 1.0 → 3.5

Week 2（需要 API 权限）:
  ├─ 方案二: 点评 API 接入（需先申请）
  └─ 方案六: POI 批量补充（半天脚本+数小时运行）
     预期效果: 整体评分 4.5 → 4.7+

Week 3:
  └─ 方案四: 组团冲突调和（2天）
     预期效果: 组团 3.0 → 4.0+
```

---

## 外部资源清单

| 资源 | 用途 | 获取方式 | 费用 |
|------|------|----------|------|
| 高德 place/text API | POI 补搜 | lbs.amap.com 注册 | 日5000次免费 |
| 大众点评商户 API | 真实评分/标签/营业时间 | open.dianping.com 企业认证 | 按量付费 |
| 高德 place/detail API | POI 详情（电话/营业时间/照片） | 同高德 | 日5000次 |
| OpenStreetMap | 无障碍/步道/公园数据 | 免费开放 | 免费 |
| 西安文旅局公开数据 | 博物馆/古迹/景区列表 | 西安文旅局官网 | 免费 |

---

*生成时间：2026-05-08*
