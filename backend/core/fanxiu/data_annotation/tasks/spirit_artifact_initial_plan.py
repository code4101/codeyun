"""初始到预备的纯缺口计划；阶段、材料可消费性和来源观察由提供方负责。"""
from dataclasses import dataclass
from .spirit_artifact_preparation import (
    SpiritArtifactStageCandidate, SpiritArtifactPreparationGap,
    calculate_preparation_gap, spirit_artifact_priority,
)


@dataclass(frozen=True)
class SpiritArtifactInitialFacts:
    """输入权威初始分类；非红装备按0阶。None代表未调查，不能当数量0。

    stable_supply_units 是本轮已确认可取得的新红1阶单位总量，必须包含
    余额/限购/箱子选择约束；共享箱或余额不能跨部件重复预留。
    """
    ware_id: int
    part: int
    stage: str
    equipped_red_grade: int
    verified_material_grade_units: int | None = None
    available_raw_bodies: int | None = None
    stable_supply_units: int | None = None


@dataclass(frozen=True)
class SpiritArtifactInitialPlanEntry:
    assessment: SpiritArtifactStageCandidate
    gap: SpiritArtifactPreparationGap | None


def plan_spirit_artifact_initial_parts(facts) -> tuple[SpiritArtifactInitialPlanEntry, ...]:
    """按阶段/部位排序输出本轮全部初始部件；不执行、不重新定义阶段。

    ready仅表示能取得并装配红色1阶本体，不证明GUI路径验收。已核实材料足够时无需
    再调查外部来源；材料不足且来源未知必须needs_analysis。每件结束重算。
    """
    result, seen = [], set()
    for item in facts:
        key = item.ware_id, item.part
        if key in seen or item.stage != '初始' or item.equipped_red_grade != 0:
            raise ValueError('要求唯一部件、权威初始阶段及红色0阶事实')
        seen.add(key)
        gap = None
        if item.verified_material_grade_units is None or item.available_raw_bodies is None:
            readiness, reason = 'needs_analysis', '现有红本体及可消费材料尚未完整核实'
        else:
            gap = calculate_preparation_gap(equipped_red_grade=item.equipped_red_grade,
                verified_material_grade_units=item.verified_material_grade_units,
                available_raw_bodies=item.available_raw_bodies, reset_required=False, target_grade=1)
            if gap.red_grade_units_missing == 0:
                readiness, reason = 'ready', ''
            elif item.stable_supply_units is None:
                readiness, reason = 'needs_analysis', '取得红色本体的稳定来源尚未核实'
            elif type(item.stable_supply_units) is not int or item.stable_supply_units < 0:
                raise ValueError('稳定供应必须为非负整数或未调查None')
            elif item.stable_supply_units >= gap.red_grade_units_missing:
                readiness, reason = 'ready', ''
            else:
                readiness, reason = 'blocked', f'本轮核实供应后仍缺{gap.red_grade_units_missing-item.stable_supply_units}个红1阶单位'
        result.append(SpiritArtifactInitialPlanEntry(
            SpiritArtifactStageCandidate('初始', *key, readiness, reason), gap))
    return tuple(sorted(result, key=lambda r: spirit_artifact_priority(
        '初始', r.assessment.ware_id, r.assessment.part)))
