from __future__ import annotations

from backend.core.fanxiu.instrumentation.daily_task_rewards import (
    TASK_REWARD_DOMAIN_ORDER,
    TASK_REWARD_DOMAIN_SPECS,
    YUNMENG_TASK_REWARD_SPECS,
    build_activity_task_reward_snapshot,
)
from backend.core.fanxiu.instrumentation.yunmeng_task_rewards import (
    YUNMENG_TASK_REWARD_DOMAIN_ORDER,
    partition_yunmeng_authorized_tasks,
    plan_yunmeng_task_reward_claim,
    read_yunmeng_task_reward_snapshot,
    read_selected_yunmeng_task_reward_fast_snapshot,
    select_live_yunmeng_task_reward_snapshot,
    verify_yunmeng_task_reward_transition,
)


def _complete_snapshot(spec, *, claimable=()):
    claimable = set(claimable)
    entries = [
        {
            "taskId": task_id,
            "status": 4 if task_id in claimable else 3,
            "turn": 1 if task_id in claimable else 0,
            "rewardTime": 0,
            "progressList": [{"finish": task_id in claimable}],
        }
        for task_id in spec.task_ids
    ]
    return {
        "ok": True,
        "available": True,
        **build_activity_task_reward_snapshot(
            spec=spec,
            task_entries=entries,
            finished_task_ids=[],
        ),
    }


def test_yunmeng_variants_are_opt_in_and_do_not_expand_daily_job():
    assert TASK_REWARD_DOMAIN_ORDER == ("lundao", "lingmai", "qixi_mojie")
    assert YUNMENG_TASK_REWARD_DOMAIN_ORDER == tuple(
        spec.key for spec in YUNMENG_TASK_REWARD_SPECS
    )
    assert all(key in TASK_REWARD_DOMAIN_SPECS for key in YUNMENG_TASK_REWARD_DOMAIN_ORDER)


def test_yunmeng_variant_uses_one_exact_score_ladder():
    wallet, summon = YUNMENG_TASK_REWARD_SPECS[:2]
    assert len(wallet.task_ids) == len(set(wallet.task_ids)) == 21
    assert len(summon.task_ids) == len(set(summon.task_ids)) == 21
    assert set(wallet.task_ids) - set(summon.task_ids) == set(range(1160009, 1160017))
    assert set(summon.task_ids) - set(wallet.task_ids) == set(range(1160022, 1160030))


def test_current_cross_server_yunmeng_uses_816_task_family():
    wallet, summon = YUNMENG_TASK_REWARD_SPECS[-2:]
    assert wallet.activity_id == summon.activity_id == 8210001
    assert wallet.label == summon.label == "8跨云梦试剑"
    assert set(wallet.task_ids) - set(summon.task_ids) == set(range(8160009, 8160017))
    assert set(summon.task_ids) - set(wallet.task_ids) == set(range(8160022, 8160030))


def test_selects_single_complete_variant_and_preserves_authorization_order():
    spec = YUNMENG_TASK_REWARD_SPECS[1]
    expected = (spec.task_ids[2], spec.task_ids[8])
    snapshots = {
        key: {"ok": True, "available": True, "complete": False, "state": "ambiguous"}
        for key in YUNMENG_TASK_REWARD_DOMAIN_ORDER
    }
    snapshots[spec.key] = _complete_snapshot(spec, claimable=expected)

    selected = select_live_yunmeng_task_reward_snapshot(snapshots)

    assert selected["ok"] is True
    assert selected["selected_domain"] == spec.key
    assert selected["authorized_claim_task_ids"] == list(expected)


def test_no_complete_variant_fails_closed_without_claim_authorization():
    selected = select_live_yunmeng_task_reward_snapshot({})
    assert selected["ok"] is False
    assert selected["state"] == "ambiguous"
    assert selected["authorized_claim_task_ids"] == []


def test_multiple_complete_variants_fail_closed():
    first, second = YUNMENG_TASK_REWARD_SPECS[:2]
    selected = select_live_yunmeng_task_reward_snapshot(
        {first.key: _complete_snapshot(first), second.key: _complete_snapshot(second)}
    )
    assert selected["ok"] is False
    assert selected["authorized_claim_task_ids"] == []
    assert selected["candidate_domains"] == [first.key, second.key]


def test_reader_checks_every_retained_variant():
    spec = YUNMENG_TASK_REWARD_SPECS[2]
    calls = []

    def reader(domain):
        calls.append(domain)
        if domain == spec.key:
            return _complete_snapshot(spec)
        return {"ok": True, "available": True, "complete": False, "state": "ambiguous"}

    selected = read_yunmeng_task_reward_snapshot(reader=reader)
    assert calls == list(YUNMENG_TASK_REWARD_DOMAIN_ORDER)
    assert selected["selected_domain"] == spec.key


def test_fast_reader_reuses_the_selected_exact_variant(monkeypatch):
    spec = YUNMENG_TASK_REWARD_SPECS[-1]
    calls = []

    def fake_fast(domain, *, expected_claimed_task_id=None):
        calls.append((domain, expected_claimed_task_id))
        return {"ok": True, "domain": domain, "complete": True}

    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.yunmeng_task_rewards."
        "read_activity_task_reward_fast_snapshot",
        fake_fast,
    )

    result = read_selected_yunmeng_task_reward_fast_snapshot(
        spec.key,
        expected_claimed_task_id=spec.task_ids[0],
    )

    assert calls == [(spec.key, spec.task_ids[0])]
    assert result["selected_domain"] == spec.key


def test_fast_reader_rejects_unselected_domains():
    try:
        read_selected_yunmeng_task_reward_fast_snapshot("lundao")
    except ValueError as exc:
        assert "未知云梦任务梯度" in str(exc)
    else:
        raise AssertionError("expected fail-closed domain validation")


def test_partitions_only_selected_variant_authorization_by_visible_tab():
    spec = YUNMENG_TASK_REWARD_SPECS[-1]
    selected = _complete_snapshot(
        spec,
        claimable=(spec.task_ids[1], spec.task_ids[9], spec.task_ids[18]),
    )
    selected["selected_domain"] = spec.key

    assert partition_yunmeng_authorized_tasks(selected) == {
        "cultivation": [spec.task_ids[1]],
        "score": [spec.task_ids[9]],
        "ranking": [spec.task_ids[18]],
    }


def test_partition_fails_closed_without_a_selected_complete_variant():
    assert partition_yunmeng_authorized_tasks(
        {"ok": False, "complete": False, "authorized_claim_task_ids": [8160001]}
    ) == {"cultivation": [], "score": [], "ranking": []}


def test_claim_plan_is_noop_when_live_ladder_is_already_claimed():
    spec = YUNMENG_TASK_REWARD_SPECS[-1]
    snapshot = _complete_snapshot(spec)
    snapshot["selected_domain"] = spec.key
    snapshot["state"] = "already_claimed"

    plan = plan_yunmeng_task_reward_claim(snapshot)

    assert plan["status"] == "already_claimed"
    assert plan["authorized_task_ids"] == []


def test_claim_plan_is_ready_only_for_fully_partitioned_authorization():
    spec = YUNMENG_TASK_REWARD_SPECS[-1]
    snapshot = _complete_snapshot(
        spec,
        claimable=(spec.task_ids[0], spec.task_ids[8], spec.task_ids[16]),
    )
    snapshot["selected_domain"] = spec.key

    plan = plan_yunmeng_task_reward_claim(snapshot)

    assert plan["status"] == "ready"
    assert plan["authorized_task_ids"] == [
        spec.task_ids[0],
        spec.task_ids[8],
        spec.task_ids[16],
    ]


def test_verifies_exact_complete_claim_transition():
    spec = YUNMENG_TASK_REWARD_SPECS[-1]
    before = _complete_snapshot(spec, claimable=(spec.task_ids[0], spec.task_ids[9]))
    before["selected_domain"] = spec.key
    after = _complete_snapshot(spec)
    after["selected_domain"] = spec.key
    after["claimed_task_ids"] = list(before["claimed_task_ids"]) + [
        spec.task_ids[0],
        spec.task_ids[9],
    ]

    result = verify_yunmeng_task_reward_transition(before, after)

    assert result["ok"] is True
    assert result["newly_claimed_task_ids"] == [spec.task_ids[0], spec.task_ids[9]]


def test_rejects_unrelated_or_partial_claim_transition():
    spec = YUNMENG_TASK_REWARD_SPECS[-1]
    before = _complete_snapshot(spec, claimable=(spec.task_ids[0], spec.task_ids[9]))
    before["selected_domain"] = spec.key
    after = _complete_snapshot(spec, claimable=(spec.task_ids[9],))
    after["selected_domain"] = spec.key
    after["claimed_task_ids"] = list(before["claimed_task_ids"]) + [spec.task_ids[0]]

    result = verify_yunmeng_task_reward_transition(before, after)

    assert result["ok"] is False
    assert "精确授权迁移" in result["reason"] or "残留" in result["reason"]
