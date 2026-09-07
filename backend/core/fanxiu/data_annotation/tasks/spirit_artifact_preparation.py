"""洗灵前置培养的纯计划；不读取游戏、不选择来源、不授权消耗。

阶段分类由事实投影负责。这里接收已判定阶段，避免另一套分类规则。
材料折算须由提供方证明；不能把未确认可消耗的库存直接相加。
"""

from dataclasses import dataclass


STAGE_ORDER = ("错升", "初始", "预备", "突破", "无双", "道威", "巅峰")


def spirit_artifact_priority(stage: str, ware_id: int, part: int) -> tuple[int, int, int, int]:
    """排序键：先阶段，再所有灵器5/6部位，最后所有灵器1～4部位。"""
    if stage not in STAGE_ORDER or ware_id < 1 or part not in range(1, 7):
        raise ValueError("需要已识别阶段、正灵器编号和1～6部位")
    return STAGE_ORDER.index(stage), int(part < 5), ware_id, part


@dataclass(frozen=True)
class SpiritArtifactPreparationGap:
    raw_bodies_missing: int
    red_grade_units_missing: int


def calculate_preparation_gap(
    *,
    equipped_red_grade: int,
    verified_material_grade_units: int,
    available_raw_bodies: int,
    reset_required: bool,
) -> SpiritArtifactPreparationGap:
    """计算达到6阶的最少新增红色1阶本体数，不替调用方兑换。

    无红色装备传0；材料单位含库存原始本体，不含当前装备，不能重复计数。
    错升时当前装备仅在已确认可吃回时传其阶数；尚未证明则不要调用。
    available_raw_bodies 为上述材料中已确认未突破红色1阶的数量。
    重置或0阶装配至少需要一个原始本体；新增的该本体同时贡献1阶，
    因而总缺口取两种约束的较大值，而非相加。
    """
    values = (equipped_red_grade, verified_material_grade_units, available_raw_bodies)
    if any(type(value) is not int or value < 0 for value in values):
        raise ValueError("阶数、材料单位及原始本体数量必须为非负整数")
    if available_raw_bodies > verified_material_grade_units:
        raise ValueError("原始本体必须已计入材料单位")
    needs_raw = reset_required or equipped_red_grade == 0
    raw_missing = int(needs_raw and available_raw_bodies == 0)
    missing = max(raw_missing, 6 - equipped_red_grade - verified_material_grade_units, 0)
    return SpiritArtifactPreparationGap(raw_missing, missing)
