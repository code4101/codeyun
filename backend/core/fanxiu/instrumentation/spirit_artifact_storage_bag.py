"""灵器自选匣：Runtime 提供库存，静态奖励目录提供部位，数据库保存展示快照。"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Mapping

from sqlmodel import Session, select

from backend.models import FanxiuPacketBusinessRecord
from .storage_bag_catalog import build_storage_bag_catalog_snapshot

DOMAIN = "spirit_artifact_storage_bag_snapshot"


def sync_known_spirit_artifact_box_counts(session: Session) -> dict[str, Any]:
    """显式读取已知灵器箱子库存并发布共享图鉴，无需打开游戏储物袋。"""
    from .backpack import read_backpack_item_counts
    from .storage_bag_catalog import publish_storage_bag_item_counts
    from ..catalog.item import load_fanxiu_item_runtime_index
    snapshot = load_spirit_artifact_storage_bag_snapshot(session)
    ids = {int(item['base_id']) for item in (snapshot or {}).get('storage_bag_items', [])}
    if not ids:
        raise ValueError('尚无灵器箱子目录，请先同步储物袋')
    counts, evidence = read_backpack_item_counts(
        ids, manager_key='spirit-artifact-box-counts', force_refresh=True)
    publish_storage_bag_item_counts(counts, evidence,
        cards_by_id=load_fanxiu_item_runtime_index(rebuild_missing=False)['cards_by_id'])
    return load_spirit_artifact_storage_bag_snapshot(session)


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
    from .storage_bag_catalog import load_storage_bag_atlas
    from ..catalog.inventory import load_spirit_artifact_hall
    from ..catalog.item import load_fanxiu_item_runtime_index
    from ..catalog.spirit_artifact_wash_rules import load_spirit_artifact_wash_rules
    atlas = load_storage_bag_atlas()
    if atlas is None and row is None:
        return None
    cards = load_fanxiu_item_runtime_index(rebuild_missing=False)['cards_by_id']
    rules = load_spirit_artifact_wash_rules()['items_by_base_id']
    payload = dict(row.payload) if row is not None else {}
    if atlas is not None:
        hall = load_spirit_artifact_hall()
        # 库存只取共享图鉴；旧专用记录仅保留展示目录和独立市场余额。
        projected = project_spirit_artifact_storage_bag(
            atlas, cards, hall['artifacts'],
            payload.get('storage_bag_items', hall['storage_bag_items']))
        payload.update(projected)
        for key in ('source_metadata_evidence', 'source_effect_text_evidence',
                    'source_type_observed_at', 'verified_non_choice_ids',
                    'verified_random_gift_ids'):
            payload.pop(key, None)
        unknown = projected['unresolved_item_ids']
        payload.update(choice_sources_complete=not unknown, stable_sources_complete=not unknown,
                       unresolved_choice_source_ids=unknown, unresolved_gift_reward_ids=unknown)
    payload['storage_bag_items'] = [classify_spirit_artifact_bag_source(
        item, cards.get(str(item.get('base_id')), {}), rules,
    ) for item in payload.get('storage_bag_items', [])]
    return payload


def classify_spirit_artifact_bag_source(item, card, items_by_base_id) -> dict[str, Any]:
    """保留其他用途的展示；稳定标记由正式目录重算，旧快照标记不可信。"""
    stable = resolve_stable_spirit_artifact_rewards(card, items_by_base_id)
    kind = ('select' if card.get('type') == 21 and card.get('sub_type') == 21
            else 'gift' if card.get('type') == 2 and card.get('sub_type') == 1 else 'unknown')
    return {**item, 'selection_kind': kind, 'stable_body_reward_ids': list(stable),
            'choices': [{**choice, 'stable_body': choice.get('reward_id') in stable}
                        for choice in item.get('choices', [])]}


def resolve_spirit_artifact_bag_source_coverage(bag, metadata, *, effect_text_snapshot=None) -> dict[str, Any]:
    """用同进程批量类型事实收敛目录未知项，不伪造名字或礼包奖励。

    OPTIONAL_GIFT=21；PANDORA=2仅说明礼包，不能单凭类型声称随机。
    非自选项不再阻塞自选来源枚举，但礼包固定奖励是否含本体是另一项
    独立未知；调用方不得把choice_sources_complete当全部稳定来源完备。
    """
    evidence = bag.get('evidence') or {}
    if (not evidence.get('pid') or
            (evidence.get('pid'), evidence.get('process_start_ticks')) !=
            (metadata.get('pid'), metadata.get('process_start_ticks'))):
        raise ValueError('储物袋与物品类型事实必须属于同一进程')
    if metadata.get('captured_at') is None:
        raise ValueError('类型事实缺少观测时间')
    rows = metadata.get('items_by_id') or {}
    texts = {}
    if effect_text_snapshot is not None:
        if ((effect_text_snapshot.get('pid'), effect_text_snapshot.get('process_start_ticks')) !=
                (metadata.get('pid'), metadata.get('process_start_ticks'))):
            raise ValueError('礼包效果文本与类型事实必须属于同一进程')
        texts = effect_text_snapshot.get('texts_by_id') or {}
    unknown, nonselect, gifts, random_gifts = [], [], [], []
    for item_id in bag.get('unresolved_item_ids') or []:
        row = rows.get(str(item_id), rows.get(item_id, {}))
        item_type = row.get('item_type_id')
        if row.get('item_resolved') is not True or type(item_type) is not int or item_type == 21:
            unknown.append(item_id)
        else:
            nonselect.append(item_id)
            if item_type == 2:
                text_id = row.get('runtime_effect_description_id')
                description = texts.get(str(text_id), texts.get(text_id))
                if description in ('打开后可随机获得以下道具：', '打开后会随机掉落以下道具'):
                    random_gifts.append(item_id)
                else:
                    gifts.append(item_id)
    return {**bag, 'unresolved_choice_source_ids': unknown,
            'verified_non_choice_ids': nonselect,
            'unresolved_gift_reward_ids': gifts,
            'verified_random_gift_ids': random_gifts,
            'choice_sources_complete': not unknown,
            'stable_sources_complete': not unknown and not gifts,
            'source_type_observed_at': metadata['captured_at']}


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
    return project_spirit_artifact_storage_bag(bag, cards_by_id, artifacts, previous_items)


def project_spirit_artifact_storage_bag(bag, cards_by_id, artifacts, previous_items):
    """从共享库存派生灵器视图；不读游戏，不保存第二份库存。"""
    if not bag.get('complete'):
        raise ValueError('共享储物袋快照不完整')
    captured_at = bag['captured_at']
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
        "unresolved_item_ids": bag.get("unresolved_item_ids", [r["base_id"] for r in bag["items"] if not r.get("item")]), "evidence": bag["evidence"],
    }


def publish_spirit_artifact_box_quantity(
    session: Session, *, box_base_id: int, runtime_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """用已完成开箱的观测更新该箱库存；不读游戏、不关联后续升级。"""
    from ..catalog.item import load_fanxiu_item_runtime_index
    from .storage_bag_catalog import sync_storage_bag_atlas
    observed_at = float(runtime_snapshot.get('observed_at') or 0)
    if observed_at <= 0:
        raise ValueError('开箱库存观测缺少时间')
    # 参数保留业务调用兼容；完整观测发布全部物品，不只更新当前箱子。
    captured_at = datetime.fromtimestamp(observed_at).astimezone().isoformat(timespec='microseconds')
    cards = load_fanxiu_item_runtime_index(rebuild_missing=False)['cards_by_id']
    sync_storage_bag_atlas(runtime_snapshot, cards, captured_at=captured_at)
    return load_spirit_artifact_storage_bag_snapshot(session)


def publish_spirit_artifact_market_currency(
    session: Session, count: int, evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """发布已读取的珍宝阁元魂余额；不读游戏，不要求储物袋页面已打开。"""
    import math
    if type(count) is not int or count < 0:
        raise ValueError('灵器市场余额必须为非负整数')
    observed_at = float(evidence.get('observed_at') or 0)
    if (not math.isfinite(observed_at) or observed_at <= 0
            or not evidence.get('pid') or not evidence.get('process_start_ticks')):
        raise ValueError('余额证据缺少有效观测时间或进程身份')
    row = session.exec(select(FanxiuPacketBusinessRecord).where(
        FanxiuPacketBusinessRecord.domain == DOMAIN,
        FanxiuPacketBusinessRecord.record_key == 'current',
    )).first()
    if row is None:
        from ..catalog.inventory import load_spirit_artifact_hall
        payload = {'storage_bag_items': load_spirit_artifact_hall()['storage_bag_items']}
        row = FanxiuPacketBusinessRecord(domain=DOMAIN, record_key='current', created_at=time.time())
    else:
        payload = dict(row.payload)
    previous = payload.get('market_currency_evidence') or {}
    latest = float(payload.get('market_currency_observed_at') or previous.get('observed_at') or 0)
    if observed_at < latest:
        return payload
    if observed_at == latest and payload.get('market_currency_count') != count:
        raise ValueError('同一余额观测时间出现冲突数值')
    payload.update(market_currency_count=count, market_currency_observed_at=observed_at,
                   market_currency_evidence=dict(evidence))
    row.payload = payload
    row.source_kind = 'dynamic_instrumentation'
    row.entity_name = '灵器自选宝匣库存'
    row.updated_at = time.time()
    session.add(row)
    session.commit()
    return payload


def sync_spirit_artifact_storage_bag(session: Session) -> dict[str, Any]:
    """兼容入口：同步共享储物袋，再读取灵器投影，不另行采集库存。"""
    from .storage_bag_catalog import sync_storage_bag_from_game
    sync_storage_bag_from_game(session)
    return load_spirit_artifact_storage_bag_snapshot(session)
