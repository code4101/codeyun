from types import SimpleNamespace

from backend.core.runtime.units import (
    DEFAULT_JOB_CONCURRENCY_KEY,
    DEFAULT_JOB_RESOURCE_LOCK,
    command_runtime_queue_name,
    command_runtime_group,
    command_runtime_resource_lock,
    infer_command_runtime_kind,
    is_legacy_codeyun_command_task,
    resolve_command_runtime_policy,
)


def _task(**kwargs):
    defaults = {
        "id": "task-1",
        "name": "demo",
        "command": "python job.py",
        "description": "",
        "runtime_kind": None,
        "schedule": None,
        "schedule_policy": None,
        "timeout": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_scheduled_known_service_keeps_service_policy():
    task = _task(name="capture", schedule="0 * * * *")

    policy = resolve_command_runtime_policy(task)

    assert infer_command_runtime_kind(task) == "service"
    assert policy.kind == "service"
    assert policy.concurrency_scope == "unit"
    assert policy.overlap_policy == "replace"
    assert policy.schedule_kind == "cron"
    assert policy.queue_key is None


def test_command_job_policy_uses_default_queue_group():
    task = _task(name="weekly", timeout=120)

    policy = resolve_command_runtime_policy(task)

    assert policy.kind == "job"
    assert policy.concurrency_scope == "group"
    assert policy.concurrency_key == DEFAULT_JOB_CONCURRENCY_KEY
    assert policy.overlap_policy == "queue"
    assert policy.timeout_policy == "terminate"
    assert policy.timeout_seconds == 120
    assert policy.queue_key == DEFAULT_JOB_CONCURRENCY_KEY
    assert policy.resource_lock == DEFAULT_JOB_RESOURCE_LOCK
    assert command_runtime_queue_name(task.id) == "command:task-1"


def test_explicit_runtime_kind_keeps_manual_command_as_job():
    task = _task(name="manual-report", runtime_kind="job", schedule=None)

    policy = resolve_command_runtime_policy(task)

    assert infer_command_runtime_kind(task) == "job"
    assert policy.kind == "job"
    assert policy.schedule_kind == "manual"


def test_schedule_policy_trigger_kind_replaces_legacy_cron_kind():
    task = _task(
        name="capture",
        schedule=None,
        schedule_policy={
            "enabled": True,
            "trigger": {"type": "monthly", "day": 27, "time": "00:00"},
        },
    )

    policy = resolve_command_runtime_policy(task)

    assert policy.kind == "service"
    assert policy.schedule_kind == "monthly"


def test_command_job_policy_marks_gui_resource_lock():
    task = _task(
        name="凡修画面检测",
        runtime_kind="job",
        command="uv run python scripts/fanxiu_bt.py watch-doctor --screenshot",
    )

    policy = resolve_command_runtime_policy(task)

    assert policy.kind == "job"
    assert policy.resource_lock == "resource:gui-automation"
    assert command_runtime_resource_lock(task, "job") == "resource:gui-automation"


def test_legacy_codeyun_command_task_is_hidden_from_runtime_list():
    assert is_legacy_codeyun_command_task(_task(name="codeyun", command="uv run dev.py"))
    assert is_legacy_codeyun_command_task(
        _task(name="codeyun", command=r"D:\home\chenkunze\slns\codeyun\.venv\Scripts\python.exe dev.py")
    )
    assert not is_legacy_codeyun_command_task(_task(name="capture", command="uv run dev.py"))
    assert not is_legacy_codeyun_command_task(_task(name="codeyun", command="uv run python scripts/job.py"))


def test_codeyun_name_is_not_a_normal_base_service_group():
    task = _task(name="codeyun", command="uv run python scripts/job.py")

    assert command_runtime_group(task, "service") == ("service:default", "默认服务组")
