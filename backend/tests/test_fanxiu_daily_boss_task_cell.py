from __future__ import annotations

import threading
from pathlib import Path

from backend.core.fanxiu.data_annotation.default_jobs import (
    register_fanxiu_data_annotation_default_runtime_jobs,
)
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
)
from backend.core.fanxiu.data_annotation.runner import (
    create_behavior_tree_runtime_runner,
)


def _drain(generator):
    while True:
        try:
            next(generator)
        except StopIteration as stop:
            return stop.value


def test_daily_boss_task_cell_leaves_scene_lifecycle_to_business_handler() -> None:
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("daily_boss")
    assert definition is not None
    calls: list[tuple[dict, dict]] = []

    class Runner:
        def _fanxiu_runtime(self, *_args, **_kwargs):
            raise AssertionError("daily boss wrapper must not repeat scene navigation")

        def _execute_daily_boss_task(self, ctx, _stop_event, payload):
            calls.append((ctx, payload))
            if False:
                yield None
            return "success"

    ctx = {"asset_tree_path": "asset-tree.json"}
    payload = {"post_challenge_timeout_seconds": 900}
    result = _drain(definition.handler(Runner(), ctx, payload, threading.Event()))

    assert result == "success"
    assert calls == [(ctx, payload)]


def test_daily_boss_mozu_world_transition_waits_without_fallback_click(monkeypatch) -> None:
    runner = create_behavior_tree_runtime_runner()
    scenes = iter(
        [
            (None, 0.0, "mozu-transition", ""),
            (34, 100.0, "world", ""),
        ]
    )
    ensured: list[str] = []

    class Runtime:
        def wait_action_settle(self, _seconds):
            if False:
                yield None

        def clear_frame(self):
            raise AssertionError("transition must finish before goto_view fallback")

        def goto_view(self, _scene_id):
            raise AssertionError("transition must not click or navigate")

    def no_overlay(*_args, **_kwargs):
        if False:
            yield None
        return False

    def ensure_world(*_args, **_kwargs):
        ensured.append("world")
        if False:
            yield None

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: Runtime())
    monkeypatch.setattr(runner, "_fanxiu_runtime_scene_text", lambda *_args, **_kwargs: next(scenes))
    monkeypatch.setattr(runner, "_close_daily_boss_item_detail_if_present", no_overlay)
    monkeypatch.setattr(runner, "_close_daily_boss_storage_bag_if_present", no_overlay)
    monkeypatch.setattr(runner, "_scene_reference_similarity", lambda *_args, **_kwargs: 96.0)
    monkeypatch.setattr(runner, "_ensure_daily_lingzu_outer_world", ensure_world)

    result = _drain(
        runner._return_daily_boss_to_world(
            {"asset_tree_path": Path("asset-tree.json"), "images": {314: {"id": 314}}},
            threading.Event(),
        )
    )

    assert result == "success"
    assert ensured == ["world"]
    assert runner.status()["phase"] == "daily_boss_wait_mozu_world_transition"


def test_daily_boss_cleanup_waits_for_world_after_goto_unknown_timeout(monkeypatch) -> None:
    runner = create_behavior_tree_runtime_runner()
    waits: list[tuple[int, float]] = []

    class Runtime:
        def clear_frame(self):
            return None

        def goto_view(self, _scene_id):
            if False:
                yield None
            raise RuntimeError("unknown transition timeout")

        def wait_view(self, scene_id, **kwargs):
            waits.append((scene_id, kwargs["timeout"]))
            if False:
                yield None
            return 34

    def no_overlay(*_args, **_kwargs):
        if False:
            yield None
        return False

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: Runtime())
    monkeypatch.setattr(runner, "_fanxiu_runtime_scene_text", lambda *_args, **_kwargs: (186, 100.0, "fight", ""))
    monkeypatch.setattr(runner, "_close_daily_boss_item_detail_if_present", no_overlay)
    monkeypatch.setattr(runner, "_close_daily_boss_storage_bag_if_present", no_overlay)

    result = _drain(
        runner._return_daily_boss_to_world(
            {"asset_tree_path": Path("asset-tree.json"), "images": {}},
            threading.Event(),
        )
    )

    assert result == "success"
    assert waits == [(34, 120.0)]


def _ocr_line(text: str, *, x: float, y: float, w: float = 100, h: float = 40, line: str) -> list[dict]:
    width = w / max(1, len(text))
    return [
        {
            "text": char,
            "x": x + index * width,
            "y": y,
            "w": width,
            "h": h,
            "parent_line_id": line,
        }
        for index, char in enumerate(text)
    ]


def test_daily_boss_transition_forward_requires_exact_splash_identity_and_button_region() -> None:
    runner = create_behavior_tree_runtime_runner()

    class Runtime:
        def __init__(self, tokens):
            self.tokens = tokens
            self.calls = 0

        def full_frame_ocr_tokens(self, _frame):
            self.calls += 1
            return self.tokens

    valid_tokens = [
        *_ocr_line("活动规则", x=700, y=500, line="rules"),
        *_ocr_line("天地尊局", x=320, y=900, line="title"),
        *_ocr_line("雁行布陈众未晓", x=150, y=1080, w=600, line="verse"),
        *_ocr_line("前往", x=398, y=1326, w=114, line="forward"),
    ]
    runtime = Runtime(valid_tokens)

    assert runner._daily_boss_transition_forward_point(runtime, "frame") == (455.0, 1346.0)
    assert runtime.calls == 1

    scattered = Runtime([
        *_ocr_line("活动规则", x=700, y=500, line="rules"),
        *_ocr_line("天地", x=320, y=900, line="title-a"),
        *_ocr_line("弈局", x=480, y=900, line="title-b"),
        *_ocr_line("雁行布陈众未晓", x=150, y=1080, w=600, line="verse"),
        *_ocr_line("前往", x=398, y=1326, w=114, line="forward"),
    ])
    assert runner._daily_boss_transition_forward_point(scattered, "frame") is None

    wrong_region = Runtime([
        *valid_tokens[:-2],
        *_ocr_line("前往", x=50, y=200, w=114, line="forward-elsewhere"),
    ])
    assert runner._daily_boss_transition_forward_point(wrong_region, "frame") is None


def test_daily_boss_transition_forward_waits_for_known_successor_before_navigation(monkeypatch) -> None:
    runner = create_behavior_tree_runtime_runner()
    events: list[str] = []
    scenes = iter(
        [
            (None, 0.0, "splash", ""),
            (None, 0.0, "loading-1", ""),
            (None, 0.0, "loading-2", ""),
            (661, 100.0, "event-home", ""),
            (34, 100.0, "world", ""),
        ]
    )

    class Runtime:
        def full_frame_ocr_tokens(self, _frame):
            return [
                *_ocr_line("活动规则", x=700, y=500, line="rules"),
                *_ocr_line("天地尊局", x=320, y=900, line="title"),
                *_ocr_line("雁行布陈众未晓", x=150, y=1080, w=600, line="verse"),
                *_ocr_line("前往", x=398, y=1326, w=114, line="forward"),
            ]

        def click_frame_point(self, _view, _x, _y):
            events.append("click")

        def wait_action_settle(self, _seconds):
            events.append("wait")
            if False:
                yield None

        def clear_frame(self):
            events.append("clear")

        def goto_view(self, scene_id):
            events.append(f"goto-{scene_id}")
            if False:
                yield None
            return "success"

    def no_overlay(*_args, **_kwargs):
        if False:
            yield None
        return False

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: Runtime())
    monkeypatch.setattr(runner, "_fanxiu_runtime_scene_text", lambda *_args, **_kwargs: next(scenes))
    monkeypatch.setattr(runner, "_close_daily_boss_item_detail_if_present", no_overlay)
    monkeypatch.setattr(runner, "_close_daily_boss_storage_bag_if_present", no_overlay)

    result = _drain(
        runner._return_daily_boss_to_world(
            {"asset_tree_path": Path("asset-tree.json"), "images": {314: {"id": 314}}},
            threading.Event(),
            allow_post_boss_transition=True,
        )
    )

    assert result == "success"
    assert events.count("click") == 1
    assert events.count("wait") == 3
    assert events.index("goto-34") > events.index("click")


def test_daily_boss_status_uses_boss_hud_roi_for_refresh_countdown(monkeypatch) -> None:
    runner = create_behavior_tree_runtime_runner()

    class Runtime:
        def ocr_text(self, _frame):
            return "离开"

        def full_frame_ocr_tokens(self, _frame):
            return [
                *_ocr_line("首领", x=826, y=148, w=38, h=69, line="boss"),
                *_ocr_line("伤害", x=527, y=201, w=58, h=27, line="damage"),
                *_ocr_line("00:09:20后刷新", x=339, y=369, w=232, h=42, line="refresh"),
                *_ocr_line("数据统计", x=633, y=517, w=111, h=28, line="stats"),
                *_ocr_line("6天后结束", x=17, y=757, w=114, h=25, line="unrelated"),
            ]

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: Runtime())

    text = runner._daily_boss_status_text_from_frame(
        {"asset_tree_path": Path("asset-tree.json")},
        "fight-frame",
    )

    assert "00:09:20后刷新" in text
    assert "首领" in text
    assert "伤害" in text
    assert "6天后结束" not in text
    assert runner._daily_boss_done_text(text) is True


def test_daily_boss_refresh_countdown_requires_boss_context() -> None:
    runner = create_behavior_tree_runtime_runner()

    assert runner._daily_boss_done_text("首领 伤害 数据统计 00:09:20后刷新") is True
    assert runner._daily_boss_done_text("活动 00:09:20后刷新") is False
    assert runner._daily_boss_done_text("伤害 数据统计 00:09:20后刷新") is False
