import pytest

from backend.core.fanxiu.instrumentation.cultivation import parse_cultivation_reward_targets
from backend.core.fanxiu.instrumentation.runtime_memory import FanxiuRuntimeMemoryError


def test_bundle_preserves_two_fashion_components():
    rows = parse_cultivation_reward_targets(
        "IsGetFashionMax|1136_19030117_18011060_1;IsGetFashionMax|2101_19030117_18012059_1",
        reward_item_id=19030117,
    )
    assert [(r["kind"], r["target_id"], r["dimension"], r["component_item_id"]) for r in rows] == [
        ("fashion", 1136, "level", 18011060), ("fashion", 2101, "level", 18012059)]
    assert all("quantity" not in r for r in rows)


@pytest.mark.parametrize("condition,item_id,kind,dimension", [
    ("IsGetPetMax|106_8010107_1", 8010107, "pet", "level"),
    ("IsGetFashionMax|4085_18014045_1", 18014045, "fashion", "level"),
    ("IsGetTalismanGradeMax|106_8010107_1", 8010107, "talisman", "stage"),
    ("IsGetGongFaMax|106_8010107_1", 8010107, "gongfa", "jie"),
])
def test_single_target_dimensions(condition, item_id, kind, dimension):
    target, = parse_cultivation_reward_targets(condition, reward_item_id=item_id)
    assert (target["kind"], target["dimension"]) == (kind, dimension)


@pytest.mark.parametrize("condition", ["", "Unsupported|1_2_1", "IsGetPetMax|106_3_1", "IsGetPetMax|106_2_1;IsGetPetMax|106_2_1"])
def test_invalid_identity_fails_closed(condition):
    with pytest.raises(FanxiuRuntimeMemoryError):
        parse_cultivation_reward_targets(condition, reward_item_id=2)
