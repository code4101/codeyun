import json
import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime as real_datetime

from backend.core.fanxiu.data_annotation import behavior_tree_control
from backend.core.fanxiu.data_annotation.default_jobs import (
    register_fanxiu_data_annotation_default_runtime_jobs,
)
from backend.core.fanxiu.data_annotation.jobs import (
    list_fanxiu_data_annotation_task_cell_definitions,
)
from backend.core.fanxiu.data_annotation.scheduler_defaults import (
    INTERNAL_SCHEDULER_PRIMITIVE_TASK_TYPES,
)


def _file_fingerprint(path):
    payload = path.read_bytes()
    return hashlib.sha256(payload).hexdigest(), path.stat().st_mtime_ns, len(payload)


def test_read_scheduler_tasks_projects_repairs_without_persisting(monkeypatch, tmp_path):
    scheduler_path = tmp_path / "scheduler_tasks.json"
    world_facts_path = tmp_path / "world_facts.json"
    scheduler_path.write_text('[{"id":"legacy"}]', encoding="utf-8")
    world_facts_path.write_text('{"seed":1}', encoding="utf-8")
    before = (_file_fingerprint(scheduler_path), _file_fingerprint(world_facts_path))

    monkeypatch.setattr(
        behavior_tree_control,
        "consolidate_arena_scheduler_instances",
        lambda raw: (raw, True),
    )

    def fake_repair(raw, _defaults, facts, **_kwargs):
        facts["derived"] = True
        return [*raw, {"id": "projected"}], True

    monkeypatch.setattr(behavior_tree_control, "repair_data_annotation_scheduler_tasks", fake_repair)
    monkeypatch.setattr(behavior_tree_control, "default_data_annotation_scheduler_tasks", lambda: [])
    monkeypatch.setattr(
        behavior_tree_control,
        "write_world_facts",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("read wrote world facts")),
    )
    monkeypatch.setattr(
        behavior_tree_control,
        "write_scheduler_tasks",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("read wrote scheduler")),
    )

    tasks = behavior_tree_control.read_scheduler_tasks(
        scheduler_state_path=scheduler_path,
        world_facts_path=world_facts_path,
    )

    assert {task["id"] for task in tasks} == {"legacy", "projected"}
    assert (_file_fingerprint(scheduler_path), _file_fingerprint(world_facts_path)) == before


def test_maintain_scheduler_tasks_persists_repairs_and_configuration_backup(monkeypatch, tmp_path):
    scheduler_path = tmp_path / "scheduler_tasks.json"
    world_facts_path = tmp_path / "world_facts.json"
    scheduler_path.write_text('[{"id":"legacy","dispatch_order":1}]', encoding="utf-8")
    world_facts_path.write_text('{"seed":1}', encoding="utf-8")

    monkeypatch.setattr(
        behavior_tree_control,
        "consolidate_arena_scheduler_instances",
        lambda raw: (raw, False),
    )

    def fake_repair(_raw, _defaults, facts, **_kwargs):
        facts["derived"] = True
        return [{"id": "legacy", "dispatch_order": 2}], True

    monkeypatch.setattr(behavior_tree_control, "repair_data_annotation_scheduler_tasks", fake_repair)
    monkeypatch.setattr(behavior_tree_control, "default_data_annotation_scheduler_tasks", lambda: [])

    tasks = behavior_tree_control.maintain_scheduler_tasks(
        scheduler_state_path=scheduler_path,
        world_facts_path=world_facts_path,
    )

    assert tasks[0]["dispatch_order"] == 2
    assert json.loads(scheduler_path.read_text(encoding="utf-8"))[0]["dispatch_order"] == 2
    assert json.loads(world_facts_path.read_text(encoding="utf-8"))["derived"] is True
    backup_path = scheduler_path.with_name("scheduler_tasks.previous-config.json")
    assert json.loads(backup_path.read_text(encoding="utf-8"))[0]["dispatch_order"] == 1


def test_build_scheduler_plan_does_not_reconcile_attempts(monkeypatch):
    monkeypatch.setattr(
        behavior_tree_control,
        "read_scheduler_settings",
        lambda **_kwargs: {"job_group_enabled": True, "time_sequence": {}},
    )
    monkeypatch.setattr(behavior_tree_control, "read_scheduler_tasks", lambda **_kwargs: [])
    monkeypatch.setattr(behavior_tree_control, "read_world_facts", lambda _path=None: {})
    monkeypatch.setattr(behavior_tree_control, "scheduler_tasks_for_dispatch", lambda tasks, **_kwargs: tasks)
    monkeypatch.setattr(behavior_tree_control, "behavior_tree_runtime_runner_status", lambda: {})
    monkeypatch.setattr(
        behavior_tree_control,
        "build_data_annotation_scheduler_plan",
        lambda *_args, **_kwargs: {"next_action": "idle", "message": "idle"},
    )
    monkeypatch.setattr(
        behavior_tree_control,
        "reconcile_stale_scheduler_attempts",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("pure plan reconciled")),
    )

    plan = behavior_tree_control.build_scheduler_plan(include_blocking_overlays=False)

    assert plan["next_action"] == "idle"


def test_every_scheduler_business_job_is_in_the_standard_checklist():
    register_fanxiu_data_annotation_default_runtime_jobs()
    definitions = list_fanxiu_data_annotation_task_cell_definitions()
    defaults = behavior_tree_control.default_data_annotation_scheduler_tasks()
    default_types = [str(task.get("task_type") or "") for task in defaults]

    assert len(default_types) == len(set(default_types))
    assert {
        definition.task_type
        for definition in definitions
        if definition.scheduler_supported
        and not definition.task_type.startswith("test_")
    } - set(default_types) == set(INTERNAL_SCHEDULER_PRIMITIVE_TASK_TYPES)

    defaults_by_type = {task["task_type"]: task for task in defaults}
    for definition in definitions:
        if not definition.standard_job:
            continue
        task = defaults_by_type[definition.task_type]
        assert task["id"] == definition.standard_job_id
        assert task["label"] == definition.label
        assert task["trigger_description"] == definition.standard_job_description
        assert task["payload"] == definition.standard_job_payload


def test_daily_lundao_default_does_not_restart_whole_navigation_each_second():
    task = next(
        item
        for item in behavior_tree_control.default_data_annotation_scheduler_tasks()
        if item["id"] == "daily-lundao-seat"
    )

    assert task["error_retry_delay_seconds"] == 60


def test_xianfu_visit_partner_has_explicit_ten_minute_business_budget():
    task = next(
        item
        for item in behavior_tree_control.default_data_annotation_scheduler_tasks()
        if item["id"] == "xianfu-visit-partner"
    )

    assert task["payload"]["max_runtime_seconds"] == 600


def test_explicit_interrupt_closes_runtime_and_attempt_without_consuming_trigger(monkeypatch):
    persisted = []
    written = []
    facts = []
    incidents = []
    task = {
        "id": "job-a",
        "label": "作业A",
        "last_result": "running",
        "next_time": "2026-07-29 14:00:00",
        "attempt_id": "attempt-a",
        "attempt_original_trigger": "2026-07-29 13:00:00",
    }
    monkeypatch.setattr(
        behavior_tree_control,
        "behavior_tree_runtime_status",
        lambda **_kwargs: {
            "running": True,
            "status": "running",
            "phase": "scheduler_task",
            "current_task_id": "job-a",
            "current_task": "作业A",
            "task_type": "job_a",
        },
    )
    monkeypatch.setattr(
        behavior_tree_control,
        "stop_fanxiu_behavior_tree_current_task",
        lambda _entry_id: {"alive": True, "execution_state": "idle"},
    )
    monkeypatch.setattr(
        behavior_tree_control,
        "persist_behavior_tree_runtime_status",
        lambda status, **_kwargs: persisted.append(dict(status)),
    )
    monkeypatch.setattr(behavior_tree_control, "read_scheduler_tasks", lambda **_kwargs: [task])
    monkeypatch.setattr(
        behavior_tree_control,
        "write_scheduler_tasks",
        lambda tasks, **_kwargs: written.append(deepcopy(tasks)),
    )
    monkeypatch.setattr(
        behavior_tree_control,
        "record_scheduler_task_fact",
        lambda item, result, **_kwargs: facts.append((deepcopy(item), result)),
    )
    monkeypatch.setattr(
        behavior_tree_control,
        "record_scheduler_incident",
        lambda **kwargs: incidents.append(deepcopy(kwargs)) or kwargs,
    )

    result = behavior_tree_control.stop_current_task("game-window2")

    assert result["running"] is False
    assert result["status"] == "interrupted"
    assert result["current_task_id"] == ""
    assert persisted[-1]["phase"] == "interrupted"
    assert written[-1][0]["last_result"] == "interrupted"
    assert written[-1][0]["next_time"] == "2026-07-29 13:00:00"
    assert written[-1][0]["attempt_id"] is None
    assert written[-1][0]["attempt_original_trigger"] is None
    assert facts[-1][1] == "interrupted"
    assert incidents[-1]["incident"]["kind"] == "attempt_interrupted"
    assert incidents[-1]["attempt_id"] == "attempt-a"


def test_runtime_status_never_projects_running_when_kernel_is_idle(monkeypatch):
    stale = {
        "running": False,
        "status": "running",
        "phase": "scheduler_task",
        "current_task_id": "job-a",
        "current_task": "作业A",
        "updated_at": time.time(),
        "logs": [],
    }
    monkeypatch.setattr(behavior_tree_control, "read_behavior_tree_runtime_status", lambda _path=None: stale)
    monkeypatch.setattr(behavior_tree_control, "behavior_tree_runtime_runner_status", lambda: stale)
    monkeypatch.setattr(behavior_tree_control, "behavior_tree_enabled", lambda **_kwargs: True)
    monkeypatch.setattr(
        "backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda: {"alive": True, "execution_state": "idle"},
    )

    result = behavior_tree_control.behavior_tree_runtime_status()

    assert result["running"] is False
    assert result["status"] == "stopped"
    assert result["phase"] == "stopped"
    assert result["current_task_id"] == ""


def test_scheduler_time_sequence_projects_every_occurrence_and_groups_by_level():
    tasks = [
        {
            "id": "legacy-daily-assistant",
            "label": "日常_助手",
            "next_time": "2026-07-30 00:00:00",
        },
        {
            "id": "daily-signin",
            "label": "日常_签到",
            "next_time": "2026-07-30 00:00:00",
        },
    ]

    groups = behavior_tree_control.scheduler_time_sequence_groups(tasks)

    midnight = next(group for group in groups if group["key"] == "00:00")
    assert [item["task_id"] for item in midnight["items"]] == [
        "legacy-daily-assistant",
        "daily-signin",
    ]
    assert [item["bias_minutes"] for item in midnight["items"]] == [0, 1]


def test_update_scheduler_time_sequence_is_independent_configuration(tmp_path):
    state = [
        {
            "id": "assistant",
            "label": "日常_助手",
            "schedule_kind": "daily",
            "dispatch_level": 0,
            "original_schedule_times": ["00:00", "12:00"],
            "schedule_times": ["00:01", "12:01"],
            "schedule_offsets_minutes": [1, 1],
            "weekdays": [],
        },
        {
            "id": "signin",
            "label": "日常_签到",
            "schedule_kind": "daily",
            "dispatch_level": 0,
            "original_schedule_times": ["00:00"],
            "schedule_times": ["00:02"],
            "schedule_offsets_minutes": [2],
            "weekdays": [],
        },
    ]
    settings_path = tmp_path / "scheduler-settings.json"
    result = behavior_tree_control.update_scheduler_time_sequence(
        [{"key": "00:00", "task_ids": ["signin", "assistant"]}],
        scheduler_settings_path=settings_path,
    )
    assert result["time_sequence"]["00:00"] == ["signin", "assistant"]
    assert state[0]["schedule_times"] == ["00:01", "12:01"]


def test_scheduler_time_sequence_omits_same_clock_tasks_on_disjoint_weekdays():
    tasks = [
        {
            "id": "weekday",
            "label": "周一至周六作业",
            "schedule_kind": "weekly",
            "dispatch_level": 0,
            "weekdays": [0, 1, 2, 3, 4, 5],
            "original_schedule_times": ["18:30"],
            "schedule_times": ["18:30"],
        },
        {
            "id": "sunday",
            "label": "周日作业",
            "schedule_kind": "weekly",
            "dispatch_level": 0,
            "weekdays": [6],
            "original_schedule_times": ["18:30"],
            "schedule_times": ["18:30"],
        },
    ]

    assert all(not group["items"] for group in behavior_tree_control.scheduler_time_sequence_groups(tasks))


def test_scheduler_time_sequence_keeps_resolved_configuration_visible():
    tasks = [
        {
            "id": "daily-daofa",
            "label": "道法争锋",
            "next_time": "2026-07-30 23:00:00",
        },
        {
            "id": "daily-xianyuan-duel",
            "label": "仙缘斗法",
            "next_time": "2026-07-30 23:00:00",
        },
    ]

    groups = behavior_tree_control.scheduler_time_sequence_groups(tasks)

    group = next(group for group in groups if group["key"] == "23:00")
    assert [item["effective_next_time"] for item in group["items"]] == [
        "2026-07-30 23:00:00",
        "2026-07-30 23:01:00",
    ]


def test_scheduler_time_sequence_heals_legacy_gaps_and_materialized_next_time():
    tasks = [
        {
            "id": "first",
            "label": "第一项",
            "schedule_kind": "daily",
            "dispatch_level": 0,
            "original_schedule_times": ["05:00"],
            "schedule_offsets_minutes": [0],
            "schedule_times": ["05:00"],
            "schedule_layers_revision": 2,
            "next_time": "2026-07-26 05:00:00",
        },
        {
            "id": "second",
            "label": "第二项",
            "schedule_kind": "daily",
            "dispatch_level": 0,
            "original_schedule_times": ["05:00"],
            "schedule_offsets_minutes": [2],
            "schedule_times": ["05:02"],
            "schedule_layers_revision": 2,
            "next_time": "2026-07-26 05:02:00",
        },
    ]

    projected = behavior_tree_control.scheduler_tasks_for_dispatch(tasks)
    assert projected[0]["next_time"] == "2026-07-26 05:00:00"
    assert projected[1]["next_time"] == "2026-07-26 05:02:00"
    assert tasks[1]["next_time"] == "2026-07-26 05:02:00"


def test_scheduler_time_layers_persist_originals_and_offsets_separately():
    from backend.core.fanxiu.data_annotation.state import (
        data_annotation_scheduler_task_state,
        normalize_data_annotation_scheduler_task,
    )

    normalized = normalize_data_annotation_scheduler_task({
        "id": "daily-a",
        "task_type": "daily_a",
        "schedule_kind": "daily",
        "original_schedule_times": ["05:00", "12:00"],
        "schedule_offsets_minutes": [2, 0],
        "schedule_layers_revision": 2,
        # Compatibility projection must never override the two authoritative layers.
        "schedule_times": ["09:09", "09:09"],
    })

    assert "original_schedule_times" not in normalized
    assert "schedule_offsets_minutes" not in normalized
    assert "schedule_times" not in normalized

    persisted = data_annotation_scheduler_task_state(normalized)
    assert persisted == normalized


def test_scheduler_time_layers_migrate_legacy_effective_times_to_offsets():
    from backend.core.fanxiu.data_annotation.state import normalize_data_annotation_scheduler_task

    normalized = normalize_data_annotation_scheduler_task({
        "id": "daily-a",
        "task_type": "daily_a",
        "schedule_kind": "daily",
        "original_schedule_times": ["05:00"],
        "schedule_times": ["05:03"],
    })

    assert "original_schedule_times" not in normalized
    assert "schedule_offsets_minutes" not in normalized
    assert "schedule_times" not in normalized


def test_scheduler_task_normalization_preserves_immediate_error_retry():
    from backend.core.fanxiu.data_annotation.state import (
        normalize_data_annotation_scheduler_task,
    )

    normalized = normalize_data_annotation_scheduler_task({
        "id": "daily-windowed",
        "task_type": "daily_windowed",
        "error_retry_delay_seconds": 0,
    })

    assert normalized["error_retry_delay_seconds"] == 0


def test_recovered_emulator_restart_keeps_login_due_until_success():
    task = {
        "id": "login-game",
        "trigger_description": "手动",
        "dispatch_level": 5,
        "error_retry_delay_seconds": 0,
        "last_message": (
            "FanxiuEmulatorRestartRequired: ADB 持续取帧失败；"
            "已完整重启 MuMu，当前业务尝试作废"
        ),
        "next_time": "2026-08-20 20:59:00",
    }

    behavior_tree_control.schedule_failed_task_retry(
        task, real_datetime(2026, 8, 20, 21, 11, 19)
    )

    assert task["next_time"] == "2026-08-20 21:11:19"


def test_ordinary_manual_login_failure_still_has_no_autonomous_retry():
    task = {
        "id": "login-game",
        "trigger_description": "手动",
        "dispatch_level": 5,
        "error_retry_delay_seconds": 0,
        "last_message": "RuntimeError: 登录游戏：挑选账号需要人工处理",
        "next_time": "2026-08-20 21:00:00",
    }

    behavior_tree_control.schedule_failed_task_retry(
        task, real_datetime(2026, 8, 20, 21, 11, 19)
    )

    assert task["next_time"] is None


def test_scheduler_time_layers_restore_all_historical_parallel_batches():
    from backend.core.fanxiu.data_annotation.state import normalize_data_annotation_scheduler_task

    legacy = [
        {
            "id": "legacy-daily-assistant",
            "task_type": "daily_assistant",
            "schedule_kind": "daily",
            "original_schedule_times": ["00:01", "05:03", "12:00", "18:00"],
            "schedule_times": ["00:01", "05:03", "12:00", "18:00"],
        },
        {
            "id": "mail-selective-claim",
            "task_type": "mail_selective_claim",
            "schedule_kind": "daily",
            "original_schedule_times": ["00:07"],
            "schedule_times": ["00:07"],
        },
        {
            "id": "daily-boss",
            "task_type": "daily_boss",
            "schedule_kind": "daily",
            "original_schedule_times": ["05:01"],
            "schedule_times": ["05:01"],
            "next_time": "2026-07-26 05:01:00",
        },
        {
            "id": "legacy-daily-mojie-raid",
            "task_type": "daily_mojie_raid",
            "schedule_kind": "daily",
            "original_schedule_times": ["13:01", "21:33"],
            "schedule_times": ["13:01", "21:33"],
            # Business-managed weekly sleep must not be mistaken for an old
            # materialized daily occurrence.
            "next_time": "2026-07-27 13:00:00",
        },
    ]

    normalized = [normalize_data_annotation_scheduler_task(task) for task in legacy]
    by_id = {task["id"]: task for task in normalized}

    assert all("schedule_times" not in task for task in normalized)
    assert by_id["daily-boss"]["next_time"] == "2026-07-26 05:01:00"
    assert by_id["legacy-daily-mojie-raid"]["next_time"] == "2026-07-27 13:00:00"


def test_scheduler_time_sequence_writes_offsets_without_changing_originals(tmp_path):
    scheduler_path = tmp_path / "scheduler.json"
    settings_path = tmp_path / "settings.json"
    selected = [
        deepcopy(task)
        for task in behavior_tree_control.default_data_annotation_scheduler_tasks()
        if task["id"] in {"daily-daofa", "daily-xianyuan-duel"}
    ]
    behavior_tree_control.write_scheduler_tasks(
        selected,
        scheduler_state_path=scheduler_path,
        preserve_runtime_state=False,
    )

    behavior_tree_control.update_scheduler_time_sequence(
        [{
            "key": "23:00",
            "task_ids": ["daily-daofa", "daily-xianyuan-duel"],
        }],
        scheduler_settings_path=settings_path,
    )

    persisted = {
        task["id"]: task
        for task in json.loads(scheduler_path.read_text(encoding="utf-8"))
    }
    selected_by_id = {task["id"]: task for task in selected}
    assert persisted["daily-daofa"]["next_time"] == selected_by_id["daily-daofa"]["next_time"]
    assert (
        persisted["daily-xianyuan-duel"]["next_time"]
        == selected_by_id["daily-xianyuan-duel"]["next_time"]
    )
    assert behavior_tree_control.read_scheduler_settings(
        scheduler_settings_path=settings_path
    )["time_sequence"]["23:00"] == ["daily-daofa", "daily-xianyuan-duel"]


def test_update_scheduler_tasks_rejects_old_trigger_fields(monkeypatch):
    task = next(
        item for item in behavior_tree_control.default_data_annotation_scheduler_tasks()
        if item["id"] == "daily-lundao-seat"
    )
    state = [deepcopy(task)]
    monkeypatch.setattr(behavior_tree_control, "read_scheduler_tasks", lambda **_kwargs: deepcopy(state))
    monkeypatch.setattr(
        behavior_tree_control,
        "write_scheduler_tasks",
        lambda tasks, **_kwargs: state.__setitem__(slice(None), deepcopy(tasks)),
    )

    incoming = deepcopy(task)
    incoming.update({
        "schedule_kind": "daily",
        "schedule_times": ["14:00"],
        "trigger_kind": "daily",
    })
    result = behavior_tree_control.update_scheduler_tasks(
        [incoming],
        now=real_datetime(2026, 7, 20, 14, 5, 0),
    )

    assert "schedule_kind" not in result[0]
    assert "schedule_times" not in result[0]
    assert "trigger_kind" not in result[0]
    assert "enabled" not in result[0]


def test_scheduler_task_update_never_deletes_omitted_task_when_counts_match():
    current = [
        {"id": "lingmai-clear", "task_type": "daily_lingmai_clear", "label": "灵脉_清体力", "next_time": "2026-07-24 21:30:00"},
        {"id": "dongtian-clear", "task_type": "daily_dongtian_clear", "label": "洞天_行动力", "next_time": "2026-07-24 21:30:00"},
    ]
    incoming = [
        {**current[1], "next_time": None},
        {"id": "new-task", "task_type": "daily_new", "label": "新作业", "next_time": None},
    ]

    merged = behavior_tree_control.merge_data_annotation_scheduler_task_updates(current, incoming)

    by_id = {task["id"]: task for task in merged}
    assert list(by_id) == ["lingmai-clear", "dongtian-clear", "new-task"]
    assert by_id["lingmai-clear"]["next_time"] == "2026-07-24 21:30:00"
    assert by_id["dongtian-clear"]["next_time"] is None


def test_scheduler_empty_update_is_noop_instead_of_clearing_tasks():
    current = [
        {"id": "lingmai-clear", "task_type": "daily_lingmai_clear", "label": "灵脉_清体力", "next_time": None},
    ]

    merged = behavior_tree_control.merge_data_annotation_scheduler_task_updates(current, [])

    assert [task["id"] for task in merged] == ["lingmai-clear"]
    assert merged[0]["next_time"] is None


def test_scheduler_configuration_change_keeps_one_rollback_snapshot(tmp_path):
    state_path = tmp_path / "scheduler_tasks.json"
    previous_config_path = tmp_path / "scheduler_tasks.previous-config.json"
    level_one = [{"id": "daily-lingquan", "dispatch_level": 1, "last_result": ""}]
    behavior_tree_control.write_data_annotation_json(state_path, level_one)

    level_zero = [{"id": "daily-lingquan", "dispatch_level": 0, "last_result": ""}]
    behavior_tree_control.write_scheduler_tasks(
        level_zero,
        scheduler_state_path=state_path,
        preserve_runtime_state=False,
    )

    assert json.loads(previous_config_path.read_text(encoding="utf-8")) == level_one

    behavior_tree_control.write_scheduler_tasks(
        [{**level_zero[0], "last_result": "success"}],
        scheduler_state_path=state_path,
        preserve_runtime_state=False,
    )

    assert json.loads(previous_config_path.read_text(encoding="utf-8")) == level_one


def test_scheduler_single_job_write_cannot_clear_another_jobs_fresh_retry(tmp_path):
    state_path = tmp_path / "scheduler_tasks.json"
    original = [
        {
            "id": "daily-experience",
            "task_type": "daily_experience",
            "label": "日常_经验",
            "next_time": "2026-08-02 08:08:28",
            "last_run_at": "2026-08-02 07:41:19",
            "last_result": "error",
        },
        {
            "id": "another-job",
            "task_type": "another_job",
            "label": "其它作业",
            "next_time": "2026-08-02 08:10:00",
            "last_run_at": None,
            "last_result": "",
        },
    ]
    behavior_tree_control.write_data_annotation_json(state_path, original)
    stale_for_experience = deepcopy(original)
    stale_for_other = deepcopy(original)

    stale_for_experience[0]["next_time"] = "2026-08-02 08:21:59"
    behavior_tree_control.write_scheduler_tasks(
        stale_for_experience,
        scheduler_state_path=state_path,
        runtime_update_ids={"daily-experience"},
    )

    stale_for_other[1]["last_result"] = "success"
    stale_for_other[1]["next_time"] = None
    behavior_tree_control.write_scheduler_tasks(
        stale_for_other,
        scheduler_state_path=state_path,
        runtime_update_ids={"another-job"},
    )

    persisted = {
        item["id"]: item
        for item in json.loads(state_path.read_text(encoding="utf-8"))
    }
    assert persisted["daily-experience"]["next_time"] == "2026-08-02 08:21:59"
    assert persisted["daily-experience"]["last_result"] == "error"
    assert persisted["another-job"]["next_time"] is None
    assert persisted["another-job"]["last_result"] == "success"


def test_scheduler_attempt_claim_is_cross_process_compare_and_swap(tmp_path):
    state_path = tmp_path / "scheduler_tasks.json"
    idle = {
        "id": "urgent-job",
        "task_type": "urgent_job",
        "label": "限时作业",
        "next_time": "2026-08-20 12:30:00",
        "last_result": "",
        "attempt_id": None,
    }
    behavior_tree_control.write_data_annotation_json(state_path, [idle])

    first = [{**idle, "last_result": "running", "attempt_id": "attempt-a"}]
    second = [{**idle, "last_result": "running", "attempt_id": "attempt-b"}]

    assert behavior_tree_control.write_scheduler_tasks(
        first,
        scheduler_state_path=state_path,
        runtime_update_ids={"urgent-job"},
        expected_runtime_attempt_ids={"urgent-job": None},
    ) is True
    assert behavior_tree_control.write_scheduler_tasks(
        second,
        scheduler_state_path=state_path,
        runtime_update_ids={"urgent-job"},
        expected_runtime_attempt_ids={"urgent-job": None},
    ) is False

    persisted = json.loads(state_path.read_text(encoding="utf-8"))[0]
    assert persisted["last_result"] == "running"
    assert persisted["attempt_id"] == "attempt-a"


def test_scheduler_losing_attempt_claim_does_not_submit_cell(monkeypatch):
    task = {
        "id": "urgent-job",
        "task_type": "urgent_job",
        "label": "限时作业",
        "next_time": "2026-08-20 12:30:00",
        "last_result": "",
        "attempt_id": None,
    }
    monkeypatch.setattr(
        "backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda **_kwargs: {"alive": True, "execution_state": "idle", "generation": 7},
    )
    monkeypatch.setattr(
        behavior_tree_control,
        "read_scheduler_tasks",
        lambda **_kwargs: [deepcopy(task)],
    )
    monkeypatch.setattr(
        behavior_tree_control,
        "write_scheduler_tasks",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        behavior_tree_control,
        "submit_runtime_task_cell",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("不得提交重复 Cell")),
    )

    result = behavior_tree_control._run_scheduler_task_cell_and_record_terminal(
        entry=object(),
        entry_id="game-window2",
        task=deepcopy(task),
        scheduled_attempt=True,
    )

    assert result["status"] == "running"
    assert result["phase"] == "scheduler_attempt_already_claimed"


def test_scheduler_cell_dispatch_lane_is_cross_process_exclusive(monkeypatch, tmp_path):
    task = {
        "id": "urgent-job",
        "task_type": "urgent_job",
        "label": "限时作业",
        "next_time": "2026-08-20 12:30:00",
        "last_result": "",
        "attempt_id": None,
    }
    entered = threading.Event()
    release = threading.Event()
    submit_count = 0

    def submit_cell(**_kwargs):
        nonlocal submit_count
        submit_count += 1
        entered.set()
        assert release.wait(timeout=3.0)
        return {"status": "success", "message": "done"}

    monkeypatch.setattr(
        "backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda **_kwargs: {"alive": True, "execution_state": "idle", "generation": 7},
    )
    monkeypatch.setattr(
        behavior_tree_control,
        "read_scheduler_tasks",
        lambda **_kwargs: [deepcopy(task)],
    )
    monkeypatch.setattr(
        behavior_tree_control,
        "write_scheduler_tasks",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(behavior_tree_control, "record_scheduler_task_fact", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(behavior_tree_control, "submit_runtime_task_cell", submit_cell)
    monkeypatch.setattr(behavior_tree_control, "_higher_level_due_task_for_attempt", lambda *_args, **_kwargs: None)

    with ThreadPoolExecutor(max_workers=1) as executor:
        first_future = executor.submit(
            behavior_tree_control._run_scheduler_task_cell_and_record_terminal,
            entry=object(),
            entry_id="game-window2",
            task=deepcopy(task),
            scheduler_state_path=tmp_path / "scheduler_tasks.json",
        )
        assert entered.wait(timeout=3.0)
        second = behavior_tree_control._run_scheduler_task_cell_and_record_terminal(
            entry=object(),
            entry_id="game-window2",
            task=deepcopy(task),
            scheduler_state_path=tmp_path / "scheduler_tasks.json",
        )
        release.set()
        first = first_future.result(timeout=3.0)

    assert first["status"] == "success"
    assert second["status"] == "running"
    assert second["phase"] == "scheduler_dispatch_already_owned"
    assert submit_count == 1


def test_stale_scheduler_snapshot_cannot_delete_newer_job_membership(tmp_path):
    state_path = tmp_path / "scheduler_tasks.json"
    current = [
        {
            "id": "existing-job",
            "task_type": "existing_job",
            "label": "已有作业",
            "next_time": None,
        },
        {
            "id": "new-standard-job",
            "task_type": "new_standard_job",
            "label": "新标准作业",
            "next_time": None,
        },
    ]
    behavior_tree_control.write_data_annotation_json(state_path, current)

    behavior_tree_control.write_scheduler_tasks(
        [deepcopy(current[0])],
        scheduler_state_path=state_path,
        preserve_runtime_state=False,
    )

    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert {item["id"] for item in persisted} == {
        "existing-job",
        "new-standard-job",
    }


def test_scheduler_job_removal_must_be_explicit(tmp_path):
    state_path = tmp_path / "scheduler_tasks.json"
    current = [
        {"id": "keep", "task_type": "keep", "label": "保留"},
        {"id": "obsolete", "task_type": "obsolete", "label": "迁移删除"},
    ]
    behavior_tree_control.write_data_annotation_json(state_path, current)

    behavior_tree_control.write_scheduler_tasks(
        [deepcopy(current[0])],
        scheduler_state_path=state_path,
        preserve_runtime_state=False,
        removed_task_ids={"obsolete"},
    )

    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert [item["id"] for item in persisted] == ["keep"]


def test_kernel_scheduler_writer_uses_shared_membership_safe_store(monkeypatch, tmp_path):
    from backend.core.fanxiu.data_annotation import behavior_tree_runtime

    state_path = tmp_path / "scheduler_tasks.json"
    current = [
        {"id": "kernel-job", "task_type": "kernel_job", "label": "Kernel 作业"},
        {"id": "api-added-job", "task_type": "api_added_job", "label": "API 新作业"},
    ]
    behavior_tree_control.write_data_annotation_json(state_path, current)
    monkeypatch.setattr(
        behavior_tree_runtime,
        "_data_annotation_scheduler_state_path",
        lambda: state_path,
    )

    behavior_tree_runtime._write_data_annotation_scheduler_tasks(
        [deepcopy(current[0])],
        runtime_update_ids={"kernel-job"},
    )

    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert {item["id"] for item in persisted} == {
        "kernel-job",
        "api-added-job",
    }


def test_concurrent_scheduler_snapshots_converge_to_membership_union(tmp_path):
    state_path = tmp_path / "scheduler_tasks.json"
    base = {"id": "base", "task_type": "base", "label": "基础作业"}
    api_job = {"id": "api-job", "task_type": "api_job", "label": "API 作业"}
    kernel_job = {
        "id": "kernel-job",
        "task_type": "kernel_job",
        "label": "Kernel 作业",
    }
    behavior_tree_control.write_data_annotation_json(state_path, [base])

    def persist(snapshot):
        behavior_tree_control.write_scheduler_tasks(
            snapshot,
            scheduler_state_path=state_path,
            preserve_runtime_state=False,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(persist, [deepcopy(base), deepcopy(api_job)]),
            executor.submit(persist, [deepcopy(base), deepcopy(kernel_job)]),
        ]
        for future in futures:
            future.result()

    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert {item["id"] for item in persisted} == {
        "base",
        "api-job",
        "kernel-job",
    }


def test_requested_standard_jobs_are_manual_without_bootstrap_next_time():
    manual_ids = {
        "login-game",
        "lilian-claim",
        "lilian-event",
        "legacy-daily-youli",
        "legacy-daily-shuangxiu",
        "legacy-daily-dungeon",
    }

    tasks = behavior_tree_control.default_data_annotation_scheduler_tasks(
        now=real_datetime(2026, 8, 2, 12, 0, 0),
    )
    selected = {task["id"]: task for task in tasks if task["id"] in manual_ids}

    assert set(selected) == manual_ids
    assert all(task["trigger_description"] == "手动" for task in selected.values())
    assert all(task["next_time"] is None for task in selected.values())
    assert selected["login-game"]["dispatch_level"] == 5


def test_schedule_login_job_first_precedes_oldest_materialized_trigger_and_is_idempotent(tmp_path):
    state_path = tmp_path / "scheduler_tasks.json"
    behavior_tree_control.write_data_annotation_json(
        state_path,
        [
            {"id": "login-game", "task_type": "login_game", "next_time": None},
            {"id": "overdue", "task_type": "daily_a", "next_time": "2026-08-03 10:00:00"},
            {"id": "future", "task_type": "daily_b", "next_time": "2026-08-03 14:00:00"},
        ],
    )

    first = behavior_tree_control.schedule_login_job_first(
        scheduler_state_path=state_path,
        now=real_datetime(2026, 8, 3, 12, 0, 0),
    )
    repeated = behavior_tree_control.schedule_login_job_first(
        scheduler_state_path=state_path,
        now=real_datetime(2026, 8, 3, 12, 1, 0),
    )

    assert first == "2026-08-03 09:59:00"
    assert repeated == first


def test_schedule_login_job_first_falls_back_to_one_minute_before_now(tmp_path):
    state_path = tmp_path / "scheduler_tasks.json"
    behavior_tree_control.write_data_annotation_json(
        state_path,
        [{"id": "login-game", "task_type": "login_game", "next_time": None}],
    )

    scheduled = behavior_tree_control.schedule_login_job_first(
        scheduler_state_path=state_path,
        now=real_datetime(2026, 8, 3, 12, 0, 0),
    )

    assert scheduled == "2026-08-03 11:59:00"


def test_manual_standard_job_finishes_with_no_automatic_next_time():
    next_time_updates = []
    world_navigation = []

    class FakeRunner:
        def _fanxiu_runtime(self, _ctx, _asset_tree_path=None, *, stop_event):
            class Runtime:
                @staticmethod
                def goto_view(scene_id):
                    world_navigation.append(int(scene_id))
                    if False:
                        yield None
                    return scene_id
            return Runtime()

        def _execute_daily_youli_task(self, _ctx, _stop_event, _payload):
            yield "running"
            return "success"

        def _persist_scheduler_task_next_time(self, task_id, next_time):
            next_time_updates.append((task_id, next_time))

    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = next(
        item
        for item in list_fanxiu_data_annotation_task_cell_definitions()
        if item.task_type == "daily_youli"
    )
    operation = definition.handler(FakeRunner(), {}, {}, threading.Event())
    assert next(operation) == "running"
    try:
        next(operation)
    except StopIteration as exc:
        assert exc.value == "success"
    else:
        raise AssertionError("manual standard Job should finish")

    assert next_time_updates == [("legacy-daily-youli", None)]
    assert world_navigation == [34, 34]


def test_read_doctor_watch_latest_prefers_heartbeat_latest_path_when_stale(monkeypatch, tmp_path):
    watch_dir = tmp_path / "fanxiu-watch"
    watch_dir.mkdir()
    stable_path = watch_dir / "doctor_watch_latest.json"
    latest_path = watch_dir / "doctor_watch_20260703_113441.latest.json"
    heartbeat_path = watch_dir / "doctor_watch_heartbeat.json"

    stable_path.write_text('{"summary":"stable"}', encoding="utf-8")
    latest_path.write_text('{"summary":"latest"}', encoding="utf-8")
    heartbeat_path.write_text(
        json.dumps({
            "updated_at": time.time() - 3600,
            "latest_path": latest_path.as_posix(),
            "stable_latest_path": stable_path.as_posix(),
        }),
        encoding="utf-8",
    )

    monkeypatch.setattr(behavior_tree_control, "doctor_watch_latest_path", lambda: stable_path)
    monkeypatch.setattr(behavior_tree_control, "doctor_watch_heartbeat_path", lambda: heartbeat_path)
    monkeypatch.setattr(
        behavior_tree_control,
        "_doctor_watch_latest_candidates",
        lambda: (_ for _ in ()).throw(AssertionError("should not scan fallback candidates")),
    )

    payload = behavior_tree_control.read_doctor_watch_latest()

    assert payload["exists"] is True
    assert payload["path"] == str(latest_path)
    assert payload["snapshot"]["summary"] == "latest"
    assert payload["heartbeat"]["active"] is False


def test_ensure_doctor_watch_background_uses_repo_root_script(monkeypatch, tmp_path):
    calls = []

    class TempRoot:
        def __call__(self, *parts):
            return tmp_path.joinpath(*parts)

    class FakeProcess:
        pid = 12345

    def fake_popen(script_path, *args, **kwargs):
        calls.append({"script_path": script_path, "args": args, "kwargs": kwargs})
        return FakeProcess()

    monkeypatch.setattr(behavior_tree_control, "codeyun_temp_root", TempRoot())
    monkeypatch.setattr(behavior_tree_control, "read_doctor_watch_heartbeat", lambda **_kwargs: {"active": False})
    monkeypatch.setattr(behavior_tree_control, "read_doctor_watch_latest", lambda: {})
    monkeypatch.setattr(behavior_tree_control, "popen_python_script_service", fake_popen)

    result = behavior_tree_control.ensure_doctor_watch_background(interval_seconds=30, include_screenshot=False)

    assert result["started"] is True
    assert calls
    script_path = calls[0]["script_path"]
    assert script_path.name == "fanxiu_bt.py"
    assert script_path.parent.name == "scripts"
    assert script_path.is_file()
    assert "backend/core/scripts" not in script_path.as_posix()
    assert calls[0]["kwargs"]["cwd"] == str(script_path.parents[1])
    assert "--auto-run-due" not in calls[0]["args"]


def test_ensure_doctor_watch_background_replaces_live_stale_code(monkeypatch, tmp_path):
    calls = []

    class TempRoot:
        def __call__(self, *parts):
            return tmp_path.joinpath(*parts)

    class ExistingProcess:
        def __init__(self, pid):
            self.pid = pid

        def cmdline(self):
            return ["pythonw.exe", "scripts/fanxiu_bt.py", "watch-doctor"]

        def is_running(self):
            return True

        def terminate(self):
            calls.append(("terminate", self.pid))

        def wait(self, timeout):
            calls.append(("wait", timeout))

    class NewProcess:
        pid = 456

    monkeypatch.setattr(behavior_tree_control, "codeyun_temp_root", TempRoot())
    monkeypatch.setattr(
        behavior_tree_control,
        "read_doctor_watch_heartbeat",
        lambda **_kwargs: {
            "active": True,
            "pid": 123,
            "code_consistent": False,
        },
    )
    monkeypatch.setattr(behavior_tree_control.psutil, "Process", ExistingProcess)
    monkeypatch.setattr(behavior_tree_control, "popen_python_script_service", lambda *_args, **_kwargs: NewProcess())

    result = behavior_tree_control.ensure_doctor_watch_background(include_screenshot=False)

    assert result["started"] is True
    assert result["replaced_pid"] == 123
    assert result["reason"] == "code_signature_mismatch"
    assert result["replacement_reasons"] == ["code_signature_mismatch"]
    assert ("terminate", 123) in calls


def test_ensure_doctor_watch_background_replaces_live_stale_heartbeat(monkeypatch, tmp_path):
    class TempRoot:
        def __call__(self, *parts):
            return tmp_path.joinpath(*parts)

    terminated = []

    class ExistingProcess:
        def __init__(self, pid):
            self.pid = pid

        def cmdline(self):
            return ["pythonw.exe", "scripts/fanxiu_bt.py", "watch-doctor"]

        def is_running(self):
            return True

        def terminate(self):
            terminated.append(self.pid)

        def wait(self, timeout):
            return None

    class NewProcess:
        pid = 789

    monkeypatch.setattr(behavior_tree_control, "codeyun_temp_root", TempRoot())
    monkeypatch.setattr(
        behavior_tree_control,
        "read_doctor_watch_heartbeat",
        lambda **_kwargs: {
            "active": False,
            "pid": 321,
            "code_consistent": True,
        },
    )
    monkeypatch.setattr(behavior_tree_control.psutil, "Process", ExistingProcess)
    monkeypatch.setattr(behavior_tree_control, "popen_python_script_service", lambda *_args, **_kwargs: NewProcess())

    result = behavior_tree_control.ensure_doctor_watch_background(include_screenshot=False)

    assert result["started"] is True
    assert result["replaced_pid"] == 321
    assert result["reason"] == "heartbeat_missing_or_stale"
    assert terminated == [321]


def test_scheduler_dispatch_order_prefers_higher_level_and_keeps_same_level_order():
    tasks = [
        {"id": "level-1-a", "dispatch_level": 1},
        {"id": "level-4", "dispatch_level": 4},
        {"id": "level-1-b", "dispatch_level": 1},
        {"id": "level-0"},
    ]

    ordered = behavior_tree_control.sort_scheduler_tasks_for_dispatch(tasks)

    assert [item["id"] for item in ordered] == ["level-4", "level-1-a", "level-1-b", "level-0"]


def test_scheduler_soft_order_applies_inside_same_level_and_trigger_cohort():
    tasks = [
        {"id": "mojie", "dispatch_level": 0, "dispatch_order": 30, "next_time": "2026-07-20 21:30:00"},
        {"id": "dongtian", "dispatch_level": 0, "dispatch_order": 20, "next_time": "2026-07-20 21:30:00"},
        {"id": "lingmai", "dispatch_level": 0, "dispatch_order": 10, "next_time": "2026-07-20 21:30:00"},
    ]

    ordered = behavior_tree_control.sort_scheduler_tasks_for_dispatch(tasks)

    assert [item["id"] for item in ordered] == ["lingmai", "dongtian", "mojie"]


def test_scheduler_repair_discards_old_trigger_types_without_losing_next_time():
    from backend.core.fanxiu.data_annotation import scheduler

    defaults = behavior_tree_control.default_data_annotation_scheduler_tasks()
    existing = [
        {
            **deepcopy(next(item for item in defaults if item["id"] == "daily-lundao-seat")),
            "schedule_kind": "daily",
            "trigger_kind": "daily",
            "schedule_times": ["15:55"],
            "window": ["15:55", "22:00"],
            "next_time": "2026-07-22 19:30:00",
        },
        {
            **deepcopy(next(item for item in defaults if item["id"] == "daily-lingmai-seat")),
            "schedule_kind": "daily",
            "trigger_kind": "daily",
            "schedule_times": ["17:30"],
            "next_time": "2026-07-22 18:00:00",
        },
    ]

    repaired, changed = scheduler.repair_data_annotation_scheduler_tasks(
        existing,
        default_tasks=defaults,
        facts={},
        task_supported=lambda _task: True,
        now=real_datetime(2026, 7, 22, 18, 0, 0),
    )

    by_id = {task["id"]: task for task in repaired}
    assert changed is True
    assert "schedule_kind" not in by_id["daily-lundao-seat"]
    assert "trigger_kind" not in by_id["daily-lundao-seat"]
    assert by_id["daily-lundao-seat"]["next_time"] == "2026-07-22 19:30:00"
    assert "schedule_kind" not in by_id["daily-lingmai-seat"]
    assert by_id["daily-lingmai-seat"]["next_time"] == "2026-07-22 18:00:00"


def test_scheduler_repair_does_not_invent_a_business_next_time():
    from backend.core.fanxiu.data_annotation import scheduler

    defaults = behavior_tree_control.default_data_annotation_scheduler_tasks()
    lingmai = deepcopy(next(item for item in defaults if item["id"] == "daily-lingmai-seat"))
    lingmai.update({
        "last_result": "success",
        "last_run_at": "2026-07-21 18:00:00",
        "next_time": None,
    })

    repaired, changed = scheduler.repair_data_annotation_scheduler_tasks(
        [lingmai],
        default_tasks=defaults,
        facts={},
        task_supported=lambda _task: True,
        now=real_datetime(2026, 7, 22, 16, 0, 0),
    )

    task = next(item for item in repaired if item["id"] == "daily-lingmai-seat")
    assert changed is True
    assert task["next_time"] is None


def test_scheduler_same_level_due_task_does_not_preempt_live_attempt(monkeypatch):
    monkeypatch.setattr(behavior_tree_control, "read_scheduler_settings", lambda **_kwargs: {"job_group_enabled": True, "time_sequence": {}})
    state = [
        {"id": "running", "task_type": "a", "dispatch_level": 3, "last_result": "running", "attempt_id": "attempt-a"},
        {"id": "due", "task_type": "b", "dispatch_level": 3},
    ]
    monkeypatch.setattr(behavior_tree_control, "read_scheduler_tasks", lambda **_kwargs: deepcopy(state))
    monkeypatch.setattr(behavior_tree_control, "task_supported", lambda _task: True)
    monkeypatch.setattr(behavior_tree_control, "data_annotation_task_due", lambda task: task["id"] == "due")

    candidate = behavior_tree_control._higher_level_due_task_for_attempt("running", "attempt-a")

    assert candidate is None


def test_scheduler_excluded_higher_level_task_does_not_preempt_live_attempt(monkeypatch):
    monkeypatch.setattr(behavior_tree_control, "read_scheduler_settings", lambda **_kwargs: {"job_group_enabled": True, "time_sequence": {}})
    state = [
        {"id": "running", "task_type": "a", "dispatch_level": 0, "last_result": "running", "attempt_id": "attempt-a"},
        {"id": "retried", "task_type": "b", "dispatch_level": 1},
    ]
    monkeypatch.setattr(behavior_tree_control, "read_scheduler_tasks", lambda **_kwargs: deepcopy(state))
    monkeypatch.setattr(behavior_tree_control, "task_supported", lambda _task: True)
    monkeypatch.setattr(behavior_tree_control, "data_annotation_task_due", lambda task: task["id"] == "retried")

    candidate = behavior_tree_control._higher_level_due_task_for_attempt(
        "running",
        "attempt-a",
        exclude_task_ids={"retried"},
    )

    assert candidate is None


def test_engineering_due_preemption_is_isolated_from_ai_manual_attempt(monkeypatch, tmp_path):
    def run_case(*, job_group_enabled: bool):
        state_lock = threading.Lock()
        state = [
            {
                "id": "manual-low",
                "label": "AI 手动作业",
                "task_type": "manual_low",
                "dispatch_level": 0,
                "last_result": "",
                "attempt_id": None,
                "next_time": "2026-08-15 07:30:00",
            },
            {
                "id": "engineering-high",
                "label": "工程高优作业",
                "task_type": "engineering_high",
                "dispatch_level": 5,
                "last_result": "",
                "attempt_id": None,
                "next_time": None,
            },
        ]
        due = threading.Event()
        cell_released = threading.Event()
        interrupted = threading.Event()
        incidents = []

        def read_tasks(**_kwargs):
            with state_lock:
                return deepcopy(state)

        def write_tasks(tasks, **_kwargs):
            with state_lock:
                state[:] = deepcopy(tasks)

        def submit_cell(**_kwargs):
            with state_lock:
                next(task for task in state if task["id"] == "manual-low")["next_time"] = "2026-08-15 09:30:00"
            assert cell_released.wait(timeout=3.0)
            if interrupted.is_set():
                return {"status": "error", "message": "Cell interrupted"}
            return {"status": "success", "message": "manual Cell completed"}

        def interrupt_kernel(command, **_kwargs):
            assert command == "interrupt"
            interrupted.set()
            cell_released.set()
            return {"ok": True}

        settings_path = tmp_path / f"settings-{job_group_enabled}.json"
        behavior_tree_control.write_scheduler_settings(
            {
                "behavior_tree_enabled": True,
                "job_group_enabled": job_group_enabled,
            },
            scheduler_settings_path=settings_path,
        )
        monkeypatch.setattr(behavior_tree_control, "read_scheduler_tasks", read_tasks)
        monkeypatch.setattr(behavior_tree_control, "write_scheduler_tasks", write_tasks)
        monkeypatch.setattr(behavior_tree_control, "record_scheduler_task_fact", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(
            behavior_tree_control,
            "record_scheduler_incident",
            lambda **kwargs: incidents.append(deepcopy(kwargs)) or kwargs,
        )
        monkeypatch.setattr(behavior_tree_control, "submit_runtime_task_cell", submit_cell)
        monkeypatch.setattr(behavior_tree_control, "task_supported", lambda _task: True)
        monkeypatch.setattr(behavior_tree_control, "data_annotation_task_due", lambda task: task["id"] == "engineering-high" and due.is_set())
        monkeypatch.setattr(
            "backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status",
            lambda: {"alive": True, "execution_state": "busy", "generation": 9},
        )
        monkeypatch.setattr(
            "backend.core.fanxiu.behavior_tree.jupyter_kernel.send_fanxiu_kernel_manager_command",
            interrupt_kernel,
        )

        due_timer = threading.Timer(0.1, due.set)
        release_timer = threading.Timer(0.9, cell_released.set)
        due_timer.start()
        release_timer.start()
        try:
            result = behavior_tree_control._run_scheduler_task_cell_and_record_terminal(
                entry=object(),
                entry_id="game-window2",
                task=deepcopy(state[0]),
                scheduler_settings_path=settings_path,
                scheduled_attempt=False,
            )
        finally:
            due_timer.cancel()
            release_timer.cancel()

        with state_lock:
            low = deepcopy(next(task for task in state if task["id"] == "manual-low"))
            high = deepcopy(next(task for task in state if task["id"] == "engineering-high"))
        return result, low, high, interrupted.is_set(), incidents

    ai_result, ai_task, ai_due_task, ai_interrupted, ai_incidents = run_case(job_group_enabled=False)
    assert ai_interrupted is False
    assert ai_result["status"] == "success"
    assert ai_task["last_result"] == "success"
    assert ai_task["next_time"] == "2026-08-15 09:30:00"
    assert ai_task["attempt_id"] is None
    assert ai_task["attempt_original_trigger"] is None
    assert ai_due_task["last_result"] == ""
    assert ai_due_task["attempt_id"] is None
    assert ai_incidents == []

    engineering_result, engineering_task, _engineering_due_task, engineering_interrupted, engineering_incidents = run_case(job_group_enabled=True)
    assert engineering_interrupted is True
    assert engineering_result["status"] == "interrupted"
    assert engineering_result["phase"] == "interrupted"
    assert engineering_task["last_result"] == "interrupted"
    assert engineering_task["next_time"] == "2026-08-15 07:30:00"
    assert "被更高级作业" in engineering_task["last_message"]
    assert engineering_task["attempt_id"] is None
    assert engineering_task["attempt_original_trigger"] is None
    assert engineering_incidents[-1]["incident"]["kind"] == "attempt_interrupted"
    assert engineering_incidents[-1]["incident"]["preemption"]["candidate_task_id"] == "engineering-high"
    assert engineering_incidents[-1]["incident"]["preemption"]["detected_at"]
    assert engineering_incidents[-1]["incident"]["preemption"]["interrupt_attempts"] == 1
    assert engineering_incidents[-1]["incident"]["preemption"]["last_error"] == ""


def test_scheduler_keeps_running_when_submit_ready_timeout_matches_live_runtime(monkeypatch):
    state = [{
        "id": "daily-lingmai-seat",
        "task_type": "daily_lingmai",
        "label": "灵脉_座位",
        "next_time": "2026-08-14 18:00:00",
        "last_result": "",
    }]
    written = []
    facts = []

    def read_tasks(**_kwargs):
        return deepcopy(state)

    def write_tasks(tasks, **_kwargs):
        state[:] = deepcopy(tasks)
        written.append(deepcopy(tasks))

    def submit_cell(**_kwargs):
        raise RuntimeError("Kernel didn't respond in 10 seconds")

    monkeypatch.setattr(behavior_tree_control, "read_scheduler_tasks", read_tasks)
    monkeypatch.setattr(behavior_tree_control, "write_scheduler_tasks", write_tasks)
    monkeypatch.setattr(behavior_tree_control, "record_scheduler_task_fact", lambda task, result, **_kwargs: facts.append((deepcopy(task), result)))
    monkeypatch.setattr(behavior_tree_control, "record_scheduler_incident", lambda **_kwargs: None)
    monkeypatch.setattr(behavior_tree_control, "submit_runtime_task_cell", submit_cell)
    monkeypatch.setattr(behavior_tree_control, "_higher_level_due_task_for_attempt", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        behavior_tree_control,
        "behavior_tree_runtime_runner_status",
        lambda: {"status": "running", "current_task_id": "daily-lingmai-seat"},
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda **_kwargs: {"alive": True, "execution_state": "busy", "generation": 7},
    )

    result = behavior_tree_control._run_scheduler_task_cell_and_record_terminal(
        entry=object(),
        entry_id="game-window2",
        task=deepcopy(state[0]),
        scheduled_attempt=True,
    )

    assert result["status"] == "running"
    assert result["phase"] == "accepted_after_submit_timeout"
    assert state[0]["last_result"] == "running"
    assert state[0]["attempt_id"]
    assert state[0]["finished_at"] is None
    assert state[0]["last_message"] == "已向 Fanxiu Kernel 提交普通 Cell"
    assert facts[-1][1] == "running"
    assert len(written) == 1


def test_scheduler_keeps_attempt_running_when_jupyter_caller_wait_times_out(monkeypatch):
    state = [{
        "id": "long-job",
        "task_type": "long_job",
        "label": "长作业",
        "next_time": "2026-08-22 12:56:37",
        "last_result": "",
        "payload": {"max_runtime_seconds": 600},
    }]
    submitted_payloads = []

    def read_tasks(**_kwargs):
        return deepcopy(state)

    def write_tasks(tasks, **_kwargs):
        state[:] = deepcopy(tasks)

    def submit_cell(**kwargs):
        submitted_payloads.append(deepcopy(kwargs["payload"]))
        raise TimeoutError("凡修 Jupyter cell 执行超时")

    monkeypatch.setattr(behavior_tree_control, "read_scheduler_tasks", read_tasks)
    monkeypatch.setattr(behavior_tree_control, "write_scheduler_tasks", write_tasks)
    monkeypatch.setattr(behavior_tree_control, "record_scheduler_task_fact", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(behavior_tree_control, "record_scheduler_incident", lambda **_kwargs: None)
    monkeypatch.setattr(behavior_tree_control, "submit_runtime_task_cell", submit_cell)
    monkeypatch.setattr(behavior_tree_control, "_higher_level_due_task_for_attempt", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        behavior_tree_control,
        "behavior_tree_runtime_runner_status",
        lambda: {
            "status": "running",
            "current_task_id": "long-job",
            "scheduler_attempt_id": state[0].get("attempt_id"),
        },
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda **_kwargs: {"alive": True, "execution_state": "busy", "generation": 7},
    )

    result = behavior_tree_control._run_scheduler_task_cell_and_record_terminal(
        entry=object(),
        entry_id="game-window2",
        task=deepcopy(state[0]),
        scheduled_attempt=True,
    )

    assert result["status"] == "running"
    assert result["phase"] == "accepted_after_submit_timeout"
    assert state[0]["last_result"] == "running"
    assert state[0]["attempt_id"]
    assert submitted_payloads[0]["__scheduler_attempt_id"] == state[0]["attempt_id"]
    assert submitted_payloads[0]["max_runtime_seconds"] == 600


def test_scheduler_reconciles_matching_runtime_success_without_overwriting_business_next_time(monkeypatch):
    class FixedDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 22, 13, 0, 30, tzinfo=tz)

    tasks = [{
        "id": "long-job",
        "last_result": "running",
        "attempt_id": "attempt-1",
        "attempt_original_trigger": "2026-08-22 12:56:37",
        "attempt_kernel_generation": 7,
        "attempt_kernel_idle_since": "2026-08-22 13:00:00",
        # The business branch wrote this before the detached caller vanished.
        "next_time": "2026-08-23 00:58:22",
    }]
    facts = []
    monkeypatch.setattr(behavior_tree_control, "datetime", FixedDateTime)
    monkeypatch.setattr(
        "backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda: {"alive": True, "execution_state": "idle", "generation": 7},
    )
    monkeypatch.setattr(
        behavior_tree_control,
        "behavior_tree_runtime_runner_status",
        lambda: {
            "running": False,
            "status": "success",
            "scheduler_task_id": "long-job",
            "scheduler_attempt_id": "attempt-1",
            "scheduler_terminal_result": "success",
            "scheduler_terminal_message": "业务已完成，进入冷却",
            "scheduler_terminal_at": real_datetime(2026, 8, 22, 12, 59, 58).timestamp(),
        },
    )
    monkeypatch.setattr(behavior_tree_control, "write_scheduler_tasks", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        behavior_tree_control,
        "record_scheduler_task_fact",
        lambda task, result, **_kwargs: facts.append((deepcopy(task), result)),
    )

    assert behavior_tree_control.reconcile_stale_scheduler_attempts(tasks) is True
    assert tasks[0]["last_result"] == "success"
    assert tasks[0]["last_message"] == "业务已完成，进入冷却"
    assert tasks[0]["next_time"] == "2026-08-23 00:58:22"
    assert tasks[0]["attempt_id"] is None
    assert facts[-1][1] == "success"


def test_scheduler_reconciles_persisted_terminal_after_runner_state_is_reloaded(monkeypatch):
    tasks = [{
        "id": "daily-redpacket",
        "last_result": "running",
        "attempt_id": "attempt-persisted",
        "attempt_original_trigger": "2026-08-27 22:19:37",
        "attempt_kernel_generation": 7,
        "next_time": "2026-08-28 11:24:59",
    }]
    monkeypatch.setattr(
        "backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda: {"alive": True, "execution_state": "idle", "generation": 7},
    )
    monkeypatch.setattr(
        behavior_tree_control,
        "behavior_tree_runtime_runner_status",
        lambda: {"running": False, "status": "idle", "updated_at": 1},
    )
    monkeypatch.setattr(
        behavior_tree_control,
        "read_behavior_tree_runtime_status",
        lambda *_args, **_kwargs: {
            "running": False,
            "status": "success",
            "message": "处理 1 个群，共打开 1 个红包",
            "updated_at": 2,
            "scheduler_task_id": "daily-redpacket",
            "scheduler_attempt_id": "attempt-persisted",
            "scheduler_terminal_result": "success",
            "scheduler_terminal_message": "处理 1 个群，共打开 1 个红包",
            "scheduler_terminal_at": real_datetime(2026, 8, 27, 23, 25, 9).timestamp(),
        },
    )
    monkeypatch.setattr(behavior_tree_control, "write_scheduler_tasks", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(behavior_tree_control, "record_scheduler_task_fact", lambda *_args, **_kwargs: None)

    assert behavior_tree_control.reconcile_stale_scheduler_attempts(tasks) is True
    assert tasks[0]["last_result"] == "success"
    assert tasks[0]["last_message"] == "处理 1 个群，共打开 1 个红包"
    assert tasks[0]["next_time"] == "2026-08-28 11:24:59"
    assert tasks[0]["attempt_id"] is None


def test_scheduler_does_not_trust_terminal_from_previous_kernel_generation(monkeypatch):
    class FixedDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 22, 13, 1, 0, tzinfo=tz)

    tasks = [{
        "id": "long-job",
        "last_result": "running",
        "attempt_id": "attempt-old-generation",
        "attempt_original_trigger": "2026-08-22 12:56:37",
        "attempt_kernel_generation": 7,
        "attempt_kernel_idle_since": "2026-08-22 12:59:00",
        "next_time": "2026-08-23 00:58:22",
    }]
    monkeypatch.setattr(behavior_tree_control, "datetime", FixedDateTime)
    monkeypatch.setattr(
        "backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda: {"alive": True, "execution_state": "idle", "generation": 8},
    )
    monkeypatch.setattr(
        behavior_tree_control,
        "behavior_tree_runtime_runner_status",
        lambda: {
            "scheduler_task_id": "long-job",
            "scheduler_attempt_id": "attempt-old-generation",
            "scheduler_terminal_result": "success",
        },
    )
    monkeypatch.setattr(behavior_tree_control, "write_scheduler_tasks", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(behavior_tree_control, "record_scheduler_task_fact", lambda *_args, **_kwargs: None)

    assert behavior_tree_control.reconcile_stale_scheduler_attempts(tasks) is True
    assert tasks[0]["last_result"] == "error"
    assert tasks[0]["next_time"] == "2026-08-22 13:11:00"


def test_scheduler_reconciles_authoritative_runtime_failure_as_error(monkeypatch):
    class FixedDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 22, 13, 10, 0, tzinfo=tz)

    tasks = [{
        "id": "long-job",
        "last_result": "running",
        "attempt_id": "attempt-timeout",
        "attempt_original_trigger": "2026-08-22 12:56:37",
        "attempt_kernel_generation": 7,
        "next_time": "2026-08-23 00:58:22",
        "error_retry_delay_seconds": 600,
    }]
    monkeypatch.setattr(behavior_tree_control, "datetime", FixedDateTime)
    monkeypatch.setattr(
        "backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda: {"alive": True, "execution_state": "idle", "generation": 7},
    )
    monkeypatch.setattr(
        behavior_tree_control,
        "behavior_tree_runtime_runner_status",
        lambda: {
            "scheduler_task_id": "long-job",
            "scheduler_attempt_id": "attempt-timeout",
            "scheduler_terminal_result": "error",
            "scheduler_terminal_message": "行为树任务超时：超过 600 秒",
            "scheduler_terminal_at": real_datetime(2026, 8, 22, 13, 9, 59).timestamp(),
        },
    )
    monkeypatch.setattr(behavior_tree_control, "write_scheduler_tasks", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(behavior_tree_control, "record_scheduler_task_fact", lambda *_args, **_kwargs: None)

    assert behavior_tree_control.reconcile_stale_scheduler_attempts(tasks) is True
    assert tasks[0]["last_result"] == "error"
    assert tasks[0]["last_message"] == "行为树任务超时：超过 600 秒"
    assert tasks[0]["next_time"] == "2026-08-22 13:20:00"
    assert tasks[0]["attempt_id"] is None


def test_scheduler_gives_idle_cell_terminal_writer_a_grace_period(monkeypatch):
    tasks = [{
        "id": "daily-a",
        "last_result": "running",
        "attempt_id": "live-attempt",
        "attempt_kernel_generation": 7,
        "started_at": "2026-07-14 16:00:00",
    }]
    written = []
    monkeypatch.setattr(
        "backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda: {"alive": True, "execution_state": "idle", "generation": 7},
    )
    monkeypatch.setattr(behavior_tree_control, "write_scheduler_tasks", lambda value, **_kwargs: written.append(deepcopy(value)))

    changed = behavior_tree_control.reconcile_stale_scheduler_attempts(tasks)

    assert changed is False
    assert tasks[0]["last_result"] == "running"
    assert tasks[0]["attempt_id"] == "live-attempt"
    assert tasks[0]["attempt_kernel_idle_since"]
    assert written


def test_scheduler_does_not_infer_success_when_cell_terminal_result_is_lost(monkeypatch):
    class FixedDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 24, 12, 1, 0, tzinfo=tz)

    tasks = [{
        "id": "daily-a",
        "last_result": "running",
        "attempt_id": "lost-attempt",
        "attempt_kernel_generation": 7,
        "attempt_kernel_idle_since": "2026-07-24 11:59:00",
        "started_at": "2026-07-24 12:00:00",
        "next_time": "2026-07-25 05:00:00",
    }]
    written = []
    facts = []
    monkeypatch.setattr(behavior_tree_control, "datetime", FixedDateTime)
    monkeypatch.setattr(
        "backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda: {"alive": True, "execution_state": "idle", "generation": 7},
    )
    monkeypatch.setattr(behavior_tree_control, "write_scheduler_tasks", lambda value, **_kwargs: written.append(deepcopy(value)))
    monkeypatch.setattr(behavior_tree_control, "record_scheduler_task_fact", lambda task, result, **_kwargs: facts.append((deepcopy(task), result)))

    changed = behavior_tree_control.reconcile_stale_scheduler_attempts(tasks)

    assert changed is True
    assert tasks[0]["last_result"] == "error"
    assert tasks[0]["attempt_id"] is None
    assert tasks[0]["finished_at"] == "2026-07-24 12:01:00"
    assert tasks[0]["next_time"] == "2026-07-24 12:11:00"
    assert "按失败策略" in tasks[0]["last_message"]
    assert written
    assert facts[-1][1] == "error"


def test_scheduler_busy_kernel_only_keeps_matching_runtime_attempt_live(monkeypatch):
    class FixedDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 11, 17, 50, 0, tzinfo=tz)

    tasks = [
        {
            "id": "stale-task",
            "last_result": "running",
            "attempt_id": "stale-attempt",
            "attempt_kernel_generation": 7,
            "attempt_kernel_idle_since": "2026-08-11 17:49:00",
            "next_time": None,
        },
        {
            "id": "live-task",
            "last_result": "running",
            "attempt_id": "live-attempt",
            "attempt_kernel_generation": 7,
        },
    ]
    monkeypatch.setattr(behavior_tree_control, "datetime", FixedDateTime)
    monkeypatch.setattr(
        "backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda: {"alive": True, "execution_state": "busy", "generation": 7},
    )
    monkeypatch.setattr(
        behavior_tree_control,
        "behavior_tree_runtime_runner_status",
        lambda: {"running": True, "current_task_id": "live-task"},
    )
    monkeypatch.setattr(behavior_tree_control, "write_scheduler_tasks", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(behavior_tree_control, "record_scheduler_task_fact", lambda *_args, **_kwargs: None)

    assert behavior_tree_control.reconcile_stale_scheduler_attempts(tasks) is True
    assert tasks[0]["last_result"] == "error"
    assert tasks[0]["attempt_id"] is None
    assert tasks[1]["last_result"] == "running"
    assert tasks[1]["attempt_id"] == "live-attempt"


def test_scheduler_migrates_prior_false_orphan_error_to_a_real_retry(monkeypatch):
    class FixedDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 24, 12, 2, 0, tzinfo=tz)

    tasks = [{
        "id": "daily-a",
        "last_result": "error",
        "last_message": "先前 Cell/Kernel 执行尝试已作废；保留原触发时间等待 Scheduler 整单重试",
        "started_at": "2026-07-24 12:00:00",
        "finished_at": "2026-07-24 12:01:00",
        "next_time": "2026-07-25 05:00:00",
    }]
    written = []
    monkeypatch.setattr(behavior_tree_control, "datetime", FixedDateTime)
    monkeypatch.setattr(
        "backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda: {"alive": True, "execution_state": "idle", "generation": 7},
    )
    monkeypatch.setattr(behavior_tree_control, "write_scheduler_tasks", lambda value, **_kwargs: written.append(deepcopy(value)))
    monkeypatch.setattr(behavior_tree_control, "record_scheduler_task_fact", lambda *_args, **_kwargs: None)

    changed = behavior_tree_control.reconcile_stale_scheduler_attempts(tasks)

    assert changed is True
    assert tasks[0]["last_result"] == "error"
    assert tasks[0]["next_time"] == "2026-07-24 12:12:00"
    assert "已按失败策略" in tasks[0]["last_message"]
    assert written


def test_prepare_scheduler_task_waits_when_kernel_busy(monkeypatch, tmp_path):
    persisted = []
    monkeypatch.setattr(
        "backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda: {"alive": True, "execution_state": "busy"},
    )
    monkeypatch.setattr(behavior_tree_control, "behavior_tree_runtime_status", lambda **_kwargs: {"status": "idle"})
    monkeypatch.setattr(behavior_tree_control, "persist_behavior_tree_runtime_status", lambda status, **_kwargs: persisted.append(deepcopy(status)))

    blocked = behavior_tree_control.prepare_runtime_for_scheduler_task(
        {"id": "daily-a"},
        [{"id": "daily-a"}],
        runtime_state_path=tmp_path / "runtime.json",
        world_facts_path=tmp_path / "facts.json",
    )

    assert blocked["phase"] == "scheduler_wait_kernel_busy"
    assert "Kernel 正在执行 Cell" in blocked["message"]
    assert persisted[-1]["phase"] == "scheduler_wait_kernel_busy"


def test_prepare_scheduler_task_recovers_stale_runtime_when_kernel_idle(monkeypatch, tmp_path):
    persisted = []
    monkeypatch.setattr(
        "backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda: {"alive": True, "execution_state": "idle"},
    )
    monkeypatch.setattr(
        behavior_tree_control,
        "behavior_tree_runtime_runner_status",
        lambda: {
            "running": True,
            "status": "running",
            "phase": "scheduler_task",
            "current_task_id": "redpacket",
        },
    )
    monkeypatch.setattr(behavior_tree_control, "persist_behavior_tree_runtime_status", lambda status, **_kwargs: persisted.append(deepcopy(status)))

    blocked = behavior_tree_control.prepare_runtime_for_scheduler_task(
        {"id": "daily-a"},
        [{"id": "redpacket", "last_result": "success", "attempt_id": None}],
        runtime_state_path=tmp_path / "runtime.json",
        world_facts_path=tmp_path / "facts.json",
    )

    assert blocked is None
    assert persisted[-1]["running"] is False
    assert persisted[-1]["phase"] == "scheduler_stale_runtime_recovered"
    assert persisted[-1]["current_task_id"] == ""


def test_prepare_scheduler_task_keeps_real_running_attempt_blocked(monkeypatch, tmp_path):
    persisted = []
    monkeypatch.setattr(
        "backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda: {"alive": True, "execution_state": "idle"},
    )
    monkeypatch.setattr(
        behavior_tree_control,
        "behavior_tree_runtime_runner_status",
        lambda: {
            "running": True,
            "status": "running",
            "phase": "scheduler_task",
            "current_task_id": "redpacket",
        },
    )
    monkeypatch.setattr(behavior_tree_control, "persist_behavior_tree_runtime_status", lambda status, **_kwargs: persisted.append(deepcopy(status)))

    blocked = behavior_tree_control.prepare_runtime_for_scheduler_task(
        {"id": "daily-a"},
        [{"id": "redpacket", "last_result": "running", "attempt_id": "attempt-redpacket"}],
        runtime_state_path=tmp_path / "runtime.json",
        world_facts_path=tmp_path / "facts.json",
    )

    assert blocked["running"] is True
    assert "当前有任务运行" in blocked["message"]
    assert persisted[-1]["running"] is True


def test_scheduler_task_normalization_preserves_terminal_message():
    from backend.core.fanxiu.data_annotation.state import normalize_data_annotation_scheduler_task

    task = normalize_data_annotation_scheduler_task({
        "id": "daily-a",
        "task_type": "daily_a",
        "last_result": "blocked",
        "last_message": "需要业务确认",
    })

    assert task["last_message"] == "需要业务确认"


def test_runtime_reload_preserves_completed_business_result(monkeypatch):
    persisted = {
        "running": False,
        "guard_enabled": True,
        "guard_running": True,
        "status": "success",
        "phase": "done",
        "message": "日常_助手执行完成",
        "logs": [],
    }
    monkeypatch.setattr(behavior_tree_control, "read_behavior_tree_runtime_status", lambda _path=None: deepcopy(persisted))
    monkeypatch.setattr(behavior_tree_control, "behavior_tree_runtime_runner_status", lambda: {
        "running": False,
        "status": "idle",
        "logs": [],
        "guard_items": {},
    })
    monkeypatch.setattr(
        behavior_tree_control,
        "persist_behavior_tree_runtime_status",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda: {"alive": True, "execution_state": "idle"},
    )

    status = behavior_tree_control.behavior_tree_runtime_status()

    assert status["guard_running"] is False
    assert status["status"] == "success"
    assert status["phase"] == "done"
    assert status["message"] == "日常_助手执行完成"


def test_runtime_reload_reports_resident_kernel_ready_instead_of_recovery_pending(monkeypatch):
    persisted = {
        "running": False,
        "guard_enabled": True,
        "guard_running": False,
        "status": "idle",
        "phase": "scheduler_failure_cleanup",
        "current_scene": 69,
        "message": "旧任务失败收尾",
        "logs": [],
    }
    monkeypatch.setattr(behavior_tree_control, "read_behavior_tree_runtime_status", lambda _path=None: deepcopy(persisted))
    monkeypatch.setattr(behavior_tree_control, "behavior_tree_runtime_runner_status", lambda: {
        "running": False,
        "status": "idle",
        "logs": [],
        "guard_items": {},
    })
    monkeypatch.setattr(
        behavior_tree_control,
        "persist_behavior_tree_runtime_status",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda: {"alive": True, "execution_state": "idle"},
    )

    status = behavior_tree_control.behavior_tree_runtime_status()

    assert status["status"] == "idle"
    assert status["phase"] == "idle"
    assert status["current_scene"] == 0
    assert status["message"] == "Kernel 已就绪，等待作业触发"
