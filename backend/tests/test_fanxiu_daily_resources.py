from backend.core.fanxiu.data_annotation import behavior_tree_executor  # noqa: F401
from datetime import datetime

from backend.core.fanxiu.data_annotation.tasks.daily_resources import DailyResourceTaskMixin
from backend.core.fanxiu.activity.xianmeng_targets import plan_xianmeng_targets


def test_xianshi_weekly_resource_success_advances_to_same_monday_five():
    task = DailyResourceTaskMixin()
    writes: list[tuple[str, str]] = []
    task._persist_scheduler_task_next_time = lambda task_id, next_time: writes.append(
        (task_id, next_time)
    )

    result = task._record_xianshi_weekly_resources_done(
        {"__scheduler_task_id": "weekly-instance"},
        now=datetime(2026, 8, 3, 1, 33),
    )

    assert result == "2026-08-03 05:00:00"
    assert writes == [("weekly-instance", "2026-08-03 05:00:00")]


def test_xianshi_weekly_resource_success_after_reset_advances_to_next_monday():
    task = DailyResourceTaskMixin()
    writes: list[tuple[str, str]] = []
    task._persist_scheduler_task_next_time = lambda task_id, next_time: writes.append(
        (task_id, next_time)
    )

    result = task._record_xianshi_weekly_resources_done(
        {},
        now=datetime(2026, 8, 3, 5, 30),
    )

    assert result == "2026-08-10 00:00:00"
    assert writes == [("xianshi-weekly-resources", "2026-08-10 00:00:00")]


def test_daily_gongfeng_law_progress_strips_previous_required_prefix_when_current_is_enough():
    task = DailyResourceTaskMixin()

    assert task._parse_daily_gongfeng_law_progress("800011400/8000") == (11400, 8000)


def test_daily_gongfeng_law_progress_strips_previous_required_prefix_when_current_is_insufficient():
    task = DailyResourceTaskMixin()

    assert task._parse_daily_gongfeng_law_progress("80001400/8000") == (1400, 8000)


def test_daily_gongfeng_law_progress_keeps_plain_progress():
    task = DailyResourceTaskMixin()

    assert task._parse_daily_gongfeng_law_progress("11400/8000") == (11400, 8000)


def test_xianmeng_autonomous_sweep_starts_at_eleven_boundary():
    task = DailyResourceTaskMixin()

    assert task._daily_xianmeng_stamina_sweep_allowed(datetime(2026, 9, 20, 10, 59)) is False
    assert task._daily_xianmeng_stamina_sweep_allowed(datetime(2026, 9, 20, 11, 0)) is True
    assert task._daily_xianmeng_stamina_sweep_allowed(datetime(2026, 9, 20, 11, 1)) is True


def test_xianmeng_eleven_sweep_spends_even_sub_cap_low_score_stamina():
    task = DailyResourceTaskMixin()
    for remaining in (1, 60, 244):
        snapshot = {"ok": True, "complete": True, "attack_count": remaining}
        assert task._daily_xianmeng_should_continue_low_score_sweep(snapshot, {}, now=datetime(2026, 9, 20, 10, 59)) == (False, remaining)
        assert task._daily_xianmeng_should_continue_low_score_sweep(snapshot, {}, now=datetime(2026, 9, 20, 11)) == (True, remaining)
    assert task._daily_xianmeng_should_continue_low_score_sweep({"ok": False}, {}, now=datetime(2026, 9, 20, 11)) == (False, None)


def test_xianmeng_completed_batch_waits_for_evening_checkpoints():
    from backend.core.fanxiu.activity.daily_activity_job_registry import next_xianmeng_stamina_review
    assert next_xianmeng_stamina_review(datetime(2026, 9, 20, 10)) == datetime(2026, 9, 20, 20, 45)
    assert next_xianmeng_stamina_review(datetime(2026, 9, 20, 12), tail_at=datetime(2026, 9, 20, 21, 10)) == datetime(2026, 9, 20, 20, 45)
    assert next_xianmeng_stamina_review(datetime(2026, 9, 20, 20, 45)) == datetime(2026, 9, 20, 21, 50)
    assert next_xianmeng_stamina_review(datetime(2026, 9, 20, 21, 50)) is None
    assert next_xianmeng_stamina_review(datetime(2026, 9, 20, 21, 40), tail_at=datetime(2026, 9, 20, 21, 50)) == datetime(2026, 9, 20, 21, 50)
    assert next_xianmeng_stamina_review(datetime(2026, 9, 20, 22)) is None


def test_xianmeng_fallback_excludes_destroyed_own_and_ally_camps(monkeypatch):
    from backend.core.fanxiu.catalog import server_relations

    monkeypatch.setattr(server_relations, "classify_fanxiu_target_relation", lambda *, is_npc, server_id: {
        "camp": "friendly" if server_id == 1 else "non_friendly",
        "relation": "same_server" if server_id == 1 else "other_server",
    })
    camps = [
        {"id": 1, "name": "own", "server_id": 1, "ally_camp_id": 2, "pillar_cur_hp": 200, "pillar_max_hp": 600},
        {"id": 2, "name": "battlefield ally", "server_id": 2, "ally_camp_id": 1, "pillar_cur_hp": 0, "pillar_max_hp": 600},
        {"id": 3, "name": "destroyed enemy", "server_id": 3, "ally_camp_id": 0, "pillar_cur_hp": 0, "pillar_max_hp": 600},
        {"id": 4, "name": "live enemy", "server_id": 4, "ally_camp_id": 0, "pillar_cur_hp": 10, "pillar_max_hp": 600},
    ]
    result = plan_xianmeng_targets({"camps": camps})
    assert [row["id"] for row in result["candidates"]] == [4]


def test_xianmeng_fallback_skips_immune_and_ranks_shielded_after_unshielded(monkeypatch):
    from backend.core.fanxiu.catalog import server_relations

    monkeypatch.setattr(server_relations, "classify_fanxiu_target_relation", lambda *, is_npc, server_id: {
        "camp": "friendly" if server_id == 1 else "non_friendly",
        "relation": "same_server" if server_id == 1 else "other_server",
    })
    camps = [
        {"id": 1, "name": "own", "server_id": 1, "pillar_cur_hp": 600, "pillar_max_hp": 600},
        {"id": 2, "name": "immune", "server_id": 2, "pillar_cur_hp": 10, "pillar_max_hp": 600, "protect_end_time": 1001},
        {"id": 3, "name": "shielded", "server_id": 3, "pillar_cur_hp": 20, "pillar_max_hp": 600, "has_xiaoyan_mirror": True},
        {"id": 4, "name": "ready", "server_id": 4, "pillar_cur_hp": 100, "pillar_max_hp": 600, "protect_end_time": 0, "has_xiaoyan_mirror": False},
    ]
    result = plan_xianmeng_targets({"camps": camps}, now_ms=1000)
    assert [row["id"] for row in result["candidates"]] == [4, 3]


def test_xianmeng_tail_preserved_until_2150():
    task = DailyResourceTaskMixin()

    assert (
        task._daily_xianmeng_should_preserve_tail_before_triple_disable(
            datetime(2026, 9, 20, 10, 59)
        )
        is True
    )
    assert (
        task._daily_xianmeng_should_preserve_tail_before_triple_disable(
            datetime(2026, 9, 20, 11, 0)
        )
        is True
    )
    assert (
        task._daily_xianmeng_should_preserve_tail_before_triple_disable(
            datetime(2026, 9, 20, 11, 1)
        )
        is True
    )
    assert (
        task._daily_xianmeng_should_preserve_tail_before_triple_disable(
            datetime(2026, 9, 20, 21, 50)
        )
        is False
    )


def test_xianmeng_exclusion_diagnostics_come_from_the_eligibility_decision(monkeypatch):
    from copy import deepcopy
    from backend.core.fanxiu.catalog import server_relations
    from backend.core.fanxiu.activity.xianmeng_targets import describe_xianmeng_attackable_targets

    monkeypatch.setattr(server_relations, "classify_fanxiu_target_relation", lambda *, is_npc, server_id: {
        "camp": "friendly" if server_id == 1 else "unknown" if server_id == 7 else "non_friendly",
        "relation": "same_server" if server_id == 1 else "other_server",
    })
    camps = [
        {"id": i, "name": str(i), "server_id": i, "pillar_cur_hp": 10, "pillar_max_hp": 100}
        for i in range(1, 9)
    ]
    camps[0]["ally_camp_id"] = 2
    camps[2]["pillar_cur_hp"] = 0
    camps[3]["pillar_max_hp"] = None
    camps[4]["pillar_max_hp"] = 0
    camps[5]["protect_end_time"] = 1001
    snapshot = {"ok": True, "complete": True, "camp_count": len(camps), "camps": camps}
    before = deepcopy(snapshot)
    result = describe_xianmeng_attackable_targets(snapshot, now_ms=1000)
    assert snapshot == before
    assert [row["id"] for row in result["fallback_plan"]["candidates"]] == [8]
    assert {row["id"]: row["reason_code"] for row in result["skipped_targets"]} == {
        1: "friendly", 2: "battlefield_ally", 3: "depleted_score", 4: "incomplete_score",
        5: "invalid_max_score", 6: "immune", 7: "unknown_relation",
    }
    assert result["skipped_targets"] == result["fallback_plan"]["skipped_targets"]
    expired = describe_xianmeng_attackable_targets(snapshot, now_ms=1001)
    assert [row["id"] for row in expired["fallback_plan"]["candidates"]] == [6, 8]


def test_xianmeng_sweep_log_clock_matches_single_constant():
    from backend.core.fanxiu.activity.daily_activity_job_registry import (
        XIANMENG_AUTONOMOUS_SWEEP_START,
    )

    assert XIANMENG_AUTONOMOUS_SWEEP_START == (11, 0)
    assert DailyResourceTaskMixin._daily_xianmeng_autonomous_sweep_start_text() == "11:00"


def test_xianmeng_tail_sweeps_stay_separate_from_eleven_sweep():
    from backend.core.fanxiu.activity.daily_activity_job_registry import (
        XIANMENG_AUTONOMOUS_SWEEP_START,
        XIANMENG_STAMINA_SWEEPS,
    )

    assert XIANMENG_STAMINA_SWEEPS == ((20, 45), (21, 50))
    assert XIANMENG_AUTONOMOUS_SWEEP_START not in XIANMENG_STAMINA_SWEEPS


def test_xianmeng_no_opponents_terminal_requires_complete_positive_evidence(monkeypatch):
    from backend.core.fanxiu.catalog import server_relations
    monkeypatch.setattr(server_relations, "classify_fanxiu_target_relation", lambda *, is_npc, server_id: {
        "camp": "friendly" if server_id == 1 else "non_friendly",
        "relation": "same_server" if server_id == 1 else "other_server",
    })
    camps = [
        {"id": 1, "server_id": 1, "ally_camp_id": 2, "pillar_cur_hp": 100, "pillar_max_hp": 600},
        {"id": 2, "server_id": 2, "ally_camp_id": 1, "pillar_cur_hp": 100, "pillar_max_hp": 600},
        {"id": 3, "server_id": 3, "pillar_cur_hp": 0, "pillar_max_hp": 600},
    ]
    classify = plan_xianmeng_targets
    assert classify({"camps": camps, "camp_count": 3})["all_opponents_defeated"]
    assert not classify({"camps": camps, "camp_count": 4})["all_opponents_defeated"]
    assert not classify({"camps": [], "camp_count": 0})["all_opponents_defeated"]
    for hp in (None, 1):
        changed = [*camps[:2], {**camps[2], "pillar_cur_hp": hp}]
        assert not classify({"camps": changed, "camp_count": 3})["all_opponents_defeated"]
