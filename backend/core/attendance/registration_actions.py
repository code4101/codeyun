"""Compatibility adapter: keep registration API semantics over attendance storage.

CodeYun owns access, existing matching rules and task state; the attendance
client owns master data and atomic persistence. No second roster implementation
or writes to the old CodeYun sheet copy are allowed here.
"""
from fastapi import HTTPException


def run_independent_registration_action(*, session, current_user, document, access,
                                        workbook, workbook_id, action,
                                        use_browser_fallback, sync_attendance=False,
                                        is_current=lambda: True):
    """Return None for a legacy sheet, otherwise execute the existing action."""
    from backend.api import note_sheets as api

    sheet_id = api._require_sheet_numeric_id(document)
    source = api._bind_independent_attendance_document(document, sheet_id=sheet_id, workbook_id=workbook_id)
    if source is None:
        return None
    from xlsln.kq5034.engine.client import LocalAttendanceSheetClient, AttendanceVersionConflict
    if not access.capabilities.can_edit_data or not access.capabilities.can_run_sheet_actions:
        raise HTTPException(status_code=403, detail="没有执行报名表动作的权限")
    client = LocalAttendanceSheetClient()
    current = api._normalize_document_json(dict(source["document_json"]))
    summaries = []
    next_document = current
    if action in (api.NOTE_SHEET_CELL_ACTION_REGISTRATION_ORDER_MATCH,
                  api.NOTE_SHEET_CELL_ACTION_REGISTRATION_COMPOSITE_UPDATE):
        next_document, summary = api._update_registration_order_match_document(
            next_document, session=session, current_user=current_user,
            use_browser_fallback=use_browser_fallback, lookup_provider=client.lookup_payment_order)
        summaries.append(summary)
    if action in (api.NOTE_SHEET_CELL_ACTION_REGISTRATION_USER_MATCH,
                  api.NOTE_SHEET_CELL_ACTION_REGISTRATION_COMPOSITE_UPDATE):
        next_document, summary = api._update_registration_user_match_document(
            next_document, session=session, current_user=current_user,
            course_name=api._get_registration_course_name(document, workbook),
            shop_id=api._resolve_registration_shop_id(session, document, workbook),
            use_browser_fallback=use_browser_fallback, lookup_provider=client.lookup_registration_user)
        summaries.append(summary)
    if not summaries:
        raise HTTPException(status_code=400, detail="不支持的报名表动作")
    changes = [dict(sheet_id=sheet_id, expected_version=source["version"], document_json=next_document)]
    affected = [(document, sheet_id)]
    if sync_attendance:
        attendance, attendance_workbook = api._resolve_registration_attendance_sheet(session, document, workbook)
        if attendance is None:
            raise HTTPException(status_code=404, detail="未找到同工作簿的考勤表")
        attendance_access = api._resolve_sheet_resource_access(session, attendance, current_user, workbook=attendance_workbook)
        if not attendance_access.capabilities.can_edit_data:
            raise HTTPException(status_code=403, detail="没有编辑考勤表的权限")
        att_id = api._require_sheet_numeric_id(attendance)
        att_source = api._bind_independent_attendance_document(attendance, sheet_id=att_id, workbook_id=workbook_id)
        if att_source is None:
            raise HTTPException(status_code=409, detail="报名表与考勤表必须属于同一存储运行时")
        next_attendance, summary = api._sync_registration_rows_to_attendance_document(next_document, att_source["document_json"])
        summaries.append(summary)
        changes.append(dict(sheet_id=att_id, expected_version=att_source["version"], document_json=next_attendance))
        affected.append((attendance, att_id))
    if not is_current():
        return {"cancelled": True}
    try:
        results = client.replace_documents(changes)
    except AttendanceVersionConflict as exc:
        raise HTTPException(status_code=409, detail="工作表数据已更新，整个匹配操作未写入，请重试") from exc
    for (shell, sid), result in zip(affected, results):
        api._bind_independent_attendance_document(shell, sheet_id=sid, workbook_id=workbook_id)
        if result["changed"]:
            api._broadcast_sheet_resource_update(shell)
    totals = {key: sum(s.get(key, 0) for s in summaries)
              for key in ("updated_count", "skipped_count", "error_count", "warning_count")}
    if action == api.NOTE_SHEET_CELL_ACTION_REGISTRATION_COMPOSITE_UPDATE:
        message = api._format_registration_composite_update_message(*summaries)
    else:
        formatter = (api._format_registration_order_match_message if action == api.NOTE_SHEET_CELL_ACTION_REGISTRATION_ORDER_MATCH
                     else api._format_registration_user_match_message)
        message = formatter(summaries[0])
        if sync_attendance:
            message += "；" + api._format_registration_attendance_sync_message(summaries[-1])
    return {**totals, "message": message, "cancelled": False}
