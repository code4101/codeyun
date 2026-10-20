from backend.core.fanxiu.data_annotation.tasks.daily_xuanhuang import (
    xuanhuang_damage_grew,
    xuanhuang_damage_reading,
)


def test_damage_reading_separates_unreadable_zero_and_value():
    # Real 2026-10-20 #419 OCR: a single "0" is dropped, the label remains.
    assert xuanhuang_damage_reading("伤害") == ("zero", 0.0, "")
    assert xuanhuang_damage_reading("伤害 0") == ("zero", 0.0, "")
    assert xuanhuang_damage_reading("") is None
    assert xuanhuang_damage_reading("队伍")[0] == "unreadable"
    assert xuanhuang_damage_reading("伤害55.6核域") == ("value", 55.6, "核域")


def test_damage_growth_requires_strict_increase():
    assert xuanhuang_damage_grew(None, (1.2, "万"))
    assert xuanhuang_damage_grew((55.6, "核域"), (56.1, "核域"))
    assert not xuanhuang_damage_grew((55.6, "核域"), (55.6, "核域"))
    assert not xuanhuang_damage_grew((55.6, "核域"), (12.0, "核域"))
    assert xuanhuang_damage_grew((9999.0, "万"), (1.0, "亿"))
    assert not xuanhuang_damage_grew((1.0, "亿"), (9999.0, "万"))
    # A garbled unit cannot prove growth across units.
    assert not xuanhuang_damage_grew((55.6, "垓垓"), (60.0, "核域"))
