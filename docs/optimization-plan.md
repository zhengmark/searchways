# 项目优化方案

> 基于 2026-05-08 完整性检查结果，共发现 6 个问题，按优先级排列。

---

## 🔴 问题 1：JWT_SECRET 未从 .env 加载（中）

**位置**：`app/auth.py:25`

**现象**：`auth.py` 在模块 import 时执行 `os.getenv("JWT_SECRET", "jwt-secret-change-me")`，此时期尚未调用 `load_dotenv()`，因为 `load_dotenv()` 在 `app/config.py` 中，而 `config.py` 的 import 链晚于 `auth.py`。如果在 `.env` 设了自定义密钥，不会生效。

**修复**：`app/auth.py` 顶部自己调用 `load_dotenv()`：
```python
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).parent.parent / ".env")
```

---

## 🟡 问题 2：用户名校验逻辑冗余（低）

**位置**：`web/routes/auth.py:21`

**现象**：两个逻辑等价的条件用 `and` 连接，可读性差且容易误导：
```python
if not v.replace("_", "").replace("-", "").isalnum() and not all(c.isalnum() or c in "_-" for c in v):
```

**修复**：简化为一条：
```python
if not all(c.isalnum() or c in "_-" for c in v):
```

---

## 🟡 问题 3：_init_auth_db 每次请求建连接（低）

**位置**：`app/auth.py:50-58`

**现象**：每次注册/登录调用 `_init_auth_db()` 都执行 `sqlite3.connect()` + schema check。当前用户量可忽略，但属于不必要的开销。

**修复**：加模块级缓存标志：
```python
_auth_db_initialized = False

def _init_auth_db():
    global _auth_db_initialized
    if _auth_db_initialized:
        return
    _AUTH_DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_AUTH_DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    schema = (_AUTH_DB_DIR / "auth_schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema)
    conn.commit()
    conn.close()
    _auth_db_initialized = True
```

---

## 🟡 问题 4：文档与代码不一致（低）

**位置**：`docs/CONTEXT.md`、`docs/ARCHITECTURE.md`

**现象**：文档仍描述旧多 Agent 架构（`poi_strategy_agent.py`、`reviewer_agent.py`、`modifier_agent.py`），实际已重构为统一 Agent (`route_agent.py`)。`app/core/` 目录中这些文件不存在。

**修复**：更新文档中的文件列表和架构描述，反映罗斯方案统一 Agent 架构。

---

## 🟢 问题 5：弹窗点击遮罩不关闭（体验）

**位置**：`web/templates/index.html` 的 `auth-overlay`、`web/static/js/auth.js`

**现象**：登录/注册弹窗打开后，点击灰色遮罩背景不会关闭弹窗，只能点 × 按钮。

**修复 1** — 遮罩加 `onclick`（JS 中处理）：
```javascript
// auth.js
document.getElementById('authOverlay').addEventListener('click', function(e) {
  if (e.target === this) hideAuthModal();
});
```
或在 overlay div 上直接加 `onclick="hideAuthModal()"`，并在 modal div 上阻止冒泡：
```html
<div class="auth-overlay" id="authOverlay" onclick="hideAuthModal()">
  <div class="auth-modal" onclick="event.stopPropagation()">
```

---

## 🟢 问题 6：注册无确认密码字段（体验）

**位置**：`web/templates/index.html` 的 auth-modal、`web/static/js/auth.js`

**现象**：注册时只有用户名 + 密码两个字段，手误输错密码无法校验。密码不符合预期时用户需要联系管理员重置。

**修复**（仅前端）：
- index.html 注册表单加 `<input type="password" id="authPassword2" placeholder="再次输入密码">`
- auth.js `submitAuth()` 在 `mode === 'register'` 时校验 `authPassword === authPassword2`，不一致时拒绝提交

---

## 总结

| 问题 | 严重度 | 工作量 | 建议 |
|------|--------|--------|------|
| 1. JWT_SECRET 加载顺序 | 中 | 3 行 | 立即修 |
| 2. 用户名校验冗余 | 低 | 1 行 | 顺手改 |
| 3. DB 初始化缓存 | 低 | 5 行 | 顺手改 |
| 4. 文档不一致 | 低 | 15 分钟 | 闲时改 |
| 5. 遮罩关闭弹窗 | 低 | 2 行 | 顺手改 |
| 6. 注册确认密码 | 低 | 10 行 | 顺手改 |

---

*生成时间：2026-05-08*
