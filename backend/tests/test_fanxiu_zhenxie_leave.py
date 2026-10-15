from backend.core.fanxiu.data_annotation.tasks.zhenxie import (
    _ZHENXIE_LEAVE_REDISCOVER_WAIT_SECONDS,
    zhenxie_landing_wait_seconds,
)


def test_zhenxie_landing_wait_capped_by_constant():
    # 剩余预算很大时，单次等待必须被上限截断（此前会拖满整段 deadline 造成 133s 卡顿）。
    assert zhenxie_landing_wait_seconds(deadline=1e9, now=0.0) == _ZHENXIE_LEAVE_REDISCOVER_WAIT_SECONDS


def test_zhenxie_landing_wait_uses_remaining_deadline_when_smaller():
    assert zhenxie_landing_wait_seconds(deadline=105.0, now=100.0) == 5.0


def test_zhenxie_landing_wait_floor_is_one_second():
    assert zhenxie_landing_wait_seconds(deadline=100.0, now=250.0) == 1.0


def test_zhenxie_joined_fact_is_dated_and_requires_formal_entry_evidence():
    from datetime import date

    from backend.core.fanxiu.data_annotation.tasks.zhenxie import zhenxie_joined_today

    today = date(2026, 10, 15)
    mark = {"date": "2026-10-15", "evidence": "272_goto_field", "attempt_id": "a"}
    assert zhenxie_joined_today(mark, today) is True
    assert zhenxie_joined_today(mark, date(2026, 10, 16)) is False
    assert zhenxie_joined_today({**mark, "evidence": "field_85_seen"}, today) is False
    assert zhenxie_joined_today({"date": "2026-10-15"}, today) is False
    assert zhenxie_joined_today(None, today) is False


def test_zhenxie_stay_status_needs_field_reobservation_not_wall_clock():
    from datetime import date, datetime

    from backend.core.fanxiu.data_annotation.tasks.zhenxie import (
        ZHENXIE_JOIN_SCHEMA,
        zhenxie_stay_status,
        zhenxie_stay_verified_mark,
    )

    today = date(2026, 10, 16)
    mark = {"schema": ZHENXIE_JOIN_SCHEMA, "date": "2026-10-16", "evidence": "272_goto_field",
            "joined_at": "2026-10-16T21:01:56", "earliest_leave_at": "2026-10-16T21:02:26"}
    early = zhenxie_stay_status(mark, today, datetime(2026, 10, 16, 21, 2, 6))
    assert early == {"joined": True, "stay": "pending", "remaining_seconds": 20.0}
    # Clock passing the boundary alone stays pending (0 s left, still unproven).
    late = zhenxie_stay_status(mark, today, datetime(2026, 10, 16, 21, 4))
    assert late["stay"] == "pending" and late["remaining_seconds"] == 0.0

    assert zhenxie_stay_verified_mark(mark, datetime(2026, 10, 16, 21, 2, 20)) is None
    verified = zhenxie_stay_verified_mark(mark, datetime(2026, 10, 16, 21, 2, 27))
    assert verified["stay_verified_at"] == "2026-10-16T21:02:27"
    assert zhenxie_stay_status(verified, today, datetime(2026, 10, 16, 21, 3))["stay"] == "verified"
    # A forged/early verification is not accepted.
    forged = {**mark, "stay_verified_at": "2026-10-16T21:02:00"}
    assert zhenxie_stay_status(forged, today, datetime(2026, 10, 16, 21, 3))["stay"] == "pending"


def test_zhenxie_legacy_or_foreign_mark_stays_unknown():
    from datetime import date, datetime

    from backend.core.fanxiu.data_annotation.tasks.zhenxie import (
        zhenxie_stay_status,
        zhenxie_stay_verified_mark,
    )

    today = date(2026, 10, 15)
    legacy = {"date": "2026-10-15", "evidence": "272_goto_field", "joined_at": "2026-10-15T21:01:56",
              "attempt_id": "1df7cd93dbce47dfbb422977277fc4bf"}
    assert zhenxie_stay_status(legacy, today, datetime(2026, 10, 15, 21, 4)) == {
        "joined": True, "stay": "unknown", "remaining_seconds": None}
    assert zhenxie_stay_verified_mark(legacy, datetime(2026, 10, 15, 21, 4)) is None
    assert zhenxie_stay_status(legacy, date(2026, 10, 16), datetime(2026, 10, 16, 21, 4))["joined"] is False


def test_zhenxie_field_evidence_requires_field_scene_id_not_any_match():
    from types import SimpleNamespace

    from backend.core.fanxiu.data_annotation.tasks.zhenxie import zhenxie_on_field

    assert zhenxie_on_field(SimpleNamespace(scene_id=85)) is True
    assert zhenxie_on_field(SimpleNamespace(scene_id=186)) is True
    # Layer 1/2 may return other formal scenes for a [85, 186] wait.
    for other in (34, 272, 271, 86, None):
        assert zhenxie_on_field(SimpleNamespace(scene_id=other)) is False
    assert zhenxie_on_field(None) is False
    assert zhenxie_on_field(SimpleNamespace(scene_id=True)) is False
