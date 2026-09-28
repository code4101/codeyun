from datetime import timedelta
import time
import re
from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import or_
from sqlmodel import Session, select

from ..db import get_session
from backend.core.access.auth import (
    create_access_token,
    verify_password,
    get_password_hash,
    get_current_active_user,
    ACCESS_TOKEN_EXPIRE_MINUTES
)
from ..models import User
from ..schemas import (
    AccountUserOption,
    AccountUserOptionsResponse,
    Token,
    UserCreate,
    UserRead,
    UserLogin,
)

router = APIRouter()


class MyProfileUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    nickname: str = Field(default='', max_length=80)
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=254)

    @field_validator('phone', 'email')
    @classmethod
    def normalize_contact(cls, value, info):
        if not value:
            return None
        if info.field_name == 'email' and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
            raise ValueError('邮箱格式不正确')
        if info.field_name == 'phone' and not re.fullmatch(r'\+?[0-9][0-9 ()-]{2,39}', value):
            raise ValueError('手机号格式不正确')
        return value


class MyPasswordUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=8, max_length=72)

    @field_validator('new_password')
    @classmethod
    def validate_password(cls, value):
        if len(value.encode('utf-8')) > 72:
            raise ValueError('密码 UTF-8 编码不能超过 72 字节')
        return value


@router.patch('/me', response_model=UserRead)
def update_my_profile(payload: MyProfileUpdate, session: Session = Depends(get_session),
                      current_user: User = Depends(get_current_active_user)):
    """Update only the current user's contact fields; no ownership or role changes."""
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(current_user, key, value)
    current_user.updated_at = time.time()
    session.add(current_user)
    session.commit()
    session.refresh(current_user)
    return current_user


@router.post('/me/password', status_code=204)
def change_my_password(payload: MyPasswordUpdate, session: Session = Depends(get_session),
                       current_user: User = Depends(get_current_active_user)):
    if not verify_password(payload.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail='当前密码不正确')
    current_user.hashed_password = get_password_hash(payload.new_password)
    current_user.password_plain = payload.new_password
    current_user.updated_at = time.time()
    session.add(current_user)
    session.commit()


def _sync_plain_password(session: Session, user: User, plain_password: str) -> None:
    if user.password_plain == plain_password:
        return

    user.password_plain = plain_password
    user.updated_at = time.time()
    session.add(user)
    session.commit()

@router.post("/login", response_model=Token)
def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: Session = Depends(get_session)
):
    # 1. Find user
    statement = select(User).where(User.username == form_data.username)
    user = session.exec(statement).first()
    
    # 2. Verify password
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
        
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")

    _sync_plain_password(session, user, form_data.password)
        
    # 3. Create access token
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    
    return {"access_token": access_token, "token_type": "bearer"}

@router.post("/login/json", response_model=Token)
def login_json(
    login_data: UserLogin,
    session: Session = Depends(get_session)
):
    # Same logic but for JSON body (Frontend uses this)
    statement = select(User).where(User.username == login_data.username)
    user = session.exec(statement).first()
    
    if not user or not verify_password(login_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )
    
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")

    _sync_plain_password(session, user, login_data.password)
        
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    
    return {"access_token": access_token, "token_type": "bearer"}

@router.post("/register", response_model=UserRead)
def register_user(
    user_in: UserCreate,
    session: Session = Depends(get_session)
):
    # Check existing
    statement = select(User).where(User.username == user_in.username)
    existing_user = session.exec(statement).first()
    if existing_user:
        raise HTTPException(
            status_code=400,
            detail="Username already registered",
        )
        
    # Create new
    hashed_password = get_password_hash(user_in.password)
    db_user = User(
        username=user_in.username,
        nickname=(user_in.nickname or "").strip(),
        phone=((user_in.phone or "").strip() or None),
        hashed_password=hashed_password,
        password_plain=user_in.password,
        email=user_in.email,
        is_active=user_in.is_active,
        is_superuser=False
    )
    
    session.add(db_user)
    session.commit()
    session.refresh(db_user)
    
    return db_user

@router.get("/me", response_model=UserRead)
def read_users_me(current_user: User = Depends(get_current_active_user)):
    return current_user


@router.get("/user-options", response_model=AccountUserOptionsResponse)
def list_account_user_options(
    q: str = Query(default="", max_length=100),
    limit: int = Query(default=30, ge=1, le=100),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    query_text = q.strip()
    statement = (
        select(User)
        .where(User.is_active == True)  # noqa: E712
        .where(User.id != current_user.id)
    )
    if query_text:
        pattern = f"%{query_text}%"
        statement = statement.where(
            or_(User.username.like(pattern), User.nickname.like(pattern))
        )
    users = session.exec(
        statement.order_by(User.username.asc(), User.id.asc()).limit(limit)
    ).all()
    return AccountUserOptionsResponse(
        users=[
            AccountUserOption(
                id=user.id or 0,
                username=user.username,
                nickname=user.nickname or "",
            )
            for user in users
            if user.id is not None
        ]
    )
