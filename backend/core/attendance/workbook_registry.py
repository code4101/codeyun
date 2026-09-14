"""Register attendance-owned sheets in CodeYun's access/navigation registry.

Only resource metadata is copied. Cell data stays in the attendance engine and
is read/written through the existing independent document adapter.
"""
from sqlmodel import Session, select

from backend.db import engine
from backend.models import SheetDocument, WorkbookDocument, WorkbookSheetLink


def register_attendance_workbook_sheets(*, workbook_id: int) -> list[int]:
    """Local provider action: attach missing metadata shells, idempotently.

    Requires an existing CodeYun workbook. Numeric identity collisions fail;
    it never replaces an unrelated resource or copies attendance cell data.
    """
    from backend.core.attendance.independent_engine_adapter import ensure_attendance_engine_importable
    ensure_attendance_engine_importable()
    from xlsln.kq5034.engine.client import LocalAttendanceSheetClient
    from backend.core.resources.sheet_refs import workbook_ref_aliases, sheet_ref_aliases
    from backend.core.resources.identity import ensure_resource_identity, RESOURCE_TYPE_SHEET

    source = LocalAttendanceSheetClient().get_workbook_document(workbook_id)
    added = []
    with Session(engine) as session:
        workbook = session.exec(select(WorkbookDocument).where(WorkbookDocument.numeric_id == workbook_id)).one()
        for position, item in enumerate(source["sheets"]):
            # Core course resources are registered by their creation/migration
            # flow. This action provisions only the optional auxiliary registry.
            if item["sheet_key"] != "attendance_corrections":
                continue
            sheet = session.exec(select(SheetDocument).where(SheetDocument.numeric_id == item["id"])).first()
            if sheet is not None and (sheet.owner_key != item["owner_key"] or sheet.sheet_key != item["sheet_key"]):
                raise ValueError(f"工作表ID冲突：{item['id']}")
            if sheet is None:
                legacy_id = f"attendance:{item['owner_key']}:{item['sheet_key']}"
                registered_id = ensure_resource_identity(session, RESOURCE_TYPE_SHEET, legacy_id, item["id"])
                if registered_id != item["id"]:
                    LocalAttendanceSheetClient().assign_sheet_route_id(
                        sheet_id=item["id"], route_id=registered_id, expected_sheet_key=item["sheet_key"],
                    )
                    item["id"] = registered_id
                sheet = SheetDocument(
                    id=str(item["id"]), numeric_id=item["id"], legacy_id=legacy_id,
                    title=item["title"], scope=item["scope"],
                    owner_type=item["owner_type"], owner_key=item["owner_key"], sheet_key=item["sheet_key"],
                    engine=item["engine"], document_json={}, owner_user_id=workbook.owner_user_id,
                    created_by_user_id=workbook.created_by_user_id,
                )
                session.add(sheet)
                session.flush()
                added.append(item["id"])
            link = session.exec(select(WorkbookSheetLink).where(
                WorkbookSheetLink.workbook_id.in_(workbook_ref_aliases(workbook)),
                WorkbookSheetLink.sheet_id.in_(sheet_ref_aliases(sheet)),
            )).first()
            if link is None:
                link = WorkbookSheetLink(workbook_id=str(workbook.numeric_id), sheet_id=str(sheet.numeric_id))
            others = session.exec(select(WorkbookSheetLink).where(
                WorkbookSheetLink.workbook_id.in_(workbook_ref_aliases(workbook)),
                WorkbookSheetLink.id != link.id,
            )).all()
            link.order_index = max((x.order_index for x in others), default=0) + 1
            session.add(link)
        session.commit()
    return added
