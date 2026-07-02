from backend.api import note_sheets


def test_normalize_registration_order_month_value_from_datetime_text():
    assert note_sheets._normalize_registration_order_month_value("2026-07-02 11:15:40") == "202607"
    assert note_sheets._normalize_registration_order_month_value("202606") == "202606"


def test_registration_order_match_repairs_completed_row_order_date(monkeypatch):
    def fail_lookup_order(*_args, **_kwargs):
        raise AssertionError("completed rows with only malformed order date should not hit order lookup")

    monkeypatch.setattr(note_sheets, "_load_attendance_kqdb_provider", lambda: (lambda: object()))
    monkeypatch.setattr(note_sheets, "_load_attendance_order_lookup_provider", lambda: fail_lookup_order)

    document = {
        "columns": ["姓名", "微信支付订单号", "订单日期", "商户订单号", "订单金额"],
        "rows": [[
            "王悦",
            "4200003220202607029854445793",
            "2026-07-02 11:15:40",
            "THJ2AI-0OZRE8O-AHWT",
            "620",
        ]],
    }

    next_document, summary = note_sheets._update_registration_order_match_document(document)

    assert next_document["rows"][0][2] == "202607"
    assert summary["target_count"] == 1
    assert summary["updated_count"] == 1
    assert summary["matched_count"] == 1
