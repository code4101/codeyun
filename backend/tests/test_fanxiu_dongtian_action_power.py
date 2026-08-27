import threading

import pytest

from backend.core.fanxiu.data_annotation.behavior_tree_runtime import BehaviorTreeRuntimeRunner
from backend.core.fanxiu.data_annotation.tasks.daily_foundation import _DONGTIAN_PLACE_ANCHORS


def test_dongtian_action_power_uses_runtime_without_gui():
    class Runtime:
        def cur_frame(self, **_kwargs):
            raise AssertionError("不得读取 GUI 帧")

    value, source = BehaviorTreeRuntimeRunner()._daily_dongtian_action_power(
        Runtime(),
        {
            "__dongtian_runtime_snapshot_override": {
                "available": True,
                "complete": True,
                "action_power": 300,
            },
        },
    )

    assert value == 300
    assert source == "runtime:XianLvMinesMgr.Model.Data.V_AttackFatigueValue"


def test_dongtian_action_power_uses_narrow_reader_not_clear_plan(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.dongtian.read_dongtian_action_power_snapshot",
        lambda: calls.append("action") or {
            "available": True,
            "complete": True,
            "action_power": 200,
            "elapsed_seconds": 0.2,
            "evidence": {},
        },
    )
    runner = BehaviorTreeRuntimeRunner()
    monkeypatch.setattr(
        runner,
        "_daily_dongtian_runtime_snapshot",
        lambda _payload: (_ for _ in ()).throw(AssertionError("不得读取清理计划")),
    )

    value, _source = runner._daily_dongtian_action_power(object(), {})

    assert value == 200
    assert calls == ["action"]


def test_dongtian_enemy_plan_is_reused_within_same_job(monkeypatch):
    runner = BehaviorTreeRuntimeRunner()
    calls = []
    plan = {
        "available": True,
        "complete": True,
        "own_union_id": 1,
        "own_union_name": "own",
        "mines": [
            {"id": 1, "cross_union_id": 2, "cross_union_name": "enemy"},
        ],
        "evidence": {},
    }
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.dongtian.read_dongtian_clear_plan_snapshot",
        lambda: calls.append("plan") or dict(plan),
    )
    payload = {}

    first = runner._daily_dongtian_enemy_places_from_runtime(payload)
    second = runner._daily_dongtian_enemy_places_from_runtime(payload)

    assert first == second == [_DONGTIAN_PLACE_ANCHORS[0]]
    assert calls == ["plan"]


def test_dongtian_initial_clear_plan_action_power_is_consumed_once(monkeypatch):
    calls = []
    runner = BehaviorTreeRuntimeRunner()
    payload = {
        "__dongtian_runtime_snapshot": {
            "available": True,
            "complete": True,
            "action_power": 300,
            "evidence": {},
        },
    }
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.dongtian.read_dongtian_action_power_snapshot",
        lambda: calls.append("action") or {
            "available": True,
            "complete": True,
            "action_power": 200,
            "evidence": {},
        },
    )

    first, _ = runner._daily_dongtian_action_power(object(), payload)
    second, _ = runner._daily_dongtian_action_power(object(), payload)

    assert (first, second) == (300, 200)
    assert calls == ["action"]


def test_dongtian_action_power_finishes_after_battle_started_with_last_100(monkeypatch):
    runner = BehaviorTreeRuntimeRunner()
    powers = iter([(100, "100")])
    battles = []
    scheduled = []

    class Runtime:
        def current_scene(self, candidates, update=False):
            assert candidates == [341, 279]
            assert update is True
            return 341, 100.0, "frame"

    def finish_battle(_runtime):
        battles.append("finished")
        if False:
            yield None

    monkeypatch.setattr(runner, "_daily_dongtian_action_power", lambda _runtime, _payload: next(powers))
    monkeypatch.setattr(runner, "_daily_dongtian_continue_enemy_occupation", finish_battle)
    monkeypatch.setattr(runner, "_persist_scheduler_task_next_time", lambda task_id, next_time: scheduled.append((task_id, next_time)))

    task = runner._daily_dongtian_clear_action_power_loop(Runtime(), None, {})
    try:
        while True:
            next(task)
    except StopIteration as done:
        result = done.value

    assert result == "success"
    assert battles == ["finished"]
    assert scheduled and scheduled[0][0] == "legacy-daily-dongtian-clear"


def test_dongtian_action_power_keeps_final_battle_visual_error_as_failure(monkeypatch):
    runner = BehaviorTreeRuntimeRunner()
    scheduled = []

    class Runtime:
        def current_scene(self, candidates, update=False):
            assert candidates == [341, 279]
            assert update is True
            return 341, 100.0, "frame"

    def fail_after_battle_started(_runtime):
        if False:
            yield None
        raise RuntimeError("ADB black frame")

    monkeypatch.setattr(
        runner,
        "_daily_dongtian_runtime_snapshot",
        lambda _payload: {"available": True, "complete": True, "action_power": 100},
    )
    monkeypatch.setattr(runner, "_daily_dongtian_continue_enemy_occupation", fail_after_battle_started)
    monkeypatch.setattr(
        runner,
        "_persist_scheduler_task_next_time",
        lambda task_id, next_time: scheduled.append((task_id, next_time)),
    )

    task = runner._daily_dongtian_clear_action_power_loop(Runtime(), None, {})
    with pytest.raises(RuntimeError, match="ADB black frame"):
        while True:
            next(task)

    assert scheduled == []


def test_dongtian_retry_short_circuits_when_runtime_already_below_100(monkeypatch):
    runner = BehaviorTreeRuntimeRunner()
    scheduled = []
    monkeypatch.setattr(
        runner,
        "_daily_dongtian_action_power",
        lambda _runtime, _payload: (
            0,
            "runtime:XianLvMinesMgr.Model.Data.V_AttackFatigueValue",
        ),
    )
    monkeypatch.setattr(
        runner,
        "_persist_scheduler_task_next_time",
        lambda task_id, next_time: scheduled.append((task_id, next_time)),
    )

    result = runner._daily_dongtian_complete_from_runtime_if_proven({})

    assert result == "success"
    assert scheduled and scheduled[0][0] == "legacy-daily-dongtian-clear"


def _finish_generator(generator):
    try:
        while True:
            next(generator)
    except StopIteration as done:
        return done.value


def test_dongtian_detail_returns_canonical_name_after_fuzzy_dynamic_title_match():
    class Runtime:
        def wait_view(self, scene_id, *, label):
            assert scene_id == 341
            assert "白玉京" in label
            if False:
                yield None

        def shape(self, scene_id, title):
            assert (scene_id, title) == (341, "地点名称")
            return object()

        def cur_frame(self, *, update=False):
            assert update is True
            return "frame-341"

        def ocr_fragments_in_shapes(self, scene_id, titles, *, frame_data_url):
            assert (scene_id, titles, frame_data_url) == (341, ["地点名称"], "frame-341")
            return [{"text": "白玉亰"}]

        def ocr_text(self, **_kwargs):
            raise AssertionError("不得再用 #341 场景标识 ROI 的编队文字核对地点")

    result = _finish_generator(
        BehaviorTreeRuntimeRunner()._daily_dongtian_validate_enemy_detail(Runtime(), "白玉京", {})
    )

    assert result == "白玉京"


def test_dongtian_detail_mismatch_returns_home_and_fails_closed():
    clicks = []

    class Runtime:
        def wait_view(self, *_args, **_kwargs):
            if False:
                yield None

        def shape(self, *_args):
            return object()

        def cur_frame(self, *, update=False):
            return "frame-341"

        def ocr_fragments_in_shapes(self, *_args, **_kwargs):
            return [{"text": "月虹梁"}]

        def wait_click_then_view(self, scene_id, title, target_scene_id):
            clicks.append((scene_id, title, target_scene_id))
            if False:
                yield None

    with pytest.raises(RuntimeError, match="顶部地点标题不一致"):
        _finish_generator(
            BehaviorTreeRuntimeRunner()._daily_dongtian_validate_enemy_detail(Runtime(), "白玉京", {})
        )

    assert clicks == [(341, "返回", 279)]


def test_dongtian_enemy_click_uses_runtime_aligned_name_box_not_global_template_offset():
    clicks = []

    class Shape:
        def __init__(self, box):
            self._box = box

        def box(self):
            return dict(self._box)

    class Runtime:
        payload = {}

        def view(self, scene_id):
            assert scene_id == 279
            return object()

        def shape(self, scene_id, title):
            assert scene_id == 279
            if title == "窗口":
                return Shape({"x": 0, "y": 0, "w": 900, "h": 1400})
            if title == "我的编队":
                return Shape({"x": 650, "y": 300, "w": 240, "h": 500})
            raise AssertionError(f"不应再读取全局地点模板：{title}")

        def wait_view(self, scene_id, *, label):
            assert scene_id == 279
            assert "洞天福地" in label
            if False:
                yield None

        def cur_frame(self, *, update=False):
            assert update is True
            return "frame-279"

        def ocr_fragments_in_shapes(self, scene_id, titles, *, frame_data_url):
            assert (scene_id, titles, frame_data_url) == (279, ["窗口"], "frame-279")
            return [{"text": "白玉京", "x": 420, "y": 640, "w": 60, "h": 30, "line_id": "line-1"}]

        def ocr_tokens_in_shapes(self, scene_id, titles, *, frame_data_url):
            assert (scene_id, titles, frame_data_url) == (279, ["窗口"], "frame-279")
            return [{
                "text": "白玉京",
                "x": 420,
                "y": 640,
                "w": 60,
                "h": 30,
                "parent_line_id": "line-1",
            }]

        def click_frame_point(self, scene_id, x, y):
            clicks.append((scene_id, x, y))

        def wait_action_settle(self, seconds):
            assert seconds == 2.0
            if False:
                yield None

    result = _finish_generator(
        BehaviorTreeRuntimeRunner()._daily_dongtian_click_first_enemy_place(
            Runtime(),
            threading.Event(),
            ["白玉京"],
            max_scrolls=0,
        )
    )

    assert result == "白玉京"
    assert clicks == [(279, 451.5, 555.0)]


@pytest.mark.parametrize("place", _DONGTIAN_PLACE_ANCHORS)
def test_dongtian_location_box_accepts_every_known_exact_place_name(place):
    runner = BehaviorTreeRuntimeRunner()
    normalized = runner._daily_dongtian_normalize_place_name(place)
    prefix = "[福地]" if place.startswith("[福地]") else "[洞天]" if place.startswith("[洞天]") else ""
    line = {
        "text": f"{prefix}{normalized}",
        "x": 100,
        "y": 200,
        "w": 120,
        "h": 30,
        "line_id": "known-place",
    }
    tokens = [{
        "text": normalized,
        "x": 110,
        "y": 200,
        "w": 100,
        "h": 30,
        "parent_line_id": "known-place",
    }]

    assert runner._daily_dongtian_location_box(line, tokens, normalized) == {
        "x": 110,
        "y": 200,
        "w": 100,
        "h": 30,
    }


def test_dongtian_location_box_rejects_same_prefix_different_place():
    runner = BehaviorTreeRuntimeRunner()
    line = {
        "text": "[福地]月虹窟",
        "x": 100,
        "y": 200,
        "w": 120,
        "h": 30,
        "line_id": "moon-rainbow-cave",
    }

    assert runner._daily_dongtian_location_box(line, [], "月虹梁") is None


def test_dongtian_location_click_point_rejects_fixed_header_hotspot():
    runner = BehaviorTreeRuntimeRunner()
    window = {"x": 13, "y": 160, "w": 872, "h": 1162}

    assert runner._daily_dongtian_location_click_point(
        {"x": 730, "y": 188, "w": 97, "h": 32},
        window,
    ) is None
    assert runner._daily_dongtian_location_click_point(
        {"x": 730, "y": 388, "w": 97, "h": 32},
        window,
    ) == (780.0, 304.0)


def test_dongtian_place_locator_reverses_after_down_boundary_and_finds_target_upward():
    clicks = []
    scrolls = []
    moved_up = False

    class Shape:
        def __init__(self, box):
            self._box = box

        def box(self):
            return dict(self._box)

    class Runtime:
        payload = {}

        def view(self, scene_id):
            assert scene_id == 279
            return object()

        def shape(self, scene_id, title):
            assert scene_id == 279
            return Shape({"x": 650, "y": 300, "w": 240, "h": 500}) if title == "我的编队" else Shape({"x": 0, "y": 0, "w": 900, "h": 1400})

        def wait_view(self, scene_id, *, label):
            assert scene_id == 279
            if False:
                yield None

        def cur_frame(self, *, update=False):
            assert update is True
            return "frame-279"

        def ocr_fragments_in_shapes(self, *_args, **_kwargs):
            if not moved_up:
                return [{"text": "[福地]盖竹山", "x": 200, "y": 900, "w": 90, "h": 30, "line_id": "bottom"}]
            return [{"text": "[洞天]璇霄崖", "x": 200, "y": 900, "w": 90, "h": 30, "line_id": "target"}]

        def ocr_tokens_in_shapes(self, *_args, **_kwargs):
            return []

        def scroll_shape_content(self, _view, _shape, *, direction):
            nonlocal moved_up
            scrolls.append(direction)
            if direction == "up":
                moved_up = True
                if False:
                    yield None
                return True
            if False:
                yield None
            return False

        def click_frame_point(self, scene_id, x, y):
            clicks.append((scene_id, x, y))

        def wait_action_settle(self, _seconds):
            if False:
                yield None

    result = _finish_generator(
        BehaviorTreeRuntimeRunner()._daily_dongtian_click_place(
            Runtime(),
            threading.Event(),
            ["[洞天]璇霄崖"],
            max_scrolls=2,
        )
    )

    assert result == "[洞天]璇霄崖"
    assert scrolls == ["down", "up"]
    assert len(clicks) == 1
