from backend.core.fanxiu.instrumentation.weekly_activity import (
    WEEKLY_ACTIVITY_TIERS,
    build_weekly_activity_snapshot,
)


def _runtime_tiers():
    return [
        {"config_id": tier["config_id"], "type": 2, "threshold": tier["threshold"]}
        for tier in WEEKLY_ACTIVITY_TIERS
    ]


def test_weekly_activity_snapshot_reports_exact_authoritative_tiers_and_claimable():
    result = build_weekly_activity_snapshot(
        active_num=800,
        all_active_num=800,
        claimed_config_ids=[13, 14],
        runtime_tiers=_runtime_tiers(),
        claimed_declared_count=2,
    )

    assert result["complete"] is True
    assert result["thresholds"] == [400, 600, 800, 1200, 1600, 2000, 2400]
    assert result["claimed_thresholds"] == [400, 600]
    assert result["claimable_config_ids"] == [15]
    assert result["claimable_thresholds"] == [800]
    assert result["status"] == "claimable"


def test_weekly_activity_snapshot_all_claimed_is_zero_action_terminal():
    result = build_weekly_activity_snapshot(
        active_num=2400,
        all_active_num=2400,
        claimed_config_ids=range(13, 20),
        runtime_tiers=_runtime_tiers(),
        claimed_declared_count=7,
    )

    assert result["complete"] is True
    assert result["status"] == "already_claimed"
    assert result["claimable"] == []
    assert result["claimed_thresholds"] == [400, 600, 800, 1200, 1600, 2000, 2400]


def test_weekly_activity_snapshot_accepts_empty_derived_config_cache_with_static_authority():
    result = build_weekly_activity_snapshot(
        active_num=2400,
        all_active_num=0,
        claimed_config_ids=range(13, 20),
        runtime_tiers=[],
        claimed_declared_count=7,
    )

    assert result["complete"] is True
    assert result["status"] == "already_claimed"
    assert result["tier_authority"] == "static_generated_config"
    assert result["all_active_num"] == 0
    assert result["claimable"] == []


def test_weekly_activity_snapshot_below_next_threshold_is_pending_not_success():
    result = build_weekly_activity_snapshot(
        active_num=800,
        all_active_num=800,
        claimed_config_ids=[13, 14, 15],
        runtime_tiers=_runtime_tiers(),
        claimed_declared_count=3,
    )

    assert result["complete"] is True
    assert result["status"] == "pending_threshold"
    assert result["claimable"] == []


def test_weekly_activity_snapshot_fails_closed_on_partial_runtime_config():
    result = build_weekly_activity_snapshot(
        active_num=2400,
        all_active_num=2400,
        claimed_config_ids=range(13, 20),
        runtime_tiers=_runtime_tiers()[4:],
        claimed_declared_count=7,
    )

    assert result["complete"] is False
    assert result["status"] == "ambiguous"
    assert result["claimable"] == []
    assert "weekly_tier_contract_mismatch" in result["reason"]


def test_weekly_activity_snapshot_fails_closed_on_unknown_or_racy_claim_list():
    result = build_weekly_activity_snapshot(
        active_num=2400,
        all_active_num=2400,
        claimed_config_ids=[13, 13, 99],
        runtime_tiers=_runtime_tiers(),
        claimed_declared_count=4,
    )

    assert result["complete"] is False
    assert result["claimable"] == []
    assert "claimed_count_mismatch" in result["reason"]
    assert "duplicate_claimed_config_id" in result["reason"]
    assert "unknown_claimed_config_id" in result["reason"]
