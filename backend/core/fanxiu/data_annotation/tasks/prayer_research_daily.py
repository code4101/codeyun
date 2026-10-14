"""每日未知箱研究：重新发现→单箱原子测量→重新发现，不保存动作游标。"""
from .prayer_research_discovery import discover_prayer_box_research
from .prayer_box_experiment import sample_exclusive_alchemy_box, sample_exclusive_body_box

STAGE_ID = 'prayer-research-daily'
# Re-evaluate unknown candidates under the native detail/use-instance guards.
# Measured experiments remain authoritative and must never be opened again.
STAGE_VERSION = '4'


def research_unknown_prayer_boxes(context):
    """炼丹专属 fixed/random；锻体每次至多一个专属固定或随机箱。

    已知估计直接复用；其他类别/自选/外箱保留明确待研发事实。没有
    未知实物时只验收无项路径，不能宣称未知实物分支已经真实验收。
    未归因、封顶或跨轮动作不自动重测；异常停止本组件及后续消费。
    """
    measured = []
    while True:
        plan = yield from discover_prayer_box_research(context)
        if not plan['unknown']:
            return {**plan, 'outcome': 'research_complete' if plan['research_complete'] else 'research_deferred',
                    'measured': measured, 'real_unknown_branch_verified': bool(measured)}
        body = plan['prayer_category'] == '淬体'
        item = (next((row for row in plan['unknown']
                      if row['resource_categories'] == ['淬体']
                      and row['operation_kind'] in ('fixed', 'random') and not row['choice_key']), None)
                if body else plan['unknown'][0])
        if item is None:
            return {**plan, 'outcome': 'research_deferred', 'measured': measured,
                    'reason': 'waiting_verified_atomic_adapter', 'real_unknown_branch_verified': bool(measured)}
        if any(row['base_id'] == item['base_id'] for row in measured):
            raise RuntimeError('已保存原子样本仍被发现为未知，停止重复消费并核对统计链')
        if not body and (plan['prayer_category'] != '炼丹' or item['resource_categories'] != ['炼丹']
                or item['operation_kind'] not in ('fixed', 'random') or item['choice_key']):
            return {**plan, 'outcome': 'research_deferred', 'measured': measured,
                    'reason': 'waiting_verified_atomic_adapter', 'real_unknown_branch_verified': bool(measured)}
        sampler = sample_exclusive_body_box if body else sample_exclusive_alchemy_box
        distribution = {'operation_kind':item['operation_kind']} if body else {}
        result = yield from sampler(context, base_id=item['base_id'],
            expected_catalog_fingerprint=item['catalog_fingerprint'], **distribution)
        if result.get('outcome') in {'preserve_experiment_space', 'research_deferred', 'already_measured'}:
            return {**plan, 'outcome': 'research_deferred', 'measured': measured,
                    'reason': result['outcome'], 'real_unknown_branch_verified': bool(measured)}
        if result.get('outcome') != 'recorded' or not result.get('experiment_saved'):
            raise RuntimeError(f"未知箱原子研究未取得可用样本，停止后续动作：{result}")
        experiment = result['experiment']
        measured.append({'base_id': item['base_id'], 'action_key': experiment['action_key']})
        if experiment['prayer_after']['round_score_gap'] == 0:
            raise RuntimeError('未知箱样本封顶，已保留真实消费事实，禁止重复测量')
        if body:
            refreshed = yield from discover_prayer_box_research(context)
            if any(row['base_id'] == item['base_id'] for row in refreshed['unknown']):
                raise RuntimeError('锻体已保存样本仍为未知，禁止重复消费')
            return {**refreshed,
                    'outcome': 'research_complete' if refreshed['research_complete'] else 'research_deferred',
                    'measured': measured, 'reason': 'one_body_sample_per_run',
                    'real_unknown_branch_verified': True}
