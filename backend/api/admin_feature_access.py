from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from backend.core.access.auth import get_current_active_superuser
from backend.core.access.feature_access import (
    FEATURE_ACCESS_SUBJECT_ANONYMOUS,
    FEATURE_ACCESS_SUBJECT_USER,
    build_feature_access_admin_subject_context,
    load_feature_access_registry,
    save_feature_access_policy_overrides,
    serialize_feature_access_registry,
)
from backend.db import get_session
from backend.models import User


router = APIRouter(
    tags=["admin-feature-access"],
    dependencies=[Depends(get_current_active_superuser)],
)


class FeatureAccessPolicyUpdateRequest(BaseModel):
    overrides: dict[str, Any] = Field(default_factory=dict)


def _get_target_user_or_404(session: Session, user_id: int) -> User:
    user = session.exec(select(User).where(User.id == user_id)).first()
    if user is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    return user


@router.get("/registry")
def get_feature_access_registry():
    return serialize_feature_access_registry(load_feature_access_registry())


@router.get("/subjects/anonymous")
def get_anonymous_feature_access_context(
    session: Session = Depends(get_session),
):
    return build_feature_access_admin_subject_context(
        session,
        subject_type=FEATURE_ACCESS_SUBJECT_ANONYMOUS,
    )


@router.put("/subjects/anonymous")
def update_anonymous_feature_access_context(
    payload: FeatureAccessPolicyUpdateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_superuser),
):
    save_feature_access_policy_overrides(
        session,
        subject_type=FEATURE_ACCESS_SUBJECT_ANONYMOUS,
        overrides=payload.overrides,
        updated_by_user_id=current_user.id,
    )
    return build_feature_access_admin_subject_context(
        session,
        subject_type=FEATURE_ACCESS_SUBJECT_ANONYMOUS,
    )


@router.get("/subjects/users/{user_id}")
def get_user_feature_access_context(
    user_id: int,
    session: Session = Depends(get_session),
):
    user = _get_target_user_or_404(session, user_id)
    return build_feature_access_admin_subject_context(
        session,
        subject_type=FEATURE_ACCESS_SUBJECT_USER,
        subject_user=user,
    )


@router.put("/subjects/users/{user_id}")
def update_user_feature_access_context(
    user_id: int,
    payload: FeatureAccessPolicyUpdateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_superuser),
):
    user = _get_target_user_or_404(session, user_id)
    if user.is_superuser:
        raise HTTPException(status_code=400, detail="超级管理员无需单独配置功能权限")

    save_feature_access_policy_overrides(
        session,
        subject_type=FEATURE_ACCESS_SUBJECT_USER,
        subject_user_id=user.id,
        overrides=payload.overrides,
        updated_by_user_id=current_user.id,
    )
    return build_feature_access_admin_subject_context(
        session,
        subject_type=FEATURE_ACCESS_SUBJECT_USER,
        subject_user=user,
    )
