from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest

from backend.core.fanxiu.data_annotation.default_jobs import (
    register_fanxiu_data_annotation_default_runtime_jobs,
)
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
)
from backend.core.fanxiu.data_annotation.ocr_values import parse_ocr_values
from backend.core.fanxiu.data_annotation.scheduler_defaults import (
    default_data_annotation_scheduler_tasks,
)
from backend.core.fanxiu.data_annotation.tasks import daily_xuanhuang
from backend.core.fanxiu.data_annotation.tasks.daily_xuanhuang import (
    DailyXuanhuangTaskMixin,
    next_daily_xuanhuang_time,
)


def _run(generator):
    while True:
        try:
            next(generator)
        except StopIteration as stop:
            return stop.value


@dataclass(frozen=True)
class _Match:
    x: float = 200
    y: float = 300
    w: float = 40
    h: float = 20


class _FakeXuanhuangRuntime:
    def __init__(
        self,
        remaining: list[int | None],
        *,
        battle_scenes: list[list[int | None]] | None = None,
        recommend_misses: int = 0,
        entry_results: list[str] | None = None,
        initial_scene: int = 34,
        daily_limit: int = 2,
    ) -> None:
        self.scene = int(initial_scene)
        self.remaining = list(remaining)
        self.counter_index = 0
        self.battle_scenes = battle_scenes or [[420] for _ in remaining]
        self.battle_index = -1
        self.battle_poll_index = 0
        self.recommend_misses = recommend_misses
        self.entry_results = list(entry_results or [])
        self.daily_limit = int(daily_limit)
        self.point_clicks: list[tuple[int, float, float]] = []
        self.shape_clicks: list[tuple[int, str]] = []
        self.goto_calls: list[int] = []
        self.wait_view_calls: list[int] = []
        self.wait_view_timeouts: list[float | None] = []
        self.loaded_ocr_calls: list[dict[str, Any]] = []
        self.next_times: list[str | None] = []
        self.wait_click_then_view_calls: list[dict[str, Any]] = []

    def set_next_time(self, next_time: str | None) -> None:
        self.next_times.append(next_time)

    def goto_view(self, scene: int):
        self.scene = int(scene)
        self.goto_calls.append(self.scene)
        if False:
            yield None
        return self.scene

    def open_daily_entry(self, **kwargs: Any):
        assert kwargs["label"] == "日常_玄荒"
        assert kwargs["title_pattern"] == "玄荒"
        assert kwargs["progress_can_mark_done"] is False
        assert kwargs["zero_progress_can_mark_done"] is False
        if self.entry_results:
            result = self.entry_results.pop(0)
            if result != "open":
                if False:
                    yield None
                return result
        self.scene = 400
        if False:
            yield None
        return "open"

    def wait_view(self, scene: int, *other_scenes: int, **kwargs: Any):
        candidates = (int(scene), *(int(item) for item in other_scenes))
        selected = self.scene if self.scene in candidates else candidates[0]
        self.scene = selected
        self.wait_view_calls.append(selected)
        self.wait_view_timeouts.append(kwargs.get("timeout"))
        if False:
            yield None
        return type("_View", (), {"id": self.scene})()

    def wait_click(self, scene: int, shape: str, **_kwargs: Any):
        self.shape_clicks.append((int(scene), shape))
        if (scene, shape) == (400, "玄荒"):
            self.scene = 417
        elif (scene, shape) == (418, "前往"):
            self.battle_index += 1
            self.battle_poll_index = 0
            self.scene = 419
        elif (scene, shape) == (420, "离开"):
            self.scene = 34
            self.counter_index += 1
        elif (scene, shape) == (85, "离开"):
            self.scene = 34
        elif (scene, shape) == (86, "确认"):
            self.scene = 34
        if False:
            yield None
        return self.scene

    def wait_click_then_view(
        self,
        scene: int,
        shape: str,
        *target_scenes: int,
        **kwargs: Any,
    ):
        self.wait_click_then_view_calls.append(
            {
                "scene": scene,
                "shape": shape,
                "target_scenes": target_scenes,
                **kwargs,
            }
        )
        yield from self.wait_click(scene, shape)
        return type("_View", (), {"id": self.scene})()

    def click_shape_center(self, scene: int, shape: str) -> None:
        self.shape_clicks.append((int(scene), shape))
        if (scene, shape) == (420, "离开"):
            self.scene = 86

    def cur_frame(self, update: bool = False) -> str:
        return f"frame-{self.scene}-{int(update)}"

    def current_scene(
        self,
        scenes: list[int] | None = None,
        *,
        frame_data_url: str | None = None,
        update: bool = False,
    ):
        if scenes == [417, 418]:
            return self.scene, 100.0, frame_data_url or self.cur_frame(update)
        if scenes in ([186, 419, 420], [420]):
            sequence = self.battle_scenes[self.battle_index]
            index = min(self.battle_poll_index, len(sequence) - 1)
            scene = sequence[index]
            self.battle_poll_index += 1
            self.scene = int(scene) if scene is not None else self.scene
            return scene, 100.0, frame_data_url or self.cur_frame(update)
        return self.scene, 100.0, frame_data_url or self.cur_frame(update)

    def find_ocr_text(self, _scene: int, target: str, **_kwargs: Any):
        assert target == "推"
        if self.recommend_misses > 0:
            self.recommend_misses -= 1
            return None
        return _Match()

    def wait_ocr_text(self, scene: int, target: str, **kwargs: Any):
        self.loaded_ocr_calls.append({
            "scene": scene,
            "target": target,
            **kwargs,
        })
        if self.recommend_misses > 0:
            self.recommend_misses -= 1
            if False:
                yield None
            return None
        if False:
            yield None
        return _Match()

    def wait_ocr_any_text(
        self,
        scene: int,
        targets: tuple[str, ...],
        **kwargs: Any,
    ):
        self.loaded_ocr_calls.append({
            "scene": scene,
            "targets": targets,
            **kwargs,
        })
        if self.recommend_misses > 0:
            self.recommend_misses -= 1
            if False:
                yield None
            return None
        if False:
            yield None
        return _Match()

    def click_frame_point(self, scene: int, x: float, y: float) -> None:
        self.point_clicks.append((scene, x, y))
        self.scene = 418

    def ocr_numbers_in_shapes(
        self,
        scene: int,
        shapes: tuple[str, ...],
        **_kwargs: Any,
    ):
        assert (scene, shapes) == (418, ("次数",))
        value = self.remaining[self.counter_index]
        if value is None:
            return [], "未识别"
        return [value, self.daily_limit], f"{value}/{self.daily_limit}"

    def wait_action_settle(self, _seconds: float = 1.0):
        if False:
            yield None


class _FakeXuanhuangRunner(DailyXuanhuangTaskMixin):
    def _daily_xuanhuang_runtime_snapshot(
        self,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        override = (payload or {}).get(
            "__daily_xuanhuang_runtime_snapshot_override"
        )
        if isinstance(override, dict):
            return dict(override)
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "counter_loaded": False,
        }


def test_daily_xuanhuang_zero_numerator_is_idempotent_success(monkeypatch):
    monkeypatch.setattr(
        daily_xuanhuang,
        "_now",
        lambda: datetime(2026, 7, 23, 18, 30),
    )
    runtime = _FakeXuanhuangRuntime([0])

    result = _run(_FakeXuanhuangRunner()._run_daily_xuanhuang_flow(runtime, {}))

    assert result["result"] == "success"
    assert result["rounds_completed"] == 0
    assert "next_time" not in result
    assert runtime.next_times == ["2026-07-24 05:00:00"]
    assert runtime.wait_view_timeouts[0] == 60.0
    assert (418, "前往") not in runtime.shape_clicks
    assert (420, "离开") not in runtime.shape_clicks
    assert runtime.scene == 34


def test_daily_xuanhuang_resumes_at_418_and_does_not_click_when_numerator_is_zero():
    runtime = _FakeXuanhuangRuntime([0], initial_scene=418)

    result = _run(_FakeXuanhuangRunner()._run_daily_xuanhuang_flow(runtime, {}))

    assert result["result"] == "success"
    assert result["rounds_completed"] == 0
    assert runtime.shape_clicks == []
    assert 69 not in runtime.goto_calls
    assert runtime.goto_calls == [34]
    assert runtime.scene == 34


def test_daily_xuanhuang_retries_full_scene_recognition_before_leaving_zero_counter():
    class IntermittentRecognitionRuntime(_FakeXuanhuangRuntime):
        def __init__(self) -> None:
            super().__init__([0], initial_scene=418)
            self.initial_scene_calls: list[list[int] | None] = []

        def current_scene(
            self,
            scenes: list[int] | None = None,
            *,
            frame_data_url: str | None = None,
            update: bool = False,
        ):
            self.initial_scene_calls.append(scenes)
            if len(self.initial_scene_calls) == 1:
                return None, 0.0, frame_data_url or self.cur_frame(update)
            return super().current_scene(
                scenes,
                frame_data_url=frame_data_url,
                update=update,
            )

    runtime = IntermittentRecognitionRuntime()

    result = _run(_FakeXuanhuangRunner()._run_daily_xuanhuang_flow(runtime, {}))

    assert result["result"] == "success"
    assert runtime.initial_scene_calls == [None, None]
    assert runtime.shape_clicks == []
    assert 69 not in runtime.goto_calls
    assert runtime.goto_calls == [34]


def test_daily_xuanhuang_reenters_and_rereads_until_numerator_is_zero():
    runtime = _FakeXuanhuangRuntime(
        [2, 1, 0],
        battle_scenes=[[419, 420], [420]],
    )

    result = _run(
        _FakeXuanhuangRunner()._run_daily_xuanhuang_flow(
            runtime,
            {"battle_timeout_seconds": 0.1, "battle_poll_seconds": 0},
        )
    )

    assert result["rounds_completed"] == 2
    assert runtime.shape_clicks.count((418, "前往")) == 2
    assert all(
        call["target_scenes"] == (186, 419, 420)
        and call["retry_if_source_remains"] is True
        and call["max_clicks"] == 3
        for call in runtime.wait_click_then_view_calls
    )
    assert runtime.shape_clicks.count((420, "离开")) == 2
    assert runtime.wait_view_calls.count(34) == 2
    assert runtime.goto_calls.count(69) == 3
    assert runtime.scene == 34


def test_daily_xuanhuang_completion_depends_on_zero_not_a_fixed_denominator():
    # Current gameplay grants two free reward attempts. A different denominator
    # here protects the abstraction only; the Job never purchases paid attempts
    # to satisfy the outer daily-activity count.
    runtime = _FakeXuanhuangRuntime(
        [3, 2, 1, 0],
        daily_limit=3,
        battle_scenes=[[419, 420], [420], [419, 420]],
    )

    result = _run(
        _FakeXuanhuangRunner()._run_daily_xuanhuang_flow(
            runtime,
            {"battle_timeout_seconds": 0.1, "battle_poll_seconds": 0},
        )
    )

    assert result["result"] == "success"
    assert result["rounds_completed"] == 3
    assert runtime.shape_clicks.count((418, "前往")) == 3
    assert runtime.shape_clicks.count((420, "离开")) == 3
    assert runtime.scene == 34


def test_daily_xuanhuang_recovers_via_395_leave_and_86_confirm():
    class XuanhuangMapRuntime(_FakeXuanhuangRuntime):
        def wait_click(self, scene: int, shape: str, **kwargs: Any):
            result = yield from super().wait_click(
                scene,
                shape,
                **kwargs,
            )
            if (scene, shape) == (420, "离开"):
                self.scene = 395
            return result

    runtime = XuanhuangMapRuntime(
        [1, 0],
        battle_scenes=[[420], [420]],
    )

    result = _run(
        _FakeXuanhuangRunner()._run_daily_xuanhuang_flow(
            runtime,
            {
                "battle_timeout_seconds": 0.1,
                "battle_poll_seconds": 0,
            },
        )
    )

    assert result["result"] == "success"
    assert (420, "离开") in runtime.shape_clicks
    assert (86, "确认") in runtime.shape_clicks
    assert runtime.scene == 34


def test_daily_xuanhuang_recovers_via_395_leave_and_55_world_map():
    class XuanhuangMapRuntime(_FakeXuanhuangRuntime):
        def wait_click(self, scene: int, shape: str, **kwargs: Any):
            result = yield from super().wait_click(
                scene,
                shape,
                **kwargs,
            )
            if (scene, shape) == (420, "离开"):
                self.scene = 395
            return result

        def click_shape_center(self, scene: int, shape: str) -> None:
            self.shape_clicks.append((int(scene), shape))
            if (scene, shape) == (420, "离开"):
                self.scene = 55

    runtime = XuanhuangMapRuntime(
        [1, 0],
        battle_scenes=[[420], [420]],
    )

    result = _run(
        _FakeXuanhuangRunner()._run_daily_xuanhuang_flow(
            runtime,
            {
                "battle_timeout_seconds": 0.1,
                "battle_poll_seconds": 0,
            },
        )
    )

    assert result["result"] == "success"
    assert 55 in runtime.wait_view_calls
    assert runtime.scene == 34


def test_daily_xuanhuang_recovers_from_real_85_landing_via_formal_leave():
    class XuanhuangRegionRuntime(_FakeXuanhuangRuntime):
        def wait_click(self, scene: int, shape: str, **kwargs: Any):
            result = yield from super().wait_click(scene, shape, **kwargs)
            if (scene, shape) == (420, "离开"):
                self.scene = 85
            return result

    runtime = XuanhuangRegionRuntime([1, 0], battle_scenes=[[420], [420]])

    result = _run(
        _FakeXuanhuangRunner()._run_daily_xuanhuang_flow(
            runtime,
            {"battle_timeout_seconds": 0.1, "battle_poll_seconds": 0},
        )
    )

    assert result["result"] == "success"
    assert (420, "离开") in runtime.shape_clicks
    assert (85, "离开") in runtime.shape_clicks
    assert 85 in runtime.wait_view_calls
    assert runtime.scene == 34


def test_daily_xuanhuang_rejects_daily_row_as_completion_evidence():
    runtime = _FakeXuanhuangRuntime(
        [1],
        entry_results=["done"],
    )

    with pytest.raises(RuntimeError, match="#418"):
        _run(_FakeXuanhuangRunner()._run_daily_xuanhuang_flow(runtime, {}))
    assert runtime.shape_clicks == []


def test_daily_xuanhuang_switches_to_level_two_after_level_three_has_no_recommendation():
    runtime = _FakeXuanhuangRuntime([0], recommend_misses=1)

    _run(_FakeXuanhuangRunner()._run_daily_xuanhuang_flow(runtime, {}))

    assert (417, "2级") in runtime.shape_clicks
    assert len(runtime.loaded_ocr_calls) == 2
    assert runtime.point_clicks == [(417, 160.0, 340.0)]


def test_daily_xuanhuang_fails_only_after_both_levels_have_no_recommendation():
    runtime = _FakeXuanhuangRuntime([0], recommend_misses=2)

    with pytest.raises(TimeoutError, match="推.*荐"):
        _run(
            _FakeXuanhuangRunner()._run_daily_xuanhuang_flow(
                runtime,
                {"recommend_timeout_seconds": 0.1},
            )
        )

    assert (417, "2级") in runtime.shape_clicks
    assert runtime.loaded_ocr_calls == [
        {
            "scene": 417,
            "targets": ("推", "荐"),
            "in_shapes": ["窗口"],
            "padding": 0,
            "timeout_seconds": 0.1,
        },
        {
            "scene": 417,
            "targets": ("推", "荐"),
            "in_shapes": ["窗口"],
            "padding": 0,
            "timeout_seconds": 0.30000000000000004,
            "direction_cycles": 3,
            "cycle_pause_seconds": 2,
        },
    ]


def test_daily_xuanhuang_uses_loaded_ocr_result_and_required_offset():
    runtime = _FakeXuanhuangRuntime([0])

    _run(_FakeXuanhuangRunner()._run_daily_xuanhuang_flow(runtime, {}))

    assert runtime.point_clicks == [
        (417, 160.0, 340.0),
    ]


def test_daily_xuanhuang_battle_allows_420_without_ever_observing_419():
    runtime = _FakeXuanhuangRuntime([1], battle_scenes=[[420]])

    saw_419 = _run(
        _FakeXuanhuangRunner()._daily_xuanhuang_wait_battle_done(
            runtime,
            timeout_seconds=300,
            poll_seconds=0,
        )
    )

    assert saw_419 is False


def test_daily_xuanhuang_resumes_existing_generic_battle_without_reclicking_forward():
    runtime = _FakeXuanhuangRuntime(
        [1, 0],
        initial_scene=186,
        battle_scenes=[[186, 420]],
    )

    result = _run(
        _FakeXuanhuangRunner()._run_daily_xuanhuang_flow(
            runtime,
            {"battle_poll_seconds": 0.01},
        )
    )

    assert result["result"] == "success"
    assert result["rounds_completed"] == 1
    assert runtime.shape_clicks.count((418, "前往")) == 0
    assert runtime.shape_clicks.count((420, "离开")) == 1


def test_daily_xuanhuang_waits_out_blank_resume_transition_before_goto():
    runtime = _FakeXuanhuangRuntime([0], initial_scene=34)
    initial_scenes = iter((None, None, None))
    original_current_scene = runtime.current_scene

    def current_scene(*args: Any, **kwargs: Any):
        if not args or args[0] is None:
            try:
                return next(initial_scenes), 0.0, runtime.cur_frame(True)
            except StopIteration:
                pass
        return original_current_scene(*args, **kwargs)

    runtime.current_scene = current_scene  # type: ignore[method-assign]

    result = _run(
        _FakeXuanhuangRunner()._run_daily_xuanhuang_flow(
            runtime,
            {"current_scene_probe_retry_seconds": 0},
        )
    )

    assert result["result"] == "success"
    assert runtime.wait_view_calls[0] == 34
    assert runtime.wait_view_timeouts[0] == 60.0


def test_daily_xuanhuang_final_deadline_probe_still_accepts_420(monkeypatch):
    runtime = _FakeXuanhuangRuntime([1], battle_scenes=[[None, 420]])
    times = iter([100.0, 401.0])
    monkeypatch.setattr(daily_xuanhuang.time, "monotonic", lambda: next(times))

    saw_419 = _run(
        _FakeXuanhuangRunner()._daily_xuanhuang_wait_battle_done(
            runtime,
            timeout_seconds=300,
            poll_seconds=0,
        )
    )

    assert saw_419 is False


def test_daily_xuanhuang_recognized_battle_renews_liveness_deadline(monkeypatch):
    runtime = _FakeXuanhuangRuntime([1], battle_scenes=[[419, 420]])
    times = iter([100.0, 401.0, 401.0, 401.0])
    monkeypatch.setattr(daily_xuanhuang.time, "monotonic", lambda: next(times))

    saw_battle = _run(
        _FakeXuanhuangRunner()._daily_xuanhuang_wait_battle_done(
            runtime,
            timeout_seconds=300,
            poll_seconds=0,
        )
    )

    assert saw_battle is True


def test_daily_xuanhuang_missing_counter_is_never_treated_as_zero():
    runtime = _FakeXuanhuangRuntime([None])

    with pytest.raises(RuntimeError, match="无法从 #418\\[次数\\] 稳定识别分子/分母"):
        _run(
            _FakeXuanhuangRunner()._run_daily_xuanhuang_flow(
                runtime,
                {"counter_attempts": 2, "counter_retry_seconds": 0},
            )
        )
    assert (418, "前往") not in runtime.shape_clicks


def test_daily_xuanhuang_prefers_complete_runtime_counter():
    runtime = _FakeXuanhuangRuntime([None])
    payload = {
        "__daily_xuanhuang_runtime_snapshot_override": {
            "complete": True,
            "counter_loaded": True,
            "remaining": 0,
        }
    }

    result = _run(
        _FakeXuanhuangRunner()._run_daily_xuanhuang_flow(
            runtime,
            payload,
        )
    )

    assert result["result"] == "success"
    assert result["rounds_completed"] == 0
    assert (418, "前往") not in runtime.shape_clicks
    assert runtime.scene == 34


def test_daily_xuanhuang_runtime_zero_at_417_skips_missing_recommendation():
    runtime = _FakeXuanhuangRuntime([None], recommend_misses=1)
    payload = {
        "__daily_xuanhuang_runtime_snapshot_override": {
            "complete": True,
            "counter_loaded": True,
            "remaining": 0,
        }
    }

    result = _run(
        _FakeXuanhuangRunner()._run_daily_xuanhuang_flow(
            runtime,
            payload,
        )
    )

    assert result["result"] == "success"
    assert result["rounds_completed"] == 0
    assert runtime.loaded_ocr_calls == []
    assert runtime.point_clicks == []
    assert runtime.scene == 34


def test_daily_xuanhuang_runtime_zero_is_checked_after_opening_daily_entry():
    runtime = _FakeXuanhuangRuntime([None], recommend_misses=1)
    payload = {
        "__daily_xuanhuang_runtime_snapshot_override": {
            "complete": True,
            "counter_loaded": True,
            "remaining": 0,
        }
    }

    result = _run(
        _FakeXuanhuangRunner()._run_daily_xuanhuang_flow(
            runtime,
            payload,
        )
    )

    assert result["result"] == "success"
    assert result["rounds_completed"] == 0
    assert runtime.goto_calls == [34, 69, 34]
    assert 417 in runtime.wait_view_calls
    assert runtime.loaded_ocr_calls == []


def test_daily_xuanhuang_reads_no_counter_before_opening_and_consumes_new_attempt():
    runtime = _FakeXuanhuangRuntime(
        [None, None],
        battle_scenes=[[419, 420]],
    )

    class RefreshingRunner(_FakeXuanhuangRunner):
        def __init__(self) -> None:
            self.remaining_snapshots = iter((1, 1, 0))
            self.snapshot_scenes: list[int] = []

        def _daily_xuanhuang_runtime_snapshot(
            self,
            payload: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            self.snapshot_scenes.append(runtime.scene)
            return {
                "ok": True,
                "available": True,
                "complete": True,
                "counter_loaded": True,
                "remaining": next(self.remaining_snapshots),
            }

    runner = RefreshingRunner()

    result = _run(
        runner._run_daily_xuanhuang_flow(
            runtime,
            {"battle_timeout_seconds": 0.1, "battle_poll_seconds": 0},
        )
    )

    assert result["result"] == "success"
    assert result["rounds_completed"] == 1
    assert runtime.shape_clicks.count((418, "前往")) == 1
    assert runtime.shape_clicks.count((420, "离开")) == 1
    assert runner.snapshot_scenes == [417, 418, 417]
    assert runtime.scene == 34


def test_daily_xuanhuang_falls_back_to_ocr_for_incomplete_runtime():
    runtime = _FakeXuanhuangRuntime([1])
    runner = _FakeXuanhuangRunner()

    remaining = _run(
        runner._daily_xuanhuang_read_remaining(
            runtime,
            payload={
                "__daily_xuanhuang_runtime_snapshot_override": {
                    "complete": False,
                    "counter_loaded": False,
                }
            },
            attempts=1,
            retry_seconds=0,
        )
    )

    assert remaining == 1


def test_ocr_fraction_parser_does_not_require_a_slash():
    assert parse_ocr_values("0/1", expected_count=2) == (0, 1)
    assert parse_ocr_values("0 1", expected_count=2) == (0, 1)
    assert parse_ocr_values("0丨1", expected_count=2) == (0, 1)
    assert parse_ocr_values("０｜１", expected_count=2) == (0, 1)
    assert parse_ocr_values("0\n1", expected_count=2) == (0, 1)


def test_ocr_fraction_parser_rejects_an_unpaired_zero():
    assert parse_ocr_values("0", expected_count=2) is None
    assert parse_ocr_values("次数 0", expected_count=2) is None


def test_ocr_fraction_parser_can_use_last_pair_for_broad_row_ocr():
    assert parse_ocr_values(
        "奖励10次 当前0丨1",
        expected_count=2,
        allow_extra_numbers=True,
    ) == (0, 1)


def test_daily_xuanhuang_next_time_is_always_next_calendar_day_at_five():
    assert next_daily_xuanhuang_time(datetime(2026, 7, 23, 4, 30)) == "2026-07-24 05:00:00"
    assert next_daily_xuanhuang_time(datetime(2026, 7, 23, 23, 59)) == "2026-07-24 05:00:00"


def test_daily_xuanhuang_catalog_is_manual_five_am_task_without_scene_policy():
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("daily_xuanhuang")
    assert definition is not None
    assert definition.label == "日常_玄荒"
    assert definition.scheduler_supported is True
    assert not hasattr(definition, "lifecycle")

    task = next(
        item
        for item in default_data_annotation_scheduler_tasks()
        if item["task_type"] == "daily_xuanhuang"
    )
    assert task["trigger_description"] == "每日"
    assert task["next_time"]
    assert task["payload"]["recommend_timeout_seconds"] == 60
    assert task["payload"]["battle_timeout_seconds"] == 120
    assert task["payload"]["max_runtime_seconds"] == 10800


def test_daily_xuanhuang_cell_does_not_preempt_task_owned_resume_with_generic_goto():
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("daily_xuanhuang")
    assert definition is not None

    class Runner:
        def _fanxiu_runtime(self, *_args: Any, **_kwargs: Any):
            raise AssertionError("玄荒 Cell 外层不得先创建 Runtime 并 goto 世界")

        def _execute_daily_xuanhuang_task(
            self,
            ctx: dict[str, Any],
            stop_event: Any,
            payload: dict[str, Any],
        ):
            assert ctx == {"asset": "tree"}
            assert stop_event == "stop"
            assert payload == {"resume": True}
            if False:
                yield None
            return {"result": "success"}

    result = _run(
        definition.handler(
            Runner(),
            {"asset": "tree"},
            {"resume": True},
            "stop",
        )
    )

    assert result == {"result": "success"}
