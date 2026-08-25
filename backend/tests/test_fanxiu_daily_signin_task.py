from __future__ import annotations

import base64
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import cv2
import numpy as np

from backend.core.fanxiu.data_annotation.default_jobs import (
    register_fanxiu_data_annotation_default_runtime_jobs,
)
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
)
from backend.core.fanxiu.data_annotation.scheduler_defaults import (
    default_data_annotation_scheduler_tasks,
)
from backend.core.fanxiu.data_annotation.tasks.daily_signin import (
    DailySigninTaskMixin,
    daily_signin_day_box,
    daily_signin_day_green_check_ratio,
    daily_signin_milestone_boxes,
    parse_daily_signin_claimed,
)
from backend.core.fanxiu.data_annotation.effective_time import job_effective_time
from backend.core.fanxiu.instrumentation.activity_signin import (
    ActivitySigninMilestone,
    ActivitySigninSnapshot,
)


def test_daily_signin_reconstructs_one_occluded_slot_from_complete_ordered_row():
    tokens: list[dict[str, Any]] = []
    for index, day in enumerate((22, 23, 24, 25)):
        tokens.extend(_tokens(f"第{day}天", y=1020, parent=f"day-{day}"))
        shift = 162 * index + 137
        for token in tokens[-len(f"第{day}天"):]:
            token["x"] += shift

    box = daily_signin_day_box(tokens, 21)

    assert box is not None
    assert box["source"] == "ordered_row_inference"
    assert box["x"] == pytest.approx(75.0, abs=3.0)


def test_daily_signin_refuses_occluded_slot_when_row_pitch_is_unstable():
    tokens: list[dict[str, Any]] = []
    for index, day in enumerate((22, 23, 24, 25)):
        row = _tokens(f"第{day}天", y=1020, parent=f"day-{day}")
        shift = (162 * index + 137) + (30 if day == 24 else 0)
        for token in row:
            token["x"] += shift
        tokens.extend(row)

    assert daily_signin_day_box(tokens, 21) is None
from backend.core.fanxiu.instrumentation.activity_menu import (
    ActivityMenuItem,
    ActivityMenuReadTimings,
    ActivityMenuSnapshot,
)


def _tokens(text: str, *, y: float = 100, parent: str = "line") -> list[dict[str, Any]]:
    return [
        {
            "text": char,
            "x": 100 + index * 12,
            "y": y,
            "w": 10,
            "h": 20,
            "parent_line_id": parent,
            "line_order": 0,
            "order": index,
        }
        for index, char in enumerate(text)
    ]


def _run(generator):
    while True:
        try:
            next(generator)
        except StopIteration as stop:
            return stop.value


def _menu_snapshot(
    kind: str,
    items: list[ActivityMenuItem],
    *,
    fingerprint: str | None = None,
    complete: bool = True,
) -> ActivityMenuSnapshot:
    return ActivityMenuSnapshot(
        kind=kind,  # type: ignore[arg-type]
        status="loaded" if complete else "not_loaded",
        complete=complete,
        items=tuple(items) if complete else (),
        pid=101,
        process_start_ticks=202,
        fingerprint=fingerprint or f"{kind}-stable",
        reason="test",
        timings=ActivityMenuReadTimings(0, 0, 0, 0, "test"),
    )


class _FakeSigninRuntime:
    def __init__(
        self,
        *,
        claimed: int = 22,
        total: int = 28,
        calendar_day: int = 23,
        date_present: bool = True,
        increment_after_click: bool = True,
        signin_visible_after_ocr: int | None = 1,
        signin_ocr_text: str = "每日签到",
        promotion_visible_after_ocr: int | None = 1,
        milestone_claimable_days: set[int] | None = None,
        signed_days: set[int] | None = None,
    ) -> None:
        self.scene = 34
        self.claimed = claimed
        self.total = total
        self.calendar_day = calendar_day
        self.signed_days = set(range(1, claimed + 1)) if signed_days is None else set(signed_days)
        self.date_present = date_present
        self.increment_after_click = increment_after_click
        self.signin_visible_after_ocr = signin_visible_after_ocr
        self.signin_ocr_text = signin_ocr_text
        self.signin_ocr_calls = 0
        self.promotion_visible_after_ocr = promotion_visible_after_ocr
        self.promotion_ocr_calls = 0
        self.date_clicked = False
        self.milestone_claimable_days = set(milestone_claimable_days or ())
        self.milestone_claimed_days = {
            day for day in (3, 7, 14, 21, 28) if day <= claimed
        } - self.milestone_claimable_days
        self.return_clicks = 0
        self.shape_clicks: list[tuple[int, str]] = []
        self.point_clicks: list[tuple[int, float, float]] = []

    def cur_frame(self, update: bool = False) -> str:
        return f"frame-{self.scene}-{int(update)}"

    def ocr_tokens(self, _frame: str) -> list[dict[str, Any]]:
        if self.scene == 34:
            self.promotion_ocr_calls += 1
            if (
                self.promotion_visible_after_ocr is None
                or self.promotion_ocr_calls < self.promotion_visible_after_ocr
            ):
                return []
            return _tokens("特惠", y=700)
        if self.scene == 403:
            self.signin_ocr_calls += 1
            if (
                self.signin_visible_after_ocr is None
                or self.signin_ocr_calls < self.signin_visible_after_ocr
            ):
                return []
            return _tokens(self.signin_ocr_text, y=720)
        return []

    def ocr_lines(self, frame: str) -> list[dict[str, Any]]:
        return self.ocr_tokens(frame)

    def ocr_tokens_in_shapes(
        self,
        _scene: int,
        shapes: list[str],
        **_kwargs,
    ) -> list[dict[str, Any]]:
        if shapes == ["每日签到"]:
            self.signin_ocr_calls += 1
            if (
                self.signin_visible_after_ocr is None
                or self.signin_ocr_calls < self.signin_visible_after_ocr
            ):
                return []
            return _tokens(self.signin_ocr_text, y=720)
        if shapes == ["特惠活动网格"]:
            return []
        if shapes == ["已领"]:
            value = self.claimed
            if self.date_clicked and self.increment_after_click:
                value += 1
            return _tokens(f"{value}/{self.total}", y=760)
        if shapes == ["日期"]:
            if not self.date_present:
                return []
            return _tokens(f"第{self.calendar_day}天", y=1025)
        if shapes == ["累签奖励"]:
            result: list[dict[str, Any]] = []
            for index, day in enumerate((3, 7, 14, 21, 28)):
                row = _tokens(f"第{day}天", y=800, parent=f"day-{day}")
                for token in row:
                    token["x"] += index * 145
                result.extend(row)
            return result
        raise AssertionError(shapes)

    def click_frame_point(self, scene: int, x: float, y: float) -> None:
        self.point_clicks.append((scene, x, y))
        if scene == 404:
            if y > 900:
                self.date_clicked = True
            elif self.milestone_claimable_days:
                day = min(self.milestone_claimable_days)
                self.milestone_claimable_days.remove(day)
                self.milestone_claimed_days.add(day)

    def wait_view(self, *scenes: int, **_kwargs):
        self.scene = int(scenes[0])
        if False:
            yield None
        return self.scene

    def goto_view(self, scene: int):
        self.scene = int(scene)
        if False:
            yield None
        return self.scene

    def wait_action_settle(self, _seconds: float):
        if False:
            yield None

    def click_shape_center(self, scene: int, shape: str) -> None:
        self.shape_clicks.append((scene, shape))
        if scene == 404 and shape == "最终大奖。" and 28 in self.milestone_claimable_days:
            self.milestone_claimable_days.remove(28)
            self.milestone_claimed_days.add(28)
        if shape == "返回":
            self.return_clicks += 1
            if self.return_clicks >= 2:
                self.scene = 34

    def current_scene(self, _scenes: list[int], *, update: bool = False):
        return self.scene, 100.0, self.cur_frame(update)


class _FakeSigninRunner(DailySigninTaskMixin):
    def __init__(self, runtime: _FakeSigninRuntime) -> None:
        self.runtime = runtime
        self.next_times: list[tuple[str, str | None]] = []
        self.logs: list[tuple[str, str]] = []
        self.menu_reads: list[str] = []
        self.menu_snapshots: dict[str, list[ActivityMenuSnapshot]] = {
            "world_left": [
                _menu_snapshot(
                    "world_left",
                    [ActivityMenuItem(1, "group:110001", "特惠", group_type=110001)],
                )
            ],
            "group_popup": [
                _menu_snapshot(
                    "group_popup",
                    [
                        ActivityMenuItem(1, "activity:101", "每日签到", activity_id=101),
                        ActivityMenuItem(2, "activity:102", "成长基金", activity_id=102),
                    ],
                )
            ],
        }

    def _fanxiu_runtime(self, *_args, **_kwargs):
        return self.runtime

    def _daily_signin_calendar_day(self, now=None) -> int:
        del now
        return self.runtime.calendar_day

    def _daily_signin_read_activity_menu(self, kind: str) -> ActivityMenuSnapshot:
        self.menu_reads.append(kind)
        snapshots = self.menu_snapshots[kind]
        return snapshots.pop(0) if len(snapshots) > 1 else snapshots[0]

    def _persist_scheduler_task_next_time(self, task_id: str, next_time: str | None) -> None:
        self.next_times.append((task_id, next_time))

    def _daily_signin_read_milestone_snapshot(self) -> ActivitySigninSnapshot:
        signed_days = set(self.runtime.signed_days)
        if self.runtime.date_clicked and self.runtime.increment_after_click:
            signed_days.add(self.runtime.calendar_day)
        reached = len(signed_days)
        return ActivitySigninSnapshot(
            activity_id=1310001,
            turn_id=1,
            total_days=self.runtime.total,
            signed_days=tuple(sorted(signed_days)),
            signed_day_count=reached,
            got_reward_ids=tuple(
                1_310_053 + index
                for index, day in enumerate((3, 7, 14, 21, 28), start=1)
                if day in self.runtime.milestone_claimed_days
            ),
            milestones=tuple(
                ActivitySigninMilestone(
                    day=day,
                    can_get_reward=day in self.runtime.milestone_claimable_days,
                )
                for day in (3, 7, 14, 21, 28)
            ),
            captured_at="2026-08-22T03:00:00+08:00",
            pid=101,
            process_start_ticks=202,
        )

    def _log(self, kind: str, message: str) -> None:
        self.logs.append((kind, message))


def test_daily_signin_parses_fraction_and_keeps_milestone_lines_separate():
    assert parse_daily_signin_claimed(_tokens("２３／２８")) == (23, 28)
    assert parse_daily_signin_claimed(_tokens("23丨28")) == (23, 28)
    assert parse_daily_signin_claimed(_tokens("23 28")) == (23, 28)
    assert parse_daily_signin_claimed(_tokens("第2天２３／２８")) == (23, 28)

    milestone_tokens = [
        *_tokens("第3天", parent="day-3"),
        *_tokens("第7天", parent="day-7"),
        *_tokens("第23天", parent="day-23"),
    ]
    boxes = daily_signin_milestone_boxes(milestone_tokens)
    assert sorted(boxes) == [3, 7, 23]
    assert boxes[23][0]["x"] == 100
    assert boxes[23][0]["w"] == 46


def test_daily_signin_green_check_ratio_uses_the_day_label_as_a_visual_anchor():
    image = np.zeros((400, 300, 3), dtype=np.uint8)
    # The derived check center is label bottom + 100 pixels.
    cv2.circle(image, (120, 170), 20, (0, 220, 0), thickness=-1)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    frame = "data:image/png;base64," + base64.b64encode(encoded.tobytes()).decode("ascii")
    day_box = {"x": 90.0, "y": 40.0, "w": 60.0, "h": 30.0, "day": 16}

    assert daily_signin_day_green_check_ratio(frame, day_box) > 0.03

    blank = np.zeros((400, 300, 3), dtype=np.uint8)
    ok, encoded = cv2.imencode(".png", blank)
    assert ok
    blank_frame = "data:image/png;base64," + base64.b64encode(encoded.tobytes()).decode("ascii")
    assert daily_signin_day_green_check_ratio(blank_frame, day_box) == 0.0


def test_daily_signin_retries_small_claimed_fraction_with_crop_ocr():
    class Runtime:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def cur_frame(self, update: bool = False) -> str:
            assert update is True
            return "frame"

        def ocr_tokens_in_shapes(self, *_args, **kwargs):
            self.calls.append(dict(kwargs))
            if kwargs.get("crop"):
                return _tokens("28/28")
            return []

    runtime = Runtime()

    assert DailySigninTaskMixin._daily_signin_read_claimed(runtime) == (28, 28)
    assert runtime.calls == [
        {"frame_data_url": "frame", "padding": 0},
        {"frame_data_url": "frame", "padding": 8, "crop": True},
    ]


def test_daily_signin_retries_a_fresh_frame_after_claim_animation():
    class Runtime:
        def __init__(self) -> None:
            self.frame_index = 0

        def cur_frame(self, update: bool = False) -> str:
            assert update is True
            self.frame_index += 1
            return f"frame-{self.frame_index}"

        def ocr_tokens_in_shapes(self, *_args, frame_data_url: str, crop: bool = False, **_kwargs):
            if frame_data_url == "frame-1":
                return []
            return _tokens("1丨28") if not crop else []

    runtime = Runtime()

    assert DailySigninTaskMixin._daily_signin_read_claimed(runtime) == (1, 28)
    assert runtime.frame_index == 2


def test_daily_signin_spaces_whole_ocr_retries_while_animation_settles():
    class Runtime:
        def __init__(self) -> None:
            self.settle_calls = 0

        def cur_frame(self, update: bool = False) -> str:
            assert update is True
            return f"frame-{self.settle_calls}"

        def ocr_tokens_in_shapes(self, *_args, **_kwargs):
            return _tokens("1 28") if self.settle_calls else []

        def wait_action_settle(self, _seconds: float):
            self.settle_calls += 1
            if False:
                yield None

    runtime = Runtime()
    runner = _FakeSigninRunner(runtime)  # type: ignore[arg-type]

    assert _run(runner._daily_signin_read_claimed_after_animation(runtime)) == (1, 28)
    assert runtime.settle_calls == 1


def test_daily_signin_claims_next_day_and_returns_to_world():
    runtime = _FakeSigninRuntime()
    runner = _FakeSigninRunner(runtime)

    result = _run(
        runner._execute_daily_signin_task(
            {"asset_tree_path": Path("asset-tree.json")},
            threading.Event(),
        )
    )

    assert result["outcome"] == "claimed"
    assert result["claimed_before"] == 22
    assert result["claimed_after"] == 23
    assert runtime.scene == 34
    assert runtime.point_clicks[0][0] == 34
    assert runtime.point_clicks[0][2] == 680
    assert runtime.point_clicks[1][0] == 403
    assert runtime.point_clicks[1][2] == 700
    assert runtime.point_clicks[2][0] == 404
    assert runtime.point_clicks[2][2] == 1065


def test_daily_signin_retries_entry_ocr_with_fresh_frame():
    runtime = _FakeSigninRuntime(signin_visible_after_ocr=2)
    runner = _FakeSigninRunner(runtime)

    result = _run(
        runner._execute_daily_signin_task(
            {"asset_tree_path": Path("asset-tree.json")},
            threading.Event(),
        )
    )

    assert result["outcome"] == "claimed"
    assert runtime.signin_ocr_calls == 2
    assert any("刷新重试 1/2" in message for _kind, message in runner.logs)


def test_daily_signin_uses_narrow_double_frame_fallback_when_popup_runtime_is_not_loaded():
    runtime = _FakeSigninRuntime()
    runner = _FakeSigninRunner(runtime)
    runner.menu_snapshots["group_popup"] = [
        _menu_snapshot("group_popup", [], complete=False)
    ]

    result = _run(
        runner._execute_daily_signin_task(
            {"asset_tree_path": Path("asset-tree.json")},
            threading.Event(),
        )
    )

    assert result["outcome"] == "claimed"
    assert len([point for point in runtime.point_clicks if point[0] == 403]) == 1
    assert any("双帧唯一" in message for kind, message in runner.logs if kind == "info")


def test_daily_signin_retries_world_promotion_ocr_without_restarting_whole_task():
    runtime = _FakeSigninRuntime(promotion_visible_after_ocr=2)
    runner = _FakeSigninRunner(runtime)

    result = _run(
        runner._execute_daily_signin_task(
            {"asset_tree_path": Path("asset-tree.json")},
            threading.Event(),
        )
    )

    assert result["outcome"] == "claimed"
    assert runtime.promotion_ocr_calls == 2
    assert any("#34 左侧「特惠」暂未安全对齐" in message for _kind, message in runner.logs)


@pytest.mark.parametrize("ocr_text", ["签到", "每日签到", "每曰签到"])
def test_daily_signin_locates_stable_signin_suffix_when_optional_prefix_is_missing_or_wrong(
    ocr_text: str,
):
    runtime = _FakeSigninRuntime(signin_ocr_text=ocr_text)
    runner = _FakeSigninRunner(runtime)

    result = _run(
        runner._execute_daily_signin_task(
            {"asset_tree_path": Path("asset-tree.json")},
            threading.Event(),
        )
    )

    assert result["outcome"] == "claimed"
    assert runtime.point_clicks[1][0] == 403


def test_daily_signin_consumes_the_same_frame_that_proved_scene_403_before_refreshing():
    class OccludedAfterRefreshRuntime(_FakeSigninRuntime):
        def current_scene(self, _scenes: list[int], *, update: bool = False):
            assert update is True
            return self.scene, 100.0, "frame-403-proven"

        def ocr_tokens_in_shapes(self, _scene, shapes, *, frame_data_url, **kwargs):
            if shapes == ["每日签到"]:
                self.signin_ocr_calls += 1
                return _tokens("每日签到", y=720) if frame_data_url == "frame-403-proven" else []
            return super().ocr_tokens_in_shapes(
                _scene,
                shapes,
                frame_data_url=frame_data_url,
                **kwargs,
            )

    runtime = OccludedAfterRefreshRuntime()
    runner = _FakeSigninRunner(runtime)

    result = _run(
        runner._execute_daily_signin_task(
            {"asset_tree_path": Path("asset-tree.json")},
            threading.Event(),
        )
    )

    assert result["outcome"] == "claimed"
    assert runtime.signin_ocr_calls == 1


def test_daily_signin_falls_back_to_the_live_403_grid_when_the_narrow_action_roi_reflows():
    class ReflowedSigninRuntime(_FakeSigninRuntime):
        def ocr_tokens_in_shapes(self, scene, shapes, **kwargs):
            if shapes == ["每日签到"]:
                self.signin_ocr_calls += 1
                return []
            if shapes == ["特惠活动网格"]:
                return _tokens("每日签到", y=382)
            return super().ocr_tokens_in_shapes(scene, shapes, **kwargs)

    runtime = ReflowedSigninRuntime()
    runner = _FakeSigninRunner(runtime)

    result = _run(
        runner._execute_daily_signin_task(
            {"asset_tree_path": Path("asset-tree.json")},
            threading.Event(),
        )
    )

    assert result["outcome"] == "claimed"
    assert runtime.signin_ocr_calls == 1
    assert runtime.point_clicks[1][0] == 403


def test_daily_signin_splits_runtime_names_from_a_joined_reflowed_grid_row():
    class JoinedReflowRuntime(_FakeSigninRuntime):
        def __init__(self) -> None:
            super().__init__()
            self.grid_crop_values: list[bool] = []

        def ocr_tokens_in_shapes(self, scene, shapes, **kwargs):
            if shapes == ["每日签到"]:
                self.signin_ocr_calls += 1
                return []
            if shapes == ["特惠活动网格"]:
                self.grid_crop_values.append(bool(kwargs.get("crop")))
                return _tokens("每日签到试炼手册成长基金", y=570)
            return super().ocr_tokens_in_shapes(scene, shapes, **kwargs)

    runtime = JoinedReflowRuntime()
    runner = _FakeSigninRunner(runtime)
    runner.menu_snapshots["group_popup"] = [
        _menu_snapshot(
            "group_popup",
            [
                ActivityMenuItem(1, "activity:101", "每日签到", activity_id=101),
                ActivityMenuItem(2, "activity:102", "试炼手册", activity_id=102),
                ActivityMenuItem(3, "activity:103", "成长基金", activity_id=103),
            ],
        )
    ]

    result = _run(
        runner._execute_daily_signin_task(
            {"asset_tree_path": Path("asset-tree.json")},
            threading.Event(),
        )
    )

    assert result["outcome"] == "claimed"
    signin_click = next(point for point in runtime.point_clicks if point[0] == 403)
    assert signin_click[2] == pytest.approx(550.0)
    assert runtime.grid_crop_values == [True]


def test_daily_signin_never_reuses_the_404_return_coordinate_after_landing_on_scene_20():
    class Scene20AfterReturnRuntime(_FakeSigninRuntime):
        def click_shape_center(self, scene: int, shape: str) -> None:
            self.shape_clicks.append((scene, shape))
            if shape == "返回":
                self.return_clicks += 1
                self.scene = 20

    runtime = Scene20AfterReturnRuntime()
    runner = _FakeSigninRunner(runtime)

    result = _run(
        runner._execute_daily_signin_task(
            {"asset_tree_path": Path("asset-tree.json")},
            threading.Event(),
        )
    )

    assert result["outcome"] == "claimed"
    assert runtime.return_clicks == 1
    assert runtime.scene == 34


def test_daily_signin_refuses_a_long_joined_ocr_line_instead_of_reusing_target_shape():
    runtime = _FakeSigninRuntime(signin_ocr_text="每日签到试炼手册成长基金")
    runner = _FakeSigninRunner(runtime)

    with pytest.raises(RuntimeError, match="未安全对齐"):
        _run(
            runner._execute_daily_signin_task(
                {"asset_tree_path": Path("asset-tree.json")},
                threading.Event(),
            )
        )

    # Only the Runtime-authorized world group was clicked.  The old target
    # shape/OCR shortcut is not used to guess the second-stage entry.
    assert [scene for scene, _x, _y in runtime.point_clicks] == [34]


def test_daily_signin_missing_entry_is_error_instead_of_false_success():
    runtime = _FakeSigninRuntime(signin_visible_after_ocr=None)
    runner = _FakeSigninRunner(runtime)

    with pytest.raises(RuntimeError, match="未安全对齐"):
        _run(
            runner._execute_daily_signin_task(
                {"asset_tree_path": Path("asset-tree.json")},
                threading.Event(),
                {"__scheduler_task_id": "daily-signin"},
            )
    )

    assert runtime.signin_ocr_calls == 3
    # #403 has no independently proven return edge.  A failed alignment keeps
    # its live popup intact so the next retry retains the real failure state.
    assert runtime.scene == 403
    assert runner.next_times == []


def test_daily_signin_runtime_not_loaded_never_lets_matching_ocr_authorize_click():
    runtime = _FakeSigninRuntime()
    runner = _FakeSigninRunner(runtime)
    runner.menu_snapshots["world_left"] = [
        _menu_snapshot("world_left", [], complete=False),
    ]

    with pytest.raises(RuntimeError, match="incomplete_runtime"):
        _run(
            runner._execute_daily_signin_task(
                {"asset_tree_path": Path("asset-tree.json")},
                threading.Event(),
            )
        )

    assert runtime.point_clicks == []


def test_daily_signin_runtime_target_absent_never_clicks_same_text_ocr():
    runtime = _FakeSigninRuntime()
    runner = _FakeSigninRunner(runtime)
    runner.menu_snapshots["world_left"] = [
        _menu_snapshot(
            "world_left",
            [ActivityMenuItem(1, "group:120010", "特惠", group_type=120010)],
        ),
    ]

    with pytest.raises(RuntimeError, match="target_not_found"):
        _run(
            runner._execute_daily_signin_task(
                {"asset_tree_path": Path("asset-tree.json")},
                threading.Event(),
            )
        )

    assert runtime.point_clicks == []


def test_daily_signin_rereads_runtime_fingerprint_before_each_click():
    runtime = _FakeSigninRuntime()
    runner = _FakeSigninRunner(runtime)
    item = ActivityMenuItem(1, "group:110001", "特惠", group_type=110001)
    runner.menu_snapshots["world_left"] = [
        _menu_snapshot("world_left", [item], fingerprint="old"),
        _menu_snapshot("world_left", [item], fingerprint="new"),
    ]

    result = _run(
        runner._execute_daily_signin_task(
            {"asset_tree_path": Path("asset-tree.json")},
            threading.Event(),
        )
    )

    assert result["outcome"] == "claimed"
    assert [scene for scene, _x, _y in runtime.point_clicks[:2]] == [34, 403]
    assert any("有序清单已变化" in message for _kind, message in runner.logs)


def test_daily_signin_uses_calendar_day_instead_of_claimed_count():
    runtime = _FakeSigninRuntime(claimed=1, calendar_day=3)
    runner = _FakeSigninRunner(runtime)

    result = _run(
        runner._execute_daily_signin_task(
            {"asset_tree_path": Path("asset-tree.json")},
            threading.Event(),
        )
    )

    assert result["outcome"] == "claimed"
    assert result["claimed_before"] == 1
    assert result["claimed_after"] == 2
    assert "今天第3天" in result["message"]


def test_daily_signin_calendar_day_uses_planned_business_clock():
    with job_effective_time({"effective_now": "2026-08-25 00:00:00"}):
        assert DailySigninTaskMixin._daily_signin_calendar_day() == 25
    assert DailySigninTaskMixin._daily_signin_calendar_day(datetime(2026, 8, 24, 23, 11)) == 24


def test_daily_signin_count_never_substitutes_for_target_day_membership():
    runtime = _FakeSigninRuntime(
        claimed=25,
        calendar_day=25,
        signed_days=set(range(1, 25)) | {26},
    )
    runner = _FakeSigninRunner(runtime)

    result = _run(
        runner._execute_daily_signin_task(
            {"asset_tree_path": Path("asset-tree.json")},
            threading.Event(),
        )
    )

    assert result["outcome"] == "claimed"
    assert runtime.date_clicked is True
    assert result["business_evidence"]["target_membership"] is True
    assert result["business_evidence"]["signed_days_delta"] == [25]


def test_daily_signin_green_noise_never_substitutes_for_runtime_membership(
    monkeypatch: pytest.MonkeyPatch,
):
    runtime = _FakeSigninRuntime(claimed=24, calendar_day=25)
    runner = _FakeSigninRunner(runtime)
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.daily_signin.daily_signin_day_green_check_ratio",
        lambda *_args, **_kwargs: 0.99,
    )

    result = _run(
        runner._execute_daily_signin_task(
            {"asset_tree_path": Path("asset-tree.json")},
            threading.Event(),
        )
    )

    assert result["outcome"] == "claimed"
    assert runtime.date_clicked is True


def test_daily_signin_rejects_post_click_count_without_membership_delta():
    runtime = _FakeSigninRuntime(claimed=24, calendar_day=25)

    class CountOnlyRunner(_FakeSigninRunner):
        def _daily_signin_read_milestone_snapshot(self) -> ActivitySigninSnapshot:
            snapshot = super()._daily_signin_read_milestone_snapshot()
            if self.runtime.date_clicked:
                return ActivitySigninSnapshot(
                    activity_id=snapshot.activity_id,
                    turn_id=snapshot.turn_id,
                    total_days=snapshot.total_days,
                    signed_days=tuple(range(1, 25)),
                    signed_day_count=25,
                    got_reward_ids=snapshot.got_reward_ids,
                    milestones=snapshot.milestones,
                    captured_at=snapshot.captured_at,
                    pid=snapshot.pid,
                    process_start_ticks=snapshot.process_start_ticks,
                )
            return snapshot

    runner = CountOnlyRunner(runtime)
    with pytest.raises(RuntimeError, match="已签到日期集合异常"):
        _run(
            runner._execute_daily_signin_task(
                {"asset_tree_path": Path("asset-tree.json")},
                threading.Event(),
            )
        )


def test_daily_signin_is_idempotent_before_click_when_today_is_claimed():
    runtime = _FakeSigninRuntime(claimed=3, calendar_day=3)
    runner = _FakeSigninRunner(runtime)

    result = _run(
        runner._execute_daily_signin_task(
            {"asset_tree_path": Path("asset-tree.json")},
            threading.Event(),
        )
    )

    assert result["outcome"] == "already_claimed"
    assert result["claimed_before"] == result["claimed_after"] == 3
    assert not any(scene == 404 for scene, _x, _y in runtime.point_clicks)
    assert runtime.scene == 34


def test_daily_signin_claims_a_reached_pending_milestone_when_today_is_already_claimed(
    monkeypatch: pytest.MonkeyPatch,
):
    runtime = _FakeSigninRuntime(
        claimed=22,
        calendar_day=22,
        milestone_claimable_days={21},
    )
    runner = _FakeSigninRunner(runtime)
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.daily_signin.daily_signin_day_green_check_ratio",
        lambda _frame, day_box, **_kwargs: (
            0.05 if int(day_box["day"]) in runtime.milestone_claimed_days else 0.0
        ),
    )

    result = _run(
        runner._execute_daily_signin_task(
            {"asset_tree_path": Path("asset-tree.json")},
            threading.Event(),
        )
    )

    assert result["outcome"] == "milestone_claimed"
    assert result["milestones_claimed"] == [21]
    assert runtime.milestone_claimable_days == set()
    assert 21 in runtime.milestone_claimed_days
    assert len([click for click in runtime.point_clicks if click[0] == 404]) == 1
    assert runtime.scene == 34


def test_daily_signin_claims_every_reached_pending_milestone(
    monkeypatch: pytest.MonkeyPatch,
):
    runtime = _FakeSigninRuntime(
        claimed=22,
        calendar_day=22,
        milestone_claimable_days={7, 21},
    )
    runner = _FakeSigninRunner(runtime)
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.daily_signin.daily_signin_day_green_check_ratio",
        lambda _frame, day_box, **_kwargs: (
            0.05 if int(day_box["day"]) in runtime.milestone_claimed_days else 0.0
        ),
    )

    result = _run(
        runner._execute_daily_signin_task(
            {"asset_tree_path": Path("asset-tree.json")},
            threading.Event(),
        )
    )

    assert result["milestones_claimed"] == [7, 21]
    assert runtime.milestone_claimable_days == set()
    assert len([click for click in runtime.point_clicks if click[0] == 404]) == 2


def test_daily_signin_refuses_an_unreached_milestone_even_if_runtime_marks_it_claimable():
    runtime = _FakeSigninRuntime(
        claimed=22,
        calendar_day=22,
        milestone_claimable_days={28},
    )
    runner = _FakeSigninRunner(runtime)

    with pytest.raises(RuntimeError, match="未达到的累签节点"):
        _run(
            runner._execute_daily_signin_task(
                {"asset_tree_path": Path("asset-tree.json")},
                threading.Event(),
            )
        )

    assert (404, "最终大奖。") not in runtime.shape_clicks


def test_daily_signin_claims_the_reached_final_milestone_once():
    runtime = _FakeSigninRuntime(
        claimed=28,
        total=28,
        calendar_day=28,
        milestone_claimable_days={28},
    )
    runner = _FakeSigninRunner(runtime)

    result = _run(
        runner._execute_daily_signin_task(
            {"asset_tree_path": Path("asset-tree.json")},
            threading.Event(),
        )
    )

    assert result["milestones_claimed"] == [28]
    assert runtime.shape_clicks.count((404, "最终大奖。")) == 1


def test_daily_signin_claims_reward_page_before_rereading_claimed_values():
    class RewardRuntime(_FakeSigninRuntime):
        def click_frame_point(self, scene: int, x: float, y: float) -> None:
            super().click_frame_point(scene, x, y)
            if scene == 404:
                self.scene = 250

    runtime = RewardRuntime()
    runner = _FakeSigninRunner(runtime)

    result = _run(
        runner._execute_daily_signin_task(
            {"asset_tree_path": Path("asset-tree.json")},
            threading.Event(),
        )
    )

    assert result["outcome"] == "claimed"
    assert (250, "领取") in runtime.shape_clicks
    assert runtime.scene == 34
    assert any("#250 奖励页" in message for _kind, message in runner.logs)


def test_daily_signin_fraction_not_changing_after_click_is_error():
    runtime = _FakeSigninRuntime(increment_after_click=False)
    runner = _FakeSigninRunner(runtime)

    with pytest.raises(RuntimeError, match="精确目标 delta"):
        _run(
            runner._execute_daily_signin_task(
                {"asset_tree_path": Path("asset-tree.json")},
                threading.Event(),
            )
        )

    assert runtime.scene == 404


def test_daily_signin_missing_today_never_advances_next_time():
    runtime = _FakeSigninRuntime(claimed=24, calendar_day=25, date_present=False)
    runner = _FakeSigninRunner(runtime)

    with pytest.raises(RuntimeError, match="日期格缺失不能证明"):
        _run(
            runner._execute_daily_signin_task(
                {"asset_tree_path": Path("asset-tree.json")},
                threading.Event(),
                {"__scheduler_task_id": "daily-signin"},
            )
        )

    assert runner.next_times == []


def test_daily_signin_commits_claimed_result_before_long_return_transition():
    class TransitionRuntime(_FakeSigninRuntime):
        def click_shape_center(self, scene: int, shape: str) -> None:
            self.shape_clicks.append((scene, shape))
            if scene == 404 and shape == "返回":
                self.scene = None

        def goto_view(self, scene: int):
            raise RuntimeError("world transition still has no stable scene")

    runtime = TransitionRuntime(claimed=3, calendar_day=3)
    runner = _FakeSigninRunner(runtime)

    result = _run(
        runner._execute_daily_signin_task(
            {"asset_tree_path": Path("asset-tree.json")},
            threading.Event(),
            {"__scheduler_task_id": "daily-signin"},
        )
    )

    assert result["outcome"] == "already_claimed"
    assert result["current_scene"] is None
    assert len(runner.next_times) == 1
    assert runner.next_times[0][0] == "daily-signin"
    assert any("业务已闭环" in message for kind, message in runner.logs if kind == "warning")


def test_daily_signin_full_cycle_does_not_repeat_an_already_claimed_final_prize():
    runtime = _FakeSigninRuntime(claimed=28, total=28, calendar_day=28, date_present=False)
    runner = _FakeSigninRunner(runtime)

    result = _run(
        runner._execute_daily_signin_task(
            {"asset_tree_path": Path("asset-tree.json")},
            threading.Event(),
        )
    )

    assert result["outcome"] == "already_claimed"
    assert (404, "最终大奖。") not in runtime.shape_clicks
    assert runtime.scene == 34


def test_daily_signin_does_not_catch_up_after_day_twenty_eight():
    runtime = _FakeSigninRuntime(claimed=27, calendar_day=29)
    runner = _FakeSigninRunner(runtime)

    result = _run(
        runner._execute_daily_signin_task(
            {"asset_tree_path": Path("asset-tree.json")},
            threading.Event(),
        )
    )

    assert result["outcome"] == "outside_daily_reward_days"
    assert not any(scene == 404 for scene, _x, _y in runtime.point_clicks)
    assert runtime.scene == 34


def test_daily_signin_is_manual_midnight_template_without_scene_policy():
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("daily_signin")
    assert definition is not None
    assert definition.label == "日常_签到"
    assert definition.scheduler_supported is True
    assert not hasattr(definition, "lifecycle")

    task = next(
        item
        for item in default_data_annotation_scheduler_tasks()
        if item["task_type"] == "daily_signin"
    )
    assert task["trigger_description"] == "每日"
    assert task["next_time"]


@pytest.mark.parametrize("initial_scene", [403, 404])
def test_daily_signin_cell_normalizes_transient_start_before_whole_job(initial_scene: int):
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("daily_signin")
    assert definition is not None

    class _Runtime:
        def __init__(self) -> None:
            self.goto_calls: list[int] = []

        def current_scene(self, _candidates, *, update=False):
            return initial_scene, 100.0, "frame"

        def goto_view(self, scene_id: int):
            self.goto_calls.append(scene_id)
            if False:
                yield None

    class _Runner:
        def __init__(self) -> None:
            self.runtime = _Runtime()

        def _fanxiu_runtime(self, _ctx, *, stop_event=None):
            return self.runtime

        def _execute_daily_signin_task(self, _ctx, _stop_event, _payload):
            if False:
                yield None
            return {"outcome": "already_claimed", "current_scene": None}

    runner = _Runner()
    result = _run(definition.handler(runner, {}, {}, threading.Event()))

    assert result["outcome"] == "already_claimed"
    assert runner.runtime.goto_calls == [34]
