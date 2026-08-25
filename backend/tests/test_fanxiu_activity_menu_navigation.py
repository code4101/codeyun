from __future__ import annotations

import pytest

from backend.core.fanxiu.data_annotation.tasks import activity_menu_navigation as module
from backend.core.fanxiu.data_annotation.tasks.activity_menu_navigation import (
    open_loaded_activity_menu_item,
)
from backend.core.fanxiu.instrumentation.activity_menu import (
    ActivityMenuItem,
    ActivityMenuReadTimings,
    ActivityMenuSnapshot,
)


def _snapshot(*, fingerprint: str = "menu-v1", complete: bool = True):
    return ActivityMenuSnapshot(
        kind="group_popup",
        status="loaded" if complete else "not_loaded",
        complete=complete,
        items=(
            ActivityMenuItem(1, "activity:101", "每日签到", activity_id=101),
            ActivityMenuItem(2, "activity:102", "每日限购", activity_id=102),
        )
        if complete
        else (),
        pid=10,
        process_start_ticks=20,
        fingerprint=fingerprint if complete else "",
        reason="loaded" if complete else "not loaded",
        timings=ActivityMenuReadTimings(0, 0, 0, 0, "test"),
    )


class Runtime:
    def __init__(self):
        self.clicked = []
        self.shape_reads = []

    def cur_frame(self, *, update):
        assert update is True
        return "frame"

    def ocr_tokens_in_shapes(self, scene_id, shapes, *, frame_data_url):
        self.shape_reads.append((scene_id, shapes, frame_data_url))
        return [{"text": "每曰签到", "x": 100, "y": 300, "w": 80, "h": 30}]

    def click_frame_point(self, scene_id, x, y):
        self.clicked.append((scene_id, x, y))

    def wait_scene(self, *scene_ids, timeout, label):
        yield {"kind": "wait", "scene_ids": scene_ids, "timeout": timeout, "label": label}
        return scene_ids[0]


def _run(generator):
    yielded = []
    while True:
        try:
            yielded.append(next(generator))
        except StopIteration as exc:
            return yielded, exc.value


def test_navigation_uses_shape_ocr_and_revalidates_runtime_fingerprint(monkeypatch):
    reads = [_snapshot(), _snapshot()]
    monkeypatch.setattr(module, "read_activity_menu_snapshot", lambda _kind: reads.pop(0))
    runtime = Runtime()

    yielded, result = _run(
        open_loaded_activity_menu_item(
            runtime,
            "每日签到",
            kind="group_popup",
            source_scene_id=403,
            ocr_shape_names=("每日签到",),
            expected_scene_ids=(404,),
        )
    )

    assert runtime.shape_reads == [(403, ("每日签到",), "frame")]
    assert runtime.clicked == [(403, 140.0, 270.0)]
    assert yielded[0]["scene_ids"] == (404,)
    assert result == 404


def test_navigation_refuses_changed_runtime_before_click(monkeypatch):
    reads = [_snapshot(), _snapshot(fingerprint="menu-v2")]
    monkeypatch.setattr(module, "read_activity_menu_snapshot", lambda _kind: reads.pop(0))
    runtime = Runtime()

    with pytest.raises(RuntimeError, match="发生变化"):
        _run(
            open_loaded_activity_menu_item(
                runtime,
                "每日签到",
                kind="group_popup",
                source_scene_id=403,
                ocr_shape_names=("每日签到",),
                expected_scene_ids=(404,),
            )
        )

    assert runtime.clicked == []


def test_navigation_requires_formal_roi_and_successor_scene():
    runtime = Runtime()

    with pytest.raises(ValueError, match="OCR Shape"):
        _run(
            open_loaded_activity_menu_item(
                runtime,
                "每日签到",
                kind="group_popup",
                source_scene_id=403,
                ocr_shape_names=(),
                expected_scene_ids=(404,),
            )
        )
    with pytest.raises(ValueError, match="后继场景"):
        _run(
            open_loaded_activity_menu_item(
                runtime,
                "每日签到",
                kind="group_popup",
                source_scene_id=403,
                ocr_shape_names=("每日签到",),
                expected_scene_ids=(),
            )
        )


def test_navigation_refuses_not_loaded_runtime(monkeypatch):
    monkeypatch.setattr(module, "read_activity_menu_snapshot", lambda _kind: _snapshot(complete=False))
    runtime = Runtime()

    with pytest.raises(RuntimeError, match="尚未完整加载"):
        _run(
            open_loaded_activity_menu_item(
                runtime,
                "每日签到",
                kind="group_popup",
                source_scene_id=403,
                ocr_shape_names=("每日签到",),
                expected_scene_ids=(404,),
            )
        )

    assert runtime.shape_reads == []
    assert runtime.clicked == []
