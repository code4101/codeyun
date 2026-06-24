from sqlmodel import Session, select

from backend.core.auth import get_password_hash
from backend.core.settings import get_settings
from backend.db import engine
from backend.models import User


def ensure_bootstrap_admin() -> None:
    settings = get_settings()
    username = settings.bootstrap_admin_username
    password = settings.bootstrap_admin_password

    if not username or not password:
        return

    with Session(engine) as session:
        user = session.exec(select(User).where(User.username == username)).first()

        if user is None:
            user = User(
                username=username,
                hashed_password=get_password_hash(password),
                email=None,
                is_active=True,
                is_superuser=True,
            )
            session.add(user)
            session.commit()
            return

        changed = False
        if not user.is_superuser:
            user.is_superuser = True
            changed = True
        if not user.is_active:
            user.is_active = True
            changed = True
        if settings.bootstrap_admin_force_reset_password:
            user.hashed_password = get_password_hash(password)
            changed = True

        if changed:
            session.add(user)
            session.commit()
