from backend.api import note_sheets


def test_normalize_registration_order_month_value_from_datetime_text():
    assert note_sheets._normalize_registration_order_month_value("2026-07-02 11:15:40") == "20260702"
    assert note_sheets._normalize_registration_order_month_value("202606") == "202606"
    assert (
        note_sheets._normalize_registration_order_month_value(
            "202606",
            "4200003191202606163382693355",
        )
        == "20260616"
    )


def test_registration_order_match_repairs_completed_row_order_date(monkeypatch):
    def fail_lookup_order(*_args, **_kwargs):
        raise AssertionError("completed rows with only malformed order date should not hit order lookup")

    monkeypatch.setattr(note_sheets, "_load_attendance_order_lookup_provider", lambda: fail_lookup_order)

    document = {
        "columns": ["姓名", "微信支付订单号", "订单日期", "商户订单号", "订单金额", "已返款"],
        "rows": [[
            "王悦",
            "4200003220202607029854445793",
            "2026-07-02 11:15:40",
            "THJ2AI-0OZRE8O-AHWT",
            "620",
            "0",
        ]],
    }

    next_document, summary = note_sheets._update_registration_order_match_document(document)

    assert next_document["rows"][0][2] == "20260702"
    assert summary["target_count"] == 1
    assert summary["updated_count"] == 1
    assert summary["matched_count"] == 1


def test_registration_order_match_adds_hidden_refund_field(monkeypatch):
    monkeypatch.setattr(
        note_sheets,
        "_load_attendance_order_lookup_provider",
        lambda: lambda *_args, **_kwargs: {
            "微信支付订单号": "4200003220202607029854445793",
            "订单日期": "20260702",
            "商户订单号": "THJ2AI-0OZRE8O-AHWT",
            "订单金额": "912",
            "已返款": "120",
        },
    )
    document = {
        "columns": ["姓名", "微信支付订单号", "订单日期", "商户订单号", "订单金额"],
        "rows": [["王悦", "4200003220202607029854445793", "", "", ""]],
    }

    next_document, summary = note_sheets._update_registration_order_match_document(document)

    refunded_index = next_document["columns"].index("已返款")
    assert next_document["rows"][0][refunded_index] == "120"
    assert next_document["column_configs"]["已返款"]["hidden"] is True
    assert summary["matched_count"] == 1


def test_registration_sync_replaces_identity_refund_and_orders_by_group_sequence():
    registration_columns = [
        "分组",
        "序号",
        "姓名",
        "微信昵称",
        "手机号",
        "微信支付订单号",
        "订单日期",
        "商户订单号",
        "订单金额",
        "已返款",
        "用户ID",
    ]
    registration = {
        "columns": registration_columns,
        "rows": [
            ["2组", "2_01", "乙", "乙昵称", "13200000002", "wx2", "202607", "m2", "912", "20", ""],
            ["1组", "1_02", "甲二", "甲二昵称", "13200000003", "wx3", "202607", "m3", "912", "30", ""],
            ["1组", "1_01", "甲一", "甲一昵称", "13200000001", "wx1", "202607", "m1", "912", "10", ""],
        ],
    }
    attendance_columns = ["分组", "学号", "姓名", "昵称", "手机号", "商户订单号", "订单金额", "已返款"]
    attendance = {
        "columns": attendance_columns,
        "rows": [
            ["旧组", "1", "旧乙", "", "", "m2", "0", "0"],
            ["旧组", "2", "旧甲二", "", "", "m3", "0", "0"],
            ["旧组", "3", "旧甲一", "", "", "m1", "0", "0"],
        ],
    }

    next_document, summary = note_sheets._sync_registration_rows_to_attendance_document(
        registration,
        attendance,
    )

    assert [row[1] for row in next_document["rows"]] == ["1_01", "1_02", "2_01"]
    assert [row[2] for row in next_document["rows"]] == ["甲一", "甲二", "乙"]
    assert [row[7] for row in next_document["rows"]] == ["10", "30", "20"]
    assert summary["repaired_count"] >= 3
