"""成长基金领取策略；购买状态是门禁，祈愿周期按资源而非菜单判定。"""
from backend.core.fanxiu.mail.policy import MAIL_PROTECTED_RESOURCE_NAMES_BY_CATEGORY


def reward_prayer_categories(metadata):
    name = metadata.get('item_name', '')
    if not name or name.startswith('未知道具'):
        raise ValueError('成长基金奖励名称未知')
    # 使用已有祈愿资源词典；宝匣描述也纳入，避免漏掉装有精魄的淬体箱。
    text = name + ' ' + metadata.get('description', '')
    return {category for category, names in MAIL_PROTECTED_RESOURCE_NAMES_BY_CATEGORY.items()
            if any(resource in text for resource in names)}


def plan_growth_funds(state, prayer):
    """返回可批领、按周留存和混合档；已领取及未购买付费列均不参与。

    bulk 会领掉整档全部达标奖励，故所有未领候选必须满足本周策略才放行。
    混合档不能借用 bulk 偷领被保护资源，交单项领取或明确报错。
    """
    order = {r['id']: i for i, r in enumerate(state['categories'])}
    claim, deferred, mixed = [], [], []
    bought = set(state['bought'])
    for fund in sorted(state['funds'], key=lambda r: (order.get(r['type'], 999), r['sort'])):
        if not fund['claimable']:
            continue
        if fund['type'] not in order:
            raise RuntimeError('可领基金不在已加载菜单内')
        decisions = []
        for reward in state['rewards']:
            if reward['fundId'] != fund['fundId']:
                continue
            for track in ('free', 'paid'):
                if track == 'paid' and fund['fundId'] not in bought:
                    continue
                if reward['id'] in state['claimed_' + track]:
                    continue
                categories = set().union(*(reward_prayer_categories(state['items'][item['item_id']])
                                           for item in reward[track]))
                decisions.append({'reward_id': reward['id'], 'track': track,
                                  'allowed': categories <= {prayer}, 'prayers': sorted(categories)})
        if not decisions:
            raise RuntimeError('基金可领状态与未领记录矛盾')
        row = {**fund, 'decisions': decisions}
        if all(d['allowed'] for d in decisions):
            claim.append(row)
        elif not any(d['allowed'] for d in decisions):
            deferred.append(row)
        else:
            mixed.append(row)
    return {'claim': claim, 'deferred': deferred, 'mixed': mixed}
