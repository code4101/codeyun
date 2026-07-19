from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from backend.core.fanxiu.data_annotation.default_jobs import register_fanxiu_data_annotation_default_runtime_jobs
from backend.core.fanxiu.data_annotation.jobs import get_fanxiu_data_annotation_task_cell_definition
from backend.core.fanxiu.data_annotation.runner import create_fanxiu_runtime_runner
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


class _Runner(WeeklyHanliTaskMixin):
    def __init__(self, runtime: _FakeRuntime) -> None:
        self.runtime = runtime
        self.logs: list[tuple[str, str]] = []

    def _fanxiu_runtime(self, *_args, **_kwargs):
        return self.runtime

    def _log(self, kind, message):
        self.logs.append((kind, message))


def test_ocr_center_uses_precise_token_union_for_both_axes():
    runner = create_fanxiu_runtime_runner()
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
        ("shape", 379, "返回", 334),
        ("shape", 334, "返回", 34),
    ]


def test_weekly_hanli_is_registered_as_scheduler_supported_type():
    register_fanxiu_data_annotation_default_runtime_jobs()

    definition = get_fanxiu_data_annotation_task_cell_definition("weekly_hanli")

    assert definition is not None
    assert definition.label == "周常_韩立"
    assert definition.scheduler_supported is True
    assert definition.stable_start_scene_id == 34
