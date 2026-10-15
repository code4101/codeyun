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
    if card.get('type') in {46, 79}:
        semantics['type_name'] = card.get('type_name')
    # Only a non-empty native condition is identity, so cards without one keep
    # their pre-field fingerprints and saved findings/questions stay bound.
    if card.get('use_condition'):
        semantics['use_condition'] = str(card['use_condition'])
    return sha256(json.dumps(semantics, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def find_storage_bag_catalog_purpose_finding(findings, card):
    """Return ``(key, finding)`` for the card's exact version, else its pre-field version.

    A finding saved before ``use_condition`` reached catalog cards remains the
    same native text/type version; no other fingerprint drift is accepted.
    """
    if not card.get('id'):
        return None, None
    keys = [storage_bag_catalog_purpose_fingerprint(card)]
    if card.get('use_condition'):
        keys.append(storage_bag_catalog_purpose_fingerprint({**card, 'use_condition': None}))
    for key in keys:
        if key in (findings or {}):
            return key, findings[key]
    return None, None


def parse_storage_bag_item_use_condition(text):
    """Parse ``Item.useCondition`` with the client ``GameUtil.CheckCondition`` grammar.

    ``;`` separates OR branches and ``,`` separates AND clauses inside one
    branch; each clause is ``[CLIENT|]Type|value``. ``ActivitybaseId|<base>_<state>``
    requires that native activity base ID to be joinable in exactly that native
    state. Activity IDs/states are retained verbatim, never named or inferred.
    ``activity_bound`` means every branch requires an activity; any branch
    without one, or any malformed clause, leaves the item not activity-bound.
    """
    raw = str(text or '').strip()
    if not raw:
        return None
    branches, malformed = [], False
    for branch in raw.split(';'):
        clauses = []
        for clause in branch.split(','):
            client = clause.startswith('CLIENT|')
            parts = clause[len('CLIENT|'):].split('|') if client else clause.split('|')
            if len(parts) != 2 or not parts[0] or not parts[1]:
                malformed = True
                clauses.append({'raw': clause, 'type': None})
                continue
            row = {'raw': clause, 'type': parts[0], 'value': parts[1]}
            if client:
                row['client_only'] = True
            if parts[0] == 'ActivitybaseId':
                match = re.fullmatch(r'(\d+)_(-?\d+)', parts[1])
                if match:
                    row.update(activity_base_id=int(match[1]), native_state=int(match[2]))
                else:
                    malformed = True
            clauses.append(row)
        branches.append(clauses)
    bound = not malformed and all(any('activity_base_id' in c for c in b) for b in branches)
    result = {'raw': raw, 'semantics': 'any_of_all_of', 'any_of': branches,
              'status': 'malformed' if malformed else ('activity_bound' if bound else 'not_activity_bound')}
    if bound:
        result['activity_base_ids'] = sorted({c['activity_base_id'] for b in branches
                                              for c in b if 'activity_base_id' in c})
    return result


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
    # The native use gate is traceable evidence only: it never upgrades a
    # purpose, completes a route or authorizes use. Absent/empty stays absent.
    condition = parse_storage_bag_item_use_condition(card.get('use_condition'))
    if condition:
        result['use_condition'] = condition
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
    if item_type == 999 and sub == 1:
        if '飞升时' in description:
            return known('ascension_material', description)
        if '可自由在时间之海中穿梭' in description:
            return known('travel_access', description)
        if '消耗后可提升【造型' in description:
            return known('cosmetic_upgrade', description)
    if item_type == 1 and re.search(r'凭此令牌可在[^\n]+进出', effect):
        return known('area_access_token', effect)
    if item_type == 1 and re.search(r'只可在当期[^\n]*祈愿中使用', description):
        return known('prayer_event_token', description, 'reserve')
    if (sub == 1 and {46: '云梦论剑', 79: '仙缘道具'}.get(item_type) == card.get('type_name')
            and item_type in {46, 79} and '指定挑战' in description
            and re.fullmatch(r'增加\d+次(?:隐身)?狙击次数', effect)):
        result['declared_activity_type'] = card['type_name']
        return known('activity_targeted_challenge', description + '\n' + effect, 'reserve')
    # Native descriptions establish retention, never a consumption or scoring
    # policy. Restrict this route to consumable types so a permanent artifact's
    # passive activity bonus cannot be mistaken for a temporary battle buff.
    text = description + '\n' + effect
    if item_type in {1, 34, 46, 79}:
        activities = [name for name in ('灵脉', '云梦试剑', '仙缘夺魁',
            '灵装化道', '魔道入侵', '虚天殿') if name in text]
        if activities and any(word in text for word in ('使用', '挑战', '狙击', '击败', '强化', '悬赏')):
            result['declared_activities'] = activities
            return known('activity_consumable_candidate', text, 'reserve')
    # An explicit collection/conversion sentence proves a material purpose;
    # the destination's prayer score and the GUI synthesis adapter remain
    # separate, unverified requirements.
    if (item_type == 5 and sub in {1, 7, 8, 78, 81}) or (item_type == 81 and sub == 81):
        conversion = re.search(r'(?:集齐|收集)\s*[0-9]+[^\n]*?(?:合成|合为|拼合|研习)[^\n]*', text)
        if conversion:
            return known('collection_conversion_material', conversion.group(), 'reserve')
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
