from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlmodel import Session

from backend.api import note_sheets as api
from backend.core.attendance.independent_engine_adapter import ensure_attendance_engine_importable
from backend.core.attendance.registration_actions import run_independent_registration_action


@pytest.fixture
def setup_roster(monkeypatch, tmp_path):
    ensure_attendance_engine_importable()
    from xlsln.kq5034.engine.client import LocalAttendanceSheetClient
    from xlsln.kq5034.engine.db import get_engine, init_db
    from xlsln.kq5034.engine.models import SheetDocument
    monkeypatch.setenv("KQ_DATABASE_PATH", str(tmp_path / "attendance.sqlite3"))
    init_db()
    columns = ["分组", "序号", "备注", "海外", "提交时间", "姓名", "微信昵称", "手机号",
               "错误手机号", "微信支付订单号", "订单日期", "商户订单号", "订单金额", "用户ID", "匹配得分", "已返款"]
    row = {"序号": "1", "姓名": "甲", "微信昵称": "甲", "手机号": "13800000000"}
    att_columns = ["分组", "学号", "姓名", "昵称", "用户ID", "订单金额", "已返款"]
    with Session(get_engine()) as session:
        for sid, title, cols, rows in [(991, "报名表", columns, [[row.get(c, "") for c in columns]]),
                                       (992, "考勤表", att_columns, [])]:
            session.add(SheetDocument(id=f"s{sid}", numeric_id=sid, sheet_key=title, title=title,
                document_json={"columns": cols, "rows": rows, "grid_rows": [cols, *rows],
                               "data_start_row": 1, "field_row_index": 0}))
        session.commit()
    client = LocalAttendanceSheetClient()
    shells = [api.SheetDocument(id=f"legacy{sid}", numeric_id=sid, title=title,
                               document_json={"columns": ["旧副本"], "rows": [["不能改"]]})
              for sid, title in [(991, "报名表"), (992, "考勤表")]]
    access = SimpleNamespace(capabilities=SimpleNamespace(can_edit_data=True, can_run_sheet_actions=True))
    monkeypatch.setattr(api, "_resolve_registration_attendance_sheet", lambda *a: (shells[1], None))
    monkeypatch.setattr(api, "_resolve_sheet_resource_access", lambda *a, **kw: access)
    monkeypatch.setattr(api, "_resolve_registration_shop_id", lambda *a: 1)
    monkeypatch.setattr(api, "_get_registration_course_name", lambda *a: "测试梵呗")
    monkeypatch.setattr(api, "_broadcast_sheet_resource_update", lambda *a, **kw: None)
    monkeypatch.setattr(LocalAttendanceSheetClient, "lookup_registration_user", lambda *a, **kw: ("u_test", 90))
    monkeypatch.setattr(LocalAttendanceSheetClient, "lookup_payment_order", lambda *a, **kw: {})
    # A legacy DB session must never be asked to persist independent sheet data.
    class NoLegacyWrites:
        def add(self, *a):
            raise AssertionError("legacy write")
        def commit(self):
            raise AssertionError("legacy commit")
    args = dict(session=NoLegacyWrites(), current_user=SimpleNamespace(id=1), document=shells[0],
                access=access, workbook=None, workbook_id=None, use_browser_fallback=False)
    return client, shells, args


def test_composite_action_uses_authoritative_data_and_is_idempotent(setup_roster):
    client, shells, args = setup_roster
    args.update(action="registration_composite_update", sync_attendance=True)
    result = run_independent_registration_action(**args)
    assert result["error_count"] == 0
    tables = [client.get_table(SimpleNamespace(sheet_id=sid, workbook_id=None)) for sid in (991, 992)]
    assert tables[0]["rows"][0]["用户ID"] == tables[1]["rows"][0]["用户ID"] == "u_test"
    versions = [t["version"] for t in tables]
    run_independent_registration_action(**args)
    assert [client.get_table(SimpleNamespace(sheet_id=sid, workbook_id=None))["version"] for sid in (991,992)] == versions


def test_cancelled_action_does_not_write_either_sheet(setup_roster):
    client, shells, args = setup_roster
    result = run_independent_registration_action(**args, action="registration_composite_update",
                                                sync_attendance=True, is_current=lambda: False)
    assert result["cancelled"]
    assert client.get_table(SimpleNamespace(sheet_id=991))["version"] == 1
    assert client.get_table(SimpleNamespace(sheet_id=992))["row_count"] == 0


def test_attendance_permission_is_checked_before_any_write(setup_roster, monkeypatch):
    client, shells, args = setup_roster
    monkeypatch.setattr(api, "_resolve_sheet_resource_access", lambda *a, **kw:
                        SimpleNamespace(capabilities=SimpleNamespace(can_edit_data=False)))
    with pytest.raises(HTTPException) as exc:
        run_independent_registration_action(**args, action="registration_composite_update", sync_attendance=True)
    assert exc.value.status_code == 403
    assert client.get_table(SimpleNamespace(sheet_id=991))["version"] == 1


def test_conflict_aborts_entire_multi_sheet_commit(setup_roster):
    from xlsln.kq5034.engine.client import AttendanceVersionConflict
    client, shells, args = setup_roster
    changes = [dict(sheet_id=sid, expected_version=1, document_json={"columns": ["x"], "rows": [[1]]}) for sid in (991,992)]
    changes[1]["expected_version"] = 99
    with pytest.raises(AttendanceVersionConflict):
        client.replace_documents(changes)
    assert client.get_table(SimpleNamespace(sheet_id=991))["version"] == 1


def test_rebuild_failure_rolls_back_identity_and_roster(setup_roster, monkeypatch):
    from xlsln.kq5034.engine import client as client_module
    client, shells, args = setup_roster
    def fail(*a, **kw):
        raise RuntimeError("rebuild failed")
    monkeypatch.setattr(client_module, "rebuild_fanbei_attendance_from_course_sheets", fail)
    doc = client.get_document(SimpleNamespace(sheet_id=991))
    with pytest.raises(RuntimeError, match="rebuild failed"):
        client.replace_documents([dict(sheet_id=991, expected_version=1, document_json={"columns": ["x"], "rows": [[1]]})],
                                 rebuild_attendance_sheet_id=992, course_name="测试梵呗")
    assert client.get_document(SimpleNamespace(sheet_id=991)) == doc


def http_client(monkeypatch, setup_roster):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    client, shells, args = setup_roster
    app = FastAPI()
    app.include_router(api.router, prefix="/api/note-sheets")
    app.dependency_overrides[api.get_session] = lambda: args["session"]
    app.dependency_overrides[api.get_current_active_user] = lambda: args["current_user"]
    monkeypatch.setattr(api, "_get_note_sheet_or_404", lambda *a, **kw: (shells[0], args["access"], None))
    monkeypatch.setattr(api, "_serialize_note_sheet_action_detail", lambda *a, **kw:
        api.NoteSheetDetailResponse(id=991, title="报名表", engine="handsontable", scope="notes",
                                    version=client.get_table(SimpleNamespace(sheet_id=991))["version"],
                                    created_at=1, updated_at=1, owner_type="", owner_key="", sheet_key=""))
    return TestClient(app)


@pytest.mark.parametrize("endpoint,action", [("update-user-match", "registration_user_match"),
                                             ("update-order-match", "registration_order_match")])
def test_original_synchronous_http_endpoints(setup_roster, monkeypatch, endpoint, action):
    http = http_client(monkeypatch, setup_roster)
    response = http.post(f"/api/note-sheets/sheets/991/registration/{endpoint}?use_browser_fallback=false")
    assert response.status_code == 200, response.text
    assert response.json()["action"] == action
    assert "sheet" in response.json() and "updated_count" in response.json()


@pytest.mark.parametrize("action", ["registration_user_match", "registration_order_match", "registration_composite_update"])
def test_original_async_http_dispatch_accepts_independent_sheets(setup_roster, monkeypatch, action):
    http = http_client(monkeypatch, setup_roster)
    calls = []
    def enqueue(**kwargs):
        calls.append(kwargs)
        return api.NoteSheetRegistrationMatchRunResponse(run_id="run-test", sheet_id=991,
                                                       action=kwargs["action"], status="pending")
    monkeypatch.setattr(api, "_start_registration_match_run", enqueue)
    response = http.post("/api/note-sheets/sheets/991/registration/match-runs",
                         json={"action": action, "use_browser_fallback": False})
    assert response.status_code == 200, response.text
    assert response.json()["run_id"] == "run-test"
    assert calls[0]["action"] == action


def test_course_update_preserves_target_and_response(setup_roster, monkeypatch):
    from xlsln.kq5034.engine.client import LocalAttendanceSheetClient
    http = http_client(monkeypatch, setup_roster)
    calls = []
    def update(self, **kwargs):
        calls.append(kwargs)
        return {"step2": {"message": "done"}, "step3": {"sheet_id": kwargs["sheet_id"]}}
    monkeypatch.setattr(LocalAttendanceSheetClient, "update_course_data", update)
    response = http.post("/api/note-sheets/sheets/991/attendance/course-update-data",
                         json={"course_type": "fanbei", "course_name": "测试梵呗", "include_frozen": True})
    assert response.status_code == 200, response.text
    assert response.json()["step3"]["sheet_id"] == 991
    assert calls == [dict(sheet_id=991, course_type="fanbei", course_name="测试梵呗", include_frozen=True)]


def test_original_http_actions_keep_permission_checks(setup_roster, monkeypatch):
    http = http_client(monkeypatch, setup_roster)
    setup_roster[2]["access"].capabilities.can_run_sheet_actions = False
    response = http.post("/api/note-sheets/sheets/991/registration/match-runs",
                         json={"action": "registration_composite_update"})
    assert response.status_code == 403
    assert setup_roster[0].get_table(SimpleNamespace(sheet_id=991))["version"] == 1


def test_user_id_detection_reads_source_and_updates_both_tables(setup_roster, monkeypatch):
    from xlsln.kq5034.engine import client as client_module
    http = http_client(monkeypatch, setup_roster)
    client, shells, args = setup_roster
    workbook = SimpleNamespace(title="测试梵呗", numeric_id=99)
    monkeypatch.setattr(api, "_get_note_sheet_or_404", lambda *a, **kw: (shells[0], args["access"], workbook))
    # The detection evidence comes from the independent source, not the stale shell.
    video = api.SheetDocument(numeric_id=993, title="视频数据", document_json={})
    from xlsln.kq5034.engine.db import get_engine
    from xlsln.kq5034.engine.models import SheetDocument
    with Session(get_engine()) as session:
        session.add(SheetDocument(id="video", numeric_id=993, sheet_key="video",
            title="视频数据", document_json={"columns": ["user_id2", "nickname"], "rows": [["u_correct", "甲"]]}))
        session.commit()
    monkeypatch.setattr(api, "_get_workbook_sheet_by_key_or_title", lambda *a, **kw:
                        {"attendance": shells[1], "video_data": video}.get(kw["sheet_key"]))
    evidence = []
    def candidates(row, progress):
        evidence.append(progress)
        assert "u_correct" in progress
        return [api.NoteSheetRegistrationUserIdDetectionCandidate(user_id="u_correct", video_count=1,
                                                                  confidence="high", evidence=["身份一致"])]
    monkeypatch.setattr(api, "_build_registration_user_id_detection_candidates", candidates)
    monkeypatch.setattr(client_module, "rebuild_fanbei_attendance_from_course_sheets", lambda *a, **kw: {"updated_rows": 1})
    response = http.post("/api/note-sheets/sheets/991/registration/detect-user-id",
                         json={"row_index": 0, "base_version": 1})
    assert response.status_code == 200, response.text
    assert response.json()["target_user_id"] == "u_correct"
    assert evidence
    assert client.get_table(SimpleNamespace(sheet_id=991))["rows"][0]["用户ID"] == "u_correct"
    assert client.get_table(SimpleNamespace(sheet_id=992))["rows"][0]["用户ID"] == "u_correct"


def test_workbook_names_live_in_attendance_and_conflicts_are_atomic(setup_roster):
    from xlsln.kq5034.engine.db import get_engine
    from xlsln.kq5034.engine.models import WorkbookDocument, WorkbookSheetLink
    from xlsln.kq5034.engine.client import AttendanceVersionConflict
    client, _, _ = setup_roster
    with Session(get_engine()) as session:
        session.add(WorkbookDocument(id="w", numeric_id=99, title="测试"))
        session.add(WorkbookSheetLink(workbook_id="w", sheet_id="s991"))
        session.commit()
    names = [dict(name="店铺ID", formula="2", comment="")]
    book = client.update_workbook_defined_names(workbook_id=99, names=names)
    assert book["defined_names"][0]["formula"] == "=2"
    with pytest.raises(AttendanceVersionConflict):
        client.update_workbook_defined_names(workbook_id=99, names=[dict(name="店铺ID", formula="1")],
            worksheets=[dict(sheet_id=991, sheet_version=999, names=[])])
    assert client.get_workbook_document(99)["defined_names"] == book["defined_names"]


def test_correct_primary_identity_updates_both_rows_and_rebuilds(setup_roster, monkeypatch):
    from xlsln.kq5034.engine.db import get_engine
    from xlsln.kq5034.engine.models import WorkbookDocument, WorkbookSheetLink
    from xlsln.kq5034.engine import client as client_module
    client, _, args = setup_roster
    run_independent_registration_action(**args, action="registration_composite_update", sync_attendance=True)
    with Session(get_engine()) as session:
        session.add(WorkbookDocument(id="w", numeric_id=99, title="测试梵呗"))
        for sid in (991, 992):
            session.add(WorkbookSheetLink(workbook_id="w", sheet_id=f"s{sid}"))
        session.commit()
    monkeypatch.setattr(client_module, "rebuild_fanbei_attendance_from_course_sheets", lambda *a, **kw: {"updated_rows": 1})
    result = client.correct_registration_user_id(workbook_id=99, student_id="1", expected_user_id="u_test", user_id="u_correct")
    assert result["rebuild"]["updated_rows"] == 1
    for sid in (991,992):
        assert client.get_table(SimpleNamespace(sheet_id=sid))["rows"][0]["用户ID"] == "u_correct"
