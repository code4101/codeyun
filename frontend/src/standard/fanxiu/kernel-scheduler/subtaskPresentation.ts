import type { FanxiuSubtaskNode } from '@/api/fanxiu/scheduler';

export const subtaskStatusLabel = (status: string) => ({
  running: '运行中', completed: '已完成', retained: '已核对', unavailable: '不可用',
  scheduled: '尚未到期', due: '待执行', pending: '待处理', retry_wait: '等待重试',
  blocked: '阻塞', error: '失败', pending_validation: '待验收', expired: '已过期',
  superseded: '已被后续轮次替代', not_applicable: '本周期不适用', empty: '暂无子任务',
  settled: '本轮已结束',
}[status] || status);

/** Count structural leaves, independent of execution and completion status. */
export const subtaskLeafCount = (nodes: FanxiuSubtaskNode[]): number => nodes.reduce(
  (sum, node) => sum + (node.kind === 'subtask' ? 1 : subtaskLeafCount(node.children)), 0,
);

/** Same-day times stay short; the tooltip/detail keeps the full timestamp. */
export const subtaskTime = (value?: string | null, businessDate?: string) => {
  if (!value) return '—';
  const text = value.replace('T', ' ');
  return text.slice(0, 10) === businessDate ? text.slice(11, 16) : text.slice(5, 16);
};
