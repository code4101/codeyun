from __future__ import annotations

import threading
from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from backend.core.fanxiu.data_annotation.default_jobs import (
    register_fanxiu_data_annotation_default_runtime_jobs,
)
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
)
from backend.core.fanxiu.data_annotation.scheduler_defaults import (
    default_data_annotation_scheduler_tasks,
)
from backend.core.fanxiu.data_annotation.tasks import lingzhuang_strengthening as task_module


def _drain(generator: Generator[Any, None, Any]) -> Any:
    while True:
        try:
            next(generator)
        except StopIteration as exc:
            return exc.value


class _Runner:
    def __init__(self) -> None:
        self.world_navigation: list[int] = []
        runner = self

        class Runtime:
            @staticmethod
            def current_scene(_scene_ids, *, update):
                assert update is True
                return None, 0.0, ""

            @staticmethod
            def goto_view(scene_id):
                runner.world_navigation.append(int(scene_id))
                if False:
                    yield None
                return scene_id

        self.runtime = Runtime()
        self.logs: list[tuple[str, str]] = []
        self.next_times: list[tuple[str, str | None]] = []

    def _fanxiu_runtime(self, _ctx, _asset_tree_path, *, stop_event):
        assert isinstance(stop_event, threading.Event)
        return self.runtime

    def _log(self, kind: str, message: str) -> None:
        self.logs.append((kind, message))

    def _persist_scheduler_task_next_time(self, task_id: str, next_time: str | None) -> None:
        self.next_times.append((task_id, next_time))


def test_lingzhuang_strengthening_is_manual_standard_job() -> None:
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition(
        "lingzhuang_strengthening"
    )

    assert definition is not None
    assert definition.label == "灵装化道_强化"
    assert definition.scheduler_supported is True
    assert definition.standard_job is True
    assert definition.standard_job_id == "lingzhuang-strengthening"
    assert definition.standard_job_description == "手动"
    assert definition.standard_job_payload == {
        "target_tier": 10,
        "max_clicks": 200,
        "max_runtime_seconds": 7200,
    }
    assert not hasattr(definition, "lifecycle")

    task = next(
        item
        for item in default_data_annotation_scheduler_tasks()
        if item["task_type"] == "lingzhuang_strengthening"
    )
    assert task["id"] == "lingzhuang-strengthening"
    assert task["next_time"] is None
    assert task["payload"] == definition.standard_job_payload


def test_lingzhuang_strengthening_uses_active_instance_and_default_tier(
    monkeypatch,
) -> None:
    runner = _Runner()
    captured: dict[str, Any] = {}
    activity = SimpleNamespace(id="activity-new", cross_count=16)
    monkeypatch.setattr(
        task_module,
        "resolve_lingzhuang_strengthening_activity",
        lambda _session, **_options: activity,
    )

    def fake_complete(runtime, **options):
        captured.update({"runtime": runtime, **options})
        if False:
            yield None
        return {
            "ok": True,
            "target_tier": 10,
            "target_progress": 4000,
            "equipment_progress": 4000,
            "click_count": 3,
        }

    monkeypatch.setattr(
        task_module,
        "complete_equipment_strengthening_tasks",
        fake_complete,
    )

    result = _drain(
        task_module.execute_lingzhuang_strengthening_task(
            runner,
            {"asset_tree_path": Path("asset-tree.json")},
            {},
            threading.Event(),
        )
    )

    assert result["outcome"] == "target_reached"
    assert result["activity_id"] == "activity-new"
    assert captured == {
        "runtime": runner.runtime,
        "activity_id": "activity-new",
        "target_progress": None,
        "target_tier": 10,
        "cross_count": 16,
        "game_task_activity_id": None,
        "max_clicks": 200,
    }


def test_lingzhuang_strengthening_uses_runtime_activity_id_for_local_preliminary(
    monkeypatch,
) -> None:
    runner = _Runner()
    captured: dict[str, Any] = {}
    activity = SimpleNamespace(
        id="lingzhuang-local",
        cross_count=1,
        evidence={"game_activity_id": 1044311},
    )
    monkeypatch.setattr(
        task_module,
        "resolve_lingzhuang_strengthening_activity",
        lambda _session, **_options: activity,
    )

    def fake_complete(runtime, **options):
        captured.update({"runtime": runtime, **options})
        if False:
            yield None
        return {
            "ok": True,
            "target_tier": 10,
            "target_progress": 4000,
            "equipment_progress": 4000,
            "click_count": 1,
        }

    monkeypatch.setattr(
        task_module,
        "complete_equipment_strengthening_tasks",
        fake_complete,
    )

    result = _drain(
        task_module.execute_lingzhuang_strengthening_task(
            runner,
            {"asset_tree_path": Path("asset-tree.json")},
            {},
            threading.Event(),
        )
    )

    assert result["outcome"] == "target_reached"
    assert captured["cross_count"] == 1
    assert captured["game_task_activity_id"] == 1044311


def test_lingzhuang_strengthening_resource_exhaustion_is_normal_stop(
    monkeypatch,
) -> None:
    runner = _Runner()
    activity = SimpleNamespace(id="activity-new", cross_count=16)
    monkeypatch.setattr(
        task_module,
        "resolve_lingzhuang_strengthening_activity",
        lambda _session, **_options: activity,
    )

    def fake_complete(_runtime, **_options):
        if False:
            yield None
        raise task_module.EquipmentStrengtheningResourceExhausted(
            "路线可用玄铁耗尽，装备任务仅到 3000 / 4000",
            target_progress=4000,
            equipment_progress=3000,
            cumulative_material=3000,
        )

    monkeypatch.setattr(
        task_module,
        "complete_equipment_strengthening_tasks",
        fake_complete,
    )

    result = _drain(
        task_module.execute_lingzhuang_strengthening_task(
            runner,
            {"asset_tree_path": Path("asset-tree.json")},
            {"target_tier": 10},
            threading.Event(),
        )
    )

    assert result["ok"] is False
    assert result["outcome"] == "insufficient_resource"
    assert result["target_progress"] == 4000
    assert result["equipment_progress"] == 3000
    assert runner.logs[-1][0] == "skip"


def test_registered_manual_job_clears_next_time_after_normal_return(monkeypatch) -> None:
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition(
        "lingzhuang_strengthening"
    )
    assert definition is not None
    runner = _Runner()

    def fake_execute(_runner, _ctx, _payload, _stop_event):
        if False:
            yield None
        return {"ok": False, "outcome": "insufficient_resource"}

    monkeypatch.setattr(
        task_module,
        "execute_lingzhuang_strengthening_task",
        fake_execute,
    )
    result = _drain(
        definition.handler(
            runner,
            {"asset_tree_path": Path("asset-tree.json")},
            dict(definition.standard_job_payload),
            threading.Event(),
        )
    )

    assert result["outcome"] == "insufficient_resource"
    assert runner.next_times == [("lingzhuang-strengthening", None)]
    assert runner.world_navigation == [34, 34]
