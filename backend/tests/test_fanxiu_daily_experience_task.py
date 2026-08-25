from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

import pytest

from backend.core.fanxiu.data_annotation import behavior_tree_runtime as behavior_tree_runtime_module
from backend.core.fanxiu.data_annotation import behavior_tree_control
from backend.core.fanxiu.data_annotation.default_jobs import (
    register_fanxiu_data_annotation_default_runtime_jobs,
)
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
)
from backend.core.fanxiu.data_annotation.scheduler import (
    set_scheduler_task_trigger_time,
)
from backend.core.fanxiu.data_annotation.scheduler_defaults import (
    default_data_annotation_scheduler_tasks,
)
from backend.core.fanxiu.data_annotation.tasks.daily_experience import (
    DailyExperienceProviderUnavailable,
    DailyExperienceTaskMixin,
    group_experience_books,
    is_daily_experience_completion_candidate,
    parse_experience_amount,
    select_daily_experience_action,
)
from backend.core.fanxiu.data_annotation.tasks import daily_foundation as daily_foundation_module
from backend.core.fanxiu.data_annotation.tasks.daily_foundation import (
    DailyFoundationTaskMixin,
)


def _run(generator):
    while True:
        try:
            next(generator)
        except StopIteration as stop:
            return stop.value


def _line(text: str, y: float, *, x: float = 420, w: float = 180, h: float = 24):
    return {"text": text, "x": x, "y": y, "w": w, "h": h}


def _tokens(text: str, *, y: float = 100):
    return [
        {
            "text": char,
            "x": 500 + index * 18,
            "y": y,
            "w": 16,
            "h": 24,
            "parent_line_id": "menu-training",
            "line_order": 0,
            "order": index,
        }
        for index, char in enumerate(text)
    ]


class _CompleteExperienceRuntime:
    def __init__(self) -> None:
        self.scene = 34
        self.clicks: list[tuple[int, str]] = []
        self.ocr_clicks: list[tuple[int, str, dict]] = []
        self.ocr_crops: list[bool] = []
        self.menu_scrolls = 0
        self.menu_scroll_results = [False]

    def wait_click(self, scene: int, shape: str, **_kwargs):
        self.clicks.append((scene, shape))
        transitions = {
            (34, "进入绿瓶"): 20,
            (405, "提升"): 406,
            (406, "返回"): 405,
            (405, "返回"): 20,
            (20, "回到世界"): 34,
        }
        self.scene = transitions[(scene, shape)]
        if False:
            yield None

    def wait_click_then_view(self, scene: int, shape: str, *targets: int, **_kwargs):
        self.clicks.append((scene, shape))
        assert (scene, shape, targets) == (405, "提升", ((406, 413),))
        self.scene = 406
        if False:
            yield None
        return 406

    def wait_view(self, scene: int, *_scenes: int, **_kwargs):
        assert self.scene == scene
        if False:
            yield None
        return scene

    def cur_frame(self, update: bool = False):
        return f"frame-{self.scene}-{int(update)}"

    def match_view(self, scene: int, *, frame_data_url: str):
        return scene == self.scene, 100.0 if scene == self.scene else 0.0, frame_data_url

    def wait_action_settle(self, _seconds: float):
        if False:
            yield None

    def scroll_shape_content(self, scene: int, shape: str, *, direction: str):
        assert (scene, shape, direction) == (20, "菜单", "left")
        self.menu_scrolls += 1
        if False:
            yield None
        return self.menu_scroll_results.pop(0) if self.menu_scroll_results else False

    def click_ocr_text(self, scene: int, target: str, **kwargs):
        self.ocr_clicks.append((scene, target, kwargs))
        self.scene = 405

    def ocr_lines_in_shapes(self, scene: int, shapes: list[str], **kwargs):
        assert (scene, shapes) == (406, ["经验书"])
        self.ocr_crops.append(bool(kwargs.get("crop")))
        return [
            _line("修炼心得", 600),
            _line("1115功法经验", 632),
            _line("日常", 710),
            _line("今日奖励已领完", 742),
        ]


class _ExperienceRunner(DailyExperienceTaskMixin):
    def __init__(self, runtime: _CompleteExperienceRuntime) -> None:
        self.runtime = runtime
        self.next_times: list[tuple[str, str | None]] = []
        self.logs: list[tuple[str, str]] = []

    def _fanxiu_runtime(self, *_args, **_kwargs):
        return self.runtime

    @staticmethod
    def _daily_experience_progression_snapshot():
        return {"complete": True, "current_book_full": False}

    def _persist_scheduler_task_next_time(self, task_id: str, next_time: str | None) -> None:
        self.next_times.append((task_id, next_time))

    def _log(self, kind: str, message: str) -> None:
        self.logs.append((kind, message))


class _BossRunner(DailyFoundationTaskMixin):
    def __init__(self, result: str) -> None:
        self.result = result
        self.logs: list[tuple[str, str]] = []

    def _execute_daily_boss_task_flow(self, *_args, **_kwargs):
        if False:
            yield None
        return self.result

    def _log(self, kind: str, message: str) -> None:
        self.logs.append((kind, message))

def test_experience_groups_keep_native_lines_and_pair_details():
    groups = group_experience_books(
        [
            _line("修炼心得", 100),
            _line("9999功法经验", 132),
            _line("潜修心得·四刻", 210),
            _line("120周天修炼效果", 242),
            _line("日常", 320),
            _line("今日奖励已领完", 352),
        ]
    )

    assert [(group.title, group.detail) for group in groups] == [
        ("修炼心得", "9999功法经验"),
        ("潜修心得·四刻", "120周天修炼效果"),
        ("日常", "今日奖励已领完"),
    ]
    assert parse_experience_amount("1.2万功法经验") == 12_000
    assert parse_experience_amount("9999功法经验") == 9999


def test_experience_groups_recover_true_insight_from_purchase_detail():
    groups = group_experience_books(
        [
            _line("修炼心得", 100),
            _line("5207功法经验", 132),
            _line("潜修真俉", 210),
            _line("点击购买道具", 242),
            _line("日常", 320),
            _line("前往获得日常奖励", 352),
        ]
    )

    action = select_daily_experience_action(groups)

    assert action is not None
    assert action[0] == "true_insight"
    assert action[1].detail == "点击购买道具"
    assert is_daily_experience_completion_candidate(groups) is False


def test_experience_groups_normalize_traditional_true_insight_and_ignore_plus_line():
    groups = group_experience_books(
        [
            _line("修炼心得", 100),
            _line("1116功法经验", 132),
            _line("潛修真悟", 210),
            _line("+", 228, x=313, w=52, h=51),
            _line("点击购买道具", 242),
            _line("日常", 320),
            _line("今日奖励已领完", 352),
        ]
    )

    action = select_daily_experience_action(groups)

    assert action is not None
    assert action[0] == "true_insight"
    assert action[1].title_line["text"] == "潛修真悟"
    assert DailyExperienceTaskMixin._daily_experience_item_point(action[1]) == (356.0, 251.0)


def test_consumed_green_aura_catalogue_row_is_not_actionable():
    groups = group_experience_books(
        [
            _line("修炼心得", 100),
            _line("1115功法经验", 132),
            _line("日常", 210),
            _line("今日奖励已领完", 242),
            _line("功法经验", 320),
            _line("点击查看获取途径", 352),
            _line("小绿瓶灵气", 430),
            _line("点击查看获取途径", 462),
        ]
    )

    assert select_daily_experience_action(groups) is None
    assert is_daily_experience_completion_candidate(groups) is True


def test_green_aura_with_cultivation_effect_remains_actionable():
    groups = group_experience_books(
        [
            _line("日常", 100),
            _line("今日奖励已领完", 132),
            _line("小绿瓶灵气", 210),
            _line("300周天修炼效果", 242),
        ]
    )

    action = select_daily_experience_action(groups)

    assert action is not None
    assert action[0] == "green_aura"


def test_daily_experience_requires_repeated_full_and_crop_confirmation_before_success():
    runtime = _CompleteExperienceRuntime()
    runner = _ExperienceRunner(runtime)
    result = _run(
        runner._execute_daily_experience_task(
            {"asset_tree_path": Path("asset-tree.json")},
            threading.Event(),
        )
    )

    assert result["outcome"] == "consumables_exhausted"
    assert runtime.scene == 34
    assert runtime.clicks[-3:] == [
        (406, "返回"),
        (405, "返回"),
        (20, "回到世界"),
    ]
    scene, target, kwargs = runtime.ocr_clicks[0]
    assert (scene, target) == (20, "修炼")
    assert kwargs["in_shapes"] == ["菜单"]
    assert kwargs["anchor"] == "top_center"
    assert kwargs["offset"] == (0.0, -1.0)
    assert kwargs["offset_unit"] == "height"
    assert runtime.menu_scrolls == 1
    assert runtime.ocr_crops == [False, True, False, True, False, True]
    assert runner.next_times == [("daily-experience", None)]


def test_daily_experience_resets_persisted_menu_position_to_left_edge():
    runtime = _CompleteExperienceRuntime()
    runtime.menu_scroll_results = [True, True, False]
    runner = _ExperienceRunner(runtime)

    _run(runner._daily_experience_enter(runtime, timeout=18))

    assert runtime.menu_scrolls == 3
    assert runtime.ocr_clicks[0][0:2] == (20, "修炼")


def test_daily_experience_open_books_closes_stale_413_and_reopens(monkeypatch):
    calls: list[tuple] = []

    class Landed:
        def __init__(self, scene_id):
            self.id = scene_id

    class Runtime:
        landings = iter((413, 406))

        def wait_click_then_view(self, scene, shape, *targets, **kwargs):
            calls.append(("wait_click_then_view", scene, shape, targets, kwargs))
            if False:
                yield None
            return Landed(next(self.landings))

    runner = _ExperienceRunner(Runtime())

    def close_result(_runtime, *, timeout):
        calls.append(("close_result", timeout))
        if False:
            yield None

    monkeypatch.setattr(runner, "_daily_experience_close_result_to_training", close_result)

    _run(runner._daily_experience_open_books(runner.runtime, timeout=18))

    assert calls == [
        (
            "wait_click_then_view",
            405,
            "提升",
            ((406, 413),),
            {
                "timeout": 18,
                "label": "日常_经验：打开经验书，等待 #406；允许已知遗留结算 #413 后有界清理",
            },
        ),
        ("close_result", 18),
        (
            "wait_click_then_view",
            405,
            "提升",
            ((406, 413),),
            {
                "timeout": 18,
                "label": "日常_经验：打开经验书，等待 #406；允许已知遗留结算 #413 后有界清理",
            },
        ),
    ]


def test_daily_experience_completion_is_committed_before_best_effort_departure(monkeypatch):
    calls: list[tuple] = []
    runner = _ExperienceRunner(_CompleteExperienceRuntime())

    def persist(task_id, next_time):
        calls.append(("persist", task_id, next_time))

    def return_world(_runtime, *, timeout):
        calls.append(("return_world", timeout))
        raise TimeoutError("world animation")
        yield  # pragma: no cover

    monkeypatch.setattr(runner, "_persist_scheduler_task_next_time", persist)
    monkeypatch.setattr(runner, "_daily_experience_return_world", return_world)

    result = _run(
        runner._daily_experience_finish_consumables_exhausted(
            runner.runtime,
            {"__scheduler_task_id": "daily-experience"},
            timeout=18,
        )
    )

    assert calls == [
        ("persist", "daily-experience", None),
        ("return_world", 18),
    ]
    assert result["result"] == "success"
    assert result["current_scene"] is None
    assert "离场未完成" in result["message"]
    assert runner.logs[0][0] == "warning"


def test_daily_experience_observation_prioritizes_overlay_and_keeps_unknown_explicit():
    class Runtime:
        matched = {405, 406, 407, 408, 413}

        def match_view(self, scene, *, frame_data_url):
            return scene in self.matched, 100.0, frame_data_url

    runtime = Runtime()
    observed = DailyExperienceTaskMixin._daily_experience_observe_scene(
        runtime,
        "frame",
        frozenset({405, 406, 407, 408, 413}),
    )
    assert observed == 413

    runtime.matched = set()
    assert DailyExperienceTaskMixin._daily_experience_observe_scene(
        runtime,
        "transient",
        frozenset({405, 406, 407, 408, 413}),
    ) is None


def test_daily_experience_provider_unavailable_fails_before_replace_click():
    calls: list[tuple] = []

    class Runtime:
        def wait_click(self, scene, shape):
            calls.append(("wait_click", scene, shape))
            if False:
                yield None

    runner = _ExperienceRunner(Runtime())
    runner._daily_experience_book_plan_snapshot = lambda: {
        "complete": False,
        "available": False,
        "reason": "provider_unavailable",
    }

    with pytest.raises(DailyExperienceProviderUnavailable):
        _run(runner._daily_experience_replace_full_book(runner.runtime, timeout=18))

    assert calls == []


def test_daily_experience_purchase_requires_business_delta_before_second_click():
    calls: list[tuple] = []

    class Landed:
        id = 414

    class Runtime:
        def click_frame_point(self, scene, x, y):
            calls.append(("click_point", scene, x, y))

        def wait_view(self, *scenes, **_kwargs):
            if False:
                yield None
            return Landed()

        def cur_frame(self, *, update=False):
            return "purchase-dialog"

        def match_view(self, scene, *, frame_data_url):
            return scene == 414, 100.0 if scene == 414 else 0.0, frame_data_url

        def ocr_text(self, frame):
            return "潜修真悟 购买 余额100"

        def wait_click(self, scene, shape):
            calls.append(("wait_click", scene, shape))
            if False:
                yield None

        def wait_action_settle(self, seconds):
            if False:
                yield None

    runner = _ExperienceRunner(Runtime())
    group = group_experience_books([
        _line("潜修真悟", 600),
        _line("点击购买道具", 632),
    ])[0]

    with pytest.raises(RuntimeError, match="未观察到业务文本变化"):
        _run(
            runner._daily_experience_buy_true_insight(
                runner.runtime,
                group,
                timeout=18,
                max_purchases=8,
            )
        )

    assert [call for call in calls if call[:2] == ("wait_click", 414)] == [
        ("wait_click", 414, "购买")
    ]


def test_daily_experience_completion_propagates_interrupt_after_commit(monkeypatch):
    runner = _ExperienceRunner(_CompleteExperienceRuntime())

    def return_world(_runtime, *, timeout):
        raise InterruptedError("stop")
        yield  # pragma: no cover

    monkeypatch.setattr(runner, "_daily_experience_return_world", return_world)

    with pytest.raises(InterruptedError, match="stop"):
        _run(
            runner._daily_experience_finish_consumables_exhausted(
                runner.runtime,
                {"__scheduler_task_id": "daily-experience"},
                timeout=18,
            )
        )

    assert runner.next_times == [("daily-experience", None)]


def test_daily_experience_return_waits_until_406_overlay_is_really_gone():
    calls: list[tuple] = []

    class Runtime:
        def __init__(self) -> None:
            self.poll = 0

        def wait_click(self, scene, shape):
            calls.append(("click", scene, shape))
            if False:
                yield None

        def cur_frame(self, *, update=False):
            self.poll += 1
            return f"frame-{self.poll}"

        def match_view(self, scene, *, frame_data_url):
            # #405 remains a perfect background match while #406 is open.
            books_open = self.poll < 3
            matched = True if scene == 405 else books_open
            calls.append(("match", scene, frame_data_url, matched))
            return matched, 100.0 if matched else 0.0, frame_data_url

        def wait_action_settle(self, seconds):
            calls.append(("settle", seconds))
            if False:
                yield None

        def wait_view(self, scene, **kwargs):
            calls.append(("wait_view", scene, kwargs))
            if False:
                yield None

    runtime = Runtime()
    runner = _ExperienceRunner(runtime)

    _run(runner._daily_experience_return_world(runtime, timeout=3))

    assert [(call[1], call[2]) for call in calls if call[0] == "click"] == [
        (406, "返回"),
        (405, "返回"),
        (20, "回到世界"),
    ]
    assert calls.index(("click", 405, "返回")) > calls.index(("match", 406, "frame-3", False))


def test_daily_experience_bugged_result_returns_from_406_before_reopening():
    calls: list[tuple] = []

    class Runtime:
        def __init__(self):
            self.scene = 406

        def click_shape_center(self, scene, shape):
            calls.append(("click_shape_center", scene, shape))

        def cur_frame(self, *, update=False):
            calls.append(("frame", update))
            return f"scene-{self.scene}"

        def match_view(self, scene, *, frame_data_url):
            matched = scene == self.scene or (scene == 405 and self.scene == 406)
            calls.append(("match", scene, frame_data_url, matched))
            return matched, 100.0 if matched else 0.0, frame_data_url

        def wait_click(self, scene, shape):
            calls.append(("wait_click", scene, shape))
            assert (scene, shape) == (406, "返回")
            self.scene = 405
            if False:
                yield None

        def wait_action_settle(self, seconds):
            calls.append(("settle", seconds))
            if False:
                yield None

    runtime = Runtime()
    runner = _ExperienceRunner(runtime)

    def reopen(_runtime, *, timeout):
        calls.append(("reopen", timeout))
        if False:
            yield None

    runner._daily_experience_reopen_books = reopen
    _run(runner._daily_experience_close_bugged_result(runtime, timeout=1))

    assert calls[0] == ("click_shape_center", 406, "返回")
    assert ("wait_click", 406, "返回") in calls
    assert calls[-1] == ("reopen", 1)


def test_daily_experience_bugged_result_can_land_directly_on_405():
    calls: list[tuple] = []

    class Runtime:
        def click_shape_center(self, scene, shape):
            calls.append(("click_shape_center", scene, shape))

        def cur_frame(self, *, update=False):
            return "training"

        def match_view(self, scene, *, frame_data_url):
            return scene == 405, 100.0 if scene == 405 else 0.0, frame_data_url

        def wait_action_settle(self, _seconds):
            if False:
                yield None

        def wait_click(self, scene, shape):
            calls.append(("wait_click", scene, shape))
            if False:
                yield None

    runtime = Runtime()
    runner = _ExperienceRunner(runtime)

    def reopen(_runtime, *, timeout):
        calls.append(("reopen", timeout))
        if False:
            yield None

    runner._daily_experience_reopen_books = reopen
    _run(runner._daily_experience_close_bugged_result(runtime, timeout=1))

    assert calls == [
        ("click_shape_center", 406, "返回"),
        ("reopen", 1),
    ]


def test_daily_experience_bugged_result_unknown_landing_fails_closed():
    calls: list[tuple] = []

    class Runtime:
        def click_shape_center(self, scene, shape):
            calls.append(("click_shape_center", scene, shape))

        def cur_frame(self, *, update=False):
            return "unknown"

        def match_view(self, scene, *, frame_data_url):
            calls.append(("match", scene))
            return False, 0.0, frame_data_url

        def wait_action_settle(self, _seconds):
            if False:
                yield None

        def wait_click(self, scene, shape):
            calls.append(("wait_click", scene, shape))
            if False:
                yield None

    runtime = Runtime()
    runner = _ExperienceRunner(runtime)

    with pytest.raises(TimeoutError, match="#413 结算层关闭后未落到"):
        _run(runner._daily_experience_close_bugged_result(runtime, timeout=1))

    assert not any(call[0] == "wait_click" for call in calls)


def test_daily_experience_green_aura_uses_hidden_book_return_for_bugged_result_page(monkeypatch):
    calls = []

    class Landed:
        id = 413

    class Runtime:
        def click_frame_point(self, scene, x, y):
            calls.append(("click_point", scene, x, y))

        def wait_view(self, *scenes, **_kwargs):
            calls.append(("wait_view", scenes))
            if False:
                yield None
            return Landed()

        def click_shape_center(self, scene, shape):
            calls.append(("click_shape_center", scene, shape))

    runtime = Runtime()
    runner = _ExperienceRunner(runtime)

    def reopen(_runtime, *, timeout):
        calls.append(("reopen", timeout))
        if False:
            yield None

    def wait_closed(_runtime, *, timeout):
        calls.append(("wait_closed", timeout))
        if False:
            yield None

    def wait_landing(_runtime, *, timeout):
        calls.append(("wait_landing", timeout))
        if False:
            yield None
        return 405

    monkeypatch.setattr(runner, "_daily_experience_reopen_books", reopen)
    monkeypatch.setattr(runner, "_daily_experience_wait_training_without_books", wait_closed)
    monkeypatch.setattr(runner, "_daily_experience_wait_bugged_result_landing", wait_landing)
    group = group_experience_books([
        _line("小绿瓶灵气", 600),
        _line("44592功法经验", 632),
    ])[0]

    _run(runner._daily_experience_use_green_aura(runtime, group, timeout=18))

    assert calls == [
        ("click_point", 406, 356.0, 641.0),
        ("wait_view", (413, 405)),
        ("click_shape_center", 406, "返回"),
        ("wait_landing", 18),
        ("wait_closed", 18),
        ("reopen", 18),
    ]


def test_daily_experience_green_aura_can_return_directly_to_training_page(monkeypatch):
    calls = []

    class Landed:
        id = 405

    class Runtime:
        def click_frame_point(self, scene, x, y):
            calls.append(("click_point", scene, x, y))

        def wait_view(self, *scenes, **_kwargs):
            calls.append(("wait_view", scenes))
            if False:
                yield None
            return Landed()

    runtime = Runtime()
    runner = _ExperienceRunner(runtime)

    def open_books(_runtime, *, timeout):
        calls.append(("open_books", timeout))
        if False:
            yield None

    monkeypatch.setattr(runner, "_daily_experience_open_books", open_books)
    group = group_experience_books([
        _line("小绿瓶灵气", 600),
        _line("44592功法经验", 632),
    ])[0]

    _run(runner._daily_experience_use_green_aura(runtime, group, timeout=18))

    assert calls == [
        ("click_point", 406, 356.0, 641.0),
        ("wait_view", (413, 405)),
        ("open_books", 18),
    ]


def test_daily_experience_owned_true_insight_can_go_directly_to_bugged_result(monkeypatch):
    calls = []

    class Landed:
        id = 413

    class Runtime:
        def click_frame_point(self, scene, x, y):
            calls.append(("click_point", scene, x, y))

        def wait_view(self, *scenes, **_kwargs):
            calls.append(("wait_view", scenes))
            if False:
                yield None
            return Landed()

    runtime = Runtime()
    runner = _ExperienceRunner(runtime)

    def close_result(_runtime, *, timeout):
        calls.append(("close_result", timeout))
        if False:
            yield None

    monkeypatch.setattr(runner, "_daily_experience_close_bugged_result", close_result)
    group = group_experience_books([
        _line("潜修真悟", 600),
        _line("120周天修炼效果", 632),
    ])[0]

    _run(runner._daily_experience_buy_true_insight(
        runtime,
        group,
        timeout=18,
        max_purchases=8,
    ))

    assert calls == [
        ("click_point", 406, 356.0, 641.0),
        ("wait_view", (414, 413)),
        ("close_result", 18),
    ]


def test_daily_experience_confirms_regular_pill_use_by_business_ocr():
    calls = []

    class Runtime:
        def __init__(self):
            self.frames = iter(("confirm", "books"))

        def wait_action_settle(self, seconds):
            calls.append(("settle", seconds))
            if False:
                yield None

        def cur_frame(self, *, update=False):
            return next(self.frames)

        def ocr_text(self, frame):
            return "服用丹药 本次服用丹药增加属性：天资+1112 确认" if frame == "confirm" else ""

        def click_ocr_text(self, scene, target, **kwargs):
            calls.append(("click_ocr_text", scene, target, kwargs))

        def match_view(self, scene, *, frame_data_url):
            return (scene == 406 and frame_data_url == "books"), 100.0, frame_data_url

    runtime = Runtime()
    runner = _ExperienceRunner(runtime)

    handled_full = _run(
        runner._daily_experience_settle_after_long_press(runtime, timeout=18)
    )

    assert handled_full is False
    assert calls == [
        ("settle", 1.0),
        ("click_ocr_text", 406, "确认", {"frame_data_url": "confirm"}),
        ("settle", 1.0),
    ]


def test_daily_experience_full_role_exp_preserves_existing_stage_upgrade(monkeypatch):
    calls = []

    class Runtime:
        def wait_click(self, scene, shape):
            calls.append(("wait_click", scene, shape))
            if False:
                yield None

        def wait_action_settle(self, seconds):
            calls.append(("settle", seconds))
            if False:
                yield None

        @staticmethod
        def cur_frame(*, update=False):
            return f"frame-{int(update)}"

        @staticmethod
        def match_view(scene, *, frame_data_url):
            return scene == 408, 100.0, frame_data_url

    runner = _ExperienceRunner(Runtime())

    def breakthrough(_runtime, *, timeout):
        calls.append(("breakthrough", timeout))
        if False:
            yield None

    monkeypatch.setattr(runner, "_daily_experience_run_breakthrough", breakthrough)

    _run(runner._daily_experience_route_full_role_exp(runner.runtime, timeout=18))

    assert calls == [
        ("wait_click", 406, "空白"),
        ("settle", 1.0),
        ("breakthrough", 18),
    ]


def test_daily_experience_full_role_exp_replaces_book_from_405(monkeypatch):
    calls = []

    class Runtime:
        @staticmethod
        def wait_click(scene, shape):
            calls.append(("wait_click", scene, shape))
            if False:
                yield None

        @staticmethod
        def wait_action_settle(seconds):
            calls.append(("settle", seconds))
            if False:
                yield None

        @staticmethod
        def cur_frame(*, update=False):
            return f"frame-{int(update)}"

        @staticmethod
        def match_view(scene, *, frame_data_url):
            return scene == 405, 100.0, frame_data_url

    runner = _ExperienceRunner(Runtime())

    def replace(_runtime, *, timeout):
        calls.append(("replace", timeout))
        if False:
            yield None

    monkeypatch.setattr(runner, "_daily_experience_replace_full_book", replace)

    _run(runner._daily_experience_route_full_role_exp(runner.runtime, timeout=18))

    assert calls == [
        ("wait_click", 406, "空白"),
        ("settle", 1.0),
        ("replace", 18),
    ]


def test_daily_experience_replace_full_book_uses_live_plan_and_option_characters():
    calls = []

    class Match:
        @staticmethod
        def point():
            return 351.5, 699.0

    class Runtime:
        @staticmethod
        def wait_click(scene, shape):
            calls.append(("wait_click", scene, shape))
            if False:
                yield None

        @staticmethod
        def wait_view(*scenes, **_kwargs):
            calls.append(("wait_view", scenes))
            if False:
                yield None

        @staticmethod
        def wait_click_then_view(scene, shape, *targets, **_kwargs):
            calls.append(("wait_click_then_view", scene, shape, targets))
            if False:
                yield None
            return 406

        @staticmethod
        def cur_frame(*, update=False):
            return f"frame-{int(update)}"

        @staticmethod
        def ocr_tokens_in_shapes(scene, shapes, **kwargs):
            calls.append(("ocr_options", scene, tuple(shapes), kwargs))
            texts = tuple("全部仙术剑修法修魔修体修")
            xs = (129, 164, 256, 291, 375, 410, 508, 536, 628, 663, 754, 782)
            return [
                {"text": text, "x": x, "y": 566, "w": 60, "h": 39}
                for text, x in zip(texts, xs, strict=True)
            ]

        @staticmethod
        def click_frame_point(scene, x, y):
            calls.append(("click_point", scene, x, y))

        @staticmethod
        def wait_action_settle(seconds):
            calls.append(("settle", seconds))
            if False:
                yield None

        @staticmethod
        def wait_ocr_text(scene, target, **kwargs):
            calls.append(("wait_ocr_text", scene, target, kwargs))
            if False:
                yield None
            return Match()

    runner = _ExperienceRunner(Runtime())
    runner._daily_experience_book_plan_snapshot = lambda: {
        "complete": True,
        "next_upgradable_book": {
            "book_id": 9001,
            "name": "水衍四时诀",
            "filter_category": "仙术",
            "selection_pool": "equipped_dependency",
        },
    }

    result = _run(runner._daily_experience_replace_full_book(runner.runtime, timeout=18))

    assert result == {
        "book_id": 9001,
        "name": "水衍四时诀",
        "category": "仙术",
        "selection_pool": "equipped_dependency",
    }
    assert ("click_point", 439, 303.5, 585.5) in calls
    assert ("wait_ocr_text", 439, "水衍四时诀", {
        "in_shapes": ("窗口",),
        "timeout_seconds": 45.0,
    }) in calls
    assert ("click_point", 439, 351.5, 699.0) in calls
    assert calls[-3:] == [
        ("wait_click", 440, "修炼"),
        ("wait_view", (405,)),
        ("wait_click_then_view", 405, "提升", ((406, 413),)),
    ]


def test_set_scheduler_trigger_time_is_a_simple_one_shot_time_slot():
    tasks = [
        {
            "id": "daily-experience",
            "task_type": "daily_experience",
            "label": "日常_经验",
            "next_time": None,
        }
    ]

    task = set_scheduler_task_trigger_time(
        tasks,
        "日常_经验",
        "13:10",
        now=datetime(2026, 7, 23, 12, 0),
    )

    assert task["next_time"] == "2026-07-23 13:10:00"

    set_scheduler_task_trigger_time(tasks, "日常_经验", None)
    assert task["next_time"] is None


def test_daily_boss_sets_experience_only_after_true_success(monkeypatch):
    triggered: list[tuple[str, object]] = []
    monkeypatch.setattr(
        daily_foundation_module,
        "_read_data_annotation_scheduler_tasks",
        lambda: [
            {
                "id": "legacy-daily-activity",
                "task_type": "daily_activity",
                "label": "日常_活跃度",
                "last_result": "success",
                "finished_at": "2026-07-23 12:00:00",
                "next_time": "2026-07-24 07:00:00",
            },
            {
                "id": "daily-experience",
                "task_type": "daily_experience",
                "label": "日常_经验",
                "next_time": None,
            },
        ],
    )
    monkeypatch.setattr(behavior_tree_runtime_module, "_now", lambda: datetime(2026, 7, 23, 13, 10))
    monkeypatch.setattr(
        behavior_tree_runtime_module,
        "set_data_annotation_scheduler_task_trigger_time",
        lambda name, when: triggered.append((name, when)) or "2026-07-23 13:10:00",
    )

    assert _run(_BossRunner("skipped")._execute_daily_boss_task({}, threading.Event())) == "skipped"
    assert triggered == []
    assert _run(_BossRunner("success")._execute_daily_boss_task({}, threading.Event())) == "success"
    assert triggered[0][0] == "日常_经验"


def test_daily_experience_waits_until_boss_and_activity_both_complete(monkeypatch):
    triggered: list[tuple[str, object]] = []
    tasks = [
        {
            "id": "daily-boss",
            "task_type": "daily_boss",
            "label": "日常_首领",
            "last_result": "success",
            "finished_at": "2026-08-08 09:00:00",
            "next_time": "2026-08-09 05:00:00",
        },
        {
            "id": "legacy-daily-activity",
            "task_type": "daily_activity",
            "label": "日常_活跃度",
            "last_result": "success",
            "finished_at": "2026-08-08 10:00:00",
            "next_time": "2026-08-08 11:00:00",
        },
        {
            "id": "daily-experience",
            "task_type": "daily_experience",
            "label": "日常_经验",
            "next_time": None,
        },
    ]
    monkeypatch.setattr(daily_foundation_module, "_read_data_annotation_scheduler_tasks", lambda: tasks)
    monkeypatch.setattr(
        behavior_tree_runtime_module,
        "set_data_annotation_scheduler_task_trigger_time",
        lambda name, when: triggered.append((name, when)) or "2026-08-08 12:00:00",
    )
    runner = _BossRunner("success")

    assert runner._trigger_daily_experience_after_prerequisites(
        completed_task_type="daily_boss",
        completed_at=datetime(2026, 8, 8, 12, 0),
    ) is None
    assert triggered == []

    tasks[1]["next_time"] = "2026-08-09 07:00:00"
    assert runner._trigger_daily_experience_after_prerequisites(
        completed_task_type="daily_boss",
        completed_at=datetime(2026, 8, 8, 12, 0),
    ) == "2026-08-08 12:00:00"
    assert triggered == [("日常_经验", datetime(2026, 8, 8, 12, 0))]


def test_daily_activity_can_be_the_last_prerequisite_and_does_not_retrigger_after_experience(monkeypatch):
    triggered: list[tuple[str, object]] = []
    tasks = [
        {
            "id": "daily-boss",
            "task_type": "daily_boss",
            "label": "日常_首领",
            "last_result": "success",
            "finished_at": "2026-08-08 09:00:00",
            "next_time": "2026-08-09 05:00:00",
        },
        {
            "id": "daily-experience",
            "task_type": "daily_experience",
            "label": "日常_经验",
            "last_result": "success",
            "finished_at": "2026-08-08 08:00:00",
            "next_time": None,
        },
    ]
    monkeypatch.setattr(daily_foundation_module, "_read_data_annotation_scheduler_tasks", lambda: tasks)
    monkeypatch.setattr(
        behavior_tree_runtime_module,
        "set_data_annotation_scheduler_task_trigger_time",
        lambda name, when: triggered.append((name, when)) or "2026-08-08 10:00:00",
    )
    runner = _BossRunner("success")

    assert runner._trigger_daily_experience_after_prerequisites(
        completed_task_type="daily_activity",
        completed_at=datetime(2026, 8, 8, 10, 0),
    ) == "2026-08-08 10:00:00"

    tasks[1]["finished_at"] = "2026-08-08 10:30:00"
    assert runner._trigger_daily_experience_after_prerequisites(
        completed_task_type="daily_activity",
        completed_at=datetime(2026, 8, 8, 10, 0),
    ) is None
    assert len(triggered) == 1


def test_daily_experience_is_manual_scheduler_catalog_type_without_scene_policy():
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("daily_experience")
    assert definition is not None
    assert definition.label == "日常_经验"
    assert definition.scheduler_supported is True
    assert not hasattr(definition, "lifecycle")

    task = next(
        item
        for item in default_data_annotation_scheduler_tasks()
        if item["task_type"] == "daily_experience"
    )
    assert task["trigger_description"] == "手动"
    assert task["next_time"] is None
    assert task["error_retry_delay_seconds"] == 600
    assert behavior_tree_control.scheduler_task_retry_delay_seconds(task) == 600

    behavior_tree_control.schedule_failed_task_retry(
        task,
        datetime(2026, 7, 30, 9, 0, 0),
    )
    assert task["next_time"] == "2026-07-30 09:10:00"


def test_daily_experience_default_wrapper_normalizes_once_and_does_not_replay_cleanup():
    calls: list[tuple] = []

    class Runtime:
        def goto_view(self, scene):
            calls.append(("goto", scene))
            if False:
                yield None

    class Runner:
        runtime = Runtime()

        def _fanxiu_runtime(self, *_args, **_kwargs):
            return self.runtime

        def _execute_daily_experience_task(self, _ctx, _stop_event, _payload):
            calls.append(("execute",))
            if False:
                yield None
            return {"result": "success", "current_scene": None}

    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("daily_experience")
    assert definition is not None

    result = _run(definition.handler(Runner(), {}, {}, threading.Event()))

    assert result["result"] == "success"
    assert calls == [("goto", 34), ("execute",)]


def test_timed_manual_experience_enters_due_dispatch_selection():
    register_fanxiu_data_annotation_default_runtime_jobs()
    tasks = [
        {
            "id": "daily-experience",
            "task_type": "daily_experience",
            "label": "日常_经验",
            "next_time": "2020-01-01 00:00:00",
        }
    ]

    selected = behavior_tree_control.select_due_data_annotation_scheduler_tasks(tasks)

    assert [task["id"] for task in selected] == ["daily-experience"]
