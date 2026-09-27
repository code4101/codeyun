"""洗灵消耗任务：以本期 QuestMgr 成员选择梯度，绝不绑定活动/任务 ID。"""
import json
import re

from ..catalog.resources import resolve_fanxiu_export_root
from .daily_task_rewards import (
    TaskRewardDomainSpec, read_activity_task_reward_snapshots,
    read_task_reward_spec_fast_snapshot,
)


def discover_xiling_task_spec(activity_id: int) -> TaskRewardDomainSpec:
    """只读已加载任务和正式配置；静态保留但本期未发放的档位不计入。"""
    raw = json.loads((resolve_fanxiu_export_root() / 'parsed_configs/ActiveTask/rows.json').read_text(encoding='utf-8'))
    rows = raw if isinstance(raw, list) else raw.get('rows', raw)
    rows = list(rows.values()) if isinstance(rows, dict) else rows
    shared = read_activity_task_reward_snapshots((), include_activity_tasks=True)
    if not shared.get('ok') or not shared.get('available'):
        raise RuntimeError(f'洗灵任务不可读：{shared.get("reason")}')
    live = {int(e['taskId']) for e in shared['task_entries']} | set(shared['finished_task_ids'])
    selected = []
    for row in rows:
        if int(row.get('activityId', 0)) != activity_id or int(row['id']) not in live:
            continue
        condition = row.get('finishCondition', [])
        match = re.fullmatch(r'ConsumeItemsAccept\|14000002_(\d+)', condition[0]) if len(condition) == 1 else None
        if match:
            selected.append((int(match[1]), int(row['id'])))
    selected.sort()
    if not selected or len({target for target, _ in selected}) != len(selected):
        raise RuntimeError('本期洗灵消耗任务缺失或梯度歧义')
    return TaskRewardDomainSpec(f'xiling_{activity_id}', '洗灵证武', activity_id,
                               tuple(i for _, i in selected), 'ConsumeItemsAccept',
                               tuple(t for t, _ in selected))


def read_xiling_task_progress(spec: TaskRewardDomainSpec) -> dict:
    """每次动作前后快读完整本期任务；达标与已领取分别返回。"""
    value = read_task_reward_spec_fast_snapshot(spec, include_task_entries=True)
    if not value.get('ok') or not value.get('complete'):
        raise RuntimeError(f'洗灵任务观察不完整：{value}')
    targets = dict(zip(spec.task_ids, spec.thresholds))
    for row in value['task_entries']:
        conditions = row['progressList']
        if len(conditions) != 1 or int(conditions[0]['target']) != targets[int(row['taskId'])]:
            raise RuntimeError('本期洗灵任务门槛与配置不一致')
    pending = set(value['pending_task_ids'])
    progress = {int(p['progress']) for row in value['task_entries']
                if int(row['taskId']) in pending for p in row['progressList']}
    if pending and len(progress) != 1:
        raise RuntimeError(f'洗灵未完成档位进度不一致：{progress}')
    value.pop('finished_task_ids', None)  # 不向调用者重复输出其它玩法的已领任务。
    return {**value, 'activity_progress': next(iter(progress)) if pending else max(spec.thresholds),
            'task_target': max(spec.thresholds), 'goal_reached': not pending}
