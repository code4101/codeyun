"""灵器自选匣：Runtime 提供库存，静态奖励目录提供部位，数据库保存展示快照。"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Mapping

from sqlmodel import Session, select

from backend.models import FanxiuPacketBusinessRecord
from .storage_bag_catalog import build_storage_bag_catalog_snapshot

DOMAIN = "spirit_artifact_storage_bag_snapshot"


def resolve_stable_spirit_artifact_rewards(
    card: Mapping[str, Any], items_by_base_id: Mapping[int, Any],
) -> tuple[int, ...]:
    """仅正式 type/sub_type=21 自选箱中的红色灵器本体是稳定来源。

    optional_gift_rewards 是共用奖励目录，随机箱与升品镜也会引用它。
    未知类型、条件奖励、数量未知均不能据此计划确定自选。
    """
    if card.get("type") != 21 or card.get("sub_type") != 21:
        return ()
    result = []
    for reward in card.get("optional_gift_rewards") or []:
        base_id = reward.get("id")
        cfg = items_by_base_id.get(base_id)
        count = reward.get("count")
        if (cfg and cfg.get("quality") == 6 and type(count) is int and count > 0
                and not reward.get("show_condition")):
            result.append(base_id)
    return tuple(dict.fromkeys(result))


def load_spirit_artifact_storage_bag_snapshot(session: Session) -> dict[str, Any] | None:
    """只读数据库；页面加载不访问游戏。"""
    row = session.exec(select(FanxiuPacketBusinessRecord).where(
        FanxiuPacketBusinessRecord.domain == DOMAIN,
        FanxiuPacketBusinessRecord.record_key == "current",
    )).first()
    if row is None:
        return None
    from ..catalog.item import load_fanxiu_item_runtime_index
    from ..catalog.spirit_artifact_wash_rules import load_spirit_artifact_wash_rules
    cards = load_fanxiu_item_runtime_index(rebuild_missing=False)['cards_by_id']
    rules = load_spirit_artifact_wash_rules()['items_by_base_id']
    payload = dict(row.payload)
    payload['storage_bag_items'] = [classify_spirit_artifact_bag_source(
        item, cards.get(str(item.get('base_id')), {}), rules,
    ) for item in payload.get('storage_bag_items', [])]
    return payload


def classify_spirit_artifact_bag_source(item, card, items_by_base_id) -> dict[str, Any]:
    """保留其他用途的展示；稳定标记由正式目录重算，旧快照标记不可信。"""
    stable = resolve_stable_spirit_artifact_rewards(card, items_by_base_id)
    kind = ('select' if card.get('type') == 21 and card.get('sub_type') == 21
            else 'random' if card.get('type') == 2 and card.get('sub_type') == 1 else 'unknown')
    return {**item, 'selection_kind': kind, 'stable_body_reward_ids': list(stable),
            'choices': [{**choice, 'stable_body': choice.get('reward_id') in stable}
                        for choice in item.get('choices', [])]}


def build_spirit_artifact_storage_bag_snapshot(
    runtime_snapshot: Mapping[str, Any],
    cards_by_id: Mapping[str, Any],
    artifacts: list[dict[str, Any]],
    previous_items: list[dict[str, Any]],
    *,
    captured_at: str,
) -> dict[str, Any]:
    """从完整普通储物袋投影库存，保留历史盒子并将已耗尽的数量归零。

    奖励用完整灵器名和部位与目录名称/描述精确关联，不依赖 OCR 或标题缩写。
    非灵器盒子不进入本页；部分奖励无法关联的盒子不输出残缺候选列表。
    """
    bag = build_storage_bag_catalog_snapshot(runtime_snapshot, cards_by_id, captured_at=captured_at)
    if (bag.get("tab") or {}).get("number") != 1:
        raise ValueError("必须读取普通储物袋完整页签，不能用筛选页签清零库存")
    quantities = {row["base_id"]: row["num"] for row in bag["items"]}
    targets = [(artifact["name"], row["part_name"])
               for artifact in artifacts for row in artifact["rows"]]
    items = {item["title"]: dict(item) for item in previous_items}
    for card in cards_by_id.values():
        rewards = card.get("optional_gift_rewards") or []
        title = card.get("name", "")
        base_id = int(card["id"])
        if not rewards or (base_id not in quantities and title not in items):
            continue
        choices = []
        for reward in rewards:
            text = f'{reward.get("name", "")} {reward.get("description", "")}'
            matches = [(name, part) for name, part in targets if f"{name}·{part}" in text]
            if len(matches) != 1:
                break
            name, part = matches[0]
            choices.append({
                "order": len(choices) + 1, "raw_name": reward["name"],
                "artifact_name": name, "part_name": part,
                "reward_id": reward["id"], "reward_quantity": reward.get("count", 1),
            })
        if len(choices) != len(rewards):
            if title in items:
                raise ValueError(f"{title} 的奖励目录不完整，保留原数据库快照")
            continue
        items[title] = {"title": title, "base_id": base_id,
                        "quantity": quantities.get(base_id, 0), "choices": choices}
    return {
        "storage_bag_items": [{**item, "order": index + 1} for index, item in enumerate(items.values())],
        "captured_at": captured_at, "source": bag["source"],
        "unresolved_item_ids": bag["unresolved_item_ids"], "evidence": bag["evidence"],
    }


def sync_spirit_artifact_storage_bag(session: Session) -> dict[str, Any]:
    """显式只读游戏并更新数据库；不打开、领取或使用宝匣。"""
    from backend.core.fanxiu.catalog.inventory import load_spirit_artifact_hall
    from backend.core.fanxiu.catalog.item import load_fanxiu_item_runtime_index
    from backend.core.fanxiu.instrumentation import fanxiu_instrumentation_service

    hall = load_spirit_artifact_hall()
    previous = load_spirit_artifact_storage_bag_snapshot(session)
    runtime = fanxiu_instrumentation_service.backpack_ui_snapshot()
    now = time.time()
    captured_at = datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds")
    payload = build_spirit_artifact_storage_bag_snapshot(
        runtime, load_fanxiu_item_runtime_index(rebuild_missing=False)["cards_by_id"],
        hall["artifacts"], (previous or hall)["storage_bag_items"], captured_at=captured_at,
    )
    row = session.exec(select(FanxiuPacketBusinessRecord).where(
        FanxiuPacketBusinessRecord.domain == DOMAIN,
        FanxiuPacketBusinessRecord.record_key == "current",
    )).first()
    if row is None:
        row = FanxiuPacketBusinessRecord(domain=DOMAIN, record_key="current", created_at=now)
    row.source_kind = "dynamic_instrumentation"
    row.entity_name = "灵器自选宝匣库存"
    row.captured_at = captured_at
    row.captured_date = captured_at[:10]
    row.payload = payload
    row.evidence = payload["evidence"] or {}
    row.updated_at = now
    session.add(row)
    session.commit()
    return payload
