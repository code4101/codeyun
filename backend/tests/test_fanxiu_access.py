"""权限判定使用真实数据库，不能伴随账号或调用方数据变更。"""
import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine, select

from backend.api.fanxiu_access import FANXIU_USERNAME, ensure_fanxiu_write_permission
from backend.models import User


@pytest.mark.parametrize(("owner_exists", "role", "allowed"), [
    (False, "outsider", False),
    (False, "admin", True),
    (True, "outsider", False),
    (True, "admin", True),
    (True, "owner", True),
])
def test_permission_check_never_creates_accounts_or_synchronizes_credentials(owner_exists, role, allowed):
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            admin = User(id=1, username="code4101", hashed_password="admin-hash", password_plain="admin-plain", is_superuser=True)
            outsider = User(id=3, username="outsider", hashed_password="x")
            owner = User(id=2, username=FANXIU_USERNAME, hashed_password="owner-hash", password_plain="owner-plain")
            session.add_all([admin, outsider] + ([owner] if owner_exists else []))
            session.commit()
            before = {user.id: user.model_dump() for user in session.exec(select(User)).all()}
            current = {"admin": admin, "owner": owner, "outsider": outsider}[role]
            session.expire(current)
            pending = User(id=4, username="unrelated-pending", hashed_password="pending")
            session.add(pending)
            if allowed:
                ensure_fanxiu_write_permission(current, session)
            else:
                with pytest.raises(HTTPException) as error:
                    ensure_fanxiu_write_permission(current, session)
                assert error.value.status_code == 403
            assert list(session.new) == [pending]
            assert not session.dirty and not session.deleted
            session.rollback()
            session.expire_all()
            assert {user.id: user.model_dump() for user in session.exec(select(User)).all()} == before
    finally:
        engine.dispose()
