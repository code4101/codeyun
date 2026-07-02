from sqlmodel import Session, SQLModel, create_engine

from backend.api.attendance import _build_order_refund_history_page
from backend.models import AttendanceOrderRefundHistory


def test_order_refund_history_page_uses_db_pagination_order_and_strips_inline_timestamps():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine, tables=[AttendanceOrderRefundHistory.__table__])

    with Session(engine) as session:
        for index in range(25):
            session.add(
                AttendanceOrderRefundHistory(
                    id=f"row-{index:02d}",
                    operator_username="tester",
                    operator_nickname="Tester",
                    student_name=f"student-{index:02d}",
                    result_text=f"2026-07-02 10:{index:02d}:00 done {index}",
                    created_at=1000 + index,
                )
            )
        session.commit()

        page = _build_order_refund_history_page(session, page=2, page_size=10)

    assert page.total == 25
    assert page.page == 2
    assert page.page_size == 10
    assert [item.id for item in page.items] == [f"row-{index:02d}" for index in range(14, 4, -1)]
    assert all("[" not in item.result_text for item in page.items)
    assert all(item.operator_name == "Tester" for item in page.items)
