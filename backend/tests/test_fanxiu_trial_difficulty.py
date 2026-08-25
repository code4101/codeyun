from __future__ import annotations

import base64
import io
import threading
from types import SimpleNamespace

import pytest
from PIL import Image

from backend.core.fanxiu.data_annotation.trial_difficulty import (
    ObservedTrialDifficulty,
    TRIAL_DIFFICULTY_AXES,
    build_even_trial_difficulty_plan,
    find_current_trial_difficulty,
)
from backend.core.fanxiu.data_annotation.trial_progression import (
    ObservedTrialAttempts,
    ObservedTrialHomeState,
    parse_xianqiao_trial_attempts,
)
from backend.core.fanxiu.instrumentation.xianqiao import (
    select_xianqiao_trial_drop_element,
)
from backend.core.fanxiu.instrumentation.runtime_memory import LuaRef
from backend.core.fanxiu.behavior_tree.runtime import create_behavior_tree_runtime_runner


def _finish(generator):
    while True:
        try:
            next(generator)
        except StopIteration as stop:
            return stop.value


def test_even_trial_difficulty_model_matches_known_levels():
    level_25 = build_even_trial_difficulty_plan(25)
    level_26 = build_even_trial_difficulty_plan(26)

    assert level_25.positions == (5, 5, 5, 5, 4)
    assert level_25.values == (10, 10, 15, 10, 40)
    assert level_26.positions == (5, 5, 5, 5, 5)
    assert level_26.values == (10, 10, 15, 10, 50)


def test_current_trial_difficulty_parser_uses_the_live_display_text():
    observation = find_current_trial_difficulty(
        [{"text": "当前难度为25级，完成挑战可得以上奖励"}]
    )

    assert observation == ObservedTrialDifficulty(
        level=25,
        text="当前难度为25级，完成挑战可得以上奖励",
    )


def test_trial_settings_orchestration_keeps_the_business_order(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    runtime = runner._fanxiu_runtime(
        {"images": {358: {"id": 358, "title": "设置难度", "width": 900, "height": 1600, "shapes": []}}},
        stop_event=threading.Event(),
    )
    events: list[str] = []

    def configure_drop(*_args, **_kwargs):
        events.append("drop_water")
        if False:
            yield None
        return {"element": "水", "desired_counts": {"金": 9, "水": 3, "火": 4}}

    def allocate(*_args, **_kwargs):
        events.append("five_elements")
        if False:
            yield None
        return {"after": {"remaining": 0}}

    def read(*_args, **_kwargs):
        events.append("read_current")
        return ObservedTrialDifficulty(level=25, text="当前难度为25级")

    def configure(_view, target_level, **_kwargs):
        events.append(f"configure_{target_level}")
        if False:
            yield None
        return {"final_level": target_level}

    monkeypatch.setattr(runtime, "configure_xianqiao_trial_drop_element", configure_drop)
    monkeypatch.setattr(runtime, "allocate_balanced_points", allocate)
    monkeypatch.setattr(runtime, "read_current_trial_difficulty", read)
    monkeypatch.setattr(runtime, "configure_even_trial_difficulty", configure)

    result = _finish(runtime.prepare_xianqiao_trial_settings(358))

    assert events == ["drop_water", "five_elements", "read_current", "configure_26"]
    assert result["drop_element"]["element"] == "水"
    assert result["current_level"] == 25
    assert result["target_level"] == 26
    assert result["difficulty"] == {"final_level": 26}


def test_trial_settings_accepts_absolute_target_after_a_failed_higher_configuration(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    runtime = runner._fanxiu_runtime(
        {"images": {358: {"id": 358, "title": "设置难度", "width": 900, "height": 1600, "shapes": []}}},
        stop_event=threading.Event(),
    )
    configured: list[int] = []

    def allocate(*_args, **_kwargs):
        if False:
            yield None
        return {"after": {"remaining": 0}}

    def configure(_view, target_level, **_kwargs):
        configured.append(target_level)
        if False:
            yield None
        return {"final_level": target_level}

    def configure_drop(*_args, **_kwargs):
        if False:
            yield None
        return {"element": "水"}

    monkeypatch.setattr(runtime, "configure_xianqiao_trial_drop_element", configure_drop)
    monkeypatch.setattr(runtime, "allocate_balanced_points", allocate)
    monkeypatch.setattr(
        runtime,
        "read_current_trial_difficulty",
        lambda *_args, **_kwargs: ObservedTrialDifficulty(level=40, text="当前难度为40级"),
    )
    monkeypatch.setattr(runtime, "configure_even_trial_difficulty", configure)

    result = _finish(runtime.prepare_xianqiao_trial_settings(358, target_level=36))

    assert result["current_level"] == 40
    assert result["target_level"] == 36
    assert configured == [36]


def test_trial_settings_retries_transient_current_difficulty_in_same_transaction(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    runtime = runner._fanxiu_runtime(
        {"images": {358: {"id": 358, "title": "设置难度", "width": 900, "height": 1600, "shapes": []}}},
        stop_event=threading.Event(),
    )
    reads = iter(
        (
            RuntimeError("未从当前画面读取到“当前难度为 N 级”"),
            ObservedTrialDifficulty(level=25, text="当前难度为25级"),
        )
    )
    waits: list[float] = []
    logs: list[tuple[str, str]] = []

    def no_op_generator(*_args, **_kwargs):
        if False:
            yield None
        return {}

    def configure(_view, target_level, **_kwargs):
        if False:
            yield None
        return {"final_level": target_level}

    def read(*_args, **_kwargs):
        result = next(reads)
        if isinstance(result, Exception):
            raise result
        return result

    def settle(seconds):
        waits.append(float(seconds))
        if False:
            yield None

    monkeypatch.setattr(runtime, "configure_xianqiao_trial_drop_element", no_op_generator)
    monkeypatch.setattr(runtime, "allocate_balanced_points", no_op_generator)
    monkeypatch.setattr(runtime, "read_current_trial_difficulty", read)
    monkeypatch.setattr(runtime, "configure_even_trial_difficulty", configure)
    monkeypatch.setattr(runtime, "wait_action_settle", settle)
    monkeypatch.setattr(runner, "_log", lambda kind, message: logs.append((kind, message)))

    result = _finish(runtime.prepare_xianqiao_trial_settings(358, settle_seconds=0.25))

    assert result["current_level"] == 25
    assert result["target_level"] == 26
    assert waits == [0.25]
    assert any("原地刷新重试 2/3" in message for _kind, message in logs)


def test_xianqiao_trial_drop_element_uses_least_desired_equipped_count():
    assert select_xianqiao_trial_drop_element({1: 9, 2: 1, 3: 3, 4: 4, 5: 1}) == {
        "element_id": 3,
        "element": "水",
        "desired_counts": {"金": 9, "水": 3, "火": 4},
    }


def test_xianqiao_trial_drop_element_tie_break_is_deterministic():
    assert select_xianqiao_trial_drop_element({1: 2, 3: 2, 4: 2})["element"] == "金"


def test_xianqiao_snapshot_counts_only_worn_items_in_newest_active_system(monkeypatch):
    from backend.core.fanxiu.instrumentation import xianqiao as xianqiao_module

    core_main = LuaRef("table", 1)
    old_parts = LuaRef("table", 2)
    current_parts = LuaRef("table", 3)
    old_part = LuaRef("table", 10)
    old_equip = LuaRef("table", 11)
    old_elements = LuaRef("table", 12)
    ignored_part = LuaRef("table", 20)
    ignored_equip = LuaRef("table", 21)
    ignored_elements = LuaRef("table", 22)
    current_part_a = LuaRef("table", 30)
    current_equip_a = LuaRef("table", 31)
    current_elements_a = LuaRef("table", 32)
    current_part_b = LuaRef("table", 40)
    current_equip_b = LuaRef("table", 41)
    current_elements_b = LuaRef("table", 42)
    empty_side_attrs = LuaRef("table", 99)

    class FakeReader:
        tables = {
            1: {"fields": {1: old_parts, 2: current_parts}, "array": [None]},
            2: {"fields": {1: old_part}, "array": [None]},
            3: {
                "fields": {1: ignored_part, 2: current_part_a, 3: current_part_b},
                "array": [None],
            },
        }
        fields_by_address = {
            10: {"coreEquipVO": old_equip},
            11: {"wear": True, "baseId": 1001, "level": 25, "elements": old_elements, "sideAttrVOList": empty_side_attrs},
            20: {"coreEquipVO": ignored_equip},
            21: {"wear": False, "baseId": 2001, "level": 30, "elements": ignored_elements, "sideAttrVOList": empty_side_attrs},
            30: {"coreEquipVO": current_equip_a},
            31: {"wear": True, "baseId": 2002, "level": 20, "elements": current_elements_a, "sideAttrVOList": empty_side_attrs},
            40: {"coreEquipVO": current_equip_b},
            41: {"wear": True, "baseId": 2003, "level": 10, "elements": current_elements_b, "sideAttrVOList": empty_side_attrs},
        }
        lists = {
            12: [1, 1, 3],
            22: [1, 1, 1, 1, 1, 1],
            32: [4, 4, 3, 1],
            42: [5, 2],
            99: [],
        }

        def __init__(self, _memory):
            pass

        def table(self, address):
            return self.tables[address]

        def fields(self, value):
            if not isinstance(value, LuaRef):
                return {}
            return self.fields_by_address.get(value.address, {})

        def list_items(self, value):
            items = self.lists.get(value.address, []) if isinstance(value, LuaRef) else []
            return list(items), len(items)

    monkeypatch.setattr(xianqiao_module, "LuaJitReader", FakeReader)
    monkeypatch.setattr(
        xianqiao_module,
        "_xianqiao_data_fields",
        lambda _reader, _root: {"CoreMainDic": core_main},
    )
    memory = SimpleNamespace(pid=123, process_start_ticks=456)

    snapshot = xianqiao_module._snapshot(
        memory,
        0x1000,
        root_cache_hit=False,
        state_address=0x2000,
        environment_address=0x3000,
    )

    assert snapshot["complete"] is True
    assert snapshot["active_system_type"] == 2
    assert snapshot["worn_parts"] == 2
    assert snapshot["element_counts_by_id"] == {1: 1, 2: 1, 3: 1, 4: 2, 5: 1}
    assert snapshot["systems"][0]["element_counts_by_id"] == {1: 2, 2: 0, 3: 1, 4: 0, 5: 0}


def test_current_trial_difficulty_falls_back_to_existing_bounded_shape(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image358 = {
        "id": 358,
        "title": "设置难度",
        "width": 900,
        "height": 1600,
        "shapes": [{"id": "current", "title": "当前难度", "x": 0.58, "y": 0.17, "w": 0.28, "h": 0.03}],
    }
    runtime = runner._fanxiu_runtime({"images": {358: image358}}, stop_event=threading.Event())
    source = Image.new("RGB", (900, 1600), color="white")
    buffer = io.BytesIO()
    source.save(buffer, format="PNG")
    frame = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(runner, "_shared_spatial_ocr_result", lambda *_args: {"tokens": []})
    original_fragments = runner._ocr_fragments_in_shapes

    def bounded_fragments(frame_data_url, image, shapes, **kwargs):
        calls.append({"ctx": kwargs.get("ctx"), "frame": frame_data_url, "shapes": shapes})
        return original_fragments(frame_data_url, image, shapes, **kwargs)

    def recognize_crop(frame_data_url, *, options=None):
        header, encoded = frame_data_url.split(",", 1)
        assert header == "data:image/png;base64"
        with Image.open(io.BytesIO(base64.b64decode(encoded))) as cropped:
            calls[-1]["crop_size"] = cropped.size
        return {"lines": [{"text": "当前难度为52级", "x": 0, "y": 0}], "tokens": []}

    monkeypatch.setattr(runner, "_ocr_fragments_in_shapes", bounded_fragments)
    monkeypatch.setattr(runner, "_ocr_frame", recognize_crop)

    observation = runtime.read_current_trial_difficulty(358, frame_data_url=frame)

    assert observation == ObservedTrialDifficulty(level=52, text="当前难度为52级")
    assert calls == [
        {"ctx": None, "frame": frame, "shapes": ("当前难度",), "crop_size": (276, 72)}
    ]


def test_xianqiao_trial_drop_element_keeps_verified_current_target(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    runtime = runner._fanxiu_runtime(
        {"images": {358: {"id": 358, "title": "设置难度", "width": 900, "height": 1600, "shapes": []}}},
        stop_event=threading.Event(),
    )
    logs: list[tuple[str, str]] = []
    monkeypatch.setattr(runner, "_log", lambda kind, message: logs.append((kind, message)))
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.xianqiao.read_xianqiao_snapshot",
        lambda: {
            "complete": True,
            "active_system_type": 1,
            "worn_parts": 5,
            "element_counts_by_id": {1: 9, 3: 3, 4: 4},
        },
    )
    monkeypatch.setattr(runtime, "find_ocr_text", lambda *_args, **_kwargs: object())

    result = _finish(runtime.configure_xianqiao_trial_drop_element())

    assert result["element"] == "水"
    assert result["changed"] is False
    assert logs == [
        ("detail", "仙窍试炼掉落元素决策：当前已穿戴仙纹 金9、水3、火4，选择最少的「水」"),
        ("success", "#358 掉落元素已复核为「水」（原设置已正确）"),
    ]


def test_xianqiao_trial_drop_element_clicks_only_runtime_decided_ocr_option(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    runtime = runner._fanxiu_runtime(
        {"images": {358: {"id": 358, "title": "设置难度", "width": 900, "height": 1600, "shapes": []}}},
        stop_event=threading.Event(),
    )
    logs: list[tuple[str, str]] = []
    monkeypatch.setattr(runner, "_log", lambda kind, message: logs.append((kind, message)))
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.xianqiao.read_xianqiao_snapshot",
        lambda: {
            "complete": True,
            "active_system_type": 1,
            "worn_parts": 5,
            "element_counts_by_id": {1: 9, 3: 3, 4: 4},
        },
    )
    reads = iter((None, SimpleNamespace(point=lambda: (700.0, 600.0)), object()))
    clicks: list[tuple[object, ...]] = []
    monkeypatch.setattr(runtime, "find_ocr_text", lambda *_args, **_kwargs: next(reads))
    monkeypatch.setattr(runtime, "click_shape", lambda *args, **_kwargs: clicks.append(("shape", *args)))
    monkeypatch.setattr(runtime, "click_frame_point", lambda *args, **_kwargs: clicks.append(("point", *args)))

    result = _finish(runtime.configure_xianqiao_trial_drop_element(settle_seconds=0))

    assert result["element"] == "水"
    assert result["changed"] is True
    assert any(click[0] == "shape" for click in clicks)
    assert any(click[0] == "point" and click[-2:] == (700.0, 600.0) for click in clicks)
    assert logs[-1] == ("success", "#358 掉落元素已复核为「水」（已切换）")


def test_trial_difficulty_final_verification_tolerates_a_transient_empty_ocr_frame(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    runtime = runner._fanxiu_runtime(
        {"images": {358: {"id": 358, "title": "设置难度", "width": 900, "height": 1600, "shapes": []}}},
        stop_event=threading.Event(),
    )
    reads = iter((
        RuntimeError("未从当前画面读取到“当前难度为 N 级”"),
        ObservedTrialDifficulty(level=25, text="当前难度为25级"),
    ))
    waits: list[float] = []

    monkeypatch.setattr(
        runner,
        "_shared_spatial_ocr_result",
        lambda *_args, **_kwargs: {
            "tokens": [
                {
                    "text": axis.label,
                    "x": 1,
                    "y": index * 10,
                    "w": 10,
                    "h": 5,
                    "parent_line_id": f"line-{index}",
                    "line_order": index,
                    "order": 0,
                }
                for index, axis in enumerate(TRIAL_DIFFICULTY_AXES)
            ]
        },
    )
    monkeypatch.setattr(runtime, "cur_frame", lambda **_kwargs: "frame")

    def set_slider(*_args, **_kwargs):
        if False:
            yield None
        return {"verified": True}

    def read(*_args, **_kwargs):
        result = next(reads)
        if isinstance(result, Exception):
            raise result
        return result

    def settle(seconds):
        waits.append(float(seconds))
        if False:
            yield None

    monkeypatch.setattr(runtime, "set_slider_value", set_slider)
    monkeypatch.setattr(runtime, "read_current_trial_difficulty", read)
    monkeypatch.setattr(runtime, "wait_action_settle", settle)

    result = _finish(runtime.configure_even_trial_difficulty(358, 25, settle_seconds=0.4))

    assert result["final_level"] == 25
    assert waits == [0.4]


def test_trial_scroll_can_use_safe_cross_axis_band(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    runtime = runner._fanxiu_runtime(
        {
            "images": {
                358: {
                    "id": 358,
                    "title": "设置难度",
                    "width": 900,
                    "height": 1600,
                    "shapes": [
                        {"title": "难度窗口", "x": 0.1, "y": 0.2, "w": 0.8, "h": 0.4}
                    ],
                }
            }
        },
        stop_event=threading.Event(),
    )
    points: list[tuple[float, float, float, float]] = []
    monkeypatch.setattr(
        runner,
        "_drag_frame_point",
        lambda _ctx, _view, x1, y1, x2, y2, **_kwargs: points.append((x1, y1, x2, y2)),
    )

    runtime.drag_shape_content(
        358,
        "难度窗口",
        direction="down",
        ratio=0.5,
        cross_axis_ratio=0.92,
    )

    assert len(points) == 1
    expected_x = (0.1 + 0.8 * 0.92) * 900
    assert points[0][0] == pytest.approx(expected_x)
    assert points[0][2] == pytest.approx(expected_x)


def _trial_challenge_runtime():
    runner = create_behavior_tree_runtime_runner()
    images = {
        scene_id: {
            "id": scene_id,
            "title": title,
            "width": 900,
            "height": 1600,
            "shapes": [{"title": shape, "x": 0.4, "y": 0.6, "w": 0.2, "h": 0.05}],
        }
        for scene_id, title, shape in (
            (357, "仙窍试炼", "挑战"),
            (359, "挑战确认", "开始挑战"),
            (360, "难度确认", "继续挑战"),
            (366, "扫荡确认", "开启扫荡"),
        )
    }
    images[34] = {
        "id": 34,
        "title": "世界",
        "width": 900,
        "height": 1600,
        "shapes": [],
    }
    return runner._fanxiu_runtime({"images": images}, stop_event=threading.Event())


def test_trial_challenge_reacts_to_each_scene_instead_of_difficulty_history(monkeypatch):
    runtime = _trial_challenge_runtime()
    observations = iter((357, 357, 359, 359, 360))
    clicks: list[tuple[int, str]] = []
    claims: list[tuple[int, ...]] = []

    def current_scene(*_args, **_kwargs):
        claims.append(runtime.active_business_view_ids())
        return next(observations), 100.0, "frame"

    monkeypatch.setattr(
        runtime,
        "current_scene",
        current_scene,
    )
    monkeypatch.setattr(
        runtime,
        "click_shape",
        lambda view, shape, **_kwargs: clicks.append((int(view), str(shape))),
    )
    result = _finish(runtime.start_xianqiao_trial_challenge(settle_seconds=0))

    assert clicks == [(357, "挑战"), (359, "开始挑战"), (360, "继续挑战")]
    assert claims == [(359, 360, 366, 227, 367)] * 5
    assert runtime.active_business_view_ids() == ()
    assert result["exit_reason"] == "continue_confirmed"


def test_trial_challenge_can_resume_directly_from_optional_confirmation(monkeypatch):
    runtime = _trial_challenge_runtime()
    observations = iter((359, 360))
    clicks: list[tuple[int, str]] = []

    monkeypatch.setattr(
        runtime,
        "current_scene",
        lambda *_args, **_kwargs: (next(observations), 100.0, "frame"),
    )
    monkeypatch.setattr(
        runtime,
        "click_shape",
        lambda view, shape, **_kwargs: clicks.append((int(view), str(shape))),
    )
    monkeypatch.setattr(runtime, "cur_frame", lambda **_kwargs: "reward-frame")

    result = _finish(runtime.start_xianqiao_trial_challenge(settle_seconds=0))

    assert clicks == [(359, "开始挑战"), (360, "继续挑战")]
    assert result["exit_reason"] == "continue_confirmed"


def test_trial_challenge_handles_sweep_as_an_observed_branch(monkeypatch):
    runtime = _trial_challenge_runtime()
    runtime.ctx["images"].update({
        227: {
            "id": 227,
            "title": "副本扫荡结果",
            "width": 900,
            "height": 1600,
            "shapes": [{"title": "继续", "x": 0.4, "y": 0.8, "w": 0.2, "h": 0.05}],
        },
        367: {"id": 367, "title": "扫荡奖励", "width": 900, "height": 1600, "shapes": []},
    })
    observations = iter((357, 366))
    landed = iter((227, 227, 357))
    clicks: list[tuple[int, str]] = []
    delays: list[float] = []

    monkeypatch.setattr(
        runtime,
        "current_scene",
        lambda *_args, **_kwargs: (next(observations), 100.0, "frame"),
    )
    monkeypatch.setattr(
        runtime,
        "click_shape",
        lambda view, shape, **_kwargs: clicks.append((int(view), str(shape))),
    )
    monkeypatch.setattr(runtime, "cur_frame", lambda **_kwargs: "reward-frame")

    def wait_home(*_args, **_kwargs):
        if False:
            yield None
        return runtime.view(next(landed))

    def settle(seconds):
        delays.append(float(seconds))
        if False:
            yield None

    monkeypatch.setattr(runtime, "wait_view", wait_home)
    monkeypatch.setattr(runtime, "wait_action_settle", settle)

    result = _finish(
        runtime.start_xianqiao_trial_challenge(
            settle_seconds=0,
            sweep_result_delay=5,
        )
    )

    assert clicks == [
        (357, "挑战"),
        (366, "开启扫荡"),
        (227, "继续"),
        (227, "继续"),
    ]
    assert delays == [0.0, 5.0, 0.0, 0.0]
    assert result["exit_reason"] == "sweep_completed"
    assert result["last_scene"] == 357


def test_trial_challenge_accepts_direct_entry_when_no_confirmation_appears(monkeypatch):
    runtime = _trial_challenge_runtime()
    observations = iter((357, None, None, None))
    clicks: list[tuple[int, str]] = []

    monkeypatch.setattr(
        runtime,
        "current_scene",
        lambda *_args, **_kwargs: (next(observations), 0.0, "frame"),
    )
    monkeypatch.setattr(
        runtime,
        "click_shape",
        lambda view, shape, **_kwargs: clicks.append((int(view), str(shape))),
    )

    result = _finish(
        runtime.start_xianqiao_trial_challenge(
            stable_departure_polls=3,
            settle_seconds=0,
        )
    )

    assert clicks == [(357, "挑战")]
    assert result["exit_reason"] == "left_confirmation_chain"


def test_trial_result_treats_362_as_battle_and_waits_for_success_exit(monkeypatch):
    runtime = _trial_challenge_runtime()
    runtime.ctx["images"].update({
        361: {
            "id": 361,
            "title": "成功结算",
            "width": 900,
            "height": 1600,
            "shapes": [{"title": "退出", "x": 0.4, "y": 0.8, "w": 0.2, "h": 0.05}],
        },
        362: {
            "id": 362,
            "title": "仙窍战斗中",
            "width": 900,
            "height": 1600,
            "shapes": [],
        },
        365: {
            "id": 365,
            "title": "失败结算",
            "width": 900,
            "height": 1600,
            "shapes": [{"title": "退出", "x": 0.4, "y": 0.8, "w": 0.2, "h": 0.05}],
        },
    })
    events: list[str] = []
    waited = iter((362, 361))

    def wait_view(*_args, **_kwargs):
        value = next(waited)
        events.append("entered_362" if value == 362 else "result_361")
        if False:
            yield None
        return runtime.view(value)

    monkeypatch.setattr(runtime, "wait_view", wait_view)
    monkeypatch.setattr(runtime, "cur_frame", lambda **_kwargs: "result-frame")
    monkeypatch.setattr(runtime, "ocr_text", lambda **_kwargs: "挑战成功 点击退出")

    result = _finish(runtime.wait_xianqiao_trial_result(result_settle_seconds=0))

    assert events == ["entered_362", "result_361"]
    assert result["outcome"] == "success"
    assert result["result_scene"] == 361


def test_trial_result_recognizes_failure_by_scene_identity(monkeypatch):
    runtime = _trial_challenge_runtime()
    runtime.ctx["images"].update({
        361: {"id": 361, "title": "成功结算", "width": 900, "height": 1600, "shapes": [{"title": "退出"}]},
        362: {"id": 362, "title": "仙窍战斗中", "width": 900, "height": 1600, "shapes": []},
        365: {"id": 365, "title": "失败结算", "width": 900, "height": 1600, "shapes": [{"title": "退出"}]},
    })
    waited = iter((362, 365))

    def immediate_wait(*_args, **_kwargs):
        if False:
            yield None
        return runtime.view(next(waited))

    monkeypatch.setattr(runtime, "wait_view", immediate_wait)
    monkeypatch.setattr(runtime, "cur_frame", lambda **_kwargs: "failure-frame")
    monkeypatch.setattr(runtime, "ocr_text", lambda **_kwargs: "挑战失败")
    clicks: list[tuple[int, str]] = []
    monkeypatch.setattr(runtime, "click_shape", lambda view, shape, **_kwargs: clicks.append((int(view), str(shape))))

    result = _finish(runtime.wait_xianqiao_trial_result(result_settle_seconds=0))

    assert result["outcome"] == "failure"
    assert result["result_scene"] == 365
    assert result["ocr_text"] == "挑战失败"
    assert clicks == []


def test_complete_trial_challenge_clicks_exit_only_for_known_success(monkeypatch):
    runtime = _trial_challenge_runtime()
    runtime.ctx["images"].update({
        361: {"id": 361, "title": "成功结算", "width": 900, "height": 1600, "shapes": [{"title": "退出"}]},
        362: {"id": 362, "title": "仙窍战斗中", "width": 900, "height": 1600, "shapes": []},
        365: {"id": 365, "title": "失败结算", "width": 900, "height": 1600, "shapes": [{"title": "退出"}]},
    })
    clicks: list[tuple[int, str]] = []

    def started(**_kwargs):
        if False:
            yield None
        return {"exit_reason": "continue_confirmed"}

    def finished(**_kwargs):
        if False:
            yield None
        return {"outcome": "success", "result_scene": 361, "_frame_data_url": "result-frame"}

    def wait_home(*_args, **_kwargs):
        if False:
            yield None
        return runtime.view(357)

    monkeypatch.setattr(runtime, "start_xianqiao_trial_challenge", started)
    monkeypatch.setattr(runtime, "wait_xianqiao_trial_result", finished)
    monkeypatch.setattr(runtime, "wait_view", wait_home)
    monkeypatch.setattr(runtime, "click_shape", lambda view, shape, **_kwargs: clicks.append((int(view), str(shape))))

    result = _finish(runtime.complete_xianqiao_trial_challenge(settle_seconds=0))

    assert clicks == [(361, "退出")]
    assert result["returned_home"] is True
    assert result["landing_scene"] == 357
    assert result["reentered_from_world"] is False


def test_complete_trial_challenge_reenters_when_failure_exit_lands_on_world(monkeypatch):
    runtime = _trial_challenge_runtime()
    runtime.ctx["images"].update({
        361: {"id": 361, "title": "成功结算", "width": 900, "height": 1600, "shapes": [{"title": "退出"}]},
        362: {"id": 362, "title": "仙窍战斗中", "width": 900, "height": 1600, "shapes": []},
        365: {"id": 365, "title": "失败结算", "width": 900, "height": 1600, "shapes": [{"title": "退出"}]},
    })
    events: list[object] = []

    def started(**_kwargs):
        if False:
            yield None
        return {"exit_reason": "continue_confirmed"}

    def finished(**_kwargs):
        if False:
            yield None
        return {"outcome": "failure", "result_scene": 365, "_frame_data_url": "failure-frame"}

    def wait_landing(*views, **_kwargs):
        events.append(("wait", tuple(int(view) for view in views)))
        if False:
            yield None
        return runtime.view(34)

    def reenter(**kwargs):
        events.append(("reenter", int(kwargs["trial_view"])))
        if False:
            yield None
        return {"terminal_scene": 357}

    monkeypatch.setattr(runtime, "start_xianqiao_trial_challenge", started)
    monkeypatch.setattr(runtime, "wait_xianqiao_trial_result", finished)
    monkeypatch.setattr(runtime, "wait_view", wait_landing)
    monkeypatch.setattr(runtime, "enter_xianqiao_trial", reenter)
    monkeypatch.setattr(
        runtime,
        "click_shape",
        lambda view, shape, **_kwargs: events.append(("click", int(view), str(shape))),
    )

    result = _finish(runtime.complete_xianqiao_trial_challenge(settle_seconds=0))

    assert events == [
        ("click", 365, "退出"),
        ("wait", (357, 34)),
        ("reenter", 357),
    ]
    assert result["returned_home"] is True
    assert result["landing_scene"] == 34
    assert result["reentered_from_world"] is True


def test_complete_trial_challenge_reenters_after_unobserved_result(monkeypatch):
    runtime = _trial_challenge_runtime()
    reentries: list[int] = []

    def started(**_kwargs):
        if False:
            yield None
        return {"exit_reason": "continue_confirmed"}

    def expired(**_kwargs):
        if False:
            yield None
        return {"outcome": "result_expired", "result_scene": 34, "_frame_data_url": None}

    def reenter(**kwargs):
        reentries.append(int(kwargs["trial_view"]))
        if False:
            yield None
        return {"terminal_scene": 357}

    monkeypatch.setattr(runtime, "start_xianqiao_trial_challenge", started)
    monkeypatch.setattr(runtime, "wait_xianqiao_trial_result", expired)
    monkeypatch.setattr(runtime, "enter_xianqiao_trial", reenter)

    result = _finish(runtime.complete_xianqiao_trial_challenge(settle_seconds=0))

    assert reentries == [357]
    assert result["result"]["outcome"] == "result_expired"
    assert result["returned_home"] is True
    assert result["reentered_from_world"] is True


def test_complete_trial_challenge_treats_returned_sweep_as_terminal(monkeypatch):
    runtime = _trial_challenge_runtime()

    def swept(**_kwargs):
        if False:
            yield None
        return {"exit_reason": "sweep_completed", "last_scene": 357, "actions": []}

    monkeypatch.setattr(runtime, "start_xianqiao_trial_challenge", swept)
    monkeypatch.setattr(
        runtime,
        "wait_xianqiao_trial_result",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("扫荡后不应等待战斗结算")),
    )

    result = _finish(runtime.complete_xianqiao_trial_challenge(settle_seconds=0))

    assert result["result"] == {"outcome": "sweep", "result_scene": 357}
    assert result["returned_home"] is True


def test_trial_result_reports_auto_expired_popup_when_game_returns_to_world(monkeypatch):
    runtime = _trial_challenge_runtime()
    runtime.ctx["images"].update({
        361: {"id": 361, "title": "成功结算", "width": 900, "height": 1600, "shapes": [{"title": "退出"}]},
        362: {"id": 362, "title": "仙窍战斗中", "width": 900, "height": 1600, "shapes": []},
        365: {"id": 365, "title": "失败结算", "width": 900, "height": 1600, "shapes": [{"title": "退出"}]},
    })
    waited = iter((362, 34))

    def wait_view(*_args, **_kwargs):
        if False:
            yield None
        return runtime.view(next(waited))

    monkeypatch.setattr(runtime, "wait_view", wait_view)

    result = _finish(runtime.wait_xianqiao_trial_result())

    assert result["outcome"] == "result_expired"
    assert result["result_scene"] == 34


def test_trial_attempt_parser():
    assert parse_xianqiao_trial_attempts("今日剩余:奖励次数:2/5") == ObservedTrialAttempts(
        remaining=2,
        capacity=5,
        text="今日剩余:奖励次数:2/5",
    )
    assert parse_xianqiao_trial_attempts("剩余奖励次数：１／３").remaining == 1
    assert parse_xianqiao_trial_attempts("励次数:5/2").remaining == 5


def test_trial_home_observation_reuses_one_frame_for_attempts_and_sweep(monkeypatch):
    runtime = _trial_challenge_runtime()
    seen_frames: list[str] = []

    monkeypatch.setattr(runtime, "cur_frame", lambda **_kwargs: "home-frame")

    def read_attempts(*_args, **kwargs):
        seen_frames.append(kwargs["frame_data_url"])
        return ObservedTrialAttempts(remaining=5, capacity=5, text="奖励次数:5/5")

    def score(*_args, **kwargs):
        seen_frames.append(kwargs["frame_data_url"])
        return 93.0

    monkeypatch.setattr(runtime, "read_xianqiao_trial_attempts", read_attempts)
    monkeypatch.setattr(runtime, "shape_score", score)

    observed = runtime.observe_xianqiao_trial_home()

    assert seen_frames == ["home-frame", "home-frame"]
    assert observed.sweep_available is True
    assert observed.attempts.remaining == 5


def test_trial_probe_uses_sweep_button_to_increment_and_rolls_back_after_failure(monkeypatch):
    runtime = _trial_challenge_runtime()
    observations = iter((
        ObservedTrialHomeState(
            attempts=ObservedTrialAttempts(remaining=2, capacity=5, text="奖励次数:2/5"),
            sweep_available=True,
            sweep_score=96.0,
        ),
        ObservedTrialHomeState(
            attempts=ObservedTrialAttempts(remaining=1, capacity=5, text="奖励次数:1/5"),
            sweep_available=True,
            sweep_score=94.0,
        ),
    ))
    attempts_after = iter((
        ObservedTrialAttempts(remaining=1, capacity=2, text="奖励次数:1/2"),
        ObservedTrialAttempts(remaining=1, capacity=2, text="奖励次数:1/2"),
    ))
    outcomes = iter(("success", "failure"))
    adjustments: list[int] = []

    monkeypatch.setattr(runtime, "current_scene", lambda *_args, **_kwargs: (357, 100.0, "frame"))
    monkeypatch.setattr(runtime, "observe_xianqiao_trial_home", lambda *_args, **_kwargs: next(observations))
    monkeypatch.setattr(runtime, "read_xianqiao_trial_attempts", lambda *_args, **_kwargs: next(attempts_after))

    def adjust(increment, **_kwargs):
        adjustments.append(increment)
        if False:
            yield None
        return {"difficulty_increment": increment}

    def challenge(**_kwargs):
        if False:
            yield None
        return {"result": {"outcome": next(outcomes)}, "returned_home": True}

    monkeypatch.setattr(runtime, "adjust_xianqiao_trial_level", adjust)
    monkeypatch.setattr(runtime, "complete_xianqiao_trial_challenge", challenge)

    result = _finish(runtime.probe_xianqiao_trial_until_failure())

    assert adjustments == [1, 1, -1]
    assert result["exit_reason"] == "failure_found"
    assert result["remaining_attempts"] == 1
    assert result["sweep_required"] is True
    assert result["rollback_settings"] == {"difficulty_increment": -1}
    assert [trial["mode"] for trial in result["trials"]] == [
        "incremented_from_sweep",
        "incremented_from_sweep",
    ]


def test_trial_probe_treats_missing_result_without_sweep_as_failure_once(monkeypatch):
    runtime = _trial_challenge_runtime()
    observations = iter((
        ObservedTrialHomeState(
            attempts=ObservedTrialAttempts(remaining=1, capacity=5, text="奖励次数:1/5"),
            sweep_available=False,
            sweep_score=0.0,
        ),
        ObservedTrialHomeState(
            attempts=ObservedTrialAttempts(remaining=1, capacity=5, text="奖励次数:1/5"),
            sweep_available=False,
            sweep_score=0.0,
        ),
    ))
    adjustments: list[int] = []
    challenge_count = 0

    monkeypatch.setattr(runtime, "current_scene", lambda *_args, **_kwargs: (357, 100.0, "frame"))
    monkeypatch.setattr(runtime, "observe_xianqiao_trial_home", lambda *_args, **_kwargs: next(observations))
    monkeypatch.setattr(
        runtime,
        "read_xianqiao_trial_attempts",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("结算丢失后应复用战后主页观察")),
    )

    def adjust(increment, **_kwargs):
        adjustments.append(increment)
        if False:
            yield None
        return {"difficulty_increment": increment}

    def challenge(**_kwargs):
        nonlocal challenge_count
        challenge_count += 1
        if False:
            yield None
        return {
            "result": {"outcome": "result_expired", "result_scene": 34},
            "returned_home": True,
            "reentered_from_world": True,
        }

    monkeypatch.setattr(runtime, "adjust_xianqiao_trial_level", adjust)
    monkeypatch.setattr(runtime, "complete_xianqiao_trial_challenge", challenge)

    result = _finish(runtime.probe_xianqiao_trial_until_failure())

    assert challenge_count == 1
    assert adjustments == [-1]
    assert result["exit_reason"] == "failure_found"
    assert result["remaining_attempts"] == 1
    assert result["sweep_required"] is True
    assert result["trials"][0]["resolved_outcome"] == "failure"
    assert result["trials"][0]["outcome_source"] == "post_challenge_sweep"


def test_trial_probe_treats_all_successful_attempts_as_normal_daily_completion(monkeypatch):
    runtime = _trial_challenge_runtime()
    observations = iter((
        ObservedTrialHomeState(
            attempts=ObservedTrialAttempts(remaining=1, capacity=5, text="奖励次数:1/5"),
            sweep_available=False,
            sweep_score=0.0,
        ),
        ObservedTrialHomeState(
            attempts=ObservedTrialAttempts(remaining=0, capacity=5, text="奖励次数:0/5"),
            sweep_available=True,
            sweep_score=95.0,
        ),
    ))
    attempts_after = iter((ObservedTrialAttempts(remaining=0, capacity=5, text="奖励次数:0/5"),))
    adjustments: list[int] = []

    monkeypatch.setattr(runtime, "current_scene", lambda *_args, **_kwargs: (357, 100.0, "frame"))
    monkeypatch.setattr(runtime, "observe_xianqiao_trial_home", lambda *_args, **_kwargs: next(observations))
    monkeypatch.setattr(runtime, "read_xianqiao_trial_attempts", lambda *_args, **_kwargs: next(attempts_after))

    def adjust(increment, **_kwargs):
        adjustments.append(increment)
        if False:
            yield None
        return {"difficulty_increment": increment}

    def challenge(**_kwargs):
        if False:
            yield None
        return {"result": {"outcome": "success"}, "returned_home": True}

    monkeypatch.setattr(runtime, "adjust_xianqiao_trial_level", adjust)
    monkeypatch.setattr(runtime, "complete_xianqiao_trial_challenge", challenge)

    result = _finish(runtime.probe_xianqiao_trial_until_failure())

    assert adjustments == []
    assert result["exit_reason"] == "attempts_exhausted"
    assert result["sweep_required"] is False
    assert result["trials"][0]["mode"] == "challenge_existing_overlevel"


def test_trial_daily_skips_purchase_by_default_then_uses_ui_driven_progression(monkeypatch):
    runtime = _trial_challenge_runtime()
    events: list[str] = []

    def purchase(target, **_kwargs):
        events.append(f"purchase_{target}")
        if False:
            yield None
        return {"purchased_after": target}

    def progress(**_kwargs):
        events.append("progress")
        if False:
            yield None
        return {"exit_reason": "attempts_exhausted", "sweep_required": False}

    def leave(**_kwargs):
        events.append("leave")
        if False:
            yield None
        return {"terminal_scene": 34}

    monkeypatch.setattr(runtime, "purchase_xianqiao_trial_attempts", purchase)
    monkeypatch.setattr(runtime, "probe_xianqiao_trial_until_failure", progress)
    monkeypatch.setattr(runtime, "leave_xianqiao_trial", leave)

    result = _finish(runtime.run_xianqiao_trial_daily(settle_seconds=0))

    assert events == ["progress", "leave"]
    assert result["purchase"]["exit_reason"] == "purchase_disabled"
    assert result["purchase"]["purchases_now"] == []
    assert result["progression"]["exit_reason"] == "attempts_exhausted"
    assert result["current_scene"] == 34


def test_trial_daily_sweeps_remaining_attempts_after_failure_then_leaves(monkeypatch):
    runtime = _trial_challenge_runtime()
    events: list[str] = []

    def purchase(*_args, **_kwargs):
        events.append("purchase")
        if False:
            yield None
        return {"purchased_after": 3}

    def progress(**_kwargs):
        events.append("probe")
        if False:
            yield None
        return {"exit_reason": "failure_found", "sweep_required": True, "remaining_attempts": 2}

    def sweep(**_kwargs):
        events.append("sweep")
        if False:
            yield None
        return {"exit_reason": "attempts_exhausted", "remaining_attempts": 0}

    def leave(**_kwargs):
        events.append("leave")
        if False:
            yield None
        return {"terminal_scene": 34}

    monkeypatch.setattr(runtime, "purchase_xianqiao_trial_attempts", purchase)
    monkeypatch.setattr(runtime, "probe_xianqiao_trial_until_failure", progress)
    monkeypatch.setattr(runtime, "sweep_remaining_xianqiao_trial_attempts", sweep)
    monkeypatch.setattr(runtime, "leave_xianqiao_trial", leave)

    result = _finish(
        runtime.run_xianqiao_trial_daily(
            target_daily_purchases=3,
            settle_seconds=0,
        )
    )

    assert events == ["purchase", "probe", "sweep", "leave"]
    assert result["sweep"]["remaining_attempts"] == 0
    assert result["result"] == "success"


def test_sweep_remaining_trial_attempts_requires_real_count_progress(monkeypatch):
    runtime = _trial_challenge_runtime()
    observations = iter((
        ObservedTrialHomeState(
            attempts=ObservedTrialAttempts(remaining=2, capacity=5, text="奖励次数:2/5"),
            sweep_available=True,
            sweep_score=98.0,
        ),
        ObservedTrialHomeState(
            attempts=ObservedTrialAttempts(remaining=1, capacity=5, text="奖励次数:1/5"),
            sweep_available=True,
            sweep_score=97.0,
        ),
    ))
    after = iter((
        ObservedTrialAttempts(remaining=1, capacity=5, text="奖励次数:1/5"),
        ObservedTrialAttempts(remaining=0, capacity=5, text="奖励次数:0/5"),
    ))

    monkeypatch.setattr(runtime, "observe_xianqiao_trial_home", lambda *_args, **_kwargs: next(observations))
    monkeypatch.setattr(runtime, "read_xianqiao_trial_attempts", lambda *_args, **_kwargs: next(after))

    def sweep_once(**_kwargs):
        if False:
            yield None
        return {"result": {"outcome": "sweep"}, "returned_home": True}

    monkeypatch.setattr(runtime, "complete_xianqiao_trial_challenge", sweep_once)

    result = _finish(runtime.sweep_remaining_xianqiao_trial_attempts(settle_seconds=0))

    assert result["remaining_attempts"] == 0
    assert len(result["sweeps"]) == 2


def test_trial_result_can_resume_directly_from_failure_popup(monkeypatch):
    runtime = _trial_challenge_runtime()
    runtime.ctx["images"].update({
        361: {"id": 361, "title": "成功结算", "width": 900, "height": 1600, "shapes": [{"title": "退出"}]},
        362: {"id": 362, "title": "仙窍战斗中", "width": 900, "height": 1600, "shapes": []},
        365: {"id": 365, "title": "失败结算", "width": 900, "height": 1600, "shapes": [{"title": "退出"}]},
    })
    wait_calls = 0

    def wait_view(*_args, **_kwargs):
        nonlocal wait_calls
        wait_calls += 1
        if False:
            yield None
        return runtime.view(365)

    monkeypatch.setattr(runtime, "wait_view", wait_view)
    monkeypatch.setattr(runtime, "cur_frame", lambda **_kwargs: "failure-frame")
    monkeypatch.setattr(runtime, "ocr_text", lambda **_kwargs: "变强途径 退出")

    result = _finish(runtime.wait_xianqiao_trial_result(result_settle_seconds=0))

    assert wait_calls == 1
    assert result["outcome"] == "failure"
