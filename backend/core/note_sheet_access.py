from __future__ import annotations

import time

from sqlmodel import Session, select

from backend.core.resource_identity import RESOURCE_TYPE_SHEET
from backend.core.sheet_refs import sheet_public_id
from backend.models import ResourceAccessGrant, SheetDocument

RESOURCE_ACCESS_SUBJECT_ANONYMOUS = "anonymous"
RESOURCE_ACCESS_ROLE_VIEWER = "viewer"


def is_generated_attendance_sheet(document: SheetDocument | None) -> bool:
    if document is None:
        return False
    title = str(document.title or "").strip()
    sheet_key = str(document.sheet_key or "").strip()
    return (
        document.scope == "notes"
        and document.owner_type == "course_workbook"
        and (sheet_key == "attendance" or title == "考勤表")
    )


def ensure_sheet_anonymous_viewer(
    session: Session,
    document: SheetDocument,
) -> ResourceAccessGrant:
    resource_id = sheet_public_id(document)
    if not resource_id:
        raise ValueError("表格资源编号缺失")

    grant = session.exec(
        select(ResourceAccessGrant)
        .where(ResourceAccessGrant.resource_type == RESOURCE_TYPE_SHEET)
        .where(ResourceAccessGrant.resource_id == resource_id)
        .where(ResourceAccessGrant.subject_key == RESOURCE_ACCESS_SUBJECT_ANONYMOUS)
    ).first()
    now = time.time()
    if grant is None:
        grant = ResourceAccessGrant(
            resource_type=RESOURCE_TYPE_SHEET,
            resource_id=resource_id,
            subject_key=RESOURCE_ACCESS_SUBJECT_ANONYMOUS,
            subject_type=RESOURCE_ACCESS_SUBJECT_ANONYMOUS,
            subject_user_id=None,
            role=RESOURCE_ACCESS_ROLE_VIEWER,
            created_at=now,
            updated_at=now,
        )
        session.add(grant)
        return grant

    if (
        grant.subject_type != RESOURCE_ACCESS_SUBJECT_ANONYMOUS
        or grant.subject_user_id is not None
        or grant.role != RESOURCE_ACCESS_ROLE_VIEWER
    ):
        grant.subject_type = RESOURCE_ACCESS_SUBJECT_ANONYMOUS
        grant.subject_user_id = None
        grant.role = RESOURCE_ACCESS_ROLE_VIEWER
        grant.updated_at = now
        session.add(grant)
    return grant


def ensure_attendance_sheet_anonymous_viewer(
    session: Session,
    document: SheetDocument | None,
) -> ResourceAccessGrant | None:
    if not is_generated_attendance_sheet(document):
        return None
    return ensure_sheet_anonymous_viewer(session, document)
