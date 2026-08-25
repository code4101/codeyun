from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from backend.core.fanxiu.activity.magic_invasion_explore import (
    MagicInvasionExploreEvidence,
    actual_magic_invasion_topup,
    completed_magic_invasion_batches,
    observe_magic_invasion_effect,
    plan_magic_invasion_explore,
)
from backend.core.fanxiu.runtime_gui.magic_invasion import (
    resolve_magic_invasion_bottom_tab,
)
from backend.core.fanxiu.data_annotation.tasks.magic_invasion_tail import (
    _exchange_shop_business_ready,
    _group_ocr_tokens,
    _resolve_exact_magic_calendar_fallback,
    _ui_calendar_day_offset,
)

def test_batches_accumulate_across_runs_but_not_activity_instances() -> None:
    evidence = [
        MagicInvasionExploreEvidence(500, activity_instance_id="cross", run_id="a", result_explore_count=861),
        MagicInvasionExploreEvidence(500, activity_instance_id="server", run_id="b", result_explore_count=834),
        MagicInvasionExploreEvidence(500, activity_instance_id="server", run_id="c", result_explore_count=854),
    ]

    assert completed_magic_invasion_batches(evidence, activity_instance_id="server") == 2
    assert completed_magic_invasion_batches(evidence, activity_instance_id="cross") == 1
    plan = plan_magic_invasion_explore(
        evidence,
        activity_instance_id="server",
        current_map_count=1,
        gameplay_available=True,
    )
    assert plan.completed_batches == 2
    assert plan.remaining_batches == 1
    assert plan.topup_count == 499
    assert plan.should_explore is True


def test_result_bonus_count_does_not_create_extra_base_batches() -> None:
    evidence = [MagicInvasionExploreEvidence(500, activity_instance_id="server", result_explore_count=854)]

    assert completed_magic_invasion_batches(evidence, activity_instance_id="server") == 1


def test_partial_confirmed_counts_accumulate_across_runs() -> None:
    evidence = [
        MagicInvasionExploreEvidence(250, activity_instance_id="server", run_id="run-a"),
        MagicInvasionExploreEvidence(250, activity_instance_id="server", run_id="run-b"),
    ]

    assert completed_magic_invasion_batches(evidence, activity_instance_id="server") == 1


def test_topup_only_fills_current_map_count_to_500() -> None:
    plan = plan_magic_invasion_explore(
        [], activity_instance_id="server", current_map_count=1, gameplay_available=True
    )

    assert plan.topup_count == 499
    assert plan.should_explore is True


def test_manual_gameplay_blocks_all_explore_clicks_by_default() -> None:
    plan = plan_magic_invasion_explore([], activity_instance_id="server", current_map_count=1)

    assert plan.should_explore is False
    assert plan.topup_count == 0
    assert plan.blocked_by_manual_gameplay is True


def test_inventory_delta_is_quantity_authority() -> None:
    assert actual_magic_invasion_topup(inventory_before=2003, inventory_after=1502) == 501


def test_direct_mount_effect_does_not_mean_pending_events_are_complete() -> None:
    observation = observe_magic_invasion_effect(
        score_before=0,
        score_after=5000,
        currency_before=0,
        currency_after=8560,
        challenges_completed=0,
        pending_event_count=99,
    )

    assert observation.effect_observed is True
    assert observation.manual_challenge_required is True
    assert observation.rewards_complete is False


def test_bottom_tab_alignment_rejects_horizontal_title_ocr_collision() -> None:
    target = resolve_magic_invasion_bottom_tab(
        [
            {"text": "兑换宝阁", "x": 263, "y": 75, "w": 393, "h": 106, "score": 0.84},
            {"text": "兑换宝阁", "x": 779, "y": 1348, "w": 43, "h": 162, "score": 0.997},
        ],
        tab_name="兑换宝阁",
        frame_width=900,
        frame_height=1600,
    )

    assert target.x == 800.5
    assert target.y == 1429
    assert target.score == 0.997


def test_tail_groups_full_frame_tokens_back_into_schedule_lines() -> None:
    lines = _group_ocr_tokens([
        {"text": "魔", "x": 10, "y": 20, "w": 10, "h": 12, "parent_line_id": "a", "order": 0},
        {"text": "道", "x": 20, "y": 20, "w": 11, "h": 12, "parent_line_id": "a", "order": 1},
        {"text": "今天", "x": 50, "y": 5, "w": 20, "h": 10, "parent_line_id": "b", "order": 0},
    ])

    assert [line["text"] for line in lines] == ["今天", "魔道"]
    assert lines[1]["w"] == 21


def test_tail_reassembles_vertical_shop_tab_before_alignment() -> None:
    lines = _group_ocr_tokens([
        {"text": "T", "x": 782, "y": 1330, "w": 42, "h": 7, "parent_line_id": "tab"},
        {"text": "兑", "x": 782, "y": 1355, "w": 42, "h": 42, "parent_line_id": "tab"},
        {"text": "换", "x": 782, "y": 1397, "w": 42, "h": 42, "parent_line_id": "tab"},
        {"text": "宝", "x": 782, "y": 1439, "w": 42, "h": 41, "parent_line_id": "tab"},
        {"text": "阁", "x": 782, "y": 1473, "w": 42, "h": 42, "parent_line_id": "tab"},
    ])
    target = resolve_magic_invasion_bottom_tab(
        lines,
        tab_name="兑换宝阁",
        frame_width=900,
        frame_height=1600,
    )

    assert lines[0]["text"] == "T兑换宝阁"
    assert target.x == 803
    assert target.y == 1422.5


def test_tail_shop_ready_accepts_server_and_cross_server_wallet_labels() -> None:
    common = [
        {"text": "兑换宝阁", "x": 260, "y": 70, "w": 390, "h": 100},
        {"text": "兑换宝阁", "x": 780, "y": 1340, "w": 42, "h": 160},
    ]

    assert _exchange_shop_business_ready(
        common
        + [
            {"text": "当前拥有魔晶4006", "x": 210, "y": 200, "w": 480, "h": 40},
            {"text": "活动期间累计魔晶159666", "x": 190, "y": 250, "w": 520, "h": 40},
        ]
    )
    assert _exchange_shop_business_ready(
        common
        + [
            {"text": "当前拥有位面魔晶318790", "x": 180, "y": 200, "w": 540, "h": 40},
            {"text": "活动期间累计位面魔晶410790", "x": 160, "y": 250, "w": 580, "h": 40},
        ]
    )


def test_tail_shop_ready_rejects_incomplete_wallet_identity() -> None:
    assert not _exchange_shop_business_ready(
        [
            {"text": "兑换宝阁", "x": 260, "y": 70, "w": 390, "h": 100},
            {"text": "兑换宝阁", "x": 780, "y": 1340, "w": 42, "h": 160},
            {"text": "当前拥有位面魔晶318790", "x": 180, "y": 200, "w": 540, "h": 40},
        ]
    )


def test_tail_early_planned_run_uses_real_ui_today_column() -> None:
    assert _ui_calendar_day_offset(
        target_date=date(2026, 8, 22),
        ui_today=date(2026, 8, 22),
    ) == 0


def test_tail_normal_midnight_run_uses_yesterday_column() -> None:
    assert _ui_calendar_day_offset(
        target_date=date(2026, 8, 22),
        ui_today=date(2026, 8, 23),
    ) == -1


def test_tail_recovers_truncated_title_only_with_exact_instance_qualifier() -> None:
    entity = SimpleNamespace(
        key="8070001|8070001400004|6400002",
        payload={"name": "魔道入侵", "littleName": "跨服[8]"},
    )
    targets = _resolve_exact_magic_calendar_fallback(
        calendar_lines=[
            {"text": "魔道入侵", "x": 98, "y": 372, "w": 131, "h": 35},
            {"text": "(预赛)", "x": 123, "y": 406, "w": 88, "h": 40},
            {"text": "魔道入", "x": 174, "y": 477, "w": 95, "h": 36},
            {"text": "跨服[8]", "x": 190, "y": 514, "w": 93, "h": 38},
        ],
        runtime_entity=entity,
        day_offset=0,
        target_x=317,
    )

    assert len(targets) == 1
    assert targets[0].runtime_key == entity.key
    assert targets[0].matched_text == "魔道入 跨服[8]"


def test_tail_rejects_truncated_title_without_exact_instance_qualifier() -> None:
    entity = SimpleNamespace(
        key="8070001|8070001400004|6400002",
        payload={"name": "魔道入侵", "littleName": "跨服[8]"},
    )

    assert not _resolve_exact_magic_calendar_fallback(
        calendar_lines=[
            {"text": "魔道入", "x": 174, "y": 477, "w": 95, "h": 36},
            {"text": "跨服[16]", "x": 190, "y": 514, "w": 110, "h": 38},
        ],
        runtime_entity=entity,
        day_offset=0,
        target_x=317,
    )
