"""洗灵前置培养的纯计划；不读取游戏、不选择来源、不授权消耗。

阶段分类由事实投影负责。这里接收已判定阶段，避免另一套分类规则。
材料折算须由提供方证明；不能把未确认可消耗的库存直接相加。
"""

from dataclasses import dataclass
from typing import Literal, Sequence


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
    target_grade: int = 6,
) -> SpiritArtifactPreparationGap:
    """计算指定目标阶数的新增红色1阶本体数，不替调用方兑换。

    初始到预备传 target_grade=1；默认6仅保留显式升六阶方案，非阶段门槛。

    无红色装备传0；材料单位含库存原始本体，不含当前装备，不能重复计数。
    错升时当前装备仅在已确认可吃回时传其阶数；尚未证明则不要调用。
    available_raw_bodies 为上述材料中已确认未突破红色1阶的数量。
    重置或0阶装配至少需要一个原始本体；新增的该本体同时贡献1阶，
    因而总缺口取两种约束的较大值，而非相加。
    """
    if type(target_grade) is not int or target_grade < 1:
        raise ValueError("目标阶数必须为正整数")
    values = (equipped_red_grade, verified_material_grade_units, available_raw_bodies)
    if any(type(value) is not int or value < 0 for value in values):
        raise ValueError("阶数、材料单位及原始本体数量必须为非负整数")
    if available_raw_bodies > verified_material_grade_units:
        raise ValueError("原始本体必须已计入材料单位")
    needs_raw = reset_required or equipped_red_grade == 0
    raw_missing = int(needs_raw and available_raw_bodies == 0)
    missing = max(raw_missing, target_grade - equipped_red_grade - verified_material_grade_units, 0)
    return SpiritArtifactPreparationGap(raw_missing, missing)


@dataclass(frozen=True)
class SpiritArtifactStageCandidate:
    """本轮事实：阶段来自权威分类；来源/条件分析产生 readiness 与阻塞原因。

    blocked 仅说明本轮当前条件不足，不代表永久完成。资源或本体变化后，
    调用方须重新分析，不能把上轮 blocked 当作新的观察事实。
    """
    stage: str
    ware_id: int
    part: int
    readiness: Literal['ready', 'blocked', 'needs_analysis'] = 'needs_analysis'
    reason: str = ''

    def __post_init__(self):
        if (self.stage not in (*STAGE_ORDER, '待识别') or type(self.ware_id) is not int
                or self.ware_id <= 0 or self.part not in range(1, 7)
                or self.readiness not in ('ready', 'blocked', 'needs_analysis')):
            raise ValueError('阶段候选身份或分析状态无效')
        if self.readiness == 'blocked' and not self.reason.strip():
            raise ValueError('跳过部件必须说明本轮阻塞原因')
        if self.stage == '待识别' and self.readiness == 'ready':
            raise ValueError('阶段未知不能直接执行')


@dataclass(frozen=True)
class SpiritArtifactStagePlan:
    action: Literal['execute', 'analyze', 'round_exhausted']
    candidate: SpiritArtifactStageCandidate | None
    skipped: tuple[SpiritArtifactStageCandidate, ...]


def plan_spirit_artifact_stage_round(
    candidates: Sequence[SpiritArtifactStageCandidate],
) -> SpiritArtifactStagePlan:
    """纯全局下一步，不调度或调用单部件动作，也不声称全局培养已经完成。

    输入是当前待处理部件的本轮分析全集。阶段→全部5/6→全部1/2/3/4；
    当前阶段有可处理项就不会进入后阶段。条件不足必须携带原因，可跳过后
    继续同阶段；同阶段仅剩 blocked 才推进后阶段。未分析项先分析，不把
    未知当作资源不足。未知阶段须先查明，避免跳过可能更早的阶段。
    每次单部件阶段变化后用新事实重新规划，不持有沿同一部件升级的游标。
    单部件换装/吃回材料的必要闭环不拆分；它结束后必须回到本规划层。
    返回 round_exhausted 只表示本轮无可执行项（含空待办），不是全部完成。
    研发带教指定部件由调用方明确选择，不修改常规排序，也不在此启用自动化。
    """
    identities = [(item.ware_id, item.part) for item in candidates]
    if len(set(identities)) != len(identities):
        raise ValueError('全局计划不能重复包含同一部件')
    ordered = sorted(candidates, key=lambda item: (
        (-1, int(item.part < 5), item.ware_id, item.part) if item.stage == '待识别'
        else spirit_artifact_priority(item.stage, item.ware_id, item.part)))
    skipped = []
    for candidate in ordered:
        if candidate.readiness == 'blocked':
            skipped.append(candidate)
            continue
        return SpiritArtifactStagePlan(
            'execute' if candidate.readiness == 'ready' else 'analyze', candidate, tuple(skipped))
    return SpiritArtifactStagePlan('round_exhausted', None, tuple(skipped))
