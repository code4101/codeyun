from __future__ import annotations

import base64
from datetime import datetime

import cv2
import numpy as np
import pytest

from backend.core.fanxiu.data_annotation import behavior_tree_runtime
from backend.core.fanxiu.data_annotation.tasks import daily_foundation
from backend.core.fanxiu.data_annotation.default_jobs import register_fanxiu_data_annotation_default_runtime_jobs
from backend.core.fanxiu.data_annotation.jobs import get_fanxiu_data_annotation_task_cell_definition
from backend.core.fanxiu.data_annotation.scheduler_defaults import default_data_annotation_scheduler_tasks


class _Runner(behavior_tree_runtime.DailyFoundationTaskMixin):
    def __init__(self) -> None:
        self.logs: list[tuple[str, str]] = []

    def _log(self, kind: str, message: str) -> None:
        self.logs.append((kind, message))


class _Runtime:
    def __init__(
        self,
        *,
        scenes: list[int | None],
        ocr_results: list[tuple[list[int], str]],
        threshold: int = 2400,
        reward_tokens: list[dict[str, object]] | None = None,
    ) -> None:
        self.payload = {"weekly_activity_threshold": threshold}
        self.scenes = list(scenes)
        self.ocr_results = list(ocr_results)
        self.actions: list[tuple[object, ...]] = []
        self.frame_index = 0
        self.next_times: list[str | None] = []
        self.attrs: dict[str, object] = {}
        self.full_frame_ocr_calls = 0
        self.reward_tokens = reward_tokens or [
            {"text": str(milestone), "x": 245.0 + index * 85.0, "y": 348.0, "w": 50.0, "h": 25.0}
            for index, milestone in enumerate(daily_foundation.WEEKLY_ACTIVITY_REWARD_MILESTONES)
        ]

    def set_next_time(self, next_time: str | None) -> None:
        self.next_times.append(next_time)

    def go_scene(self, scene_id: int):
        self.actions.append(("go_scene", scene_id))
        if False:
            yield None
        return "success"

    def click_shape_center(self, scene_id: int, shape: str) -> None:
        self.actions.append(("click", scene_id, shape))

    def click_frame_point(self, scene_id: int, x: float, y: float) -> None:
        self.actions.append(("click_point", scene_id, x, y))

    def wait_action_settle(self, seconds: float):
        self.actions.append(("settle", seconds))
        if False:
            yield None
        return "success"

    def cur_frame(self, *, update: bool = False) -> str:
        assert update is True
        self.frame_index += 1
        return f"frame-{self.frame_index}"

    def current_scene(self, scene_ids, *, frame_data_url: str):
        assert scene_ids == [402]
        assert frame_data_url.startswith("frame-")
        scene_id = self.scenes.pop(0)
        return scene_id, 100.0 if scene_id == 402 else 0.0, frame_data_url

    def ocr_numbers_in_shapes(self, scene_id: int, shape_titles, **kwargs):
        assert scene_id == 402
        assert shape_titles == ["活跃度"]
        assert kwargs["padding"] == 0
        assert str(kwargs["frame_data_url"]).startswith("frame-")
        return self.ocr_results.pop(0)

    def full_frame_ocr_tokens(self, frame_data_url: str):
        assert str(frame_data_url).startswith("frame-")
        self.full_frame_ocr_calls += 1
        return self.reward_tokens


def _run(generator):
    while True:
        try:
            next(generator)
        except StopIteration as exc:
            return exc.value


def _weekly_snapshot(*, claimed: set[int]) -> dict[str, object]:
    claimable = [
        milestone
        for milestone in daily_foundation.WEEKLY_ACTIVITY_REWARD_MILESTONES
        if milestone not in claimed
    ]
    return {
        "complete": True,
        "status": "already_claimed" if not claimable else "claimable",
        "active_num": 2400,
        "thresholds": list(daily_foundation.WEEKLY_ACTIVITY_REWARD_MILESTONES),
        "claimed_thresholds": sorted(claimed),
        "claimable_thresholds": claimable,
    }


def test_weekly_activity_is_registered_for_thursday_midnight() -> None:
    register_fanxiu_data_annotation_default_runtime_jobs()
    task = next(item for item in default_data_annotation_scheduler_tasks() if item["id"] == "weekly-activity")
    definition = get_fanxiu_data_annotation_task_cell_definition("weekly_activity")

    assert task["task_type"] == "weekly_activity"
    assert task["label"] == "周常_活跃度"
    assert task["trigger_description"] == "每周"
    assert task["next_time"]
    assert task["payload"]["weekly_activity_threshold"] == 2400
    assert definition is not None
    assert definition.scheduler_supported is True
    assert not hasattr(definition, "lifecycle")


def test_weekly_activity_next_time_uses_friday_saturday_then_next_thursday() -> None:
    runner = _Runner()

    assert runner._next_weekly_activity_time_text(
        completed=False,
        now=datetime(2026, 7, 23, 0, 5),
    ) == "2026-07-24 00:00:00"
    assert runner._next_weekly_activity_time_text(
        completed=False,
        now=datetime(2026, 7, 24, 0, 5),
    ) == "2026-07-25 00:00:00"
    assert runner._next_weekly_activity_time_text(
        completed=True,
        now=datetime(2026, 7, 23, 0, 5),
    ) == "2026-07-30 00:00:00"
    assert runner._next_weekly_activity_time_text(
        completed=True,
        now=datetime(2026, 7, 25, 0, 5),
    ) == "2026-07-30 00:00:00"


def test_weekly_activity_repeats_scene_and_ocr_recognition_before_friday_retry(monkeypatch) -> None:
    monkeypatch.setattr(behavior_tree_runtime, "_now", lambda: datetime(2026, 7, 23, 0, 5))
    runtime = _Runtime(
        scenes=[None, 402, 402],
        ocr_results=[([], ""), ([2399], "2399")],
    )

    result = _run(_Runner().weekly_activity_flow(runtime))

    assert result["result"] == "success"
    assert "next_time" not in result
    assert runtime.next_times == ["2026-07-24 00:00:00"]
    assert result["current_scene"] == 34
    assert runtime.actions[0:2] == [("go_scene", 69), ("click", 69, "周常")]
    assert ("click", 402, "奖励") not in runtime.actions
    assert runtime.actions[-1] == ("go_scene", 34)
    assert runtime.frame_index == 3


def test_weekly_activity_at_threshold_claims_only_proven_tier_and_verifies_green_check(monkeypatch) -> None:
    monkeypatch.setattr(behavior_tree_runtime, "_now", lambda: datetime(2026, 7, 23, 0, 5))
    snapshots = [
        {
            milestone: {
                "state": "claimable" if milestone == 2400 else "claimed",
                "point": (float(milestone), 270.0),
            }
            for milestone in daily_foundation.WEEKLY_ACTIVITY_REWARD_MILESTONES
        },
        {
            milestone: {"state": "claimed", "point": (float(milestone), 270.0)}
            for milestone in daily_foundation.WEEKLY_ACTIVITY_REWARD_MILESTONES
        },
    ]
    monkeypatch.setattr(
        daily_foundation,
        "detect_weekly_activity_reward_states",
        lambda _frame, _layout: snapshots.pop(0),
    )
    runtime_snapshots = [
        _weekly_snapshot(claimed=set(daily_foundation.WEEKLY_ACTIVITY_REWARD_MILESTONES) - {2400}),
        _weekly_snapshot(claimed=set(daily_foundation.WEEKLY_ACTIVITY_REWARD_MILESTONES)),
    ]
    monkeypatch.setattr(
        daily_foundation,
        "read_weekly_activity_runtime_snapshot",
        lambda: runtime_snapshots.pop(0),
    )
    runtime = _Runtime(scenes=[402, 402], ocr_results=[([2400], "2400")])

    result = _run(_Runner().weekly_activity_flow(runtime))

    assert result["result"] == "success"
    assert "next_time" not in result
    assert runtime.next_times == ["2026-07-30 00:00:00"]
    assert ("click", 402, "奖励") not in runtime.actions
    assert ("click_point", 402, 2400.0, 270.0) in runtime.actions
    assert "本次领取 [2400]" in result["message"]
    assert runtime.actions[-1] == ("go_scene", 34)
    assert runtime.full_frame_ocr_calls == 3


def test_weekly_activity_all_claimed_is_zero_click_idempotent(monkeypatch) -> None:
    monkeypatch.setattr(behavior_tree_runtime, "_now", lambda: datetime(2026, 7, 23, 0, 5))
    monkeypatch.setattr(
        daily_foundation,
        "detect_weekly_activity_reward_states",
        lambda _frame, _layout: {
            milestone: {"state": "claimed", "point": (float(milestone), 270.0)}
            for milestone in daily_foundation.WEEKLY_ACTIVITY_REWARD_MILESTONES
        },
    )
    monkeypatch.setattr(
        daily_foundation,
        "read_weekly_activity_runtime_snapshot",
        lambda: _weekly_snapshot(claimed=set(daily_foundation.WEEKLY_ACTIVITY_REWARD_MILESTONES)),
    )
    runtime = _Runtime(scenes=[402], ocr_results=[([2400], "2400")])

    result = _run(_Runner().weekly_activity_flow(runtime))

    assert result["result"] == "success"
    assert "零点击幂等结束" in result["message"]
    assert not any(action[0] in {"click", "click_point"} and action[1] == 402 for action in runtime.actions)


def test_weekly_activity_unknown_eligible_tier_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(behavior_tree_runtime, "_now", lambda: datetime(2026, 7, 23, 0, 5))
    monkeypatch.setattr(
        daily_foundation,
        "detect_weekly_activity_reward_states",
        lambda _frame, _layout: {
            milestone: {
                "state": "unknown" if milestone == 2400 else "claimed",
                "point": (float(milestone), 270.0),
            }
            for milestone in daily_foundation.WEEKLY_ACTIVITY_REWARD_MILESTONES
        },
    )
    monkeypatch.setattr(
        daily_foundation,
        "read_weekly_activity_runtime_snapshot",
        lambda: _weekly_snapshot(claimed=set(daily_foundation.WEEKLY_ACTIVITY_REWARD_MILESTONES)),
    )
    runtime = _Runtime(scenes=[402], ocr_results=[([2400], "2400")])

    with pytest.raises(RuntimeError, match="2400:unknown!=Runtime-claimed"):
        _run(_Runner().weekly_activity_flow(runtime))
    assert not any(action[0] == "click_point" for action in runtime.actions)


def test_weekly_activity_invisible_runtime_claimable_tier_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(behavior_tree_runtime, "_now", lambda: datetime(2026, 7, 23, 0, 5))
    monkeypatch.setattr(
        daily_foundation,
        "detect_weekly_activity_reward_states",
        lambda _frame, _layout: {
            milestone: {"state": "claimed", "point": (float(milestone), 270.0)}
            for milestone in (1600, 2000, 2400)
        },
    )
    runtime = _Runtime(
        scenes=[402],
        ocr_results=[([2400], "2400")],
        reward_tokens=[
            {"text": "1600", "x": 344.0, "y": 348.0, "w": 54.0, "h": 25.0},
            {"text": "2000", "x": 543.0, "y": 348.0, "w": 56.0, "h": 25.0},
            {"text": "2400", "x": 745.0, "y": 348.0, "w": 56.0, "h": 25.0},
        ],
    )
    monkeypatch.setattr(
        daily_foundation,
        "read_weekly_activity_runtime_snapshot",
        lambda: _weekly_snapshot(claimed={1600, 2000, 2400}),
    )

    with pytest.raises(RuntimeError, match=r"Runtime 可领档 \[400, 600, 800, 1200\].*拒绝猜滑动"):
        _run(_Runner().weekly_activity_flow(runtime))

    assert runtime.next_times == []
    assert not any(action[0] == "click_point" for action in runtime.actions)


def test_weekly_activity_visual_classifier_distinguishes_claimed_claimable_and_unknown() -> None:
    image = np.zeros((1600, 900, 3), dtype=np.uint8)
    layout = {
        1600: {"point": (371.0, 270.0)},
        2000: {"point": (571.0, 270.0)},
        2400: {"point": (773.0, 270.0)},
    }
    cv2.circle(image, (371, 270), 35, (0, 255, 0), -1)
    cv2.circle(image, (571, 270), 35, (0, 215, 255), -1)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    frame = "data:image/png;base64," + base64.b64encode(encoded.tobytes()).decode("ascii")

    states = daily_foundation.detect_weekly_activity_reward_states(frame, layout)

    assert states[1600]["state"] == "claimed"
    assert states[2000]["state"] == "claimable"
    assert states[2400]["state"] == "unknown"


def test_weekly_activity_layout_follows_visible_ocr_labels_instead_of_fixed_x() -> None:
    tokens = [
        {"text": "1600", "x": 344.0, "y": 348.0, "w": 54.0, "h": 25.0},
        {"text": "2000", "x": 543.0, "y": 348.0, "w": 56.0, "h": 25.0},
        {"text": "2400", "x": 745.0, "y": 348.0, "w": 56.0, "h": 25.0},
        {"text": "600", "x": 452.0, "y": 650.0, "w": 48.0, "h": 25.0},
    ]

    layout = daily_foundation.weekly_activity_reward_layout_from_ocr(
        tokens,
        frame_width=900,
        frame_height=1600,
    )

    assert list(layout) == [1600, 2000, 2400]
    assert layout[1600]["point"] == pytest.approx((371.0, 270.0))
    assert layout[2000]["point"] == pytest.approx((571.0, 270.0))
    assert layout[2400]["point"] == pytest.approx((773.0, 270.0))


def test_weekly_activity_layout_rejects_missing_or_reversed_track_labels() -> None:
    with pytest.raises(RuntimeError, match="未识别到奖励轨道档位标签"):
        daily_foundation.weekly_activity_reward_layout_from_ocr(
            [{"text": "2400", "x": 745.0, "y": 900.0, "w": 56.0, "h": 25.0}],
            frame_width=900,
            frame_height=1600,
        )


def test_weekly_activity_pending_badge_uses_weekly_tab_local_region() -> None:
    assert daily_foundation.weekly_activity_pending_badge_present(
        [{"text": "领", "x": 795.0, "y": 1394.0, "w": 32.0, "h": 34.0}],
        frame_width=900,
        frame_height=1600,
    ) is True
    assert daily_foundation.weekly_activity_pending_badge_present(
        [
            {"text": "领", "x": 690.0, "y": 1394.0, "w": 32.0, "h": 34.0},
            {"text": "周常", "x": 770.0, "y": 1490.0, "w": 70.0, "h": 44.0},
        ],
        frame_width=900,
        frame_height=1600,
    ) is False
    with pytest.raises(RuntimeError, match="顺序异常"):
        daily_foundation.weekly_activity_reward_layout_from_ocr(
            [
                {"text": "2400", "x": 344.0, "y": 348.0, "w": 54.0, "h": 25.0},
                {"text": "2000", "x": 543.0, "y": 348.0, "w": 56.0, "h": 25.0},
            ],
            frame_width=900,
            frame_height=1600,
        )


def test_weekly_activity_saturday_below_threshold_ends_week(monkeypatch) -> None:
    monkeypatch.setattr(behavior_tree_runtime, "_now", lambda: datetime(2026, 7, 25, 0, 5))
    runtime = _Runtime(scenes=[402], ocr_results=[([1800], "1800")])

    result = _run(_Runner().weekly_activity_flow(runtime))

    assert result["result"] == "success"
    assert "next_time" not in result
    assert runtime.next_times == ["2026-07-30 00:00:00"]
    assert "周六最终检查" in result["message"]
    assert ("click", 402, "奖励") not in runtime.actions
    assert runtime.actions[-1] == ("go_scene", 34)
