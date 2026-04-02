from __future__ import annotations

from backend.models import User


def can_manage_attendance_service(user: User | None) -> bool:
    return bool(user and user.is_superuser)


def can_use_attendance_service(user: User | None, *, granted_user_ids: list[int] | None = None) -> bool:
    if not user:
        return False
    if user.is_superuser:
        return True
    allowed = {int(item) for item in (granted_user_ids or []) if isinstance(item, int)}
    return user.id in allowed
