from __future__ import annotations

import threading
from pathlib import Path

import pytest

from backend.core.fanxiu.data_annotation.default_jobs import (
    register_fanxiu_data_annotation_default_runtime_jobs,
)
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
)
from backend.core.fanxiu.data_annotation.scheduler_defaults import (
    default_data_annotation_scheduler_tasks,
)
from backend.core.fanxiu.data_annotation.tasks import wanbao_zhenbao_job as job
from backend.core.fanxiu.data_annotation.tasks.activity_store import (
    ActivityStoreNumericTarget,
    ActivityStoreOperationResult,
    ActivityStoreRegionScan,
)


def _drain(generator):
    while True:
        try:
            next(generator)
        except StopIteration as stop:
            return stop.value


def _snapshot(*, task_claimable: int = 0) -> dict:
    return {
        "complete": True,
        "activity_id": 712,
        "draw": {
            "strategy": "first_hit",
            "enabled": True,
            "available_draws": 138,
            "progress": 10,
            "y": 1,
        },
        "tasks": {"activity_id": 712, "claimable_count": task_claimable},
        "xiangzhen": {"claimable_box_count": 0},
        "cumulative_rewards": {
            "claimable_reward_ids": [],
            "milestones": [
                {
                    "id": 71200001,
                    "target": 10,
                    "reward": "Item|9070095_1",
                    "claimed": True,
                    "claimable": False,
                }
            ],
        },
    }


def test_wanbao_is_exactly_one_manual_standard_job() -> None:
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("wanbao_zhenbao")
    assert definition is not None
    assert definition.scheduler_supported is True
    assert definition.standard_job is True
    assert definition.standard_job_id == "wanbao-zhenbao"
    assert definition.standard_job_description == "手动"

    matches = [
        item
        for item in default_data_annotation_scheduler_tasks()
        if item["task_type"] == "wanbao_zhenbao"
    ]
    assert len(matches) == 1
    assert matches[0]["id"] == "wanbao-zhenbao"
    assert matches[0]["trigger_description"] == "手动"
    assert matches[0]["next_time"] is None


def test_store_selector_clicks_only_approved_non_cash_prices(monkeypatch) -> None:
    observed = {}
    targets = (
        ActivityStoreNumericTarget(248, "248", False, 1, 1, 1, 1),
        ActivityStoreNumericTarget(988, "988", False, 2, 1, 1, 1),
        ActivityStoreNumericTarget(6, "6", True, 3, 1, 1, 1),
        ActivityStoreNumericTarget(128, "128", False, 4, 1, 1, 1),
    )

    def operate(_runtime, **kwargs):
        observed.update(kwargs)
        selected = kwargs["select_targets"](ActivityStoreRegionScan(targets))
        observed["selected"] = selected
        return ActivityStoreOperationResult((248, 988), ())

    monkeypatch.setattr(job, "operate_activity_store_region", operate)
    result = job.complete_wanbao_store(object())

    assert [target.value for target in observed["selected"]] == [248, 988]
    assert observed["scene_id"] == 602
    assert observed["region_title"] == "购买区"
    assert observed["purchase_timeout_seconds"] == 20.0
    assert observed["stability_timeout_seconds"] == 20.0
    assert result.clicked_values == (248, 988)


def test_job_does_not_draw_again_after_first_hit(monkeypatch) -> None:
    clicks = []

    class Runtime:
        @staticmethod
        def current_scene(_scene_ids, *, update):
            assert update is True
            return 34, 1.0, "frame"

        @staticmethod
        def goto_view(scene_id):
            if False:
                yield None
            return scene_id

        @staticmethod
        def wait_click(scene_id, shape, **_kwargs):
            clicks.append((scene_id, shape))
            if False:
                yield None
            return scene_id

        @staticmethod
        def wait_action_settle(seconds):
            assert seconds == 5.0
            if False:
                yield None

        @staticmethod
        def wait_view(scene_id, *_others, **_kwargs):
            if False:
                yield None
            return scene_id

    class Runner:
        def _fanxiu_runtime(self, _ctx, _asset_tree_path, *, stop_event):
            assert stop_event is not None
            return Runtime()

        @staticmethod
        def _log(_kind, _message):
            pass

    monkeypatch.setattr(job, "read_wanbao_zhenbao_runtime", lambda: _snapshot())
    monkeypatch.setattr(
        job,
        "complete_wanbao_store",
        lambda _runtime: ActivityStoreOperationResult((248,), ()),
    )
    result = _drain(
        job.execute_wanbao_zhenbao_job(
            Runner(),
            {"asset_tree_path": Path("asset-tree.json")},
            {},
            threading.Event(),
        )
    )

    assert result["result"] == "success"
    assert result["draw"] == {
        "complete": True,
        "outcome": "target_complete",
        "clicked": False,
        "expected_draws": 0,
        "reason": "抽奖目标已达成：1/1",
    }
    assert all("启宝" not in shape and "抽奖" not in shape for _scene, shape in clicks)
    assert clicks == [
        (34, "万宝臻宝"),
        (600, "商店"),
        (602, "万宝臻宝"),
        (600, "返回"),
    ]


def test_reward_page_resume_clicks_continue_before_accounting() -> None:
    clicks = []
    goto_calls = []

    class Runtime:
        @staticmethod
        def current_scene(scene_ids, *, update):
            assert scene_ids in ([604, 600, 601, 602, 34], [604, 600, 34])
            assert update is True
            return 604, 1.0, "frame"

        @staticmethod
        def wait_click(scene_id, shape, **_kwargs):
            clicks.append((scene_id, shape))
            if False:
                yield None
            return scene_id

        @staticmethod
        def wait_action_settle(seconds):
            assert seconds == 5.0
            if False:
                yield None

        @staticmethod
        def wait_view(scene_id, *_others, **_kwargs):
            if False:
                yield None
            return scene_id

        @staticmethod
        def goto_view(scene_id):
            goto_calls.append(scene_id)
            if False:
                yield None
            return scene_id

    assert _drain(job._open_wanbao_main(Runtime())) == 600
    assert clicks == [(604, "点击屏幕继续")]
    assert goto_calls == []


def test_auto_closing_reward_page_never_clicks_through() -> None:
    clicks = []

    class Runtime:
        @staticmethod
        def current_scene(_scene_ids, *, update):
            assert update is True
            return 604, 1.0, "frame"

        @staticmethod
        def ocr_text(*, frame_data_url):
            assert frame_data_url == "frame"
            return "3秒后自动关闭"

        @staticmethod
        def wait_view(scene_id, *_others, **_kwargs):
            if False:
                yield None
            return scene_id

        @staticmethod
        def wait_click(*args, **kwargs):
            clicks.append((args, kwargs))
            if False:
                yield None

    landing = _drain(job._settle_wanbao_reward_page(Runtime(), label="测试"))

    assert landing == 600
    assert clicks == []


def test_job_reconciles_a_fresh_snapshot_before_each_phase(monkeypatch) -> None:
    reads = []
    observed = []

    def snapshot_reader():
        snapshot = _snapshot()
        snapshot["reconciliation_id"] = len(reads) + 1
        reads.append(snapshot["reconciliation_id"])
        return snapshot

    class Runtime:
        @staticmethod
        def current_scene(_scene_ids, *, update):
            assert update is True
            return 600, 1.0, "frame"

        @staticmethod
        def wait_click(scene_id, shape, **_kwargs):
            if False:
                yield None
            return (scene_id, shape)

        @staticmethod
        def wait_view(scene_id, *_others, **_kwargs):
            if False:
                yield None
            return scene_id

    class Runner:
        def _fanxiu_runtime(self, _ctx, _asset_tree_path, *, stop_event):
            return Runtime()

        @staticmethod
        def _log(_kind, _message):
            pass

    def complete(label):
        def operation(_runtime, snapshot):
            observed.append((label, snapshot["reconciliation_id"]))
            return {"complete": True, "outcome": "nothing_claimable", "final_scene": 600}

        return operation

    def draw_policy(_runtime, snapshot):
        observed.append(("draw", snapshot["reconciliation_id"]))
        return {
            "complete": True,
            "outcome": "deferred_by_user",
            "clicked": False,
            "expected_draws": 0,
            "reason": "deferred",
        }

    monkeypatch.setattr(job, "read_wanbao_zhenbao_runtime", snapshot_reader)
    monkeypatch.setattr(job, "complete_wanbao_tasks", complete("tasks"))
    monkeypatch.setattr(job, "complete_wanbao_cumulative_rewards", complete("cumulative"))
    monkeypatch.setattr(job, "apply_wanbao_draw_policy", draw_policy)
    monkeypatch.setattr(job, "complete_wanbao_xiangzhen", complete("xiangzhen"))
    monkeypatch.setattr(
        job,
        "complete_wanbao_store",
        lambda _runtime: ActivityStoreOperationResult((), ()),
    )

    result = _drain(
        job.execute_wanbao_zhenbao_job(
            Runner(),
            {"asset_tree_path": Path("asset-tree.json")},
            {},
            threading.Event(),
        )
    )
    assert result["result"] == "success"
    assert reads == [1, 2, 3, 4]
    assert observed == [
        ("tasks", 1),
        ("cumulative", 2),
        ("draw", 3),
        ("xiangzhen", 4),
    ]


def test_unproven_executable_draw_policy_fails_closed() -> None:
    clicks = []

    class Runtime:
        def click_shape(self, *_args, **_kwargs):
            clicks.append((_args, _kwargs))

    def executable_policy(_snapshot):
        return job.WanbaoDrawDecision("draw", "consume", expected_draws=1)

    with pytest.raises(RuntimeError, match="抽奖执行器尚未通过"):
        job.apply_wanbao_draw_policy(
            Runtime(),
            _snapshot(),
            policy=executable_policy,
        )
    assert clicks == []


def test_claimable_task_requires_exact_post_click_transition(monkeypatch) -> None:
    class Runtime:
        @staticmethod
        def current_scene(_scene_ids, *, update):
            assert update is True
            return 34, 1.0, "frame"

        @staticmethod
        def goto_view(scene_id):
            if False:
                yield None
            return scene_id

        @staticmethod
        def wait_click(scene_id, _shape, **_kwargs):
            if False:
                yield None
            return scene_id

        @staticmethod
        def wait_view(scene_id, *_others, **_kwargs):
            if False:
                yield None
            return scene_id

        @staticmethod
        def wait_action_settle(_seconds):
            if False:
                yield None

        @staticmethod
        def click_frame_point(_scene_id, _x, _y):
            return None

    class Runner:
        def _fanxiu_runtime(self, _ctx, _asset_tree_path, *, stop_event):
            return Runtime()

        @staticmethod
        def _log(_kind, _message):
            pass

    monkeypatch.setattr(
        job,
        "read_wanbao_zhenbao_runtime",
        lambda: _snapshot(task_claimable=2),
    )
    monkeypatch.setattr(
        job,
        "read_wanbao_task_runtime",
        lambda **_kwargs: {
            "complete": True,
            "tasks": [
                {"task_id": 71200005, "state": "claimable"},
                {"task_id": 71200009, "state": "claimable"},
            ],
        },
    )

    with pytest.raises(RuntimeError, match="71200005 未形成精确单步迁移"):
        _drain(
            job.execute_wanbao_zhenbao_job(
                Runner(),
                {"asset_tree_path": Path("asset-tree.json")},
                {},
                threading.Event(),
            )
        )


def test_tasks_are_idempotent_after_verified_claim(monkeypatch) -> None:
    point_clicks = []
    reads = iter(
        [
            {
                "complete": True,
                "tasks": [{"task_id": 71200005, "state": "claimable"}],
            },
            {
                "complete": True,
                "tasks": [{"task_id": 71200005, "state": "claimed"}],
            },
        ]
    )

    class Runtime:
        @staticmethod
        def wait_click(scene_id, shape, **_kwargs):
            if False:
                yield None
            return (scene_id, shape)

        @staticmethod
        def wait_view(scene_id, *_others, **_kwargs):
            if False:
                yield None
            return scene_id

        @staticmethod
        def wait_action_settle(_seconds):
            if False:
                yield None

        @staticmethod
        def click_frame_point(scene_id, x, y):
            point_clicks.append((scene_id, x, y))

    monkeypatch.setattr(job, "read_wanbao_task_runtime", lambda **_kwargs: next(reads))
    first = _drain(job.complete_wanbao_tasks(Runtime(), _snapshot(task_claimable=1)))
    second = job.complete_wanbao_tasks(Runtime(), _snapshot(task_claimable=0))

    assert first["claimed_task_ids"] == [71200005]
    assert second["outcome"] == "nothing_claimable"
    assert point_clicks == [(601, 470.0, 245.0)]


def test_store_rerun_delegates_to_empty_fixed_point(monkeypatch) -> None:
    results = iter(
        [
            ActivityStoreOperationResult((248, 988), ()),
            ActivityStoreOperationResult((), ()),
        ]
    )
    calls = []

    def operate(_runtime, **kwargs):
        calls.append(kwargs)
        return next(results)

    monkeypatch.setattr(job, "operate_activity_store_region", operate)
    first = job.complete_wanbao_store(object())
    second = job.complete_wanbao_store(object())

    assert first.clicked_values == (248, 988)
    assert second.clicked_values == ()
    assert len(calls) == 2
    assert all(call["scene_id"] == 602 for call in calls)


def test_xiangzhen_rerun_is_noop_after_runtime_reconciliation(monkeypatch) -> None:
    clicks = []

    class Runtime:
        @staticmethod
        def current_scene(_scene_ids, *, update):
            assert update is True
            return 604, 1.0, "frame"

        @staticmethod
        def wait_click(scene_id, shape, **_kwargs):
            clicks.append((scene_id, shape))
            if False:
                yield None
            return scene_id

        @staticmethod
        def wait_view(scene_id, *_others, **_kwargs):
            if False:
                yield None
            return scene_id

        @staticmethod
        def wait_action_settle(_seconds):
            if False:
                yield None

    before = _snapshot()
    before["xiangzhen"] = {
        "claimable_box_count": 2,
        "claim_action_ready": True,
        "open_box_record_count": 3,
    }
    after = _snapshot()
    after["xiangzhen"] = {
        "claimable_box_count": 0,
        "claim_action_ready": False,
        "open_box_record_count": 5,
    }
    monkeypatch.setattr(job, "read_wanbao_zhenbao_runtime", lambda: after)

    first = _drain(job.complete_wanbao_xiangzhen(Runtime(), before))
    second = job.complete_wanbao_xiangzhen(Runtime(), after)

    assert first["outcome"] == "opened_all"
    assert first["opened_count"] == 2
    assert second["outcome"] == "nothing_claimable"
    assert clicks == [
        (600, "飨珍"),
        (603, "开启全部"),
        (604, "点击屏幕继续"),
    ]


def test_single_xiangzhen_uses_one_grid_and_closes_after_runtime_zero(
    monkeypatch,
) -> None:
    clicks = []

    class Runtime:
        @staticmethod
        def wait_click(scene_id, shape, **_kwargs):
            clicks.append((scene_id, shape))
            if False:
                yield None
            return scene_id

        @staticmethod
        def wait_view(scene_id, *_others, **_kwargs):
            if False:
                yield None
            return scene_id

        @staticmethod
        def wait_action_settle(seconds):
            assert seconds == 2.0
            if False:
                yield None

    before = _snapshot()
    before["xiangzhen"] = {
        "claimable_box_count": 1,
        "claim_action_ready": True,
        "open_box_record_count": 5,
    }
    after = _snapshot()
    after["xiangzhen"] = {
        "claimable_box_count": 0,
        "claim_action_ready": False,
        "open_box_record_count": 6,
    }
    monkeypatch.setattr(job, "read_wanbao_zhenbao_runtime", lambda: after)

    result = _drain(job.complete_wanbao_xiangzhen(Runtime(), before))

    assert result["opened_count"] == 1
    assert result["final_scene"] == 600
    assert clicks == [(600, "飨珍"), (603, "点击开启"), (603, "关闭")]
