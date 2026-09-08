"""均衡补给纯规划：先分配，后按目标批量执行；不访问游戏。

调用方按仙市、储物袋顺序提交来源，每次将 projected_levels 传入下一来源。
levels 必须已计入当前装备与库存待用材料的可兑现提升；阶数和境数分别规划。
此模块不把一件道具擅自折算成一级：每种来源显式提供已核实的单位收益。
全量升阶/升境必须重新扫描全部装备和库存，不能拿本模块的兑换列表作范围。
"""
from dataclasses import dataclass
from heapq import heapify, heappop, heappush
from typing import Mapping, Sequence


@dataclass(frozen=True)
class BalancedSupplyChoice:
    target: tuple[int, int]
    reward_id: int
    level_gain: int = 1
    available: int | None = None


@dataclass(frozen=True)
class BalancedSupplyBatch:
    target: tuple[int, int]
    reward_id: int
    quantity: int


@dataclass(frozen=True)
class BalancedSupplyPlan:
    batches: tuple[BalancedSupplyBatch, ...]
    projected_levels: dict[tuple[int, int], int]
    unallocated: int


def plan_balanced_spirit_artifact_supply(
    levels: Mapping[tuple[int, int], int],
    choices: Sequence[BalancedSupplyChoice],
    quantity: int,
) -> BalancedSupplyPlan:
    """优先最低预计等级，同值按灵器/部位编号；输出每目标一次的批量数量。

quantity 为同一来源可分配的单位数（市场预算应先按单价换算）。
available 为该目标本轮上限，None 表示该来源已确认无目标数量限制。
不能用未知级别零值兜底，也不能在不同单价或不同来源间暗定兑换价值。
输入不变；同一目标重复奖励拒绝，避免随目录顺序选取另一种资源。
"""
    if type(quantity) is not int or quantity < 0:
        raise ValueError('分配数量必须为非负整数')
    projected = dict(levels)
    for target, level in projected.items():
        if (not isinstance(target, tuple) or len(target) != 2
                or any(type(n) is not int for n in target)
                or target[0] <= 0 or target[1] not in range(1, 7)
                or type(level) is not int or level < 0):
            raise ValueError('必须提供明确部件及非负当前预计等级')
    by_target = {}
    for choice in choices:
        if (choice.target not in projected or choice.target in by_target
                or type(choice.reward_id) is not int or choice.reward_id <= 0
                or type(choice.level_gain) is not int or choice.level_gain <= 0
                or choice.available is not None
                and (type(choice.available) is not int or choice.available < 0)):
            raise ValueError('来源目标、收益或可用数量不明确/重复')
        by_target[choice.target] = choice
    counts = dict.fromkeys(by_target, 0)
    heap = [(projected[t], t) for t, c in by_target.items() if c.available != 0]
    heapify(heap)
    remaining = quantity
    while remaining and heap:
        level, target = heappop(heap)
        choice = by_target[target]
        counts[target] += 1
        projected[target] = level + choice.level_gain
        remaining -= 1
        if choice.available is None or counts[target] < choice.available:
            heappush(heap, (projected[target], target))
    batches = tuple(BalancedSupplyBatch(t, by_target[t].reward_id, counts[t])
                    for t in sorted(counts) if counts[t])
    return BalancedSupplyPlan(batches, projected, remaining)


def plan_spirit_artifact_supply_round(*, grade_levels, realm_levels, market_targets,
                                     currency: int, boxes, cards, progression):
    """仙市先、储物袋后；按正式奖励/消耗关系选择阶或境，不按箱名推断。

    levels 为已计入库存材料的预计等级；本函数只负责本轮新增资源分配。
    boxes 按业务库存目录顺序传入，字段为 base_id/quantity。同一箱里的奖励
    必须属于同一维度、每箱确定一件且无条件；超出支持范围明确拒绝规划。
    输出 sources 每个目标只有一个数量记录。执行器逐批重新核实实时库存、
    费用和到账，未决消费不得靠重算计划重复执行。
    """
    grade, realm = dict(grade_levels), dict(realm_levels)
    sources = []
    targets = tuple(market_targets)
    if type(currency) is not int or currency < 0:
        raise ValueError('市场余额未核实')
    if targets:
        costs = {(t.cost_item_id, t.unit_price) for t in targets}
        if len(costs) != 1:
            raise ValueError('不同费用不能隐式比较兑换价值')
        cost_item, price = next(iter(costs))
        if price <= 0:
            raise ValueError('市场单价必须为正')
        choices = []
        for t in targets:
            cap = max((level for base, level in progression['grade'] if base == t.base_id), default=0)
            choices.append(BalancedSupplyChoice((t.ware_id, t.part), t.base_id,
                                                available=max(0, cap - grade[(t.ware_id, t.part)])))
        result = plan_balanced_spirit_artifact_supply(grade, choices, currency // price)
        grade = result.projected_levels
        sources.append(dict(kind='market', cost_item_id=cost_item, unit_price=price,
                            plan=result))
    reward_map = {}
    for base, cfg in progression['items'].items():
        if cfg.get('quality') != 6:
            continue
        target = (cfg['type'], cfg['parts'])
        cap = max((level for b, level in progression['grade'] if b == base), default=0)
        reward_map[base] = ('grade', target, cap)
        if cfg.get('canUltra') != 1:
            continue
        costs = [(level, cost) for (b, level), cost in progression['realm'].items()
                 if b == base and cost is not None]
        ids = {cost[0] for _, cost in costs}
        if len(ids) == 1 and all(cost[1] == 1 for _, cost in costs):
            reward = next(iter(ids))
            mapped = ('realm', target, max(level for level, _ in costs))
            if reward in reward_map and reward_map[reward] != mapped:
                raise ValueError('同一材料存在多种升级用途，须明确分配策略')
            reward_map[reward] = mapped
    for box in boxes:
        if type(box['quantity']) is not int or box['quantity'] < 0:
            raise ValueError('自选箱数量未核实')
        if box['quantity'] == 0:
            continue
        card = cards.get(str(box['base_id']), {})
        if (card.get('type'), card.get('sub_type')) != (21, 21):
            raise ValueError('非明确自选箱不能纳入批量分配')
        rewards = card.get('optional_gift_rewards') or []
        if not rewards or any(r.get('count') != 1 or r.get('show_condition')
                              or r['id'] not in reward_map for r in rewards):
            raise ValueError('自选奖励收益不明确')
        dimensions = {reward_map[r['id']][0] for r in rewards}
        if len(dimensions) != 1:
            raise ValueError('同箱阶境混合奖励尚未定义资源价值')
        dimension = dimensions.pop()
        levels = grade if dimension == 'grade' else realm
        choices = []
        for reward in rewards:
            _, target, cap = reward_map[reward['id']]
            choices.append(BalancedSupplyChoice(target, reward['id'],
                                                available=max(0, cap - levels[target])))
        result = plan_balanced_spirit_artifact_supply(levels, choices, box['quantity'])
        if dimension == 'grade':
            grade = result.projected_levels
        else:
            realm = result.projected_levels
        sources.append(dict(kind='storage_bag', base_id=box['base_id'], dimension=dimension,
                            plan=result))
    return dict(sources=sources, projected_grade=grade, projected_realm=realm)


def plan_spirit_artifact_initial_round(*, owned, item_counts, boxes, cards,
                                      market_targets, progression):
    """全库存预计提升→市场→自选的完整纯规划入口；无需先把已有材料实际吃掉。

    红色本体为0/1起步语义，非红装备不把其原始grade=1计作红色阶数。
    仅原始红色1阶、0境备件用于预计升阶；已培养备件保留，不隐式授权消费。
    镜类材料按逐级配置扣减，已有材料、市场、新箱子不会重复计算。
    """
    inventory, equipped = owned['inventory'], owned['equipped']
    if (inventory.get('complete') is not True or equipped.get('complete') is not True
            or not inventory.get('pid') or not inventory.get('process_start_ticks')
            or (equipped.get('pid'), equipped.get('process_start_ticks')) !=
               (inventory['pid'], inventory['process_start_ticks'])):
        raise ValueError('初始补给规划要求完整同进程装备与库存')
    targets = {(cfg['type'], cfg['parts']) for cfg in progression['items'].values()}
    grades = dict.fromkeys(targets, 0)
    realms = dict(grades)
    equipped_ids = {r['item_id'] for r in equipped['items']}
    for row in equipped['items']:
        key = (row['ware_id'], row['part'])
        if type(row.get('grade')) is not int or type(row.get('realm')) is not int:
            raise ValueError('已装备部件等级未核实')
        grades[key] = row['grade'] if row['quality'] == 6 else 0
        realms[key] = row['realm']
    for row in inventory['items']:
        if (row['item_id'] not in equipped_ids and row['quality'] == 6
                and row['grade'] == 1 and row.get('realm') == 0
                and row['is_break'] is False and row['quantity'] == 1):
            grades[(row['ware_id'], row['part'])] += 1
    remaining = dict(item_counts)
    for row in sorted(equipped['items'], key=lambda r: (r['ware_id'], r['part'])):
        if progression['items'][row['base_id']].get('canUltra') != 1:
            continue
        key = (row['ware_id'], row['part'])
        while True:
            cost = progression['realm'].get((row['base_id'], realms[key] + 1))
            if not cost or cost[0] in progression['items']:
                break
            count = remaining.get(cost[0], 0)
            if type(count) is not int or count < 0:
                raise ValueError('已有升境材料数量未核实')
            if count < cost[1]:
                break
            remaining[cost[0]] -= cost[1]
            realms[key] += 1
    market_targets = tuple(market_targets)
    cost_ids = {t.cost_item_id for t in market_targets}
    if len(cost_ids) > 1:
        raise ValueError('多种市场货币尚未定义比较策略')
    currency = item_counts.get(next(iter(cost_ids)), 0) if cost_ids else 0
    return plan_spirit_artifact_supply_round(grade_levels=grades, realm_levels=realms,
        market_targets=market_targets, currency=currency, boxes=boxes, cards=cards,
        progression=progression)
