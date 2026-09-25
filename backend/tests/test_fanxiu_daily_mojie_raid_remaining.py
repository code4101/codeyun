from __future__ import annotations

from backend.core.fanxiu.data_annotation.tasks.mojie_raid import MojieRaidTaskMixin


def test_mojie_remaining_parser_anchors_value_after_business_label() -> None:
    runner = MojieRaidTaskMixin()

    assert runner._daily_mojie_raid_remaining_ocr_fallback(
        "灵力+1.2兆本周剩余进攻次数：0本周剩余鼓舞次数：15"
    ) == 0


def test_mojie_remaining_parser_supports_ocr_digit_variants() -> None:
    runner = MojieRaidTaskMixin()

    assert runner._daily_mojie_raid_remaining_ocr_fallback("本周剩余进攻次数：８") == 8
    assert runner._daily_mojie_raid_remaining_ocr_fallback("剩余进攻次数: B") == 8
    assert runner._daily_mojie_raid_remaining_ocr_fallback("剩余进攻次数: O") == 0
    assert runner._daily_mojie_raid_remaining_ocr_fallback("本周剩余鼓舞次数：15") is None
