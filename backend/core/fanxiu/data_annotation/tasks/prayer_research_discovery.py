"""每日发现本周祈愿的未知期望箱；发现完成与研究完成分别报告。"""
from datetime import datetime

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


def discover_prayer_box_research(context):
    """核验实际周入口、完整当前库存、奖励图，显式发布待研究清单。

    此入口只打开页面和读取事实，不开箱、不补分、不领奖。每日养成集
    在兑换后、使用前执行；已有估计直接跳过。未分类、外箱、条件及
    未测选项各自保留原因，交给对应原子研究执行器，不伪造研究完成。
    """
    week = prayer_cycle_week()
    yield from context.go_scene(34)
    entry = None
    for _ in range(8):
        frame = context.cur_frame(update=True)
        entry = prayer_entry_fragment(context.ocr_fragments(frame)) or prayer_entry_from_tokens(
            context.ocr_tokens_in_shapes(34, ('左侧菜单',), frame_data_url=frame))
        if entry is not None:
            break
        yield from context.wait_action_settle(0.75)
    if entry is None or prayer_entry_category(str(entry.get('text') or '')) != week.name:
        raise RuntimeError('每日研究发现：真实祈愿入口与周周期不符')
    yield from context.wait_click(34, '右侧菜单/储物袋', timeout=10)
    yield from context.wait_scene([525], wait=10)
    snapshot = fanxiu_instrumentation_service.backpack_ui_snapshot()
    # 公共库存绑定器先验证完整同进程快照，再允许读取其中的物品 ID。
    prayer_closeout_inventory(snapshot, {}, {'rules': []})
    cards = dict(load_fanxiu_item_runtime_index(rebuild_missing=False)['cards_by_id'])
    ids = {row['base_id'] for row in snapshot['items'] if not row.get('is_padding') and row.get('num', 0) > 0}
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
                activity_entry_text=entry['text'], outcome='discovery_complete',
                research_complete=not (plan['unknown'] or plan['deferred'] or plan['unclassified'] or plan['unresolved_reward_graphs']))
    from backend.core.fanxiu.data_annotation.kernel_scheduler_control import record_world_discovery
    record_world_discovery('prayer_research_plan', plan)
    yield from context.go_scene(34)
    return plan
