"""突破后洗炼的共同保护规则；全 A/S 是跳过条件，不是全锁指令。"""

S_ATTRIBUTE_NAMES = frozenset({'灵器无双', '混沌灵威', '混沌道威'})


def plan_spirit_artifact_protected_locks(effects, rules, a_codes):
    """纯决策：保护已有 A/S，释放其它词条；未识别属性拒绝猜测。

    全部受保护时返回 should_wash=False、desired_lock_ids=None，调用方
    不改锁、不消费。否则目标锁集合严格小于词条数，保证可洗槽位。
    此规则仅用于突破后的追 S 路线，不替代预备阶段的收集/精炼策略。
    """
    ids = [e['cleanse_id'] for e in effects]
    if not ids or len(set(ids)) != len(ids):
        raise ValueError('需要完整且不重复的已保存词条')
    protected = set()
    for effect_id in ids:
        rule = rules[effect_id]
        if not rule.get('code') and rule.get('name') not in S_ATTRIBUTE_NAMES:
            raise ValueError(f'词条分类不明：{effect_id}')
        if rule.get('code') in a_codes or rule.get('name') in S_ATTRIBUTE_NAMES:
            protected.add(effect_id)
    washable = set(ids) - protected
    return dict(should_wash=bool(washable), protected_ids=sorted(protected),
                desired_lock_ids=sorted(protected) if washable else None,
                washable_ids=sorted(washable))


def prepare_spirit_artifact_protected_locks(context, execute):
    """在当前洗炼页校准 A/S 锁；不洗炼、不保存候选、不打开自动设置。"""
    from ...instrumentation.spirit_artifact_ui import read_spirit_artifact_ui_snapshot
    from ...instrumentation.spirit_artifact_affixes import enrich_spirit_artifact_effects
    from ...catalog.spirit_artifact_wash_rules import load_spirit_artifact_wash_rules
    from .spirit_artifact_cleanse import SpiritArtifactCleanseRuntimeGuiAdapter
    current = read_spirit_artifact_ui_snapshot()
    if not current.get('is_wash') or not current.get('is_break'):
        raise RuntimeError('A/S 保护要求当前已突破洗炼部件')
    enrich_spirit_artifact_effects(current['effects'], pid=current['pid'],
                                 process_start_ticks=current['process_start_ticks'])
    plan = plan_spirit_artifact_protected_locks(
        current['effects'], {e['cleanse_id']: e for e in current['effects']},
        load_spirit_artifact_wash_rules()['wares'][current['ware_id']]['a_codes'])
    if not plan['should_wash']:
        return dict(status='skipped', reason='all_attributes_protected', plan=plan)
    desired = set(plan['desired_lock_ids'])
    if {e['cleanse_id'] for e in current['effects'] if e['locked']} != desired:
        if current['pending_effects']:
            raise RuntimeError('候选未处理，不能调整 A/S 锁')
        SpiritArtifactCleanseRuntimeGuiAdapter(context, execute).set_locks(
            sorted(desired), target_item_id=current['item_id'])
    return dict(status='ready', item_id=current['item_id'], plan=plan)
