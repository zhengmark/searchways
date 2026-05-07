"""用户画像持久化管理.

=== 接口文档 ===

## 快速集成（当前版本）
本项目暂未接入用户登录系统，默认使用 `UserProfileManager(user_id="default")`.
后续接入时只需更改 user_id 参数即可，无需修改其他代码:

    # 当前（无登录）
    mgr = UserProfileManager(user_id="default")

    # 未来（接入登录后）
    mgr = UserProfileManager(user_id=login_state.user_id)

## 文件位置
用户画像存储在 `users/{user_id}.json`，由 UserProfileManager 自动管理。

## 数据结构（users/{user_id}.json）
    {
      "user_id": "default",
      "created_at": "2026-05-02T10:00:00",
      "updated_at": "2026-05-02T12:30:00",
      "profile": {                          // 用户画像（见 multi_agent/types.py:UserProfile）
        "group_type": "solo",              // solo/couple/family/friends
        "age_preference": "all",           // all/young/middle/senior
        "energy_level": "medium",          // low/medium/high
        "budget_level": "medium",          // low/medium/high
        "interests": ["美食", "文化"],      // 兴趣标签
        "notes": ""                         // 饮食/无障碍等备注
      },
      "favorites": {                        // 偏好地点（运行时自动积累）
        "origins": ["丈八六路地铁站"],
        "destinations": ["钟楼"],
        "pois": ["回民街"],
        "keywords": ["美食", "咖啡"]
      },
      "history": [                          // 出行历史（自动压缩至 256KB）
        {
          "timestamp": "2026-05-02T10:30:00",
          "user_input": "从丈八六路出发去浐灞玩",
          "city": "西安",
          "origin": "丈八六路地铁站",
          "destination": "浐灞",
          "stops": ["回民街", "大雁塔"],
          "duration_min": 58,
          "review_score": 4.5
        }
      ],
      "session": {                          // 当前会话状态（跨重启恢复）
        "city": "西安",
        "origin": "丈八六路地铁站",
        "origin_coords": [34.261, 108.940],
        "destination": "钟楼",
        "dest_coords": [34.260, 108.942],
        "last_stops": ["回民街", "大雁塔"],
        "last_user_input": "从丈八六路出发去浐灞玩",
        "num_stops": 3,
        "messages": []                      // 对话历史（最多保留 6 条）
      }
    }

## 外部接口（供 login 模块或其他服务调用）

### UserProfileManager(user_id: str)
    构造函数，传入用户 ID。文件不存在时自动创建默认画像。

### mgr.load() -> dict
    读取用户画像文件，返回完整字典。

### mgr.save(data: dict)
    写入用户画像文件（自动序列化 datetime，压缩历史记录）。

### mgr.update_profile(profile: UserProfile)
    更新用户画像字段（增量合并，不覆盖未提供的字段）。

### mgr.add_to_favorites(category: str, item: str)
    将地点/关键词添加到对应收藏列表（去重，最多 20 条）。

### mgr.add_history(entry: dict)
    添加出行记录到历史列表。自动压缩到 _MAX_HISTORY_BYTES (256KB)。
    压缩策略：先按时间排序，保留最新的记录直至大小不超标。

### mgr.save_session(session_state: dict)
    保存当前会话状态，供程序重启后恢复多轮对话。

### mgr.load_session() -> dict | None
    读取上次会话状态。文件不存在或无 session 时返回 None。

## 压缩策略
- 文件总大小上限 _MAX_FILE_BYTES = 256KB
- 历史记录超过限制时，保留最新记录，删除最旧的
- session 和 profile 不参与压缩（始终保留）
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from app.core.types import UserProfile

# 用户画像存储目录
_USERS_DIR = Path(__file__).parent.parent / "data" / "users"

# 单用户文件上限 256KB
_MAX_FILE_BYTES = 256 * 1024

# 收藏列表上限
_MAX_FAVORITES = 20

# 历史记录上限（条数软限制，优先按大小压缩）
_MAX_HISTORY_ITEMS = 100


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_profile() -> dict:
    return {
        "user_id": "default",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "profile": UserProfile().model_dump(),
        "favorites": {
            "origins": [],
            "destinations": [],
            "pois": [],
            "keywords": [],
        },
        "history": [],
        "session": {},
    }


class UserProfileManager:
    """用户画像管理器.

    用法:
        mgr = UserProfileManager(user_id="default")
        data = mgr.load()
        mgr.update_profile(some_user_profile_object)
        mgr.add_to_favorites("pois", "回民街")
        mgr.add_history({"timestamp": ..., "user_input": ..., ...})
        mgr.save_session({"city": "西安", "origin": "丈八六路", ...})
        mgr.save(data)
    """

    def __init__(self, user_id: str = "default"):
        self.user_id = user_id
        self.file_path = _USERS_DIR / f"{user_id}.json"

    # ── 基础 I/O ──────────────────────────────────────

    def load(self) -> dict:
        """加载用户画像，文件不存在时创建默认画像并写入磁盘."""
        _USERS_DIR.mkdir(parents=True, exist_ok=True)
        if not self.file_path.exists():
            data = _default_profile()
            data["user_id"] = self.user_id
            self._write(data)
            return data
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            data = _default_profile()
            data["user_id"] = self.user_id
            return data

    def save(self, data: dict):
        """保存用户画像到磁盘（自动压缩历史记录）."""
        data["updated_at"] = _now_iso()
        self._write(data)

    def _write(self, data: dict):
        """写入文件，自动压缩至大小限制内."""
        _USERS_DIR.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(data, ensure_ascii=False, indent=2)

        # 超出大小限制则压缩历史记录
        if len(serialized.encode("utf-8")) > _MAX_FILE_BYTES:
            data = self._compress_history(data)
            serialized = json.dumps(data, ensure_ascii=False, indent=2)

        with open(self.file_path, "w", encoding="utf-8") as f:
            f.write(serialized)

    # ── 画像更新 ──────────────────────────────────────

    def update_profile(self, profile: UserProfile):
        """增量合并用户画像（只更新传入的非默认字段）."""
        data = self.load()
        existing = data.get("profile", {})
        new_fields = profile.model_dump()
        # 只覆盖非默认值
        for key, value in new_fields.items():
            if value and value != _default_profile()["profile"].get(key):
                existing[key] = value
        data["profile"] = existing
        self.save(data)

    # ── 收藏管理 ──────────────────────────────────────

    def add_to_favorites(self, category: str, item: str):
        """将地点/关键词添加到收藏列表（去重，最多 20 条）."""
        if category not in ("origins", "destinations", "pois", "keywords"):
            raise ValueError(f"Invalid favorites category: {category}")
        data = self.load()
        fav_list = data["favorites"].get(category, [])
        if item in fav_list:
            fav_list.remove(item)
        fav_list.insert(0, item)
        data["favorites"][category] = fav_list[:_MAX_FAVORITES]
        self.save(data)

    # ── 历史管理 ──────────────────────────────────────

    def add_history(self, entry: dict):
        """添加出行记录。自动压缩至 _MAX_FILE_BYTES 以内."""
        data = self.load()
        entry.setdefault("timestamp", _now_iso())
        data["history"].insert(0, entry)
        if len(data["history"]) > _MAX_HISTORY_ITEMS:
            data["history"] = data["history"][:_MAX_HISTORY_ITEMS]
        self.save(data)

    def _compress_history(self, data: dict) -> dict:
        """压缩历史记录：保留最新记录，直到文件大小降至限制内."""
        history = data.get("history", [])
        if not history:
            return data

        # 二分查找可以保留的条数
        lo, hi = 0, len(history)
        while lo < hi:
            mid = (lo + hi) // 2
            test_data = dict(data)
            test_data["history"] = history[:mid] if mid > 0 else []
            size = len(json.dumps(test_data, ensure_ascii=False, indent=2).encode("utf-8"))
            if size <= _MAX_FILE_BYTES:
                lo = mid + 1
            else:
                hi = mid

        keep = max(lo - 1, 0)
        data["history"] = history[:keep] if keep > 0 else []
        return data

    # ── 会话持久化 ──────────────────────────────────────

    def save_session(self, session_state: dict):
        """保存当前会话状态到文件."""
        data = self.load()
        data["session"] = session_state
        self.save(data)

    def load_session(self) -> dict:
        """读取上次会话状态。无 session 时返回空 dict."""
        data = self.load()
        return data.get("session", {})

    # ── 重置 ──────────────────────────────────────────

    def reset(self):
        """重置用户画像为默认值."""
        data = _default_profile()
        data["user_id"] = self.user_id
        self._write(data)
