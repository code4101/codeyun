"""Child settings cannot acquire or overwrite the parent Scheduler attempt."""
from backend.core.fanxiu.data_annotation.tasks.resource_daily_execution import (
    internal_component_payload,
)


def test_internal_component_payload_preserves_settings_and_removes_scheduler_identity():
    settings = {"schedule": True, "limit": 3,
                "__scheduler_task_id": "parent", "__scheduler_attempt_id": "live",
                "__scheduler_future_metadata": "also-private"}
    child = internal_component_payload(settings)
    assert child == {"schedule": False, "limit": 3}
    assert settings["schedule"] is True
    assert settings["__scheduler_attempt_id"] == "live"
