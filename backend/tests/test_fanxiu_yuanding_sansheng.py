from __future__ import annotations

from datetime import datetime

from backend.core.fanxiu.data_annotation.default_jobs import (
    register_fanxiu_data_annotation_default_runtime_jobs,
)
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
)
from backend.core.fanxiu.data_annotation.scheduler_defaults import (
    default_data_annotation_scheduler_tasks,
)
from backend.core.fanxiu.data_annotation.tasks.yuanding_sansheng import (
    exact_fragment,
    gift_tab_fragment,
    yuanding_page_state,
    yuanding_store_state,
)
from backend.core.fanxiu.activity.yuanding_rank_strategy import (
    conservative_single_step_batch,
    cumulative_marriage_capacity,
    infer_event_score_multiplier,
    marriages_needed_for_score,
    observe_disciple_scores,
    plan_yuanding_rank_capacity,
)


def _fragment(text: str, x: float, y: float, w: float = 80, h: float = 35):
    return {"text": text, "x": x, "y": y, "w": w, "h": h}


def test_yuanding_sansheng_is_internal_under_resource_parent() -> None:
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition(
        "yuanding_sansheng_daily_gift"
    )
    assert definition is not None
    assert definition.scheduler_supported is False

    tasks = [
        item
        for item in default_data_annotation_scheduler_tasks(datetime(2026, 8, 8, 18, 0, 0))
        if item["task_type"] in {"yuanding_sansheng_daily_gift", "resource_ranking"}
    ]
    assert len(tasks) == 1
    assert tasks[0]["id"] == "resource-ranking"
    assert tasks[0]["label"] == "资源榜"
    assert tasks[0]["trigger_description"] == "动态"
    assert tasks[0]["error_retry_delay_seconds"] == 600


def test_yuanding_entry_and_gift_tab_must_be_unique() -> None:
    entry = _fragment("缘定三生", 330, 480, 160, 70)
    assert exact_fragment([entry], "缘定三生") is entry
    assert exact_fragment([entry, dict(entry)], "缘定三生") is None

    tab = _fragment("礼包", 772, 1449, 54, 95)
    assert gift_tab_fragment([tab]) is tab
    assert gift_tab_fragment([_fragment("礼包", 300, 700)]) is None


def test_yuanding_store_state_distinguishes_free_and_claimed() -> None:
    common = [
        _fragment("冲榜商店", 416, 323, 370, 95),
        _fragment("免费冲榜礼包", 125, 589, 221, 38),
    ]
    claimable = common + [
        _fragment("每日限购：1", 116, 790, 194, 34),
        _fragment("免费", 670, 781, 93, 49),
    ]
    claimed = common + [_fragment("每日限购：0", 116, 790, 197, 34)]
    removed = [
        _fragment("冲榜商店", 416, 323, 370, 95),
        _fragment("VIP3特惠灵石礼包", 126, 888, 298, 39),
    ]
    assert yuanding_store_state(claimable, "冲榜商店 免费冲榜礼包 每日限购：1") == "claimable"
    assert yuanding_store_state(claimed, "冲榜商店 免费冲榜礼包 每日限购：0") == "claimed"
    assert yuanding_store_state(
        removed,
        "冲榜商店 VIP3特惠灵石礼包 活动内限购：5 适度娱乐，理性消费",
    ) == "claimed"
    assert yuanding_store_state(common, "免费冲榜礼包") == "loading"


def test_yuanding_page_state_is_ocr_gated_on_unknown_activity_pages() -> None:
    assert yuanding_page_state(34, [], "") == "world"
    assert yuanding_page_state(66, [], "日程 今天") == "schedule"
    assert yuanding_page_state(
        None,
        [_fragment("查看详情", 355, 1360, 193, 46)],
        "缘宠三生 活动时间：2026.08.08-2026.08.09",
    ) == "intro"
    assert yuanding_page_state(
        249,
        [_fragment("礼包", 772, 1449, 54, 95)],
        "缘宠三生 榜单 奖励 任务 礼包",
    ) == "main"
    assert yuanding_page_state(
        None,
        [_fragment("免费冲榜礼包", 125, 589, 221, 38)],
        "冲榜商店 免费冲榜礼包",
    ) == "store"
    assert yuanding_page_state(None, [], "其它活动") == "unknown"


def test_yuanding_capacity_plan_doubles_remaining_score_and_drops_one_tier() -> None:
    tiers = [
        {"rank_start": 17, "rank_end": 32, "guard_score": 900},
        {"rank_start": 33, "rank_end": 64, "guard_score": 600},
        {"rank_start": 65, "rank_end": 128, "guard_score": 300},
    ]
    plan = plan_yuanding_rank_capacity(
        current_rank=128,
        current_score=100,
        remaining_disciple_scores=[300, 100, 200],
        reward_tiers=tiers,
        realization_ratio=0.8,
    )

    assert plan.ordered_scores == (100, 200, 300)
    assert plan.remaining_own_score == 600
    assert plan.theoretical_capacity == 1300
    assert plan.conservative_capacity == 1060
    assert plan.achievable_rank_end == 32
    assert plan.target_rank_end == 64
    assert plan.target_guard_score == 600


def test_yuanding_marriages_always_consume_low_scores_first() -> None:
    assert cumulative_marriage_capacity([300, 100, 200]) == (200, 600, 1200)
    assert marriages_needed_for_score(
        current_score=100,
        target_score=650,
        ordered_disciple_scores=[300, 100, 200],
    ) == 2
    assert conservative_single_step_batch(current_rank=73, target_rank=64) == 1
    assert conservative_single_step_batch(current_rank=64, target_rank=64) == 0


def test_yuanding_partial_packet_scores_are_only_a_lower_bound() -> None:
    partial = observe_disciple_scores([30, 10, 20], expected_count=1132)
    assert partial.captured_count == 3
    assert partial.captured_score_sum == 60
    assert partial.complete is False

    complete = observe_disciple_scores([30, 10, 20], expected_count=3)
    assert complete.complete is True


def test_yuanding_observed_score_supports_an_explicit_event_multiplier() -> None:
    basic = 1_156_264 + 1_121_154
    observed = 2_505_159
    multiplier = infer_event_score_multiplier(
        basic_pair_score=basic,
        observed_score=observed,
    )
    assert multiplier is not None
    assert round(multiplier, 4) == 1.1
    assert cumulative_marriage_capacity(
        [1_156_264],
        opponent_score_ratio=1_121_154 / 1_156_264,
        event_score_multiplier=1.1,
    ) == (2_505_159,)
