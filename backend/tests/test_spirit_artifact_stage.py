import pytest

from backend.core.fanxiu.catalog.inventory_models import classify_spirit_artifact_stage


SWORD = ["ATTACK", "CRI_VALUE", "CRI_DAMAGE_FIX"]
FOUR_A = ["ATTACK", "CORE_1", "CORE_2", "CORE_3"]


def stage(codes=SWORD, *, missing=None, extra_names=(), is_break=True, rank=10, partial=False):
    effects = [{"code": code, "affix": "" if code == missing else "满"} for code in codes]
    effects += [{"code": "B" + str(i), "affix": "", "name": name} for i, name in enumerate(extra_names)]
    effects += [{"code": "B" + str(i), "affix": ""} for i in range(6-len(effects))]
    return classify_spirit_artifact_stage(rank=rank, base_id=14000406, is_break=is_break,
                                         effects=effects[:-1] if partial else effects, a_codes=codes)


def test_three_full_a_with_nonfull_b_is_breakthrough():
    assert stage() == "突破"
    assert stage(FOUR_A) == "突破"
    assert stage(FOUR_A, missing="CORE_3") == "错升"


@pytest.mark.parametrize("rank", [1, 5, 6, 10])
def test_wrong_upgrade_checks_a_identity_independent_of_rank(rank):
    assert stage(missing="ATTACK", rank=rank) == "错升"


def test_unknown_stays_unknown_and_unbroken_stays_prepared():
    assert stage(codes=[]) == "待识别"
    assert stage(partial=True) == "待识别"
    assert stage(is_break=None) == "待识别"
    assert stage(is_break=False) == "预备"
    assert stage(is_break=False, rank=5) == "初始"


def test_s_tiers_still_require_all_a():
    assert stage(extra_names=["灵器无双"]) == "无双"
    assert stage(extra_names=["灵器无双", "混沌道威"]) == "道威"
    assert stage(missing="ATTACK", extra_names=["灵器无双", "混沌道威"]) == "错升"


def test_four_full_non_a_cannot_substitute_missing_a():
    effects = [{"code": c, "affix": "满"} for c in ["CRI_VALUE", "CRI_DAMAGE_FIX", "MAXMP", "MAXHP"]]
    effects += [{"code": "DEFENSE", "affix": ""}, {"code": "ATTACK", "affix": ""}]
    assert classify_spirit_artifact_stage(rank=10, base_id=14000406, is_break=True,
                                         effects=effects, a_codes=SWORD) == "错升"


def test_missing_identity_is_unknown_but_explicit_special_type_is_known():
    effects = [{"code": c, "affix": "满"} for c in SWORD]
    effects += [{"code": "MAXMP", "affix": ""}, {"code": "MAXHP", "affix": ""}, {"affix": "", "name": "灵器无双"}]
    args = dict(rank=10, base_id=14000406, is_break=True, effects=effects, a_codes=SWORD)
    assert classify_spirit_artifact_stage(**args) == "待识别"
    effects[-1]["type"] = 3
    assert classify_spirit_artifact_stage(**args) == "无双"
