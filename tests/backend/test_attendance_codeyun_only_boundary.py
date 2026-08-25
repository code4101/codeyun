from pathlib import Path

from backend.core.attendance import order


BACKEND_ROOT = Path(__file__).parents[2] / "backend"


def test_attendance_order_bridge_has_no_retired_table_adapter():
    assert not hasattr(order, "sync_kqbook_order_sheet")


def test_attendance_backend_has_no_retired_table_dependencies():
    forbidden = (
        "WpsOnlineBook",
        "pyxllib.ext.wpsapi",
        "KqBook",
        "sync_kqbook_order_sheet",
        "lesson_table",
        "lesson_data_table",
        "clockin_table",
        "clockin_data_table",
        "user_table",
        "weipay_table",
        "weipay_matview",
        "_load_attendance_kqdb_provider",
        "find_order_in_db",
        "attendance.header-tool",
        "/header-tool/generate",
        "remote_repair_attempted",
        "sync_fanbei_attendance_step1",
        "build_fanbei_attendance_step2_data(",
        "/attendance/fanbei/step1",
        "/attendance/fanbei/step2-data",
    )
    violations = []
    for path in BACKEND_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            if marker in text:
                violations.append(f"{path.relative_to(BACKEND_ROOT)}: {marker}")
    assert not violations, "\n".join(violations)
