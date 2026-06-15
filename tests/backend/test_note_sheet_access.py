from __future__ import annotations

from sqlmodel import select

from backend.core.notes.sheet_access import ensure_attendance_sheet_anonymous_viewer
from backend.models import ResourceAccessGrant, SheetDocument


def _anonymous_sheet_grant(session, sheet: SheetDocument) -> ResourceAccessGrant | None:
    return session.exec(
        select(ResourceAccessGrant)
        .where(ResourceAccessGrant.resource_type == "sheet")
        .where(ResourceAccessGrant.resource_id == str(sheet.numeric_id))
        .where(ResourceAccessGrant.subject_key == "anonymous")
    ).first()


def test_generated_attendance_sheet_defaults_to_anonymous_viewer(session):
    sheet = SheetDocument(
        id="attendance-sheet",
        numeric_id=4101,
        scope="notes",
        owner_type="course_workbook",
        owner_key="course-a",
        sheet_key="attendance",
        title="考勤表",
    )
    session.add(sheet)
    session.flush()

    grant = ensure_attendance_sheet_anonymous_viewer(session, sheet)
    session.commit()

    assert grant is not None
    stored = _anonymous_sheet_grant(session, sheet)
    assert stored is not None
    assert stored.subject_type == "anonymous"
    assert stored.subject_user_id is None
    assert stored.role == "viewer"


def test_generated_attendance_sheet_public_grant_is_normalized_to_viewer(session):
    sheet = SheetDocument(
        id="attendance-sheet",
        numeric_id=4102,
        scope="notes",
        owner_type="course_workbook",
        owner_key="course-a",
        sheet_key="attendance",
        title="考勤表",
    )
    session.add(sheet)
    session.add(
        ResourceAccessGrant(
            resource_type="sheet",
            resource_id="4102",
            subject_key="anonymous",
            subject_type="anonymous",
            role="deny",
        )
    )
    session.flush()

    ensure_attendance_sheet_anonymous_viewer(session, sheet)
    session.commit()

    assert _anonymous_sheet_grant(session, sheet).role == "viewer"


def test_non_course_workbook_attendance_title_is_not_auto_public(session):
    sheet = SheetDocument(
        id="user-sheet",
        numeric_id=4103,
        scope="notes",
        owner_type="user",
        owner_key="1",
        sheet_key="attendance",
        title="考勤表",
    )
    session.add(sheet)
    session.flush()

    grant = ensure_attendance_sheet_anonymous_viewer(session, sheet)
    session.commit()

    assert grant is None
    assert _anonymous_sheet_grant(session, sheet) is None
