import pytest

from backend.core.fanxiu.catalog.inventory_models import classify_spirit_artifact_stage


SWORD = ["ATTACK", "CRI_VALUE", "CRI_DAMAGE_FIX"]
FOUR_A = ["ATTACK", "CORE_1", "CORE_2", "CORE_3"]


@pytest.mark.parametrize('is_break', [True, False, None])
def test_nonred_body_is_initial_even_with_break_flag(is_break):
    # 无红色的培养进度是0；低品质自身突破不属于红色错升。
    assert classify_spirit_artifact_stage(
        rank=1, base_id=14000905, is_break=is_break,
        effects=[{'code': 'ATTACK', 'affix': ''}], a_codes=FOUR_A,
    ) == '初始'


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
    assert stage(is_break=False, rank=5) == "预备"


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


def test_only_explicit_empty_slot_is_initial_zero():
    from backend.core.fanxiu.catalog.inventory_models import FanxiuSpiritArtifactPartRow
    assert FanxiuSpiritArtifactPartRow(runtime_empty_slot=True).stage == "初始"
    assert FanxiuSpiritArtifactPartRow().stage == "待识别"
    assert FanxiuSpiritArtifactPartRow(runtime_empty_slot=True, runtime_item_id="conflict").stage == "待识别"


@pytest.mark.parametrize("rank,base_id,values", [
    (1, 14000906, [("ATTACK", 7556, 10000), ("SKILL_ATTACK_RECOVER_FIX", 6572, 10000),
                  ("BLOCK_VALUE", 22347, 30000), ("GONGFA_PLUS_FIX", 42639, 60000)]),
    (1, 14001006, [("ATTACK", 7029, 10000), ("SKILL_ATTACK_RECOVER_FIX", 6409, 10000),
                  ("BLOCK_VALUE", 22529, 30000), ("GONGFA_PLUS_FIX", 38439, 60000)]),
    (4, 14001906, [("MAXHP", 983280, 1200000), ("ATTACK", 7538, 10000),
                  ("XIANYU_PLUS_FIX", 48708, 60000), ("ALL_SKILL_REDUCE_FIX", 7260, 10000),
                  ("PET_PLUS_FIX", 51474, 60000)]),
])
def test_observed_low_a_proves_wrong_upgrade_before_six_slots(rank, base_id, values):
    # 2026-09-08 的 2-3、2-4、4-1 真实快照精简；非六槽不是未知培养状态。
    effects = [dict(code=c, value=v, normal_max=m, affix="") for c,v,m in values]
    required = [c for c,_,_ in values if c != "MAXHP"]
    assert classify_spirit_artifact_stage(rank=rank, base_id=base_id, is_break=True,
                                         effects=effects, a_codes=required) == "错升"


def test_partial_unknown_or_duplicate_a_does_not_prove_wrong_upgrade():
    args = dict(rank=1, base_id=14000906, is_break=True, a_codes=["ATTACK", "CORE"])
    assert classify_spirit_artifact_stage(**args, effects=[{"code":"ATTACK"}]) == "待识别"
    assert classify_spirit_artifact_stage(**args, effects=[{"code":"ATTACK", "affix":"满"}]) == "待识别"
    assert classify_spirit_artifact_stage(**args, effects=[{"code":"ATTACK", "affix":"满"},
                                                           {"code":"ATTACK", "affix":""}]) == "待识别"


@pytest.mark.parametrize("rank,expected", [(0, "初始"), (1, "预备"), (5, "预备"), (6, "预备")])
def test_new_red_grade_threshold(rank, expected):
    assert stage(is_break=False, rank=rank) == expected


def test_five_slot_breakthrough_requires_complete_four_a():
    effects = [{"code": code, "affix": "满"} for code in FOUR_A] + [{"code": "MAXHP", "affix": ""}]
    assert classify_spirit_artifact_stage(rank=1, base_id=14000406, is_break=True,
        effects=effects, a_codes=FOUR_A) == "突破"
    assert classify_spirit_artifact_stage(rank=6, base_id=14000406, is_break=True,
        effects=effects, a_codes=FOUR_A) == "待识别"
