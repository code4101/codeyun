"""Exact reward attribution for the two selected second-tier ring boxes."""
from collections.abc import Mapping
from datetime import datetime


RING_INVENTORY_SOURCE = "BackpackMgr.Model.BackpackData._EquipGemItemDic"
RING_BOX_IDS = frozenset({19010195, 19010277})


def ring_box_reward_targets(box_card: Mapping, cards: Mapping) -> dict[int, str]:
    """Bind the configured one-ring candidates; other type48 boxes fail closed."""
    rewards = box_card.get("optional_gift_rewards") or []
    has_ring = any((cards.get(str(row.get("id"))) or {}).get("type") == 48
                   for row in rewards if isinstance(row, Mapping))
    if not has_ring:
        return {}
    if box_card.get("id") not in RING_BOX_IDS or not rewards:
        raise ValueError("灵环开箱只开放两种已授权二阶箱的独立原子入口")
    result = {}
    for reward in rewards:
        item_id = reward.get("id")
        card = cards.get(str(item_id)) or {}
        if (type(item_id) is not int or item_id <= 0 or item_id in result
                or reward.get("count") != 1 or card.get("type") != 48
                or card.get("sub_type") != 38 or not reward.get("name")):
            raise ValueError("灵环箱候选身份、类型或每箱1个契约不完整")
        result[item_id] = reward["name"]
    return result


def verify_ring_reward_delta(before: Mapping, after: Mapping, *, targets: Mapping[int, str],
                             opened_count: int, process_identity: tuple[int, int]) -> list[dict]:
    """Require exactly one attributed ring per consumed box and no other changes."""
    if type(opened_count) is not int or opened_count < 0 or not targets:
        raise ValueError("灵环收益验账需要实际开箱数量和完整候选")

    def totals(snapshot):
        evidence = snapshot.get("evidence") or {}
        if (snapshot.get("complete") is not True or snapshot.get("source") != RING_INVENTORY_SOURCE
                or (evidence.get("pid"), evidence.get("process_start_ticks")) != process_identity
                or not snapshot.get("fingerprint")):
            raise ValueError("灵环收益库存来源、完整性或进程身份不一致")
        result, seen = {}, set()
        for item in snapshot.get("items") or []:
            instance, base, quantity = (item.get(key) for key in ("instance_id", "base_id", "num"))
            if (not instance or instance in seen or type(base) is not int or base <= 0
                    or type(quantity) is not int or quantity <= 0):
                raise ValueError("灵环收益库存实例或数量无效")
            seen.add(instance)
            result[base] = result.get(base, 0) + quantity
        return result

    previous, current = totals(before), totals(after)
    if datetime.fromisoformat(after["captured_at"]) < datetime.fromisoformat(before["captured_at"]):
        raise ValueError("灵环动作后快照时间倒退")
    deltas = {base: current.get(base, 0) - previous.get(base, 0)
              for base in previous.keys() | current.keys()}
    if any(delta < 0 or (delta and base not in targets) for base, delta in deltas.items()):
        raise ValueError("灵环库存出现减少或候选之外增量")
    if sum(deltas.values()) != opened_count:
        raise ValueError("灵环实际收益与每箱1个及实际开箱量不一致")
    return [{"item_id": base, "reward_key": f"item:{base}", "name": targets[base],
             "quantity": delta} for base, delta in sorted(deltas.items()) if delta > 0]
