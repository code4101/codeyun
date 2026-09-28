from datetime import timedelta
import time
import re
from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query, status, UploadFile, File, Response
from fastapi.responses import FileResponse
from backend.core.access.avatars import MAX_AVATAR_BYTES, avatar_path, avatar_url, save_avatar, reset_avatar
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from backend.core.access.password_strength import assess_password, generate_password
from sqlmodel import Session, select

from ..db import get_session
from backend.core.access.auth import (
    create_user_access_token,
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


def user_profile(user: User) -> UserRead:
    # Legacy records may not have a known password. Never report these as safe,
    # nor evaluate a stale plaintext value that no longer matches the login hash.
    needs_reset = None
    if user.password_plain and user.password_plain != '未知':
        try:
            if verify_password(user.password_plain, user.hashed_password):
                needs_reset = not assess_password(user.password_plain, user.username)['accepted']
        except (ValueError, TypeError):
            pass
    return UserRead.model_validate(user).model_copy(update={
        'avatar_url': avatar_url(user.id), 'password_needs_reset': needs_reset,
    })


@router.post('/me/avatar', response_model=UserRead)
def upload_my_avatar(file: UploadFile = File(...), current_user: User = Depends(get_current_active_user)):
    try:
        content = file.file.read(MAX_AVATAR_BYTES + 1)
        save_avatar(current_user.id, content)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        file.file.close()
    return user_profile(current_user)


@router.delete('/me/avatar', response_model=UserRead)
def reset_my_avatar(current_user: User = Depends(get_current_active_user)):
    reset_avatar(current_user.id)
    return user_profile(current_user)


@router.get('/avatars/{user_id}')
def read_avatar(user_id: int):
    if user_id <= 0 or not avatar_path(user_id).is_file():
        raise HTTPException(404, '头像不存在')
    return FileResponse(avatar_path(user_id), media_type='image/webp', headers={'Cache-Control': 'no-cache', 'X-Content-Type-Options': 'nosniff'})


class MyProfileUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    username: str = Field(default='', min_length=1, max_length=80, pattern=r'^\S+$')
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
    new_password: str = Field(min_length=10, max_length=72)
    confirm_password: str = Field(min_length=10, max_length=72)

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
    changes = payload.model_dump(exclude_unset=True)
    if 'username' in changes:
        existing = session.exec(select(User.id).where(User.username == changes['username'], User.id != current_user.id)).first()
        if existing is not None:
            raise HTTPException(409, '该账号名已被使用')
    for key, value in changes.items():
        setattr(current_user, key, value)
    current_user.updated_at = time.time()
    session.add(current_user)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, '该账号名已被使用')
    session.refresh(current_user)
    return user_profile(current_user)


@router.post('/me/password', status_code=204)
def change_my_password(payload: MyPasswordUpdate, session: Session = Depends(get_session),
                       current_user: User = Depends(get_current_active_user)):
    if payload.new_password != payload.confirm_password:
        raise HTTPException(400, '两次输入的新密码不一致')
    strength = assess_password(payload.new_password, current_user.username)
    if not strength['accepted']:
        raise HTTPException(422, '；'.join(strength['reasons']))
    current_user.hashed_password = get_password_hash(payload.new_password)
    current_user.password_plain = payload.new_password
    current_user.updated_at = time.time()
    session.add(current_user)
    session.commit()


class PasswordAssessment(BaseModel):
    model_config = ConfigDict(extra='forbid')
    password: str = Field(max_length=72)


@router.post('/me/password-strength')
def password_strength(payload: PasswordAssessment, current_user: User = Depends(get_current_active_user)):
    return assess_password(payload.password, current_user.username)


@router.post('/me/password-generate')
def generate_my_password(response: Response, current_user: User = Depends(get_current_active_user)):
    """Offer a password without changing the account until the user confirms saving."""
    response.headers['Cache-Control'] = 'no-store'
    return {'password': generate_password(current_user.username)}


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
    access_token = create_user_access_token(
        user, expires_delta=access_token_expires
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
    access_token = create_user_access_token(
        user, expires_delta=access_token_expires
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
    
    return user_profile(db_user)

@router.get("/me", response_model=UserRead)
def read_users_me(current_user: User = Depends(get_current_active_user)):
    return user_profile(current_user)


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
