from __future__ import annotations

from dataclasses import replace

from backend.core.fanxiu.activity.lianti_faxiang_planner import (
    BREAKTHROUGH_ESSENCE_ITEM_ID,
    SCORE_PER_TEMPERING_ESSENCE,
    TEMPERING_ESSENCE_ITEM_ID,
    LiantiQuestEntry,
    LiantiResourceFacts,
    LiantiTaskMilestone,
    plan_lianti_must_get_resource_use,
)


NOW = "2026-08-14T15:00:30+08:00"


def _milestones(prefix: int = 104301150) -> tuple[LiantiTaskMilestone, ...]:
    targets = (5_000, 10_000, 20_000, 30_000, 40_000)
    return tuple(
        LiantiTaskMilestone(
            task_id=prefix + order,
            target=target,
            order=order,
            must_get=order <= 3,
        )
        for order, target in enumerate(targets, 1)
    )


def _entries(
    milestones: tuple[LiantiTaskMilestone, ...], score: int = 12_300
) -> tuple[LiantiQuestEntry, ...]:
    return tuple(
        LiantiQuestEntry(
            task_id=row.task_id,
            target=row.target,
            progress=min(score, row.target),
            finished=score >= row.target,
        )
        for row in milestones
    )


def _facts(score: int = 12_300) -> LiantiResourceFacts:
    return LiantiResourceFacts(
        activity_instance_id="runtime-1043011-1786654805000-0",
        activity_id=1043011,
        quest_entries_complete=True,
        current_score=score,
        tempering_essence_count=100,
        breakthrough_essence_count=3,
        breakthroughs_required_to_target=1,
        quest_captured_at="2026-08-14T15:00:10+08:00",
        score_captured_at="2026-08-14T15:00:11+08:00",
        inventory_captured_at="2026-08-14T15:00:12+08:00",
    )


def test_plans_only_to_current_period_highest_must_get_tier() -> None:
    current = _milestones()
    # The static table retains an older 101.. ladder. It must not influence
    # the target because its ids are absent from this period's QuestEntryVO.
    old = _milestones(104301100)
    plan = plan_lianti_must_get_resource_use(
        facts=_facts(),
        configured_milestones=old + current,
        quest_entries=_entries(current),
        now=NOW,
    )

    assert plan.kind == "use"
    assert plan.target_task_id == 104301153
    assert plan.target_score == 20_000
    assert plan.must_get_task_ids == (104301151, 104301152, 104301153)
    assert plan.tempering_essence_to_use == 77
    assert plan.expected_score_after == 20_000
    assert TEMPERING_ESSENCE_ITEM_ID == 5030001
    assert BREAKTHROUGH_ESSENCE_ITEM_ID == 5030002
    assert SCORE_PER_TEMPERING_ESSENCE == 100


def test_rounds_up_one_item_without_selecting_a_higher_non_must_get_tier() -> None:
    milestones = _milestones()
    facts = _facts(score=19_950)
    plan = plan_lianti_must_get_resource_use(
        facts=facts,
        configured_milestones=milestones,
        quest_entries=_entries(milestones, score=19_950),
        now=NOW,
    )

    assert plan.kind == "use"
    assert plan.tempering_essence_to_use == 1
    assert plan.expected_score_after == 20_050
    assert plan.target_score == 20_000


def test_already_at_must_get_target_is_idempotently_complete() -> None:
    milestones = _milestones()
    facts = _facts(score=20_000)
    plan = plan_lianti_must_get_resource_use(
        facts=facts,
        configured_milestones=milestones,
        quest_entries=_entries(milestones, score=20_000),
        now=NOW,
    )

    assert plan.kind == "complete"
    assert plan.tempering_essence_to_use == 0


def test_incomplete_or_unjoinable_current_quest_membership_blocks() -> None:
    milestones = _milestones()
    entries = _entries(milestones)
    incomplete = plan_lianti_must_get_resource_use(
        facts=replace(_facts(), quest_entries_complete=False),
        configured_milestones=milestones,
        quest_entries=entries,
        now=NOW,
    )
    missing = plan_lianti_must_get_resource_use(
        facts=_facts(),
        configured_milestones=milestones[:-1],
        quest_entries=entries,
        now=NOW,
    )

    assert incomplete.kind == "blocked"
    assert "不完整" in incomplete.reason
    assert missing.kind == "blocked"
    assert "无法完整连接" in missing.reason


def test_runtime_target_and_score_progress_must_match_config() -> None:
    milestones = _milestones()
    entries = list(_entries(milestones))
    entries[2] = replace(entries[2], target=20_100)
    target_mismatch = plan_lianti_must_get_resource_use(
        facts=_facts(),
        configured_milestones=milestones,
        quest_entries=tuple(entries),
        now=NOW,
    )
    progress_entries = list(_entries(milestones))
    progress_entries[2] = replace(progress_entries[2], progress=12_200)
    progress_mismatch = plan_lianti_must_get_resource_use(
        facts=_facts(),
        configured_milestones=milestones,
        quest_entries=tuple(progress_entries),
        now=NOW,
    )

    assert target_mismatch.kind == "blocked"
    assert "目标不一致" in target_mismatch.reason
    assert progress_mismatch.kind == "blocked"
    assert "进度与实时炼体积分不一致" in progress_mismatch.reason


def test_no_explicit_current_must_get_tier_blocks() -> None:
    milestones = tuple(replace(row, must_get=False) for row in _milestones())
    plan = plan_lianti_must_get_resource_use(
        facts=_facts(),
        configured_milestones=milestones,
        quest_entries=_entries(milestones),
        now=NOW,
    )

    assert plan.kind == "blocked"
    assert "没有明确必拿档" in plan.reason


def test_stale_or_cross_window_facts_block() -> None:
    milestones = _milestones()
    entries = _entries(milestones)
    stale = plan_lianti_must_get_resource_use(
        facts=replace(
            _facts(), inventory_captured_at="2026-08-14T14:57:00+08:00"
        ),
        configured_milestones=milestones,
        quest_entries=entries,
        now=NOW,
    )
    skewed = plan_lianti_must_get_resource_use(
        facts=replace(
            _facts(), inventory_captured_at="2026-08-14T15:00:50+08:00"
        ),
        configured_milestones=milestones,
        quest_entries=entries,
        now="2026-08-14T15:01:00+08:00",
    )

    assert stale.kind == "blocked"
    assert "已过期" in stale.reason
    assert skewed.kind == "blocked"
    assert "同一采集窗口" in skewed.reason


def test_breakthrough_path_must_be_proven_and_funded() -> None:
    milestones = _milestones()
    entries = _entries(milestones)
    unknown = plan_lianti_must_get_resource_use(
        facts=replace(_facts(), breakthroughs_required_to_target=None),
        configured_milestones=milestones,
        quest_entries=entries,
        now=NOW,
    )
    short = plan_lianti_must_get_resource_use(
        facts=replace(
            _facts(), breakthrough_essence_count=1, breakthroughs_required_to_target=2
        ),
        configured_milestones=milestones,
        quest_entries=entries,
        now=NOW,
    )

    assert unknown.kind == "blocked"
    assert "所需的突破次数" in unknown.reason
    assert short.kind == "blocked"
    assert "龙髓精魄不足" in short.reason


def test_tempering_inventory_shortage_blocks_without_partial_plan() -> None:
    milestones = _milestones()
    plan = plan_lianti_must_get_resource_use(
        facts=replace(_facts(), tempering_essence_count=76),
        configured_milestones=milestones,
        quest_entries=_entries(milestones),
        now=NOW,
    )

    assert plan.kind == "blocked"
    assert plan.tempering_essence_to_use == 0
    assert "淬体精魄不足" in plan.reason


def test_wrong_activity_identity_blocks() -> None:
    milestones = _milestones()
    plan = plan_lianti_must_get_resource_use(
        facts=replace(_facts(), activity_id=1043010),
        configured_milestones=milestones,
        quest_entries=_entries(milestones),
        now=NOW,
    )

    assert plan.kind == "blocked"
    assert "1043011" in plan.reason
