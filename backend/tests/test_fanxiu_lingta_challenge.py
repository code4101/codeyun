from __future__ import annotations

import threading
from datetime import datetime
from types import SimpleNamespace

import pytest

from backend.core.fanxiu.behavior_tree.runtime import create_behavior_tree_runtime_runner
from backend.core.fanxiu.data_annotation.tasks import lingta_challenge as lingta_challenge_module

from backend.core.fanxiu.data_annotation.tasks.lingta_challenge import (
    LINGTA_DAILY_LEVEL_LIMIT,
    LingtaProgress,
    classify_lingta_settlement_text,
    lingta_challenge_admission,
    lingta_current_card_point,
    lingta_progress_fragment,
    next_lingta_challenge_time,
    parse_lingta_progress_lines,
)
from backend.core.fanxiu.data_annotation.popup_guard import (
    FanxiuEmulatorRestartRequired,
)


def test_lingta_daily_limit_is_twenty() -> None:
    assert LINGTA_DAILY_LEVEL_LIMIT == 20


def test_next_lingta_challenge_time_uses_next_daily_0700() -> None:
    assert next_lingta_challenge_time(
        datetime(2026, 8, 11, 6, 59, 59)
    ) == datetime(2026, 8, 11, 7, 0, 0)
    assert next_lingta_challenge_time(
        datetime(2026, 8, 11, 7, 0, 0)
    ) == datetime(2026, 8, 12, 7, 0, 0)
    assert next_lingta_challenge_time(
        datetime(2026, 8, 11, 23, 59, 59)
    ) == datetime(2026, 8, 12, 7, 0, 0)


def test_lingta_admission_is_side_effect_free_before_0700() -> None:
    assert lingta_challenge_admission(datetime(2026, 8, 12, 6, 30)) == {
        "result": "success",
        "message": "灵塔_挑战：尚未到每日 07:00，未读取或操作游戏界面",
        "next_time": "2026-08-12 07:00:00",
        "current_scene": None,
    }
    assert lingta_challenge_admission(datetime(2026, 8, 12, 7, 0)) is None


def test_lingta_runtime_admission_uses_planned_business_clock(monkeypatch) -> None:
    from backend.core.fanxiu.data_annotation import behavior_tree_runtime

    monkeypatch.setattr(
        behavior_tree_runtime,
        "_now",
        lambda: datetime(2026, 8, 12, 7, 1),
    )
    runner = create_behavior_tree_runtime_runner()
    persisted: list[tuple] = []
    runner._persist_admission_decision = (
        lambda payload, decision: persisted.append(
            (payload, decision)
        )
        or decision
    )

    assert runner.apply_lingta_challenge_admission({}) is None
    assert persisted == [({}, None)]


def test_parse_lingta_progress_ignores_completed_and_locked_neighbours() -> None:
    assert parse_lingta_progress_lines(
        ["归墟之塔 已完成", "已通过： ７３ / ５００ 层", "昆仑之塔 金仙前期十层解锁"]
    ) == LingtaProgress(passed=73, total=500, text="已通过：73/500层")
    assert parse_lingta_progress_lines(["已通过：184/300层", "已通过：184/300层"]) == LingtaProgress(
        passed=184,
        total=300,
        text="已通过：184/300层",
    )
    assert parse_lingta_progress_lines(["已完成", "十层解锁"]) is None


def test_parse_lingta_progress_rejects_ambiguous_or_invalid_results() -> None:
    assert parse_lingta_progress_lines(["已通过：501/500层"]) is None
    try:
        parse_lingta_progress_lines(["已通过：73/500层", "已通过：184/300层"])
    except RuntimeError as exc:
        assert "拒绝猜测点击目标" in str(exc)
    else:
        raise AssertionError("multiple current progress lines must be rejected")


def test_lingta_progress_fragment_requires_real_unique_box() -> None:
    fragment = {"text": "已通过：73/500层", "x": 505, "y": 918, "w": 251, "h": 29}
    assert lingta_progress_fragment(
        [
            {"text": "归墟之塔 已完成", "x": 100, "y": 500, "w": 200, "h": 40},
            fragment,
        ]
    ) == (fragment, LingtaProgress(passed=73, total=500, text="已通过：73/500层"))
    assert lingta_progress_fragment([{"text": "已通过：73/500层", "x": 0, "y": 0, "w": 0, "h": 29}]) is None


def test_current_card_relation_handles_old_left_and_current_right_samples() -> None:
    assert lingta_current_card_point({"x": 149, "y": 918, "w": 268, "h": 29}) == (283.0, 671.5)
    assert lingta_current_card_point({"x": 505, "y": 918, "w": 253, "h": 30}) == (631.5, 663.0)


def test_lingta_crop_ocr_rebases_progress_box_to_full_frame(monkeypatch) -> None:
    runner = create_behavior_tree_runtime_runner()
    image = {
        "type": "image",
        "filename": "0194.png",
        "width": 900,
        "height": 1600,
        "shapes": [
            {
                "id": "shape-lingta-progress",
                "title": "当前灵塔信息区",
                "x": 0.1,
                "y": 0.2,
                "w": 0.8,
                "h": 0.55,
            }
        ],
    }
    monkeypatch.setattr(
        runner,
        "_crop_frame_data_url_for_shapes",
        lambda *_args, **_kwargs: ("crop-frame", 500.0, 900.0),
    )
    monkeypatch.setattr(
        runner,
        "_ocr_frame",
        lambda *_args, **_kwargs: {
            "lines": [
                {"text": "已通过：73/500层", "x": 4, "y": 18, "w": 254, "h": 30}
            ]
        },
    )

    fragments = runner._ocr_fragments_in_shapes(
        "frame-194",
        image,
        ("当前灵塔信息区",),
        padding=0,
        ctx=None,
    )

    assert fragments == [
        {"text": "已通过：73/500层", "x": 504.0, "y": 918.0, "w": 254, "h": 30}
    ]
    evidence = lingta_progress_fragment(fragments)
    assert evidence is not None
    assert lingta_current_card_point(evidence[0]) == (631.0, 663.0)


def test_lingta_unknown_settlement_evidence_is_saved_to_standard_temp_pipeline(
    monkeypatch,
) -> None:
    runner = create_behavior_tree_runtime_runner()
    calls: list[tuple] = []

    def fake_build(bound_runner, ctx, frame, **kwargs):
        calls.append((bound_runner, ctx, frame, kwargs))
        return SimpleNamespace(
            frame_path=r"C:\Temp\codeyun\fanxiu_unknown\settlement.png",
            report_path=r"C:\Temp\codeyun\fanxiu_unknown\settlement.json",
        )

    monkeypatch.setattr(
        lingta_challenge_module,
        "build_unknown_evidence",
        fake_build,
    )
    runtime = SimpleNamespace(runner="runtime-runner", ctx={"entry_id": "entry"})

    suffix = runner._preserve_lingta_settlement_evidence(
        runtime,
        "data:image/png;base64,AAAA",
        label="灵塔_挑战_专用失败汇总",
    )

    assert "settlement.png" in suffix and "settlement.json" in suffix
    assert calls == [
        (
            "runtime-runner",
            {"entry_id": "entry"},
            "data:image/png;base64,AAAA",
            {
                "label": "灵塔_挑战_专用失败汇总",
                "expected_scene_ids": [365, 194, 532, 533, 534, 548, 34],
                "last_scene_id": None,
                "last_score": 0.0,
            },
        )
    ]


def test_lingta_route_ignores_completed_sweep_and_opens_current_floor_detail() -> None:
    class RouteRuntime:
        payload = {"max_scrolls": 10}

        def __init__(self) -> None:
            self.actions: list[tuple] = []

        def goto_view(self, scene_id):
            self.actions.append(("goto", scene_id))
            if False:
                yield None

        def wait_click_then_view(self, source, shape, target, **kwargs):
            self.actions.append(("wait_click", source, shape, target, kwargs.get("label")))
            if False:
                yield None
            return target

        def view_visible(self, scene_id):
            return ("view", scene_id)

        def shape_visible(self, scene_id, shape):
            return ("shape", scene_id, shape)

        def wait_any(self, conditions, **kwargs):
            self.actions.append(
                (
                    "wait_any",
                    tuple(conditions),
                    conditions,
                    kwargs.get("timeout"),
                    kwargs.get("label"),
                )
            )
            if False:
                yield None
            return "jump"

        def open_daily_entry(self, **kwargs):
            self.actions.append(("open_daily_entry", kwargs))
            if False:
                yield None
            return "open"

        def wait_view(self, *scene_ids, **kwargs):
            self.actions.append(("wait_view", scene_ids, kwargs.get("label"), kwargs.get("timeout")))
            if False:
                yield None
            if scene_ids == (193, 194):
                return 194
            if scene_ids == (531, 532):
                return 531
            return scene_ids[-1]

        def current_scene(self, scene_ids, **kwargs):
            self.actions.append(("current_scene", tuple(scene_ids), kwargs.get("update")))
            return 194, 100.0, "frame-194"

        def ocr_fragments_in_shapes(self, scene_id, shape_titles, **kwargs):
            self.actions.append(
                (
                    "ocr_fragments",
                    scene_id,
                    tuple(shape_titles),
                    kwargs.get("frame_data_url"),
                    kwargs.get("crop"),
                )
            )
            return [
                {
                    "text": "已通过：73/500层",
                    "x": 504,
                    "y": 918,
                    "w": 254,
                    "h": 30,
                }
            ]

        def click_frame_point(self, scene_id, x, y):
            self.actions.append(("click_point", scene_id, x, y))

    runner = create_behavior_tree_runtime_runner()
    runtime = RouteRuntime()

    progress = _drain(runner._open_lingta_current_floor_detail(runtime))

    assert progress == LingtaProgress(passed=73, total=500, text="已通过：73/500层")
    open_call = next(action for action in runtime.actions if action[0] == "open_daily_entry")
    assert open_call[1]["progress_can_mark_done"] is False
    assert open_call[1]["title_pattern"] == r"挑战或扫荡混沌灵塔|混沌灵塔|灵塔"
    assert ("wait_view", (193, 194), "灵塔_挑战：等待 #193/#194", 60.0) in runtime.actions
    assert ("click_point", 194, 631.0, 663.0) in runtime.actions
    wait_any_call = next(action for action in runtime.actions if action[0] == "wait_any")
    assert wait_any_call[1] == ("current_floor", "jump")
    assert wait_any_call[2] == {
        "current_floor": ("view", 532),
        "jump": ("shape", 531, "前往当前层"),
    }
    assert wait_any_call[3] == 30
    assert any(
        action[:4] == ("wait_click", 531, "前往当前层", 532)
        for action in runtime.actions
    )


def test_lingta_route_accepts_current_card_landing_directly_on_532() -> None:
    class DirectRuntime:
        payload = {}

        def __init__(self) -> None:
            self.actions: list[tuple] = []

        def goto_view(self, scene_id):
            if False:
                yield None

        def wait_click_then_view(self, source, shape, target, **_kwargs):
            self.actions.append(("wait_click", source, shape, target))
            if False:
                yield None
            return target

        def open_daily_entry(self, **kwargs):
            self.actions.append(("open_daily_entry", kwargs))
            if False:
                yield None
            return "open"

        def wait_view(self, *scene_ids, **_kwargs):
            if False:
                yield None
            return 194 if scene_ids == (193, 194) else 532

        def current_scene(self, *_args, **_kwargs):
            return 194, 100.0, "frame"

        def ocr_fragments_in_shapes(self, *_args, **_kwargs):
            return [{"text": "已通过：73/500层", "x": 504, "y": 918, "w": 254, "h": 30}]

        def click_frame_point(self, scene_id, x, y):
            self.actions.append(("click_point", scene_id, x, y))

    runner = create_behavior_tree_runtime_runner()
    runtime = DirectRuntime()

    _drain(runner._open_lingta_current_floor_detail(runtime))

    open_call = next(action for action in runtime.actions if action[0] == "open_daily_entry")
    assert open_call[1]["max_scrolls"] == 30
    assert not [action for action in runtime.actions if action[0] == "wait_click" and action[1] == 531]


def test_lingta_route_accepts_late_direct_532_after_first_identifying_531() -> None:
    class LateDirectRuntime:
        payload = {}

        def __init__(self) -> None:
            self.actions: list[tuple] = []

        def goto_view(self, _scene_id):
            if False:
                yield None

        def wait_click_then_view(self, source, shape, target, **_kwargs):
            self.actions.append(("wait_click", source, shape, target))
            if False:
                yield None
            return target

        def view_visible(self, scene_id):
            return ("view", scene_id)

        def shape_visible(self, scene_id, shape):
            return ("shape", scene_id, shape)

        def wait_any(self, conditions, **kwargs):
            self.actions.append(("wait_any", tuple(conditions), kwargs.get("timeout")))
            if False:
                yield None
            return "current_floor"

        def open_daily_entry(self, **_kwargs):
            if False:
                yield None
            return "open"

        def wait_view(self, *scene_ids, **_kwargs):
            if False:
                yield None
            return 194 if scene_ids == (193, 194) else 531

        def current_scene(self, *_args, **_kwargs):
            return 194, 100.0, "frame"

        def ocr_fragments_in_shapes(self, *_args, **_kwargs):
            return [{"text": "已通过：73/500层", "x": 504, "y": 918, "w": 254, "h": 30}]

        def click_frame_point(self, _scene_id, _x, _y):
            return None

    runner = create_behavior_tree_runtime_runner()
    runtime = LateDirectRuntime()

    progress = _drain(runner._open_lingta_current_floor_detail(runtime))

    assert progress == LingtaProgress(passed=73, total=500, text="已通过：73/500层")
    assert ("wait_any", ("current_floor", "jump"), 30) in runtime.actions
    assert not [
        action for action in runtime.actions
        if action[0] == "wait_click" and action[1] == 531
    ]


def test_lingta_route_propagates_overview_race_timeout_without_clicking() -> None:
    class MissingBranchRuntime:
        payload = {}

        def __init__(self) -> None:
            self.actions: list[tuple] = []

        def goto_view(self, _scene_id):
            if False:
                yield None

        def wait_click_then_view(self, source, shape, target, **_kwargs):
            self.actions.append(("wait_click", source, shape, target))
            if False:
                yield None
            return target

        def view_visible(self, scene_id):
            return ("view", scene_id)

        def shape_visible(self, scene_id, shape):
            return ("shape", scene_id, shape)

        def wait_any(self, conditions, **_kwargs):
            self.actions.append(("wait_any", tuple(conditions)))
            if False:
                yield None
            raise TimeoutError("#532 与前往当前层均未出现")

        def open_daily_entry(self, **_kwargs):
            if False:
                yield None
            return "open"

        def wait_view(self, *scene_ids, **_kwargs):
            if False:
                yield None
            return 194 if scene_ids == (193, 194) else 531

        def current_scene(self, *_args, **_kwargs):
            return 194, 100.0, "frame"

        def ocr_fragments_in_shapes(self, *_args, **_kwargs):
            return [{"text": "已通过：73/500层", "x": 504, "y": 918, "w": 254, "h": 30}]

        def click_frame_point(self, _scene_id, _x, _y):
            return None

    runner = create_behavior_tree_runtime_runner()
    runtime = MissingBranchRuntime()

    with pytest.raises(TimeoutError, match="#532 与前往当前层均未出现"):
        _drain(runner._open_lingta_current_floor_detail(runtime))

    assert ("wait_any", ("current_floor", "jump")) in runtime.actions
    assert not [
        action for action in runtime.actions
        if action[0] == "wait_click" and action[1] == 531
    ]


def test_lingta_settlement_classifier_separates_countdown_from_level_gate() -> None:
    assert classify_lingta_settlement_text("胜利 下一层（８秒） 点击退出") == "auto_next"
    assert classify_lingta_settlement_text("胜利 下一层 点击退出") == "level_gate"
    assert (
        classify_lingta_settlement_text(
            "累计获得 本轮指导次数：3 当前人物境界：真仙 指导推荐境界：金仙"
        )
        == "capacity_failure"
    )
    assert classify_lingta_settlement_text("已通关！") == "tower_complete"
    assert classify_lingta_settlement_text("战斗中") is None


def test_lingta_challenge_is_one_daily_0700_standard_job() -> None:
    from backend.core.fanxiu.data_annotation.default_jobs import (
        register_fanxiu_data_annotation_default_runtime_jobs,
    )
    from backend.core.fanxiu.data_annotation.jobs import (
        get_fanxiu_data_annotation_task_cell_definition,
    )
    from backend.core.fanxiu.data_annotation.scheduler_defaults import (
        default_data_annotation_scheduler_tasks,
    )
    from backend.core.fanxiu.data_annotation.behavior_tree_control import (
        sort_scheduler_tasks_for_dispatch,
    )

    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("lingta_challenge")
    assert definition is not None
    assert definition.label == "灵塔_挑战"
    assert definition.scheduler_supported is True
    assert definition.standard_job is True
    assert definition.standard_job_id == "lingta-challenge"
    assert definition.standard_job_description == "每日"
    tasks = default_data_annotation_scheduler_tasks(datetime(2026, 8, 12, 1, 0))
    matches = [item for item in tasks if item["task_type"] == "lingta_challenge"]
    assert len(matches) == 1
    assert matches[0]["id"] == "lingta-challenge"
    assert matches[0]["next_time"] == "2026-08-12 07:00:00"
    assert matches[0]["payload"] == definition.standard_job_payload
    seven_am_cohort = [
        item
        for item in tasks
        if item["id"] in {"legacy-daily-activity", "lingta-challenge"}
    ]
    assert [
        item["id"] for item in sort_scheduler_tasks_for_dispatch(seven_am_cohort)
    ] == ["legacy-daily-activity", "lingta-challenge"]


def _drain(generator):
    try:
        while True:
            next(generator)
    except StopIteration as exc:
        return exc.value


def _capture_next_time(runner):
    updates = []
    runner._persist_scheduler_task_next_time = lambda task_id, next_time: updates.append((task_id, next_time))
    return updates


class _FakeLingtaRuntime:
    def __init__(
        self,
        scenes: tuple[int | None, ...],
        *,
        payload: dict | None = None,
        ocr_text: str = "变强途径",
    ):
        self.stop_event = threading.Event()
        self.payload = payload or {
            "__scheduler_task_id": "lingta-challenge",
            "monitor_poll_seconds": 0.5,
        }
        self._scenes = iter(scenes)
        self._last_scene: int | None = 34
        self.actions: list[tuple] = []
        self.wait_click_options: list[dict] = []
        self.completion_message = ""
        self._ocr_text = ocr_text

    def current_scene(self, _candidates, **_kwargs):
        self._last_scene = next(self._scenes, self._last_scene)
        return self._last_scene, 100.0, "frame"

    def click_ocr_text(self, scene_id, title, **kwargs):
        self.actions.append(
            (
                "click_ocr",
                scene_id,
                title,
                kwargs.get("in_shapes"),
                kwargs.get("match_mode"),
            )
        )

    def ocr_text(self, _frame):
        return self._ocr_text

    def wait_click_then_view(self, source, shape, target, **_kwargs):
        self.actions.append(("wait_click_then_view", source, shape, target))
        if False:
            yield None
        return target

    def wait_click(self, source, shape, **kwargs):
        self.actions.append(("wait_click", source, shape))
        self.wait_click_options.append(dict(kwargs))
        if False:
            yield None

    def wait_action_settle(self, seconds):
        self.actions.append(("settle", seconds))
        if False:
            yield None

    def goto_view(self, scene_id):
        self.actions.append(("goto", scene_id))
        if False:
            yield None

    def set_completion_message(self, message: str) -> None:
        self.completion_message = message


def _patch_lingta_open(runner, *, passed: int = 73, total: int = 500) -> None:
    def open_current_floor(_runtime):
        if False:
            yield None
        return LingtaProgress(passed=passed, total=total, text=f"已通过：{passed}/{total}层")

    runner._open_lingta_current_floor_detail = open_current_floor


def test_lingta_flow_refuses_challenge_when_start_mark_persistence_fails() -> None:
    runner = create_behavior_tree_runtime_runner()
    runtime = _FakeLingtaRuntime((34,))
    _patch_lingta_open(runner)
    runner._read_lingta_challenge_snapshot = lambda: {
        "ok": True,
        "current_tower_id": 1426,
        "chain_pass_count": 0,
    }
    runner._set_scheduler_task_payload_flag = lambda *_args: False

    with pytest.raises(RuntimeError, match="防重复标记未确认持久化"):
        _drain(runner.灵塔挑战流程(runtime))

    assert not [action for action in runtime.actions if action[0] == "click_ocr"]


def test_lingta_flow_continues_from_already_open_532_without_reopening_route() -> None:
    runner = create_behavior_tree_runtime_runner()
    runtime = _FakeLingtaRuntime((532, 365))
    runner._open_lingta_current_floor_detail = lambda *_args: (_ for _ in ()).throw(
        AssertionError("already-open #532 must not navigate away")
    )
    snapshots = iter(
        (
            {"ok": True, "current_tower_id": 1426, "chain_pass_count": 0},
            {"ok": True, "current_tower_id": 1426, "chain_pass_count": 0},
        )
    )
    runner._read_lingta_challenge_snapshot = lambda: next(snapshots)
    persisted: list[tuple] = []
    cleared: list[tuple] = []
    runner._set_scheduler_task_payload_flag = lambda *args: persisted.append(args) or True
    runner._clear_scheduler_task_payload_flag = lambda *args: cleared.append(args)

    result = _drain(runner.灵塔挑战流程(runtime))

    assert result["outcome"] == "power_limit"
    assert [action for action in runtime.actions if action[0] == "click_ocr"] == [
        ("click_ocr", 532, "挑战", ("挑战文字",), "exact")
    ]
    assert persisted[0][2]["ui_passed"] is None
    assert persisted[0][2]["ui_total"] is None
    assert cleared == [("lingta-challenge", "lingta_auto_chain_started")]


def test_lingta_flow_waits_through_stale_detail_frame_then_treats_failure_as_normal_terminal() -> None:
    runner = create_behavior_tree_runtime_runner()
    runtime = _FakeLingtaRuntime((34, 532, 365))
    _patch_lingta_open(runner)
    snapshots = iter(
        (
            # #532 can still expose residue from an earlier session; the new
            # tower scene clears it before accumulating this run's wins.
            {
                "ok": True,
                "current_tower_id": 1426,
                "chain_pass_count": 7,
                "config_bounds_complete": True,
                "has_current_tower_config": True,
                # Being on the last configured floor must not skip that floor.
                "has_next_tower_config": False,
            },
            {"ok": True, "current_tower_id": 1427, "chain_pass_count": 1},
        )
    )
    runner._read_lingta_challenge_snapshot = lambda: next(snapshots)
    persisted: list[tuple] = []
    cleared: list[tuple] = []
    runner._set_scheduler_task_payload_flag = lambda *args: persisted.append(args) or True
    runner._clear_scheduler_task_payload_flag = lambda *args: cleared.append(args)

    result = _drain(runner.灵塔挑战流程(runtime))

    assert result["result"] == "success"
    assert result["outcome"] == "power_limit"
    assert "连续通过 1 层后挑战失败" in result["message"]
    assert [action for action in runtime.actions if action[0] == "click_ocr"] == [
        ("click_ocr", 532, "挑战", ("挑战文字",), "exact")
    ]
    assert persisted and cleared == [("lingta-challenge", "lingta_auto_chain_started")]
    assert ("settle", 0.5) in runtime.actions
    assert ("wait_click_then_view", 365, "退出", [34, 532]) in runtime.actions


def test_lingta_failure_landing_on_current_floor_returns_world_before_success() -> None:
    class FailureLandingRuntime(_FakeLingtaRuntime):
        def wait_click_then_view(self, source, shape, target, **_kwargs):
            self.actions.append(("wait_click_then_view", source, shape, target))
            if False:
                yield None
            return SimpleNamespace(id=532)

    runner = create_behavior_tree_runtime_runner()
    runtime = FailureLandingRuntime((365,))
    runner._set_scheduler_task_payload_flag = lambda *_args: True
    runner._clear_scheduler_task_payload_flag = lambda *_args: None

    result = _drain(runner.灵塔挑战流程(runtime))

    assert result["outcome"] == "power_limit"
    assert runtime.actions == [
        ("wait_click_then_view", 365, "退出", [34, 532]),
        ("goto", 34),
    ]


def test_lingta_failure_persists_terminal_mark_before_exit_error() -> None:
    class ExitErrorRuntime(_FakeLingtaRuntime):
        def wait_click_then_view(self, *_args, **_kwargs):
            if False:
                yield None
            raise TimeoutError("exit landed on #532")

    runner = create_behavior_tree_runtime_runner()
    runtime = ExitErrorRuntime((365,))
    persisted: list[tuple] = []
    runner._set_scheduler_task_payload_flag = lambda *args: persisted.append(args) or True

    with pytest.raises(TimeoutError, match="landed on #532"):
        _drain(runner.灵塔挑战流程(runtime))

    assert persisted
    mark = persisted[-1][2]
    assert mark["terminal_outcome"] == "power_limit"
    assert mark["terminal_scene_id"] == 365


def test_lingta_retry_from_532_power_limit_mark_never_reclicks_challenge() -> None:
    runner = create_behavior_tree_runtime_runner()
    runtime = _FakeLingtaRuntime(
        (532,),
        payload={
            "__scheduler_task_id": "lingta-challenge",
            "lingta_auto_chain_started": {
                "terminal_outcome": "power_limit",
                "terminal_scene_id": 365,
                "max_chain_pass_count": 0,
            },
        },
    )
    cleared: list[tuple] = []
    runner._clear_scheduler_task_payload_flag = lambda *args: cleared.append(args)

    result = _drain(runner.灵塔挑战流程(runtime))

    assert result["outcome"] == "power_limit"
    assert "未重复点击挑战" in result["message"]
    assert runtime.actions == [("goto", 34)]
    assert cleared == [("lingta-challenge", "lingta_auto_chain_started")]


def test_lingta_flow_treats_zero_pass_failure_as_normal_terminal(monkeypatch) -> None:
    from backend.core.fanxiu.data_annotation import behavior_tree_runtime

    monkeypatch.setattr(
        behavior_tree_runtime,
        "_now",
        lambda: datetime(2026, 8, 12, 7, 1),
    )
    runner = create_behavior_tree_runtime_runner()
    updates = _capture_next_time(runner)
    runtime = _FakeLingtaRuntime((365,))
    persisted: list[tuple] = []
    cleared: list[tuple] = []
    runner._set_scheduler_task_payload_flag = lambda *args: persisted.append(args) or True
    runner._clear_scheduler_task_payload_flag = lambda *args: cleared.append(args)

    result = _drain(runner.灵塔挑战流程(runtime))

    assert result["result"] == "success"
    assert result["outcome"] == "power_limit"
    assert "当前层挑战失败" in result["message"]
    assert "next_time" not in result
    assert updates[0] == ("lingta-challenge", "2026-08-13 07:00:00")
    assert persisted[-1][2]["terminal_outcome"] == "power_limit"
    assert cleared == [("lingta-challenge", "lingta_auto_chain_started")]
    assert ("wait_click_then_view", 365, "退出", [34, 532]) in runtime.actions
    assert not [action for action in runtime.actions if action[0] == "click_ocr"]


@pytest.mark.parametrize("stable_scene", [34, 194])
def test_lingta_flow_finishes_marked_chain_from_stable_scene_without_reclick(
    stable_scene: int,
) -> None:
    runner = create_behavior_tree_runtime_runner()
    updates = _capture_next_time(runner)
    start_mark = {
        "started_at": "2026-08-12T07:00:10",
        "start_tower_id": 1426,
    }
    runtime = _FakeLingtaRuntime(
        (stable_scene,),
        payload={
            "__scheduler_task_id": "lingta-challenge",
            "lingta_auto_chain_started": start_mark,
        },
    )
    runner._read_lingta_challenge_snapshot = lambda: {
        "ok": True,
        "current_tower_id": 1427,
        "chain_pass_count": 1,
    }
    cleared: list[tuple] = []
    runner._clear_scheduler_task_payload_flag = lambda *args: cleared.append(args)

    result = _drain(runner.灵塔挑战流程(runtime))

    assert result["result"] == "success"
    assert result["outcome"] == "recovered_auto_chain"
    assert "next_time" not in result
    assert updates[0][1].endswith("07:00:00")
    assert "未重复点击挑战" in result["message"]
    assert cleared == [("lingta-challenge", "lingta_auto_chain_started")]
    assert not [action for action in runtime.actions if action[0] == "click_ocr"]
    assert (("goto", 34) in runtime.actions) is (stable_scene == 194)


def test_lingta_flow_keeps_start_mark_when_stable_scene_has_no_advance_proof() -> None:
    runner = create_behavior_tree_runtime_runner()
    runtime = _FakeLingtaRuntime(
        (34,),
        payload={
            "__scheduler_task_id": "lingta-challenge",
            "lingta_auto_chain_started": {
                "started_at": "2026-08-12T07:00:10",
                "start_tower_id": 1426,
            },
        },
    )
    runner._read_lingta_challenge_snapshot = lambda: {
        "ok": True,
        "current_tower_id": 1426,
        "chain_pass_count": 0,
    }
    cleared: list[tuple] = []
    runner._clear_scheduler_task_payload_flag = lambda *args: cleared.append(args)

    with pytest.raises(RuntimeError, match="没有确认层数推进"):
        _drain(runner.灵塔挑战流程(runtime))

    assert cleared == []


def test_lingta_flow_reads_persisted_idempotency_fact_when_cell_payload_is_stale() -> None:
    runner = create_behavior_tree_runtime_runner()
    _capture_next_time(runner)
    runtime = _FakeLingtaRuntime(
        (34,),
        payload={"__scheduler_task_id": "lingta-challenge"},
    )
    persisted_mark = {
        "started_at": "2026-08-25T11:03:30",
        "start_tower_id": 1426,
        "max_chain_pass_count": 3,
        "progress_evidence": "three_distinct_548_next_clicks",
    }
    runner._get_scheduler_task_payload_flag = lambda *args: persisted_mark
    runner._read_lingta_challenge_snapshot = lambda: {
        "ok": True,
        "current_tower_id": 1426,
        "chain_pass_count": 0,
    }
    cleared: list[tuple] = []
    runner._clear_scheduler_task_payload_flag = lambda *args: cleared.append(args)

    result = _drain(runner.灵塔挑战流程(runtime))

    assert result["result"] == "success"
    assert "本轮计数 3" in result["message"]
    assert not [action for action in runtime.actions if action[0] == "click_ocr"]
    assert cleared == [("lingta-challenge", "lingta_auto_chain_started")]
    assert not [action for action in runtime.actions if action[0] == "click_ocr"]


def test_lingta_flow_does_not_treat_preexisting_max_config_as_progress() -> None:
    runner = create_behavior_tree_runtime_runner()
    updates = _capture_next_time(runner)
    runtime = _FakeLingtaRuntime(
        (34,),
        payload={
            "__scheduler_task_id": "lingta-challenge",
            "lingta_auto_chain_started": {
                "started_at": "2026-08-15T07:15:48",
                "start_tower_id": 1426,
            },
        },
    )
    runner._read_lingta_challenge_snapshot = lambda: {
        "ok": True,
        "current_tower_id": 1426,
        "max_configured_tower_id": 1426,
        "config_bounds_complete": True,
        "has_current_tower_config": True,
        "has_next_tower_config": False,
        "chain_pass_count": 0,
    }
    cleared: list[tuple] = []
    runner._clear_scheduler_task_payload_flag = lambda *args: cleared.append(args)

    with pytest.raises(RuntimeError, match="没有确认层数推进"):
        _drain(runner.灵塔挑战流程(runtime))

    assert updates == []
    assert cleared == []
    assert not [action for action in runtime.actions if action[0] == "click_ocr"]


def test_lingta_flow_recovers_only_after_advancing_beyond_config_boundary() -> None:
    runner = create_behavior_tree_runtime_runner()
    updates = _capture_next_time(runner)
    runtime = _FakeLingtaRuntime(
        (34,),
        payload={
            "__scheduler_task_id": "lingta-challenge",
            "lingta_auto_chain_started": {
                "started_at": "2026-08-20T11:37:41",
                "start_tower_id": 1426,
            },
        },
    )
    runner._read_lingta_challenge_snapshot = lambda: {
        "ok": True,
        "current_tower_id": 1427,
        "max_configured_tower_id": 1426,
        "config_bounds_complete": True,
        "has_current_tower_config": False,
        "has_next_tower_config": False,
        "chain_pass_count": 0,
    }
    cleared: list[tuple] = []
    runner._clear_scheduler_task_payload_flag = lambda *args: cleared.append(args)

    result = _drain(runner.灵塔挑战流程(runtime))

    assert result["outcome"] == "no_next_floor"
    assert "从 1426 推进到 1427" in result["message"]
    assert updates[0][1].endswith("07:00:00")
    assert cleared == [("lingta-challenge", "lingta_auto_chain_started")]


def test_lingta_flow_finishes_daily_limit_settlement_without_reclick() -> None:
    runner = create_behavior_tree_runtime_runner()
    updates = _capture_next_time(runner)
    runtime = _FakeLingtaRuntime(
        (533,),
        payload={
            "__scheduler_task_id": "lingta-challenge",
            "lingta_auto_chain_started": {
                "started_at": "2026-08-12T09:13:46",
                "start_tower_id": 1426,
            },
        },
    )
    cleared: list[tuple] = []
    runner._set_scheduler_task_payload_flag = lambda *_args: True
    runner._clear_scheduler_task_payload_flag = lambda *args: cleared.append(args)

    result = _drain(runner.灵塔挑战流程(runtime))

    assert result["outcome"] == "daily_limit"
    assert "已挑战 20 层" in result["message"]
    assert "next_time" not in result
    assert updates[0][1].endswith("07:00:00")
    assert ("wait_click_then_view", 533, "点击退出", [34, 534]) in runtime.actions
    assert cleared == [("lingta-challenge", "lingta_auto_chain_started")]
    assert not [action for action in runtime.actions if action[0] == "click_ocr"]


def test_lingta_flow_finishes_auto_closed_daily_limit_detail_without_reclick() -> None:
    runner = create_behavior_tree_runtime_runner()
    runtime = _FakeLingtaRuntime(
        (534,),
        payload={
            "__scheduler_task_id": "lingta-challenge",
            "lingta_auto_chain_started": {
                "started_at": "2026-08-12T09:13:46",
                "start_tower_id": 1426,
            },
        },
    )
    cleared: list[tuple] = []
    runner._set_scheduler_task_payload_flag = lambda *_args: True
    runner._clear_scheduler_task_payload_flag = lambda *args: cleared.append(args)

    result = _drain(runner.灵塔挑战流程(runtime))

    assert result["outcome"] == "daily_limit"
    assert "20/20" in result["message"]
    assert ("wait_click_then_view", 534, "返回灵塔列表", 194) in runtime.actions
    assert ("goto", 34) in runtime.actions
    assert cleared == [("lingta-challenge", "lingta_auto_chain_started")]
    assert not [action for action in runtime.actions if action[0] == "click_ocr"]


def test_lingta_flow_follows_daily_limit_exit_into_detail_page() -> None:
    runner = create_behavior_tree_runtime_runner()

    class DetailLandingRuntime(_FakeLingtaRuntime):
        def wait_click_then_view(self, source, shape, target, **_kwargs):
            self.actions.append(("wait_click_then_view", source, shape, target))
            if False:
                yield None
            if source == 533:
                return 534
            return target

    runtime = DetailLandingRuntime(
        (533,),
        payload={
            "__scheduler_task_id": "lingta-challenge",
            "lingta_auto_chain_started": {
                "started_at": "2026-08-12T09:13:46",
                "start_tower_id": 1426,
            },
        },
    )
    cleared: list[tuple] = []
    runner._set_scheduler_task_payload_flag = lambda *_args: True
    runner._clear_scheduler_task_payload_flag = lambda *args: cleared.append(args)

    result = _drain(runner.灵塔挑战流程(runtime))

    assert result["outcome"] == "daily_limit"
    assert ("wait_click_then_view", 533, "点击退出", [34, 534]) in runtime.actions
    assert ("wait_click_then_view", 534, "返回灵塔列表", 194) in runtime.actions
    assert ("goto", 34) in runtime.actions
    assert cleared == [("lingta-challenge", "lingta_auto_chain_started")]


def test_lingta_flow_finishes_persisted_daily_limit_terminal_from_world() -> None:
    runner = create_behavior_tree_runtime_runner()
    runtime = _FakeLingtaRuntime(
        (34,),
        payload={
            "__scheduler_task_id": "lingta-challenge",
            "lingta_auto_chain_started": {
                "started_at": "2026-08-13T07:11:03",
                "start_tower_id": 1426,
                "terminal_outcome": "daily_limit",
                "terminal_scene_id": 534,
                "terminal_observed_at": "2026-08-13T07:22:24",
            },
        },
    )
    runner._read_lingta_challenge_snapshot = lambda: (_ for _ in ()).throw(
        AssertionError("visible terminal start mark should avoid runtime inference")
    )
    cleared: list[tuple] = []
    runner._clear_scheduler_task_payload_flag = lambda *args: cleared.append(args)

    result = _drain(runner.灵塔挑战流程(runtime))

    assert result["outcome"] == "daily_limit"
    assert "防重复标记" in result["message"]
    assert cleared == [("lingta-challenge", "lingta_auto_chain_started")]
    assert not [action for action in runtime.actions if action[0] == "click_ocr"]


def test_lingta_flow_exits_observed_daily_limit_after_one_challenge() -> None:
    runner = create_behavior_tree_runtime_runner()
    runtime = _FakeLingtaRuntime((34, 533))
    _patch_lingta_open(runner)
    snapshots = iter(({"ok": True, "current_tower_id": 1426, "chain_pass_count": 0},))
    runner._read_lingta_challenge_snapshot = lambda: next(snapshots)
    runner._set_scheduler_task_payload_flag = lambda *_args: True
    cleared: list[tuple] = []
    runner._clear_scheduler_task_payload_flag = lambda *args: cleared.append(args)

    result = _drain(runner.灵塔挑战流程(runtime))

    assert result["outcome"] == "daily_limit"
    assert "已挑战 20 层" in result["message"]
    assert [action for action in runtime.actions if action[0] == "click_ocr"] == [
        ("click_ocr", 532, "挑战", ("挑战文字",), "exact")
    ]
    assert ("wait_click_then_view", 533, "点击退出", [34, 534]) in runtime.actions
    assert cleared == [("lingta-challenge", "lingta_auto_chain_started")]


def test_lingta_flow_finishes_persisted_win_when_live_model_was_unloaded() -> None:
    runner = create_behavior_tree_runtime_runner()
    runtime = _FakeLingtaRuntime(
        (34,),
        payload={
            "__scheduler_task_id": "lingta-challenge",
            "lingta_auto_chain_started": {
                "started_at": "2026-08-12T07:00:10",
                "start_tower_id": 1426,
                "max_chain_pass_count": 3,
                "last_tower_id": 1429,
                "last_observed_at": "2026-08-12T07:02:30",
            },
        },
    )
    runner._read_lingta_challenge_snapshot = lambda: {
        "ok": False,
        "available": False,
        "reason": "CapacityTowerDungeonMgr 已卸载",
    }
    cleared: list[tuple] = []
    runner._clear_scheduler_task_payload_flag = lambda *args: cleared.append(args)

    result = _drain(runner.灵塔挑战流程(runtime))

    assert result["result"] == "success"
    assert result["outcome"] == "recovered_auto_chain"
    assert "本轮计数 3、当前配置 1429" in result["message"]
    assert cleared == [("lingta-challenge", "lingta_auto_chain_started")]
    assert not [action for action in runtime.actions if action[0] == "click_ocr"]


def test_lingta_flow_preserves_start_mark_on_level_gated_victory() -> None:
    runner = create_behavior_tree_runtime_runner()
    runtime = _FakeLingtaRuntime(
        (34, None),
        ocr_text="胜利 下一层 点击退出",
    )
    _patch_lingta_open(runner)
    snapshots = iter(
        (
            {"ok": True, "current_tower_id": 1426, "chain_pass_count": 0},
            {"ok": True, "current_tower_id": 1427, "chain_pass_count": 1},
        )
    )
    runner._read_lingta_challenge_snapshot = lambda: next(snapshots)
    runner._set_scheduler_task_payload_flag = lambda *_args: True
    cleared: list[tuple] = []
    runner._clear_scheduler_task_payload_flag = lambda *args: cleared.append(args)

    with pytest.raises(RuntimeError, match="静态‘下一层/点击退出’等级门槛页"):
        _drain(runner.灵塔挑战流程(runtime))

    assert len([action for action in runtime.actions if action[0] == "click_ocr"]) == 1
    assert cleared == []


def test_lingta_flow_consumes_countdown_victory_locally_then_confirms_world() -> None:
    runner = create_behavior_tree_runtime_runner()
    runtime = _FakeLingtaRuntime((34, 548, 34))
    _patch_lingta_open(runner)
    snapshots = iter(
        (
            {"ok": True, "current_tower_id": 1426, "chain_pass_count": 0},
            {"ok": True, "current_tower_id": 1426, "chain_pass_count": 0},
            {"ok": True, "current_tower_id": 1426, "chain_pass_count": 0},
        )
    )
    runner._read_lingta_challenge_snapshot = lambda: next(snapshots)
    persisted: list[tuple] = []
    runner._set_scheduler_task_payload_flag = lambda *args: persisted.append(args) or True
    cleared: list[tuple] = []
    runner._clear_scheduler_task_payload_flag = lambda *args: cleared.append(args)

    result = _drain(runner.灵塔挑战流程(runtime))

    assert result["result"] == "success"
    assert result["outcome"] == "no_next_floor"
    assert ("wait_click", 548, "下一层") in runtime.actions
    assert runtime.wait_click_options == [{"timeout": 2.0}]
    assert persisted[-1][2]["max_chain_pass_count"] == 1
    assert persisted[-1][2]["progress_evidence"] == "ordinary_result_next_clicked"
    assert cleared == [("lingta-challenge", "lingta_auto_chain_started")]


def test_lingta_flow_uses_unique_ui_progress_when_runtime_root_is_unavailable() -> None:
    runner = create_behavior_tree_runtime_runner()
    _capture_next_time(runner)
    runtime = _FakeLingtaRuntime((34, 548, 34))
    _patch_lingta_open(runner, passed=149, total=500)
    runner._read_lingta_challenge_snapshot = lambda: {
        "ok": False,
        "reason": "manager_not_found",
    }
    persisted: list[tuple] = []
    runner._set_scheduler_task_payload_flag = lambda *args: persisted.append(args) or True
    runner._clear_scheduler_task_payload_flag = lambda *_args: None

    result = _drain(runner.灵塔挑战流程(runtime))

    assert result["result"] == "success"
    assert persisted[0][2]["start_tower_id"] == 0
    assert persisted[0][2]["ui_passed"] == 149
    assert persisted[0][2]["start_evidence"] == "unique_ui_progress"
    assert persisted[-1][2]["max_chain_pass_count"] == 1
    assert len([action for action in runtime.actions if action[0] == "click_ocr"]) == 1


def test_lingta_countdown_click_timeout_reidentifies_returned_detail() -> None:
    runner = create_behavior_tree_runtime_runner()

    class VanishedCountdownRuntime(_FakeLingtaRuntime):
        def wait_click(self, source, shape, **kwargs):
            self.actions.append(("wait_click", source, shape))
            self.wait_click_options.append(dict(kwargs))
            if False:
                yield None
            raise RuntimeError("wait_click #548 [下一层] 超时，最后 0% OCR=")

    runtime = VanishedCountdownRuntime((34, 548, 532))
    _patch_lingta_open(runner)
    snapshots = iter(
        (
            {"ok": True, "current_tower_id": 1426, "chain_pass_count": 0},
            {"ok": True, "current_tower_id": 1426, "chain_pass_count": 0},
        )
    )
    runner._read_lingta_challenge_snapshot = lambda: next(snapshots)
    runner._set_scheduler_task_payload_flag = lambda *_args: True
    runner._preserve_lingta_settlement_evidence = lambda *_args, **_kwargs: ""

    with pytest.raises(RuntimeError, match="随后自动链返回 #532"):
        _drain(runner.灵塔挑战流程(runtime))

    assert runtime.wait_click_options == [{"timeout": 2.0}]


def test_lingta_retry_uses_list_progress_to_close_interrupted_chain() -> None:
    runner = create_behavior_tree_runtime_runner()
    runtime = _FakeLingtaRuntime(
        (34,),
        payload={
            "__scheduler_task_id": "lingta-challenge",
            "lingta_auto_chain_started": {
                "started_at": "2026-08-21T11:43:45",
                "start_tower_id": 1426,
                "ui_passed": 140,
                "ui_total": 500,
                "launch_left_detail_at": "2026-08-21T11:43:51",
            },
        },
    )

    def reopen_progress(_runtime):
        if False:
            yield None
        return LingtaProgress(passed=145, total=500, text="已通过：145/500层")

    runner._open_lingta_current_floor_detail = reopen_progress
    runner._read_lingta_challenge_snapshot = lambda: {
        "ok": True,
        "current_tower_id": 1426,
        "chain_pass_count": 0,
    }
    cleared: list[tuple] = []
    runner._clear_scheduler_task_payload_flag = lambda *args: cleared.append(args)
    next_times = _capture_next_time(runner)

    result = _drain(runner.灵塔挑战流程(runtime))

    assert result["outcome"] == "recovered_auto_chain"
    assert "140 推进到 145" in result["message"]
    assert ("goto", 34) in runtime.actions
    assert cleared == [("lingta-challenge", "lingta_auto_chain_started")]
    assert next_times and next_times[0][0] == "lingta-challenge"


def test_lingta_flow_distinguishes_returned_532_after_confirmed_launch() -> None:
    runner = create_behavior_tree_runtime_runner()
    runtime = _FakeLingtaRuntime((34, None, 532))
    _patch_lingta_open(runner)
    snapshots = iter(
        (
            {"ok": True, "current_tower_id": 1426, "chain_pass_count": 0},
            {"ok": True, "current_tower_id": 1426, "chain_pass_count": 0},
        )
    )
    runner._read_lingta_challenge_snapshot = lambda: next(snapshots)
    persisted: list[tuple] = []
    runner._set_scheduler_task_payload_flag = lambda *args: persisted.append(args) or True
    runner._clear_scheduler_task_payload_flag = lambda *_args: (_ for _ in ()).throw(
        AssertionError("unproven returned detail must keep the marker")
    )
    runner._preserve_lingta_settlement_evidence = lambda *_args, **kwargs: (
        f"；label={kwargs['label']}"
    )

    with pytest.raises(RuntimeError, match="已确认挑战离开 #532，随后自动链返回 #532") as exc_info:
        _drain(runner.灵塔挑战流程(runtime))

    assert "自动链离开后返回532" in str(exc_info.value)
    assert persisted[1][2]["launch_left_detail_at"]
    assert ("goto", 34) in runtime.actions


def test_lingta_flow_retry_keeps_marker_for_confirmed_returned_532() -> None:
    runner = create_behavior_tree_runtime_runner()
    runtime = _FakeLingtaRuntime(
        (532,),
        payload={
            "__scheduler_task_id": "lingta-challenge",
            "lingta_auto_chain_started": {
                "started_at": "2026-08-20T11:37:42",
                "start_tower_id": 1426,
                "launch_left_detail_at": "2026-08-20T11:37:49",
            },
        },
    )

    with pytest.raises(RuntimeError, match="上次挑战已经离开 #532，当前又返回 #532"):
        _drain(runner.灵塔挑战流程(runtime))

    assert runtime.actions == []


def test_lingta_flow_preserves_special_failure_summary_for_real_asset_capture() -> None:
    runner = create_behavior_tree_runtime_runner()
    runtime = _FakeLingtaRuntime(
        (34, None),
        ocr_text=(
            "累计获得 本轮指导次数：3 "
            "当前人物境界：真仙前期 指导推荐境界：金仙前期 点击退出"
        ),
    )
    _patch_lingta_open(runner)
    snapshots = iter(
        (
            {"ok": True, "current_tower_id": 1426, "chain_pass_count": 0},
            {"ok": True, "current_tower_id": 1429, "chain_pass_count": 3},
        )
    )
    runner._read_lingta_challenge_snapshot = lambda: next(snapshots)
    persisted: list[tuple] = []
    runner._set_scheduler_task_payload_flag = lambda *args: persisted.append(args) or True
    cleared: list[tuple] = []
    runner._clear_scheduler_task_payload_flag = lambda *args: cleared.append(args)

    with pytest.raises(RuntimeError, match="灵塔专用失败汇总页"):
        _drain(runner.灵塔挑战流程(runtime))

    assert len([action for action in runtime.actions if action[0] == "click_ocr"]) == 1
    assert len(persisted) == 3
    assert persisted[-1][2]["max_chain_pass_count"] == 3
    assert persisted[-1][2]["last_tower_id"] == 1429
    assert persisted[-1][2]["last_observed_at"]
    assert cleared == []


def test_lingta_flow_preserves_last_floor_settlement_when_config_is_exhausted() -> None:
    runner = create_behavior_tree_runtime_runner()
    runtime = _FakeLingtaRuntime((34, None), ocr_text="胜利 点击退出")
    _patch_lingta_open(runner)
    snapshots = iter(
        (
            {
                "ok": True,
                "current_tower_id": 1426,
                "chain_pass_count": 0,
                "config_bounds_complete": True,
                "has_current_tower_config": True,
            },
            {
                "ok": True,
                "current_tower_id": 1427,
                "chain_pass_count": 1,
                "config_bounds_complete": True,
                "has_current_tower_config": False,
            },
        )
    )
    runner._read_lingta_challenge_snapshot = lambda: next(snapshots)
    runner._set_scheduler_task_payload_flag = lambda *_args: True
    cleared: list[tuple] = []
    runner._clear_scheduler_task_payload_flag = lambda *args: cleared.append(args)

    with pytest.raises(RuntimeError, match="越过本地最大灵塔配置"):
        _drain(runner.灵塔挑战流程(runtime))

    assert len([action for action in runtime.actions if action[0] == "click_ocr"]) == 1
    assert cleared == []


def test_lingta_flow_accepts_server_exit_after_twenty_wins_as_daily_limit() -> None:
    runner = create_behavior_tree_runtime_runner()
    updates = _capture_next_time(runner)
    runtime = _FakeLingtaRuntime((34, 34))
    _patch_lingta_open(runner)
    snapshots = iter(
        (
            {"ok": True, "current_tower_id": 1426, "chain_pass_count": 0},
            {"ok": True, "current_tower_id": 1446, "chain_pass_count": 20},
        )
    )
    runner._read_lingta_challenge_snapshot = lambda: next(snapshots)
    runner._set_scheduler_task_payload_flag = lambda *_args: True
    cleared: list[tuple] = []
    runner._clear_scheduler_task_payload_flag = lambda *args: cleared.append(args)

    result = _drain(runner.灵塔挑战流程(runtime))

    assert result["result"] == "success"
    assert result["outcome"] == "daily_limit"
    assert "本轮通过 20 层" in result["message"]
    assert "每日 20 层上限" in result["message"]
    assert "next_time" not in result
    assert updates[0][1].endswith("07:00:00")
    assert cleared == [("lingta-challenge", "lingta_auto_chain_started")]
    assert len([action for action in runtime.actions if action[0] == "click_ocr"]) == 1


def test_lingta_flow_classifies_early_stable_exit_as_no_next_floor() -> None:
    runner = create_behavior_tree_runtime_runner()
    updates = _capture_next_time(runner)
    runtime = _FakeLingtaRuntime((34, 34))
    _patch_lingta_open(runner)
    snapshots = iter(
        (
            {"ok": True, "current_tower_id": 1426, "chain_pass_count": 0},
            {"ok": True, "current_tower_id": 1429, "chain_pass_count": 3},
        )
    )
    runner._read_lingta_challenge_snapshot = lambda: next(snapshots)
    runner._set_scheduler_task_payload_flag = lambda *_args: True
    cleared: list[tuple] = []
    runner._clear_scheduler_task_payload_flag = lambda *args: cleared.append(args)

    result = _drain(runner.灵塔挑战流程(runtime))

    assert result["result"] == "success"
    assert result["outcome"] == "no_next_floor"
    assert "本轮通过 3 层" in result["message"]
    assert "没有可继续挑战的下一层" in result["message"]
    assert "next_time" not in result
    assert updates[0][1].endswith("07:00:00")
    assert cleared == [("lingta-challenge", "lingta_auto_chain_started")]
    assert len([action for action in runtime.actions if action[0] == "click_ocr"]) == 1


def _prepare_lingta_stuck_after_click(runner, runtime, monkeypatch):
    _patch_lingta_open(runner)
    runner._read_lingta_challenge_snapshot = lambda: {
        "ok": True,
        "current_tower_id": 1426,
        "chain_pass_count": 0,
    }
    persisted: list[tuple] = []
    cleared: list[tuple] = []
    runner._set_scheduler_task_payload_flag = lambda *args: persisted.append(args) or True
    runner._clear_scheduler_task_payload_flag = lambda *args: cleared.append(args)
    runner._preserve_lingta_settlement_evidence = lambda *_args, **_kwargs: "；evidence"
    monotonic_values = iter((0.0, 0.0, 4.0))
    monkeypatch.setattr(
        lingta_challenge_module.time,
        "monotonic",
        lambda: next(monotonic_values),
    )
    return persisted, cleared


def test_lingta_stuck_532_cleanup_success_keeps_marker_and_primary_error(monkeypatch) -> None:
    runner = create_behavior_tree_runtime_runner()
    runtime = _FakeLingtaRuntime(
        (34, 532),
        payload={
            "__scheduler_task_id": "lingta-challenge",
            "start_transition_grace_seconds": 3,
        },
    )
    persisted, cleared = _prepare_lingta_stuck_after_click(runner, runtime, monkeypatch)

    with pytest.raises(RuntimeError, match="点击挑战后未观察到离开 #532"):
        _drain(runner.灵塔挑战流程(runtime))

    assert ("goto", 34) in runtime.actions
    assert len([action for action in runtime.actions if action[0] == "click_ocr"]) == 1
    assert persisted and cleared == []
    assert runtime.payload["lingta_auto_chain_started"] == persisted[-1][2]


def test_lingta_stuck_532_cleanup_failure_does_not_replace_primary_error(monkeypatch) -> None:
    class CleanupFailureRuntime(_FakeLingtaRuntime):
        def goto_view(self, scene_id):
            self.actions.append(("goto", scene_id))
            if False:
                yield None
            raise RuntimeError("cleanup route unavailable")

    runner = create_behavior_tree_runtime_runner()
    runtime = CleanupFailureRuntime(
        (34, 532),
        payload={
            "__scheduler_task_id": "lingta-challenge",
            "start_transition_grace_seconds": 3,
        },
    )
    persisted, cleared = _prepare_lingta_stuck_after_click(runner, runtime, monkeypatch)

    with pytest.raises(RuntimeError, match="点击挑战后未观察到离开 #532") as caught:
        _drain(runner.灵塔挑战流程(runtime))

    assert "cleanup route unavailable" not in str(caught.value)
    assert any(
        "局部清场失败：RuntimeError: cleanup route unavailable" in note
        for note in getattr(caught.value, "__notes__", ())
    )
    assert persisted and cleared == []
    assert runtime.payload["lingta_auto_chain_started"] == persisted[-1][2]


@pytest.mark.parametrize(
    "cleanup_error",
    [
        InterruptedError("cell stopped"),
        GeneratorExit("generator closed"),
        FanxiuEmulatorRestartRequired("device restarted"),
    ],
)
def test_lingta_stuck_532_cleanup_control_flow_errors_propagate(
    monkeypatch,
    cleanup_error,
) -> None:
    class InterruptedCleanupRuntime(_FakeLingtaRuntime):
        def goto_view(self, scene_id):
            self.actions.append(("goto", scene_id))
            if False:
                yield None
            raise cleanup_error

    runner = create_behavior_tree_runtime_runner()
    runtime = InterruptedCleanupRuntime(
        (34, 532),
        payload={
            "__scheduler_task_id": "lingta-challenge",
            "start_transition_grace_seconds": 3,
        },
    )
    persisted, cleared = _prepare_lingta_stuck_after_click(runner, runtime, monkeypatch)

    with pytest.raises(type(cleanup_error)) as caught:
        _drain(runner.灵塔挑战流程(runtime))

    assert caught.value is cleanup_error
    assert persisted and cleared == []
