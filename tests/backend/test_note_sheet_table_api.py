from __future__ import annotations

from datetime import date

from sqlmodel import select

from backend.api import note_sheets as note_sheets_api
from backend.models import SheetDocument, WorkbookDocument, WorkbookSheetLink


def _create_structured_sheet(session) -> None:
    workbook = WorkbookDocument(
        numeric_id=2,
        title="考勤汇总",
    )
    sheet = SheetDocument(
        numeric_id=4,
        scope="notes",
        owner_type="user",
        owner_key="1",
        sheet_key="4",
        title="课程",
        document_json={
            "columns": ["分组", "学号", "昵称", "商户订单号", "用户ID", "第01课"],
            "data_start_row": 3,
            "field_row_index": 1,
            "grid_rows": [
                ["用户信息", "", "", "", "", "打卡数据"],
                ["分组", "学号", "昵称", "商户订单号", "用户ID", "第01课"],
                ["", "", "考勤常见问题解答", "", "", ""],
                ["1组", 1, "阿丹", "", "u_1", ""],
                ["1组", 2, "着了迷", "T1", "u_2", ""],
            ],
            "rows": [
                ["1组", 1, "阿丹", "", "u_1", ""],
                ["1组", 2, "着了迷", "T1", "u_2", ""],
            ],
        },
        version=1,
    )
    session.add(workbook)
    session.add(sheet)
    session.flush()
    session.add(WorkbookSheetLink(workbook_id=workbook.id, sheet_id=sheet.id, order_index=10))
    session.commit()


def test_note_sheet_table_api_allows_trusted_device_read_and_patch(client, session, test_device, monkeypatch):
    _create_structured_sheet(session)
    broadcasts: list[tuple[str, dict]] = []

    async def fake_broadcast(room: str, message: dict) -> None:
        broadcasts.append((room, message))

    monkeypatch.setattr(note_sheets_api.ws_manager, "broadcast", fake_broadcast)

    read_response = client.get(
        "/api/note-sheets/sheets/4/table",
        params={"workbook_id": 2},
        headers={"X-Device-Token": test_device["token"]},
    )

    assert read_response.status_code == 200
    table = read_response.json()
    assert table["columns"] == ["分组", "学号", "昵称", "商户订单号", "用户ID", "第01课"]
    assert table["rows"][0]["_sheet_row"] == 4
    assert table["rows"][1]["用户ID"] == "u_2"

    patch_response = client.patch(
        "/api/note-sheets/sheets/4/table",
        params={"workbook_id": 2},
        headers={"X-Device-Token": test_device["token"]},
        json={
            "expected_version": 1,
            "operations": [
                {
                    "type": "write_fields",
                    "key_field": "用户ID",
                    "fields": ["第1课"],
                    "rows": [{"用户ID": "u_2", "第1课": 1}],
                },
                {
                    "type": "set_note_cell",
                    "field": "商户订单号",
                    "value": "2026/05/10 07:10",
                },
            ],
        },
    )

    assert patch_response.status_code == 200
    patched = patch_response.json()
    assert patched["updated_cell_count"] == 2
    assert patched["updated_row_count"] == 1
    assert patched["table"]["version"] == 2

    stored = session.exec(select(SheetDocument).where(SheetDocument.numeric_id == 4)).one()
    assert stored.document_json["rows"][1][5] == 1
    assert stored.document_json["grid_rows"][2][3] == "2026/05/10 07:10"
    assert broadcasts[-1][0] == "resource:sheet:4"
    assert broadcasts[-1][1]["type"] == "resource-updated"
    assert broadcasts[-1][1]["resource_type"] == "sheet"
    assert broadcasts[-1][1]["resource_id"] == "4"
    assert broadcasts[-1][1]["version"] == 2


def test_note_sheet_table_api_defaults_to_text_values_and_can_read_raw(client, session, test_device):
    workbook = WorkbookDocument(
        numeric_id=3,
        title="20260509梵呗初阶",
    )
    sheet = SheetDocument(
        numeric_id=5,
        scope="notes",
        owner_type="user",
        owner_key="1",
        sheet_key="5",
        title="考勤表",
        document_json={
            "columns": ["商户订单号", "当前应返款", "返款配置"],
            "data_start_row": 3,
            "field_row_index": 1,
            "grid_rows": [
                ["退款操作", "", ""],
                ["商户订单号", "当前应返款", "返款配置"],
                ["", '=DATEDIF("2026-05-09",TODAY(),"d")', ""],
                [
                    "TEEEL7-0OZRE8O-9IA3",
                    40,
                    '=IF(B4>0,TEXTJOIN(",",TRUE,A4,B4,"5月梵呗初阶第"&$B$3&"天返款",A4&"_day"&$B$3),"")',
                ],
            ],
            "rows": [
                [
                    "TEEEL7-0OZRE8O-9IA3",
                    40,
                    '=IF(B4>0,TEXTJOIN(",",TRUE,A4,B4,"5月梵呗初阶第"&$B$3&"天返款",A4&"_day"&$B$3),"")',
                ],
            ],
        },
        version=1,
    )
    session.add(workbook)
    session.add(sheet)
    session.flush()
    session.add(WorkbookSheetLink(workbook_id=workbook.id, sheet_id=sheet.id, order_index=10))
    session.commit()

    text_response = client.get(
        "/api/note-sheets/sheets/5/table",
        params={"workbook_id": 3, "include_grid": True},
        headers={"X-Device-Token": test_device["token"]},
    )

    assert text_response.status_code == 200
    text_table = text_response.json()
    day_index = max((date.today() - date(2026, 5, 9)).days, 0)
    assert text_table["value_mode"] == "text"
    assert text_table["grid_rows"][2][1] == day_index
    assert text_table["rows"][0]["返款配置"] == (
        f"TEEEL7-0OZRE8O-9IA3,40,5月梵呗初阶第{day_index}天返款,"
        f"TEEEL7-0OZRE8O-9IA3_day{day_index}"
    )

    raw_response = client.get(
        "/api/note-sheets/sheets/5/table",
        params={"workbook_id": 3, "value_mode": "raw", "include_grid": True},
        headers={"X-Device-Token": test_device["token"]},
    )

    assert raw_response.status_code == 200
    raw_table = raw_response.json()
    assert raw_table["value_mode"] == "raw"
    assert raw_table["grid_rows"][2][1] == '=DATEDIF("2026-05-09",TODAY(),"d")'
    assert raw_table["rows"][0]["返款配置"].startswith("=IF(")


def test_note_sheet_table_api_returns_evaluated_defined_name_values(client, session, test_device):
    workbook = WorkbookDocument(
        numeric_id=13,
        title="名称管理器计算值",
    )
    sheet = SheetDocument(
        numeric_id=14,
        scope="notes",
        owner_type="user",
        owner_key="1",
        sheet_key="14",
        title="考勤表",
        document_json={
            "columns": ["商户订单号", "当前应返款"],
            "data_start_row": 1,
            "field_row_index": 0,
            "defined_names": [
                {"name": "返款周期", "formula": "=3", "scope": "worksheet"},
                {"name": "返款说明", "formula": '="测试第"&返款周期&"天返款"', "scope": "worksheet"},
                {"name": "返款ID后缀", "formula": '="_day"&返款周期', "scope": "worksheet"},
            ],
            "grid_rows": [
                ["商户订单号", "当前应返款"],
                ["TEEEL7-0OZRE8O-9IA3", "=返款周期*10"],
                ["", 20],
                ["TZERO", 0],
            ],
            "rows": [
                ["TEEEL7-0OZRE8O-9IA3", "=返款周期*10"],
                ["", 20],
                ["TZERO", 0],
            ],
        },
        version=1,
    )
    session.add(workbook)
    session.add(sheet)
    session.flush()
    session.add(WorkbookSheetLink(workbook_id=workbook.id, sheet_id=sheet.id, order_index=10))
    session.commit()

    text_response = client.get(
        "/api/note-sheets/sheets/14/table",
        params={"workbook_id": 13, "include_grid": True},
        headers={"X-Device-Token": test_device["token"]},
    )

    assert text_response.status_code == 200
    text_table = text_response.json()
    assert text_table["columns"] == ["商户订单号", "当前应返款"]
    assert text_table["grid_rows"][0] == ["商户订单号", "当前应返款"]
    assert text_table["rows"][0]["当前应返款"] == 30
    assert text_table["defined_name_values"]["返款周期"] == 3
    assert text_table["defined_name_values"]["返款说明"] == "测试第3天返款"
    assert text_table["defined_name_values"]["返款id后缀"] == "_day3"

    raw_response = client.get(
        "/api/note-sheets/sheets/14/table",
        params={"workbook_id": 13, "value_mode": "raw"},
        headers={"X-Device-Token": test_device["token"]},
    )

    assert raw_response.status_code == 200
    raw_table = raw_response.json()
    assert raw_table["columns"] == ["商户订单号", "当前应返款"]
    assert raw_table["defined_name_values"] == {}


def test_note_sheet_table_api_normalizes_zen_stage_refund_defined_names(client, session, test_device):
    workbook = WorkbookDocument(
        numeric_id=15,
        title="修道班7期5阶",
    )
    sheet = SheetDocument(
        numeric_id=16,
        scope="notes",
        owner_type="user",
        owner_key="1",
        sheet_key="16",
        title="考勤表",
        document_json={
            "columns": ["返款配置"],
            "data_start_row": 1,
            "field_row_index": 0,
            "grid_rows": [
                ["返款配置"],
                ['=TEXTJOIN(",",TRUE,返款说明,"order"&返款ID后缀,返款周期)'],
            ],
            "rows": [
                ['=TEXTJOIN(",",TRUE,返款说明,"order"&返款ID后缀,返款周期)'],
            ],
        },
        version=1,
    )
    session.add(workbook)
    session.add(sheet)
    session.flush()
    session.add(WorkbookSheetLink(workbook_id=workbook.id, sheet_id=sheet.id, order_index=10))
    note_sheets_api._set_workbook_defined_names(session, workbook, [
        {"name": "开始日期", "formula": '=DATE(2026,6,1)'},
        {"name": "第几天", "formula": '=DATEDIF(开始日期,"2026-06-14","d")'},
        {"name": "返款周期", "formula": "=第几天"},
        {"name": "返款说明", "formula": '="修道班第"&返款周期&"天返款"'},
        {"name": "返款ID后缀", "formula": '="_day"&返款周期'},
    ])
    session.commit()

    text_response = client.get(
        "/api/note-sheets/sheets/16/table",
        params={"workbook_id": 15, "include_grid": True},
        headers={"X-Device-Token": test_device["token"]},
    )

    assert text_response.status_code == 200
    text_table = text_response.json()
    assert text_table["defined_name_values"]["返款周期"] == 2
    assert text_table["defined_name_values"]["返款说明"] == "修道班第2周返款"
    assert text_table["defined_name_values"]["返款id后缀"] == "_week2"
    assert text_table["rows"][0]["返款配置"] == "修道班第2周返款,order_week2,2"


def test_note_sheet_table_api_evaluates_legacy_attendance_formulas(client, session, test_device):
    workbook = WorkbookDocument(
        numeric_id=6,
        title="20250106念住闯关",
    )
    sheet = SheetDocument(
        numeric_id=8,
        scope="notes",
        owner_type="user",
        owner_key="1",
        sheet_key="8",
        title="考勤表",
        document_json={
            "columns": ["第01课", "第02课", "完成视频数", "视频应返款", "总应返款", "返款配置"],
            "data_start_row": 1,
            "field_row_index": 0,
            "grid_rows": [
                ["第01课", "第02课", "完成视频数", "视频应返款", "总应返款", "返款配置"],
                [
                    "3遍/120%",
                    "学习中/80%",
                    '=COUNTIF(A2:B2,"*遍*")',
                    "=SWITCH(TRUE(),C2>=1,20,TRUE,0)",
                    "=MIN(D2+10,25)",
                    '=IF(E2>0,TEXTJOIN(",",TRUE,"x",E2),"")',
                ],
            ],
            "rows": [
                [
                    "3遍/120%",
                    "学习中/80%",
                    '=COUNTIF(A2:B2,"*遍*")',
                    "=SWITCH(TRUE(),C2>=1,20,TRUE,0)",
                    "=MIN(D2+10,25)",
                    '=IF(E2>0,TEXTJOIN(",",TRUE,"x",E2),"")',
                ],
            ],
        },
        version=1,
    )
    session.add(workbook)
    session.add(sheet)
    session.flush()
    session.add(WorkbookSheetLink(workbook_id=workbook.id, sheet_id=sheet.id, order_index=10))
    session.commit()

    response = client.get(
        "/api/note-sheets/sheets/8/table",
        params={"workbook_id": 6},
        headers={"X-Device-Token": test_device["token"]},
    )

    assert response.status_code == 200
    row = response.json()["rows"][0]
    assert row["完成视频数"] == 1
    assert row["视频应返款"] == 20
    assert row["总应返款"] == 25
    assert row["返款配置"] == "x,25"


def test_note_sheet_formula_evaluates_sum_of_products_before_refund_cap():
    from backend.api import note_sheets

    grid_rows = [
        [""] * 15,
        [""] * 15,
        [""] * 11 + [499] + [""] * 3,
        [
            "",
            "",
            "",
            "",
            "",
            "",
            '=COUNTIF(O4:AI4,"*完成*")+COUNTIF(O4:AI4,"*回放*")',
            '=COUNTIF(O4:AI4,"*当堂*")*19+COUNTIF(O4:AI4,"*第1天*")*14+COUNTIF(O4:AI4,"*第2天*")*9+COUNTIF(O4:AI4,"*第3天*")*4',
            "=SWITCH(TRUE,N4>=15,100,N4>=10,60,N4>=5,30,0)",
            "=MIN(IFERROR(H4+I4+K4-IF($L$3>0,$L$3,K4),0),K4)",
            499,
            0,
            "=(K4>0)*(J4-L4)",
            1,
            "当堂完成/98%",
        ],
    ]
    cache = {}

    assert note_sheets._get_formula_grid_cell(grid_rows, 3, 7, cache, {}) == 19
    assert note_sheets._get_formula_grid_cell(grid_rows, 3, 9, cache, {}) == 19
    assert note_sheets._get_formula_grid_cell(grid_rows, 3, 12, cache, {}) == 19


def test_note_sheet_defined_names_support_workbook_and_sheet_scope(client, session, auth_user):
    workbook = WorkbookDocument(
        numeric_id=9,
        title="名称管理器测试",
        owner_user_id=auth_user.id,
        created_by_user_id=auth_user.id,
    )
    sheet = SheetDocument(
        numeric_id=10,
        scope="notes",
        owner_type="user",
        owner_key=str(auth_user.id),
        sheet_key="10",
        title="考勤表",
        owner_user_id=auth_user.id,
        created_by_user_id=auth_user.id,
        document_json={
            "columns": ["课程天数", "当前应返款"],
            "data_start_row": 1,
            "field_row_index": 0,
            "grid_rows": [
                ["课程天数", "当前应返款"],
                ["=第几天", "=IF(第几天>0,第几天*10,0)"],
            ],
            "rows": [
                ["=第几天", "=IF(第几天>0,第几天*10,0)"],
            ],
        },
        version=1,
    )
    session.add(workbook)
    session.add(sheet)
    session.flush()
    session.add(WorkbookSheetLink(workbook_id=workbook.id, sheet_id=sheet.id, order_index=10))
    session.commit()

    workbook_response = client.put(
        "/api/note-sheets/workbooks/9/defined-names",
        json={"names": [{"name": "第几天", "formula": "=2", "comment": "工作簿默认"}]},
    )
    assert workbook_response.status_code == 200
    assert workbook_response.json()["workbook"][0]["scope"] == "workbook"

    sheet_response = client.put(
        "/api/note-sheets/sheets/10/defined-names",
        params={"workbook_id": 9},
        json={"names": [{"name": "第几天", "formula": "=3", "comment": "工作表覆盖"}]},
    )
    assert sheet_response.status_code == 200
    payload = sheet_response.json()
    assert payload["workbook"][0]["formula"] == "=2"
    assert payload["worksheet"][0]["formula"] == "=3"
    assert payload["worksheets"][0]["sheet_id"] == 10
    assert payload["worksheets"][0]["sheet_title"] == "考勤表"
    assert payload["effective"][0]["formula"] == "=3"

    workbook_scope_response = client.get("/api/note-sheets/workbooks/9/defined-names")
    assert workbook_scope_response.status_code == 200
    workbook_scope_payload = workbook_scope_response.json()
    assert workbook_scope_payload["worksheets"][0]["sheet_id"] == 10
    assert workbook_scope_payload["worksheets"][0]["names"][0]["formula"] == "=3"

    workbook_scope_update_response = client.put(
        "/api/note-sheets/workbooks/9/defined-names",
        json={
            "names": [{"name": "第几天", "formula": "=2", "comment": "工作簿默认"}],
            "worksheets": [{
                "sheet_id": 10,
                "names": [{"name": "第几天", "formula": "=4", "comment": "工作表覆盖"}],
            }],
        },
    )
    assert workbook_scope_update_response.status_code == 200
    assert workbook_scope_update_response.json()["worksheets"][0]["names"][0]["formula"] == "=4"

    table_response = client.get(
        "/api/note-sheets/sheets/10/table",
        params={"workbook_id": 9},
    )
    assert table_response.status_code == 200
    row = table_response.json()["rows"][0]
    assert row["课程天数"] == 4
    assert row["当前应返款"] == 40


def test_note_sheet_formula_defined_names_are_cached_per_evaluation(monkeypatch):
    from backend.api import note_sheets

    original_evaluate = note_sheets._evaluate_table_formula_expr
    formula_call_count = 0

    def wrapped_evaluate(expr, **kwargs):
        nonlocal formula_call_count
        if str(expr).strip() == "1+2":
            formula_call_count += 1
        return original_evaluate(expr, **kwargs)

    monkeypatch.setattr(note_sheets, "_evaluate_table_formula_expr", wrapped_evaluate)

    result = note_sheets._evaluate_table_formula_expr(
        "第几天+第几天",
        grid_rows=[],
        cache={},
        defined_names={"第几天": "=1+2"},
    )

    assert result == 6
    assert formula_call_count == 1


def test_note_sheet_formula_defined_names_support_standard_attendance_periods():
    from backend.api import note_sheets

    result = note_sheets._evaluate_table_formula_expr(
        'TEXTJOIN(",",TRUE,返款说明,"order"&返款ID后缀,第几周)',
        grid_rows=[],
        cache={},
        defined_names={
            "开始日期": '=DATE(2026,5,9)',
            "第几天": '=DATEDIF(开始日期,"2026-05-23","d")',
            "第几周": "=INT((第几天-1)/7)+1",
            "返款周期": "=第几周",
            "返款说明": '="测试第"&返款周期&"周返款"',
            "返款id后缀": '="_week"&返款周期',
        },
    )

    assert result == "测试第2周返款,order_week2,2"
