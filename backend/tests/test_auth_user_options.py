from sqlmodel import Session, create_engine

from backend.api.auth import list_account_user_options
from backend.models import User


def _engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    User.__table__.create(engine, checkfirst=True)
    return engine


def test_account_user_options_search_username_and_nickname_and_exclude_self():
    with Session(_engine()) as session:
        current_user = User(username="owner", nickname="书柜主人", hashed_password="test")
        naiya = User(username="naiya0721", nickname="奈亚", hashed_password="test")
        another = User(username="reader", nickname="另一个读者", hashed_password="test")
        inactive = User(
            username="naiya-disabled",
            nickname="奈亚停用",
            hashed_password="test",
            is_active=False,
        )
        session.add_all([current_user, naiya, another, inactive])
        session.commit()
        session.refresh(current_user)

        by_username = list_account_user_options(
            q="nai",
            limit=30,
            session=session,
            current_user=current_user,
        )
        by_nickname = list_account_user_options(
            q="奈亚",
            limit=30,
            session=session,
            current_user=current_user,
        )
        all_users = list_account_user_options(
            q="",
            limit=30,
            session=session,
            current_user=current_user,
        )

        assert [user.username for user in by_username.users] == ["naiya0721"]
        assert [user.username for user in by_nickname.users] == ["naiya0721"]
        assert [user.username for user in all_users.users] == ["naiya0721", "reader"]
        assert by_nickname.users[0].nickname == "奈亚"
