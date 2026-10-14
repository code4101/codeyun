"""每日发现本周祈愿的未知期望箱；发现完成与研究完成分别报告。"""
from datetime import datetime
import json
import time

from sqlmodel import Session
from backend.db import engine
from backend.core.fanxiu.catalog.item import load_fanxiu_item_runtime_index
from backend.core.fanxiu.instrumentation import fanxiu_instrumentation_service
from backend.core.fanxiu.instrumentation.item_reward_graph import read_item_reward_graph_runtime
from backend.core.fanxiu.prayer_cycle import prayer_cycle_week, prayer_entry_category
from backend.core.fanxiu.prayer_research_planning import plan_unknown_prayer_boxes
from backend.core.fanxiu.storage_bag_unknown_research import read_storage_bag_unknown_research
from backend.core.fanxiu.storage_bag_prayer_research import (
    load_prayer_box_research, persist_prayer_box_rule,
    refresh_prayer_research_rules, soil_box_reward_modifier,
)
from .prayer_box_closeout_execution import prayer_closeout_inventory
from .prayer_daily_resource import prayer_entry_fragment, prayer_entry_from_tokens

STAGE_ID = 'prayer-research-discovery'
STAGE_VERSION = '1'


def observe_prayer_research_inventory(context, *, timeout_seconds=25):
    """等待当前储物袋完整原生列表；首次不完整事实留证，校验失败不重试。

    场景标题出现不代表其 ItemInfoList 已加载。只能用全新完整同进程
    快照进入规划；没有完整快照时有界等待，绝不将空列表当作空库存。
    """
    if type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 60:
        raise ValueError('祈愿库存加载等待必须在0至60秒之间')
    deadline = time.monotonic() + timeout_seconds
    evidence_path = None
    while True:
        snapshot = fanxiu_instrumentation_service.backpack_ui_snapshot()
        if snapshot.get('complete') is True:
            prayer_closeout_inventory(snapshot, {}, {'rules': []})
            return {**snapshot, 'loading_evidence_path': str(evidence_path) if evidence_path else None}
        if evidence_path is None:
            from backend.core.temp_paths import codeyun_temp_root
            evidence_path = codeyun_temp_root('fanxiu-prayer-inventory-not-ready') / f'{time.time_ns()}.json'
            evidence_path.write_text(json.dumps(snapshot, ensure_ascii=False, default=str), encoding='utf-8')
        if time.monotonic() >= deadline:
            raise RuntimeError(f'祈愿研究储物袋未形成完整原生库存：{snapshot.get("reason")}；首次现场 {evidence_path}')
        yield from context.wait_action_settle(1)
        yield from context.wait_scene_exact([525], timeout=5, label='祈愿研究：等待储物袋原生列表加载')


def discover_prayer_box_research(context, *, target_base_id: int | None = None):
    """核验实际周入口、完整当前库存、奖励图，显式发布待研究清单。

    此入口只打开页面和读取事实，不开箱、不补分、不领奖。每日养成集
    在兑换后、使用前执行；已有估计直接跳过。未分类、外箱、条件及
    未测选项各自保留原因，交给对应原子研究执行器，不伪造研究完成。

    target_base_id 仅限制目录奖励图展开和候选规划，库存观察仍然完整。
    单物品结果独立发布，不覆盖每日全量清单，也不授予其他物品消费权限。
    """
    if target_base_id is not None and (type(target_base_id) is not int or target_base_id <= 0):
        raise ValueError('单物品研究范围需要正整数 target_base_id')
    week = prayer_cycle_week()
    yield from context.go_scene(34)
    entry = None
    entry_source = 'world_ocr'
    for _ in range(8):
        frame = context.cur_frame(update=True)
        entry = prayer_entry_fragment(context.ocr_fragments(frame)) or prayer_entry_from_tokens(
            context.ocr_tokens_in_shapes(34, ('左侧菜单',), frame_data_url=frame))
        if entry is not None:
            break
        yield from context.wait_action_settle(0.75)
    if entry is None:
        # The world menu can retain a scroll position that hides the weekly
        # prayer label. Bind the naturally loaded menu identity, then use the
        # shared bounded navigation rather than guessing a hidden icon.
        from backend.core.fanxiu.instrumentation.activity_menu import read_activity_menu_snapshot
        from .activity_menu_navigation import open_loaded_activity_menu_item
        menu = read_activity_menu_snapshot('world_left')
        candidates = [item for item in menu.items
                      if prayer_entry_category(item.name) == week.name]
        if menu.complete and len(candidates) == 1:
            item = candidates[0]
            yield from open_loaded_activity_menu_item(context, item.activity_id,
                kind='world_left', source_scene_id=34, ocr_shape_names=('左侧菜单',),
                expected_scene_ids=(455,), target_gui_name=item.name, max_scrolls=3)
            entry = {'text': item.name}
            entry_source = 'native_menu_verified_navigation'
            yield from context.go_scene(34)
    if entry is None or prayer_entry_category(str(entry.get('text') or '')) != week.name:
        raise RuntimeError('每日研究发现：真实祈愿入口与周周期不符')
    yield from context.wait_click(34, '右侧菜单/储物袋', timeout=10)
    yield from context.wait_scene([525], wait=10)
    snapshot = yield from observe_prayer_research_inventory(context)
    cards = dict(load_fanxiu_item_runtime_index(rebuild_missing=False)['cards_by_id'])
    ids = {row['base_id'] for row in snapshot['items'] if not row.get('is_padding') and row.get('num', 0) > 0}
    if target_base_id is not None:
        ids.intersection_update({target_base_id})
    missing = sorted(base for base in ids if str(base) not in cards)
    if missing:
        graph = read_item_reward_graph_runtime(missing)
        # 奖励图同时包含已知内箱。补未知根/叶子时保持目录接口已有卡的
        # 规范表示，避免同一物品因Runtime补充字段产生“版本变更”假象。
        for base, card in graph['cards_by_id'].items():
            cards.setdefault(base, card)
    with Session(engine) as session:
        research = load_prayer_box_research(session)
        previous_pending = {int(r['base_id']) for r in research['rules']
                            if r.get('classification_complete') is False}
        research['rules'] = refresh_prayer_research_rules(ids, cards, research['rules'])
        for rule in research['rules']:
            if not rule['classification_complete'] or rule['base_id'] in previous_pending:
                persist_prayer_box_rule(session, rule)
    inventory = prayer_closeout_inventory(snapshot, cards, research)
    if target_base_id is not None:
        inventory = [row for row in inventory if row['base_id'] == target_base_id]
    modifiers = {}
    if week.name == '炼丹' and 39032101 in ids:
        from .danling_upgrade import inspect_danling_current_skill
        skill = yield from inspect_danling_current_skill(context, name='坤元土灵')
        modifiers[39032101] = soil_box_reward_modifier(skill)['fingerprint']
    plan = plan_unknown_prayer_boxes(prayer_category=week.name, research=research, inventory=inventory,
                                    reward_modifier_fingerprints=modifiers,
                                    owner_purposes=read_storage_bag_unknown_research().get('owner_purposes', {}))
    excluded_ids = {row['base_id'] for row in plan['excluded_by_owner']}
    plan['unclassified'] = [base for base in sorted(ids) if str(base) not in cards and base not in excluded_ids]
    if prayer_cycle_week().week_start != week.week_start:
        raise RuntimeError('每日研究发现跨越祈愿周界，丢弃清单')
    plan.update(week_start=week.week_start.isoformat(), captured_at=datetime.now().astimezone().isoformat(),
                activity_entry_text=entry['text'], activity_entry_source=entry_source,
                inventory_loading_evidence_path=snapshot.get('loading_evidence_path'),
                outcome='discovery_complete',
                research_complete=not (plan['unknown'] or plan['deferred'] or plan['unclassified'] or plan['unresolved_reward_graphs']))
    from backend.core.fanxiu.data_annotation.kernel_scheduler_control import record_world_discovery
    key = 'prayer_research_plan'
    if target_base_id is not None:
        plan.update(scope='single_item', target_base_id=target_base_id)
        key += f':item:{target_base_id}'
    record_world_discovery(key, plan)
    yield from context.go_scene(34)
    return plan
