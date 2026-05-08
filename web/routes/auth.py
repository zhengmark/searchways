"""认证 API 路由 — 注册 / 登录 / 验活."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from app.auth import create_user, authenticate_user, get_current_user_optional

router = APIRouter(prefix="/api/auth", tags=["auth"])


class AuthRequest(BaseModel):
    username: str
    password: str

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3 or len(v) > 20:
            raise ValueError("用户名需 3-20 个字符")
        if not all(c.isalnum() or c in "_-" for c in v):
            raise ValueError("用户名只能包含字母、数字、下划线和连字符")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 6:
            raise ValueError("密码至少 6 位")
        return v


@router.post("/register")
async def register(req: AuthRequest):
    try:
        return create_user(req.username, req.password)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/login")
async def login(req: AuthRequest):
    return authenticate_user(req.username, req.password)


@router.get("/me")
async def me(user: Optional[str] = Depends(get_current_user_optional)):
    if user is None:
        return {"username": None}
    return {"username": user}
