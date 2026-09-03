from datetime import datetime

from backend.core.fanxiu.data_annotation.arena_schedule import (
    next_daofa_cycle_trigger_at,
    next_daofa_trigger_at,
    next_xianyuan_duel_cycle_trigger_at,
    next_xianyuan_duel_trigger_at,
    xianyuan_duel_scheduler_in_window,
)
from backend.core.fanxiu.data_annotation.runner import create_behavior_tree_executor
from backend.core.fanxiu.data_annotation.kernel_scheduler_defaults import (
    consolidate_arena_scheduler_instances,
    default_kernel_scheduler_tasks,
)


def test_arena_businesses_each_have_one_dynamic_scheduler_job():
    tasks = default_kernel_scheduler_tasks(now=datetime(2026, 8, 2, 10, 0, 0))
    daofa = [task for task in tasks if task["task_type"] == "daily_daofa"]
    xianyuan = [task for task in tasks if task["task_type"] == "daily_xianyuan_duel"]

    assert [
        (task["id"], task["label"], task["trigger_description"], task["next_time"])
        for task in daofa
    ] == [
        ("daily-daofa", "道法争锋", "动态", "2026-08-02 18:30:00")
    ]
    assert [
        (task["id"], task["label"], task["trigger_description"], task["next_time"])
        for task in xianyuan
    ] == [
        ("daily-xianyuan-duel", "仙缘斗法", "动态", "2026-08-02 19:00:00")
    ]


def test_scheduler_migration_removes_retired_daily_gongfeng_instance():
    tasks, changed = consolidate_arena_scheduler_instances([
        {"id": "legacy-daily-assistant", "task_type": "daily_assistant"},
        {
            "id": "legacy-daily-gongfeng",
            "task_type": "daily_gongfeng",
            "next_time": "2026-08-08 05:00:00",
        },
    ])

    assert changed is True
    assert [task["id"] for task in tasks] == ["legacy-daily-assistant"]


def test_scheduler_migration_retires_legacy_gameplay_job_while_family_is_hidden():
    tasks, changed = consolidate_arena_scheduler_instances([
        {
            "id": "magic-invasion-explore",
            "task_type": "magic_invasion_explore",
            "label": "魔道入侵_探查",
            "next_time": "2026-08-22 10:01:00",
            "payload": {
                "target_batches": 3,
                "batch_size": 500,
                "magic_invasion_progress": {"occurrence_id": "cross-8", "completed_batches": 1},
            },
        }
    ], now=datetime(2026, 8, 21, 23, 0, 0))

    assert changed is True
    assert tasks == []


def test_scheduler_migration_removes_existing_hidden_gameplay_lifecycle():
    tasks, changed = consolidate_arena_scheduler_instances([
        {
            "id": "magic-invasion-explore",
            "task_type": "magic_invasion_explore",
            "next_time": "2026-08-22 10:01:00",
            "payload": {"magic_invasion_progress": {"completed_batches": 2}},
        },
        {
            "id": "ranking-lifecycle",
            "task_type": "ranking_lifecycle",
            "next_time": "2026-08-22 19:00:00",
            "payload": {"max_execution_seconds": 10800, "owner": "parent"},
        },
        {
            "id": "yunmeng-tail",
            "task_type": "yunmeng_tail",
            "next_time": "not-a-time",
            "payload": {"gui_step": "exchange"},
        },
    ], now=datetime(2026, 8, 21, 23, 0, 0))

    assert changed is True
    assert tasks == []


def test_scheduler_migration_retires_every_gameplay_child_without_dual_track():
    retired = [
        ("magic-invasion-explore", "magic_invasion_explore"),
        ("xutian-palace-rankings", "xutian_palace_rankings"),
        ("xutian-palace-native-auto", "xutian_palace_native_auto"),
        ("yunmeng-trial-auto-challenge", "yunmeng_trial_auto_challenge"),
        ("yunmeng-tail", "yunmeng_tail"),
        ("legacy-daily-xianmeng", "daily_xianmeng"),
    ]
    raw = [
        {
            "id": task_id,
            "task_type": task_type,
            "next_time": f"2026-08-21 {22 + index:02d}:00:00",
            "payload": {"child": task_type},
            "last_result": "running",
        }
        for index, (task_id, task_type) in enumerate(retired)
    ]

    tasks, changed = consolidate_arena_scheduler_instances(
        raw,
        now=datetime(2026, 8, 21, 23, 0, 0),
    )

    assert changed is True
    assert tasks == []

    rerun, rerun_changed = consolidate_arena_scheduler_instances(
        tasks,
        now=datetime(2026, 8, 21, 23, 0, 0),
    )
    assert rerun_changed is False
    assert rerun == tasks


def test_default_scheduler_hides_gameplay_family_but_keeps_resource_family() -> None:
    tasks = default_kernel_scheduler_tasks(now=datetime(2026, 8, 21, 23, 0, 0))
    gameplay_types = {
        "ranking_lifecycle",
        "magic_invasion_explore",
        "xutian_palace_rankings",
        "xutian_palace_native_auto",
        "yunmeng_trial_auto_challenge",
        "yunmeng_tail",
        "daily_xianmeng",
        "resource_ranking",
        "resource_rank_daily_free_gift",
        "dandao_task_rewards",
        "yuanding_sansheng_daily_gift",
    }

    visible = [task for task in tasks if task["task_type"] in gameplay_types]
    assert [(task["id"], task["task_type"], task["label"]) for task in visible] == [
        ("resource-ranking", "resource_ranking", "资源榜"),
    ]


def test_scheduler_migration_is_idempotent_and_keeps_ranking_families_isolated():
    raw = [
        {
            "id": "ranking-lifecycle",
            "task_type": "ranking_lifecycle",
            "next_time": "2026-08-22 19:00:00",
            "payload": {"max_execution_seconds": 10800, "magic_invasion_progress": {"step": 3}},
        },
        {
            "id": "legacy-daily-xianmeng",
            "task_type": "daily_xianmeng",
            "next_time": "2026-08-22 10:00:00",
            "payload": {"gui_step": "battle"},
        },
        {
            "id": "resource-rank-daily-free-gift",
            "task_type": "resource_rank_daily_free_gift",
            "next_time": "2026-08-22 05:10:00",
            "payload": {"gui_step": "gift"},
        },
    ]
    migrated, changed = consolidate_arena_scheduler_instances(
        raw, now=datetime(2026, 8, 21, 23, 0, 0)
    )
    assert changed is True
    by_id = {item["id"]: item for item in migrated}
    assert set(by_id) == {"resource-ranking"}
    assert by_id["resource-ranking"]["next_time"] == "2026-08-22 00:30:00"
    assert by_id["resource-ranking"]["payload"] == {"max_execution_seconds": 10800}

    rerun, rerun_changed = consolidate_arena_scheduler_instances(
        migrated, now=datetime(2026, 8, 21, 23, 0, 0)
    )
    assert rerun_changed is False
    assert rerun == migrated


def test_scheduler_migration_removes_hidden_gameplay_and_normalizes_resource_label():
    migrated, changed = consolidate_arena_scheduler_instances([
        {
            "id": "ranking-lifecycle",
            "task_type": "old-type",
            "label": "旧玩法名",
            "template_id": "old-template",
            "template_label": "旧模板名",
            "next_time": "2026-08-22 00:30:00",
            "payload": {},
        },
        {
            "id": "resource-ranking",
            "task_type": "old-resource-type",
            "label": "旧资源名",
            "template_id": "old-resource-template",
            "template_label": "旧资源模板名",
            "next_time": "2026-08-22 00:30:00",
            "payload": {},
        },
    ])
    assert changed is True
    by_id = {item["id"]: item for item in migrated}
    assert "ranking-lifecycle" not in by_id
    assert (
        by_id["resource-ranking"]["task_type"],
        by_id["resource-ranking"]["label"],
        by_id["resource-ranking"]["template_id"],
        by_id["resource-ranking"]["template_label"],
    ) == ("resource_ranking", "资源榜", "resource_ranking", "资源榜")


def test_arena_next_time_switches_between_sunday_and_weekday_rules():
    saturday_end = datetime(2026, 8, 1, 23, 59, 59)
    sunday_end = datetime(2026, 8, 2, 23, 59, 59)

    assert next_daofa_trigger_at(saturday_end) == datetime(2026, 8, 2, 18, 30, 0)
    assert next_xianyuan_duel_trigger_at(saturday_end) == datetime(2026, 8, 2, 19, 0, 0)
    assert next_daofa_trigger_at(sunday_end) == datetime(2026, 8, 3, 23, 0, 0)
    assert next_xianyuan_duel_trigger_at(sunday_end) == datetime(2026, 8, 3, 23, 0, 0)


def test_arena_completed_cycle_always_advances_to_the_next_day():
    before_weekday_trigger = datetime(2026, 8, 4, 22, 46, 45)
    saturday_before_trigger = datetime(2026, 8, 1, 22, 0, 0)

    assert next_daofa_cycle_trigger_at(before_weekday_trigger) == datetime(2026, 8, 5, 23, 0, 0)
    assert next_xianyuan_duel_cycle_trigger_at(before_weekday_trigger) == datetime(2026, 8, 5, 23, 0, 0)
    assert next_daofa_cycle_trigger_at(saturday_before_trigger) == datetime(2026, 8, 2, 18, 30, 0)
    assert next_xianyuan_duel_cycle_trigger_at(saturday_before_trigger) == datetime(2026, 8, 2, 19, 0, 0)


def test_old_sunday_instances_are_folded_into_the_single_jobs():
    raw = [
        {"id": "daily-daofa", "next_time": "2026-08-03 23:00:00"},
        {"id": "sunday-daofa", "next_time": "2026-08-02 18:30:00"},
        {"id": "daily-xianyuan-duel", "next_time": "2026-08-03 23:00:00"},
        {"id": "sunday-xianyuan-duel", "next_time": "2026-08-02 19:00:00"},
    ]

    merged, changed = consolidate_arena_scheduler_instances(raw)
    by_id = {task["id"]: task for task in merged}

    assert changed is True
    assert set(by_id) == {"daily-daofa", "daily-xianyuan-duel"}
    assert by_id["daily-daofa"]["next_time"] == "2026-08-02 18:30:00"
    assert by_id["daily-xianyuan-duel"]["next_time"] == "2026-08-02 19:00:00"


def test_xianyuan_duel_admission_advances_stale_run_without_game_side_effects(monkeypatch):
    runner = create_behavior_tree_executor()
    persisted: list[tuple[str, str]] = []
    monkeypatch.setattr(
        runner,
        "_persist_scheduler_task_next_time",
        lambda task_id, next_time: persisted.append((task_id, next_time)),
    )
    monkeypatch.setitem(
        runner.daily_xianyuan_duel_admission.__func__.__globals__,
        "_now",
        lambda: datetime(2026, 8, 2, 9, 0, 0),
    )
    decision = runner.daily_xianyuan_duel_admission(
        {"__scheduler_task_id": "daily-xianyuan-duel"}
    )

    assert decision["result"] == "success"
    assert "next_time" not in decision
    assert persisted == [("daily-xianyuan-duel", "2026-08-02 19:00:00")]
    assert decision["current_scene"] is None
    assert decision["scheduler_incident"]["kind"] == "window_expired"


def test_xianyuan_duel_uses_game_availability_not_strategy_trigger_as_window(monkeypatch):
    runner = create_behavior_tree_executor()
    monkeypatch.setitem(
        runner.daily_xianyuan_duel_admission.__func__.__globals__,
        "_now",
        lambda: datetime(2026, 8, 4, 22, 51, 30),
    )

    assert runner.daily_xianyuan_duel_admission({"__scheduler_task_id": "daily-xianyuan-duel"}) is None
    assert xianyuan_duel_scheduler_in_window(datetime(2026, 8, 2, 10, 0, 0)) is True
    assert xianyuan_duel_scheduler_in_window(datetime(2026, 8, 2, 21, 59, 59)) is True
    assert xianyuan_duel_scheduler_in_window(datetime(2026, 8, 2, 22, 0, 0)) is False
