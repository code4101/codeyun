"""已选本体的预备→突破：按当前属性分流，不保存子阶段游标。"""
from pathlib import Path
import time

from ...instrumentation.spirit_artifact_wash_observation import (
    SpiritArtifactWashTarget, read_spirit_artifact_wash_observation,
)
from .spirit_artifact_advanced_scroll import AdvancedScrollProfile
from .spirit_artifact_yinxian import YinxianAttribute, meets_yinxian_target


def run_spirit_artifact_cultivation(
    context, execute, *, target: SpiritArtifactWashTarget, rules: dict,
    c_codes: set[str], evidence_dir: Path, stop_at: float,
    max_consumptions: int = 100, target_ratio: float = .90,
    scroll_profile: AdvancedScrollProfile | None = None,
    fast_observation: bool = True,
    navigate: bool = False,
) -> dict:
    """连续凑基础、培养 A、突破；navigate=True 时先导航至指定本体。

    未齐四基础且没有达标 A 才补基础；已有达标/满 A 直接保护并继续培养。
    入口候选交给所选策略重新评价。普通洗炼和高级道具共用一次消耗预算；
    任一子步骤暂停即返回，只有实际突破成功才 complete。突破接口负责一次
    总览同步；异常由调用方处理，禁止自动重发不确定动作。
    """
    from ...catalog.spirit_artifact_wash_rules import load_spirit_artifact_wash_rules
    from ...instrumentation.spirit_artifact_affixes import read_spirit_artifact_affix_rules
    from .spirit_artifact_basic_collection import run_basic_attribute_collection
    from .spirit_artifact_a_collection import run_a_collection
    from .spirit_artifact_breakthrough_action import breakthrough_spirit_artifact

    if max_consumptions <= 0 or stop_at <= time.time():
        raise ValueError('培养需要有效期限和消耗预算')
    profile = scroll_profile if scroll_profile is not None else AdvancedScrollProfile(.8, .8, .4)
    if not isinstance(profile, AdvancedScrollProfile):
        raise TypeError('scroll_profile 必须是 AdvancedScrollProfile')
    if navigate:
        from ...catalog.spirit_artifact_identity import load_spirit_artifact_templates
        from .spirit_artifact_cleanse import SpiritArtifactCleanseRuntimeGuiAdapter
        name, parts = load_spirit_artifact_templates()[target.ware_id]
        SpiritArtifactCleanseRuntimeGuiAdapter(context, execute).select_item(
            target.item_id, target.ware_id, name, parts[target.part - 1])
    configured = load_spirit_artifact_wash_rules()['wares'][target.ware_id]
    a_codes = set(configured['a_codes'])
    rules = dict(rules)
    evidence_dir = Path(evidence_dir)
    from ...instrumentation.spirit_artifact_memory import spirit_artifact_memory
    current = None if navigate else spirit_artifact_memory.snapshot(target)
    if current is None:
        current = read_spirit_artifact_wash_observation(target, verify_ui=True)
    consumed, steps = 0, []

    def result(status, reason):
        return dict(status=status, stop_reason=reason, consumed=consumed,
                    snapshot=current, steps=steps)

    while True:
        if current.get('is_break') is not False:
            return result('blocked', 'target_not_unbroken')
        missing = {e['cleanse_id'] for e in current['effects']} - rules.keys()
        if missing:
            fetched = read_spirit_artifact_affix_rules(sorted(missing))
            if (fetched['pid'], fetched['process_start_ticks']) != target.process_identity:
                raise RuntimeError('培养词条配置与目标进程不一致')
            rules.update(fetched['rules'])
        attributes = [YinxianAttribute(
            e['cleanse_id'], rules[e['cleanse_id']]['code'], e['value'], e['quality'],
            e['locked'], int(rules[e['cleanse_id']]['max']),
            150 if target.part in (5, 6) else 100) for e in current['effects']]
        full_a = {e.code for e in attributes if e.quality >= 6 and e.is_full}
        if time.time() >= stop_at - 90:
            return result('paused', 'deadline_paused')
        if a_codes <= full_a and current['pending_effects']:
            return result('blocked', 'full_a_with_unresolved_candidate')
        if a_codes <= full_a and not current['pending_effects']:
            completed = breakthrough_spirit_artifact(
                context, execute, target=target,
                evidence_path=evidence_dir / 'breakthrough.jsonl')
            steps.append({'step': 'breakthrough', 'result': completed})
            current = completed['snapshot']
            if completed['status'] != 'complete' or current.get('is_break') is not True:
                return result('blocked', 'breakthrough_not_completed')
            return result('complete', 'breakthrough_completed')
        remaining = max_consumptions - consumed
        if remaining <= 0:
            return result('paused', 'budget_paused')
        has_a_progress = any(meets_yinxian_target(
            is_needed_a=e.code in a_codes, basic_score=e.ratio * e.basic_full_score,
            basic_full_score=e.basic_full_score, target_ratio=target_ratio) for e in attributes)
        basics_complete = {'ATTACK', 'MAXMP', 'MAXHP', 'DEFENSE'} <= {e.code for e in attributes}
        if not basics_complete and not has_a_progress:
            step = 'basic'
            outcome = run_basic_attribute_collection(
                context, execute, target=target, rules=rules, c_codes=c_codes,
                evidence_path=evidence_dir / 'basic.jsonl', stop_at=stop_at,
                max_rolls=remaining, evaluate_existing_candidate=True)
            consumed += outcome['rolls']
        else:
            step = 'a_collection'
            outcome = run_a_collection(
                context, execute, target=target, rules=rules, c_codes=c_codes,
                evidence_path=evidence_dir / 'a.jsonl', stop_at=stop_at,
                max_consumptions=remaining, target_ratio=target_ratio,
                fast_observation=fast_observation, scroll_profile=profile,
                initial_snapshot=current if current.get('is_wash') is True else None,
                evaluate_existing_candidate=True)
            consumed += outcome['consumed']
        steps.append({'step': step, 'result': outcome})
        current = outcome['snapshot']
        if outcome['status'] != 'complete':
            return result(outcome['status'], outcome.get('stop_reason', f'{step}_paused'))
