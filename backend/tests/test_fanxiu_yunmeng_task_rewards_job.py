from __future__ import annotations

from backend.core.fanxiu.data_annotation.tasks.yunmeng_task_rewards import (
    YunmengTaskRewardGuiAssets,
    claim_yunmeng_task_rewards_if_available,
    claim_yunmeng_task_rewards_with_runtime,
)
from backend.core.fanxiu.instrumentation.daily_task_rewards import (
    YUNMENG_TASK_REWARD_SPECS,
    build_activity_task_reward_snapshot,
)


def _snapshot(spec, *, claimable=(), claimed=()):
    claimable = set(claimable)
    claimed = set(claimed)
    entries = [
        {
            "taskId": task_id,
            "status": 3 if task_id in claimed else (4 if task_id in claimable else 3),
            "turn": 1 if task_id in claimable else 0,
            "rewardTime": 0,
            "progressList": [{"finish": task_id in claimable}],
        }
        for task_id in spec.task_ids
    ]
    snapshot = {
        "ok": True,
        "available": True,
        **build_activity_task_reward_snapshot(
            spec=spec,
            task_entries=entries,
            finished_task_ids=list(claimed),
        ),
        "selected_domain": spec.key,
    }
    return snapshot


def _result(generator):
    while True:
        try:
            next(generator)
        except StopIteration as stop:
            return stop.value


def test_already_claimed_path_never_calls_gui():
    spec = YUNMENG_TASK_REWARD_SPECS[-1]
    snapshot = _snapshot(spec, claimed=spec.task_ids)
    calls = []

    def adapter(_snapshot, _plan):
        calls.append("gui")
        if False:
            yield None
        return {"ok": True}

    result = _result(
        claim_yunmeng_task_rewards_if_available(
            reader=lambda: snapshot,
            adapter=adapter,
        )
    )

    assert result["status"] == "already_claimed"
    assert calls == []


def test_claimable_path_without_verified_adapter_fails_closed():
    spec = YUNMENG_TASK_REWARD_SPECS[-1]
    snapshot = _snapshot(spec, claimable=(spec.task_ids[0],))

    result = _result(claim_yunmeng_task_rewards_if_available(reader=lambda: snapshot))

    assert result["status"] == "pending_research"
    assert result["authorized_task_ids"] == [spec.task_ids[0]]


def test_adapter_then_exact_quest_transition_completes_transaction():
    spec = YUNMENG_TASK_REWARD_SPECS[-1]
    intended = (spec.task_ids[0], spec.task_ids[8], spec.task_ids[16])
    before = _snapshot(spec, claimable=intended)
    after = _snapshot(spec, claimed=intended)
    snapshots = iter((before, after))
    observed_plan = {}

    def adapter(_snapshot, plan):
        observed_plan.update(plan)
        yield "clicked"
        return {"ok": True}

    generator = claim_yunmeng_task_rewards_if_available(
        reader=lambda: next(snapshots),
        adapter=adapter,
    )
    assert next(generator) == "clicked"
    result = _result(generator)

    assert result["status"] == "claimed"
    assert result["claimed_task_ids"] == sorted(intended)
    assert observed_plan["tabs"] == {
        "cultivation": [spec.task_ids[0]],
        "score": [spec.task_ids[8]],
        "ranking": [spec.task_ids[16]],
    }


def test_adapter_success_without_exact_quest_transition_is_unverified():
    spec = YUNMENG_TASK_REWARD_SPECS[-1]
    intended = (spec.task_ids[0], spec.task_ids[8])
    before = _snapshot(spec, claimable=intended)
    after = _snapshot(spec, claimable=(spec.task_ids[8],), claimed=(spec.task_ids[0],))
    snapshots = iter((before, after))

    def adapter(_snapshot, _plan):
        if False:
            yield None
        return {"ok": True}

    result = _result(
        claim_yunmeng_task_rewards_if_available(
            reader=lambda: next(snapshots),
            adapter=adapter,
        )
    )

    assert result["status"] == "unverified"


def test_adapter_final_snapshot_avoids_second_full_reader_pass():
    spec = YUNMENG_TASK_REWARD_SPECS[-1]
    intended = (spec.task_ids[0],)
    before = _snapshot(spec, claimable=intended)
    after = _snapshot(spec, claimed=intended)
    reader_calls = []

    def reader():
        reader_calls.append("read")
        return before

    def adapter(_snapshot, _plan):
        if False:
            yield None
        return {"ok": True, "after_snapshot": after}

    result = _result(
        claim_yunmeng_task_rewards_if_available(reader=reader, adapter=adapter)
    )

    assert result["status"] == "claimed"
    assert result["claimed_task_ids"] == list(intended)
    assert reader_calls == ["read"]


class _View:
    def __init__(self, scene_id):
        self.id = scene_id


class _Runtime:
    def __init__(self, landings):
        self.landings = iter(landings)
        self.wait_clicks = []
        self.clicks = []

    def wait_click_then_view(self, source, shape, targets, **options):
        self.wait_clicks.append((source, shape, tuple(targets), options.get("label")))
        yield f"wait:{shape}"
        return _View(next(self.landings))

    def click_shape_center(self, scene_id, shape):
        self.clicks.append((scene_id, shape))

    def wait_action_settle(self, seconds):
        yield f"settle:{seconds}"


def test_runtime_adapter_claims_each_tab_once_per_exact_transition_and_returns_home():
    spec = YUNMENG_TASK_REWARD_SPECS[-1]
    intended = (spec.task_ids[0], spec.task_ids[8], spec.task_ids[16])
    before = _snapshot(spec, claimable=intended)
    plan = {
        "authorized_task_ids": list(intended),
        "tabs": {
            "cultivation": [intended[0]],
            "score": [intended[1]],
            "ranking": [intended[2]],
        },
    }
    assets = YunmengTaskRewardGuiAssets(
        home_scene_id=558,
        task_scene_ids={"cultivation": 565, "score": 566, "ranking": 567},
        tab_shape_names={"cultivation": "修炼", "score": "夺分", "ranking": "榜单"},
    )
    runtime = _Runtime((565, 566, 567, 558))
    states = [
        _snapshot(spec, claimable=intended[1:], claimed=intended[:1]),
        _snapshot(spec, claimable=intended[2:], claimed=intended[:2]),
        _snapshot(spec, claimed=intended),
    ]
    for state, expected in zip(states, intended, strict=True):
        state["selected_domain"] = spec.key
        state["expected_task_claimed"] = True
        state["expected_claimed_task_id"] = expected
    fast_calls = []

    def fast_reader(domain, *, expected_claimed_task_id=None):
        fast_calls.append((domain, expected_claimed_task_id))
        return states[len(fast_calls) - 1]

    result = _result(
        claim_yunmeng_task_rewards_with_runtime(
            runtime,
            before,
            plan,
            assets=assets,
            fast_reader=fast_reader,
        )
    )

    assert result["ok"] is True
    assert result["claimed_task_ids"] == list(intended)
    assert runtime.clicks == [
        (565, "首条任务领取区"),
        (566, "首条任务领取区"),
        (567, "首条任务领取区"),
    ]
    assert fast_calls == [(spec.key, task_id) for task_id in intended]
    assert [item[:3] for item in runtime.wait_clicks] == [
        (558, "任务", (565, 566, 567)),
        (565, "夺分", (566,)),
        (566, "榜单", (567,)),
        (567, "云梦试剑", (558,)),
    ]


def test_runtime_adapter_serializes_multiple_rows_in_one_tab():
    spec = YUNMENG_TASK_REWARD_SPECS[-1]
    intended = (spec.task_ids[0], spec.task_ids[1], spec.task_ids[2])
    before = _snapshot(spec, claimable=intended)
    assets = YunmengTaskRewardGuiAssets(
        home_scene_id=558,
        task_scene_ids={"cultivation": 565},
        tab_shape_names={"cultivation": "修炼"},
    )
    runtime = _Runtime((565, 558))
    states = [
        _snapshot(spec, claimable=intended[1:], claimed=intended[:1]),
        _snapshot(spec, claimable=intended[2:], claimed=intended[:2]),
        _snapshot(spec, claimed=intended),
    ]
    for state in states:
        state["selected_domain"] = spec.key
        state["expected_task_claimed"] = True

    result = _result(
        claim_yunmeng_task_rewards_with_runtime(
            runtime,
            before,
            {
                "authorized_task_ids": list(intended),
                "tabs": {"cultivation": list(intended)},
            },
            assets=assets,
            fast_reader=lambda *_args, **_kwargs: states.pop(0),
        )
    )

    assert result["ok"] is True
    assert result["claimed_task_ids"] == list(intended)
    assert runtime.clicks == [(565, "首条任务领取区")] * 3
    assert [item[:3] for item in runtime.wait_clicks] == [
        (558, "任务", (565,)),
        (565, "云梦试剑", (558,)),
    ]


def test_runtime_adapter_stops_after_one_click_when_quest_transition_is_not_exact():
    spec = YUNMENG_TASK_REWARD_SPECS[-1]
    intended = (spec.task_ids[0], spec.task_ids[1])
    before = _snapshot(spec, claimable=intended)
    assets = YunmengTaskRewardGuiAssets(
        home_scene_id=558,
        task_scene_ids={"cultivation": 565},
        tab_shape_names={"cultivation": "修炼"},
    )
    runtime = _Runtime((565,))
    unchanged = dict(before)
    unchanged["expected_task_claimed"] = False

    result = _result(
        claim_yunmeng_task_rewards_with_runtime(
            runtime,
            before,
            {
                "authorized_task_ids": list(intended),
                "tabs": {"cultivation": list(intended)},
            },
            assets=assets,
            fast_reader=lambda *_args, **_kwargs: unchanged,
        )
    )

    assert result["ok"] is False
    assert runtime.clicks == [(565, "首条任务领取区")]
