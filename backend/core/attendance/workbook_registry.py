"""Register attendance-owned sheets in CodeYun's access/navigation registry.

Only resource metadata is copied. Cell data stays in the attendance engine and
is read/written through the existing independent document adapter.
"""
from sqlmodel import Session, select

from backend.db import engine
from backend.models import SheetDocument, WorkbookDocument, WorkbookSheetLink


def reconcile_monthly_course_registry() -> list[int]:
    """Register attendance-created workbooks and inherit template access grants.

    This is a navigation/access projection only. Creating courses and repairing
    summary links remain atomic operations in the independent attendance API.
    """
    from backend.core.attendance.independent_engine_adapter import ensure_attendance_engine_importable
    ensure_attendance_engine_importable()
    from xlsln.kq5034.engine.client import LocalAttendanceSheetClient
    from xlsln.kq5034.engine.monthly_courses import list_monthly_course_manifests
    from backend.models import ResourceAccessGrant
    from backend.core.resources.identity import ensure_resource_identity
    from backend.core.resources.sheet_refs import workbook_ref_aliases, sheet_ref_aliases

    client = LocalAttendanceSheetClient()
    added = []
    route_changes = {}
    with Session(engine) as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        def copy_grants(kind, source_id, target_id):
            grants = session.exec(select(ResourceAccessGrant).where(
                ResourceAccessGrant.resource_type == kind,
                ResourceAccessGrant.resource_id == str(source_id),
            )).all()
            for grant in grants:
                values = grant.model_dump(exclude={"id"})
                values["resource_id"] = str(target_id)
                session.add(ResourceAccessGrant(**values))

        for manifest in list_monthly_course_manifests():
            for course in manifest["courses"]:
                wid = course["workbook_id"]
                source = session.exec(select(WorkbookDocument).where(
                    WorkbookDocument.numeric_id == course["source_workbook_id"],
                )).one()
                payload = client.get_workbook_document(wid)
                workbook = session.exec(select(WorkbookDocument).where(WorkbookDocument.numeric_id == wid)).first()
                if workbook is None:
                    pk = str(payload.get("legacy_id") or f"attendance:workbook:{wid}")
                    # Historical workbook route numbers predate the global ID
                    # registry and legitimately overlap unrelated sheet IDs.
                    ensure_resource_identity(session, "workbook", pk, None)
                    workbook = WorkbookDocument(id=str(wid), numeric_id=wid, legacy_id=pk,
                        title=payload["title"], owner_user_id=source.owner_user_id,
                        created_by_user_id=source.created_by_user_id)
                    session.add(workbook)
                    copy_grants("workbook", source.numeric_id, wid)
                    added.append(wid)
                    session.flush()
                elif workbook.title != payload["title"]:
                    raise ValueError(f"独立考勤工作簿身份冲突：{wid}")
                for position, item in enumerate(payload["sheets"]):
                    sid = item["id"]
                    sheet = session.exec(select(SheetDocument).where(SheetDocument.numeric_id == sid)).first()
                    if sheet is None:
                        pk = str(item.get("legacy_id") or f"attendance:sheet:{sid}")
                        assigned = ensure_resource_identity(session, "sheet", pk, sid)
                        if assigned != sid:
                            route_changes.setdefault(wid, {})[sid] = assigned
                            sid = assigned
                        sheet = session.exec(select(SheetDocument).where(SheetDocument.numeric_id == sid)).first()
                        if sheet is not None:
                            if (sheet.owner_key, sheet.sheet_key) != (item["owner_key"], item["sheet_key"]):
                                raise ValueError(f"独立考勤工作表身份冲突：{sid}")
                            continue
                        sheet = SheetDocument(id=str(sid), numeric_id=sid, legacy_id=pk,
                            scope=item["scope"], owner_type=item["owner_type"], owner_key=item["owner_key"],
                            sheet_key=item["sheet_key"], title=item["title"], engine=item["engine"],
                            document_json={}, owner_user_id=source.owner_user_id,
                            created_by_user_id=source.created_by_user_id)
                        session.add(sheet)
                        source_bundle = client.get_workbook_document(course["source_workbook_id"])
                        source_sheet = next(s for s in source_bundle["sheets"] if s["sheet_key"] == item["sheet_key"])
                        copy_grants("sheet", source_sheet["id"], sid)
                        session.flush()
                    elif (sheet.owner_key, sheet.sheet_key) != (item["owner_key"], item["sheet_key"]):
                        raise ValueError(f"独立考勤工作表身份冲突：{sid}")
                    link = session.exec(select(WorkbookSheetLink).where(
                        WorkbookSheetLink.workbook_id.in_(workbook_ref_aliases(workbook)),
                        WorkbookSheetLink.sheet_id.in_(sheet_ref_aliases(sheet)),
                    )).first()
                    if link is None:
                        session.add(WorkbookSheetLink(workbook_id=workbook.id, sheet_id=sheet.id, order_index=position))
        session.commit()
    from xlsln.kq5034.engine.monthly_courses import assign_monthly_sheet_routes
    for wid, routes in route_changes.items():
        assign_monthly_sheet_routes(workbook_id=wid, sheet_routes=routes)
    return added


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
