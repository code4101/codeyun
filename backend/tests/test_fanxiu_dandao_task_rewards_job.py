from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from backend.core.fanxiu.data_annotation.tasks import dandao_task_rewards as job
from backend.core.fanxiu.data_annotation.tasks.resource_rank_daily_gift import (
    RESOURCE_RANK_GIFT_ADAPTERS,
)
from backend.core.fanxiu.instrumentation import dandao_task_rewards as reader


def _finish(generator):
    try:
        while True:
            next(generator)
    except StopIteration as exc:
        return exc.value


class _Runtime:
    def __init__(self) -> None:
        self.next_time = None

    def set_next_time(self, value: str) -> None:
        self.next_time = value


class _ClaimRuntime(_Runtime):
    def __init__(self) -> None:
        super().__init__()
        self.clicked = []

    def current_scene(self, _scene_ids, *, update=False):
        return 598, 100.0, "frame"

    def click_shape(self, scene_id, shape_title, *, frame_data_url):
        self.clicked.append((scene_id, shape_title, frame_data_url))

    def wait_action_settle(self, _seconds):
        if False:
            yield None

    def go_scene(self, scene_id):
        assert scene_id == 34
        return None


def test_reader_projects_runtime_selected_ladder(monkeypatch) -> None:
    monkeypatch.setattr(
        reader,
        "read_activity_task_reward_snapshots",
        lambda *_args, **_kwargs: {
            "ok": True,
            "available": True,
            "protocol": "QuestMgr",
            "captured_at": "2026-08-21T18:00:00+08:00",
            "task_entries": [
                {"taskId": 165, "status": 5, "turn": 1, "rewardTime": 1},
                {"taskId": 166, "status": 4, "turn": 1, "rewardTime": 0},
            ],
            "finished_task_ids": [165],
            "evidence": {},
        },
    )
    monkeypatch.setattr(
        reader,
        "resolve_dandao_live_task_ids",
        lambda *_args, **_kwargs: (165, 166),
    )

    snapshot = reader.read_dandao_task_reward_snapshot(4043101)

    assert snapshot["complete"] is True
    assert snapshot["claimed_task_ids"] == [165]
    assert snapshot["authorized_claim_task_ids"] == [166]


def test_reader_rebinds_process_once_after_stale_mapping(monkeypatch) -> None:
    calls = []

    def _read(*_args, **kwargs):
        calls.append(bool(kwargs.get("force_process_refresh")))
        if len(calls) == 1:
            return {
                "ok": False,
                "available": False,
                "failed_stage": "activity_task_decode",
                "reason": "Runtime 内存地址越界",
            }
        return {
            "ok": True,
            "available": True,
            "protocol": "QuestMgr",
            "task_entries": [
                {"taskId": 165, "status": 5, "turn": 1, "rewardTime": 1},
            ],
            "finished_task_ids": [165],
            "evidence": {"mapping": "fresh"},
        }

    monkeypatch.setattr(reader, "read_activity_task_reward_snapshots", _read)
    monkeypatch.setattr(
        reader,
        "resolve_dandao_live_task_ids",
        lambda *_args, **_kwargs: (165,),
    )

    snapshot = reader.read_dandao_task_reward_snapshot(4043101)

    assert calls == [False, True]
    assert snapshot["state"] == "already_claimed"
    assert snapshot["evidence"]["process_refresh_retry"] is True
    assert snapshot["evidence"]["mapping"] == "fresh"


def test_already_claimed_retry_is_zero_click_and_schedules_next_day(monkeypatch) -> None:
    current = datetime(2026, 8, 21, 18, 11, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(
        job,
        "_active_dandao_adapter",
        lambda _now: (RESOURCE_RANK_GIFT_ADAPTERS[0], 4043101),
    )
    monkeypatch.setattr(
        job,
        "read_dandao_task_reward_snapshot",
        lambda _activity_id: {
            "ok": True,
            "complete": True,
            "state": "already_claimed",
            "authorized_claim_task_ids": [],
        },
    )
    runtime = _Runtime()

    result = _finish(job.run_dandao_task_rewards_flow(runtime, now=current))

    assert result["claimed_count"] == 0
    assert result["boundary"] == "already_claimed"
    assert runtime.next_time == "2026-08-22 18:10:00"


def test_pending_retry_schedules_bounded_recheck(monkeypatch) -> None:
    current = datetime(2026, 8, 21, 18, 11, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(
        job,
        "_active_dandao_adapter",
        lambda _now: (RESOURCE_RANK_GIFT_ADAPTERS[0], 4043101),
    )
    monkeypatch.setattr(
        job,
        "read_dandao_task_reward_snapshot",
        lambda _activity_id: {
            "ok": True,
            "complete": True,
            "state": "none",
            "authorized_claim_task_ids": [],
        },
    )
    runtime = _Runtime()

    result = _finish(job.run_dandao_task_rewards_flow(runtime, now=current))

    assert result["boundary"] == "no_claimable_progress"
    assert runtime.next_time == "2026-08-21 18:41:00"


def test_claim_flow_clicks_only_itemclick_shape_and_requires_exact_readback(monkeypatch) -> None:
    current = datetime(2026, 8, 21, 18, 11, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(
        job,
        "_active_dandao_adapter",
        lambda _now: (RESOURCE_RANK_GIFT_ADAPTERS[0], 4043101),
    )

    def _open(*_args, **_kwargs):
        if False:
            yield None
        return 598

    monkeypatch.setattr(job, "open_resource_rank_activity_page", _open)
    snapshots = iter(
        [
            {
                "ok": True,
                "complete": True,
                "state": "claimable",
                "authorized_claim_task_ids": [159],
                "claimed_task_ids": [165],
                "pending_task_ids": [],
            },
            {
                "ok": True,
                "complete": True,
                "state": "already_claimed",
                "authorized_claim_task_ids": [],
                "claimed_task_ids": [165, 159],
                "pending_task_ids": [],
            },
        ]
    )
    monkeypatch.setattr(job, "read_dandao_task_reward_snapshot", lambda _activity_id: next(snapshots))
    runtime = _ClaimRuntime()

    result = _finish(job.run_dandao_task_rewards_flow(runtime, now=current))

    assert runtime.clicked == [(598, "首条任务领取区", "frame")]
    assert result["claimed_ids"] == [159]
    assert runtime.next_time == "2026-08-22 18:10:00"
