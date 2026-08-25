from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from backend.core.fanxiu.data_annotation.default_jobs import (
    register_fanxiu_data_annotation_default_runtime_jobs,
)
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
)
from backend.core.fanxiu.data_annotation.behavior_tree_control import read_scheduler_tasks
from backend.core.fanxiu.data_annotation.scheduler_defaults import (
    default_data_annotation_scheduler_tasks,
)
from backend.core.fanxiu.data_annotation.tasks.lilian_claim import (
    execute_lilian_claim_task,
)


def _drain(generator):
    while True:
        try:
            next(generator)
        except StopIteration as exc:
            return exc.value


class _Runtime:
    def __init__(self):
        self.scene_id = 34
        self.actions = []

    def wait_click_then_view(self, scene, shape, *targets, **options):
        self.actions.append(("wait_click_then_view", scene, shape, targets, options))
        if False:
            yield None
        self.scene_id = targets[0]
        return SimpleNamespace(id=self.scene_id)

    def wait_click(self, scene, shape, **options):
        self.actions.append(("wait_click", scene, shape, options))
        if False:
            yield None

    def wait_action_settle(self, seconds):
        self.actions.append(("wait_action_settle", seconds))
        if False:
            yield None

    def wait_view(self, *views, **options):
        self.actions.append(("wait_view", views, options))
        if False:
            yield None
        self.scene_id = views[0]
        return SimpleNamespace(id=self.scene_id)

    def goto_view(self, scene):
        self.actions.append(("goto_view", scene))
        if False:
            yield None
        self.scene_id = scene
        return "success"

    def current_scene(self, views, update=False):
        self.actions.append(("current_scene", tuple(views), update))
        return self.scene_id, 100.0, "frame"


class _Runner:
    def __init__(self, runtime):
        self.runtime = runtime

    def _fanxiu_runtime(self, *_args, **_kwargs):
        return self.runtime


def test_lilian_claim_runs_simple_branch_and_returns_world():
    runtime = _Runtime()

    result = _drain(
        execute_lilian_claim_task(
            _Runner(runtime),
            {"asset_tree_path": Path("asset-tree.json")},
            {},
            threading.Event(),
        )
    )

    assert result == {
        "result": "success",
        "message": "历练_领取：已点击一次一键收取并返回 #34",
        "current_scene": 34,
    }
    assert [action[:3] for action in runtime.actions if action[0] == "wait_click"] == [
        ("wait_click", 427, "确认"),
        ("wait_click", 427, "资源"),
        ("wait_click", 441, "一键收取"),
    ]
    assert [action for action in runtime.actions if action[0] == "goto_view"] == [
        ("goto_view", 34)
    ]


def test_lilian_claim_is_manual_standard_job_in_default_checklist(tmp_path):
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("lilian_claim")

    assert definition is not None
    assert definition.label == "历练_领取"
    assert definition.scheduler_supported is True
    assert definition.standard_job is True
    assert definition.standard_job_id == "lilian-claim"
    assert definition.standard_job_description == "手动"
    assert not hasattr(definition, "lifecycle")
    default_task = next(
        task
        for task in default_data_annotation_scheduler_tasks(datetime(2026, 8, 1, 14, 0))
        if task["task_type"] == "lilian_claim"
    )
    assert default_task["id"] == "lilian-claim"
    assert default_task["next_time"] is None
    assert default_task["trigger_description"] == "手动"

    scheduler_path = tmp_path / "scheduler.json"
    world_facts_path = tmp_path / "world-facts.json"
    now = datetime(2026, 8, 1, 14, 0)
    tasks = read_scheduler_tasks(
        scheduler_state_path=scheduler_path,
        world_facts_path=world_facts_path,
        now=now,
    )
    task = next(item for item in tasks if item["task_type"] == "lilian_claim")
    assert task["id"] == "lilian-claim"
    assert task["next_time"] is None
    assert task["trigger_description"] == "手动"
    assert task["template_source"] == "preset"
