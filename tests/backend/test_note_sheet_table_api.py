from __future__ import annotations

from datetime import date

from sqlmodel import select

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


def test_note_sheet_table_api_allows_trusted_device_read_and_patch(client, session, test_device):
    _create_structured_sheet(session)

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
                    "value": "最近运行更新时间: 2026/05/10 07:10",
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
    assert stored.document_json["grid_rows"][2][3] == "最近运行更新时间: 2026/05/10 07:10"


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
