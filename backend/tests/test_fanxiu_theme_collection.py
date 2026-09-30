from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine


@pytest.fixture(autouse=True)
def empty_world_menu(monkeypatch):
    """Planning tests supply menu facts instead of reading a live process."""
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.theme_collection._default_menu_reader",
        lambda: SimpleNamespace(complete=True, items=()),
    )

from backend.core.fanxiu.activity.theme_collection import (
    GARDEN_BANQUET_DAILY_KINDS,
    GARDEN_BANQUET_TAIL_KIND,
    HOLY_WOOD_DAILY_KIND,
    HOLY_WOOD_TAIL_KIND,
    STAGE_ACTIVE,
    STAGE_TAIL,
    THEME_COLLECTION_INTERNALIZED_TASK_IDS,
    THEME_COLLECTION_MEMBERS,
    THEME_COLLECTION_TASK_ID,
    VISIT_XIANZUN_KIND,
    ThemeCheckpoint,
    ThemeMemberSpec,
    ThemeStageRule,
    active_theme_checkpoints,
    checkpoints_for_occurrence,
    discover_theme_occurrences,
    due_theme_checkpoints,
    iter_theme_checkpoints,
    next_theme_collection_time,
    project_theme_collection,
    theme_collection_migration_contract,
    theme_occurrence_diagnostics,
)
from backend.core.fanxiu.activity.theme_collection_store import (
    completed_theme_stage_keys,
    persist_theme_stage_completion,
)


TZ = ZoneInfo("Asia/Shanghai")
HOLY_WOOD_KIND = HOLY_WOOD_DAILY_KIND
GARDEN_KIND = "garden_banquet_1000"
VISIT_KIND = VISIT_XIANZUN_KIND
XIANYUAN_START = "2026-09-01T00:00:00+08:00"
XIANYUAN_END = "2026-09-03T23:59:00+08:00"


def test_period_only_observation_does_not_duplicate_real_instances():
    rows = [_occurrence("仙园游宴", 304, XIANYUAN_START, XIANYUAN_END,
                        runtime_id=value) for value in (0, 304, 305)]
    found = discover_theme_occurrences(_plan(*rows))
    assert len(found) == 2
    assert {r.instance_key.rsplit(":", 1)[-1] for r in found} == {"304", "305"}


def _plan(*occurrences: dict, observations: tuple[dict, ...] = ()) -> dict:
    return {
        "status": "ready",
        "source_kind": "worldline_activity_runtime_memory",
        "occurrences": list(occurrences),
        "activity_observations": list(observations),
    }


def _occurrence(
    name: str,
    activity_id: int,
    start: str,
    end: str,
    *,
    runtime_id: int = 1,
    base_id: int = 0,
) -> dict:
    return {
        "identity_complete": True,
        "name": name,
        "activity_id": activity_id,
        "base_id": base_id,
        "start_at": start,
        "end_at": end,
        "close_panel_at": end,
        "runtime_ids": [runtime_id],
    }


def _xianyuan_plan(*, runtime_id: int = 11) -> dict:
    return _plan(
        _occurrence(
            "仙园游宴",
            304,
            XIANYUAN_START,
            XIANYUAN_END,
            runtime_id=runtime_id,
            base_id=118000,
        )
    )


def test_discover_keeps_multiple_coexisting_instances() -> None:
    occurrences = discover_theme_occurrences(
        _plan(
            _occurrence(
                "仙园游宴", 304, XIANYUAN_START, XIANYUAN_END, runtime_id=11
            ),
            _occurrence(
                "仙园游宴", 304, XIANYUAN_START, XIANYUAN_END, runtime_id=12
            ),
        )
    )

    assert len(occurrences) == 2
    assert {item.member_id for item in occurrences} == {"xianyuan-banquet"}
    assert len({item.instance_key for item in occurrences}) == 2


def test_activation_dictionary_occurrence_is_accepted() -> None:
    start = datetime.fromisoformat(XIANYUAN_START)
    end = datetime.fromisoformat(XIANYUAN_END)
    activation_rows = [
        {
            "activity_id": 304,
            "base_id": 118000,
            "state": 2,
            "start_time_ms": int(start.timestamp() * 1000),
            "end_time_ms": int(end.timestamp() * 1000),
            "close_panel_time_ms": int(end.timestamp() * 1000),
        }
    ]

    occurrences = discover_theme_occurrences(
        _plan(), activation_occurrences=activation_rows
    )

    assert len(occurrences) == 1
    assert occurrences[0].member_id == "xianyuan-banquet"
    assert occurrences[0].activity_id == 304


def test_non_worldline_authority_row_is_accepted() -> None:
    authority_rows = [
        {
            "name": "仙园游宴",
            "activity_id": 304,
            "base_id": 118000,
            "source_kind": "revenue_activity_period_runtime_memory",
            "start_at": XIANYUAN_START,
            "end_at": XIANYUAN_END,
            "close_panel_at": XIANYUAN_END,
        }
    ]

    occurrences = discover_theme_occurrences(
        _plan(), authority_occurrences=authority_rows
    )

    assert len(occurrences) == 1
    assert occurrences[0].member_id == "xianyuan-banquet"
    assert occurrences[0].activity_id == 304


def test_one_instance_holds_coexisting_stages() -> None:
    occurrence = discover_theme_occurrences(_xianyuan_plan())[0]
    spec = next(
        item for item in THEME_COLLECTION_MEMBERS if item.member_id == "xianyuan-banquet"
    )

    checkpoints = checkpoints_for_occurrence(occurrence, spec)
    garden_daily = [
        item for item in checkpoints if item.kind in GARDEN_BANQUET_DAILY_KINDS
    ]
    holy_daily = [item for item in checkpoints if item.kind == HOLY_WOOD_KIND]
    garden_tail = [
        item for item in checkpoints if item.kind == GARDEN_BANQUET_TAIL_KIND
    ]
    holy_tail = [item for item in checkpoints if item.kind == HOLY_WOOD_TAIL_KIND]
    visit_tail = [item for item in checkpoints if item.kind == VISIT_KIND]

    assert len(holy_daily) == 3  # daily 00:00
    assert len(garden_daily) == 3 * len(GARDEN_BANQUET_DAILY_KINDS)
    assert {item.kind for item in garden_daily} == set(GARDEN_BANQUET_DAILY_KINDS)
    assert len(garden_tail) == 1  # last day 21:00, its own kind
    assert garden_tail[0].business_date == "2026-09-03"
    assert len(holy_tail) == 1  # last day 21:00
    # 仙缘送礼 is the only after-end ending: last day 22:00 trigger, but its
    # due_at tracks the authoritative end_at (23:59 here) and it hard-closes at
    # the next midnight.
    assert len(visit_tail) == 1
    assert visit_tail[0].business_date == "2026-09-03"
    assert visit_tail[0].due_at == datetime(2026, 9, 3, 23, 59, tzinfo=TZ)
    assert visit_tail[0].deadline_at == datetime(2026, 9, 4, 0, 0, tzinfo=TZ)


XIANYUAN_REAL_START = "2026-09-18T00:00:00+08:00"
XIANYUAN_REAL_END = "2026-09-20T22:00:01+08:00"


def _xianyuan_real_plan(*, runtime_ids: tuple[int, ...] = (11,)) -> dict:
    return _plan(
        *(
            _occurrence(
                "仙园游宴",
                304,
                XIANYUAN_REAL_START,
                XIANYUAN_REAL_END,
                runtime_id=runtime_id,
                base_id=118000,
            )
            for runtime_id in runtime_ids
        )
    )


def test_after_end_visit_xianzun_due_after_22_on_last_playable_day() -> None:
    occurrences = discover_theme_occurrences(_xianyuan_real_plan())
    now = datetime(2026, 9, 20, 22, 30, tzinfo=TZ)

    due = due_theme_checkpoints(occurrences, now=now)
    visit = [item for item in due if item.kind == VISIT_KIND]

    assert len(visit) == 1
    assert visit[0].business_date == "2026-09-20"
    assert visit[0].due_at == datetime(2026, 9, 20, 22, 0, 1, tzinfo=TZ)
    assert visit[0].deadline_at == datetime(2026, 9, 21, 0, 0, tzinfo=TZ)
    # Only the explicit after-end ending survives the closed mother instance.
    assert {item.kind for item in due} == {VISIT_KIND}


def test_after_end_visit_xianzun_completion_stops_rearming() -> None:
    occurrences = discover_theme_occurrences(_xianyuan_real_plan())
    occurrence = occurrences[0]
    now = datetime(2026, 9, 20, 22, 30, tzinfo=TZ)
    completed = {
        (occurrence.instance_key, "xianyuan-banquet", VISIT_KIND, "2026-09-20")
    }

    assert due_theme_checkpoints(occurrences, now=now, completed_keys=completed) == ()

    projection = project_theme_collection(
        _xianyuan_real_plan(), now=now, completed_stage_keys=completed
    )
    assert projection["due_stages"] == []


def test_after_end_visit_xianzun_is_not_replayed_after_midnight() -> None:
    occurrences = discover_theme_occurrences(_xianyuan_real_plan())
    just_before = datetime(2026, 9, 20, 23, 59, 59, tzinfo=TZ)
    midnight = datetime(2026, 9, 21, 0, 0, tzinfo=TZ)

    assert [
        item
        for item in due_theme_checkpoints(occurrences, now=just_before)
        if item.kind == VISIT_KIND
    ]
    assert due_theme_checkpoints(occurrences, now=midnight) == ()
    assert active_theme_checkpoints(occurrences, now=midnight) == ()


def test_after_end_visit_completion_stays_isolated_per_instance() -> None:
    occurrences = discover_theme_occurrences(_xianyuan_real_plan(runtime_ids=(11, 12)))
    now = datetime(2026, 9, 20, 22, 30, tzinfo=TZ)
    completed = {
        (occurrences[0].instance_key, "xianyuan-banquet", VISIT_KIND, "2026-09-20")
    }

    visit = [
        item
        for item in due_theme_checkpoints(occurrences, now=now, completed_keys=completed)
        if item.kind == VISIT_KIND
    ]

    assert len(visit) == 1
    assert visit[0].instance_key == occurrences[1].instance_key


def test_after_end_window_stays_valid_when_parent_ends_at_midnight() -> None:
    plan = _plan(
        _occurrence(
            "仙园游宴",
            304,
            XIANYUAN_REAL_START,
            "2026-09-21T00:00:00+08:00",
            runtime_id=11,
            base_id=118000,
        )
    )
    occurrences = discover_theme_occurrences(plan)
    before_trigger = datetime(2026, 9, 20, 21, 59, tzinfo=TZ)
    after_trigger = datetime(2026, 9, 20, 22, 30, tzinfo=TZ)

    assert not [
        item
        for item in due_theme_checkpoints(occurrences, now=before_trigger)
        if item.kind == VISIT_KIND
    ]
    visit = [
        item
        for item in due_theme_checkpoints(occurrences, now=after_trigger)
        if item.kind == VISIT_KIND
    ]
    assert len(visit) == 1
    assert visit[0].due_at == datetime(2026, 9, 20, 22, 0, tzinfo=TZ)
    assert visit[0].deadline_at == datetime(2026, 9, 21, 0, 0, tzinfo=TZ)


def test_after_end_exemption_does_not_revive_other_members() -> None:
    authority_rows = [
        {
            "name": "蓬莱仙藏",
            "activity_id": 701,
            "source_kind": "revenue_activity_period_runtime_memory",
            "start_at": "2026-09-01T00:00:00+08:00",
            "end_at": "2026-09-01T12:00:00+08:00",
            "close_panel_at": "2026-09-01T12:00:00+08:00",
        }
    ]
    occurrences = discover_theme_occurrences(
        _plan(), authority_occurrences=authority_rows
    )
    now = datetime(2026, 9, 1, 13, 0, tzinfo=TZ)

    assert any(item.member_id == "penglai-xianzang" for item in occurrences)
    assert active_theme_checkpoints(occurrences, now=now) == ()
    assert due_theme_checkpoints(occurrences, now=now) == ()


def test_one_instance_can_hold_coexisting_prepare_active_tail_stages() -> None:
    occurrence = discover_theme_occurrences(_xianyuan_plan())[0]
    spec = ThemeMemberSpec(
        member_id="xianyuan-banquet",
        names=frozenset({"仙园游宴"}),
        executor_task_id="xianyuan-banquet",
        stage_rules=(
            ThemeStageRule(
                member_id="xianyuan-banquet",
                stage="prepare",
                kind="prepare_0000",
                day_scope="start_day",
                trigger=time(0, 0),
            ),
            ThemeStageRule(
                member_id="xianyuan-banquet",
                stage=STAGE_ACTIVE,
                kind="active_1000",
                day_scope="daily",
                trigger=time(10, 0),
            ),
            ThemeStageRule(
                member_id="xianyuan-banquet",
                stage=STAGE_TAIL,
                kind="tail_2100",
                day_scope="last_day",
                trigger=time(21, 0),
            ),
        ),
    )

    checkpoints = checkpoints_for_occurrence(
        occurrence, spec, production_kinds={"prepare_0000", "active_1000", "tail_2100"}
    )
    kinds = {item.kind for item in checkpoints}

    assert kinds == {"prepare_0000", "active_1000", "tail_2100"}
    assert len([item for item in checkpoints if item.kind == "active_1000"]) == 3


def test_daily_stage_uses_actual_dates_and_not_weekdays() -> None:
    occurrence = discover_theme_occurrences(_xianyuan_plan())[0]
    spec = ThemeMemberSpec(
        member_id="xianyuan-banquet",
        names=frozenset({"仙园游宴"}),
        executor_task_id="xianyuan-banquet",
        stage_rules=(
            ThemeStageRule(
                member_id="xianyuan-banquet",
                stage=STAGE_ACTIVE,
                kind=HOLY_WOOD_KIND,
                day_scope="daily",
                trigger=time(0, 0),
            ),
        ),
    )

    checkpoints = checkpoints_for_occurrence(
        occurrence, spec, production_kinds={HOLY_WOOD_KIND}
    )

    assert [item.business_date for item in checkpoints] == [
        "2026-09-01",
        "2026-09-02",
        "2026-09-03",
    ]
    assert all(item.due_at.hour == 0 for item in checkpoints)


def test_unaccepted_stages_are_deferred_and_never_due() -> None:
    occurrences = discover_theme_occurrences(_xianyuan_plan())
    now = datetime(2026, 9, 1, 1, 0, tzinfo=TZ)

    assert (
        due_theme_checkpoints(
            occurrences, now=now, production_only=True, production_kinds=()
        )
        == ()
    )

    projection = project_theme_collection(
        _xianyuan_plan(), now=now, production_kinds=()
    )

    assert projection["status"] == "pending_validation"
    assert projection["desired_next_times"][THEME_COLLECTION_TASK_ID] is None
    deferred_kinds = {item["kind"] for item in projection["deferred_stages"]}
    assert {HOLY_WOOD_KIND, GARDEN_KIND}.issubset(deferred_kinds)
    assert all(
        item["status"] == "pending_validation"
        for item in projection["decisions"]
        if item.get("kind") in deferred_kinds
    )


def test_observation_without_time_authority_is_non_executable_diagnostic() -> None:
    plan = _plan(
        observations=(
            {"name": "蓬莱仙藏", "activity_id": 701, "is_schedule_occurrence": False},
        )
    )

    assert discover_theme_occurrences(plan) == ()
    diagnostics = theme_occurrence_diagnostics(plan)
    assert diagnostics == [
        {
            "member_id": "penglai-xianzang",
            "name": "蓬莱仙藏",
            "activity_id": 701,
            "status": "non_executable",
            "reason": "missing_time_authority",
        }
    ]

    projection = project_theme_collection(plan, now=datetime(2026, 9, 1, 1, 0, tzinfo=TZ))
    assert projection["status"] == "pending_validation"
    assert projection["diagnostics"] == diagnostics


def test_completion_is_isolated_by_instance_and_business_date() -> None:
    occurrences = discover_theme_occurrences(
        _plan(
            _occurrence("仙园游宴", 304, XIANYUAN_START, XIANYUAN_END, runtime_id=11),
            _occurrence("仙园游宴", 304, XIANYUAN_START, XIANYUAN_END, runtime_id=12),
        )
    )
    now = datetime(2026, 9, 2, 0, 30, tzinfo=TZ)
    completed_instance_a = (
        occurrences[0].instance_key,
        "xianyuan-banquet",
        HOLY_WOOD_KIND,
        "2026-09-01",
    )

    due = due_theme_checkpoints(
        occurrences,
        now=now,
        completed_keys={completed_instance_a},
        production_kinds={HOLY_WOOD_KIND},
    )
    keys = {item.key for item in due}

    assert completed_instance_a not in keys
    assert (
        occurrences[0].instance_key,
        "xianyuan-banquet",
        HOLY_WOOD_KIND,
        "2026-09-02",
    ) in keys
    # Cross-instance isolation: instance B may still run today even though the
    # same stage already completed on instance A.
    assert (
        occurrences[1].instance_key,
        "xianyuan-banquet",
        HOLY_WOOD_KIND,
        "2026-09-02",
    ) in keys
    # History is not replayed: instance B's yesterday slot is no longer due.
    assert (
        occurrences[1].instance_key,
        "xianyuan-banquet",
        HOLY_WOOD_KIND,
        "2026-09-01",
    ) not in keys


def test_next_time_is_earliest_valid_pending_or_reconcile() -> None:
    occurrences = discover_theme_occurrences(_xianyuan_plan())
    now = datetime(2026, 9, 1, 0, 30, tzinfo=TZ)

    next_time = next_theme_collection_time(
        occurrences,
        now=now,
        production_kinds={HOLY_WOOD_KIND},
    )

    assert next_time == datetime(2026, 9, 2, 0, 0, tzinfo=TZ)


def test_iter_stops_and_propagates_on_member_failure() -> None:
    due_at = datetime(2026, 9, 1, 0, 0, tzinfo=TZ)
    checkpoints = tuple(
        ThemeCheckpoint(
            instance_key="theme:instance",
            member_id="xianyuan-banquet",
            stage=STAGE_ACTIVE,
            kind=kind,
            business_date="2026-09-01",
            due_at=due_at,
        )
        for kind in ("garden_banquet_1000", "garden_banquet_1200", "garden_banquet_1400")
    )

    calls: list[ThemeCheckpoint] = []

    def execute(checkpoint: ThemeCheckpoint):
        if len(calls) == 1:
            raise RuntimeError("theme member failed")
        calls.append(checkpoint)
        return {"status": "completed"}

    with pytest.raises(RuntimeError, match="theme member failed"):
        iter_theme_checkpoints(checkpoints, execute=execute)

    assert len(calls) == 1


def test_projection_owns_one_canonical_job() -> None:
    now = datetime(2026, 9, 1, 0, 30, tzinfo=TZ)
    occurrence = discover_theme_occurrences(_xianyuan_plan())[0]
    projection = project_theme_collection(
        _xianyuan_plan(),
        completed_stage_keys={
            (occurrence.instance_key, "xianyuan-banquet", HOLY_WOOD_KIND, "2026-09-01")
        },
        now=now,
        production_kinds={HOLY_WOOD_KIND},
    )

    assert projection["status"] == "ready"
    assert list(projection["desired_next_times"]) == [THEME_COLLECTION_TASK_ID]
    assert projection["desired_next_times"][THEME_COLLECTION_TASK_ID] == "2026-09-02 00:00:00"
    assert projection["due_stage_keys"] == []


def test_legacy_migration_requires_all_three_gates() -> None:
    now = datetime(2026, 9, 1, 0, 30, tzinfo=TZ)
    projection = project_theme_collection(
        _xianyuan_plan(),
        now=now,
        production_kinds={HOLY_WOOD_KIND},
    )

    blocked = theme_collection_migration_contract(
        projection,
        registered_standard_task_ids=["theme-collection"],
        completion_store_ready=False,
        daily_sync_adapter_ready=True,
    )
    assert blocked["status"] == "blocked"
    assert blocked["removed_task_ids"] == []

    ready = theme_collection_migration_contract(
        projection,
        registered_standard_task_ids=["theme-collection"],
        completion_store_ready=True,
        daily_sync_adapter_ready=True,
    )
    assert ready["status"] == "ready"
    assert "holy-wood-prayer" in ready["removed_task_ids"]

    missing_canonical = theme_collection_migration_contract(
        projection,
        registered_standard_task_ids=[],
        completion_store_ready=True,
        daily_sync_adapter_ready=True,
    )
    assert missing_canonical["status"] == "blocked"


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_stage_completion_store_isolates_instance_member_and_date() -> None:
    with _session() as session:
        persist_theme_stage_completion(
            session,
            instance_key="theme:instance-a",
            member_id="xianyuan-banquet",
            stage=STAGE_ACTIVE,
            stage_kind=HOLY_WOOD_KIND,
            business_date="2026-09-01",
            completed_at=datetime(2026, 9, 1, 0, 1, tzinfo=TZ),
        )
        persist_theme_stage_completion(
            session,
            instance_key="theme:instance-b",
            member_id="xianyuan-banquet",
            stage=STAGE_ACTIVE,
            stage_kind=HOLY_WOOD_KIND,
            business_date="2026-09-01",
            completed_at=datetime(2026, 9, 1, 0, 1, tzinfo=TZ),
        )

        keys = completed_theme_stage_keys(session)

        assert keys == {
            ("theme:instance-a", "xianyuan-banquet", HOLY_WOOD_KIND, "2026-09-01"),
            ("theme:instance-b", "xianyuan-banquet", HOLY_WOOD_KIND, "2026-09-01"),
        }
        assert (
            "theme:instance-a",
            "xianyuan-banquet",
            HOLY_WOOD_KIND,
            "2026-09-02",
        ) not in keys
        assert completed_theme_stage_keys(
            session, instance_keys=["theme:instance-a"]
        ) == {
            ("theme:instance-a", "xianyuan-banquet", HOLY_WOOD_KIND, "2026-09-01")
        }


def test_multiple_instances_are_due_independently() -> None:
    occurrences = discover_theme_occurrences(
        _plan(
            _occurrence("仙园游宴", 304, XIANYUAN_START, XIANYUAN_END, runtime_id=11),
            _occurrence("仙园游宴", 304, XIANYUAN_START, XIANYUAN_END, runtime_id=12),
        )
    )
    now = datetime(2026, 9, 1, 0, 30, tzinfo=TZ)

    due = due_theme_checkpoints(
        occurrences, now=now, production_kinds={HOLY_WOOD_KIND}
    )

    assert len(due) == 2
    assert {item.instance_key for item in due} == {
        occurrence.instance_key for occurrence in occurrences
    }


def test_garden_window_before_ten_only_holy_wood_is_due() -> None:
    occurrences = discover_theme_occurrences(_xianyuan_plan())
    now = datetime(2026, 9, 1, 9, 0, tzinfo=TZ)

    due = due_theme_checkpoints(occurrences, now=now)
    kinds = {item.kind for item in due}

    assert HOLY_WOOD_KIND in kinds
    assert not any(kind.startswith("garden_banquet") for kind in kinds)
    assert next_theme_collection_time(occurrences, now=now) == datetime(
        2026, 9, 1, 10, 0, tzinfo=TZ
    )


def test_garden_window_inside_runs_only_latest_missed_round() -> None:
    occurrences = discover_theme_occurrences(_xianyuan_plan())
    now = datetime(2026, 9, 1, 15, 0, tzinfo=TZ)

    due = due_theme_checkpoints(occurrences, now=now)
    garden = [item for item in due if item.kind.startswith("garden_banquet")]

    assert [item.kind for item in garden] == ["garden_banquet_1400"]
    assert next_theme_collection_time(occurrences, now=now) == datetime(
        2026, 9, 1, 16, 0, tzinfo=TZ
    )


def test_garden_window_after_22_has_no_round_today() -> None:
    occurrences = discover_theme_occurrences(_xianyuan_plan())
    now = datetime(2026, 9, 1, 22, 30, tzinfo=TZ)

    due = due_theme_checkpoints(occurrences, now=now)
    garden = [item for item in due if item.kind.startswith("garden_banquet")]

    assert garden == []
    # The next executable round belongs to tomorrow.
    assert next_theme_collection_time(occurrences, now=now) == datetime(
        2026, 9, 2, 0, 0, tzinfo=TZ
    )


def test_garden_window_tail_21_round_on_last_day() -> None:
    occurrences = discover_theme_occurrences(_xianyuan_plan())
    now = datetime(2026, 9, 3, 21, 30, tzinfo=TZ)

    due = due_theme_checkpoints(occurrences, now=now)
    kinds = {item.kind for item in due}

    assert GARDEN_BANQUET_TAIL_KIND in kinds
    assert HOLY_WOOD_TAIL_KIND in kinds


def test_history_and_expired_instances_never_run() -> None:
    occurrences = discover_theme_occurrences(_xianyuan_plan())

    after = datetime(2026, 9, 4, 0, 10, tzinfo=TZ)

    assert due_theme_checkpoints(occurrences, now=after) == ()
    assert active_theme_checkpoints(occurrences, now=after) == ()


def test_default_catalogue_contains_only_canonical_theme_job() -> None:
    from backend.core.fanxiu.data_annotation.kernel_scheduler_defaults import (
        default_kernel_scheduler_tasks,
    )

    tasks = default_kernel_scheduler_tasks(datetime(2026, 9, 1, 0, 0))
    ids = {str(task.get("id") or "") for task in tasks}

    assert "theme-collection" in ids
    assert ids.isdisjoint(THEME_COLLECTION_INTERNALIZED_TASK_IDS)


def test_theme_family_migration_is_idempotent_and_preserves_other_tasks() -> None:
    from backend.core.fanxiu.data_annotation.kernel_scheduler_defaults import (
        consolidate_arena_scheduler_instances,
    )

    raw = [
        {"id": "keep-me", "task_type": "keep_me", "next_time": "2026-09-01 00:00:00"},
        {
            "id": "penglai-xianzang-config",
            "task_type": "penglai_xianzang_config",
            "next_time": "2026-09-01 00:05:00",
        },
        {
            "id": "holy-wood-prayer",
            "task_type": "holy_wood_prayer",
            "next_time": "2026-09-01 00:00:00",
        },
        {
            "id": "theme-collection",
            "task_type": "theme_collection",
            "next_time": "2026-09-02 00:00:00",
        },
    ]

    migrated, changed = consolidate_arena_scheduler_instances(raw)
    ids = {str(item.get("id") or "") for item in migrated}

    assert changed is True
    assert "keep-me" in ids
    assert "theme-collection" in ids
    assert ids.isdisjoint(THEME_COLLECTION_INTERNALIZED_TASK_IDS)

    again, changed_again = consolidate_arena_scheduler_instances(migrated)

    assert changed_again is False
    assert {str(item.get("id") or "") for item in again} == ids


def _xianyuan_runtime(*, start: str, end: str, activity_id: int = 304) -> dict:
    start_ms = int(datetime.fromisoformat(start).timestamp() * 1000)
    end_ms = int(datetime.fromisoformat(end).timestamp() * 1000)
    return {
        "ok": True,
        "complete": True,
        "occurrences": [
            {
                "activity_id": activity_id,
                "base_id": 118000,
                "state": 2,
                "start_time_ms": start_ms,
                "end_time_ms": end_ms,
                "close_panel_time_ms": end_ms,
            }
        ],
    }


def test_read_theme_collection_plan_uses_activation_occurrence() -> None:
    from backend.core.fanxiu.data_annotation.tasks.theme_collection import (
        read_theme_collection_plan,
    )

    plan = read_theme_collection_plan(
        plan=_plan(),
        now=datetime(2026, 9, 1, 15, 0, tzinfo=TZ),
        xianyuan_reader=lambda **_: _xianyuan_runtime(
            start=XIANYUAN_START, end=XIANYUAN_END
        ),
        period_reader=lambda activity_id: {},
        completion_reader=lambda: set(),
    )

    assert plan["plan_ready"] is True
    kinds = {stage["kind"] for stage in plan["due_stages"]}
    assert HOLY_WOOD_KIND in kinds
    assert "garden_banquet_1400" in kinds
    assert "garden_banquet_1000" not in kinds


def test_read_theme_collection_plan_incomplete_keeps_trigger() -> None:
    from backend.core.fanxiu.data_annotation.tasks.theme_collection import (
        read_theme_collection_plan,
    )

    plan = read_theme_collection_plan(
        plan={"status": "not_loaded", "occurrences": [], "activity_observations": []},
        now=datetime(2026, 9, 1, 15, 0, tzinfo=TZ),
        xianyuan_reader=lambda **_: {"complete": False, "occurrences": []},
        period_reader=lambda activity_id: {},
        completion_reader=lambda: set(),
    )

    assert plan["plan_ready"] is False
    assert plan["next_time"] is None
    assert plan["retry_at"] is not None


def test_read_theme_collection_plan_revenue_period_is_authority() -> None:
    from backend.core.fanxiu.data_annotation.tasks.theme_collection import (
        read_theme_collection_plan,
    )

    def period_reader(activity_id: int) -> dict:
        assert activity_id == 701
        return {
            "complete": True,
            "activity_id": 701,
            "start_time_ms": int(
                datetime.fromisoformat(XIANYUAN_START).timestamp() * 1000
            ),
            "end_time_ms": int(
                datetime.fromisoformat(XIANYUAN_END).timestamp() * 1000
            ),
        }

    plan = read_theme_collection_plan(
        plan=_plan(
            observations=(
                {"name": "蓬莱仙藏", "activity_id": 701, "is_schedule_occurrence": False},
            )
        ),
        now=datetime(2026, 9, 1, 0, 30, tzinfo=TZ),
        xianyuan_reader=lambda **_: {"complete": True, "occurrences": []},
        period_reader=period_reader,
        completion_reader=lambda: set(),
    )

    assert plan["plan_ready"] is True
    assert "penglai-xianzang" in {
        row["member_id"] for row in plan["occurrences"]
    }
    assert "xianzang_config_0005" in {
        stage["kind"] for stage in plan["due_stages"]
    }


def test_read_theme_collection_plan_prefers_worldline_over_revenue_duplicate() -> None:
    from backend.core.fanxiu.data_annotation.tasks.theme_collection import (
        read_theme_collection_plan,
    )

    plan = read_theme_collection_plan(
        plan=_plan(
            _occurrence("蓬莱仙藏", 701, XIANYUAN_START, XIANYUAN_END, runtime_id=7),
            observations=(
                {"name": "蓬莱仙藏", "activity_id": 701, "is_schedule_occurrence": False},
            ),
        ),
        now=datetime(2026, 9, 1, 0, 30, tzinfo=TZ),
        xianyuan_reader=lambda **_: {"complete": True, "occurrences": []},
        period_reader=lambda activity_id: {
            "complete": True,
            "activity_id": activity_id,
            "start_time_ms": int(
                datetime.fromisoformat(XIANYUAN_START).timestamp() * 1000
            ),
            "end_time_ms": int(
                datetime.fromisoformat(XIANYUAN_END).timestamp() * 1000
            ),
        },
        completion_reader=lambda: set(),
    )

    members = [row["member_id"] for row in plan["occurrences"]]
    assert members.count("penglai-xianzang") == 1


def test_missing_revenue_period_is_diagnostic_not_action() -> None:
    from backend.core.fanxiu.data_annotation.tasks.theme_collection import (
        read_theme_collection_plan,
    )

    def period_reader(activity_id: int) -> dict:
        raise RuntimeError("activity period ambiguous")

    plan = read_theme_collection_plan(
        plan=_plan(
            observations=(
                {"name": "蓬莱仙藏", "activity_id": 701, "is_schedule_occurrence": False},
            )
        ),
        now=datetime(2026, 9, 1, 0, 30, tzinfo=TZ),
        xianyuan_reader=lambda **_: {"complete": True, "occurrences": []},
        period_reader=period_reader,
        completion_reader=lambda: set(),
    )

    assert plan["plan_ready"] is False
    assert "penglai-xianzang" in {
        row["member_id"] for row in plan["diagnostics"]
    }


def test_completed_latest_banquet_round_cannot_rearm_missed_slots():
    now = datetime(2026, 9, 2, 20, 30, tzinfo=TZ)
    plan = _xianyuan_plan()
    due = due_theme_checkpoints(discover_theme_occurrences(plan), now=now)
    completed = {c.key for c in due}
    projection = project_theme_collection(plan, now=now, completed_stage_keys=completed)
    assert projection["due_stages"] == []
    assert projection["desired_next_times"][THEME_COLLECTION_TASK_ID] == "2026-09-03 00:00:00"


def test_completed_last_day_round_wakes_for_2100_tail():
    now = datetime(2026, 9, 3, 20, 30, tzinfo=TZ)
    plan = _xianyuan_plan()
    due = due_theme_checkpoints(discover_theme_occurrences(plan), now=now)
    projection = project_theme_collection(plan, now=now, completed_stage_keys={c.key for c in due})
    assert projection["due_stages"] == []
    assert projection["desired_next_times"][THEME_COLLECTION_TASK_ID] == "2026-09-03 21:00:00"


def test_last_day_tail_supersedes_unfinished_earlier_banquet_rounds():
    now = datetime(2026, 9, 3, 21, 10, tzinfo=TZ)
    plan = _xianyuan_plan()
    due = due_theme_checkpoints(discover_theme_occurrences(plan), now=now)
    assert [c.kind for c in due if c.kind.startswith("garden_banquet")] == ["garden_banquet_2100"]
    projection = project_theme_collection(plan, now=now, completed_stage_keys={c.key for c in due})
    assert projection["due_stages"] == []


def test_holy_wood_tail_precedes_banquet_to_drain_reward_gifts():
    due = due_theme_checkpoints(discover_theme_occurrences(_xianyuan_plan()), now=datetime(2026,9,3,21,10,tzinfo=TZ))
    kinds = [c.kind for c in due]
    assert kinds.index("holy_wood_prayer_2100") < kinds.index("garden_banquet_2100")


def test_live_plan_checks_window_after_fact_reads(monkeypatch):
    import backend.core.fanxiu.data_annotation.tasks.theme_collection as api

    class Clock(datetime):
        current = datetime(2026, 9, 3, 21, 59, 59, tzinfo=TZ)

        @classmethod
        def now(cls, tz=None):
            return cls.current.astimezone(tz) if tz else cls.current

    def read_facts(**kwargs):
        Clock.current = datetime(2026, 9, 3, 22, 0, 1, tzinfo=TZ)
        return _xianyuan_plan()

    monkeypatch.setattr(api, "datetime", Clock)
    result = api.read_theme_collection_plan(
        plan_reader=read_facts,
        xianyuan_reader=lambda **_: {"available": True, "complete": True, "occurrences": []},
        completion_reader=lambda: set(),
    )
    assert not any(s["kind"].startswith("garden_banquet") for s in result["due_stages"])
    assert result["captured_at"] == "2026-09-03T22:00:01+08:00"
