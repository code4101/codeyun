from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException
from jose import JWTError

from backend.core.access.auth import get_optional_current_user_from_token


def test_optional_user_auth_allows_a_request_without_credentials():
    assert get_optional_current_user_from_token(token=None, session=Mock()) is None


def test_optional_user_auth_rejects_an_invalid_presented_token():
    with patch("backend.core.access.auth.jwt.decode", side_effect=JWTError("expired")):
        with pytest.raises(HTTPException) as exc_info:
            get_optional_current_user_from_token(token="expired-token", session=Mock())

    assert exc_info.value.status_code == 401


def test_optional_user_auth_rejects_a_token_for_a_missing_user():
    session = Mock()
    session.exec.return_value.first.return_value = None
    with patch("backend.core.access.auth.jwt.decode", return_value={"sub": "deleted-user"}):
        with pytest.raises(HTTPException) as exc_info:
            get_optional_current_user_from_token(token="valid-token", session=session)

    assert exc_info.value.status_code == 401
