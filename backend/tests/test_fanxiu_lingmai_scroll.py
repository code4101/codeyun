from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from backend.core.fanxiu.runtime_gui import ocr_name_similarity
from backend.core.fanxiu.data_annotation.tasks.daily_foundation import (
    select_visible_lingmai_target,
)
from backend.core.fanxiu.data_annotation.tasks.lingmai import lingmai_facts_retry_seconds
from backend.core.fanxiu.behavior_tree.runtime import create_behavior_tree_runtime_runner
from pyxllib.prog import BehaviorTreeStatus


def _drain(generator):
    while True:
        try:
            next(generator)
        except StopIteration as stop:
            return stop.value


def test_lingmai_uses_weakest_runtime_authorized_target_still_visible():
    eligible = [
        {"seat_id": 1, "name": "云端|剑南春", "battle_score": 100},
        {"seat_id": 2, "name": "虚天、张舒", "battle_score": 200},
    ]

    target = select_visible_lingmai_target(eligible, "[九州风云]虚天、张舒 驱离")

    assert target == eligible[1]


def test_lingmai_incomplete_facts_reuse_bounded_no_target_backoff() -> None:
    assert lingmai_facts_retry_seconds(
        {"lingmai_no_target_retry_seconds": 1800}
    ) == 1800
    assert lingmai_facts_retry_seconds(
        {
            "lingmai_facts_retry_seconds": 300,
            "lingmai_no_target_retry_seconds": 1800,
        }
    ) == 300


def test_lingmai_kick_search_stops_when_scroll_content_no_longer_changes(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    monkeypatch.setattr(runner, "_shared_spatial_ocr_result", lambda *_args, **_kwargs: {"tokens": []})

    class Runtime:
        ctx = {}
        runner = SimpleNamespace(
            _box=lambda raw, _view: dict(raw),
            _frame_size=lambda _view: (900.0, 1600.0),
        )

        def __init__(self):
            self.drag_count = 0

        @staticmethod
        def shape(_scene, title):
            raw = (
                {"title": title, "x": 200, "y": 480, "w": 200, "h": 40}
                if title == "姓名"
                else {"title": title, "x": 770, "y": 500, "w": 60, "h": 40}
            )
            return SimpleNamespace(raw=raw)

        @staticmethod
        def view(_scene):
            return SimpleNamespace(raw={"width": 900, "height": 1600})

        @staticmethod
        def cur_frame(*, update):
            assert update is True
            return "before"

        @staticmethod
        def image_signature_bytes_in_shape(_shape, *, frame_data_url):
            assert frame_data_url in {"before", "after"}
            return b"unchanged"

        @staticmethod
        def image_signature_similarity(left, right):
            assert left == right == b"unchanged"
            return 100.0

        def drag_shape_to_frame_edge(self, *args, **kwargs):
            self.drag_count += 1

        @staticmethod
        def wait_action_settle(_seconds):
            yield BehaviorTreeStatus.RUNNING

        @staticmethod
        def current_scene(scene_ids, *, update):
            assert scene_ids == [286]
            assert update is True
            return 286, 100.0, "after"

    runtime = Runtime()
    with pytest.raises(RuntimeError, match="列表已到底"):
        _drain(runner._click_daily_lingmai_kick_target(
                {},
                threading.Event(),
                {"lingmai_kick_reset_to_top": True},
            runtime,
            target_player={"seat_id": 7, "name": "目标玩家"},
            task_label="灵脉_座位",
        ))

    assert runtime.drag_count == 2


def test_lingmai_kick_rejects_best_effort_low_similarity_without_click(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    tokens = [{"text": "[古往今来]倪清", "x": 200, "y": 680, "w": 180, "h": 40}]
    monkeypatch.setattr(runner, "_shared_spatial_ocr_result", lambda *_args, **_kwargs: {"tokens": tokens})
    method_globals = runner._click_daily_lingmai_kick_target.__func__.__globals__
    monkeypatch.setitem(
        method_globals,
        "group_ocr_tokens",
        lambda _tokens: [{"text": "[古往今来]倪清", "x": 200, "y": 680, "w": 180, "h": 40}],
    )
    monkeypatch.setitem(
        method_globals,
        "query_spatial_ocr",
        lambda _tokens, _fragment: {"tokens": tokens},
    )

    class Runtime:
        ctx = {}
        runner = SimpleNamespace(
            _box=lambda raw, _view: dict(raw),
            _frame_size=lambda _view: (900.0, 1600.0),
        )

        def __init__(self):
            self.clicked = False
            self.drag_count = 0

        @staticmethod
        def shape(_scene, title):
            raw = (
                {"title": title, "x": 200, "y": 480, "w": 200, "h": 40}
                if title == "姓名"
                else {"title": title, "x": 770, "y": 500, "w": 60, "h": 40}
            )
            return SimpleNamespace(raw=raw)

        @staticmethod
        def view(_scene):
            return SimpleNamespace(raw={"width": 900, "height": 1600})

        @staticmethod
        def cur_frame(*, update):
            assert update is True
            return "frame"

        @staticmethod
        def image_signature_bytes_in_shape(_shape, *, frame_data_url):
            assert frame_data_url == "frame"
            return b"unchanged"

        @staticmethod
        def image_signature_similarity(left, right):
            assert left == right == b"unchanged"
            return 100.0

        def drag_shape_to_frame_edge(self, *args, **kwargs):
            self.drag_count += 1

        @staticmethod
        def wait_action_settle(_seconds):
            yield BehaviorTreeStatus.RUNNING

        @staticmethod
        def current_scene(scene_ids, *, update):
            assert scene_ids == [286]
            assert update is True
            return 286, 100.0, "frame"

        def click_frame_point(self, *_args):
            self.clicked = True

    runtime = Runtime()
    with pytest.raises(RuntimeError, match="低于门槛"):
        _drain(runner._click_daily_lingmai_kick_target(
            {},
            threading.Event(),
            {},
            runtime,
            target_player={"seat_id": 7, "name": "虚天、雪楠"},
            task_label="灵脉_座位",
        ))

    assert runtime.clicked is False
    assert runtime.drag_count == 1


@pytest.mark.parametrize(
    ("runtime_name", "visible_name"),
    [
        ("\u0361仙-小鱼", "仙-小渔"),
        ("云端|剑南春", "[九州风云]云端"),
        ("虚天、张舒", "[莺啼燕语]虚天、张舒"),
    ],
)
def test_lingmai_kick_fuzzy_matches_runtime_name_variants(
    monkeypatch,
    runtime_name,
    visible_name,
):
    runner = create_behavior_tree_runtime_runner()
    tokens = [{"text": visible_name, "x": 200, "y": 680, "w": 100, "h": 40}]
    monkeypatch.setattr(runner, "_shared_spatial_ocr_result", lambda *_args, **_kwargs: {"tokens": tokens})
    method_globals = runner._click_daily_lingmai_kick_target.__func__.__globals__
    monkeypatch.setitem(method_globals, "group_ocr_tokens", lambda _tokens: [
        {"text": visible_name, "x": 200, "y": 680, "w": 100, "h": 40},
    ])
    monkeypatch.setitem(
        method_globals,
        "query_spatial_ocr",
        lambda _tokens, _fragment: {"tokens": tokens},
    )

    class Runtime:
        ctx = {}
        runner = SimpleNamespace(
            _box=lambda raw, _view: dict(raw),
            _frame_size=lambda _view: (900.0, 1600.0),
        )

        def __init__(self):
            self.clicked = None

        @staticmethod
        def shape(_scene, title):
            raw = (
                {"title": title, "x": 200, "y": 480, "w": 200, "h": 40}
                if title == "姓名"
                else {"title": title, "x": 770, "y": 500, "w": 60, "h": 40}
            )
            return SimpleNamespace(raw=raw)

        @staticmethod
        def view(_scene):
            return SimpleNamespace(raw={"width": 900, "height": 1600})

        @staticmethod
        def cur_frame(*, update):
            assert update is True
            return "frame"

        def click_frame_point(self, scene_id, x, y):
            self.clicked = (scene_id, x, y)

        @staticmethod
        def wait_scene(scene_id, *, timeout, label):
            assert scene_id == 380
            yield BehaviorTreeStatus.RUNNING

        @staticmethod
        def current_scene(scene_ids, *, update):
            assert scene_ids == [380]
            return 380, 100.0, "frame380"

        @staticmethod
        def ocr_text(_frame):
            return "驱离"

    runtime = Runtime()
    def complete(*_args, **_kwargs):
        if False:
            yield
        return "done"

    monkeypatch.setattr(runner, "_complete_daily_lingmai_kick", complete)
    result = _drain(runner._click_daily_lingmai_kick_target(
        {},
        threading.Event(),
        {},
        runtime,
        target_player={"seat_id": 7, "name": runtime_name},
        task_label="灵脉_座位",
    ))

    assert result == "done"
    assert runtime.clicked == pytest.approx((286, 800.0, 720.0))


def test_lingmai_kick_nudges_edge_candidate_before_clicking(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    frames = {
        "edge": [{"text": "目标玩家", "x": 200, "y": 1200, "w": 100, "h": 40}],
        "safe": [{"text": "目标玩家", "x": 200, "y": 900, "w": 100, "h": 40}],
    }
    monkeypatch.setattr(
        runner,
        "_shared_spatial_ocr_result",
        lambda _ctx, frame, **_kwargs: {"tokens": frames[frame]},
    )
    method_globals = runner._click_daily_lingmai_kick_target.__func__.__globals__
    monkeypatch.setitem(
        method_globals,
        "group_ocr_tokens",
        lambda tokens: [dict(tokens[0])],
    )
    monkeypatch.setitem(
        method_globals,
        "query_spatial_ocr",
        lambda tokens, _fragment: {"tokens": tokens},
    )

    class Runtime:
        ctx = {}
        runner = SimpleNamespace(
            _box=lambda raw, _view: dict(raw),
            _frame_size=lambda _view: (900.0, 1600.0),
        )

        def __init__(self):
            self.frame = "edge"
            self.clicked = None
            self.nudged = 0

        @staticmethod
        def shape(_scene, title):
            raw = (
                {"title": title, "x": 200, "y": 480, "w": 200, "h": 40}
                if title == "姓名"
                else {"title": title, "x": 770, "y": 500, "w": 60, "h": 40}
            )
            return SimpleNamespace(raw=raw)

        @staticmethod
        def view(_scene):
            return SimpleNamespace(raw={"width": 900, "height": 1600})

        def cur_frame(self, *, update):
            assert update is True
            return self.frame

        def nudge_shape_content_for_box(self, _shape, box, **_kwargs):
            assert float(box["y"]) + float(box["h"]) > 1600 * 0.78
            self.nudged += 1
            self.frame = "safe"
            yield BehaviorTreeStatus.RUNNING
            return "down"

        def click_frame_point(self, scene_id, x, y):
            self.clicked = (scene_id, x, y)

        @staticmethod
        def wait_scene(scene_id, *, timeout, label):
            assert scene_id == 380
            yield BehaviorTreeStatus.RUNNING

        @staticmethod
        def current_scene(scene_ids, *, update):
            assert scene_ids == [380]
            return 380, 100.0, "frame380"

        @staticmethod
        def ocr_text(_frame):
            return "驱离"

    runtime = Runtime()

    def complete(*_args, **_kwargs):
        if False:
            yield
        return "done"

    monkeypatch.setattr(runner, "_complete_daily_lingmai_kick", complete)
    result = _drain(runner._click_daily_lingmai_kick_target(
        {},
        threading.Event(),
        {},
        runtime,
        target_player={"seat_id": 7, "name": "目标玩家"},
        task_label="灵脉_座位",
    ))

    assert result == "done"
    assert runtime.nudged == 1
    assert runtime.clicked == pytest.approx((286, 800.0, 940.0))


def test_lingmai_name_similarity_uses_shared_edit_distance():
    assert ocr_name_similarity("\u0361仙-小鱼", "仙-小渔") == pytest.approx(2 / 3)
    assert ocr_name_similarity("小鱼", "小渔") == pytest.approx(0.5)
    assert ocr_name_similarity("小鱼", "【称号】小鱼【联盟】") == 1.0
