from backend.core.fanxiu.data_annotation import behavior_tree_executor  # noqa: F401
from datetime import datetime

from backend.core.fanxiu.data_annotation.tasks.daily_resources import DailyResourceTaskMixin
from backend.core.fanxiu.data_annotation.behavior_tree_executor import BehaviorTreeExecutor
import threading


def _drain(generator):
    while True:
        try:
            next(generator)
        except StopIteration as exc:
            return exc.value


def test_xianshi_weekly_resource_restores_right_menu_before_clicking_entry():
    task = DailyResourceTaskMixin()
    task._log = lambda *_args, **_kwargs: None
    events: list[tuple] = []

    class FakeRuntime:
        matches = iter([False, False, True])

        def shape(self, view_id, title):
            events.append(("shape", view_id, title))
            return object()

        def match_shape(self, _shape):
            matched = next(self.matches)
            events.append(("match", matched))
            return matched

        def ocr_text_in_shapes(self, *_args, **_kwargs):
            return ""

        def scroll_shape_content(self, view_id, title, **options):
            events.append(("scroll", view_id, title, options["direction"]))
            if False:
                yield
            return True

        def wait_click_then_shape(self, *args, **_options):
            events.append(("click",) + args)
            if False:
                yield
            return True

    result = _drain(task._open_xianshi_weekly_resource_entry(FakeRuntime(), {}))

    assert result is True
    assert events == [
        ("shape", 34, "仙市"),
        ("match", False),
        ("scroll", 34, "右侧菜单", "up"),
        ("match", False),
        ("scroll", 34, "右侧菜单", "up"),
        ("match", True),
        ("click", 34, "仙市", 247, "秘藏阁"),
    ]


def test_xianshi_weekly_resource_leaves_world_like_internal_scene_first():
    task = DailyResourceTaskMixin()
    task._log = lambda *_args, **_kwargs: None
    events: list[tuple] = []

    class FakeRuntime:
        matches = iter([False, True])

        def shape(self, view_id, title):
            return object()

        def match_shape(self, _shape):
            return next(self.matches)

        def ocr_text_in_shapes(self, *_args, **_kwargs):
            return "天机阁 储物袋 战斗"

        def click_shape_center(self, view_id, title):
            events.append(("leave", view_id, title))

        def wait_scene(self, layer0, **_options):
            view_id = layer0[0]
            events.append(("wait", view_id))
            if False:
                yield
            return view_id

        def wait_click_then_scene(self, *args, **_options):
            events.append(("confirm",) + args)
            if False:
                yield
            return True

        def wait_click_then_shape(self, *args, **_options):
            events.append(("entry",) + args)
            if False:
                yield
            return True

    result = _drain(task._open_xianshi_weekly_resource_entry(FakeRuntime(), {}))

    assert result is True
    assert events == [
        ("leave", 85, "离开"),
        ("wait", 86),
        ("confirm", 86, "确认", 34),
        ("entry", 34, "仙市", 247, "秘藏阁"),
    ]


def test_daily_xianshi_uses_reference_tab_shape_without_treating_248_as_scene():
    task = object.__new__(BehaviorTreeExecutor)
    events: list[tuple] = []

    class FakeRuntime:
        def wait_click_then_shape(self, *args, **options):
            events.append(("entry", args, options))
            if False:
                yield

        def wait_click(self, *args, **options):
            events.append(("click", args, options))
            if False:
                yield

        def wait_action_settle(self, seconds):
            events.append(("settle", seconds))
            if False:
                yield

        def shape_visible(self, *args):
            events.append(("shape_visible", args))
            return "coin-tab-condition"

        def wait_any(self, conditions, **options):
            events.append(("wait_any", conditions, options))
            if False:
                yield
            return "coin_tab"

        def click_shape_center(self, *args):
            events.append(("fixed_click", args))

        def wait_scene(self, *args, **options):
            events.append(("wait_scene", args, options))
            if False:
                yield
            return 249

    runtime = FakeRuntime()
    task._behavior_tree_context = lambda *_args, **_kwargs: runtime
    task._ensure_world_main_for_right_menu = lambda *_args, **_kwargs: iter(())

    _drain(
        task._open_daily_xianshi_coin_list(
            {"asset_tree_path": None},
            object(),
            {},
            {},
            {},
            {},
            task_label="仙市_秘藏阁",
        )
    )

    assert ("click", (247, "秘藏阁"), {}) in events
    assert ("shape_visible", (248, "仙币")) in events
    assert ("fixed_click", (248, "仙币")) in events
    assert any(event[0] == "wait_scene" and event[1] == ([249],) for event in events)


def test_daily_xianshi_retries_box_click_when_first_click_stays_on_list():
    task = object.__new__(BehaviorTreeExecutor)
    task._lock = threading.RLock()
    task._status = {}
    task._log_locked = lambda *_args, **_kwargs: None
    task._log = lambda *_args, **_kwargs: None
    task._raise_if_stopped = lambda *_args, **_kwargs: None
    clicks: list[tuple[int, str]] = []
    matches = iter([None, {"matched": True, "similarity": 100.0}])

    class FakeRuntime:
        def click_shape_center(self, view_id, title):
            clicks.append((view_id, title))

        def wait_action_settle(self, _seconds):
            if False:
                yield

        def cur_frame(self, **_options):
            return "frame"

        def shape_matches(self, *_args, **_options):
            return next(matches)

        def ocr_text(self, *_args, **_options):
            return "coin list"

    task._behavior_tree_context = lambda *_args, **_kwargs: FakeRuntime()
    task._daily_xianshi_text_is_box_detail = lambda _text: False

    result = _drain(
        task._click_daily_xianshi_free_coin_box(
            {"asset_tree_path": None},
            object(),
            {},
            {},
            {},
            task_label="仙市_秘藏阁",
        )
    )

    assert result is True
    assert clicks == [
        (249, "首个宝匣"),
        (249, "首个宝匣"),
        (250, "领取"),
    ]


def test_xianshi_weekly_resource_success_advances_to_same_monday_five():
    task = DailyResourceTaskMixin()
    writes: list[tuple[str, str]] = []
    task._persist_scheduler_task_next_time = lambda task_id, next_time: writes.append(
        (task_id, next_time)
    )

    result = task._record_xianshi_weekly_resources_done(
        {"__scheduler_task_id": "weekly-instance"},
        now=datetime(2026, 8, 3, 1, 33),
    )

    assert result == "2026-08-03 05:00:00"
    assert writes == [("weekly-instance", "2026-08-03 05:00:00")]


def test_xianshi_weekly_resource_success_after_reset_advances_to_next_monday():
    task = DailyResourceTaskMixin()
    writes: list[tuple[str, str]] = []
    task._persist_scheduler_task_next_time = lambda task_id, next_time: writes.append(
        (task_id, next_time)
    )

    result = task._record_xianshi_weekly_resources_done(
        {},
        now=datetime(2026, 8, 3, 5, 30),
    )

    assert result == "2026-08-10 00:00:00"
    assert writes == [("xianshi-weekly-resources", "2026-08-10 00:00:00")]


def test_daily_gongfeng_law_progress_strips_previous_required_prefix_when_current_is_enough():
    task = DailyResourceTaskMixin()

    assert task._parse_daily_gongfeng_law_progress("800011400/8000") == (11400, 8000)


def test_daily_gongfeng_law_progress_strips_previous_required_prefix_when_current_is_insufficient():
    task = DailyResourceTaskMixin()

    assert task._parse_daily_gongfeng_law_progress("80001400/8000") == (1400, 8000)


def test_daily_gongfeng_law_progress_keeps_plain_progress():
    task = DailyResourceTaskMixin()

    assert task._parse_daily_gongfeng_law_progress("11400/8000") == (11400, 8000)
