# 系统落地优化报告 (2026-05-09)

## 优化前状态

系统完成度约 70%：后端数据流已打通，前端含大量 mock 数据，session 无过期管理，无容器化部署，无个性化。

## 优化内容

### 1. 稳定性加固

| 改动 | 文件 | 说明 |
|------|------|------|
| Session TTL 24h 淘汰 | `web/server.py` | 后台守护线程每 30 分钟清理过期 session 文件 |
| 文件锁防并发 | `web/server.py` | `fcntl.flock` + `os.fsync` 防止并发写覆盖 |
| 启动清理旧文件 | `web/server.py` | 启动时跳过 7 天前的 session 文件 |
| 输入校验 | `web/server.py` | 空查询/超长/无效 session_id 统一返回 422 |
| 422 异常处理 | `web/server.py` | 统一 `RequestValidationError` handler，返回结构化错误 |
| LLM 重试优化 | `app/llm_client.py` | 随机抖动避免惊群，区分 401/403(不重试) 和 429/5xx(重试) |
| 超时保护双保险 | `app/core/route_agent.py` | build_route 失败时直接调 tool_build_route 兜底 |
| 错误分类 | `web/server.py` | 区分 400(参数)/404(会话)/500(服务器)/422(校验) |

### 2. 前端去 mock

| 改动 | 文件 | 说明 |
|------|------|------|
| 删除 mock fallback | `route-editor.js` | `enableEditMode` 数据不足时显示错误提示而非 mock |
| 接通真实 API | `route-editor.js` | `_onChatModify` 改为调用 `/api/chat` |
| 删除 confirm mock | `route-editor.js` | `_onConfirm` 失败时显示错误而非 mock 路线 |
| 错误提示栏 | `index.html` + `style.css` | 侧边栏顶部红色错误提示 |
| 变量命名修复 | `route-editor.js` | `segments` → `projRanges` 消除冲突 |
| 推荐 POI 分段均匀选取 | `route-editor.js` | 路线分 5 段，每段各取 top 5 个 POI |

### 3. Docker 化部署

| 改动 | 文件 | 说明 |
|------|------|------|
| Dockerfile | `Dockerfile` | python:3.11-slim，安装依赖，暴露 8000 |
| Compose | `docker-compose.yml` | 健康检查 + env_file + data 卷持久化 + restart unless-stopped |
| 部署脚本 | `scripts/deploy.sh` | git pull → build → up -d → health check |
| 健康检查扩展 | `web/server.py` | `/api/health` 返回 DB 状态 + session 数量 |

### 4. 个性化推荐

| 改动 | 文件 | 说明 |
|------|------|------|
| 偏好学习 | `app/user_profile.py` | `update_from_route()` 从完成路线学习品类/区域/预算偏好 |
| 偏好注入 | `app/core/route_agent.py` | `_build_messages` 注入偏好到 LLM context |
| 推荐 API | `web/server.py` | `GET /api/profile/suggestions` 推荐"再来一次"/偏好品类 |
| 确认时学习 | `web/server.py` | 路线确认时自动调用 `update_from_route` |

### 5. 走廊 POI 修复

| 改动 | 文件 | 说明 |
|------|------|------|
| 修复 UnboundLocalError | `app/pipeline/corridor_engine.py` | 删除重复 import + 修复 `__math` 拼写 |
| 全量簇加载 | `app/pipeline/cluster_tools.py` | corridor 用全部查询到的 31 个簇而非 LLM 选的 4-5 个 |
| 投影比例排序 | `db/cluster.py` | 簇按 projection 排序返回，确保均匀分布 |
| 杂项过滤 | `db/cluster.py` | 过滤便利店/烟酒店等非旅游品类主导的簇 |

### 6. 地图交互增强

| 改动 | 文件 | 说明 |
|------|------|------|
| 颜色图例 | `map.js` + `style.css` | 地图左上角自动显示品类颜色图例 |
| 路段 hover tooltip | `map.js` | hover 路段时显示交通方式 + 时间 |
| 路线分段渲染 | `map.js` | 不同颜色代表不同交通方式 |
| 推荐 POI 标记 | `map.js` | 虚线圆圈 + 品类颜色 + hover 显示"点击加入路线" |
| 侧边栏拖拽排序 | `route-editor.js` | HTML5 DnD 自由调整游览顺序 |
| 页面加载即显示地图 | `app.js` + `style.css` | editor-layout 默认可见 |

## 技术栈

| 层级 | 技术 |
|------|------|
| LLM | LongCat (Anthropic 兼容 API) — tool_use / system prompt / 自动重试 |
| 地图 | 高德开放平台 — 地理编码 / POI 搜索 / 步行路径 / 逆地理编码 |
| 数据库 | SQLite + Haversine 空间查询 — 14,222 条西安 POI / 995 个聚类簇 |
| Web | FastAPI + Jinja2 + SSE 流式推送 |
| 前端 | Leaflet.js 地图 + Mermaid.js 路线图 + HTML5 DnD |
| 聚类 | 网格法预计算 (995簇) + 走廊投影排序 |
| 部署 | Docker + docker-compose + 一键部署脚本 |
| 个性化 | 用户画像学习 (品类/区域/预算) + LLM context 注入 |
