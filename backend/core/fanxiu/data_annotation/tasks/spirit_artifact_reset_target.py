"""把已有事实计划解析为市场重置调用参数；不重做阶段判定、不触碰游戏。"""
from __future__ import annotations

from dataclasses import dataclass

from ...catalog.spirit_artifact_market import load_spirit_artifact_market_targets
from .spirit_artifact_purchase import SpiritArtifactPurchaseRequest
from .spirit_artifact_reset_plan import SpiritArtifactResetPlanEntry


@dataclass(frozen=True)
class SpiritArtifactMarketResetTarget:
    entry: SpiritArtifactResetPlanEntry
    request: SpiritArtifactPurchaseRequest
    artifact_name: str
    selected_part_text: str


def prepare_spirit_artifact_market_reset(
    entry: SpiritArtifactResetPlanEntry, *, cost_item_id: int, currency_limit: int,
) -> SpiritArtifactMarketResetTarget:
    """从事实计划绑定唯一正式商品；调用方不再拼goods/base/name三套身份。

    币种与费用上限必须由授权传入，不从目录自动扩大消费授权。无市场供应或
    目标歧义报错，不自动切换开箱。返回数据可直接传给reset_from_market；
    它仍负责fresh准入、来源可用性、当前价格、重入和收尾。本函数不把旧计划
    当成执行许可，也不根据供应目录把blocked/choose_source计划改成可执行。
    静态解析及已完成目标的真实市场入口仅收尾重入已验证；
    新采购仍由来源组件执行当前界面与到账核验。
    """
    if (type(cost_item_id) is not int or cost_item_id <= 0
            or type(currency_limit) is not int or currency_limit < 0):
        raise ValueError('需要明确币种和非负整数费用上限')
    if (entry.assessment.stage != '错升' or entry.assessment.readiness != 'ready'
            or entry.action not in ('reset_owned', 'obtain_raw')):
        raise ValueError('只接受已规划就绪的错升目标')
    candidates = [target for target in load_spirit_artifact_market_targets()
                  if (target.ware_id, target.part) ==
                  (entry.assessment.ware_id, entry.assessment.part)]
    if len(candidates) != 1:
        raise ValueError('目标无唯一市场本体供应，不自动切换其他来源')
    target = candidates[0]
    if target.base_id != entry.base_id or target.cost_item_id != cost_item_id:
        raise ValueError('计划本体或授权币种与市场目录不一致')
    if currency_limit < target.unit_price:
        raise ValueError('授权费用上限不足一件本体，不自动提高预算')
    if entry.action == 'obtain_raw' and not any(
        supply.kind == 'market' and supply.reference_id == target.goods_id
        and supply.base_id == target.base_id and supply.cost_item_id == target.cost_item_id
        and supply.unit_cost == target.unit_price and supply.available > 0
        for supply in entry.supplies
    ):
        raise ValueError('当前计划没有对应的已核实市场供应')
    request = SpiritArtifactPurchaseRequest(
        target.goods_id, target.base_id, target.ware_id, target.part, target.name,
        cost_item_id=cost_item_id, unit_price=target.unit_price,
        currency_limit=currency_limit)
    return SpiritArtifactMarketResetTarget(entry, request, target.artifact_name, target.name)
