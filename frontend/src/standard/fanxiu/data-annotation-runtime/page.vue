<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ElMessage } from 'element-plus';
import { QuestionFilled } from '@element-plus/icons-vue';
import { taskStore } from '@/store/taskStore';
import {
  advanceNextFanxiuDataAnnotationSchedulerTask,
  ensureFanxiuDataAnnotationDoctorWatch,
  getFanxiuDataAnnotationDoctorWatchLatest,
  getFanxiuDataAnnotationRuntimeCellLogs,
  getFanxiuDataAnnotationRuntimeStatus,
  getFanxiuDataAnnotationSchedulerPlan,
  getFanxiuDataAnnotationSchedulerTasks,
  runNowFanxiuDataAnnotationSchedulerTask,
  saveFanxiuDataAnnotationSchedulerTasks,
  setFanxiuDataAnnotationSchedulerSettings,
  setFanxiuDataAnnotationRuntimeBehaviorTree,
  setFanxiuDataAnnotationRuntimeGuard,
  setFanxiuDataAnnotationRuntimeGuardGroup,
  setFanxiuDataAnnotationRuntimeIsolation,
  stopFanxiuDataAnnotationRuntimeCurrentTask,
  type FanxiuDataAnnotationDoctorWatchLatestResponse,
  type FanxiuDataAnnotationRuntimeCellLog,
  type FanxiuDataAnnotationRuntimeLogEntry,
  type FanxiuDataAnnotationRuntimeGuardItem,
  type FanxiuDataAnnotationRuntimeStatus,
  type FanxiuDataAnnotationSchedulerPlanResponse,
  type FanxiuDataAnnotationSchedulerTaskItem,
} from '@/api/fanxiu';

const route = useRoute();
const router = useRouter();

const entryId = ref(String(route.query.entry_id || ''));
const runtimeStatus = ref<FanxiuDataAnnotationRuntimeStatus | null>(null);
const schedulerTasks = ref<FanxiuDataAnnotationSchedulerTaskItem[]>([]);
const schedulerPlan = ref<FanxiuDataAnnotationSchedulerPlanResponse | null>(null);
const doctorWatchLatest = ref<FanxiuDataAnnotationDoctorWatchLatestResponse | null>(null);
const schedulerJobGroupEnabled = ref(true);
const cellLogs = ref<FanxiuDataAnnotationRuntimeCellLog[]>([]);
const activeCellIndex = ref(0);
const logs = ref<FanxiuDataAnnotationRuntimeLogEntry[]>([]);
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
let pollTimer: number | null = null;
let pollTick = 0;
let polling = false;
let doctorEnsurePromise: Promise<void> | null = null;
let lastDoctorEnsureAt = 0;
const CELL_LOG_LIMIT = 24;
const CELL_LOG_ENTRY_LIMIT = 1200;
const DOCTOR_ENSURE_COOLDOWN_MS = 30000;
const HUMAN_ISOLATION_TOKEN_KEY = 'fanxiuHumanRuntimeIsolationToken';
const HUMAN_ISOLATION_TTL_SECONDS = 21600;

const devices = computed(() => taskStore.devices);
const behaviorTreeEnabled = computed(() => runtimeStatus.value?.behavior_tree_enabled ?? true);
const guardGroupEnabled = computed(() => runtimeStatus.value?.guard_group_enabled ?? true);
const guardEnabled = computed(() => Boolean(runtimeStatus.value?.guard_enabled));
const guardItemEnabled = (guardId: string) => Boolean(runtimeStatus.value?.guard_items?.[guardId]?.enabled);
const machineName = 'codepc_mf';
const isolation = computed(() => runtimeStatus.value?.isolation || {});
const isolationReason = computed(() => String(isolation.value.reason || ''));
const isolationActive = computed(() => Boolean(isolation.value.active));
const humanIsolationActive = computed(() => (
  isolationActive.value
  && (isolationReason.value === 'human_using_runtime' || isolationReason.value.startsWith('human_using_runtime:'))
));
const nonHumanIsolationActive = computed(() => isolationActive.value && !humanIsolationActive.value);
const isolationToken = computed(() => String(isolation.value.token || ''));
const runtimeMessage = computed(() => runtimeStatus.value?.message || '-');
type RuntimeLayerStatusKey = 'kernel_status' | 'cell_status' | 'scheduler_status' | 'orchestration_status';

const statusText = (section: RuntimeLayerStatusKey, key: string, fallback = '-') => {
  const value = runtimeStatus.value?.[section]?.[key];
  return value === undefined || value === null || value === '' ? fallback : String(value);
};
const kernelMessageText = computed(() => statusText('kernel_status', 'message', runtimeMessage.value));
const kernelToggleText = computed(() => (behaviorTreeEnabled.value ? '内核开启' : '内核关闭'));
const kernelToggleTitle = computed(() => (behaviorTreeEnabled.value ? '点击关闭内核' : '点击打开内核'));
const runtimeSecondaryMessage = computed(() => {
  const message = runtimeMessage.value.trim();
  if (!message || message === '-') return '';
  return message === kernelMessageText.value.trim() ? '' : message;
});
const schedulerOwnerKey = computed<'engineering' | 'ai' | 'human' | 'isolated'>(() => {
  if (humanIsolationActive.value) return 'human';
  if (nonHumanIsolationActive.value) return 'isolated';
  return schedulerJobGroupEnabled.value ? 'engineering' : 'ai';
});
const schedulerOwnerOptions = [
  { label: '人工', value: 'human' },
  { label: 'AI', value: 'ai' },
  { label: '工程', value: 'engineering' },
];
const schedulerOwnerTitle = computed(() => {
  if (humanIsolationActive.value) return '人工使用；AI 和工程只能等待';
  if (nonHumanIsolationActive.value) return `已有隔离锁：${isolationReason.value || 'unknown'}`;
  if (schedulerJobGroupEnabled.value) return '工程使用；工程自动执行到期作业，AI 只旁观';
  return 'AI使用；工程不自动执行，到期作业由 AI 主动接管';
});
const guardItems = computed<FanxiuDataAnnotationRuntimeGuardItem[]>(() => {
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
  if (['go_scene', 'hide_floating_window'].includes(task.task_type)) return false;
  const label = task.label || '';
  if (/到.*#\d+|隐藏浮动窗|到世界|到设置页/.test(label)) return false;
  return true;
};

const taskTriggerValue = (task: FanxiuDataAnnotationSchedulerTaskItem) => {
  const exact = parseRuntimeTime(task.retry_after || task.next_time || '');
  if (exact) return exact.getTime();
  const clock = [...(task.schedule_times || [])].filter(Boolean).sort()[0] || '';
  return clock ? Date.parse(`1970-01-01T${clock}`) : Number.POSITIVE_INFINITY;
};

const shouldShowBusinessTask = (task: FanxiuDataAnnotationSchedulerTaskItem) => {
  if (!isBusinessTask(task)) return false;
  return task.supported !== false || task.enabled || ['daily', 'weekly', 'dynamic', 'manual'].includes(task.schedule_kind || '');
};

const businessTasks = computed(() => schedulerTasks.value
  .filter(shouldShowBusinessTask)
  .sort((a, b) => (
    Number(!a.enabled) - Number(!b.enabled)
    || taskTriggerValue(a) - taskTriggerValue(b)
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
  if (heartbeat.active) return '巡检进程 在线';
  const age = Number(heartbeat.age_seconds || 0);
  return age > 0 ? `巡检进程 失联 ${formatHeartbeatAge(age)}` : '巡检进程 未确认';
});
const doctorHeartbeatClass = computed(() => (doctorHeartbeat.value.active ? 'is-ok' : 'is-error'));
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
  const triggerLabels: Record<string, string> = {
    daily: '每日',
    weekly: '每周',
    dynamic: '动态',
    manual: '按需',
  };
  return triggerLabels[task.trigger_kind || task.schedule_kind || ''] || task.trigger_kind || task.schedule_kind || '按需';
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
  if (task.retry_after) return formatRuntimeTime(task.retry_after);
  if (task.next_time) return formatRuntimeTime(task.next_time);
  return '';
};

const nextTriggerTitle = (task: FanxiuDataAnnotationSchedulerTaskItem) => {
  if (task.retry_after) return `重试时间 ${task.retry_after}`;
  if (task.next_time) return task.next_time || '';
  if (task.schedule_kind === 'dynamic') return '动态作业未记录下次时间';
  if (task.schedule_kind === 'manual') return '按需触发的作业实例没有固定下次触发时间';
  return '';
};

const canAdvanceTaskNext = (task: FanxiuDataAnnotationSchedulerTaskItem | null) => (
  Boolean(task && ['daily', 'weekly'].includes(task.schedule_kind || ''))
);

const logSourceText = (entry: FanxiuDataAnnotationRuntimeLogEntry) => {
  const file = String(entry.source_file || '').trim();
  const line = entry.source_line ? `:${entry.source_line}` : '';
  const expr = String(entry.source_expr || '').trim();
  return file && expr ? `${file}${line}  ${expr}` : '';
};

const logEntryKey = (entry: FanxiuDataAnnotationRuntimeLogEntry, index: number) => (
  entry.id || `${entry.ts || ''}-${entry.time}-${entry.kind}-${entry.scope || ''}-${entry.item_id || ''}-${entry.message}-${index}`
);

const sameLogEntries = (left: FanxiuDataAnnotationRuntimeLogEntry[], right: FanxiuDataAnnotationRuntimeLogEntry[]) => (
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

const runContextTaskNow = () => {
  const task = contextMenu.value.task;
  closeLogMenu();
  if (!task) return;
  void runAction(`run-now:${task.id}`, () => runNowFanxiuDataAnnotationSchedulerTask(entryId.value, task.id, {}, true));
};

const advanceContextTaskNext = () => {
  const task = contextMenu.value.task;
  closeLogMenu();
  if (!canAdvanceTaskNext(task)) return;
  void runAction(`advance-next:${task.id}`, async () => {
    await advanceNextFanxiuDataAnnotationSchedulerTask(entryId.value, task.id);
    ElMessage.success('已推进到下一次触发');
  });
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

const getStoredHumanIsolationToken = () => localStorage.getItem(HUMAN_ISOLATION_TOKEN_KEY) || '';

const ensureHumanIsolationToken = () => {
  const stored = getStoredHumanIsolationToken();
  if (stored) return stored;
  const token = window.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  localStorage.setItem(HUMAN_ISOLATION_TOKEN_KEY, token);
  return token;
};

const warnRefreshFailure = (scope: string, error: unknown) => {
  console.warn(`${scope} failed`, error);
};

const applyStatus = (status: FanxiuDataAnnotationRuntimeStatus) => {
  runtimeStatus.value = status;
  const nextIsolation = status.isolation || {};
  const reason = String(nextIsolation.reason || '');
  const active = Boolean(nextIsolation.active);
  const token = String(nextIsolation.token || '');
  const isHuman = reason === 'human_using_runtime' || reason.startsWith('human_using_runtime:');
  if (active && isHuman && token) {
    localStorage.setItem(HUMAN_ISOLATION_TOKEN_KEY, token);
  } else if (!active) {
    localStorage.removeItem(HUMAN_ISOLATION_TOKEN_KEY);
  }
};

const refreshStatus = async () => {
  const status = await getFanxiuDataAnnotationRuntimeStatus(entryId.value, { includeCellLogs: false, includeLogs: false });
  applyStatus(status);
};

const refreshLogs = async () => {
  logsLoading.value = true;
  try {
    const response = await getFanxiuDataAnnotationRuntimeCellLogs(CELL_LOG_LIMIT, CELL_LOG_ENTRY_LIMIT);
    const nextCells = response.cells || [];
    const previousCellId = currentCellLog.value?.id || '';
    cellLogs.value = nextCells;
    const retainedIndex = previousCellId ? nextCells.findIndex((cell) => cell.id === previousCellId) : -1;
    activeCellIndex.value = retainedIndex >= 0 ? retainedIndex : 0;
    const nextLogs = currentCellLog.value?.entries || [];
    if (!sameLogEntries(logs.value, nextLogs)) {
      logs.value = nextLogs;
    }
  } finally {
    logsLoading.value = false;
  }
};

const refreshScheduler = async () => {
  const [tasksResponse, planResponse] = await Promise.all([
    getFanxiuDataAnnotationSchedulerTasks(),
    getFanxiuDataAnnotationSchedulerPlan(),
  ]);
  schedulerTasks.value = tasksResponse.tasks || [];
  schedulerPlan.value = planResponse;
  schedulerJobGroupEnabled.value = tasksResponse.job_group_enabled ?? planResponse.job_group_enabled ?? true;
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
    const [statusResult, schedulerResult] = await Promise.allSettled([refreshStatus(), refreshScheduler()]);
    if (statusResult.status === 'rejected') {
      warnRefreshFailure('refresh status', statusResult.reason);
    }
    if (schedulerResult.status === 'rejected') {
      warnRefreshFailure('refresh scheduler', schedulerResult.reason);
    }
  } finally {
    loading.value = false;
  }
  scheduleLogsRefresh();
  void refreshDoctorWatchPanel().catch((error) => {
    warnRefreshFailure('refresh doctor watch', error);
  });
};

const runAction = async (name: string, action: () => Promise<FanxiuDataAnnotationRuntimeStatus | void>) => {
  if (!entryId.value) {
    ElMessage.warning('未找到 mf 设备入口');
    return;
  }
  actionLoading.value = name;
  try {
    const status = await action();
    if (status) applyStatus(status);
    const followups = [refreshLogs(), refreshScheduler(), refreshDoctorWatchLatest()];
    await Promise.all(followups);
    ensureDoctorWatchInBackground();
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '操作失败');
  } finally {
    actionLoading.value = '';
  }
};

const toggleGuard = () => runAction('guard', () => setFanxiuDataAnnotationRuntimeGuard(entryId.value, !guardEnabled.value, 2, 'close_popups'));

const toggleGuardGroupEnabled = () => runAction('guard-group', () => setFanxiuDataAnnotationRuntimeGuardGroup(entryId.value, !guardGroupEnabled.value));

const setHumanIsolation = (enabled: boolean) => {
  const token = enabled ? ensureHumanIsolationToken() : (isolationToken.value || getStoredHumanIsolationToken());
  return setFanxiuDataAnnotationRuntimeIsolation(entryId.value, enabled, token, HUMAN_ISOLATION_TTL_SECONDS);
};

const changeSchedulerOwner = async (value: string) => {
  if (!['engineering', 'ai', 'human'].includes(value)) return;
  if (value === schedulerOwnerKey.value) return;
  const owner = value as 'engineering' | 'ai' | 'human';
  actionLoading.value = 'scheduler-owner';
  try {
    let status: FanxiuDataAnnotationRuntimeStatus | null = null;
    if (humanIsolationActive.value && owner !== 'human') {
      status = await setHumanIsolation(false);
      applyStatus(status);
    }
    if (owner === 'human') {
      if (runtimeStatus.value?.running || runtimeStatus.value?.status === 'running') {
        status = await stopFanxiuDataAnnotationRuntimeCurrentTask(entryId.value);
        applyStatus(status);
      }
      status = await setHumanIsolation(true);
      applyStatus(status);
    } else {
      const response = await setFanxiuDataAnnotationSchedulerSettings(owner === 'engineering', entryId.value);
      schedulerTasks.value = response.tasks || [];
      schedulerJobGroupEnabled.value = response.job_group_enabled ?? true;
    }
    const followups = [refreshStatus(), refreshLogs(), refreshScheduler(), refreshDoctorWatchLatest()];
    await Promise.all(followups);
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '保存失败');
  } finally {
    actionLoading.value = '';
  }
};

const toggleKernelEnabled = () => runAction(
  'kernel-toggle',
  () => setFanxiuDataAnnotationRuntimeBehaviorTree(entryId.value, !behaviorTreeEnabled.value),
);

const toggleGuardItem = (itemId: string) => {
  if (itemId === 'close_popups') {
    void toggleGuard();
    return;
  }
  void runAction(`guard:${itemId}`, () => setFanxiuDataAnnotationRuntimeGuard(entryId.value, !guardItemEnabled(itemId), 2, itemId));
};

const toggleTaskEnabled = async (task: FanxiuDataAnnotationSchedulerTaskItem) => {
  const willEnable = !task.enabled;
  actionLoading.value = `enable:${task.id}`;
  try {
    const response = await saveFanxiuDataAnnotationSchedulerTasks([{ ...task, enabled: willEnable }]);
    schedulerTasks.value = response.tasks || [];
    schedulerJobGroupEnabled.value = response.job_group_enabled ?? schedulerJobGroupEnabled.value;
    const followups = [refreshStatus(), refreshLogs(), refreshScheduler(), refreshDoctorWatchLatest()];
    await Promise.all(followups);
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '保存失败');
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
    const syncSlowState = pollTick % 2 === 0;
    void (async () => {
      try {
        try {
          await refreshStatus();
        } catch (error) {
          warnRefreshFailure('poll refresh status', error);
        }
        if (syncSlowState) {
          const slowRefreshes = [refreshLogs(), refreshScheduler(), refreshDoctorWatchLatest()];
          const scopes = ['poll refresh logs', 'poll refresh scheduler', 'poll refresh doctor watch'];
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
  await taskStore.fetchDevices();
  if (!entryId.value) {
    const mfDevice = devices.value.find((item) => item.name === machineName || item.id === machineName || item.id.includes('codepc_mf'));
    entryId.value = mfDevice?.id || devices.value[0]?.id || '';
  }
  await refreshAll();
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
          <h2>凡修行为树</h2>
        </div>
        <div class="runtime-header-controls">
          <el-button
            class="kernel-toggle-button"
            :class="{ 'is-enabled': behaviorTreeEnabled }"
            size="small"
            :loading="actionLoading === 'kernel-toggle'"
            :title="kernelToggleTitle"
            @click="toggleKernelEnabled"
          >
            {{ kernelToggleText }}
          </el-button>
        </div>
      </div>
    </header>

    <main class="runtime-main" v-loading="loading">
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
          <h3>作业</h3>
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
                  <h4>使用者</h4>
                  <p>同一时间只有一个使用者：我、AI、工程。</p>
                  <p>我使用时，AI 和工程都等待；AI 使用时，工程不自动跑；工程使用时，AI 只旁观。</p>
                </div>
              </el-popover>
            </span>
            <el-select
              class="scheduler-owner-select"
              size="small"
              :model-value="schedulerOwnerKey"
              :disabled="nonHumanIsolationActive || actionLoading === 'scheduler-owner'"
              :loading="actionLoading === 'scheduler-owner'"
              :title="schedulerOwnerTitle"
              @change="changeSchedulerOwner"
            >
              <el-option
                v-if="nonHumanIsolationActive"
                label="隔离中"
                value="isolated"
                disabled
              />
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
              <col class="col-enable" />
              <col class="col-trigger" />
            </colgroup>
            <thead>
              <tr>
                <th>序号</th>
                <th>名称</th>
                <th>触发</th>
                <th>启用</th>
                <th>下次触发</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="(task, index) in businessTasks"
                :key="task.id"
                @contextmenu.prevent.stop="openTaskMenu($event, task)"
              >
                <td><span class="index-pill">{{ index + 1 }}</span></td>
                <td :title="task.label"><strong>{{ task.label }}</strong></td>
                <td :title="taskMetaText(task)">{{ taskMetaText(task) }}</td>
                <td>
                  <button
                    class="enable-dot"
                    :class="{ enabled: task.enabled }"
                    type="button"
                    :disabled="actionLoading === `enable:${task.id}`"
                    title="切换启用"
                    @click="toggleTaskEnabled(task)"
                  />
                </td>
                <td :title="nextTriggerTitle(task)">{{ nextTriggerText(task) }}</td>
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

    <div
      v-if="contextMenu.visible"
      class="runtime-context-menu"
      :style="{ left: `${contextMenu.x}px`, top: `${contextMenu.y}px` }"
      @click.stop
    >
      <button v-if="contextMenu.task" type="button" @click="runContextTaskNow">触发一次</button>
      <button
        v-if="canAdvanceTaskNext(contextMenu.task)"
        type="button"
        @click="advanceContextTaskNext"
      >推进到下次</button>
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
  width: 582px;
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
.runtime-native-table td:nth-child(4) {
  text-align: center;
}

.runtime-native-table strong {
  font-weight: 500;
}

.runtime-native-table tbody tr {
  cursor: default;
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

@media (max-width: 900px) {
  .runtime-header {
    display: flex;
    flex-direction: column;
    align-items: stretch;
  }
}
</style>
