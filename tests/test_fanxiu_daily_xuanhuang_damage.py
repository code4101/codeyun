from backend.core.fanxiu.data_annotation.tasks.daily_xuanhuang import (
    xuanhuang_stall_verdict,
    xuanhuang_verified_remaining,
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


def test_damage_growth_compares_compound_magnitudes():
    assert not xuanhuang_damage_grew(None, (1.2, "万"))
    assert not xuanhuang_damage_grew((5.0, "万京"), (1.0, "京"))
    assert xuanhuang_damage_grew((9.9, "垓"), (1.0, "垓垓"))
    assert xuanhuang_damage_grew((2.0, "亿"), (1.0, "万亿"))
    assert xuanhuang_damage_grew((9999.0, "万"), (1.0, "亿"))
    assert xuanhuang_damage_grew((55.6, "垓垓"), (56.1, "垓垓"))
    assert not xuanhuang_damage_grew((55.6, "垓垓"), (55.6, "垓垓"))
    assert not xuanhuang_damage_grew((55.6, "垓垓"), (12.0, "垓垓"))
    assert not xuanhuang_damage_grew((55.6, "垓垓"), (60.0, "核域"))
    assert not xuanhuang_damage_grew((55.6, "核域"), (60.0, "核域"))


def test_damage_growth_uses_exact_canonical_magnitude():
    assert not xuanhuang_damage_grew((1e100, ""), (1.0, "垓垓"))
    assert xuanhuang_damage_grew((1.0, "垓垓"), (1e100, ""))
    for bad in (float("nan"), float("inf"), -1.0):
        assert not xuanhuang_damage_grew((1.0, "万"), (bad, "万"))
        assert not xuanhuang_damage_grew((bad, "万"), (2.0, "万"))


def test_stall_counter_verdict_fails_closed():
    ok = {"complete": True, "counter_loaded": True}
    assert xuanhuang_verified_remaining({**ok, "remaining": 2}) == 2
    assert xuanhuang_verified_remaining({**ok, "remaining": True}) is None
    assert xuanhuang_verified_remaining({**ok, "remaining": -1}) is None
    assert xuanhuang_verified_remaining({"complete": False, "counter_loaded": True, "remaining": 2}) is None
    assert xuanhuang_verified_remaining({"complete": True, "remaining": 2}) is None
    assert xuanhuang_stall_verdict(2, 2) == "stalled"
    assert xuanhuang_stall_verdict(2, 1) == "settled"
    for before, after in ((None, 2), (2, None), (2, 3), (True, 1)):
        assert xuanhuang_stall_verdict(before, after) == "unverified"
