from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from backend.core.fanxiu.data_annotation.default_jobs import register_fanxiu_data_annotation_default_runtime_jobs
from backend.core.fanxiu.data_annotation.jobs import get_fanxiu_data_annotation_task_cell_definition
from backend.core.fanxiu.data_annotation.runner import create_behavior_tree_runtime_runner
from backend.core.fanxiu.data_annotation.scheduler import repair_data_annotation_scheduler_tasks
from backend.core.fanxiu.data_annotation.scheduler_defaults import default_data_annotation_scheduler_tasks
from backend.core.fanxiu.data_annotation.tasks.weekly_hanli import WeeklyHanliTaskMixin


def _drain(generator):
    try:
        while True:
            next(generator)
    except StopIteration as exc:
        return exc.value


class _FakeRuntime:
    def __init__(self) -> None:
        self.actions: list[tuple] = []
        self.gift_ocr_round = 0

    def cur_frame(self, update=False):
        assert update is True
        return "frame"

    def click_shape_center_then_view(self, scene, shape, target, **_kwargs):
        self.actions.append(("shape", scene, shape, target))
        if False:
            yield None
        return SimpleNamespace(id=target)

    def ocr_centers_in_shape(self, scene, shape, *, include, **_kwargs):
        keyword = include[0]
        self.actions.append(("ocr", scene, shape, keyword))
        if keyword == "韩立":
            return [(458.0, 925.0, "韩立18·不离不弃")]
        if keyword == "私聊":
            return [(250.0, 992.0, "私聊传音领收到礼物")]
        if scene == 379 and shape == "礼物" and keyword == "点击领取":
            self.gift_ocr_round += 1
            if self.gift_ocr_round == 1:
                return [
                    (405.0, 612.0, "点击领取"),
                    (405.0, 742.0, "点击领取"),
                ]
            if self.gift_ocr_round == 2:
                return [(405.0, 742.0, "点击领取")]
        if scene == 379 and shape == "空状态" and keyword == "空空如也":
            if self.gift_ocr_round >= 3:
                return [(454.0, 505.0, "空空如也...")]
        return []

    def click_frame_point(self, scene, x, y):
        self.actions.append(("point", scene, x, y))

    def wait_action_settle(self, seconds):
        self.actions.append(("settle", seconds))
        if False:
            yield None

    def wait_view(self, scene, **_kwargs):
        self.actions.append(("wait", scene))
        if False:
            yield None
        return SimpleNamespace(id=scene)


class _BlankPrivateChatRuntime(_FakeRuntime):
    def ocr_centers_in_shape(self, scene, shape, *, include, **kwargs):
        if scene == 379 and shape == "空状态" and include == ("空空如也",):
            self.actions.append(("ocr", scene, shape, include[0]))
            return []
        return super().ocr_centers_in_shape(scene, shape, include=include, **kwargs)


class _Runner(WeeklyHanliTaskMixin):
    def __init__(self, runtime: _FakeRuntime) -> None:
        self.runtime = runtime
        self.logs: list[tuple[str, str]] = []
        self.next_times: list[tuple[str, str]] = []

    def _fanxiu_runtime(self, *_args, **_kwargs):
        return self.runtime

    def _log(self, kind, message):
        self.logs.append((kind, message))

    def _persist_scheduler_task_next_time(self, task_id, next_time):
        self.next_times.append((task_id, next_time))


def test_ocr_center_uses_precise_token_union_for_both_axes():
    runner = create_behavior_tree_runtime_runner()
    image = {
        "width": 900,
        "height": 1600,
        "shapes": [{"title": "窗口", "x": 0.1, "y": 0.5, "w": 0.8, "h": 0.3}],
    }
    fragments = [{"text": "私聊传音领收到礼物", "x": 225, "y": 968, "w": 582, "h": 41}]
    tokens = [
        {"text": "私", "x": 225, "y": 976, "w": 29, "h": 32},
        {"text": "聊", "x": 247, "y": 976, "w": 28, "h": 32},
        {"text": "传", "x": 279, "y": 976, "w": 28, "h": 32},
        {"text": "音", "x": 305, "y": 976, "w": 27, "h": 32},
    ]

    assert runner._ocr_centers_in_shape(
        fragments,
        image,
        "窗口",
        include=("私聊",),
        tokens=tokens,
    ) == [(250.0, 992.0, "私聊传音领收到礼物")]


def test_weekly_hanli_flow_enters_private_chat_and_returns_world():
    runtime = _FakeRuntime()
    runner = _Runner(runtime)

    result = _drain(runner._execute_weekly_hanli_task(
        {"asset_tree_path": Path("asset-tree.json")},
        object(),
        {},
    ))

    assert result["result"] == "success"
    assert result["current_scene"] == 34
    assert runtime.actions == [
        ("shape", 34, "聊天", 332),
        ("shape", 332, "通讯录", 333),
        ("shape", 333, "仙缘", 334),
        ("ocr", 334, "窗口", "韩立"),
        ("point", 334, 458.0, 925.0),
        ("settle", 0.8),
        ("ocr", 334, "窗口", "私聊"),
        ("point", 334, 250.0, 992.0),
        ("settle", 0.8),
        ("wait", 379),
        ("ocr", 379, "礼物", "点击领取"),
        ("point", 379, 405.0, 612.0),
        ("settle", 5.0),
        ("wait", 379),
        ("ocr", 379, "礼物", "点击领取"),
        ("point", 379, 405.0, 742.0),
        ("settle", 5.0),
        ("wait", 379),
        ("ocr", 379, "礼物", "点击领取"),
        ("ocr", 379, "空状态", "空空如也"),
        ("shape", 379, "返回", 334),
        ("shape", 334, "返回", 34),
    ]
    assert result["gift_count"] == 2
    assert "next_time" not in result
    assert runner.next_times[0][0] == "weekly-hanli"
    assert runner.next_times[0][1].endswith("05:00:00")


def test_weekly_hanli_empty_reward_list_is_a_valid_idempotent_completion():
    runtime = _FakeRuntime()
    runtime.gift_ocr_round = 2
    runner = _Runner(runtime)

    result = _drain(runner._execute_weekly_hanli_task(
        {"asset_tree_path": Path("asset-tree.json")},
        object(),
        {},
    ))

    assert result["result"] == "success"
    assert result["gift_count"] == 0
    assert ("ocr", 379, "空状态", "空空如也") in runtime.actions
    assert not any(action[0] == "point" and action[1] == 379 for action in runtime.actions)
    assert any("幂等完成态" in message for _kind, message in runner.logs)


def test_weekly_hanli_stable_blank_private_chat_is_a_valid_completion():
    runtime = _BlankPrivateChatRuntime()
    runtime.gift_ocr_round = 2
    runner = _Runner(runtime)

    result = _drain(runner._execute_weekly_hanli_task(
        {"asset_tree_path": Path("asset-tree.json")},
        object(),
        {},
    ))

    assert result["result"] == "success"
    assert result["gift_count"] == 0
    assert runtime.actions.count(("ocr", 379, "礼物", "点击领取")) == 3
    assert ("wait", 379) in runtime.actions
    assert any("稳定空白列表" in message for _kind, message in runner.logs)


def test_weekly_hanli_success_advances_to_next_monday_five():
    runner = _Runner(_FakeRuntime())

    result = runner._record_weekly_hanli_done(
        {"__scheduler_task_id": "weekly-instance"},
        now=datetime(2026, 8, 3, 5, 30),
    )

    assert result == "2026-08-10 05:00:00"
    assert runner.next_times == [("weekly-instance", "2026-08-10 05:00:00")]


def test_weekly_hanli_is_registered_as_scheduler_supported_type():
    register_fanxiu_data_annotation_default_runtime_jobs()

    definition = get_fanxiu_data_annotation_task_cell_definition("weekly_hanli")

    assert definition is not None
    assert definition.label == "周常_韩立"
    assert definition.scheduler_supported is True
    assert not hasattr(definition, "lifecycle")


def test_weekly_hanli_default_scheduler_template_runs_every_monday_at_five():
    task = next(item for item in default_data_annotation_scheduler_tasks() if item["id"] == "weekly-hanli")

    assert task["task_type"] == "weekly_hanli"
    assert task["label"] == "周常_韩立"
    assert task["trigger_description"] == "每周"
    assert task["next_time"]


def test_weekly_hanli_discards_old_trigger_fields_and_preserves_business_time():
    defaults = default_data_annotation_scheduler_tasks()
    old_task = {
        "id": "weekly-hanli",
        "task_type": "weekly_hanli",
        "label": "周常_韩立",
        "source": "data_annotation_runtime",
        "schedule_kind": "weekly",
        "enabled": False,
        "next_time": "2026-07-27 05:00:00",
        "schedule_times": ["05:00"],
        "weekdays": [0],
        "payload": {"__scheduler_definition_task_type": "weekly_hanli"},
    }

    repaired, changed = repair_data_annotation_scheduler_tasks(
        [old_task],
        default_tasks=defaults,
        facts={},
        task_supported=lambda _task: True,
        now=datetime(2026, 7, 22, 14, 0, 0),
    )

    task = next(item for item in repaired if item["id"] == "weekly-hanli")
    assert changed is True
    assert "enabled" not in task
    assert task["next_time"] == "2026-07-27 05:00:00"
    assert "schedule_kind" not in task
    assert "schedule_times" not in task
    assert "weekdays" not in task
    assert task["trigger_description"] == "每周"

    task["next_time"] = None
    repaired_again, _changed = repair_data_annotation_scheduler_tasks(
        [task],
        default_tasks=defaults,
        facts={},
        task_supported=lambda _task: True,
        now=datetime(2026, 7, 22, 14, 1, 0),
    )
    assert next(item for item in repaired_again if item["id"] == "weekly-hanli")["next_time"] is None
