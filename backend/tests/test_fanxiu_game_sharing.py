from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlmodel import SQLModel, Session, create_engine

from backend.models import User
from backend.core.fanxiu.game import sharing
from backend.api.fanxiu_game_sharing import InputRequest


def test_grant_uses_account_id_and_can_revoke_without_elevation(tmp_path,monkeypatch):
    monkeypatch.setattr(sharing,'sharing_path',lambda:tmp_path/'sharing.json')
    engine=create_engine('sqlite://')
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        user=User(username='guest',hashed_password='unused',is_active=True,is_superuser=False)
        session.add(user)
        session.commit()
        session.refresh(user)
        assert sharing.game_sharing_permission(user)=={'view':False,'control':False}
        result=sharing.grant_game_sharing(session,'guest')
        assert result['user_id']==user.id
        assert sharing.require_game_sharing(user,control=True)['control']
        assert not user.is_superuser
        sharing.grant_game_sharing(session,'guest',view=False,control=False)
        with pytest.raises(HTTPException) as error:
            sharing.require_game_sharing(user)
        assert error.value.status_code==403
        with pytest.raises(ValueError):
            sharing.grant_game_sharing(session,'missing')


def test_inactive_account_cannot_use_existing_grant(tmp_path,monkeypatch):
    monkeypatch.setattr(sharing,'sharing_path',lambda:tmp_path/'sharing.json')
    user=User(id=5,username='guest',hashed_password='unused',is_active=False,is_superuser=True)
    with pytest.raises(HTTPException):
        sharing.require_game_sharing(user)


def test_input_requires_bounded_coordinates_and_complete_drag():
    base={'request_id':str(uuid4()),'x':.1,'y':.2,'width':900,'height':1600}
    assert InputRequest(**base).x==.1
    with pytest.raises(ValueError):
        InputRequest(**{**base,'x':1})
    with pytest.raises(ValueError):
        InputRequest(**{**base,'end_x':.3})
    with pytest.raises(ValueError):
        InputRequest(**{**base,'request_id':'../../anything'})
