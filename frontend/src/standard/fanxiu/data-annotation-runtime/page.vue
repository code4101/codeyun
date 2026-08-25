<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ElMessage, ElMessageBox } from 'element-plus';
import { QuestionFilled } from '@element-plus/icons-vue';
import { taskStore } from '@/store/taskStore';
import SchedulerTimeSequenceDialog from './SchedulerTimeSequenceDialog.vue';
import {
  ensureFanxiuDataAnnotationDoctorWatch,
  getFanxiuDataAnnotationDoctorWatchLatest,
  getFanxiuBehaviorTreeRuntimeCellLogs,
  getFanxiuBehaviorTreeRuntimeStatus,
  getFanxiuInfoWindowStatus,
  getFanxiuDataAnnotationSchedulerPlan,
  getFanxiuDataAnnotationSchedulerTasks,
  getFanxiuGameStateInspectionStatus,
  runNowFanxiuDataAnnotationSchedulerTask,
  setFanxiuDataAnnotationSchedulerTaskNextTime,
  restartFanxiuBehaviorTreeRuntimeDevice,
  setFanxiuDataAnnotationSchedulerSettings,
  setFanxiuBehaviorTreeRuntimeBehaviorTree,
  setFanxiuBehaviorTreeRuntimeGuard,
  setFanxiuBehaviorTreeRuntimeGuardGroup,
  setFanxiuInfoWindowSettings,
  stopFanxiuBehaviorTreeRuntimeCurrentTask,
  type FanxiuDataAnnotationDoctorWatchLatestResponse,
  type FanxiuBehaviorTreeRuntimeCellLog,
  type FanxiuBehaviorTreeRuntimeLogEntry,
  type FanxiuBehaviorTreeRuntimeGuardItem,
  type FanxiuBehaviorTreeRuntimeStatus,
  type FanxiuDataAnnotationSchedulerPlanResponse,
  type FanxiuDataAnnotationSchedulerTaskItem,
  type FanxiuGameStateInspectionStatus,
  type FanxiuInfoWindowControlStatus,
  type FanxiuInfoWindowSettings,
} from '@/api/fanxiu';

const route = useRoute();
const router = useRouter();

const entryId = ref(String(route.query.entry_id || ''));
const runtimeStatus = ref<FanxiuBehaviorTreeRuntimeStatus | null>(null);
const schedulerTasks = ref<FanxiuDataAnnotationSchedulerTaskItem[]>([]);
const schedulerPlan = ref<FanxiuDataAnnotationSchedulerPlanResponse | null>(null);
// PRODUCT CONTRACT: 游戏状态巡检是行为树 Runtime 页的固定一级能力，不是可被“精简 UI”删除的诊断装饰。
// 若调整布局，必须保留状态接口、周期刷新、巡检项和最近检查结果的可见 UI，并同步通过契约测试。
const gameStateInspection = ref<FanxiuGameStateInspectionStatus | null>(null);
const infoWindowStatus = ref<FanxiuInfoWindowControlStatus | null>(null);
const doctorWatchLatest = ref<FanxiuDataAnnotationDoctorWatchLatestResponse | null>(null);
const schedulerJobGroupEnabled = ref(true);
const cellLogs = ref<FanxiuBehaviorTreeRuntimeCellLog[]>([]);
const activeCellIndex = ref(0);
const logs = ref<FanxiuBehaviorTreeRuntimeLogEntry[]>([]);
const loading = ref(false);
const logsLoading = ref(false);
const actionLoading = ref('');
const contextMenu = ref({
  visible: false,
  x: 0,
  y: 0,
  scope: '',
  itemId: '',
  title: '',
  task: null as FanxiuDataAnnotationSchedulerTaskItem | null,
});
const schedulerTimeDialog = ref({
  visible: false,
  task: null as FanxiuDataAnnotationSchedulerTaskItem | null,
  nextTime: '',
});
const schedulerTimeSequenceDialog = ref<InstanceType<typeof SchedulerTimeSequenceDialog> | null>(null);
let pollTimer: number | null = null;
let pollTick = 0;
let polling = false;
let doctorEnsurePromise: Promise<void> | null = null;
let lastDoctorEnsureAt = 0;
const CELL_LOG_LIMIT = 24;
// Keep the runtime page on a summary-sized log slice; full history lives on the logs page.
const CELL_LOG_ENTRY_LIMIT = 200;
const CELL_LOG_POLL_LIMIT = 1;
const CELL_LOG_POLL_ENTRY_LIMIT = 80;
const SLOW_STATE_POLL_TICKS = 10;
const DOCTOR_ENSURE_COOLDOWN_MS = 30000;

const devices = computed(() => taskStore.devices);
const behaviorTreeEnabled = computed(() => runtimeStatus.value?.behavior_tree_enabled ?? true);
type KernelDisplayState = 'loading' | 'disabled' | 'enabled' | 'error';
const kernelDisplayState = computed<KernelDisplayState>(() => {
  if (runtimeStatus.value === null) return 'loading';
  if (!behaviorTreeEnabled.value) return 'disabled';
  if (runtimeStatus.value.kernel?.alive === true) return 'enabled';
  if (runtimeStatus.value.kernel?.alive === false) return 'error';
  return 'loading';
});
const guardGroupEnabled = computed(() => runtimeStatus.value?.guard_group_enabled ?? true);
const guardItemEnabled = (guardId: string) => Boolean(runtimeStatus.value?.guard_items?.[guardId]?.enabled);
const machineName = 'codepc_mf';
const runtimeMessage = computed(() => runtimeStatus.value?.message || '-');
const kernelMessageText = computed(() => String(runtimeStatus.value?.kernel?.execution_state || 'dead'));
const kernelToggleText = computed(() => ({
  loading: '加载中',
  disabled: '内核关闭',
  enabled: '内核开启',
  error: '内核异常',
}[kernelDisplayState.value]));
const kernelToggleTitle = computed(() => ({
  loading: '正在读取内核状态',
  disabled: '点击打开内核',
  enabled: '点击关闭内核',
  error: '内核子进程已停止，点击关闭后可重新开启',
}[kernelDisplayState.value]));
const runtimeSecondaryMessage = computed(() => {
  const message = runtimeMessage.value.trim();
  if (!message || message === '-') return '';
  return message === kernelMessageText.value.trim() ? '' : message;
});
const schedulerOwnerKey = computed<'engineering' | 'ai'>(() => (schedulerJobGroupEnabled.value ? 'engineering' : 'ai'));
const schedulerOwnerOptions = [
  { label: 'AI', value: 'ai' },
  { label: '工程', value: 'engineering' },
];
const schedulerOwnerTitle = computed(() => (
  schedulerJobGroupEnabled.value
    ? '工程调度：自动提交到期 Cell'
    : 'AI 调度：工程暂停自动提交'
));
const gameStateInspectionStatusText = computed(() => {
  const status = gameStateInspection.value?.status || '';
  if (status === 'paused') return '已暂停';
  if (status === 'starting') return '启动中';
  if (status === 'error' || status === 'unavailable') return '异常';
  return gameStateInspection.value?.probe_count ? '运行中' : '运行中，暂无巡检项';
});
const gameStateInspectionStatusClass = computed(() => (
  ['error', 'unavailable'].includes(gameStateInspection.value?.status || '')
    ? 'is-error'
    : gameStateInspection.value?.status === 'paused'
      ? 'is-paused'
      : 'is-running'
));
const gameStateInspectionIntervalText = computed(() => {
  const seconds = Number(gameStateInspection.value?.interval_seconds || 60);
  return seconds % 60 === 0 ? `每 ${seconds / 60} 分钟` : `每 ${seconds} 秒`;
});
const gameStateInspectionProbeText = computed(() => (
  (gameStateInspection.value?.probes || [])
    .map((probe) => String(probe.label || probe.id || '').trim())
    .filter(Boolean)
    .join('、')
  || '暂无'
));
const defaultInfoWindowSettings: FanxiuInfoWindowSettings = {
  enabled: true,
  active_recognition: false,
  show_scene_id: true,
  show_scene_score: true,
  show_scene_identity_shapes: true,
  show_all_shapes: false,
};
const infoWindowSettings = computed(() => infoWindowStatus.value?.settings || defaultInfoWindowSettings);
const infoWindowStatusText = computed(() => {
  if (!infoWindowSettings.value.enabled) return '已关闭';
  if (infoWindowStatus.value?.renderer?.visible) return '显示中';
  if (infoWindowStatus.value?.renderer?.running) return '等待 MuMu';
  return '启动中';
});
const infoWindowStatusClass = computed(() => {
  if (!infoWindowSettings.value.enabled) return 'is-paused';
  return infoWindowStatus.value?.renderer?.visible ? 'is-running' : '';
});
const guardItems = computed<FanxiuBehaviorTreeRuntimeGuardItem[]>(() => {
  const items = runtimeStatus.value?.guard_items || {};
  return Object.values(items).map((item) => ({
    ...item,
    id: item.id || '',
    label: item.label || item.id || '未命名守护',
    enabled: guardItemEnabled(item.id || ''),
    message: item.message || '-',
  }));
});
const guardGroupTitle = computed(() => (
  guardGroupEnabled.value
    ? '守护组已开启；启用的守护会参与常驻行为树'
    : '守护组已关闭；单个守护配置仍保留，但不会自动执行'
));
const isBusinessTask = (task: FanxiuDataAnnotationSchedulerTaskItem) => {
  if (['go_scene', 'hide_floating_window', 'maintenance_recovery'].includes(task.task_type)) return false;
  const label = task.label || '';
  if (/到.*#\d+|隐藏浮动窗|到世界|到设置页/.test(label)) return false;
  return true;
};

const taskTriggerValue = (task: FanxiuDataAnnotationSchedulerTaskItem) => {
  const exact = parseRuntimeTime(task.next_time || '');
  return exact?.getTime() ?? Number.POSITIVE_INFINITY;
};

const shouldShowBusinessTask = (task: FanxiuDataAnnotationSchedulerTaskItem) => {
  if (!isBusinessTask(task)) return false;
  return task.supported !== false;
};

const businessTasks = computed(() => schedulerTasks.value
  .filter(shouldShowBusinessTask)
  .sort((a, b) => (
    taskTriggerValue(a) - taskTriggerValue(b)
    || (Number(a.dispatch_order) > 0 ? Number(a.dispatch_order) : 10000)
      - (Number(b.dispatch_order) > 0 ? Number(b.dispatch_order) : 10000)
    || String(a.label || a.id).localeCompare(String(b.label || b.id), 'zh-CN')
  )));

const schedulerBlockingMessage = computed(() => {
  const blockers = schedulerPlan.value?.blocking_overlays || [];
  const blocker = blockers.find((item) => Boolean(item.blocking));
  return typeof blocker?.message === 'string' ? blocker.message : '';
});
const doctorSnapshot = computed(() => doctorWatchLatest.value?.snapshot || {});
const doctorSeverity = computed(() => String(doctorSnapshot.value.severity || ''));
const doctorSeverityText = computed(() => {
  const labels: Record<string, string> = {
    blocked: '阻塞',
    error: '错误',
    attention: '待执行',
    ok: '正常',
  };
  return labels[doctorSeverity.value] || '未巡检';
});
const doctorSeverityClass = computed(() => (
  ['blocked', 'error', 'attention', 'ok'].includes(doctorSeverity.value)
    ? `is-${doctorSeverity.value}`
    : 'is-unknown'
));
const doctorActionText = computed(() => {
  const actions = doctorSnapshot.value.action_required || [];
  return Array.isArray(actions) && actions.length ? String(actions[0]) : '';
});
const doctorFirstBlocker = computed(() => {
  const blockers = doctorSnapshot.value.blocked_by || [];
  return Array.isArray(blockers) && blockers.length && typeof blockers[0] === 'object'
    ? blockers[0] as Record<string, unknown>
    : null;
});
const doctorAnnotationTarget = computed(() => {
  const targets = doctorSnapshot.value.annotation_targets || [];
  return Array.isArray(targets) && targets.length && typeof targets[0] === 'object'
    ? targets[0]
    : null;
});
const doctorAnnotationTitle = computed(() => {
  const targetTitle = String(doctorAnnotationTarget.value?.title || '').trim();
  if (targetTitle) return targetTitle;
  const title = String(doctorFirstBlocker.value?.title || '').trim();
  return title === '游戏公告' || title === '灵祖奖励浮层' ? title : '';
});
const doctorSummaryText = computed(() => String(doctorSnapshot.value.summary || doctorWatchLatest.value?.message || ''));
const doctorHeartbeat = computed(() => doctorWatchLatest.value?.heartbeat || {});
const formatHeartbeatAge = (seconds: number) => {
  const safeSeconds = Number.isFinite(seconds) ? Math.max(0, seconds) : 0;
  if (safeSeconds < 60) return `${Math.round(safeSeconds)}s`;
  if (safeSeconds < 3600) {
    const minutes = safeSeconds / 60;
    const value = minutes < 10 ? Math.round(minutes * 10) / 10 : Math.round(minutes);
    return `${value}min`;
  }
  const hours = safeSeconds / 3600;
  const value = hours < 10 ? Math.round(hours * 10) / 10 : Math.round(hours);
  return `${value}hour`;
};
const doctorHeartbeatText = computed(() => {
  const heartbeat = doctorHeartbeat.value;
  if (!doctorWatchLatest.value?.exists) return '';
  if (heartbeat.active) return '外部 Scheduler 在线';
  const age = Number(heartbeat.age_seconds || 0);
  return age > 0 ? `外部 Scheduler 失联 ${formatHeartbeatAge(age)}` : '外部 Scheduler 未确认';
});
const doctorHeartbeatClass = computed(() => (
  doctorHeartbeat.value.active ? 'is-ok' : 'is-error'
));
const doctorStaleText = computed(() => {
  const due = Number(doctorSnapshot.value.due_task_count || 0);
  const stale = Number(doctorSnapshot.value.stale_due_count ?? doctorSnapshot.value.stale_due_success_count ?? 0);
  const blocked = Number(doctorSnapshot.value.blocked_due_count || 0);
  const oldSuccess = Number(doctorSnapshot.value.stale_due_success_count || 0);
  if (!due && !stale && !blocked && !oldSuccess) return '';
  const parts = [`到期 ${due}`, `未推进 ${stale}`];
  if (blocked) parts.push(`阻断 ${blocked}`);
  if (oldSuccess) parts.push(`旧成功 ${oldSuccess}`);
  return parts.join(' / ');
});

const taskMetaText = (task: FanxiuDataAnnotationSchedulerTaskItem) => {
  return task.trigger_description || '';
};

const pad2 = (value: number) => String(value).padStart(2, '0');

const parseRuntimeTime = (value: string) => {
  const text = String(value || '').trim();
  if (!text) return null;
  const date = new Date(text.replace(' ', 'T'));
  return Number.isFinite(date.getTime()) ? date : null;
};

const formatRuntimeTime = (value: string) => {
  const date = parseRuntimeTime(value);
  if (!date) return value;
  const now = new Date();
  const time = `${pad2(date.getHours())}:${pad2(date.getMinutes())}`;
  const isSameDate = date.getFullYear() === now.getFullYear()
    && date.getMonth() === now.getMonth()
    && date.getDate() === now.getDate();
  const targetMinute = date.getHours() * 60 + date.getMinutes();
  const nowMinute = now.getHours() * 60 + now.getMinutes();
  if (isSameDate && targetMinute >= nowMinute) return time;
  const monthDayTime = `${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ${time}`;
  if (date.getFullYear() === now.getFullYear()) return monthDayTime;
  return `${date.getFullYear()}-${monthDayTime}`;
};

const nextTriggerText = (task: FanxiuDataAnnotationSchedulerTaskItem) => {
  if (task.next_time) return formatRuntimeTime(task.next_time);
  return '';
};

const nextTriggerTitle = (task: FanxiuDataAnnotationSchedulerTaskItem) => {
  if (!task.next_time) return '作业尚未设置下次触发时间';
  const bias = Number(task.schedule_bias_minutes || 0);
  if (bias <= 0) return task.next_time;
  return `原始 ${task.original_next_time || task.next_time}；时间编排 +${bias} 分钟`;
};

const canRunTaskEarly = (task: FanxiuDataAnnotationSchedulerTaskItem) => {
  const nextTime = parseRuntimeTime(task.next_time || '');
  return Boolean(nextTime && nextTime.getTime() > Date.now());
};

const taskDispatchLevel = (task: FanxiuDataAnnotationSchedulerTaskItem) => (
  Math.min(5, Math.max(0, Number(task.dispatch_level) || 0))
);

const taskDispatchLevelClass = (task: FanxiuDataAnnotationSchedulerTaskItem) => (
  `is-level-${taskDispatchLevel(task)}`
);

const logSourceText = (entry: FanxiuBehaviorTreeRuntimeLogEntry) => {
  const file = String(entry.source_file || '').trim();
  const line = entry.source_line ? `:${entry.source_line}` : '';
  const expr = String(entry.source_expr || '').trim();
  return file && expr ? `${file}${line}  ${expr}` : '';
};

const logEntryKey = (entry: FanxiuBehaviorTreeRuntimeLogEntry, index: number) => (
  entry.id || `${entry.ts || ''}-${entry.time}-${entry.kind}-${entry.scope || ''}-${entry.item_id || ''}-${entry.message}-${index}`
);

const sameLogEntries = (left: FanxiuBehaviorTreeRuntimeLogEntry[], right: FanxiuBehaviorTreeRuntimeLogEntry[]) => (
  left.length === right.length
  && left.every((item, index) => {
    const other = right[index];
    return item.id === other.id
      && item.time === other.time
      && item.kind === other.kind
      && item.message === other.message;
  })
);
const currentCellLog = computed(() => cellLogs.value[activeCellIndex.value] || null);
const cellPageText = computed(() => {
  if (!cellLogs.value.length) return '0/0';
  return `${activeCellIndex.value + 1}/${cellLogs.value.length}`;
});
const canShowNewerCell = computed(() => activeCellIndex.value > 0);
const canShowOlderCell = computed(() => activeCellIndex.value < cellLogs.value.length - 1);
const showNewerCell = () => {
  if (!canShowNewerCell.value) return;
  activeCellIndex.value -= 1;
  logs.value = currentCellLog.value?.entries || [];
};
const showOlderCell = () => {
  if (!canShowOlderCell.value) return;
  activeCellIndex.value += 1;
  logs.value = currentCellLog.value?.entries || [];
};

const openLogMenu = (event: MouseEvent, scope: string, itemId: string, title: string) => {
  contextMenu.value = {
    visible: true,
    x: event.clientX,
    y: event.clientY,
    scope,
    itemId,
    title,
    task: null,
  };
};

const openTaskMenu = (event: MouseEvent, task: FanxiuDataAnnotationSchedulerTaskItem) => {
  contextMenu.value = {
    visible: true,
    x: event.clientX,
    y: event.clientY,
    scope: 'job',
    itemId: task.id,
    title: task.label,
    task,
  };
};

const closeLogMenu = () => {
  contextMenu.value.visible = false;
};

const openContextLogs = () => {
  const menu = contextMenu.value;
  closeLogMenu();
  void router.push({
    path: '/fanxiu/data-annotation/runtime/logs',
    query: {
      entry_id: entryId.value,
      scope: menu.scope,
      item_id: menu.itemId,
      title: menu.title,
    },
  });
};

const runContextTaskNow = async () => {
  const task = contextMenu.value.task;
  closeLogMenu();
  if (!task) return;
  if (!entryId.value) {
    ElMessage.warning('未找到 mf 设备入口');
    return;
  }
  actionLoading.value = `run-current:${task.id}`;
  try {
    const status = await runNowFanxiuDataAnnotationSchedulerTask(
      entryId.value,
      task.id,
      {},
      true,
      'current',
    );
    if (status.status === 'error' || status.status === 'stopped') {
      ElMessage.error(status.error || status.message || `${task.label}运行失败`);
    } else {
      ElMessage.success(`${task.label}已立即运行（按当前时间）`);
    }
    await Promise.allSettled([
      refreshScheduler(),
      refreshStatus(),
      refreshSchedulerPlan(),
      refreshDoctorWatchLatest(),
      refreshCellLogs(),
    ]);
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '立即运行失败');
  } finally {
    actionLoading.value = '';
  }
};

const runContextTaskEarly = async () => {
  const task = contextMenu.value.task;
  closeLogMenu();
  if (!task) return;
  if (!canRunTaskEarly(task)) {
    ElMessage.warning(task.next_time ? '该作业已经到期，请使用立即运行' : '该作业没有计划时间，不能提前运行');
    return;
  }
  if (!entryId.value) {
    ElMessage.warning('未找到 mf 设备入口');
    return;
  }
  actionLoading.value = `run-planned:${task.id}`;
  try {
    const status = await runNowFanxiuDataAnnotationSchedulerTask(
      entryId.value,
      task.id,
      {},
      true,
      'planned',
    );
    if (status.status === 'error' || status.status === 'stopped') {
      ElMessage.error(status.error || status.message || `${task.label}运行失败`);
    } else {
      ElMessage.success(`${task.label}已提前运行（按计划时间）`);
    }
    await Promise.allSettled([
      refreshScheduler(),
      refreshStatus(),
      refreshSchedulerPlan(),
      refreshDoctorWatchLatest(),
      refreshCellLogs(),
    ]);
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '提前运行失败');
  } finally {
    actionLoading.value = '';
  }
};

const clearContextTaskSchedule = async () => {
  const task = contextMenu.value.task;
  closeLogMenu();
  if (!task) return;
  if (!entryId.value) {
    ElMessage.warning('未找到 mf 设备入口');
    return;
  }
  actionLoading.value = `next-time:${task.id}`;
  try {
    await setFanxiuDataAnnotationSchedulerTaskNextTime(entryId.value, task.id, null);
    schedulerTasks.value = schedulerTasks.value.map(item => (
      item.id === task.id
        ? { ...item, next_time: null, original_next_time: null, schedule_bias_minutes: 0 }
        : item
    ));
    ElMessage.success(`${task.label}已取消执行`);
    void Promise.allSettled([refreshStatus(), refreshSchedulerPlan()]);
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '取消安排失败');
  } finally {
    actionLoading.value = '';
  }
};

const openContextTaskTime = () => {
  const task = contextMenu.value.task;
  closeLogMenu();
  if (!task) return;
  schedulerTimeDialog.value = {
    visible: true,
    task,
    nextTime: task.original_next_time || task.next_time || '',
  };
};

const saveContextTaskTime = async () => {
  const task = schedulerTimeDialog.value.task;
  const nextTime = schedulerTimeDialog.value.nextTime;
  if (!task || !nextTime) {
    ElMessage.warning('请选择时间');
    return;
  }
  if (!entryId.value) {
    ElMessage.warning('未找到 mf 设备入口');
    return;
  }
  actionLoading.value = `next-time:${task.id}`;
  try {
    const result = await setFanxiuDataAnnotationSchedulerTaskNextTime(entryId.value, task.id, nextTime);
    schedulerTasks.value = schedulerTasks.value.map(item => (
      item.id === task.id
        ? { ...item, next_time: result.next_time, original_next_time: result.next_time, schedule_bias_minutes: 0 }
        : item
    ));
    schedulerTimeDialog.value.visible = false;
    ElMessage.success(`${task.label}已设置执行时间`);
    void Promise.allSettled([refreshStatus(), refreshSchedulerPlan()]);
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '设置时间失败');
  } finally {
    actionLoading.value = '';
  }
};

const openDoctorAnnotationTarget = () => {
  const target = doctorAnnotationTarget.value;
  if (target?.path) {
    void router.push({
      path: target.path,
      query: {
        entry_id: entryId.value,
        ...(target.query || {}),
      },
    });
    return;
  }
  const title = doctorAnnotationTitle.value;
  if (!title) return;
  void router.push({
    path: '/fanxiu/data-annotation',
    query: {
      entry_id: entryId.value,
      focus_image_title: title,
    },
  });
};

const warnRefreshFailure = (scope: string, error: unknown) => {
  console.warn(`${scope} failed`, error);
};

const applyCellLogsPayload = (
  nextCells: FanxiuBehaviorTreeRuntimeCellLog[],
  options: { latestOnly?: boolean } = {},
) => {
  const previousCellId = currentCellLog.value?.id || '';
  if (options.latestOnly) {
    const latest = nextCells[0];
    if (!latest) return;
    const existing = cellLogs.value;
    const latestChanged = existing[0]?.id !== latest.id;
    const merged = latestChanged
      ? [latest, ...existing.filter((cell) => cell.id !== latest.id)].slice(0, CELL_LOG_LIMIT)
      : existing.map((cell, index) => (index === 0 ? latest : cell));
    cellLogs.value = merged;
    const retainedIndex = previousCellId ? merged.findIndex((cell) => cell.id === previousCellId) : -1;
    activeCellIndex.value = retainedIndex >= 0 ? retainedIndex : 0;
  } else {
    cellLogs.value = nextCells;
    const retainedIndex = previousCellId ? nextCells.findIndex((cell) => cell.id === previousCellId) : -1;
    activeCellIndex.value = retainedIndex >= 0 ? retainedIndex : 0;
  }
  const nextLogs = currentCellLog.value?.entries || [];
  if (!sameLogEntries(logs.value, nextLogs)) {
    logs.value = nextLogs;
  }
};

const applyStatus = (status: FanxiuBehaviorTreeRuntimeStatus) => {
  runtimeStatus.value = status;
};

const refreshStatus = async () => {
  const status = await getFanxiuBehaviorTreeRuntimeStatus(entryId.value, { includeCellLogs: false, includeLogs: false });
  applyStatus(status);
};

const refreshLogs = async (options: { latestOnly?: boolean } = {}) => {
  logsLoading.value = true;
  try {
    const response = await getFanxiuBehaviorTreeRuntimeCellLogs(
      options.latestOnly ? CELL_LOG_POLL_LIMIT : CELL_LOG_LIMIT,
      options.latestOnly ? CELL_LOG_POLL_ENTRY_LIMIT : CELL_LOG_ENTRY_LIMIT,
    );
    applyCellLogsPayload(response.cells || [], options);
  } finally {
    logsLoading.value = false;
  }
};

const refreshScheduler = async () => {
  const [tasksResponse, planResponse] = await Promise.all([getFanxiuDataAnnotationSchedulerTasks(), getFanxiuDataAnnotationSchedulerPlan()]);
  schedulerTasks.value = tasksResponse.tasks || [];
  schedulerPlan.value = planResponse;
  schedulerJobGroupEnabled.value = tasksResponse.job_group_enabled ?? planResponse.job_group_enabled ?? true;
};

const refreshSchedulerTasks = async () => {
  const response = await getFanxiuDataAnnotationSchedulerTasks();
  schedulerTasks.value = response.tasks || [];
  schedulerJobGroupEnabled.value = response.job_group_enabled ?? schedulerJobGroupEnabled.value;
};

const refreshSchedulerPlan = async () => {
  const response = await getFanxiuDataAnnotationSchedulerPlan();
  schedulerPlan.value = response;
  schedulerJobGroupEnabled.value = response.job_group_enabled ?? schedulerJobGroupEnabled.value;
};

const refreshGameStateInspection = async () => {
  gameStateInspection.value = await getFanxiuGameStateInspectionStatus();
};

const refreshInfoWindow = async () => {
  infoWindowStatus.value = await getFanxiuInfoWindowStatus(entryId.value);
};

const refreshDoctorWatchLatest = async () => {
  doctorWatchLatest.value = await getFanxiuDataAnnotationDoctorWatchLatest();
};

const applyDoctorWatchLatestPayload = (payload: unknown) => {
  if (!payload || typeof payload !== 'object') return false;
  const candidate = payload as Partial<FanxiuDataAnnotationDoctorWatchLatestResponse>;
  const hasPayload = (
    typeof candidate.exists === 'boolean'
    || typeof candidate.message === 'string'
    || (candidate.snapshot && typeof candidate.snapshot === 'object')
    || (candidate.heartbeat && typeof candidate.heartbeat === 'object')
  );
  if (!hasPayload) return false;
  doctorWatchLatest.value = candidate as FanxiuDataAnnotationDoctorWatchLatestResponse;
  return true;
};

const shouldEnsureDoctorWatch = (payload = doctorWatchLatest.value) => {
  if (!payload?.exists) return true;
  return !Boolean(payload.heartbeat?.active);
};

const ensureDoctorWatchInBackground = () => {
  if (doctorEnsurePromise || !shouldEnsureDoctorWatch()) return;
  const now = Date.now();
  if (now - lastDoctorEnsureAt < DOCTOR_ENSURE_COOLDOWN_MS) return;
  lastDoctorEnsureAt = now;
  doctorEnsurePromise = (async () => {
    try {
      const ensured = await ensureFanxiuDataAnnotationDoctorWatch();
      applyDoctorWatchLatestPayload(ensured.latest);
    } catch (error) {
      console.warn('ensure doctor watch failed', error);
    } finally {
      doctorEnsurePromise = null;
    }
  })();
};

const refreshDoctorWatchPanel = async () => {
  await refreshDoctorWatchLatest();
  ensureDoctorWatchInBackground();
};

const scheduleLogsRefresh = () => {
  window.requestAnimationFrame(() => {
    void refreshLogs().catch((error) => {
      warnRefreshFailure('refresh logs', error);
    });
  });
};

const refreshAll = async () => {
  loading.value = true;
  try {
    const [statusResult, schedulerResult, infoWindowResult] = await Promise.allSettled([
      refreshStatus(),
      refreshSchedulerTasks(),
      refreshInfoWindow(),
    ]);
    if (statusResult.status === 'rejected') {
      warnRefreshFailure('refresh status', statusResult.reason);
    }
    if (schedulerResult.status === 'rejected') {
      warnRefreshFailure('refresh scheduler', schedulerResult.reason);
    }
    if (infoWindowResult.status === 'rejected') {
      warnRefreshFailure('refresh info window', infoWindowResult.reason);
    }
  } finally {
    loading.value = false;
  }
  scheduleLogsRefresh();
  void refreshSchedulerPlan().catch((error) => {
    warnRefreshFailure('refresh scheduler plan', error);
  });
  void refreshDoctorWatchPanel().catch((error) => {
    warnRefreshFailure('refresh doctor watch', error);
  });
  void refreshGameStateInspection().catch((error) => {
    warnRefreshFailure('refresh game state inspection', error);
  });
};

const runAction = async (name: string, action: () => Promise<FanxiuBehaviorTreeRuntimeStatus | void>) => {
  if (!entryId.value) {
    ElMessage.warning('未找到 mf 设备入口');
    return;
  }
  actionLoading.value = name;
  try {
    const status = await action();
    if (status) applyStatus(status);
    const followups = [
      refreshLogs(),
      refreshScheduler(),
      refreshDoctorWatchLatest(),
      refreshGameStateInspection(),
    ];
    await Promise.all(followups);
    ensureDoctorWatchInBackground();
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '操作失败');
  } finally {
    actionLoading.value = '';
  }
};

const toggleGuardGroupEnabled = () => runAction('guard-group', () => setFanxiuBehaviorTreeRuntimeGuardGroup(entryId.value, !guardGroupEnabled.value));

const changeSchedulerOwner = async (value: string) => {
  if (!['engineering', 'ai'].includes(value)) return;
  if (value === schedulerOwnerKey.value) return;
  const owner = value as 'engineering' | 'ai';
  actionLoading.value = 'scheduler-owner';
  try {
    const response = await setFanxiuDataAnnotationSchedulerSettings(owner === 'engineering', entryId.value);
    schedulerTasks.value = response.tasks || [];
    schedulerJobGroupEnabled.value = response.job_group_enabled ?? true;
    const followups = [
      refreshStatus(),
      refreshLogs(),
      refreshScheduler(),
      refreshDoctorWatchLatest(),
      refreshGameStateInspection(),
    ];
    await Promise.all(followups);
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '保存失败');
  } finally {
    actionLoading.value = '';
  }
};

const toggleKernelEnabled = () => runAction(
  'kernel-toggle',
  () => setFanxiuBehaviorTreeRuntimeBehaviorTree(entryId.value, !behaviorTreeEnabled.value),
);

const restartSimulator = async () => {
  if (!entryId.value) {
    ElMessage.warning('未找到 mf 设备入口');
    return;
  }
  try {
    await ElMessageBox.confirm(
      '重启会中断当前作业并重新启动游戏，是否继续？',
      '重启模拟器',
      {
        confirmButtonText: '重启',
        cancelButtonText: '取消',
        type: 'warning',
      },
    );
  } catch {
    return;
  }

  actionLoading.value = 'device-restart';
  try {
    const result = await restartFanxiuBehaviorTreeRuntimeDevice(entryId.value);
    applyStatus(result.runtime);
    ElMessage.success(result.message || '模拟器已重启');
    await Promise.all([
      refreshLogs(),
      refreshScheduler(),
      refreshSchedulerPlan(),
      refreshDoctorWatchLatest(),
      refreshGameStateInspection(),
    ]);
    ensureDoctorWatchInBackground();
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '模拟器重启失败');
  } finally {
    actionLoading.value = '';
  }
};

const toggleGuardItem = (itemId: string) => {
  void runAction(`guard:${itemId}`, () => setFanxiuBehaviorTreeRuntimeGuard(entryId.value, !guardItemEnabled(itemId), 2, itemId));
};

const setInfoWindowSetting = async (key: keyof FanxiuInfoWindowSettings, value: boolean) => {
  if (actionLoading.value === 'info-window') return;
  const previous = infoWindowStatus.value;
  const settings = { ...infoWindowSettings.value, [key]: value };
  if (previous) {
    infoWindowStatus.value = { ...previous, settings };
  }
  actionLoading.value = 'info-window';
  try {
    infoWindowStatus.value = await setFanxiuInfoWindowSettings(entryId.value, settings);
  } catch (error: any) {
    infoWindowStatus.value = previous;
    ElMessage.error(error?.response?.data?.detail || error?.message || '信息窗设置保存失败');
  } finally {
    actionLoading.value = '';
  }
};

const startPolling = () => {
  if (pollTimer !== null) return;
  pollTimer = window.setInterval(() => {
    if (polling) return;
    polling = true;
    pollTick += 1;
    const syncSlowState = pollTick % SLOW_STATE_POLL_TICKS === 0;
    void (async () => {
      try {
        try {
          await refreshStatus();
        } catch (error) {
          warnRefreshFailure('poll refresh status', error);
        }
        if (syncSlowState) {
          const slowRefreshes = [
            refreshLogs({ latestOnly: true }),
            refreshScheduler(),
            refreshDoctorWatchLatest(),
            refreshGameStateInspection(),
            refreshInfoWindow(),
          ];
          const scopes = [
            'poll refresh logs',
            'poll refresh scheduler',
            'poll refresh doctor watch',
            'poll refresh game state inspection',
            'poll refresh info window',
          ];
          const results = await Promise.allSettled(slowRefreshes);
          results.forEach((result, index) => {
            if (result.status === 'rejected') {
              warnRefreshFailure(scopes[index], result.reason);
            }
          });
          ensureDoctorWatchInBackground();
        }
      } finally {
        polling = false;
      }
    })();
  }, 1500);
};

const stopPolling = () => {
  if (pollTimer === null) return;
  window.clearInterval(pollTimer);
  pollTimer = null;
};

onMounted(async () => {
  const initialEntryId = entryId.value;
  const [devicesResult] = await Promise.allSettled([
    taskStore.fetchDevices(),
    refreshAll(),
  ]);
  if (devicesResult.status === 'rejected') {
    warnRefreshFailure('fetch devices', devicesResult.reason);
  }
  if (!entryId.value) {
    const mfDevice = devices.value.find((item) => item.name === machineName || item.id === machineName || item.id.includes('codepc_mf'));
    entryId.value = mfDevice?.id || devices.value[0]?.id || '';
  }
  if (!initialEntryId && entryId.value) {
    void refreshStatus().catch((error) => {
      warnRefreshFailure('refresh resolved device status', error);
    });
  }
  startPolling();
  window.addEventListener('click', closeLogMenu);
});

onUnmounted(() => {
  stopPolling();
  window.removeEventListener('click', closeLogMenu);
});
</script>

<template>
  <div class="runtime-page">
    <header class="runtime-header">
      <div>
        <div class="runtime-title">
          <h2>行为树 Runtime</h2>
        </div>
        <div class="runtime-header-controls">
          <el-button
            class="kernel-toggle-button"
            :class="{ 'is-enabled': kernelDisplayState === 'enabled', 'is-error': kernelDisplayState === 'error' }"
            size="small"
            :loading="actionLoading === 'kernel-toggle'"
            :disabled="kernelDisplayState === 'loading'"
            :title="kernelToggleTitle"
            @click="toggleKernelEnabled"
          >
            {{ kernelToggleText }}
          </el-button>
          <el-button
            size="small"
            :loading="actionLoading === 'device-restart'"
            :disabled="Boolean(actionLoading) && actionLoading !== 'device-restart'"
            @click="restartSimulator"
          >
            重启模拟器
          </el-button>
        </div>
      </div>
    </header>

    <main class="runtime-main" v-loading="loading">
      <!--
        PRODUCT CONTRACT — DO NOT DELETE:
        游戏状态巡检是用户依赖的行为树 Runtime 常驻 UI。重构只能调整呈现，不能删除本区、状态请求或轮询。
        backend/tests/test_fanxiu_runtime_page_contract.py 会锁定这条产品约束。
      -->
      <section class="runtime-section" data-testid="game-state-inspection-panel">
        <div class="section-title inspection-section-title">
          <div>
            <h3>{{ gameStateInspection?.name || '游戏状态巡检' }}</h3>
            <p>{{ gameStateInspection?.description || '通过只读游戏 Runtime 数据定期检查游戏状态，并按业务事实提前相关作业' }}</p>
          </div>
          <span class="inspection-status" :class="gameStateInspectionStatusClass">
            {{ gameStateInspectionStatusText }}
          </span>
        </div>
        <div class="inspection-facts">
          <span>周期 <strong>{{ gameStateInspectionIntervalText }}</strong></span>
          <span>巡检项 <strong>{{ gameStateInspectionProbeText }}</strong></span>
          <span>最近检查 <strong>{{ gameStateInspection?.last_checked_at || '-' }}</strong></span>
          <span>结果 <strong>{{ gameStateInspection?.last_message || '-' }}</strong></span>
        </div>
      </section>

      <section class="runtime-section" data-testid="fanxiu-info-window-panel">
        <div class="section-title info-window-title">
          <div class="info-window-heading">
            <h3>凡修信息窗</h3>
            <span class="inspection-status" :class="infoWindowStatusClass">{{ infoWindowStatusText }}</span>
          </div>
          <el-switch
            :model-value="infoWindowSettings.enabled"
            :loading="actionLoading === 'info-window'"
            aria-label="开关凡修信息窗"
            @change="setInfoWindowSetting('enabled', Boolean($event))"
          />
        </div>
        <div class="info-window-options" :class="{ 'is-disabled': !infoWindowSettings.enabled }">
          <label title="默认关闭；开启后优先读取最近识别结果，仅当结果已超过 5 秒才主动识别并更新时间">
            <span>主动识别</span>
            <el-switch
              size="small"
              :model-value="infoWindowSettings.active_recognition"
              :disabled="!infoWindowSettings.enabled || actionLoading === 'info-window'"
              @change="setInfoWindowSetting('active_recognition', Boolean($event))"
            />
          </label>
          <label>
            <span>场景编号</span>
            <el-switch
              size="small"
              :model-value="infoWindowSettings.show_scene_id"
              :disabled="!infoWindowSettings.enabled || actionLoading === 'info-window'"
              @change="setInfoWindowSetting('show_scene_id', Boolean($event))"
            />
          </label>
          <label>
            <span>识别置信度</span>
            <el-switch
              size="small"
              :model-value="infoWindowSettings.show_scene_score"
              :disabled="!infoWindowSettings.enabled || actionLoading === 'info-window'"
              @change="setInfoWindowSetting('show_scene_score', Boolean($event))"
            />
          </label>
          <label title="显示当前已识别场景中标记为 isSceneIdentity 的 Shape">
            <span>场景标识框</span>
            <el-switch
              size="small"
              :model-value="infoWindowSettings.show_scene_identity_shapes"
              :disabled="!infoWindowSettings.enabled || infoWindowSettings.show_all_shapes || actionLoading === 'info-window'"
              @change="setInfoWindowSetting('show_scene_identity_shapes', Boolean($event))"
            />
          </label>
          <label title="显示当前已识别场景中的全部非分组 Shape，包含场景标识框">
            <span>全部 Shape</span>
            <el-switch
              size="small"
              :model-value="infoWindowSettings.show_all_shapes"
              :disabled="!infoWindowSettings.enabled || actionLoading === 'info-window'"
              @change="setInfoWindowSetting('show_all_shapes', Boolean($event))"
            />
          </label>
        </div>
      </section>

      <section class="runtime-section">
        <div class="section-title group-section-title">
          <h3>守护</h3>
          <div class="section-actions">
            <button
              class="enable-dot group-enable-dot"
              :class="{ enabled: guardGroupEnabled }"
              type="button"
              aria-label="切换守护组启用状态"
              :disabled="actionLoading === 'guard-group'"
              :title="guardGroupTitle"
              @click="toggleGuardGroupEnabled"
            />
          </div>
        </div>
        <div class="runtime-table">
          <table class="runtime-native-table is-guard-table">
            <colgroup>
              <col class="col-index" />
              <col class="col-name" />
              <col class="col-exec" />
              <col class="col-enable" />
            </colgroup>
            <thead>
              <tr>
                <th>序号</th>
                <th>名称</th>
                <th>备注</th>
                <th>启用</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="(item, index) in guardItems"
                :key="item.id"
                @contextmenu.prevent.stop="openLogMenu($event, 'guard', item.id, item.label)"
              >
                <td><span class="index-pill">{{ index + 1 }}</span></td>
                <td :title="item.label"><strong>{{ item.label }}</strong></td>
                <td :title="item.message">{{ item.message }}</td>
                <td>
                  <button
                    class="enable-dot"
                    :class="{ enabled: item.enabled }"
                    type="button"
                    :disabled="actionLoading === 'guard' || actionLoading === `guard:${item.id}`"
                    title="切换启用"
                    @click="toggleGuardItem(item.id)"
                  />
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="runtime-section">
        <div class="section-title group-section-title">
          <div class="job-title">
            <h3>作业</h3>
            <el-button
              link
              type="primary"
              size="small"
              title="编排同一原始时间的作业顺序"
              @click="schedulerTimeSequenceDialog?.open()"
            >时间编排</el-button>
          </div>
          <div class="section-actions">
            <span class="runtime-control-label-group">
              <span class="runtime-control-label">调度器</span>
              <el-popover trigger="click" placement="right-start" :width="420">
                <template #reference>
                  <el-button
                    class="help-button"
                    size="small"
                    circle
                    aria-label="查看调度器说明"
                  >
                    <el-icon><QuestionFilled /></el-icon>
                  </el-button>
                </template>
                <div class="runtime-help-doc">
                  <h4>调度来源</h4>
                  <p>工程模式自动提交到期 Cell；切换到 AI 会停止当前工程 Cell，并暂停后续工程提交。</p>
                  <p>AI 或人工提交的普通 Cell 不会被这次切换误停。</p>
                  <p>两者都通过同一个 Kernel Cell 入口执行。</p>
                  <h4>手动运行</h4>
                  <p><strong>提前运行（按计划时间）</strong>：默认方式。作业立即执行；业务时间模拟为原下次触发时间后 1 分钟，适合提前完成定时作业，并可能把下次时间直接推进到下一周期。</p>
                  <p><strong>立即运行（按当前时间）</strong>：作业立即执行；窗口判断和下一次时间都使用真实此刻。</p>
                </div>
              </el-popover>
            </span>
            <el-select
              class="scheduler-owner-select"
              size="small"
              :model-value="schedulerOwnerKey"
              :disabled="actionLoading === 'scheduler-owner'"
              :loading="actionLoading === 'scheduler-owner'"
              :title="schedulerOwnerTitle"
              @change="changeSchedulerOwner"
            >
              <el-option
                v-for="option in schedulerOwnerOptions"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              />
            </el-select>
          </div>
        </div>
        <div class="runtime-table">
          <table class="runtime-native-table is-job-table">
            <colgroup>
              <col class="col-index" />
              <col class="col-name" />
              <col class="col-exec" />
              <col class="col-level" />
              <col class="col-trigger" />
            </colgroup>
            <thead>
              <tr>
                <th>序号</th>
                <th>名称</th>
                <th>触发说明</th>
                <th>级别</th>
                <th>下次触发</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="(task, index) in businessTasks"
                :key="task.id"
                :class="taskDispatchLevelClass(task)"
                @contextmenu.prevent.stop="openTaskMenu($event, task)"
              >
                <td><span class="index-pill">{{ index + 1 }}</span></td>
                <td :title="task.label"><strong>{{ task.label }}</strong></td>
                <td :title="taskMetaText(task)">{{ taskMetaText(task) }}</td>
                <td>
                  <span class="dispatch-level-value">{{ taskDispatchLevel(task) }}级</span>
                </td>
                <td :title="nextTriggerTitle(task)">
                  <span class="next-trigger-time" :class="taskDispatchLevelClass(task)">{{ nextTriggerText(task) }}</span>
                </td>
              </tr>
              <tr v-if="!businessTasks.length">
                <td colspan="5" class="empty-cell">暂无作业</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="runtime-section runtime-state-section">
        <div class="section-title">
          <h3>运行状态</h3>
        </div>
        <div class="runtime-state-row">
          <div v-if="schedulerBlockingMessage" class="runtime-blocking" :title="schedulerBlockingMessage">
            {{ schedulerBlockingMessage }}
          </div>
          <div
            v-if="doctorWatchLatest?.exists"
            class="runtime-doctor"
            :class="doctorSeverityClass"
            :title="doctorSummaryText"
          >
            <span>巡检 {{ doctorSeverityText }}</span>
            <span v-if="doctorHeartbeatText" :class="doctorHeartbeatClass">{{ doctorHeartbeatText }}</span>
            <span v-if="doctorStaleText">{{ doctorStaleText }}</span>
            <span v-if="doctorSnapshot.checked_at">巡检 {{ doctorSnapshot.checked_at }}</span>
          </div>
          <div v-if="doctorActionText" class="runtime-blocking runtime-blocking-action" :title="doctorActionText">
            <span>{{ doctorActionText }}</span>
            <el-button
              v-if="doctorAnnotationTitle"
              size="small"
              plain
              @click="openDoctorAnnotationTarget"
            >
              打开补标
            </el-button>
          </div>
          <div v-if="runtimeSecondaryMessage" class="runtime-message" :title="runtimeSecondaryMessage">{{ runtimeSecondaryMessage }}</div>
        </div>
      </section>

      <section class="runtime-section">
        <div class="section-title cell-log-title">
          <h3>Cell 日志</h3>
          <div class="cell-log-pager">
            <el-button size="small" :disabled="!canShowNewerCell" @click="showNewerCell">‹</el-button>
            <span>{{ cellPageText }}</span>
            <el-button size="small" :disabled="!canShowOlderCell" @click="showOlderCell">›</el-button>
          </div>
        </div>
        <div v-if="currentCellLog" class="cell-log-head">
          <div>
            <strong>{{ currentCellLog.title }}</strong>
            <span>{{ currentCellLog.started_at }} - {{ currentCellLog.ended_at }}</span>
          </div>
          <pre>{{ currentCellLog.source }}</pre>
        </div>
        <div class="log-list">
          <div v-if="logsLoading && !logs.length" class="empty-row">Cell 日志加载中...</div>
          <template v-else>
            <div v-for="(entry, index) in logs" :key="logEntryKey(entry, index)" class="log-row" :class="`is-${entry.kind}`">
              <span>{{ entry.time }}</span>
              <b>{{ entry.kind }}</b>
              <p>
                <code v-if="logSourceText(entry)">{{ logSourceText(entry) }}</code>
                <span>{{ entry.message }}</span>
              </p>
            </div>
            <div v-if="!logs.length" class="empty-row">暂无 cell 日志</div>
          </template>
        </div>
      </section>
    </main>

    <el-dialog
      v-model="schedulerTimeDialog.visible"
      title="执行时间"
      width="360px"
      destroy-on-close
    >
      <el-date-picker
        v-model="schedulerTimeDialog.nextTime"
        type="datetime"
        format="YYYY-MM-DD HH:mm"
        value-format="YYYY-MM-DD HH:mm:ss"
        placeholder="选择执行时间"
        style="width: 100%"
      />
      <template #footer>
        <el-button @click="schedulerTimeDialog.visible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="Boolean(schedulerTimeDialog.task && actionLoading === `next-time:${schedulerTimeDialog.task.id}`)"
          @click="saveContextTaskTime"
        >确定</el-button>
      </template>
    </el-dialog>
    <SchedulerTimeSequenceDialog
      ref="schedulerTimeSequenceDialog"
      @saved="refreshScheduler"
    />

    <div
      v-if="contextMenu.visible"
      class="runtime-context-menu"
      :style="{ left: `${contextMenu.x}px`, top: `${contextMenu.y}px` }"
      @click.stop
    >
      <button
        v-if="contextMenu.task"
        type="button"
        :disabled="!canRunTaskEarly(contextMenu.task)"
        :title="canRunTaskEarly(contextMenu.task) ? '立即运行，并把业务时间模拟为原下次触发时间后 1 分钟' : (contextMenu.task.next_time ? '该作业已经到期，请使用立即运行' : '该作业没有计划时间')"
        @click="runContextTaskEarly"
      >提前运行（按计划时间）</button>
      <button v-if="contextMenu.task" type="button" @click="runContextTaskNow">立即运行（按当前时间）</button>
      <button v-if="contextMenu.task" type="button" @click="clearContextTaskSchedule">取消执行</button>
      <button v-if="contextMenu.task" type="button" @click="openContextTaskTime">执行时间…</button>
      <button type="button" @click="openContextLogs">日志</button>
    </div>
  </div>
</template>

<style scoped>
.runtime-page {
  min-height: 100%;
  background: #f5f7fa;
  color: #1f2937;
}

.runtime-header {
  min-height: 72px;
  padding: 12px 18px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  border-bottom: 1px solid #e5e7eb;
  background: #fff;
}

.runtime-header h2 {
  margin: 4px 0 0;
  font-size: 20px;
  line-height: 1.2;
}

.runtime-title {
  display: flex;
  align-items: center;
  gap: 8px;
}

.runtime-header-controls {
  margin-top: 10px;
  display: inline-flex;
  align-items: center;
}

.help-button {
  width: 22px;
  height: 22px;
  min-height: 22px;
  padding: 0;
  color: #64748b;
}

.runtime-help-doc h4 {
  margin: 0 0 8px;
  font-size: 14px;
}

.runtime-help-doc p {
  margin: 7px 0;
  color: #4b5563;
  font-size: 13px;
  line-height: 1.55;
}

.runtime-main {
  padding: 14px 18px 28px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.runtime-section {
  padding: 12px;
  border: 1px solid #dfe6ef;
  background: #fff;
}

.section-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.section-actions {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.group-section-title {
  justify-content: flex-start;
}

.section-title h3 {
  margin: 0;
  font-size: 15px;
}

.inspection-section-title {
  align-items: flex-start;
}

.inspection-section-title > div {
  min-width: 0;
}

.inspection-section-title p {
  margin: 5px 0 0;
  color: #6b7280;
  font-size: 12px;
}

.inspection-status {
  flex: none;
  padding: 2px 8px;
  border-radius: 10px;
  color: #64748b;
  background: #f1f5f9;
}

.inspection-status.is-running {
  color: #166534;
  background: #dcfce7;
}

.inspection-status.is-error {
  color: #991b1b;
  background: #fee2e2;
}

.inspection-facts {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 22px;
  padding-top: 9px;
  border-top: 1px solid #e5e7eb;
  color: #6b7280;
  font-size: 12px;
}

.inspection-facts strong {
  margin-left: 4px;
  color: #374151;
  font-weight: 500;
}

.info-window-title {
  margin-bottom: 8px;
}

.info-window-heading {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.info-window-options {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 24px;
  padding-top: 9px;
  border-top: 1px solid #e5e7eb;
}

.info-window-options.is-disabled {
  opacity: 0.55;
}

.info-window-options label {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: #374151;
  font-size: 13px;
  white-space: nowrap;
}

.section-title span,
.muted {
  color: #6b7280;
  font-size: 12px;
}

.runtime-control-label {
  color: #1f2937;
  font-size: 14px;
  font-weight: 600;
  white-space: nowrap;
}

.runtime-control-label-group {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
}

.kernel-toggle-button {
  min-width: 82px;
  height: 28px;
  color: #475569;
  font-size: 13px;
  border-color: #cbd5e1;
  background: #f1f5f9;
}

.kernel-toggle-button.is-enabled {
  color: #166534;
  border-color: #86efac;
  background: #dcfce7;
}

.kernel-toggle-button.is-enabled:hover,
.kernel-toggle-button.is-enabled:focus {
  color: #14532d;
  border-color: #4ade80;
  background: #bbf7d0;
}

.scheduler-owner-select {
  width: 66px;
}

.kernel-toggle-button.is-error {
  color: #b91c1c;
  border-color: #fca5a5;
  background: #fef2f2;
}

.job-title {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.scheduler-owner-select :deep(.el-select__wrapper) {
  min-height: 28px;
  font-size: 13px;
}

.runtime-table {
  width: max-content;
  max-width: 100%;
  overflow-x: auto;
  border-top: 1px solid #e5e7eb;
}

.runtime-native-table {
  border-collapse: collapse;
  table-layout: fixed;
}

.runtime-native-table.is-guard-table {
  width: 466px;
}

.runtime-native-table.is-job-table {
  width: 636px;
}

.runtime-native-table th,
.runtime-native-table td {
  box-sizing: border-box;
}

.runtime-native-table th:nth-child(1),
.runtime-native-table td:nth-child(1) {
  width: 56px;
}

.runtime-native-table th:nth-child(2),
.runtime-native-table td:nth-child(2) {
  width: 128px;
}

.runtime-native-table.is-job-table th:nth-child(2),
.runtime-native-table.is-job-table td:nth-child(2) {
  width: 184px;
}

.runtime-native-table.is-job-table th:nth-child(3),
.runtime-native-table.is-job-table td:nth-child(3) {
  width: 88px;
}

.runtime-native-table th:nth-child(3),
.runtime-native-table td:nth-child(3) {
  width: 210px;
}

.runtime-native-table th:nth-child(4),
.runtime-native-table td:nth-child(4) {
  width: 72px;
}

.runtime-native-table th:nth-child(5),
.runtime-native-table td:nth-child(5) {
  width: 120px;
}

.runtime-native-table.is-job-table th:nth-child(6),
.runtime-native-table.is-job-table td:nth-child(6) {
  width: 116px;
}

.runtime-native-table th,
.runtime-native-table td {
  height: 42px;
  padding: 0 16px;
  border-bottom: 1px solid #e9eef5;
  font-size: 13px;
  text-align: left;
  vertical-align: middle;
  white-space: nowrap;
}

.runtime-native-table th:nth-child(2),
.runtime-native-table td:nth-child(2),
.runtime-native-table th:nth-child(3),
.runtime-native-table td:nth-child(3) {
  overflow: hidden;
  text-overflow: ellipsis;
}

.runtime-native-table th {
  height: 34px;
  color: #64748b;
  font-size: 12px;
  font-weight: 500;
}

.runtime-native-table th:first-child,
.runtime-native-table td:first-child {
  padding-left: 0;
}

.runtime-native-table th:nth-child(4),
.runtime-native-table td:nth-child(4),
.runtime-native-table.is-job-table th:nth-child(5),
.runtime-native-table.is-job-table td:nth-child(5) {
  text-align: center;
}

.dispatch-level-value {
  color: #475569;
  font-variant-numeric: tabular-nums;
}

.next-trigger-time {
  color: #111827;
  font-variant-numeric: tabular-nums;
}

.runtime-native-table strong {
  font-weight: 500;
}

.runtime-native-table tbody tr {
  cursor: default;
}

.runtime-native-table.is-job-table tbody tr.is-level-1 {
  --dispatch-level-color: #b91c1c;
}

.runtime-native-table.is-job-table tbody tr.is-level-2 {
  --dispatch-level-color: #c2410c;
}

.runtime-native-table.is-job-table tbody tr.is-level-3 {
  --dispatch-level-color: #6d28d9;
}

.runtime-native-table.is-job-table tbody tr.is-level-4 {
  --dispatch-level-color: #0369a1;
}

.runtime-native-table.is-job-table tbody tr.is-level-5 {
  --dispatch-level-color: #64748b;
}

.runtime-native-table.is-job-table tbody tr:is(.is-level-1, .is-level-2, .is-level-3, .is-level-4, .is-level-5) td,
.runtime-native-table.is-job-table tbody tr:is(.is-level-1, .is-level-2, .is-level-3, .is-level-4, .is-level-5) .index-pill,
.runtime-native-table.is-job-table tbody tr:is(.is-level-1, .is-level-2, .is-level-3, .is-level-4, .is-level-5) .dispatch-level-value,
.runtime-native-table.is-job-table tbody tr:is(.is-level-1, .is-level-2, .is-level-3, .is-level-4, .is-level-5) .next-trigger-time {
  color: var(--dispatch-level-color);
}

.index-pill {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 22px;
  color: #64748b;
  font-size: 12px;
  border-radius: 8px;
  background: #e8eef6;
}

.enable-dot {
  width: 28px;
  height: 28px;
  padding: 0;
  border: 0;
  border-radius: 50%;
  background: #9ca3af;
  cursor: pointer;
  box-shadow: inset 0 0 0 1px rgba(15, 23, 42, 0.04);
}

.enable-dot.enabled {
  background: #16a34a;
  box-shadow: 0 0 0 3px rgba(22, 163, 74, 0.16);
}

.enable-dot:disabled {
  cursor: not-allowed;
  opacity: 1;
}

.group-enable-dot {
  width: 24px;
  height: 24px;
}

.runtime-message {
  margin-top: 8px;
  min-width: 0;
  color: #6b7280;
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.runtime-state-section {
  padding-top: 10px;
  padding-bottom: 10px;
}

.runtime-state-row {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.runtime-state-row .runtime-message,
.runtime-state-row .runtime-blocking,
.runtime-state-row .runtime-doctor {
  margin-top: 0;
}

.runtime-blocking {
  margin-top: 8px;
  min-width: 0;
  width: max-content;
  max-width: 100%;
  padding: 4px 8px;
  color: #b45309;
  font-size: 12px;
  border: 1px solid #f5d08a;
  background: #fff7ed;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.runtime-blocking-action {
  display: flex;
  align-items: center;
  gap: 8px;
}

.runtime-blocking-action span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.runtime-doctor {
  margin-top: 8px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 0;
  font-size: 12px;
}

.runtime-doctor span {
  display: inline-flex;
  align-items: center;
  height: 24px;
  padding: 0 8px;
  border: 1px solid #d1d5db;
  background: #f9fafb;
  color: #374151;
}

.runtime-doctor.is-blocked span,
.runtime-doctor.is-error span {
  color: #991b1b;
  border-color: #fecaca;
  background: #fef2f2;
}

.runtime-doctor.is-attention span {
  color: #92400e;
  border-color: #fcd34d;
  background: #fffbeb;
}

.runtime-doctor.is-ok span {
  color: #166534;
  border-color: #bbf7d0;
  background: #f0fdf4;
}

.empty-cell {
  color: #9ca3af;
  font-size: 13px;
}

.empty-row {
  min-height: 30px;
  display: flex;
  align-items: center;
  color: #9ca3af;
  font-size: 13px;
  padding: 0 10px;
  border-bottom: 1px solid #f1f5f9;
}

.log-list {
  max-height: 460px;
  overflow: auto;
  border: 1px solid #edf2f7;
}

.cell-log-title {
  gap: 12px;
}

.cell-log-pager {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #64748b;
  font-size: 12px;
}

.cell-log-head {
  margin-bottom: 8px;
  border: 1px solid #edf2f7;
  background: #fbfdff;
}

.cell-log-head > div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 10px;
  border-bottom: 1px solid #edf2f7;
  font-size: 12px;
}

.cell-log-head strong {
  color: #1f2937;
  font-weight: 600;
}

.cell-log-head span {
  color: #64748b;
}

.cell-log-head pre {
  max-height: 132px;
  margin: 0;
  padding: 8px 10px;
  overflow: auto;
  color: #334155;
  font-family: Consolas, 'Courier New', monospace;
  font-size: 12px;
  line-height: 1.45;
  white-space: pre-wrap;
}

.log-row {
  display: grid;
  grid-template-columns: 68px 54px minmax(0, 1fr);
  gap: 8px;
  min-height: 30px;
  padding: 7px 9px;
  border-bottom: 1px solid #f1f5f9;
  font-size: 12px;
}

.log-row span,
.log-row b {
  color: #6b7280;
  font-weight: 400;
}

.log-row p {
  min-width: 0;
  margin: 0;
  word-break: break-all;
}

.log-row code {
  display: inline-block;
  margin-right: 10px;
  color: #0f766e;
  font-family: Consolas, 'Courier New', monospace;
  font-size: 12px;
  word-break: break-all;
}

.log-row.is-error {
  background: #fef2f2;
}

.log-row.is-success {
  background: #f0fdf4;
}

.runtime-context-menu {
  position: fixed;
  z-index: 1000;
  min-width: 92px;
  padding: 4px;
  border: 1px solid #d1d5db;
  border-radius: 6px;
  background: #fff;
  box-shadow: 0 10px 24px rgba(15, 23, 42, 0.14);
}

.runtime-context-menu button {
  width: 100%;
  height: 28px;
  padding: 0 10px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: #1f2937;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
}

.runtime-context-menu button:hover {
  background: #f3f4f6;
}

.runtime-context-menu button:disabled {
  color: #9ca3af;
  cursor: not-allowed;
  background: transparent;
}

@media (max-width: 900px) {
  .runtime-header {
    display: flex;
    flex-direction: column;
    align-items: stretch;
  }
}
</style>
