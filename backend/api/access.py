from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session

from backend.core.auth import get_optional_current_user_from_token
from backend.core.feature_access import build_feature_access_subject_context
from backend.db import get_session
from backend.models import User


router = APIRouter(tags=["access"])


@router.get("/context")
def get_access_context(
    session: Session = Depends(get_session),
    current_user: User | None = Depends(get_optional_current_user_from_token),
):
    return build_feature_access_subject_context(
        session,
        current_user=current_user,
    )
