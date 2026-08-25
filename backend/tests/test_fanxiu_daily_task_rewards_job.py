from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.core.fanxiu.data_annotation.tasks.daily_task_rewards import (
    DAILY_TASK_REWARD_DOMAIN_ORDER,
    DailyTaskRewardsTaskMixin,
    claim_first_row_until_clear,
    next_daily_task_reward_time,
    run_daily_task_rewards_job,
)


def _snapshot(domain: str, *, state: str = "none", claimable=()) -> dict:
    return {
        "ok": True,
        "available": True,
        "complete": True,
        "domain": domain,
        "state": state,
        "authorized_claim_task_ids": list(claimable),
        "claimed_task_ids": [],
    }


def _drain(generator):
    while True:
        try:
            next(generator)
        except StopIteration as exc:
            return exc.value


def test_next_time_is_always_next_day_0630_and_preserves_timezone() -> None:
    before = datetime(2026, 8, 14, 1, 0, tzinfo=timezone.utc)
    after = datetime(2026, 8, 14, 23, 59, tzinfo=timezone.utc)
    assert next_daily_task_reward_time(before) == datetime(2026, 8, 15, 6, 30, tzinfo=timezone.utc)
    assert next_daily_task_reward_time(after) == datetime(2026, 8, 15, 6, 30, tzinfo=timezone.utc)


def test_domain_order_and_dongtian_auto_mail_skip_are_fixed() -> None:
    result = run_daily_task_rewards_job(
        reader=lambda domain: _snapshot(domain),
        now=datetime(2026, 8, 14, 6, 30),
    )
    assert DAILY_TASK_REWARD_DOMAIN_ORDER == ("lundao", "qixi_mojie", "lingmai")
    assert [row["domain"] for row in result["domains"]] == [
        "lundao",
        "qixi_mojie",
        "lingmai",
        "dongtian",
    ]
    assert result["domains"][-1]["status"] == "skipped_auto_mail"
    assert result["next_time"] == "2026-08-15 06:30:00"


def test_claimable_domain_without_gui_evidence_is_pending_research() -> None:
    called = []

    def reader(domain: str) -> dict:
        called.append(domain)
        return _snapshot(domain, state="claimable", claimable=[100 + len(called)])

    result = run_daily_task_rewards_job(reader=reader)
    assert called == list(DAILY_TASK_REWARD_DOMAIN_ORDER)
    assert [row["status"] for row in result["domains"][:3]] == ["pending_research"] * 3
    assert result["status"] == "completed_with_pending"


def test_failure_is_isolated_and_later_domains_still_run() -> None:
    called = []

    def reader(domain: str) -> dict:
        called.append(domain)
        if domain == "lundao":
            raise RuntimeError("not loaded")
        if domain == "qixi_mojie":
            return {"ok": True, "available": True, "complete": False, "state": "ambiguous"}
        return _snapshot(domain, state="already_claimed")

    result = run_daily_task_rewards_job(reader=reader)
    assert called == list(DAILY_TASK_REWARD_DOMAIN_ORDER)
    assert [row["status"] for row in result["domains"][:3]] == [
        "fail_closed",
        "fail_closed",
        "already_claimed",
    ]


def test_idempotent_states_never_call_gui_adapter() -> None:
    adapter_calls = []

    def adapter(snapshot: dict) -> dict:
        adapter_calls.append(snapshot)
        return {"ok": True}

    states = {
        "lundao": _snapshot("lundao", state="already_claimed"),
        "qixi_mojie": _snapshot("qixi_mojie", state="none"),
        "lingmai": _snapshot("lingmai", state="none"),
    }
    result = run_daily_task_rewards_job(
        reader=lambda domain: states[domain],
        gui_adapters={domain: adapter for domain in DAILY_TASK_REWARD_DOMAIN_ORDER},
    )
    assert adapter_calls == []
    assert [row["status"] for row in result["domains"][:3]] == [
        "already_claimed",
        "nothing_claimable",
        "nothing_claimable",
    ]
    assert result["status"] == "completed"


def test_gui_success_requires_post_claim_questmgr_verification() -> None:
    reads = {"lundao": 0}

    def reader(domain: str) -> dict:
        if domain != "lundao":
            return _snapshot(domain)
        reads[domain] += 1
        if reads[domain] == 1:
            return _snapshot(domain, state="claimable", claimable=[11160101])
        result = _snapshot(domain, state="none")
        result["claimed_task_ids"] = [11160101]
        return result

    result = run_daily_task_rewards_job(
        reader=reader,
        gui_adapters={"lundao": lambda _snapshot: {"ok": True}},
    )
    assert result["domains"][0]["status"] == "claimed"
    assert result["domains"][0]["claimed_task_ids"] == [11160101]


def test_unverified_gui_result_does_not_report_completion() -> None:
    result = run_daily_task_rewards_job(
        reader=lambda domain: _snapshot(domain, state="claimable", claimable=[1]),
        gui_adapters={"lundao": lambda _snapshot: {"ok": True}},
    )
    assert result["domains"][0]["status"] == "unverified"
    assert result["status"] == "completed_with_pending"


def test_default_reader_uses_one_shared_initial_snapshot(monkeypatch) -> None:
    import backend.core.fanxiu.data_annotation.tasks.daily_task_rewards as module

    batch_calls = []
    single_calls = []

    def batch_reader() -> dict:
        batch_calls.append(True)
        return {
            "domains": {
                domain: _snapshot(domain, state="none")
                for domain in DAILY_TASK_REWARD_DOMAIN_ORDER
            }
        }

    monkeypatch.setattr(module, "read_all_activity_task_reward_snapshots", batch_reader)
    monkeypatch.setattr(
        module,
        "read_activity_task_reward_snapshot",
        lambda domain: single_calls.append(domain) or _snapshot(domain),
    )

    result = run_daily_task_rewards_job()

    assert batch_calls == [True]
    assert single_calls == []
    assert result["status"] == "completed"


def test_default_reader_rereads_only_clicked_domain(monkeypatch) -> None:
    import backend.core.fanxiu.data_annotation.tasks.daily_task_rewards as module

    initial_lundao = _snapshot("lundao", state="claimable", claimable=[11160101])
    initial = {
        "lundao": initial_lundao,
        "qixi_mojie": _snapshot("qixi_mojie"),
        "lingmai": _snapshot("lingmai"),
    }
    single_calls = []

    monkeypatch.setattr(
        module,
        "read_all_activity_task_reward_snapshots",
        lambda: {"domains": initial},
    )

    def single_reader(domain: str) -> dict:
        single_calls.append(domain)
        after = _snapshot(domain)
        after["claimed_task_ids"] = [11160101]
        return after

    monkeypatch.setattr(module, "read_activity_task_reward_snapshot", single_reader)

    result = run_daily_task_rewards_job(
        gui_adapters={"lundao": lambda _snapshot: {"ok": True}},
    )

    assert single_calls == ["lundao"]
    assert result["domains"][0]["status"] == "claimed"


def test_first_row_claim_loop_requires_exact_ordered_state_transition() -> None:
    states = [
        _snapshot("lundao", state="claimable", claimable=[11, 12]),
        {
            **_snapshot("lundao", state="claimable", claimable=[12]),
            "claimed_task_ids": [11],
            "expected_task_claimed": True,
        },
        {
            **_snapshot("lundao", state="none"),
            "claimed_task_ids": [11, 12],
            "expected_task_claimed": True,
        },
    ]
    expected_calls = []

    def reader(_domain, *, expected_claimed_task_id=None):
        expected_calls.append(expected_claimed_task_id)
        return states.pop(0)

    class Runtime:
        def __init__(self):
            self.clicks = []
            self.settles = []

        def click_frame_point(self, scene_id, x, y):
            self.clicks.append((scene_id, x, y))

        def wait_action_settle(self, seconds):
            self.settles.append(seconds)
            if False:
                yield None

    runtime = Runtime()
    result = _drain(claim_first_row_until_clear(
        domain="lundao",
        scene_id=550,
        runtime=runtime,
        reader=reader,
    ))
    assert result["ok"] is True
    assert result["claimed_task_ids"] == [11, 12]
    assert runtime.clicks == [(550, 560.0, 365.0), (550, 560.0, 365.0)]
    assert expected_calls == [None, 11, 12]


def test_first_row_claim_loop_stops_on_non_monotonic_result() -> None:
    before = _snapshot("lundao", state="claimable", claimable=[11, 12])
    unchanged = {
        **_snapshot("lundao", state="claimable", claimable=[11, 12]),
        "expected_task_claimed": False,
    }
    states = [before, unchanged]

    class Runtime:
        def click_frame_point(self, *_args):
            return None

        def wait_action_settle(self, *_args, **_kwargs):
            if False:
                yield None

    def reader(_domain, *, expected_claimed_task_id=None):
        return states.pop(0)

    result = _drain(claim_first_row_until_clear(
        domain="lundao", scene_id=550, runtime=Runtime(), reader=reader
    ))
    assert result["ok"] is False
    assert "精确单步状态迁移" in result["reason"]


class _WorkflowRuntime:
    def __init__(self):
        import threading

        self.stop_event = threading.Event()
        self.payload = {}
        self.ctx = {}
        self.clicks = []
        self.entries = []
        self.departures = 0
        self.message = ""
        self.next_times = []

    def wait_click_then_view(self, scene_id, shape, candidates, **_kwargs):
        self.entries.append((scene_id, shape, tuple(candidates)))
        if False:
            yield None

    def click_frame_point(self, scene_id, x, y):
        self.clicks.append((scene_id, x, y))

    def wait_action_settle(self, _seconds):
        if False:
            yield None

    def goto_view(self, scene_id):
        assert scene_id == 34
        self.departures += 1
        if False:
            yield None

    def set_completion_message(self, message):
        self.message = message

    def set_next_time(self, next_time):
        self.next_times.append(next_time)


def test_formal_workflow_uses_one_batch_and_zero_ui_when_nothing_claimable(monkeypatch) -> None:
    import backend.core.fanxiu.data_annotation.tasks.daily_task_rewards as module

    batch_calls = []
    monkeypatch.setattr(
        module,
        "read_all_activity_task_reward_snapshots",
        lambda: batch_calls.append(True) or {
            "domains": {domain: _snapshot(domain) for domain in DAILY_TASK_REWARD_DOMAIN_ORDER}
        },
    )
    runtime = _WorkflowRuntime()
    result = _drain(DailyTaskRewardsTaskMixin().日常任务奖励流程(runtime))

    assert batch_calls == [True]
    assert runtime.entries == []
    assert runtime.clicks == []
    assert runtime.departures == 0
    assert result["current_scene"] is None
    assert result["domains"][-1]["status"] == "skipped_auto_mail"
    assert "洞天05:00" in runtime.message


def test_formal_workflow_claims_with_fast_expected_verification(monkeypatch) -> None:
    import backend.core.fanxiu.data_annotation.tasks.daily_task_rewards as module

    initial = {
        "lundao": _snapshot("lundao", state="claimable", claimable=[11160101]),
        "qixi_mojie": _snapshot("qixi_mojie"),
        "lingmai": _snapshot("lingmai"),
    }
    monkeypatch.setattr(
        module, "read_all_activity_task_reward_snapshots", lambda: {"domains": initial}
    )

    def navigate(_owner, _ctx, _stop_event, _payload, _runtime, domain):
        assert domain == "lundao"
        if False:
            yield None
        return {"scene_id": 549}

    fast_calls = []

    def fast_reader(domain, *, expected_claimed_task_id=None):
        fast_calls.append((domain, expected_claimed_task_id))
        after = _snapshot(domain)
        after.update(
            claimed_task_ids=[11160101],
            expected_task_claimed=True,
            expected_claimed_task_id=expected_claimed_task_id,
        )
        return after

    monkeypatch.setattr(module, "navigate_to_daily_task_reward_cover", navigate)
    monkeypatch.setattr(module, "read_activity_task_reward_fast_snapshot", fast_reader)
    runtime = _WorkflowRuntime()
    result = _drain(DailyTaskRewardsTaskMixin().日常任务奖励流程(runtime))

    assert runtime.entries == [(549, "任务", (550,))]
    assert runtime.clicks == [(550, 560.0, 365.0)]
    assert fast_calls == [("lundao", 11160101)]
    assert runtime.departures == 1
    assert result["domains"][0]["status"] == "claimed"


def test_formal_workflow_isolates_domain_failure_but_withholds_next_time(monkeypatch) -> None:
    import backend.core.fanxiu.data_annotation.tasks.daily_task_rewards as module

    initial = {
        "lundao": _snapshot("lundao", state="claimable", claimable=[11160101]),
        "qixi_mojie": _snapshot("qixi_mojie", state="claimable", claimable=[64220001]),
        "lingmai": _snapshot("lingmai"),
    }
    monkeypatch.setattr(
        module, "read_all_activity_task_reward_snapshots", lambda: {"domains": initial}
    )

    def navigate(_owner, _ctx, _stop_event, _payload, _runtime, domain):
        if False:
            yield None
        if domain == "lundao":
            raise RuntimeError("论道入口异常")
        return {"scene_id": 319}

    def fast_reader(domain, *, expected_claimed_task_id=None):
        after = _snapshot(domain)
        after.update(
            claimed_task_ids=[expected_claimed_task_id],
            expected_task_claimed=True,
        )
        return after

    monkeypatch.setattr(module, "navigate_to_daily_task_reward_cover", navigate)
    monkeypatch.setattr(module, "read_activity_task_reward_fast_snapshot", fast_reader)
    runtime = _WorkflowRuntime()

    with pytest.raises(RuntimeError, match="lundao=failed"):
        _drain(DailyTaskRewardsTaskMixin().日常任务奖励流程(runtime))
    assert runtime.entries == [(319, "联盟任务", (551,))]
    assert runtime.clicks == [(551, 470.0, 245.0)]
    assert runtime.message == ""
    assert runtime.departures == 2
