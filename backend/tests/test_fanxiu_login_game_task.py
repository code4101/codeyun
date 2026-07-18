from __future__ import annotations

import threading

import pytest

from backend.core.fanxiu.data_annotation.default_jobs import (
    register_fanxiu_data_annotation_default_runtime_jobs,
)
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
)
from backend.core.fanxiu.data_annotation.tasks.login_game import LoginGameTaskMixin


def _drain(generator):
    try:
        while True:
            next(generator)
    except StopIteration as exc:
        return exc.value


class _FakeRuntime:
    def __init__(self, scenes, *, ocr_text=""):
        self.scenes = list(scenes)
        self._ocr_text = ocr_text
        self.actions: list[tuple] = []
        self.completion_message = ""

    def current_scene(self, _scene_ids, *, update=False):
        assert update is True
        scene_id = self.scenes.pop(0)
        return scene_id, 100.0 if scene_id is not None else 0.0, f"frame-{scene_id}"

    def wait_click_then_view(self, scene_id, shape, targets, **_options):
        self.actions.append(("click", scene_id, shape, targets))
        if False:
            yield None
        return targets

    def wait_action_settle(self, seconds):
        self.actions.append(("settle", seconds))
        if False:
            yield None

    def goto_view(self, scene_id):
        self.actions.append(("goto", scene_id))
        if False:
            yield None
        return "success"

    def ocr_text(self, _frame):
        return self._ocr_text

    def click_shape_center(self, scene_id, shape):
        self.actions.append(("click_center", scene_id, shape))

    def set_completion_message(self, message):
        self.completion_message = message


class _FakeRunner(LoginGameTaskMixin):
    scene_threshold = 80

    def __init__(self, scenes, *, ocr_text=""):
        self._lock = threading.Lock()
        self.runtime = _FakeRuntime(scenes, ocr_text=ocr_text)
        self.logs: list[tuple[str, str]] = []

    def _fanxiu_runtime(self, _ctx, _asset_tree_path=None, *, stop_event):
        assert isinstance(stop_event, threading.Event)
        return self.runtime

    def _raise_if_stopped(self, _stop_event):
        return None

    def _world_scene_ocr_confirmed_text(self, text):
        return "储物袋" in text and "装备" in text

    def _known_blocking_overlay_info(self, _ctx):
        return None

    def _set_status_locked(self, *_args, **_kwargs):
        return None

    def _log(self, level, message):
        self.logs.append((level, message))


def test_login_game_is_scheduler_catalog_task_without_stable_world_start():
    register_fanxiu_data_annotation_default_runtime_jobs()

    definition = get_fanxiu_data_annotation_task_cell_definition("login_game")

    assert definition is not None
    assert definition.label == "登录游戏"
    assert definition.scheduler_supported is True
    assert definition.stable_start_scene_id is None


def test_login_game_uses_current_account_and_reaches_world():
    runner = _FakeRunner([15, 17, 18, 19, 34])

    result = _drain(runner._execute_login_game_task({}, threading.Event()))

    assert result == "success"
    assert runner.runtime.actions == [
        ("click", 15, "登录", [17, 18]),
        ("click", 17, "同意", 18),
        ("click_center", 18, "进入游戏"),
        ("settle", 2.0),
        ("click", 19, "空白", [19, 20, 21, 22, 34, 47]),
    ]
    assert runner.runtime.completion_message == "登录游戏完成，已进入 #34 世界"


def test_login_game_stops_on_account_picker():
    runner = _FakeRunner([16])

    with pytest.raises(RuntimeError, match="避免误登"):
        _drain(runner._execute_login_game_task({}, threading.Event()))

    assert runner.runtime.actions == []


def test_login_game_uses_full_frame_ocr_when_dynamic_cover_identity_misses():
    runner = _FakeRunner(
        [None, 34],
        ocr_text="AppVer:2.46.700211 进入游戏 健康游戏忠告",
    )

    result = _drain(runner._execute_login_game_task({}, threading.Event()))

    assert result == "success"
    assert runner.runtime.actions == [
        ("click_center", 18, "进入游戏"),
        ("settle", 2.0),
    ]


def test_login_game_treats_world_ocr_as_terminal_even_when_image_is_misidentified():
    runner = _FakeRunner([20], ocr_text="储物袋 角色 装备 星海 功法书")

    result = _drain(runner._execute_login_game_task({}, threading.Event()))

    assert result == "success"
    assert runner.runtime.actions == []
