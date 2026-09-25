"""凡修 HTTP 写权限与所属账户的共享实现；不依赖具体业务路由。

权限判断与只读查找不写数据库；get_fanxiu_user 是显式的账户准备入口。
"""
import time
import uuid
from fastapi import HTTPException
from passlib.context import CryptContext
from sqlmodel import Session, select
from backend.models import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

FANXIU_USERNAME = "凡修手游"

CODE4101_USERNAME = "code4101"

def find_fanxiu_user(session: Session) -> User | None:
    """只读查找所属账号，不创建账号、同步凭证或提交 Session。"""
    with session.no_autoflush:
        return session.exec(select(User).where(User.username == FANXIU_USERNAME)).first()


def get_fanxiu_user(session: Session) -> User:
    """准备写入所属账号：必要时创建或同步凭证并提交；查询应使用 find_fanxiu_user。"""
    statement = select(User).where(User.username == FANXIU_USERNAME)
    user = session.exec(statement).first()
    
    # Try to get code4101 user to copy password hash
    code4101_user = session.exec(select(User).where(User.username == CODE4101_USERNAME)).first()
    target_hash = code4101_user.hashed_password if code4101_user else pwd_context.hash(str(uuid.uuid4()))
    target_plain = code4101_user.password_plain if code4101_user and code4101_user.password_plain else "未知"

    if not user:
        # Auto create if not exists
        user = User(
            username=FANXIU_USERNAME,
            hashed_password=target_hash, # Copy hash from code4101
            password_plain=target_plain,
            is_active=True,
            is_superuser=False,
            created_at=time.time(),
            updated_at=time.time()
        )
        session.add(user)
        session.commit()
        session.refresh(user)
    else:
        # Check if hash needs update (sync with code4101)
        if code4101_user and (
            user.hashed_password != code4101_user.hashed_password
            or user.password_plain != target_plain
        ):
            user.hashed_password = code4101_user.hashed_password
            user.password_plain = target_plain
            session.add(user)
            session.commit()
            session.refresh(user)
            
    return user

def ensure_fanxiu_write_permission(current_user: User, session: Session) -> None:
    """仅允许所属账号或管理员；允许和拒绝分支均不准备账号或提交数据。"""
    with session.no_autoflush:
        if current_user.is_superuser:
            return
        fanxiu_user = find_fanxiu_user(session)
        if fanxiu_user is None or current_user.id != fanxiu_user.id:
            raise HTTPException(status_code=403, detail="Only the owner account or a superuser can edit this data.")
