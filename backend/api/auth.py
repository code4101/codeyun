from datetime import timedelta
import time
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
            detail="Incorrect username or password",
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
            detail="Incorrect username or password",
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
