export const subtaskStatusLabel = (status: string) => ({
  running: '运行中', completed: '已完成', retained: '已核对', unavailable: '不可用',
  scheduled: '尚未到期', due: '待执行', pending: '待处理', retry_wait: '等待重试',
  blocked: '阻塞', error: '失败', pending_validation: '待验收', expired: '已过期',
  superseded: '已被后续轮次替代', not_applicable: '本周期不适用', empty: '暂无子任务',
  settled: '本轮已结束',
}[status] || status);

export const subtaskProgress = (counts: Record<string, number>) => {
  const total = Object.entries(counts).reduce((sum, [status, n]) =>
    sum + (['not_applicable', 'superseded', 'expired'].includes(status) ? 0 : n), 0);
  const complete = (counts.completed || 0) + (counts.retained || 0);
  return total ? `${complete}/${total} 完成` : '';
};

/** Same-day times stay short; the tooltip/detail keeps the full timestamp. */
export const subtaskTime = (value?: string | null, businessDate?: string) => {
  if (!value) return '—';
  const text = value.replace('T', ' ');
  return text.slice(0, 10) === businessDate ? text.slice(11, 16) : text.slice(5, 16);
};

export const subtaskSummary = (counts: Record<string, number>) => {
  const omitted = ['not_applicable', 'superseded', 'expired'].reduce((sum, s) => sum + (counts[s] || 0), 0);
  const total = Object.values(counts).reduce((sum, n) => sum + n, 0) - omitted;
  const complete = (counts.completed || 0) + (counts.retained || 0);
  const details = ['running', 'error', 'blocked', 'retry_wait', 'due', 'pending', 'pending_validation', 'unavailable', 'not_applicable', 'superseded', 'expired']
    .filter(status => counts[status])
    .map(status => `${subtaskStatusLabel(status)} ${counts[status]}`);
  return [`已完成 ${complete}/${total}`, ...details].join(' · ');
};
