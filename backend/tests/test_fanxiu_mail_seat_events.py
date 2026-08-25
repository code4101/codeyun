from __future__ import annotations

from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from backend.core.fanxiu.mail.seat_events import (
    SEAT_EVICTION_MAIL_SCHEMAS,
    list_fanxiu_seat_eviction_events,
    parse_fanxiu_seat_eviction_event,
    reconcile_seat_eviction_with_protocol_fact,
)
from backend.core.fanxiu.history_museum.packet_capture.mail_sync import _mail_i18n_template_values
from backend.models import FanxiuMailRecord


def _param(kind: str, value):
    if kind == "reward":
        return {"_class": "I18nParam2Reward", "value": {"items": [{"_class": "NoticeRewardVO", "type": 0, "code": 37, "amount": value}]}, "_super": {"key": "REWARD"}}
    if kind in {"name", "name_number"}:
        return {"_class": "I18nParam2Name", "value": str(value), "_super": {"key": "NAME"}}
    return {"_class": "I18nParam2Num", "value": value, "_super": {"key": "NUM"}}


def test_every_declared_eviction_template_has_a_consumable_parameter_schema():
    assert set(SEAT_EVICTION_MAIL_SCHEMAS) == {2101, 2105, 2107, 2109, 2112, 2114, 2202, 2206, 2208, 2211, 2213}
    for mail_type, schema in SEAT_EVICTION_MAIL_SCHEMAS.items():
        params = []
        for index, (_field, kind) in enumerate(schema["fields"], start=1):
            params.append(_param(kind, index))
        event = parse_fanxiu_seat_eviction_event(
            mail_type,
            {"id": f"mail-{mail_type}", "createTime": 1786622400000, "i18nParams": {"items": params}},
        )
        assert event is not None
        assert event["complete"] is True
        assert event["domain"] == schema["domain"]
        assert event["content_id"] == schema["content_id"]


def test_real_2202_parameter_order_keeps_buff_out_of_elapsed_time():
    event = parse_fanxiu_seat_eviction_event(
        2202,
        {
            "id": "24082878061635584",
            "createTime": 1781596920000,
            "i18nParams": {
                "items": [
                    _param("name", "仙-谢云流"),
                    _param("num", 100),
                    _param("name", "仙煌神脉"),
                    _param("num", 6_169_383),
                    _param("num", 4_630_617),
                    _param("reward", 166_885),
                ]
            },
        },
    )
    assert event is not None
    assert event["complete"] is True
    assert event["opponent"] == "仙-谢云流"
    assert event["buff"] == 100
    assert event["location"] == "仙煌神脉"
    assert event["elapsed_ms"] == 6_169_383
    assert event["remaining_ms"] == 4_630_617
    assert event["rewards"] == [{"item_id": 37, "amount": 166_885, "type": 0, "class": "NoticeRewardVO"}]

    values = _mail_i18n_template_values(
        {
            "i18nParams": {
                "items": [
                    _param("name", "仙-谢云流"), _param("num", 100),
                    _param("name", "仙煌神脉"), _param("num", 6_169_383),
                    _param("num", 4_630_617), _param("reward", 166_885),
                ]
            }
        },
        "$NAME$ $NUM$ $NAME$ $TIME$ $TIME$ $L_REWARDS$",
    )
    assert values[:5] == ["仙-谢云流", "100", "仙煌神脉", "6169.38秒", "4630.62秒"]


def test_real_2107_projects_extra_reward_and_leaderboard_score():
    event = parse_fanxiu_seat_eviction_event(
        2107,
        {
            "id": "24082878061668088",
            "createTime": 1784716620000,
            "i18nParams": {"items": [
                _param("name", "贫道丶无心"), _param("name", "大罗道场"),
                _param("num", 5_580_000), _param("num", 12_060_000),
                _param("name_number", "60"), _param("reward", 5_580),
                _param("reward", 3_348), _param("num", 13_392),
            ]},
        },
    )
    assert event is not None
    assert event["complete"] is True
    assert event["extra"] is True
    assert event["guarantee"] is False
    assert event["buff"] == 60
    assert event["extra_rewards"][0]["amount"] == 3_348
    assert event["leaderboard_points"] == 13_392


def test_schema_mismatch_fails_closed_without_shifting_fields():
    event = parse_fanxiu_seat_eviction_event(
        2202,
        {"i18nParams": {"items": [_param("name", "对手"), _param("name", "不应当作增益")]}},
    )
    assert event is not None
    assert event["complete"] is False
    assert "buff" not in event
    assert "location" not in event
    assert event["incomplete_reason"] == "parameter_1_expected_num_got_name"


def test_durable_event_list_and_protocol_reconciliation_are_positive_only():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        event = parse_fanxiu_seat_eviction_event(
            2208,
            {"id": "evicted", "createTime": 1786622400000, "i18nParams": {"items": [
                _param("name", "对手"), _param("name", "圣脉"), _param("num", 60_000),
                _param("num", 120_000), _param("reward", 10),
            ]}},
        )
        session.add(FanxiuMailRecord(mail_key="mail:evicted", mail_id="evicted", mail_type="2208", payload={"seat_eviction_event": event}))
        session.commit()
        assert list_fanxiu_seat_eviction_events(session, domain="lundao") == []
        events = list_fanxiu_seat_eviction_events(session, domain="lingmai")
        assert [item["mail_id"] for item in events] == ["evicted"]
        assert reconcile_seat_eviction_with_protocol_fact(events[0], None)["relation"] == "not_loaded"
        relation = reconcile_seat_eviction_with_protocol_fact(
            events[0],
            {"available": True, "seated": False, "captured_at": "2026-08-13T20:01:00+08:00"},
        )
        assert relation["relation"] == "post_event_vacated_fact"
