from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlmodel import Session, SQLModel, create_engine

from backend.core.fanxiu.activity.ranking_lifecycle import (
    DAILY_RECONCILE_KIND,
    EXCHANGE_TAIL_KIND,
    MAGIC_ACTIVE_KIND,
    RESOURCE_FREE_GIFT_KIND,
    TIANDI_YIJU_ACTIVE_KIND,
    RankingActivityIdentity,
    RankingOccurrence,
    checkpoints_for_occurrence,
    discover_ranking_occurrences,
    due_ranking_checkpoints,
    next_ranking_lifecycle_time,
)
from backend.core.fanxiu.activity.ranking_lifecycle_store import (
    completed_ranking_checkpoint_keys,
    record_ranking_checkpoint_result,
)


TZ = ZoneInfo("Asia/Shanghai")

RESOURCE_RANK_ACTIVITY_ID_CASES = (
    *((value, "lingzhuang-huadao") for value in (
        1044501, 1044301, 2044301, 4044301, 1044311,
        8044301, 16044301, 32044301,
    )),
    *((value, "yaochi-flower-festival") for value in (
        1042801, 2042801, 4042801, 1042811,
        8042801, 16042801, 32042801,
    )),
    *((value, "yuanding-sansheng") for value in (
        16045101, 32045101, 64045101,
    )),
    *((value, "lingchong-jingwu") for value in (
        1042001, 1042901, 2042901, 4042901, 1042911,
        8042901, 16042901, 32042901,
    )),
    *((value, "lianti-faxiang") for value in (
        1041701, 1043001, 2043001, 4043001, 1043011,
        8043001, 16043001, 32043001,
    )),
    *((value, "dandao-wending") for value in (
        1041401, 1043101, 2043101, 4043101, 1043111,
        8043101, 16043101, 32043101,
    )),
)


def _ms(value: str) -> int:
    return int(datetime.fromisoformat(value).timestamp() * 1000)


def _schedule() -> dict:
    return {
        "items": [
            {
                "class": "MagicInvadeActivityVO",
                "id": 1070011400004,
                "activityId": 1070011,
                "serverCount": 1,
                "prepareEndTime": _ms("2026-08-20T00:00:00+08:00"),
                "startTime": _ms("2026-08-21T10:00:00+08:00"),
                "endTime": _ms("2026-08-21T22:00:00+08:00"),
                "closePanelTime": _ms("2026-08-22T23:59:59+08:00"),
            },
            {
                "class": "MagicInvadeActivityVO",
                "id": 8070001400004,
                "activityId": 8070001,
                "serverCount": 8,
                "prepareEndTime": _ms("2026-08-21T00:00:00+08:00"),
                "startTime": _ms("2026-08-22T10:00:00+08:00"),
                "endTime": _ms("2026-08-22T22:00:00+08:00"),
                "closePanelTime": _ms("2026-08-23T23:59:59+08:00"),
            },
            {
                "class": "ResourceActivityVO",
                "id": 9001,
                "activityId": 9002,
                "serverCount": 8,
                "prepareEndTime": _ms("2026-08-21T00:00:00+08:00"),
                "startTime": _ms("2026-08-22T10:00:00+08:00"),
                "endTime": _ms("2026-08-24T22:00:00+08:00"),
                "closePanelTime": _ms("2026-08-25T23:59:59+08:00"),
            },
            {"class": "UnknownActivityVO", "id": 1},
        ],
    }


IDENTITIES = (
    RankingActivityIdentity(
        "magic-invasion", "gameplay_rank", ("MagicInvadeActivityVO",)
    ),
    RankingActivityIdentity(
        "resource-example", "resource_rank", ("ResourceActivityVO",)
    ),
)


def test_discovery_preserves_multiple_previous_current_and_resource_occurrences() -> None:
    rows = discover_ranking_occurrences(_schedule(), identities=IDENTITIES)

    assert [(row.activity_type, row.runtime_id) for row in rows] == [
        ("magic-invasion", "1070011400004"),
        ("magic-invasion", "8070001400004"),
        ("resource-example", "9001"),
    ]
    assert len({row.instance_key for row in rows}) == 3
    assert rows[0].cross_count == 1
    assert rows[1].cross_count == 8


def test_discovery_uses_normalized_stable_activity_type_without_raw_vo_class() -> None:
    schedule = _schedule()
    magic = dict(schedule["items"][1])
    magic.pop("class")
    magic["activityType"] = 7
    identity = RankingActivityIdentity(
        "magic-invasion",
        "gameplay_rank",
        ("MagicInvadeActivityVO",),
        runtime_activity_types=(7,),
    )

    rows = discover_ranking_occurrences({"items": [magic]}, identities=(identity,))

    assert len(rows) == 1
    assert rows[0].runtime_id == "8070001400004"


@pytest.mark.parametrize(
    ("activity_id", "activity_type", "base_id"),
    ((8090001, 9, 90000), (8090002, 13, 90001), (8090004, 17, 90002)),
)
def test_discovery_uses_stable_tiandi_yiju_runtime_identity(
    activity_id: int,
    activity_type: int,
    base_id: int,
) -> None:
    rows = discover_ranking_occurrences({
        "items": [{
            "id": activity_id * 100000 + 4,
            "activityId": activity_id,
            "activityType": activity_type,
            "baseId": base_id,
            "name": "天地弈局",
            "serverCount": 8,
            "prepareEndTime": _ms("2026-08-27T00:00:00+08:00"),
            "startTime": _ms("2026-08-28T10:00:00+08:00"),
            "endTime": _ms("2026-08-28T22:00:00+08:00"),
            "closePanelTime": _ms("2026-08-29T23:59:59+08:00"),
        }]
    })

    assert len(rows) == 1
    assert rows[0].activity_type == "tiandi-yiju"
    assert rows[0].activity_id == activity_id


def test_tiandi_yiju_checkpoint_only_targets_real_board_occurrences() -> None:
    def occurrence(activity_id: int) -> RankingOccurrence:
        return RankingOccurrence(
            activity_type="tiandi-yiju",
            family="gameplay_rank",
            runtime_id=f"runtime-{activity_id}",
            activity_id=activity_id,
            start_at=datetime(2026, 8, 28, 10, tzinfo=TZ),
            end_at=datetime(2026, 8, 28, 22, tzinfo=TZ),
            prepare_at=datetime(2026, 8, 27, 0, tzinfo=TZ),
            close_at=datetime(2026, 8, 29, 23, 59, 59, tzinfo=TZ),
            cross_count=8,
        )

    board = checkpoints_for_occurrence(
        occurrence(8090004), business_day=datetime(2026, 8, 28, tzinfo=TZ).date()
    )
    group_selection = checkpoints_for_occurrence(
        occurrence(8090002), business_day=datetime(2026, 8, 28, tzinfo=TZ).date()
    )

    assert TIANDI_YIJU_ACTIVE_KIND in {item.checkpoint_kind for item in board}
    assert TIANDI_YIJU_ACTIVE_KIND not in {
        item.checkpoint_kind for item in group_selection
    }


def test_tiandi_yiju_active_checkpoint_catches_up_only_while_board_is_open() -> None:
    occurrence = RankingOccurrence(
        activity_type="tiandi-yiju",
        family="gameplay_rank",
        runtime_id="8090001400004",
        activity_id=8090001,
        start_at=datetime(2026, 8, 27, 10, tzinfo=TZ),
        end_at=datetime(2026, 8, 27, 22, tzinfo=TZ),
        prepare_at=datetime(2026, 8, 27, 0, tzinfo=TZ),
        close_at=datetime(2026, 8, 30, 23, 59, 59, tzinfo=TZ),
        cross_count=1,
    )

    active = due_ranking_checkpoints(
        (occurrence,), now=datetime(2026, 8, 27, 15, tzinfo=TZ)
    )
    expired = due_ranking_checkpoints(
        (occurrence,), now=datetime(2026, 8, 27, 22, 1, tzinfo=TZ)
    )

    assert TIANDI_YIJU_ACTIVE_KIND in {item.checkpoint_kind for item in active}
    assert TIANDI_YIJU_ACTIVE_KIND not in {item.checkpoint_kind for item in expired}


@pytest.mark.parametrize(
    ("activity_id", "expected_type"),
    RESOURCE_RANK_ACTIVITY_ID_CASES,
)
def test_discovery_covers_retained_resource_rank_variants_without_vo_class(
    activity_id: int,
    expected_type: str,
) -> None:
    schedule = {
        "items": [
            {
                "id": activity_id * 100000 + 4,
                "activityId": activity_id,
                "activityType": 12,
                "serverCount": 32,
                "prepareEndTime": _ms("2026-08-31T00:00:00+08:00"),
                "startTime": _ms("2026-09-01T10:00:00+08:00"),
                "endTime": _ms("2026-09-02T22:00:00+08:00"),
                "closePanelTime": _ms("2026-09-03T23:59:59+08:00"),
            }
        ]
    }

    occurrences = discover_ranking_occurrences(schedule)

    assert len(occurrences) == 1
    assert occurrences[0].activity_type == expected_type


def test_exact_activity_id_wins_over_shared_legacy_vo_class() -> None:
    schedule = {
        "items": [
            {
                "class": "CrossRankActivityVO",
                "id": 16044301000004,
                "activityId": 16044301,
                "activityType": 12,
                "serverCount": 16,
                "prepareEndTime": _ms("2026-08-31T00:00:00+08:00"),
                "startTime": _ms("2026-09-01T10:00:00+08:00"),
                "endTime": _ms("2026-09-02T22:00:00+08:00"),
                "closePanelTime": _ms("2026-09-03T23:59:59+08:00"),
            }
        ]
    }

    occurrences = discover_ranking_occurrences(schedule)

    assert len(occurrences) == 1
    assert occurrences[0].activity_type == "lingzhuang-huadao"


def test_0030_collects_previous_tail_today_start_resource_and_missed_days() -> None:
    rows = discover_ranking_occurrences(_schedule(), identities=IDENTITIES)
    due = due_ranking_checkpoints(
        rows,
        now=datetime(2026, 8, 22, 0, 30, tzinfo=TZ),
    )

    assert len(due) == 8
    assert {item.checkpoint_kind for item in due} == {
        DAILY_RECONCILE_KIND,
        EXCHANGE_TAIL_KIND,
    }
    assert {item.runtime_id for item in due} == {
        "1070011400004",
        "8070001400004",
        "9001",
    }
    assert {(item.runtime_id, item.business_date) for item in due} == {
        ("1070011400004", "2026-08-20"),
        ("1070011400004", "2026-08-21"),
        ("1070011400004", "2026-08-22"),
        ("8070001400004", "2026-08-21"),
        ("8070001400004", "2026-08-22"),
        ("9001", "2026-08-21"),
        ("9001", "2026-08-22"),
    }
    tails = [item for item in due if item.checkpoint_kind == EXCHANGE_TAIL_KIND]
    assert [(item.runtime_id, item.business_date) for item in tails] == [
        ("1070011400004", "2026-08-22")
    ]


def test_free_gift_checkpoint_is_limited_to_activities_with_a_proven_adapter() -> None:
    def occurrence(activity_type: str, activity_id: int) -> RankingOccurrence:
        return RankingOccurrence(
            activity_type=activity_type,
            family="resource_rank",
            runtime_id=f"runtime-{activity_type}",
            activity_id=activity_id,
            start_at=datetime(2026, 8, 26, 5, 0, 5, tzinfo=TZ),
            end_at=datetime(2026, 8, 26, 22, 0, tzinfo=TZ),
            prepare_at=datetime(2026, 8, 26, 0, 0, tzinfo=TZ),
            close_at=datetime(2026, 8, 27, 23, 59, 59, tzinfo=TZ),
            cross_count=1,
        )

    dandao = checkpoints_for_occurrence(
        occurrence("dandao-wending", 1043111),
        business_day=datetime(2026, 8, 26, tzinfo=TZ).date(),
    )
    lingzhuang = checkpoints_for_occurrence(
        occurrence("lingzhuang-huadao", 1044311),
        business_day=datetime(2026, 8, 26, tzinfo=TZ).date(),
    )

    assert RESOURCE_FREE_GIFT_KIND in {item.checkpoint_kind for item in dandao}
    assert RESOURCE_FREE_GIFT_KIND in {
        item.checkpoint_kind for item in lingzhuang
    }


def test_exchange_tail_is_never_replayed_after_panel_close() -> None:
    server = next(
        row
        for row in discover_ranking_occurrences(_schedule(), identities=IDENTITIES)
        if row.runtime_id == "1070011400004"
    )

    due = due_ranking_checkpoints(
        (server,),
        now=datetime(2026, 8, 23, 0, 1, tzinfo=TZ),
    )

    assert all(item.checkpoint_kind != EXCHANGE_TAIL_KIND for item in due)


def test_xianyuan_exchange_tail_is_due_at_midnight() -> None:
    occurrence = RankingOccurrence(
        activity_type="xianyuan-duokui",
        family="gameplay_rank",
        runtime_id="846001400015",
        activity_id=846001,
        start_at=datetime(2026, 8, 26, 10, tzinfo=TZ),
        end_at=datetime(2026, 8, 26, 22, tzinfo=TZ),
        prepare_at=datetime(2026, 8, 26, 0, tzinfo=TZ),
        close_at=datetime(2026, 8, 27, 23, 59, 59, tzinfo=TZ),
        cross_count=8,
    )

    checkpoints = checkpoints_for_occurrence(
        occurrence,
        business_day=datetime(2026, 8, 27, tzinfo=TZ).date(),
    )
    tail = next(item for item in checkpoints if item.checkpoint_kind == EXCHANGE_TAIL_KIND)

    assert tail.due_at == datetime(2026, 8, 27, 0, 0, tzinfo=TZ)
    due = due_ranking_checkpoints(
        (occurrence,),
        now=datetime(2026, 8, 27, 0, 0, tzinfo=TZ),
    )
    assert any(item.key == tail.key for item in due)


def test_expired_magic_action_is_never_replayed_but_daily_tail_is_reconciled() -> None:
    server = next(
        row
        for row in discover_ranking_occurrences(_schedule(), identities=IDENTITIES)
        if row.runtime_id == "1070011400004"
    )

    due = due_ranking_checkpoints(
        (server,),
        now=datetime(2026, 8, 21, 23, 0, tzinfo=TZ),
    )

    assert due
    assert {item.checkpoint_kind for item in due} == {DAILY_RECONCILE_KIND}


def test_magic_1900_is_extra_checkpoint_not_a_parallel_job() -> None:
    rows = discover_ranking_occurrences(_schedule(), identities=IDENTITIES)
    cross = next(row for row in rows if row.runtime_id == "8070001400004")
    daily_due = due_ranking_checkpoints(
        (cross,),
        now=datetime(2026, 8, 22, 0, 30, tzinfo=TZ),
    )
    completed = {item.key for item in daily_due}

    assert next_ranking_lifecycle_time(
        (cross,),
        now=datetime(2026, 8, 22, 0, 31, tzinfo=TZ),
        completed_keys=completed,
    ) == datetime(2026, 8, 22, 19, 0, tzinfo=TZ)
    active_due = due_ranking_checkpoints(
        (cross,),
        now=datetime(2026, 8, 22, 19, 0, tzinfo=TZ),
        completed_keys=completed,
    )
    assert [item.checkpoint_kind for item in active_due] == [MAGIC_ACTIVE_KIND]


def test_checkpoint_store_is_occurrence_scoped_and_idempotently_updates_one_row() -> None:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    cross = next(
        row
        for row in discover_ranking_occurrences(_schedule(), identities=IDENTITIES)
        if row.runtime_id == "8070001400004"
    )
    checkpoint = due_ranking_checkpoints(
        (cross,),
        now=datetime(2026, 8, 22, 0, 30, tzinfo=TZ),
    )[0]

    with Session(engine) as session:
        first = record_ranking_checkpoint_result(
            session,
            checkpoint,
            status="retained",
            message="榜单未开放，静态奖励与既有事实已对齐",
        )
        second = record_ranking_checkpoint_result(
            session,
            checkpoint,
            status="completed",
            message="重复对账已确认",
        )

        assert first.id == second.id
        assert second.attempt_count == 2
        assert completed_ranking_checkpoint_keys(session) == {checkpoint.key}


def test_invalid_or_duplicate_runtime_rows_fail_closed_without_duplicate_checkpoint() -> None:
    schedule = _schedule()
    schedule["items"].append(dict(schedule["items"][1]))
    schedule["items"].append(
        {
            "class": "MagicInvadeActivityVO",
            "id": 99,
            "activityId": 1,
            "startTime": _ms("2026-08-22T22:00:00+08:00"),
            "endTime": _ms("2026-08-22T10:00:00+08:00"),
        }
    )

    rows = discover_ranking_occurrences(schedule, identities=IDENTITIES)

    assert len(rows) == 3
