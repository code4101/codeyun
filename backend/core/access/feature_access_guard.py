from __future__ import annotations

from fastapi import Depends, HTTPException, status
from sqlmodel import Session

from backend.core.access.auth import get_optional_current_user_from_token
from backend.core.access.feature_access import is_feature_access_allowed
from backend.db import get_session
from backend.models import User


def ensure_feature_access(
    session: Session,
    *,
    feature_key: str,
    current_user: User | None,
) -> User | None:
    if not is_feature_access_allowed(
        session,
        feature_key=feature_key,
        current_user=current_user,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="当前账号无权访问该功能",
        )
    return current_user


def ensure_any_feature_access(
    session: Session,
    *,
    feature_keys: tuple[str, ...],
    current_user: User | None,
) -> User | None:
    if any(
        is_feature_access_allowed(
            session,
            feature_key=feature_key,
            current_user=current_user,
        )
        for feature_key in feature_keys
    ):
        return current_user

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="当前账号无权访问该功能",
    )


def require_feature_access_dependency(feature_key: str):
    async def dependency(
        session: Session = Depends(get_session),
        current_user: User | None = Depends(get_optional_current_user_from_token),
    ) -> User | None:
        return ensure_feature_access(
            session,
            feature_key=feature_key,
            current_user=current_user,
        )

    return dependency


def require_any_feature_access_dependency(*feature_keys: str):
    effective_feature_keys = tuple(
        feature_key.strip()
        for feature_key in feature_keys
        if feature_key and feature_key.strip()
    )

    async def dependency(
        session: Session = Depends(get_session),
        current_user: User | None = Depends(get_optional_current_user_from_token),
    ) -> User | None:
        return ensure_any_feature_access(
            session,
            feature_keys=effective_feature_keys,
            current_user=current_user,
        )

    return dependency
