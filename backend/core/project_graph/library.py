"""Local administrative migration contract. Never exposed unauthenticated over HTTP."""
from sqlmodel import Session, select
from backend.models import AppSetting, User


def designate_legacy_browser_owner(username: str) -> int:
    """Explicitly assign the ownerless pre-server browser library to its known owner.

    The browser keeps its source data; imports into the server are name-idempotent.
    This changes no users, permissions, documents or resource identifiers.
    """
    from backend.db import engine
    with Session(engine) as session:
        user = session.exec(select(User).where(User.username == username, User.is_active == True)).first()
        if not user:
            raise ValueError('用户不存在或未启用')
        key = 'project-graph.legacy-browser-owner'
        current = session.get(AppSetting, key)
        if current and current.value.get('owner_id') != user.id:
            raise ValueError('旧浏览器文件已指定其他所有者')
        session.add(AppSetting(key=key, value={'owner_id': user.id}) if not current else current)
        session.commit()
        return user.id
