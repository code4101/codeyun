from __future__ import annotations

from backend.core.fanxiu.data_annotation.default_jobs import (
    register_fanxiu_data_annotation_default_runtime_jobs,
)
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
)
from backend.core.fanxiu.data_annotation.scheduler_defaults import (
    default_data_annotation_scheduler_tasks,
)
from backend.core.fanxiu.data_annotation.tasks.xutian_palace_rankings import (
    _wait_one_of,
)


def test_xutian_rankings_is_a_manual_standard_job() -> None:
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition(
        "xutian_palace_rankings"
    )
    assert definition is not None
    assert definition.scheduler_supported is True
    assert definition.standard_job is True

    task = next(
        item
        for item in default_data_annotation_scheduler_tasks()
        if item["id"] == "xutian-palace-rankings"
    )
    assert task["trigger_description"] == "手动"
    assert task["next_time"] is None


def test_wait_one_of_does_not_treat_unknown_frame_as_success() -> None:
    class Runtime:
        observations = iter(
            [
                (None, 0.0, "black"),
                (34, 100.0, "world"),
            ]
        )

        def current_scene(self, targets, *, update):
            assert targets == [34, 66]
            assert update is True
            return next(self.observations)

        @staticmethod
        def ocr_text(frame):
            return frame

    assert _wait_one_of(Runtime(), (34, 66), timeout_seconds=1.0) == (
        34,
        100.0,
        "world",
    )
