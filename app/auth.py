"""认证模块 — 密码 bcrypt 哈希 + JWT 签发/验证 + FastAPI 依赖注入."""
import sqlite3
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

# 确保 .env 已加载（此模块可能在 config.py 之前被 import）
load_dotenv(Path(__file__).parent.parent / ".env")

# ── 密码哈希 ──────────────────────────────────────

_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str) -> str:
    return _pwd_ctx.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_ctx.verify(plain, hashed)

# ── JWT ───────────────────────────────────────────

_SECRET = os.getenv("JWT_SECRET", "jwt-secret-change-me")
_ALGORITHM = "HS256"
_EXPIRE_DAYS = 7

def create_token(username: str) -> str:
    payload = {
        "sub": username,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(days=_EXPIRE_DAYS),
    }
    return jwt.encode(payload, _SECRET, algorithm=_ALGORITHM)

def verify_token(token: str) -> str | None:
    """验证 token，返回 username 或 None."""
    try:
        payload = jwt.decode(token, _SECRET, algorithms=[_ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None

# ── 用户数据库 ─────────────────────────────────────

_AUTH_DB_DIR = Path(__file__).parent.parent / "db"
_AUTH_DB_PATH = _AUTH_DB_DIR / "auth.db"
_auth_db_initialized = False


def _init_auth_db():
    """首次使用时自动建表（模块级缓存，避免每次请求重连）."""
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

def create_user(username: str, password: str) -> dict:
    """注册新用户，返回 {'token', 'username'}。重名时抛 HTTPException."""
    _init_auth_db()
    conn = sqlite3.connect(str(_AUTH_DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        existing = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="用户名已被注册")
        pw_hash = hash_password(password)
        conn.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (username, pw_hash))
        conn.commit()
        token = create_token(username)
        return {"token": token, "username": username}
    finally:
        conn.close()

def authenticate_user(username: str, password: str) -> dict:
    """验证登录，返回 {'token', 'username'}。失败抛 HTTPException."""
    _init_auth_db()
    conn = sqlite3.connect(str(_AUTH_DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT password_hash FROM users WHERE username = ?", (username,)).fetchone()
        if not row or not verify_password(password, row["password_hash"]):
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        token = create_token(username)
        return {"token": token, "username": username}
    finally:
        conn.close()

# ── FastAPI 依赖注入 ───────────────────────────────

_bearer = HTTPBearer(auto_error=False)

async def get_current_user_optional(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Optional[str]:
    """从 Authorization header 提取 username（可选，无 token 返回 None）."""
    if credentials is None:
        return None
    username = verify_token(credentials.credentials)
    if username is None:
        raise HTTPException(status_code=401, detail="token 无效或已过期")
    return username

async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    """从 Authorization header 提取 username（必须，无 token 返回 401）."""
    if credentials is None:
        raise HTTPException(status_code=401, detail="请先登录")
    username = verify_token(credentials.credentials)
    if username is None:
        raise HTTPException(status_code=401, detail="token 无效或已过期")
    return username
