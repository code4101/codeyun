"""Pure validation of preserved native Scheduler success evidence."""
from datetime import datetime


def project_preserved_scheduler_success(task, snapshot, *, next_time):
    """Reject stale, inferred, mismatched or unjournaled terminal recovery."""
    if not isinstance(snapshot, dict):
        raise ValueError('缺少原生终态快照')
    attempt = snapshot.get('scheduler_attempt_id')
    if (not attempt or snapshot.get('scheduler_task_id') != task.get('id')
            or snapshot.get('scheduler_terminal_result') != 'success'
            or snapshot.get('status') != 'success' or snapshot.get('running')
            or snapshot.get('current_scene') != 34):
        raise ValueError('原生成功终态身份或安全边界不符')
    start = datetime.fromtimestamp(float(snapshot['started_at'])).strftime('%Y-%m-%d %H:%M:%S')
    end = float(snapshot['scheduler_terminal_at'])
    if (task.get('started_at') != start or task.get('last_run_at') != start
            or end < float(snapshot['started_at'])
            or end != float(snapshot['finished_at'])
            or datetime.strptime(next_time, '%Y-%m-%d %H:%M:%S').timestamp() <= end):
        raise ValueError('原生终态已过期、时间或新触发不符')
    same_running = task.get('last_result') == 'running' and task.get('attempt_id') == attempt
    invalidated = (task.get('last_result') == 'error' and not task.get('attempt_id')
                   and str(task.get('last_message') or '').startswith('先前 Cell/Kernel 执行尝试已作废；'))
    if not (same_running or invalidated):
        raise ValueError('当前作业不是同一待回收attempt，拒绝覆盖')
    witnesses = [row for row in snapshot.get('logs') or []
                 if row.get('attempt_id') == attempt and row.get('item_id') == task['id']
                 and str(row.get('message') or '').endswith('下次 ' + next_time)]
    if not witnesses:
        raise ValueError('缺少原attempt明确下次触发凭证')
    return {'last_result': 'success',
            'last_message': snapshot.get('scheduler_terminal_message') or '已回收原生成功终态',
            'finished_at': datetime.fromtimestamp(end).strftime('%Y-%m-%d %H:%M:%S'),
            'next_time': next_time, 'attempt_id': None, 'attempt_original_trigger': None,
            'attempt_kernel_generation': None, 'attempt_kernel_idle_since': None}
