"""每日未知箱研究：重新发现→单箱原子测量→重新发现，不保存动作游标。"""
from .prayer_research_discovery import discover_prayer_box_research
from .prayer_box_experiment import sample_exclusive_alchemy_box

STAGE_ID = 'prayer-research-daily'
STAGE_VERSION = '1'


def research_unknown_prayer_boxes(context):
    """目前自动执行已验收的炼丹专属 fixed/random 单箱原子入口。

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
        item = plan['unknown'][0]
        if any(row['base_id'] == item['base_id'] for row in measured):
            raise RuntimeError('已保存原子样本仍被发现为未知，停止重复消费并核对统计链')
        if (plan['prayer_category'] != '炼丹' or item['resource_categories'] != ['炼丹']
                or item['operation_kind'] not in ('fixed', 'random') or item['choice_key']):
            return {**plan, 'outcome': 'research_deferred', 'measured': measured,
                    'reason': 'waiting_verified_atomic_adapter', 'real_unknown_branch_verified': bool(measured)}
        result = yield from sample_exclusive_alchemy_box(context, base_id=item['base_id'],
            expected_catalog_fingerprint=item['catalog_fingerprint'])
        if result.get('outcome') == 'preserve_experiment_space':
            return {**plan, 'outcome': 'research_deferred', 'measured': measured,
                    'reason': 'preserve_experiment_space', 'real_unknown_branch_verified': bool(measured)}
        if result.get('outcome') != 'recorded' or not result.get('experiment_saved'):
            raise RuntimeError(f"未知箱原子研究未取得可用样本，停止后续动作：{result}")
        experiment = result['experiment']
        measured.append({'base_id': item['base_id'], 'action_key': experiment['action_key']})
        if experiment['prayer_after']['round_score_gap'] == 0:
            raise RuntimeError('未知箱样本封顶，已保留真实消费事实，禁止重复测量')
