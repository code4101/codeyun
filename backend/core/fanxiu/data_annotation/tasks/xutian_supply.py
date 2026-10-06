"""虚天通过堕天松补给；价格、限购与双差值由共用交易接口负责。"""
from .sacred_exchange_supply import SacredExchangeSupplySpec, ensure_sacred_exchange_stock
from math import ceil

XUTIAN_SACRED_SOURCE_ITEM_ID = 4000002
# Identity and safe return verified from the live 堕天松 description panel.
XUTIAN_SACRED_SOURCE_DETAIL_SCENE = 930
XUTIAN_SUPPLY_ITEM_IDS = frozenset({30030000, 30030001})


def xutian_sacred_supply_spec(*, item_id: int, source_detail_scene: int) -> SacredExchangeSupplySpec:
    """Bind a verified source detail scene and one native consumable whitelist."""
    if item_id not in XUTIAN_SUPPLY_ITEM_IDS or source_detail_scene <= 0:
        raise ValueError("虚天补给仅兑换探灵符/万年灵液，并需已验收的堕天松详情场景")
    return SacredExchangeSupplySpec(label="虚天殿道具补给", source_item_id=XUTIAN_SACRED_SOURCE_ITEM_ID,
                                    source_item_name="堕天松", target_item_id=item_id,
                                    detail_scene=source_detail_scene)


def ensure_xutian_consumable_supply(context, *, item_id: int, required_stock: int,
                                   source_detail_scene: int = XUTIAN_SACRED_SOURCE_DETAIL_SCENE):
    """Fill an explicit stock floor using owned 堕天松; independent of tier batch size."""
    return (yield from ensure_sacred_exchange_stock(
        context, spec=xutian_sacred_supply_spec(item_id=item_id, source_detail_scene=source_detail_scene),
        required_stock=required_stock))


def ensure_xutian_batch_supply(context, *, required_challenges: int, resources: dict,
                               allow_item_refill: bool):
    """Supply shortages without changing the planner's whole next-tier count.

    Owned 堕天松 is the only payment source. Refill stock may be acquired ahead
    to 3000 (the existing ranking stock policy). Owner seq1249 limits automatic
    acquisition to missing exploration/challenge refills. Boost switches stay
    ON, but boost inventory never triggers exchange.
    Every purchase re-reads price, limits, source identity and both deltas.
    """
    if type(required_challenges) is not int or not 0 < required_challenges <= 5000:
        raise ValueError("虚天补给需一个合法的完整逐档批次数量")
    targets = []
    if allow_item_refill is True:
        for key, label in ((1, "explore"), (2, "challenge")):
            config = resources["count_configs"][key]
            item_id = int(config["supplement_item_id"])
            per_item = int(config["restored_per_item"])
            if per_item <= 0 or item_id not in {30030000, 30030001}:
                raise RuntimeError("虚天补次数道具契约发生变化")
            needed = ceil(max(0, required_challenges + 2 - int(resources[label]["count"])) / per_item)
            owned = int(resources["supplement_item_counts"].get(item_id, 0))
            if owned < needed:
                targets.append((item_id, max(3000, needed)))
    receipts = []
    for item_id, stock in targets:
        result = yield from ensure_xutian_consumable_supply(context, item_id=item_id, required_stock=stock)
        receipts.append({"item_id": item_id, "target_stock": stock, **result})
    return {"status": "supplied" if receipts else "sufficient", "receipts": receipts}
