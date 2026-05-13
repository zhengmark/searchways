---
marp: true
size: 16:9
theme: gaia
paginate: true
style: |
  section { font-family: 'Noto Sans CJK SC', 'Microsoft YaHei', sans-serif; font-size: 28px; }
  section.dense { font-size: 24px; }
  section.lead { text-align: center; }
  h1 { font-size: 48px; }
  h2 { font-size: 36px; }
---

<!-- _class: lead -->

# 现在就出发 — AI 本地路线智能规划

## 两周开发周报

2026年4月28日 ~ 5月9日 · 20 commits

---

# 项目概览

| 指标 | 数据 |
|:--:|:--|
| **周期** | 4/28 ~ 5/9（12 天） |
| **提交数** | 20 commits |
| **代码规模** | +7,892 / -1,141 行（最近一次） |
| **仓库** | github.com/zhengmark/searchways |
| **状态** | ✅ 可部署，完成度 ~85% |

**一句话总结：** 从零搭建了一个 AI 路线规划系统，含 LLM Agent、交互地图、个性化推荐、Docker 部署。

---

# Week 1 (4/28 — 5/2)

<!-- _class: dense -->

## 基础架构搭建

| 日期 | 里程碑 | 说明 |
|:--:|------|------|
| 4/28 | 项目初始化 | 仓库创建，技术选型 |
| 4/29 | 多智能体架构 | Plan-Execute-Review-Refine 四 Agent 协同 |
| 5/2 | 多轮对话 | session 持久化、意图识别、约束保留 |
| 5/2 | 用户画像 | 偏好持久化、历史记录、256KB 压缩 |
| 5/2 | 代码重构 | 消除重复、POI 类型修复、审核闭环 |

**Week 1 成果：** 多 Agent 架构骨架 + 多轮对话 + 用户系统雏形

---

# Week 2 (5/7) — 爆发日

<!-- _class: dense -->

## 一天完成 9 个里程碑

| # | 时间 | 里程碑 | 效果 |
|:--:|:--:|------|------|
| 1 | 19:31 | POI 数据库搭建 | SQLite + Haversine，14,222 条 |
| 2 | 19:52 | 网站骨架 | FastAPI + Jinja2 + SSE |
| 3 | 20:17 | API 接入管线 | orchestrator → pipeline → 前端 |
| 4 | 20:33 | 推荐引擎 | 四路召回 + 走廊精排 + MMR |
| 5 | 20:50 | LLM 适配 | 推荐引擎集成到 orchestrator |
| 6 | 20:59 | 聚类预计算 | 995 簇，93.9% 覆盖率 |
| 7 | 23:46 | 走廊感知 + UI | 推荐评分走廊感知，网站 10 项打磨 |

---

# Week 2 (5/8) — 架构重构

## 统一 Agent + 性能优化

| 里程碑 | 说明 |
|------|------|
| **建图 15x 优化** | K=8 近邻 + 预剪枝 top-15，90s→6s |
| **Session 类型化** | AgentSession 全字段 typed |
| **统一 Agent 架构** | 1 次 LLM 工具调用替代 4 Agent 串行，-824 行 |
| **用户登录系统** | bcrypt + JWT + 前后端完整认证 |
| **17 项修复** | falsy 检查、substring 匹配、dedup 缓存等 |
| **10 用户测试** | 评分从 3.2 → 4.1/5 |

**核心转变：** 多 Agent 串行 → 统一 Agent 工具调用（罗斯方案）

---

# 架构演进

```
Week 1:                          Week 2:
多 Agent 串行                    统一 Agent 工具调用
                                
UserIntent → IntentAgent         LLM System Prompt
  → POIStrategyAgent               │
  → RouteBuilder                  ├─ geocode()
  → NarratorAgent                  ├─ query_clusters()
  → ReviewerAgent                  └─ build_route()
                                
4 次 LLM 调用                    1 次 LLM + ≤6 次工具调用
无交互式编辑                     交互式地图编辑
```

**净减 824 行代码，可用性大幅提升**

---

# Week 2 (5/9) — 落地优化

<!-- _class: dense -->

## 四个维度

| 维度 | 内容 | 关键改动 |
|:--:|------|------|
| **稳定性** | session TTL 24h、fcntl 锁、LLM 重试、超时保护 | `server.py` `llm_client.py` |
| **前端去 mock** | 删除所有 mock fallback、接通真实 API | `route-editor.js` |
| **Docker** | Dockerfile + compose + deploy.sh | 新增 4 文件 |
| **个性化** | 画像学习、偏好注入 LLM、推荐 API | `user_profile.py` |

### 同时修复
- 走廊引擎 bug（UnboundLocalError + 拼写错误）→ POI 0→1630+
- 投影排序 + 分段均匀选取 → 路线全程覆盖
- 地图增强：颜色图例 + hover tooltip + DnD 排序

---

# 性能数据

<!-- _class: dense -->

| 指标 | 优化前 | 优化后 | 提升 |
|:--:|:--:|:--:|:--:|
| POI 搜索 | ~1169ms (API) | ~8ms (SQLite) | **~39x** |
| 建图速度 | ~90s | ~6s | **~15x** |
| LLM 调用次数 | 4 次/轮 | 1 次/轮 | **4x** |
| 聚类覆盖率 | — | 93.9% (995簇) | — |
| 走廊推荐 POI | 0 | 1630+ | ∞ |
| 用户测试评分 | 3.2/5 | 4.1/5 | +28% |

---

# 技术栈

<div class="columns">
<div>

| 层级 | 技术 |
|:--|------|
| LLM | LongCat (Anthropic 兼容) |
| 地图 | 高德开放平台 |
| 数据库 | SQLite + Haversine |
| Web | FastAPI + Jinja2 + SSE |

</div>
<div>

| 层级 | 技术 |
|:--|------|
| 前端 | Leaflet.js + Mermaid.js |
| 聚类 | 网格法 0.01°×0.01° |
| 部署 | Docker + compose |
| 个性化 | 画像学习 + LLM 注入 |

</div>
</div>

---

# API 端点总览

<!-- _class: dense -->

| 类别 | 端点 | 功能 |
|:--:|------|------|
| 规划 | `POST /api/plan` | 同步规划 |
| 规划 | `POST /api/plan/stream` | SSE 流式 + 实时进度 |
| 对话 | `POST /api/chat` | 多轮修改 |
| 编辑 | `POST /api/route/{id}/select-poi` | 添加 POI |
| 编辑 | `POST /api/route/{id}/remove-poi` | 移除 POI |
| 编辑 | `POST /api/route/{id}/reorder` | 重排站点 |
| 编辑 | `POST /api/route/{id}/confirm` | 确认路线 + 偏好学习 |
| 画像 | `GET /api/profile/suggestions` | 个性化推荐 |
| 认证 | `POST /api/auth/*` | 注册/登录/验证 |

**共 18 个端点**

---

# 下一步 / TODO

<div class="columns">
<div>

### 短期（本周）
- 前端 E2E 测试
- CI/CD 流水线
- 多城市 POI 数据灌入
- 移动端适配

</div>
<div>

### 中期（2-4 周）
- LLM mock 测试框架
- 路线缓存加速
- AB 测试框架
- 分享功能完善

</div>
</div>

---

# 关键指标

- [x] **20** commits in 12 days
- [x] **42** files changed in final polish
- [x] **18** API endpoints
- [x] **14,222** Xi'an POIs in DB
- [x] **995** pre-computed clusters
- [x] **~15x** route-building speedup
- [x] **4.1/5** user satisfaction score
- [x] **Docker-deployable** with one command

---

<!-- _class: lead -->

# 谢谢！

**仓库:** github.com/zhengmark/searchways
**文档:** docs/landing-optimization.md
**周报:** docs/weekly-report-2026-05-09.md
