"""Conservative purpose routing from authoritative catalog text, without stock.

Purpose, reward-graph completeness and actual prayer scoring are orthogonal.
These findings never select/consume an item or create a current-stock discovery.
"""
from hashlib import sha256
import json
import re


def storage_bag_catalog_purpose_fingerprint(card):
    semantics = {k: card.get(k) for k in ('id', 'name', 'type', 'sub_type', 'description',
                                         'effect_description', 'effect_value', 'can_use',
                                         'definition_defaults_validated', 'backpack')}
    return sha256(json.dumps(semantics, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def classify_storage_bag_catalog_purpose(card, *, reward_rule=None, cards_by_id=None):
    """Return a quoted-source purpose; references to buffs do not reclassify artifacts.

    Resource descriptions establish a retention candidate, not a full pool or
    scoring eligibility. Unresolved box purposes remain owner questions; missing
    configuration fields remain technical research instead of basic user questions.
    """
    clean = lambda s: re.sub(r'<[^>]*>', '', str(s or '')).strip()
    description = clean(card.get('description'))
    effect = clean(card.get('effect_description'))
    item_type, sub = card.get('type'), card.get('sub_type')
    result = {'base_id': int(card['id']), 'name': card.get('name'),
              'fingerprint': storage_bag_catalog_purpose_fingerprint(card),
              'purpose_status': 'unknown', 'purpose_complete': False,
              'scope': 'catalog_only', 'prayer_eligibility': 'unverified',
              'reward_graph_complete': bool((reward_rule or {}).get('classification_complete')),
              'source': card.get('metadata_source', 'catalog'), 'evidence': ''}
    def known(route, quote, purpose='independent_lifecycle'):
        result.update(route=route, evidence=quote, purpose_status=purpose, purpose_complete=True)
        return result
    if item_type == 1 and sub == 35 and effect.startswith('可用于神炼'):
        return known('artifact_refinement', effect.split('\n')[0])
    if item_type == 5 and sub == 6 and '点击使用前往认主法宝' in effect:
        return known('artifact_ownership', '点击使用前往认主法宝')
    if item_type == 5 and sub == 76 and '补全星海法则' in description:
        return known('star_sea_law', description.split('\n')[0])
    if item_type == 1 and '万域问鼎中挑战对手' in description and '倍个人积分及域界道箓' in effect:
        return known('wanyu_challenge_buff', description + '\n' + effect)
    if item_type == 8 and sub == 6:
        return known('cosmetic_unlock', f"type=8/sub_type=6，{description}")
    if item_type == 5 and sub == 49:
        result.update(route='activity_material_candidate', purpose_status='reserve',
                      evidence=f"Item type=5/sub_type=49，canUse={card.get('can_use')}，effectValue={card.get('effect_value')}；{description}",
                      needed='仍需已加载活动配置或真实协议绑定具体用途；不凭effect数字或名称绑定活动')
        return result
    if item_type in (2, 21):
        categories = (reward_rule or {}).get('resource_categories') or []
        if categories:
            result['declared_categories'] = list(categories)
            return known('prayer_resource_candidate', '已读取奖励图资源类别：' + '、'.join(categories), 'reserve')
        for token, category in (('装有洗灵道具', '洗灵'), ('装有炼体法相道具', '炼体法相')):
            if token in description:
                result['declared_categories'] = [category]
                return known('activity_resource_candidate', description, 'reserve')
        descendants, pending, seen = [], [(cards_by_id or {}).get(str(card['id']), card)], set()
        while pending and len(seen) < 512:
            parent = pending.pop()
            if parent['id'] in seen:
                continue
            seen.add(parent['id'])
            for reward in parent.get('optional_gift_rewards', []):
                child = (cards_by_id or {}).get(str(reward['id']))
                if child:
                    descendants.append(child)
                    pending.append(child)
        conversions = [f"{c['name']}：{clean(c.get('description'))}" for c in descendants
                       if '可转化为其中之一' in clean(c.get('description'))]
        if conversions:
            result['choice_candidates'] = [c['name'] for c in descendants if '可转化为其中之一' in clean(c.get('description'))]
            result['needs_owner_choice'] = item_type == 21
            return known('resource_conversion_candidate', '\n'.join(conversions), 'reserve')
        gameplay = [f"{c['name']}：{clean(c.get('description'))}\n{clean(c.get('effect_description'))}" for c in descendants
                    if any(word in clean(c.get('description')) + clean(c.get('effect_description'))
                           for word in ('虚天殿活动', '兽渊探秘活动', '洞天福地的兑换宝阁'))]
        if gameplay:
            return known('gameplay_resource_proceeds', '\n'.join(gameplay), 'reserve')
        if result['reward_graph_complete'] and descendants:
            result['reward_names'] = sorted({c['name'] for c in descendants})
        result.update(route='choice_box' if item_type == 21 else 'ordinary_box_candidate',
                      purpose_status='await_owner',
                      evidence=description + '\n' + effect,
                      needed='选择用途/自选策略；目录类型和随机文案不授予消费')
        return result
    result.update(route='unresolved_metadata', evidence=f"type={item_type}/sub_type={sub}；{description}\n{effect}",
                  needed='补齐用途字段或明确业务入口，不按名称猜测，不询问用户可读配置基础问题')
    return result
