"""非祈愿自选箱的库存均衡纯规划；配置、规划与游戏执行相互独立。"""
from dataclasses import dataclass
from heapq import heapify, heappop, heappush
from typing import Mapping, Sequence


@dataclass(frozen=True)
class BalancedBoxChoice:
    reward_id: int
    count_per_box: int
    balance_unit: str
    units_per_item: int
    available_boxes: int | None = None


@dataclass(frozen=True)
class BalancedBoxBatch:
    reward_id: int
    opened_count: int


@dataclass(frozen=True)
class BalancedBoxPlan:
    batches: tuple[BalancedBoxBatch, ...]
    projected_inventory: dict[int, int]
    projected_balance_units: dict[int, int]
    remaining_boxes: int


def plan_storage_bag_balanced_choice(
    choices: Sequence[BalancedBoxChoice], inventory: Mapping[int, int],
    quantity: int, *, non_prayer_verified: bool,
) -> BalancedBoxPlan:
    """每箱优先补当前最低的可比较库存，平局按奖励ID稳定选择，最后合并批次。

    单位与换算由业务提供方核实，禁止按箱名猜价值：同单位普通道具可显式
    使用 units_per_item=1；不同单箱数量按真实奖励更新预计库存。换算为
    分数时调用方统一放大到整数单位。缺失库存为未知，不能默认为0。
    非祈愿资格须独立核实，未核实不得用均衡策略消耗祈愿候选。可选额度为
    当前明确限制，None表示已核实不限。这里不设置auto_claim，不读游戏。
    多来源依次传入 projected_inventory，避免每个箱都按同一旧库存分配。
    输出是全部顺利到账的预计值，执行方须按真实库存重建请求并验证到账，
    不能将预计值写作实测，不能重放可能已发生的开箱。
    """
    if non_prayer_verified is not True:
        raise ValueError("均衡自选需要已核实非祈愿资格")
    if type(quantity) is not int or quantity < 0 or not choices:
        raise ValueError("均衡自选需要非负整数箱数及明确候选")
    projected = dict(inventory)
    if any(type(i) is not int or i <= 0 or type(n) is not int or n < 0
           for i, n in projected.items()):
        raise ValueError("库存必须为明确物品ID和非负整数数量")
    by_id = {}
    for choice in choices:
        if (type(choice.reward_id) is not int or choice.reward_id <= 0
                or choice.reward_id in by_id or choice.reward_id not in projected
                or type(choice.count_per_box) is not int or choice.count_per_box <= 0
                or not isinstance(choice.balance_unit, str) or not choice.balance_unit.strip()
                or type(choice.units_per_item) is not int or choice.units_per_item <= 0
                or choice.available_boxes is not None
                and (type(choice.available_boxes) is not int or choice.available_boxes < 0)):
            raise ValueError("候选重复、库存缺失或奖励/单位/额度未核实")
        by_id[choice.reward_id] = choice
    if len({c.balance_unit for c in choices}) != 1:
        raise ValueError("不同资源单位不能直接进行库存均衡")
    counts = dict.fromkeys(by_id, 0)
    balances = {i: projected[i] * c.units_per_item for i, c in by_id.items()}
    heap = [(balances[i], i) for i, c in by_id.items() if c.available_boxes != 0]
    heapify(heap)
    remaining = quantity
    while remaining and heap:
        balance, reward_id = heappop(heap)
        choice = by_id[reward_id]
        counts[reward_id] += 1
        projected[reward_id] += choice.count_per_box
        balances[reward_id] = balance + choice.count_per_box * choice.units_per_item
        remaining -= 1
        if choice.available_boxes is None or counts[reward_id] < choice.available_boxes:
            heappush(heap, (balances[reward_id], reward_id))
    batches = tuple(BalancedBoxBatch(i, counts[i]) for i in sorted(counts) if counts[i])
    return BalancedBoxPlan(batches, projected, balances, remaining)
