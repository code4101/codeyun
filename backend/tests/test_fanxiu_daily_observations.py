"""Stable OCR samples; no simulated scene or game execution."""
import pytest

from backend.core.fanxiu.data_annotation.tasks.daily_observations import (
    parse_daily_boss_cd_seconds,
    parse_daily_boss_cd_seconds_from_six_digits,
    parse_daily_boss_hp_percent,
    parse_daily_boss_reward_remaining,
    parse_first_int,
    parse_xianfu_skill_cd_seconds,
    parse_xianfu_visit_cd_seconds,
)


@pytest.mark.parametrize("parser,text,expected", [
    (parse_xianfu_visit_cd_seconds, "０１：０２：０３", 3723),
    (parse_xianfu_visit_cd_seconds, "1小时2分3秒", 3723),
    (parse_xianfu_visit_cd_seconds, "免费", 0),
    (parse_xianfu_visit_cd_seconds, "未知", None),
    (parse_xianfu_skill_cd_seconds, "免费领悟", 0),
    (parse_daily_boss_cd_seconds, "30:00", 1800),
    (parse_daily_boss_cd_seconds, "30:01", None),
    (parse_daily_boss_cd_seconds_from_six_digits, "００２９５９", 1799),
    (parse_daily_boss_cd_seconds_from_six_digits, "006000", None),
    (parse_daily_boss_reward_remaining, "剩余奖励次数：０丨３", 0),
    (parse_daily_boss_reward_remaining, "次数未知", None),
    (parse_daily_boss_hp_percent, "101% 85% 20%", 20),
    (parse_first_int, "剩余１２次", 12),
])
def test_observation_values_keep_zero_distinct_from_unknown(parser, text, expected):
    assert parser(text) == expected
