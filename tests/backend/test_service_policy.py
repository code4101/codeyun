from types import SimpleNamespace

from backend.core.services.policy import (
    command_service_group,
    is_legacy_codeyun_service,
    resolve_service_policy,
)


def _service(**kwargs):
    defaults = {
        "id": "service-1",
        "name": "demo",
        "command": "server.exe",
        "schedule": None,
        "schedule_policy": None,
        "timeout": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_service_policy_is_per_service_and_never_queued():
    policy = resolve_service_policy(_service(name="capture", schedule="0 * * * *"))

    assert policy.concurrency_key == "service:service-1"
    assert policy.overlap_policy == "replace"
    assert policy.schedule_kind == "cron"
    assert policy.queue_key is None


def test_service_schedule_policy_uses_declared_trigger():
    policy = resolve_service_policy(
        _service(schedule_policy={"trigger": {"type": "monthly", "day": 27, "time": "00:00"}})
    )

    assert policy.schedule_kind == "monthly"


def test_legacy_codeyun_process_row_is_not_a_managed_service():
    assert is_legacy_codeyun_service(_service(name="codeyun", command="uv run dev.py"))
    assert not is_legacy_codeyun_service(_service(name="capture", command="uv run dev.py"))


def test_service_grouping_has_no_job_inference():
    assert command_service_group(_service(name="nginx")) == ("service:network", "网络服务")
    assert command_service_group(_service(name="demo")) == ("service:default", "默认服务组")

