"""突破→无双的优先道具路线；一次调用最多使用一颗，不继续洗其它部件。"""
import json
import time
from pathlib import Path

from ...catalog.spirit_artifact_wash_rules import load_spirit_artifact_wash_rules
from ...instrumentation.backpack import read_backpack_item_counts
from ...instrumentation.spirit_artifact import read_spirit_artifact_item_runtime
from ...instrumentation.spirit_artifact_affixes import read_spirit_artifact_affix_rules
from ...instrumentation.spirit_artifact_wash_observation import read_spirit_artifact_wash_observation
from ...instrumentation.spirit_artifact_collector import collect_spirit_artifact_snapshot_once
from .spirit_artifact_cleanse import SpiritArtifactCleanseRuntimeGuiAdapter
from .spirit_artifact_protected_locks import plan_spirit_artifact_protected_locks

PEERLESS_STONE = 14000049


def prefer_peerless_stone(*, grade: int, effect_count: int, stock: int) -> bool:
    """用户准入：阶数<6、词条<=5、有无双石时，优先于手动/自动。"""
    return 0 < grade < 6 and 0 < effect_count <= 5 and stock > 0


def use_peerless_stone_once(context, execute, *, target, evidence_path: Path) -> dict:
    """已选中的突破部件上使用一颗无双石并保存；不满足条件返回 skipped。

    采用无双不比较总评分。已有 A/S 必须锁定，候选及保存结果不得改变它们。
    消耗动作不重试；异常保留现场及证据。调用本入口即授权这一次道具消耗。
    """
    path = Path(evidence_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    def record(event, **data):
        with path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(dict(event=event, at=time.time(), target=target, **data),
                               ensure_ascii=False, default=str) + '\n')
    def fingerprint(effects):
        return {e['cleanse_id']: (e['value'], e['quality'], e['locked']) for e in effects}
    def stock():
        counts, state = read_backpack_item_counts([PEERLESS_STONE], manager_key='peerless-stone', force_refresh=True)
        if (state['pid'], state['process_start_ticks']) != target.process_identity:
            raise RuntimeError('无双石库存与目标进程不同')
        return counts[PEERLESS_STONE]
    before = read_spirit_artifact_wash_observation(target, verify_ui=True)
    item = read_spirit_artifact_item_runtime(target.item_id)
    if fingerprint(item['effects']) != fingerprint(before['effects']):
        raise RuntimeError('部件属性在准入检查中变化')
    owned = stock()
    if not prefer_peerless_stone(grade=item['grade'], effect_count=len(before['effects']), stock=owned):
        return dict(status='skipped', reason='stone_route_not_eligible', consumed=0)
    config = read_spirit_artifact_affix_rules([e['cleanse_id'] for e in before['effects'] + before['pending_effects']])
    if (config['pid'], config['process_start_ticks']) != target.process_identity:
        raise RuntimeError('词条规则与目标进程不同')
    rules = config['rules']
    if any(rules[e['cleanse_id']]['name'] == '灵器无双' for e in before['effects']):
        return dict(status='skipped', reason='already_peerless', consumed=0)
    if any(rules[e['cleanse_id']]['name'] == '灵器无双' for e in before['pending_effects']):
        raise RuntimeError('已有无双候选，应先保存，不再使用无双石')
    a_codes = set(load_spirit_artifact_wash_rules()['wares'][target.ware_id]['a_codes'])
    a_ids = {e['cleanse_id'] for e in before['effects'] if rules[e['cleanse_id']]['code'] in a_codes}
    if before.get('is_break') is not True or len(a_ids) != 4:
        raise RuntimeError('无双石路线要求已突破且四 A 完整')
    gui = SpiritArtifactCleanseRuntimeGuiAdapter(context, execute)
    lock_plan = plan_spirit_artifact_protected_locks(before['effects'], rules, a_codes)
    if not lock_plan['should_wash']:
        return dict(status='skipped', reason='all_attributes_protected', consumed=0)
    protected_ids = set(lock_plan['desired_lock_ids'])
    if {e['cleanse_id'] for e in before['effects'] if e['locked']} != protected_ids:
        if before['pending_effects']:
            raise RuntimeError('未处理候选与锁状态冲突，保留现场')
        gui.set_locks(sorted(protected_ids), target_item_id=target.item_id)
        before = read_spirit_artifact_wash_observation(target, verify_ui=True)
    protected = {k:v for k,v in fingerprint(before['effects']).items() if k in protected_ids}
    if not any(not e['locked'] for e in before['effects']):
        raise RuntimeError('没有可洗炼的未锁词条')
    record('eligible', grade=item['grade'], count=owned, before=before)
    preview = gui.preview_advanced_item(PEERLESS_STONE)
    if preview['target_item_id'] != target.item_id or preview['count'] != owned:
        raise RuntimeError('无双石确认目标或库存改变')
    if fingerprint(preview['observation']['effects']) != fingerprint(before['effects']):
        raise RuntimeError('确认页本体属性改变')
    record('consume_attempt', preview=preview)
    context.click_shape_center(713, '确认使用道具')
    execute(context.wait_scene([714], wait=15))
    after = read_spirit_artifact_wash_observation(target, verify_ui=True)
    remaining = stock()
    pending = fingerprint(after['pending_effects'])
    if (owned - remaining != 1 or fingerprint(after['effects']) != fingerprint(before['effects'])
            or len(pending) != len(before['effects']) or any(pending.get(k) != v for k,v in protected.items())):
        record('verification_failed', after=after, remaining=remaining)
        raise RuntimeError('无双石消耗/锁定属性/候选验证失败，不重试')
    result_rules = read_spirit_artifact_affix_rules(list(pending))
    if (result_rules['pid'], result_rules['process_start_ticks']) != target.process_identity:
        raise RuntimeError('无双候选配置进程改变')
    if not any(result_rules['rules'][k]['name'] == '灵器无双' for k in pending):
        raise RuntimeError('使用无双石后未读到灵器无双候选')
    record('peerless_candidate', after=after, remaining=remaining)
    context.click_shape_center(714, '保留新属性')
    gui.finish_effect_activation()
    saved = read_spirit_artifact_wash_observation(target, verify_ui=True)
    if saved['pending_effects'] or fingerprint(saved['effects']) != pending:
        raise RuntimeError('无双候选未正确保存')
    hall = collect_spirit_artifact_snapshot_once()
    if not hall.get('runtime_complete'):
        raise RuntimeError('无双已保存，但总览同步不完整')
    # 属性已落地不代表视觉事务完成；同步期间仍可能出现延迟激活弹层。
    final_page = gui.finish_effect_activation()
    record('saved', snapshot=saved, remaining=remaining, final_scene=final_page.scene_id)
    return dict(status='complete', consumed=1, stock_before=owned, stock_after=remaining,
                snapshot=saved)
