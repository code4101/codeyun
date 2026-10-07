"""Gameplay-rank whole-batch shortage supply through shared 神物兑换.

Owner seq1487 confirms challenges are free after exploration. Only exploration
stock is funded; current shop prices apply, with no cash purchase or recharge.
"""
from fractions import Fraction
from backend.core.fanxiu.activity.exchange_challenge_planning import required_exchange_resource_stock
from .sacred_exchange_supply import SacredExchangeSupplySpec, ensure_sacred_exchange_stock


BEAST_EXPLORE_SUPPLY = SacredExchangeSupplySpec(
    label='兽渊探查符补给', source_item_id=4000003,
    source_item_name='星海火树', target_item_id=30050000,
)


def required_beast_explore_stock(*, explores: int, points: int,
                                points_per_item: int, cost_per_explore: int) -> int:
    """Minimum total stock funding a whole planned batch after free points."""
    if any(type(v) is not int for v in (explores, points, points_per_item, cost_per_explore)):
        raise ValueError('兽渊补给参数必须是整数')
    if explores <= 0 or points < 0 or points_per_item <= 0 or cost_per_explore <= 0:
        raise ValueError('兽渊补给缺少有效整轮次数及资源换算')
    return required_exchange_resource_stock(attempts=explores, free_points=points,
        points_per_item=points_per_item, points_per_attempt=Fraction(cost_per_explore))


def ensure_beast_explore_stock(context, *, required_stock: int):
    """Raise only the explicitly authorized explore-item stock floor.

    The common provider owns current inventory, shop identity, quantity control,
    source/target double-delta proof and insufficient-source failure. Its GUI
    binding must still be accepted on the real 星海火树 panel before publishing.
    """
    return (yield from ensure_sacred_exchange_stock(
        context, spec=BEAST_EXPLORE_SUPPLY, required_stock=required_stock))
