"""实机祈愿页 OCR 文本的稳定解析契约。"""
import pytest

from backend.core.fanxiu.data_annotation.tasks.prayer_task_progress import parse_prayer_round_progress


def test_header_reward_gap_is_separate_from_maximum_task_score():
    r = parse_prayer_round_progress("奖励轮次：4/5", "再获得18326积分可获得",
        "累计获得32000仙花积分\n80\n300\n26674/32000\n获取积分\n累计获得38000仙花积分\n80\n300",
        maximum_task_score=160000)
    assert (r['current_round'], r['total_rounds'], r['current_score']) == (4, 5, 26674)
    assert (r['round_target_score'], r['round_score_gap']) == (160000, 133326)
    assert r['header_reward_score_gap'] == 18326


def test_reward_quantities_cannot_be_concatenated_into_score():
    with pytest.raises(ValueError, match="进度条数字"):
        parse_prayer_round_progress("奖励轮次：4/5", "再获得18326积分可获得",
            "累计获得32000仙花积分8030026674/32000", maximum_task_score=160000)


def test_claimed_low_tier_does_not_override_current_score_and_units_are_parsed():
    r = parse_prayer_round_progress("奖励轮次：4/5", "再获得18326积分可获得",
        "26674/16万\n获取积分\n累计获得1000仙花积分\n1000/1000\n已领取", maximum_task_score=160000)
    assert (r['current_score'], r['round_target_score'], r['round_score_gap']) == (26674, 160000, 133326)


def test_header_can_be_missing_but_claimed_only_rows_cannot_prove_current_score():
    r = parse_prayer_round_progress("奖励轮次：4/5", "", "26674/32000", maximum_task_score=160000)
    assert r['header_reward_score_gap'] is None
    assert r['round_score_gap'] == 133326
    with pytest.raises(ValueError, match="观察不完整"):
        parse_prayer_round_progress("奖励轮次：4/5", "", "1000/1000\n已领取", maximum_task_score=160000)
