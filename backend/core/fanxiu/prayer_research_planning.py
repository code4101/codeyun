"""每日祈愿研究准入：当前库存 × 当前奖励图 × 同条件已有估计。

本模块只规划未知项，不操作游戏。发现、研究执行、周日补分分别拥有
自己的完成判据；研究计划不能授予开箱权限，也不能证明生产已完成。
"""
from collections.abc import Mapping
import math
from typing import Any


def prayer_research_category_allowed(prayer_category: str, resource_categories: list[str]) -> bool:
    """炼丹研究专属箱；其他周允许含本周资源的复合箱。

    这里只核对类别，完整奖励图、操作适配与原子取证另有准入条件。
    发现与显式实验共用规则，不能出现已发现的合法类别被实验层拒绝。
    """
    return (prayer_category in {'仙花', '洗灵', '淬体', '灵兽', '炼丹'}
            and prayer_category in resource_categories
            and (prayer_category != '炼丹' or resource_categories == ['炼丹']))


def plan_unknown_prayer_boxes(
    *, prayer_category: str, research: Mapping[str, Any],
    inventory: list[Mapping[str, Any]],
    reward_modifier_fingerprints: Mapping[int, str] | None = None,
) -> dict[str, Any]:
    """从已核实的当前目录规则和完整库存中筛选本周未知期望。

    inventory 提供唯一 base_id、quantity、catalog_fingerprint。提供方先
    将新物品的完整奖励图分类为 rules，不能只传旧研究候选而漏掉新箱。
    既有同版本/类别/选项/奖励条件的有限非负样本均值直接复用，包括
    已观察的零均值；零均值仍不证明全奖池零收益，也不授予补分资格。
    旧目录已有估计而当前指纹变化时，先等待目录差异核对，不能仅因
    元数据指纹不同就重复开箱。土灵缺少当前奖励条件时等待取证；
    条件变更时原估计不覆盖新条件。
    炼丹仅研究专属类。其他周保留复合箱、自选未测选项；外箱与不完整
    奖励图单独返回待处理事实，由适配器决定能否执行，不静默消失。
    """
    if prayer_category not in {'仙花', '洗灵', '淬体', '灵兽', '炼丹'}:
        raise ValueError('未知祈愿类别')
    stock = {}
    for row in inventory:
        base, quantity = row.get('base_id'), row.get('quantity')
        if (type(base) is not int or base <= 0 or base in stock
                or type(quantity) is not int or quantity < 0
                or not row.get('catalog_fingerprint')):
            raise ValueError('研究计划需要唯一物品、非负库存及当前目录版本')
        stock[base] = row
    modifiers = reward_modifier_fingerprints or {}
    summaries = research.get('summaries') or []
    isolated = list(research.get('excluded_experiments') or []) + list(research.get('round_capped_experiments') or [])
    isolated += [row['experiment'] for row in research.get('quarantined_experiments') or []]
    choices = {r['base_id']: r for r in research.get('choice_plans') or []}
    result = dict(prayer_category=prayer_category, unknown=[], known=[], deferred=[])
    seen = set()
    for rule in research.get('rules') or []:
        base = rule['base_id']
        if base in seen:
            raise ValueError('研究目录包含重复物品')
        seen.add(base)
        owned = stock.get(base)
        categories = rule.get('resource_categories') or []
        if not owned or not owned['quantity'] or not prayer_research_category_allowed(prayer_category, categories):
            continue
        entry = dict(base_id=base, name=rule.get('name'), quantity=owned['quantity'],
                     catalog_fingerprint=rule['catalog_fingerprint'],
                     operation_kind=rule.get('operation_kind'),
                     resource_categories=categories)
        reason = None
        if owned['catalog_fingerprint'] != rule['catalog_fingerprint']:
            reason = 'waiting_current_catalog'
        elif rule.get('classification_complete') is not True:
            reason = 'waiting_complete_reward_graph'
        elif base == 39032101 and not modifiers.get(base):
            reason = 'waiting_current_reward_modifier'
        if reason:
            result['deferred'].append({**entry, 'reason': reason})
            continue
        options = ['']
        if rule.get('operation_kind') == 'choice':
            choice = choices.get(base) or {}
            options = [o['choice_key'] for o in choice.get('options') or []
                       if o.get('prayer_category') == prayer_category]
            if choice.get('catalog_fingerprint') != rule['catalog_fingerprint'] or not options:
                result['deferred'].append({**entry, 'reason': 'waiting_current_choice_options'})
                continue
        for option in options:
            item = {**entry, 'choice_key': option,
                    'reward_modifier_fingerprint': modifiers.get(base, '')}
            matches = [s for s in summaries
                       if s.get('base_id') == base
                       and s.get('prayer_category') == prayer_category
                       and s.get('catalog_fingerprint') == rule['catalog_fingerprint']
                       and (s.get('choice_key') or '') == option
                       and (s.get('reward_modifier_fingerprint') or '') == modifiers.get(base, '')
                       and s.get('eligibility') != 'waiting_verified_reward_context'
                       and type(s.get('mean_score_per_box')) in (int, float)
                       and math.isfinite(s['mean_score_per_box'])
                       and s['mean_score_per_box'] >= 0]
            if len(matches) > 1:
                raise ValueError('同条件收益摘要未合并')
            if matches:
                result['known'].append({**item, 'expected_score_per_box': matches[0]['mean_score_per_box'],
                                        'eligibility': matches[0].get('eligibility')})
            elif rule.get('is_outer_container'):
                result['deferred'].append({**item, 'reason': 'waiting_outer_container_attribution'})
            elif any(row.get('base_id') == base
                     and row.get('catalog_fingerprint') == rule['catalog_fingerprint']
                     and row.get('prayer_category') == prayer_category
                     and (row.get('choice_key') or '') == option for row in isolated):
                result['deferred'].append({**item, 'reason': 'waiting_isolated_experiment_review'})
            elif any(s.get('base_id') == base and s.get('prayer_category') == prayer_category
                     and (s.get('choice_key') or '') == option
                     and (s.get('reward_modifier_fingerprint') or '') == modifiers.get(base, '')
                     and s.get('catalog_fingerprint') != rule['catalog_fingerprint']
                     for s in summaries):
                result['deferred'].append({**item, 'reason': 'waiting_catalog_reconciliation'})
            else:
                result['unknown'].append(item)
    for rows in (result['unknown'], result['known'], result['deferred']):
        rows.sort(key=lambda r: (len(r['resource_categories']), r['base_id'], r.get('choice_key', '')))
    return result
