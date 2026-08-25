from __future__ import annotations

from pathlib import Path
import threading

import pytest

from backend.core.fanxiu.data_annotation.tasks.take_medicine_batch import TakeMedicineBatchTaskMixin


def _done(value=None):
    if False:
        yield None
    return value


def _drain(generator):
    try:
        while True:
            next(generator)
    except StopIteration as exc:
        return exc.value


class _Runtime:
    def __init__(
        self,
        scene: int,
        *,
        landing_after_confirm: int = 595,
        confirmation_text: str = "服用列表 吸收药效时间需",
    ) -> None:
        self.scene = scene
        self.landing_after_confirm = landing_after_confirm
        self.confirmation_text = confirmation_text
        self.clicks: list[tuple[int, str]] = []

    def current_scene(self, _scene_ids, update=False):
        return self.scene, 95.0, "frame"

    def click_shape_center_then_view(self, scene_id, shape, *_targets, **_kwargs):
        self.clicks.append((scene_id, shape))
        self.scene = 593
        return _done(type("View", (), {"id": self.scene})())

    def click_shape_center(self, scene_id, shape):
        self.clicks.append((scene_id, shape))
        if (scene_id, shape) == (593, "一键服用"):
            self.scene = 594
        elif (scene_id, shape) == (594, "确认"):
            self.scene = self.landing_after_confirm

    def wait_view(self, *scene_ids, **_kwargs):
        assert self.scene in scene_ids
        return _done(type("View", (), {"id": self.scene})())

    def wait_click(self, scene_id, shape, **_kwargs):
        self.clicks.append((scene_id, shape))
        if scene_id == 507:
            self.scene = 405
        return _done("clicked")

    def cur_frame(self, *, update=False):
        assert update is True
        return "frame"

    def full_frame_ocr_tokens(self, frame):
        assert frame == "frame"
        return [{"text": self.confirmation_text}]


class _Runner(TakeMedicineBatchTaskMixin):
    def __init__(self, runtime: _Runtime) -> None:
        self.runtime = runtime
        self.next_times = []
        self.logs = []
        self.stopped_checks = 0
        self.raise_stopped = False

    def _fanxiu_runtime(self, *_args, **_kwargs):
        return self.runtime

    def _persist_scheduler_task_next_time(self, task_id, next_time):
        self.next_times.append((task_id, next_time))

    def _log(self, level, message):
        self.logs.append((level, message))

    def _raise_if_stopped(self, _stop_event):
        self.stopped_checks += 1
        if self.raise_stopped:
            raise RuntimeError("stopped")


def _run(runner: _Runner):
    return _drain(runner._execute_take_medicine_batch_task(
        {"asset_tree_path": Path("asset-tree.json")},
        threading.Event(),
        {"__scheduler_task_id": "take-medicine-batch"},
    ))


def test_progress_page_is_zero_click_idempotent() -> None:
    runner = _Runner(_Runtime(595))
    result = _run(runner)
    assert result["result"] == "already_running"
    assert result["confirmation_clicks"] == 0
    assert runner.runtime.clicks == []
    assert runner.next_times == [("take-medicine-batch", None)]


def test_confirmation_is_clicked_exactly_once_after_final_stop_gate() -> None:
    runner = _Runner(_Runtime(405))
    result = _run(runner)
    assert result["result"] == "started"
    assert result["confirmation_clicks"] == 1
    assert runner.stopped_checks == 1
    assert runner.runtime.clicks == [
        (405, "服用丹药"), (593, "一键服用"), (594, "确认"),
    ]
    assert all(shape != "停止服用" for _scene, shape in runner.runtime.clicks)


def test_stop_before_confirmation_blocks_irreversible_action() -> None:
    runner = _Runner(_Runtime(405))
    runner.raise_stopped = True
    with pytest.raises(RuntimeError, match="stopped"):
        _run(runner)
    assert (594, "确认") not in runner.runtime.clicks


def test_confirmation_requires_both_semantic_ocr_anchors() -> None:
    runner = _Runner(_Runtime(405, confirmation_text="VIP经验 使用后增加VIP经验"))
    with pytest.raises(RuntimeError, match="服用列表.*吸收药效时间需"):
        _run(runner)
    assert (594, "确认") not in runner.runtime.clicks


def test_cross_cell_confirmation_is_manual_business_block_not_error() -> None:
    runner = _Runner(_Runtime(594))
    result = _run(runner)
    assert result["result"] == "manual_required"
    assert result["confirmation_clicks"] == 0
    assert runner.runtime.clicks == []
    assert runner.next_times == [("take-medicine-batch", None)]


@pytest.mark.parametrize("landing", [507, 405, 408])
def test_immediate_completion_does_not_retry_confirmation(landing: int) -> None:
    runner = _Runner(_Runtime(405, landing_after_confirm=landing))
    result = _run(runner)
    assert result["result"] == "completed"
    assert runner.runtime.clicks.count((594, "确认")) == 1
    assert all(shape != "停止服用" for _scene, shape in runner.runtime.clicks)


def test_existing_result_popup_is_acknowledged_without_new_confirmation() -> None:
    runner = _Runner(_Runtime(507))
    result = _run(runner)
    assert result["result"] == "completed"
    assert result["confirmation_clicks"] == 0
    assert runner.runtime.clicks == [(507, "确认")]
