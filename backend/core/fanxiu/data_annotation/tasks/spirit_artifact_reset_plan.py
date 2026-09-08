"""稳定来源错升重置的纯计划；不读游戏、不采购、不分配整批未来库存。"""
from dataclasses import dataclass
from typing import Literal, Mapping, Sequence, Any
from .spirit_artifact_preparation import (
    SpiritArtifactStageCandidate, plan_spirit_artifact_stage_round, spirit_artifact_priority,
)
from ...catalog.inventory_models import FanxiuSpiritArtifactHallSnapshot
from ...catalog.spirit_artifact_wash_rules import load_spirit_artifact_wash_rules


@dataclass(frozen=True)
class SpiritArtifactResetSupply:
    """来源提供方已核实的红色1阶本体供应；引用为goods ID或自选箱ID。

    仙市须传实际余额货币ID与单价；储物袋available是箱子现有数量，
    reference_id定位箱子，base_id定位其中确定可选奖励。这里只筛选可用性，
    购买或自选仍须对应公共动作在当前界面复验，不能凭此结构直接发确认。
    """
    kind: Literal['market', 'storage_bag']
    base_id: int
    reference_id: int
    available: int
    cost_item_id: int = 0
    unit_cost: int = 0

    def __post_init__(self):
        if (self.kind not in ('market','storage_bag') or self.base_id <= 0 or self.reference_id <= 0
                or type(self.available) is not int or self.available < 0 or self.unit_cost < 0
                or (self.kind == 'market' and (self.cost_item_id <= 0 or self.unit_cost <= 0))):
            raise ValueError('稳定供应必须有明确来源、红本体ID、数量与费用')


@dataclass(frozen=True)
class SpiritArtifactResetPlanEntry:
    assessment: SpiritArtifactStageCandidate
    previous_item_id: str
    base_id: int
    previous_grade: int
    raw_item_ids: tuple[str, ...] = ()
    supplies: tuple[SpiritArtifactResetSupply, ...] = ()
    action: str = 'blocked'  # reset_owned / obtain_raw / choose_source / blocked / analyze


@dataclass(frozen=True)
class SpiritArtifactResetPlan:
    entries: tuple[SpiritArtifactResetPlanEntry, ...]
    next_entry: SpiritArtifactResetPlanEntry | None
    status: str  # execute / analyze / round_exhausted
    ignored: tuple[tuple[int, int, str], ...]


def plan_spirit_artifact_stable_resets(
    hall: Mapping[str, Any], inventory: Mapping[str, Any], *,
    supplies: Sequence[SpiritArtifactResetSupply], balances: Mapping[int, int],
    source_order: tuple[str, ...] = (),
) -> SpiritArtifactResetPlan:
    """重新分类当前全馆，只规划错升；已手动修复的部件不会沿旧清单再重置。

    要求完整全馆/库存事实及同进程。supplies是已核实来源的完整枚举；空列表
    表示本轮已查无仙市/储物袋供应，不能把未调查冒充查无。余额与库存须由
    调用方作为同轮事实提供。每完成一件必须重读并重规划，不预留或重复消费
    同一个自选箱/余额。优先已有raw；两种外部来源都可用时，只有显式source_order
    才选一种，否则返回待选择。raw数量多可列出，但当前单raw执行器的多材料
    限制由执行器拒绝，不在此伪造两件库存或代替其消费授权。
    未识别阶段先分析，无供应明确阻塞并跳过；round_exhausted不是全量完成。
    """
    if (hall.get('runtime_complete') is not True or inventory.get('complete') is not True
            or not inventory.get('pid') or not inventory.get('process_start_ticks')):
        raise ValueError('重置规划要求完整已装配快照与已核实库存')
    if source_order and (len(source_order) != 2 or set(source_order) != {'market','storage_bag'}):
        raise ValueError('source_order须明确两种外部来源的优先顺序')
    identity = (inventory['pid'], inventory['process_start_ticks'])
    rules = load_spirit_artifact_wash_rules()
    bag_sources = [source for source in supplies if source.kind == 'storage_bag']
    if bag_sources:
        from ...catalog.item import load_fanxiu_item_runtime_index
        from ...instrumentation.spirit_artifact_storage_bag import resolve_stable_spirit_artifact_rewards
        cards = load_fanxiu_item_runtime_index(rebuild_missing=False)['cards_by_id']
        for source in bag_sources:
            card = cards.get(str(source.reference_id), {})
            if source.base_id not in resolve_stable_spirit_artifact_rewards(card, rules['items_by_base_id']):
                raise ValueError('储物袋来源不是明确自选箱中的精确红色本体奖励')
    for supply in supplies:
        cfg = rules['items_by_base_id'].get(supply.base_id)
        if cfg is None or cfg['quality'] != 6:
            raise ValueError('来源奖励不是已核实的红色本体配置')
    stock = inventory.get('items')
    if not isinstance(stock, list) or len({r.get('item_id') for r in stock}) != len(stock):
        raise ValueError('完整库存实例缺失或重复')
    rows = FanxiuSpiritArtifactHallSnapshot.model_validate(dict(hall))
    entries, ignored = [], []
    for artifact in rows.artifacts:
        for row in artifact.rows:
            stage = row.stage
            ware, part = row.runtime_ware_id, row.runtime_part
            if stage not in ('错升','待识别'):
                ignored.append((ware, part, stage))
                continue
            observation = row.runtime_observation or hall.get('runtime_debug', {})
            if (observation.get('pid'), observation.get('process_start_ticks')) != identity:
                raise ValueError('目标部件与库存进程不一致，须重新观察')
            def entry(action, readiness, reason='', raw=(), choices=()):
                return SpiritArtifactResetPlanEntry(
                    SpiritArtifactStageCandidate(stage, ware, part, readiness, reason),
                    row.runtime_item_id, row.runtime_base_id, row.rank, raw, choices, action)
            if stage == '待识别':
                entries.append(entry('analyze','needs_analysis','阶段事实不足'))
                continue
            matching = [r for r in stock if r.get('base_id') == row.runtime_base_id]
            old = [r for r in matching if r.get('item_id') == row.runtime_item_id]
            if (len(old)!=1 or old[0].get('is_break') is not True or old[0].get('grade') != row.rank):
                raise ValueError('错升本体与当前库存事实不一致，不能规划重置')
            raw = tuple(sorted(str(r['item_id']) for r in matching
                if r.get('item_id') != row.runtime_item_id and r.get('quality') == 6
                and r.get('grade') == 1 and r.get('is_break') is False and r.get('quantity') == 1))
            if raw:
                entries.append(entry('reset_owned','ready',raw=raw))
                continue
            available, reasons = [], []
            for source in supplies:
                if source.base_id != row.runtime_base_id:
                    continue
                if source.available <= 0:
                    reasons.append(source.kind + '数量不足')
                elif source.kind == 'market' and balances.get(source.cost_item_id, -1) < source.unit_cost:
                    reasons.append('仙市余额不足或未核实')
                else:
                    available.append(source)
            kinds = {source.kind for source in available}
            if source_order:
                available.sort(key=lambda source: (source_order.index(source.kind), source.reference_id))
                available = available[:1]
            if available:
                choose = len(kinds)>1 and not source_order
                entries.append(entry('choose_source' if choose else 'obtain_raw',
                    'needs_analysis' if choose else 'ready', '多种稳定来源待选择' if choose else '', choices=tuple(available)))
            else:
                entries.append(entry('blocked','blocked','；'.join(reasons) or '无已有红raw及仙市/储物袋稳定供应，留待手工'))
    entries.sort(key=lambda e: (-1, 0, e.assessment.ware_id,e.assessment.part)
                 if e.assessment.stage=='待识别' else spirit_artifact_priority(
                     e.assessment.stage,e.assessment.ware_id,e.assessment.part))
    phase = plan_spirit_artifact_stage_round([entry.assessment for entry in entries])
    selected = next((entry for entry in entries if entry.assessment == phase.candidate), None)
    return SpiritArtifactResetPlan(tuple(entries), selected, phase.action, tuple(ignored))
