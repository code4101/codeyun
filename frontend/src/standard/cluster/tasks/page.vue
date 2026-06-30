<script setup lang="ts">
import { ref, onMounted, onUnmounted, nextTick, computed, watch, defineAsyncComponent } from 'vue';
import { useRouter, useRoute } from 'vue-router';
import DocPage from '@/components/DocPage.vue';
import SortableOrderHandle from '@/components/SortableOrderHandle.vue';
import api, { getDeviceEntryPath } from '@/api';
import { ElMessage, ElMessageBox } from 'element-plus';
import { Plus, VideoPlay, VideoPause, Delete, Document, Connection, Setting, View, Hide } from '@element-plus/icons-vue';
import { taskStore, type Task, type Device } from '@/store/taskStore';
import {
  addRuntimeJob,
  configureRuntimeItemAutostart,
  configureRuntimeJobSchedule,
  deleteRuntimeJob,
  fetchRuntimeJobCatalog,
  fetchRuntimeStatus,
  stopRuntimeItem,
  triggerRuntimeItem,
  triggerRuntimeJob,
  type SchedulePolicy,
  type RuntimeKind,
  type RuntimeItem,
  type RuntimeJobCatalogItem,
  type RuntimeStatusResponse,
} from '@/api/runtime';
import Sortable from 'sortablejs';

const RuntimeSystemMetricsChart = defineAsyncComponent(
  () => import('@/components/RuntimeSystemMetricsChart.vue')
);

const router = useRouter();
const route = useRoute();
const devices = computed(() => taskStore.devices);
const currentDevice = computed(() => {
  return taskStore.devices.find(d => d.id === currentDeviceId.value);
});

const runtimeStatuses = ref<Record<string, RuntimeStatusResponse>>({});
const CODEYUN_WATCHDOG_KEY = 'codeyun-watchdog';
const RUNTIME_STATUS_CACHE_KEY_PREFIX = 'codeyun.runtime-status.v1';
const RUNTIME_STATUS_CACHE_TTL_MS = 10 * 60 * 1000;
const currentRuntimeStatus = computed(() => runtimeStatuses.value[currentDeviceId.value] || null);
const currentRuntimeItems = computed<RuntimeItem[]>(() => currentRuntimeStatus.value?.items || []);
const serviceItems = computed(() => currentRuntimeItems.value.filter(item => item.kind === 'service'));
const jobItems = computed(() => currentRuntimeItems.value.filter(item => item.kind === 'job'));
const sortedJobItems = computed(() => [...jobItems.value].sort(compareRuntimeJobs));
const queueSnapshot = computed(() => currentRuntimeStatus.value?.queue || null);
const viewportWidth = ref(typeof window === 'undefined' ? 1280 : window.innerWidth);
const runtimeNameColumnWidth = computed(() => (viewportWidth.value < 960 ? 88 : 116));
const runtimeCommandColumnMinWidth = computed(() => (viewportWidth.value < 960 ? 240 : 520));
const runtimeNextColumnWidth = computed(() => (viewportWidth.value < 960 ? 96 : 130));
const runtimeLoadIssue = ref('');
const systemMonitorAnchorRef = ref<HTMLElement | null>(null);
const systemMonitorActivated = ref(false);
const getDeviceEntryMeta = (device: Device) => {
  if (device.mode === 'local') {
    return '本地入口';
  }

  if (!device.server_url) {
    return '远程入口';
  }
  try {
    return `远程 · ${new URL(device.server_url).host}`;
  } catch {
    return `远程 · ${device.server_url.replace(/^https?:\/\//, '')}`;
  }
};
const getLegacyCommandRuntimeKind = (task: Task): RuntimeKind => {
  if (task.runtime_kind === 'job' || task.runtime_kind === 'service') return task.runtime_kind;
  return task.schedule || task.schedule_policy ? 'job' : 'service';
};
const isLoopbackHost = (host: string) => {
  const normalized = host.trim().toLowerCase();
  return normalized === 'localhost' || normalized === '127.0.0.1' || normalized === '::1' || normalized === '[::1]';
};
const updateViewportWidth = () => {
  viewportWidth.value = typeof window === 'undefined' ? 1280 : window.innerWidth;
};

type RuntimeStatusCachePayload = {
  savedAt: number;
  runtime: RuntimeStatusResponse;
};

const runtimeStatusCacheKey = (deviceId: string) => `${RUNTIME_STATUS_CACHE_KEY_PREFIX}:${deviceId}`;

const cloneRuntimeStatus = (runtime: RuntimeStatusResponse): RuntimeStatusResponse => {
  const cloned = JSON.parse(JSON.stringify(runtime)) as RuntimeStatusResponse;
  cloned.items = (cloned.items || []).map((item: RuntimeItem) => ({
    ...item,
    actionLoading: false,
    toggleLoading: false,
  }));
  return cloned;
};

const buildCommandTasksFromRuntime = (runtime: RuntimeStatusResponse, deviceId: string): Task[] => (
  (runtime.items || [])
    .filter(item => item.source === 'command')
    .map((item: RuntimeItem) => ({
      ...(item.raw || {}),
      status: item.status || { running: item.active },
      entry_id: deviceId,
    })) as Task[]
);

const persistRuntimeStatusCache = (deviceId: string, runtime: RuntimeStatusResponse) => {
  if (typeof window === 'undefined' || !deviceId) return;
  try {
    const payload: RuntimeStatusCachePayload = {
      savedAt: Date.now(),
      runtime: cloneRuntimeStatus(runtime),
    };
    window.localStorage.setItem(runtimeStatusCacheKey(deviceId), JSON.stringify(payload));
  } catch (error) {
    console.warn('Failed to persist runtime status cache', error);
  }
};

const applyRuntimeStatus = (deviceId: string, runtime: RuntimeStatusResponse, persist = true) => {
  const normalized = cloneRuntimeStatus(runtime);
  runtimeStatuses.value[deviceId] = normalized;
  taskStore.tasks[deviceId] = buildCommandTasksFromRuntime(normalized, deviceId);
  if (persist) {
    persistRuntimeStatusCache(deviceId, normalized);
  }
};

const hydrateRuntimeStatusFromCache = (deviceId: string) => {
  if (typeof window === 'undefined' || !deviceId) return false;
  try {
    const raw = window.localStorage.getItem(runtimeStatusCacheKey(deviceId));
    if (!raw) return false;
    const payload = JSON.parse(raw) as Partial<RuntimeStatusCachePayload>;
    if (
      !payload
      || typeof payload.savedAt !== 'number'
      || !payload.runtime
      || Date.now() - payload.savedAt > RUNTIME_STATUS_CACHE_TTL_MS
    ) {
      window.localStorage.removeItem(runtimeStatusCacheKey(deviceId));
      return false;
    }
    applyRuntimeStatus(deviceId, payload.runtime as RuntimeStatusResponse, false);
    return true;
  } catch (error) {
    console.warn('Failed to restore runtime status cache', error);
    return false;
  }
};

const loading = ref(false); // Initial loading
const dialogVisible = ref(false);
const jobCatalogDialogVisible = ref(false);
const jobCatalogLoading = ref(false);
const jobCatalogItems = ref<RuntimeJobCatalogItem[]>([]);
const addJobTypeLoadingKey = ref('');
const scheduleDialogVisible = ref(false);
const scheduleTarget = ref<RuntimeItem | null>(null);
const deviceDialogVisible = ref(false);
const currentDeviceId = ref<string>(
  Array.isArray(route.query.entry_id)
    ? (route.query.entry_id[0] || '')
    : ((route.query.entry_id as string) || (Array.isArray(route.query.device_id) ? (route.query.device_id[0] || '') : ((route.query.device_id as string) || '')))
);
const primeRuntimeStatusCache = (deviceId: string) => {
  if (!deviceId || runtimeStatuses.value[deviceId]) {
    return false;
  }
  return hydrateRuntimeStatusFromCache(deviceId);
};
primeRuntimeStatusCache(currentDeviceId.value);
const isEditingDevice = ref(false);
const isEditingTask = ref(false);
const currentTaskId = ref<string>('');
const addDeviceLoading = ref(false);
const runtimeContextMenu = ref<{
  visible: boolean;
  x: number;
  y: number;
  target: RuntimeItem | null;
}>({
  visible: false,
  x: 0,
  y: 0,
  target: null,
});
const form = ref({
  name: '',
  command: '',
  cwd: '',
  description: '',
  device_id: '',
  runtime_kind: 'service' as RuntimeKind,
  run_as_admin: false,
  schedule: '',
  schedule_mode: 'manual',
  schedule_once_at: '',
  schedule_interval_value: 60,
  schedule_interval_unit: 'minutes',
  schedule_time: '00:00',
  schedule_weekdays: [1] as number[],
  schedule_month_day: 1,
  schedule_cron: '',
  scheduled_action: 'default',
  next_run_at: '',
  retry_enabled: false,
  retry_minutes: 10,
  timeout: null as number | null
});
const initialNextRunAt = ref('');

const nlpInput = ref('');

const parseNlp = () => {
    const text = nlpInput.value.trim();
    if (!text) return;
    
    // Simple regex rules
    const minuteMatch = text.match(/每(\d+)分钟/);
    if (minuteMatch) {
        form.value.schedule_mode = 'interval';
        form.value.schedule_interval_value = parseInt(minuteMatch[1]);
        form.value.schedule_interval_unit = 'minutes';
        ElMessage.success('已设置间隔触发');
        return;
    }
    
    if (text.includes('每小时')) {
        form.value.schedule_mode = 'interval';
        form.value.schedule_interval_value = 1;
        form.value.schedule_interval_unit = 'hours';
        ElMessage.success('已设置每小时触发');
        return;
    }

    const dailyMatch = text.match(/每天(\d+)点/);
    if (dailyMatch) {
        form.value.schedule_mode = 'daily';
        form.value.schedule_time = `${String(parseInt(dailyMatch[1])).padStart(2, '0')}:00`;
        ElMessage.success('已设置每天触发');
        return;
    }

    // New: Timeout parsing (e.g., "超时1小时", "超时30分钟")
    const timeoutHour = text.match(/超时(\d+)小时/);
    if (timeoutHour) {
        form.value.timeout = parseInt(timeoutHour[1]) * 3600;
        ElMessage.success(`已设置超时: ${timeoutHour[1]} 小时`);
        return;
    }
    const timeoutMin = text.match(/超时(\d+)分钟/);
    if (timeoutMin) {
        form.value.timeout = parseInt(timeoutMin[1]) * 60;
        ElMessage.success(`已设置超时: ${timeoutMin[1]} 分钟`);
        return;
    }

    ElMessage.warning('无法解析该自然语言描述，请直接设置定时规则');
};

const deviceForm = ref({
  mode: 'remote' as 'local' | 'remote',
  server_url: '',
  name: '',
  token: ''
});

const currentDeviceConfig = ref({
  new_name: '',
  server_url: '',
  token: '',
  token_dirty: false
});

const hiddenDeviceTokenDisplay = '••••••••••••••••';
const tokenRevealLoading = ref(false);
const isDeviceTokenVisible = ref(false);
const deviceTokenDisplayValue = computed(() => (
  isDeviceTokenVisible.value ? currentDeviceConfig.value.token : hiddenDeviceTokenDisplay
));

const resetDeviceTokenReveal = () => {
  tokenRevealLoading.value = false;
  isDeviceTokenVisible.value = false;
  currentDeviceConfig.value.token = '';
  currentDeviceConfig.value.token_dirty = false;
};

const stopEditingDevice = () => {
  isEditingDevice.value = false;
  resetDeviceTokenReveal();
};

const handleDeviceTokenInput = (value: string) => {
  if (!isDeviceTokenVisible.value) return;
  currentDeviceConfig.value.token = value;
  currentDeviceConfig.value.token_dirty = true;
};

const revealDeviceToken = async () => {
  const device = devices.value.find(d => d.id === currentDeviceId.value);
  if (!device || tokenRevealLoading.value) return;

  tokenRevealLoading.value = true;
  try {
    const token = await taskStore.fetchDeviceToken(device.id);
    if (currentDeviceId.value !== device.id || !isEditingDevice.value) return;
    currentDeviceConfig.value.token = token;
    currentDeviceConfig.value.token_dirty = false;
    isDeviceTokenVisible.value = true;
  } catch (err: any) {
    ElMessage.error(err.response?.data?.detail || '读取 Token 失败');
  } finally {
    tokenRevealLoading.value = false;
  }
};

const toggleDeviceTokenVisibility = () => {
  if (isDeviceTokenVisible.value) {
    resetDeviceTokenReveal();
    return;
  }
  revealDeviceToken();
};

const updateDeviceConfig = async () => {
  const device = devices.value.find(d => d.id === currentDeviceId.value);
  if (!device) return;

  const updates: Partial<Device> = {
    name: currentDeviceConfig.value.new_name,
    server_url: device.mode === 'remote' ? currentDeviceConfig.value.server_url : undefined
  };

  if (currentDeviceConfig.value.token_dirty) {
    const token = currentDeviceConfig.value.token.trim();
    if (!token) {
      ElMessage.warning('Token 不能为空');
      return;
    }
    updates.token = token;
  }

  try {
    await taskStore.updateDevice(device.id, updates);
    ElMessage.success('设备配置已更新');
    stopEditingDevice();
  } catch (err: any) {
    ElMessage.error(err.response?.data?.detail || '更新失败');
  }
};

let taskPollTimer: number | null = null;
let systemMonitorObserver: IntersectionObserver | null = null;
let systemMonitorWarmupTimer: number | null = null;
const taskFetchInFlight = new Set<string>();
const taskFetchVersions = new Map<string, number>();

const stopTaskPolling = () => {
  if (taskPollTimer) {
    window.clearInterval(taskPollTimer);
    taskPollTimer = null;
  }
};

const startTaskPolling = (entryId: string) => {
  stopTaskPolling();
  if (!entryId) return;
  taskPollTimer = window.setInterval(() => {
    if (currentDeviceId.value === entryId) {
      fetchTasks(entryId, true);
    }
  }, 3000);
};

const activateSystemMonitor = () => {
  if (systemMonitorActivated.value) return;
  if (systemMonitorWarmupTimer !== null) {
    window.clearTimeout(systemMonitorWarmupTimer);
    systemMonitorWarmupTimer = null;
  }
  systemMonitorActivated.value = true;
  systemMonitorObserver?.disconnect();
  systemMonitorObserver = null;
};

const scheduleSystemMonitorActivation = () => {
  if (systemMonitorActivated.value || typeof window === 'undefined') return;
  if (systemMonitorWarmupTimer !== null) {
    window.clearTimeout(systemMonitorWarmupTimer);
  }
  systemMonitorWarmupTimer = window.setTimeout(() => {
    systemMonitorWarmupTimer = null;
    activateSystemMonitor();
  }, 900);
};

const initSystemMonitorObserver = () => {
  if (systemMonitorActivated.value || typeof window === 'undefined') return;
  const anchor = systemMonitorAnchorRef.value;
  if (!anchor) return;
  if (!('IntersectionObserver' in window)) {
    activateSystemMonitor();
    return;
  }
  systemMonitorObserver?.disconnect();
  systemMonitorObserver = new IntersectionObserver((entries) => {
    if (entries.some(entry => entry.isIntersecting)) {
      activateSystemMonitor();
    }
  }, {
    root: null,
    rootMargin: '240px 0px',
    threshold: 0.01,
  });
  systemMonitorObserver.observe(anchor);
  scheduleSystemMonitorActivation();
};

const syncDeviceConfig = () => {
  const device = devices.value.find(d => d.id === currentDeviceId.value);
  if (device) {
    currentDeviceConfig.value = {
      new_name: device.name || device.device_id,
      server_url: device.server_url || '',
      token: '',
      token_dirty: false
    };
    resetDeviceTokenReveal();
  }
};

const startEditingDevice = () => {
  syncDeviceConfig();
  isEditingDevice.value = true;
};

// Watch for device switching to refresh tasks and restart polling
watch(currentDeviceId, async (newId, oldId) => {
  if (newId && newId !== oldId) {
    stopEditingDevice();
    deviceError.value = false;
    stopTaskPolling();
    await fetchTasks(newId, false);
    startTaskPolling(newId);
  } else if (!newId) {
    stopTaskPolling();
  }
});

const fetchDevices = async () => {
  const previousDeviceId = currentDeviceId.value;
  // Try to use the store's action which now calls /api/devices
  try {
    await taskStore.fetchDevices();
    let nextDeviceId = '';

    // Auto-select first device if none selected
    if (taskStore.devices.length > 0) {
       if (!currentDeviceId.value) {
           nextDeviceId = taskStore.devices[0].id;
       } else {
            const exists = taskStore.devices.find(d => d.id === currentDeviceId.value);
            if (exists) {
                nextDeviceId = exists.id;
            } else {
                const legacyDevice = taskStore.devices.find(d => d.device_id === currentDeviceId.value);
                nextDeviceId = legacyDevice?.id || taskStore.devices[0].id;
            }
       }
    } else {
        // No devices, clear selection
        nextDeviceId = '';
    }

    primeRuntimeStatusCache(nextDeviceId);
    currentDeviceId.value = nextDeviceId;

    // Update current device config if selected
    const current = taskStore.devices.find(d => d.id === currentDeviceId.value);
    if (current) {
      // No need to sync here either, it's done when "Config" button is clicked
      if (isEditingDevice.value) {
        // If somehow we are editing, close it
        stopEditingDevice();
      }
    } else {
        // If device was selected but not found in list (e.g. deleted), clear selection
        if (currentDeviceId.value) {
            currentDeviceId.value = '';
        }
    }
  } catch (err) {
    console.error('Failed to fetch devices', err);
    ElMessage.error('获取设备列表失败');
  }
  return previousDeviceId !== currentDeviceId.value;
};

const deviceError = ref(false);
const tokenDialogVisible = ref(false);
const tokenForm = ref({
    token: ''
});

const openTokenDialog = () => {
    tokenForm.value.token = '';
    tokenDialogVisible.value = true;
};

const handleUpdateToken = async () => {
    if (!tokenForm.value.token) {
        ElMessage.warning('请输入新的 Token');
        return;
    }
    
    const device = devices.value.find(d => d.id === currentDeviceId.value);
    if (!device) return;
    
    try {
        await taskStore.updateDevice(device.id, {
            token: tokenForm.value.token
        });
        ElMessage.success('Token 已更新');
        tokenDialogVisible.value = false;
        deviceError.value = false;
        fetchTasks(device.id, false); // Retry fetching tasks
    } catch (err: any) {
        ElMessage.error(err.response?.data?.detail || '更新失败');
    }
};

const fetchTasks = async (deviceId: string, isPolling: boolean) => {
    if (!deviceId) return;
    if (isPolling && taskFetchInFlight.has(deviceId)) return;
    const requestVersion = (taskFetchVersions.get(deviceId) || 0) + 1;
    taskFetchVersions.set(deviceId, requestVersion);
    const isLatestTaskFetch = () => taskFetchVersions.get(deviceId) === requestVersion;
    const isCurrentDeviceRequest = () => currentDeviceId.value === deviceId;
    if (!isPolling && !runtimeStatuses.value[deviceId]) {
      hydrateRuntimeStatusFromCache(deviceId);
    }
    const cachedRuntime = runtimeStatuses.value[deviceId];
    const hasVisibleRuntimeData = Boolean(
      (cachedRuntime?.items?.length || 0)
      || cachedRuntime?.queue?.running
      || (cachedRuntime?.queue?.pending?.length || 0)
      || (cachedRuntime?.queue?.recent?.length || 0)
    );
    taskFetchInFlight.add(deviceId);
    if (!isPolling && isCurrentDeviceRequest()) {
        runtimeLoadIssue.value = '';
    }
    
    // Check if we have cached tasks
    const hasCache = Boolean(cachedRuntime || (taskStore.tasks[deviceId] && taskStore.tasks[deviceId].length > 0));
    
    // Don't show global loading if we have cache or are polling
    if (!isPolling && !hasCache) {
        loading.value = true;
    }
    
    const device = devices.value.find(d => d.id === deviceId);
    if (!device) {
        loading.value = false;
        taskFetchInFlight.delete(deviceId);
        return;
    }
    
    try {
        const runtime = await fetchRuntimeStatus(deviceId);
        applyRuntimeStatus(deviceId, runtime);
        if (isCurrentDeviceRequest() && isLatestTaskFetch()) {
          deviceError.value = false;
          runtimeLoadIssue.value = '';
        }
        
    } catch (err: any) {
        console.error('Failed to fetch tasks', err);
        if (err.response?.status === 404) {
            await fetchLegacyTasks(deviceId, isPolling, () => isCurrentDeviceRequest() && isLatestTaskFetch());
            return;
        }
        if (!isCurrentDeviceRequest() || !isLatestTaskFetch()) {
            return;
        }
        const shouldSurfaceLoadIssue = !hasVisibleRuntimeData;
        if (err.code === 'ECONNABORTED') {
            if (shouldSurfaceLoadIssue) {
              runtimeLoadIssue.value = '运行状态读取超时，下方空表不代表远端没有服务或作业';
            }
            return;
        }
        if (!isPolling) {
            if (err.response?.status === 401 || err.response?.status === 502 || err.code === 'ERR_NETWORK') {
                deviceError.value = true;
                if (shouldSurfaceLoadIssue) {
                  runtimeLoadIssue.value = '无法通过平台代理读取运行状态，请检查后端地址或 Token';
                }
                if (!isPolling && !hasCache) {
                   ElMessage.error('无法通过平台代理连接设备，请检查后端地址或 Token');
                }
            }
        } else {
             if (err.response?.status === 401 || err.response?.status === 502) {
                  deviceError.value = true;
                 if (shouldSurfaceLoadIssue) {
                   runtimeLoadIssue.value = '无法通过平台代理读取运行状态，请检查后端地址或 Token';
                 }
              }
        }
        if (!runtimeLoadIssue.value && shouldSurfaceLoadIssue) {
            runtimeLoadIssue.value = '运行状态暂时不可用，请稍后再看当前入口的服务和作业';
        }
    } finally {
        taskFetchInFlight.delete(deviceId);
        if (!isPolling) loading.value = false;
        if (!isPolling) nextTick(initRuntimeSortables);
    }
};

const fetchLegacyTasks = async (
  deviceId: string,
  isPolling: boolean,
  shouldApplyGlobalState: () => boolean = () => currentDeviceId.value === deviceId,
) => {
  try {
    const response = await api.get(getDeviceEntryPath(deviceId, '/task/'));
    const tasks = response.data;
    tasks.forEach((t: Task) => {
      t.entry_id = deviceId;
    });
    const runtime = {
      device_id: devices.value.find(d => d.id === deviceId)?.device_id || deviceId,
      device: {},
      groups: [
        { id: 'job:legacy', kind: 'job', title: '旧命令调度' },
        { id: 'service:legacy', kind: 'service', title: '旧运行命令' },
      ],
      items: tasks.map((task: Task) => {
        const kind = getLegacyCommandRuntimeKind(task);
        return {
          id: `command:${task.id}`,
          key: task.id,
          kind,
          source: 'command',
          group_id: kind === 'job' ? 'job:legacy' : 'service:legacy',
          group_title: kind === 'job' ? '旧命令调度' : '旧运行命令',
          title: task.name,
          description: task.description,
          command: task.command,
          cwd: task.cwd,
          runtime_kind: kind,
          schedule: task.schedule,
          schedule_policy: task.schedule_policy,
          schedule_state: task.schedule_state,
          schedule_status: task.schedule_status,
          schedule_label: task.schedule || '',
          next_run_at: task.next_run_at,
          timeout: task.timeout,
          order: 0,
          active: Boolean(task.status?.running),
          status: task.status || { running: false },
          actions: ['start', 'stop', 'logs', 'delete', 'reorder'],
          raw: task as any,
        };
      }),
      queue: null,
      runner_running: false,
      next_wake_at: null,
    } as RuntimeStatusResponse;
    applyRuntimeStatus(deviceId, runtime);
    if (shouldApplyGlobalState()) {
      deviceError.value = false;
      runtimeLoadIssue.value = '';
    }
  } catch (err: any) {
    if (!isPolling && shouldApplyGlobalState()) {
      deviceError.value = true;
      runtimeLoadIssue.value = '无法读取运行列表，请检查当前入口连接状态';
      ElMessage.error(err.response?.data?.detail || '无法读取运行列表');
    }
  }
};

const handleStatusClick = async (item: RuntimeItem) => {
    if (item.source !== 'command') return;
    const device = devices.value.find(d => d.id === currentDeviceId.value);
    if (!device) return;
    
    item.actionLoading = true;
    try {
        const action = item.active || item.status?.running ? 'stop' : 'start';
        await api.post(getDeviceEntryPath(device.id, `/task/${item.key}/${action}`));
        
        // Optimistic update
        item.active = action === 'start';
        item.status = {
          ...item.status,
          running: action === 'start',
        };
        
        // Refresh after delay
        setTimeout(() => fetchTasks(device.id, true), 1000);
        
    } catch (err: any) {
        ElMessage.error(err.response?.data?.detail || '操作失败');
    } finally {
        item.actionLoading = false;
    }
};

const handleServiceStatusClick = async (item: RuntimeItem) => {
  if (item.kind !== 'service') return;
  if (item.source === 'command') {
    await handleStatusClick(item);
    return;
  }

  item.actionLoading = true;
  try {
    if (isRuntimeItemRunning(item)) {
      await stopRuntimeItem(currentDeviceId.value, item.source, item.key);
    } else {
      await triggerRuntimeItem(currentDeviceId.value, item.source, item.key);
    }
    await fetchTasks(currentDeviceId.value, true);
  } catch (err: any) {
    ElMessage.error(err.response?.data?.detail || '操作失败');
  } finally {
    item.actionLoading = false;
  }
};

const handleTriggerRuntimeJob = async (item: RuntimeItem) => {
  if (item.source !== 'builtin') return;
  item.actionLoading = true;
  try {
    await triggerRuntimeJob(currentDeviceId.value, item.key);
    ElMessage.success('作业已提交');
    await fetchTasks(currentDeviceId.value, true);
  } catch (err: any) {
    ElMessage.error(err.response?.data?.detail || '作业提交失败');
  } finally {
    item.actionLoading = false;
  }
};

const handleTriggerRuntimeItem = async (item: RuntimeItem) => {
  item.actionLoading = true;
  try {
    const result = await triggerRuntimeItem(currentDeviceId.value, item.source, item.key);
    ElMessage.success(result?.queued === false ? '作业已在队列中' : '作业已提交');
    await fetchTasks(currentDeviceId.value, true);
  } catch (err: any) {
    ElMessage.error(err.response?.data?.detail || '作业提交失败');
  } finally {
    item.actionLoading = false;
  }
};

const isRuntimeItemRunning = (item: RuntimeItem | null | undefined) => {
  return Boolean(item?.active || item?.status?.running);
};

const isRuntimeItemQueued = (item: RuntimeItem | null | undefined) => {
  return Boolean(item?.status?.queued && !isRuntimeItemRunning(item));
};

const handleJobStatusClick = async (item: RuntimeItem) => {
  if (item.kind !== 'job') return;
  if (item.source === 'command') {
    if (isRuntimeItemRunning(item)) {
      await handleStatusClick(item);
      return;
    }
    if (!isRuntimeItemQueued(item)) {
      await handleTriggerRuntimeItem(item);
    }
    return;
  }
  if (!isRuntimeItemRunning(item)) {
    await handleTriggerRuntimeJob(item);
  }
};

const handleSwitchDevice = (device: Device) => {
    currentDeviceId.value = device.id;
    deviceDialogVisible.value = false;
};

const closeRuntimeContextMenu = () => {
  runtimeContextMenu.value.visible = false;
  runtimeContextMenu.value.target = null;
};

const handleRuntimeRowContextMenu = (row: RuntimeItem, _column: unknown, event: MouseEvent) => {
  event.preventDefault();
  runtimeContextMenu.value = {
    visible: true,
    x: event.clientX,
    y: event.clientY,
    target: row,
  };
};

const handleRuntimeContextDelete = async () => {
  const target = runtimeContextMenu.value.target;
  closeRuntimeContextMenu();
  if (!target) return;
  await handleDelete(target);
};

const handleRuntimeContextLogs = () => {
  const target = runtimeContextMenu.value.target;
  closeRuntimeContextMenu();
  if (!target) return;
  viewLogs(target);
};

const handleRuntimeContextExecute = async () => {
  const target = runtimeContextMenu.value.target;
  closeRuntimeContextMenu();
  if (!target || target.kind !== 'job') return;
  await handleStatusClick(target);
};

const handleRuntimeContextConfigure = () => {
  const target = runtimeContextMenu.value.target;
  closeRuntimeContextMenu();
  if (!target) return;
  if (target.source === 'command') {
    openEditDialog(target);
  } else if (target.source === 'builtin' && target.kind === 'job') {
    openBuiltinScheduleDialog(target);
  } else if (target.source === 'builtin' && target.kind === 'service') {
    if (target.key === CODEYUN_WATCHDOG_KEY) {
      void handleConfigureWatchdogAutostart(target);
      return;
    }
    viewLogs(target);
  }
};

const handleConfigureWatchdogAutostart = async (item: RuntimeItem) => {
  const startup = item.status?.startup || {};
  const nextEnabled = !startup.enabled;
  try {
    await ElMessageBox.confirm(
      nextEnabled
        ? '将在 Windows 计划任务中创建或更新 CodeYun Watchdog 登录自启项。'
        : '将禁用 CodeYun Watchdog 登录自启项，不会停止当前正在运行的守护。',
      nextEnabled ? '开启开机自启' : '关闭开机自启',
      {
        confirmButtonText: nextEnabled ? '开启' : '关闭',
        cancelButtonText: '取消',
        type: nextEnabled ? 'info' : 'warning',
      }
    );
  } catch {
    return;
  }

  item.actionLoading = true;
  try {
    await configureRuntimeItemAutostart(currentDeviceId.value, item.source, item.key, nextEnabled);
    ElMessage.success(nextEnabled ? '已开启开机自启' : '已关闭开机自启');
    await fetchTasks(currentDeviceId.value, true);
  } catch (err: any) {
    ElMessage.error(err.response?.data?.detail || '配置失败');
  } finally {
    item.actionLoading = false;
  }
};

const canDeleteRuntimeItem = (item: RuntimeItem | null | undefined) => {
  return Boolean(item && (item.source === 'command' || item.kind === 'job'));
};

const runtimeKindLabel = (kind: RuntimeKind | null | undefined) => kind === 'job' ? '作业' : '服务';

const scheduleUnitSeconds = (unit: string) => {
  if (unit === 'hours') return 3600;
  if (unit === 'days') return 86400;
  return 60;
};

const defaultScheduledAction = (kind: RuntimeKind) => kind === 'job' ? 'enqueue' : 'restart';

const toDateTimeInputValue = (value: string | null | undefined) => {
  if (!value) return '';
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return String(value).slice(0, 19);
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}T${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}`;
};

const setFormNextRunAt = (value: string | null | undefined) => {
  const formatted = toDateTimeInputValue(value);
  form.value.next_run_at = formatted;
  initialNextRunAt.value = formatted;
};

const buildNextRunAtPatch = () => {
  const current = form.value.next_run_at || '';
  if (current === initialNextRunAt.value) return {};
  return { next_run_at: current.trim() || null };
};

const resetScheduleFields = () => {
  form.value.schedule = '';
  form.value.schedule_mode = 'manual';
  form.value.schedule_once_at = '';
  form.value.schedule_interval_value = 60;
  form.value.schedule_interval_unit = 'minutes';
  form.value.schedule_time = '00:00';
  form.value.schedule_weekdays = [1];
  form.value.schedule_month_day = 1;
  form.value.schedule_cron = '';
  form.value.scheduled_action = 'default';
  form.value.retry_enabled = false;
  form.value.retry_minutes = 10;
};

const scheduleTimeValue = (trigger: Record<string, any>, fallback = '00:00') => {
  const value = trigger.times ?? trigger.time;
  if (Array.isArray(value)) return String(value[0] || fallback);
  return String(value || fallback);
};

const applySchedulePolicyToForm = (policy: SchedulePolicy | null | undefined) => {
  resetScheduleFields();
  if (!policy?.enabled || !policy.trigger) return;

  const trigger = policy.trigger || {};
  const mode = String(trigger.type || 'manual');
  if (mode === 'once') {
    form.value.schedule_mode = 'once';
    form.value.schedule_once_at = String(trigger.at || trigger.time || '');
  } else if (mode === 'interval') {
    const seconds = Number(trigger.seconds || 0);
    form.value.schedule_mode = 'interval';
    if (seconds > 0 && seconds % 86400 === 0) {
      form.value.schedule_interval_value = seconds / 86400;
      form.value.schedule_interval_unit = 'days';
    } else if (seconds > 0 && seconds % 3600 === 0) {
      form.value.schedule_interval_value = seconds / 3600;
      form.value.schedule_interval_unit = 'hours';
    } else {
      form.value.schedule_interval_value = Math.max(1, Math.round(seconds / 60));
      form.value.schedule_interval_unit = 'minutes';
    }
  } else if (mode === 'daily') {
    form.value.schedule_mode = 'daily';
    form.value.schedule_time = scheduleTimeValue(trigger);
  } else if (mode === 'weekly') {
    form.value.schedule_mode = 'weekly';
    const weekdays = trigger.weekdays || trigger.days || [1];
    form.value.schedule_weekdays = Array.isArray(weekdays) ? weekdays.map(Number) : [Number(weekdays || 1)];
    form.value.schedule_time = scheduleTimeValue(trigger);
  } else if (mode === 'monthly') {
    form.value.schedule_mode = 'monthly';
    const dayValue = Array.isArray(trigger.days) ? trigger.days[0] : (trigger.day ?? 1);
    form.value.schedule_month_day = Math.min(31, Math.max(1, Number(dayValue === 'last' ? 31 : dayValue || 1)));
    form.value.schedule_time = scheduleTimeValue(trigger);
  } else if (mode === 'cron') {
    form.value.schedule_mode = 'cron';
    form.value.schedule_cron = String(trigger.expression || trigger.cron || '');
  }

  const action = String(policy.action?.type || '');
  form.value.scheduled_action = action && action !== defaultScheduledAction(form.value.runtime_kind)
    ? action
    : 'default';

  const retry = policy.outcome?.on_failure || policy.outcome?.on_timeout;
  if (retry?.type === 'retry_after') {
    form.value.retry_enabled = true;
    form.value.retry_minutes = Number(retry.minutes || Math.max(1, Math.round(Number(retry.seconds || 600) / 60)));
  }
};

const buildSchedulePolicyFromForm = (): SchedulePolicy | null => {
  const mode = form.value.schedule_mode;
  if (!mode || mode === 'manual') return null;

  let trigger: Record<string, any> | null = null;
  if (mode === 'once') {
    const at = form.value.schedule_once_at.trim();
    if (!at) return null;
    trigger = { type: 'once', at };
  } else if (mode === 'interval') {
    const value = Number(form.value.schedule_interval_value || 0);
    if (value <= 0) return null;
    trigger = {
      type: 'interval',
      seconds: value * scheduleUnitSeconds(form.value.schedule_interval_unit),
      anchor: form.value.runtime_kind === 'job' ? 'last_finish' : 'last_trigger',
    };
  } else if (mode === 'daily') {
    trigger = { type: 'daily', time: form.value.schedule_time || '00:00' };
  } else if (mode === 'weekly') {
    trigger = {
      type: 'weekly',
      weekdays: form.value.schedule_weekdays?.length ? form.value.schedule_weekdays : [1],
      time: form.value.schedule_time || '00:00',
    };
  } else if (mode === 'monthly') {
    trigger = {
      type: 'monthly',
      day: Number(form.value.schedule_month_day || 1),
      time: form.value.schedule_time || '00:00',
    };
  } else if (mode === 'cron') {
    const expression = form.value.schedule_cron.trim();
    if (!expression) return null;
    trigger = { type: 'cron', expression };
  }

  if (!trigger) return null;
  const actionType = form.value.scheduled_action === 'default'
    ? defaultScheduledAction(form.value.runtime_kind)
    : form.value.scheduled_action;
  const policy: SchedulePolicy = {
    enabled: true,
    trigger,
    action: { type: actionType },
    concurrency: form.value.runtime_kind === 'job'
      ? { scope: 'group', policy: 'queue' }
      : { scope: 'unit', policy: 'replace' },
  };
  if (form.value.retry_enabled) {
    policy.outcome = {
      on_failure: { type: 'retry_after', minutes: Number(form.value.retry_minutes || 10) },
      on_timeout: { type: 'retry_after', minutes: Number(form.value.retry_minutes || 10) },
    };
  }
  return policy;
};

const buildTaskPayloadFromForm = () => ({
  name: form.value.name,
  command: form.value.command,
  cwd: form.value.cwd,
  description: form.value.description,
  device_id: form.value.device_id,
  runtime_kind: form.value.runtime_kind,
  run_as_admin: form.value.run_as_admin,
  schedule: '',
  schedule_policy: buildSchedulePolicyFromForm(),
  timeout: form.value.timeout,
  ...buildNextRunAtPatch(),
});

const openCreateDialog = (kind: RuntimeKind) => {
    if (!currentDeviceId.value) {
        ElMessage.warning('请先选择一个设备');
        return;
    }
    
    isEditingTask.value = false;
    currentTaskId.value = '';
    form.value = {
        name: '',
        command: '',
        cwd: '',
        description: '',
        device_id: currentDevice.value?.device_id || '',
        runtime_kind: kind,
        run_as_admin: false,
        schedule: '',
        schedule_mode: 'manual',
        schedule_once_at: '',
        schedule_interval_value: 60,
        schedule_interval_unit: 'minutes',
        schedule_time: '00:00',
        schedule_weekdays: [1],
        schedule_month_day: 1,
        schedule_cron: '',
        scheduled_action: 'default',
        next_run_at: '',
        retry_enabled: false,
        retry_minutes: 10,
        timeout: null
    };
    initialNextRunAt.value = '';
    dialogVisible.value = true;
};

const openJobCatalogDialog = async () => {
    if (!currentDeviceId.value) {
        ElMessage.warning('请先选择一个设备');
        return;
    }
    jobCatalogDialogVisible.value = true;
    jobCatalogLoading.value = true;
    try {
        const payload = await fetchRuntimeJobCatalog(currentDeviceId.value);
        jobCatalogItems.value = payload.items || [];
    } catch (err: any) {
        ElMessage.error(err.response?.data?.detail || '读取作业类型失败');
    } finally {
        jobCatalogLoading.value = false;
    }
};

const handleAddBuiltinJobType = async (item: RuntimeJobCatalogItem) => {
    if (!currentDeviceId.value || item.added || addJobTypeLoadingKey.value) return;
    addJobTypeLoadingKey.value = item.key;
    try {
        await addRuntimeJob(currentDeviceId.value, item.key);
        ElMessage.success('已加入作业清单');
        await Promise.all([
          fetchTasks(currentDeviceId.value, false),
          fetchRuntimeJobCatalog(currentDeviceId.value).then(payload => {
            jobCatalogItems.value = payload.items || [];
          }),
        ]);
    } catch (err: any) {
        ElMessage.error(err.response?.data?.detail || '添加失败');
    } finally {
        addJobTypeLoadingKey.value = '';
    }
};

const openCustomJobDialog = () => {
    jobCatalogDialogVisible.value = false;
    openCreateDialog('job');
};

const openEditDialog = (item: RuntimeItem) => {
    if (item.source !== 'command') return;

    isEditingTask.value = true;
    currentTaskId.value = item.key;
    form.value = {
        name: String(item.raw?.name || item.title || ''),
        command: item.command || '',
        cwd: item.cwd || '',
        description: item.description || '',
        device_id: currentDevice.value?.device_id || '',
        runtime_kind: item.kind,
        run_as_admin: Boolean(item.raw?.run_as_admin),
        schedule: item.schedule || '',
        schedule_mode: 'manual',
        schedule_once_at: '',
        schedule_interval_value: 60,
        schedule_interval_unit: 'minutes',
        schedule_time: '00:00',
        schedule_weekdays: [1],
        schedule_month_day: 1,
        schedule_cron: '',
        scheduled_action: 'default',
        next_run_at: '',
        retry_enabled: false,
        retry_minutes: 10,
        timeout: item.timeout ?? null
    };
    setFormNextRunAt(getRuntimeNextRunAt(item));
    applySchedulePolicyToForm(item.schedule_policy);
    if (!item.schedule_policy && item.schedule) {
        form.value.schedule_mode = 'cron';
        form.value.schedule_cron = item.schedule;
    }
    dialogVisible.value = true;
};

const openBuiltinScheduleDialog = (item: RuntimeItem) => {
    if (item.source !== 'builtin') return;

    scheduleTarget.value = item;
    form.value.runtime_kind = 'job';
    resetScheduleFields();
    setFormNextRunAt(getRuntimeNextRunAt(item));
    applySchedulePolicyToForm(item.schedule_policy);
    if (!item.schedule_policy && item.schedule) {
        form.value.schedule_mode = 'cron';
        form.value.schedule_cron = item.schedule;
    }
    scheduleDialogVisible.value = true;
};

const handleSubmitTask = async () => {
    if (!form.value.name || !form.value.command) {
        ElMessage.warning('名称和命令必填');
        return;
    }
    
    const entryId = currentDeviceId.value;
    const device = devices.value.find(d => d.id === entryId);
    if (!device) return;
    
    try {
        const payload = buildTaskPayloadFromForm();
        if (isEditingTask.value) {
            await api.post(getDeviceEntryPath(entryId, `/task/${currentTaskId.value}/update`), payload);
            ElMessage.success(`${runtimeKindLabel(form.value.runtime_kind)}已保存`);
        } else {
            await api.post(getDeviceEntryPath(entryId, '/task/create'), payload);
            ElMessage.success(`${runtimeKindLabel(form.value.runtime_kind)}创建成功`);
        }
        
        dialogVisible.value = false;
        fetchTasks(entryId, false);
        
    } catch (err: any) {
        ElMessage.error(err.response?.data?.detail || '创建失败');
    }
};

const handleSubmitBuiltinSchedule = async () => {
    const target = scheduleTarget.value;
    if (!target) return;

    try {
        const policy = buildSchedulePolicyFromForm();
        const nextRunPatch = buildNextRunAtPatch() as { next_run_at?: string | null };
        await configureRuntimeJobSchedule(currentDeviceId.value, target.key, policy, nextRunPatch.next_run_at);
        ElMessage.success('定时已保存');
        scheduleDialogVisible.value = false;
        scheduleTarget.value = null;
        await fetchTasks(currentDeviceId.value, false);
    } catch (err: any) {
        ElMessage.error(err.response?.data?.detail || '保存失败');
    }
};

const handleAddDevice = async () => {
  if (!deviceForm.value.token.trim()) {
    ElMessage.warning('Token is required');
    return;
  }

  if (deviceForm.value.mode === 'remote') {
    let serverUrl = deviceForm.value.server_url.trim();
    if (!serverUrl) {
      ElMessage.warning('远程模式必须填写后端地址');
      return;
    }

    if (!serverUrl.startsWith('http://') && !serverUrl.startsWith('https://')) {
      serverUrl = 'http://' + serverUrl;
      deviceForm.value.server_url = serverUrl;
    }

    try {
      const urlObj = new URL(serverUrl);
      if (isLoopbackHost(urlObj.hostname)) {
        ElMessage.warning('localhost、127.0.0.1、::1 不能作为远程设备后端地址，请改用本地设备模式');
        return;
      }
      if (!urlObj.port) {
        urlObj.port = '8000';
        serverUrl = urlObj.toString();
      }
      if (serverUrl.endsWith('/')) {
        serverUrl = serverUrl.slice(0, -1);
      }
      deviceForm.value.server_url = serverUrl;
    } catch (e) {
      console.warn('Server URL parse failed, using raw value', e);
    }
  }
  
  addDeviceLoading.value = true;
  
  try {
    const newDevice = await taskStore.addDevice({
        mode: deviceForm.value.mode,
        name: deviceForm.value.name,
        server_url: deviceForm.value.mode === 'remote' ? deviceForm.value.server_url : undefined,
        token: deviceForm.value.token.trim()
    });
    
    ElMessage.success('Device added successfully');
    
    deviceForm.value = { mode: 'remote', server_url: '', name: '', token: '' };
    currentDeviceId.value = newDevice.id;
    deviceDialogVisible.value = false;
    
  } catch (err: any) {
    console.error(err);
    const errorMsg = err.response?.data?.detail || err.message || 'Failed to add device';
    ElMessage.error(errorMsg);
  } finally {
    addDeviceLoading.value = false;
  }
};

const handleDeleteDevice = async (device: Device) => {
  try {
    await ElMessageBox.confirm(`确定要移除设备 "${device.name}" 的关联吗? (不会影响设备本身运行)`, '警告', {
      type: 'warning',
      confirmButtonText: '移除',
      cancelButtonText: '取消'
    });
    
    await taskStore.removeDevice(device.id);
    ElMessage.success('设备关联已移除');
    
    // Refresh list handled by store, just check selection
    if (taskStore.devices.length > 0) {
        if (!taskStore.devices.find(d => d.id === currentDeviceId.value)) {
            currentDeviceId.value = taskStore.devices[0].id;
        }
    } else {
        currentDeviceId.value = '';
    }
  } catch (err) {
    if (err !== 'cancel') {
        console.error(err);
        ElMessage.error('删除失败');
    }
  }
};

const handleDelete = async (item: RuntimeItem) => {
  try {
    await ElMessageBox.confirm(`确定删除 "${item.title}" 吗？`, '删除确认', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消'
    });

    const device = devices.value.find(d => d.id === currentDeviceId.value);
    if (!device) return;

    if (item.source === 'builtin') {
      await deleteRuntimeJob(device.id, item.key);
    } else {
      await api.delete(getDeviceEntryPath(device.id, `/task/${item.key}`));
    }
    ElMessage.success('已删除');
    await fetchTasks(currentDeviceId.value, false);
  } catch (err) {
    // Cancelled
  }
};

const viewLogs = (item: RuntimeItem) => {
  router.push({
    name: 'TaskLogs',
    params: { id: item.key },
    query: { entry_id: currentDeviceId.value, source: item.source },
  });
};

const jobRuntimeStatusType = (item: RuntimeItem): 'success' | 'info' | 'warning' => {
  if (isRuntimeItemQueued(item)) return 'warning';
  return isRuntimeItemRunning(item) ? 'success' : 'info';
};

const isJobStatusButtonDisabled = (item: RuntimeItem) => {
  return isRuntimeItemQueued(item) || (item.source === 'builtin' && isRuntimeItemRunning(item));
};

const getJobStatusButtonTitle = (item: RuntimeItem) => {
  if (isRuntimeItemQueued(item)) return '排队中';
  if (!isRuntimeItemRunning(item)) return '点击执行';
  return item.source === 'command' ? '点击停止' : '作业正在运行';
};

const getServiceStatusButtonTitle = (item: RuntimeItem) => {
  return isRuntimeItemRunning(item) ? '点击停止' : '点击启动';
};

function getRuntimeNextRunAt(item: RuntimeItem): string {
  const value = item.schedule_status?.next_run_at || item.next_run_at || item.status?.next_run_at || item.raw?.next_run_at || '';
  return typeof value === 'string' ? value : '';
}

function getRuntimeNextRunTime(item: RuntimeItem): number {
  const nextRunAt = getRuntimeNextRunAt(item);
  if (!nextRunAt) return Number.POSITIVE_INFINITY;
  const time = new Date(nextRunAt).getTime();
  return Number.isFinite(time) ? time : Number.POSITIVE_INFINITY;
}

function isRuntimeJobEnabledForSort(item: RuntimeItem): boolean {
  if (item.source === 'builtin') return Boolean(item.enabled);
  return Boolean(item.schedule || item.schedule_label || getRuntimeNextRunAt(item) || item.active || item.status?.running);
}

function compareRuntimeJobs(a: RuntimeItem, b: RuntimeItem): number {
  const activeDiff = Number(Boolean(b.active || b.status?.running)) - Number(Boolean(a.active || a.status?.running));
  if (activeDiff) return activeDiff;

  const enabledDiff = Number(isRuntimeJobEnabledForSort(b)) - Number(isRuntimeJobEnabledForSort(a));
  if (enabledDiff) return enabledDiff;

  const aNext = getRuntimeNextRunTime(a);
  const bNext = getRuntimeNextRunTime(b);
  const aHasNext = Number.isFinite(aNext);
  const bHasNext = Number.isFinite(bNext);
  if (aHasNext !== bHasNext) return aHasNext ? -1 : 1;
  if (aHasNext && aNext !== bNext) return aNext - bNext;

  return (a.order || 0) - (b.order || 0);
}

const pad2 = (value: number) => String(value).padStart(2, '0');

const formatRuntimeNextTrigger = (item: RuntimeItem) => {
  const nextRunAt = getRuntimeNextRunAt(item);
  if (!nextRunAt) return '';

  const date = new Date(nextRunAt);
  if (!Number.isFinite(date.getTime())) return '';

  const now = new Date();
  const time = `${pad2(date.getHours())}:${pad2(date.getMinutes())}`;
  const isSameDate = date.getFullYear() === now.getFullYear()
    && date.getMonth() === now.getMonth()
    && date.getDate() === now.getDate();
  const isWithinNext24Hours = date.getTime() >= now.getTime()
    && date.getTime() - now.getTime() < 24 * 60 * 60 * 1000;
  if (isSameDate || isWithinNext24Hours) return time;

  const monthDayTime = `${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ${time}`;
  if (date.getFullYear() === now.getFullYear()) return monthDayTime;
  return `${date.getFullYear()}-${monthDayTime}`;
};

const getRuntimeNextTriggerTitle = (item: RuntimeItem) => {
  const nextRunAt = getRuntimeNextRunAt(item);
  if (!nextRunAt) return '';
  const date = new Date(nextRunAt);
  return Number.isFinite(date.getTime()) ? date.toLocaleString() : nextRunAt;
};

const runtimeQueueRecordName = (item: Record<string, any>) => {
  const metadata = item?.metadata || {};
  return item?.display_name || metadata.title || metadata.task_title || item?.name || item?.id;
};

const formatQueueRecordTimestamp = (value: unknown) => {
  if (value === null || value === undefined || value === '') return '';
  const raw = typeof value === 'number' ? value * 1000 : value;
  const date = new Date(raw as any);
  if (!Number.isFinite(date.getTime())) return '';

  const now = new Date();
  const time = `${pad2(date.getHours())}:${pad2(date.getMinutes())}`;
  const sameDate = date.getFullYear() === now.getFullYear()
    && date.getMonth() === now.getMonth()
    && date.getDate() === now.getDate();
  if (sameDate) return time;

  const monthDayTime = `${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ${time}`;
  if (date.getFullYear() === now.getFullYear()) return monthDayTime;
  return `${date.getFullYear()}-${monthDayTime}`;
};

const getQueueRecordFinishedLabel = (item: Record<string, any>) => (
  formatQueueRecordTimestamp(item?.finished_at) || item?.status || '完成'
);

const getQueueRecordTimeTitle = (value: unknown) => {
  if (value === null || value === undefined || value === '') return '';
  const raw = typeof value === 'number' ? value * 1000 : value;
  const date = new Date(raw as any);
  return Number.isFinite(date.getTime()) ? date.toLocaleString() : '';
};

const getRuntimeRowClassName = ({ row }: { row: RuntimeItem }) => (
  row.source === 'command' ? '' : 'is-not-sortable'
);

const serviceTableRef = ref<any>(null);
const tabsRef = ref<any>(null);
let serviceSortableInstance: Sortable | null = null;
let tabsSortableInstance: Sortable | null = null;

const createRuntimeSortable = (
  tableComponent: any,
  items: RuntimeItem[],
  assignInstance: (instance: Sortable | null) => void
) => {
  if (!tableComponent) {
    assignInstance(null);
    return;
  }
  const el = tableComponent.$el.querySelector('.el-table__body-wrapper tbody');
  if (!el) {
    assignInstance(null);
    return;
  }
  const commandItems = items.filter(item => item.source === 'command');
  if (!commandItems.length) {
    assignInstance(null);
    return;
  }

  const instance = Sortable.create(el, {
    handle: '.sortable-order-handle',
    animation: 150,
    filter: '.is-not-sortable',
    onMove: (evt: any) => !evt.related?.classList.contains('is-not-sortable'),
    onEnd: async ({ newIndex, oldIndex }: any) => {
      if (newIndex === oldIndex || newIndex == null || oldIndex == null) return;
      
      const currList = [...commandItems];
      const targetRow = currList.splice(oldIndex, 1)[0];
      if (!targetRow) return;
      currList.splice(newIndex, 0, targetRow);

      const device = devices.value.find(d => d.id === currentDeviceId.value);
      if (!device) return;
      
      try {
        await api.post(getDeviceEntryPath(device.id, '/task/reorder'), currList.map(t => t.key));
        ElMessage.success('顺序已更新');
        await fetchTasks(currentDeviceId.value, true);
      } catch (err) {
        ElMessage.error('排序保存失败');
        fetchTasks(currentDeviceId.value, true);
      }
    }
  });
  assignInstance(instance);
};

const initRuntimeSortables = () => {
  if (serviceSortableInstance) serviceSortableInstance.destroy();
  serviceSortableInstance = null;

  createRuntimeSortable(serviceTableRef.value, serviceItems.value, instance => {
    serviceSortableInstance = instance;
  });
};

const initSortable = () => {
  initRuntimeSortables();
};

const initDeviceSortable = () => {
  if (tabsSortableInstance) tabsSortableInstance.destroy();
  if (!tabsRef.value) return;
  
  const navEl = tabsRef.value.$el.querySelector('.el-tabs__nav');
  if (!navEl) return;
  
  tabsSortableInstance = Sortable.create(navEl, {
    animation: 150,
    filter: '.is-disabled', // The Add Device tab is disabled
    onMove: (evt: any) => {
        // Prevent dragging past the last element (Add Device)
        // If the target element is disabled, don't allow dropping there
        if (evt.related.classList.contains('is-disabled')) return false;
        return true;
    },
    onEnd: async ({ newIndex, oldIndex }: any) => {
      if (newIndex === oldIndex) return;
      
      const currList = [...taskStore.devices];
      if (oldIndex >= currList.length || newIndex >= currList.length) return;
      
      const targetDev = currList.splice(oldIndex, 1)[0];
      currList.splice(newIndex, 0, targetDev);
      
      taskStore.devices = currList;
      
      try {
          await taskStore.reorderDevices(currList.map(d => d.id));
          ElMessage.success('设备顺序已更新');
      } catch (err) {
          ElMessage.error('设备排序保存失败');
          fetchDevices();
      }
    }
  });
};

onMounted(async () => {
  window.addEventListener('click', closeRuntimeContextMenu);
  window.addEventListener('resize', updateViewportWidth);
  updateViewportWidth();
  let selectionChangedDuringBootstrap = false;
  if (taskStore.devices.length > 0) {
    // Cache hit: trigger background refresh, don't wait
    void fetchDevices();
  } else {
    // No cache: must wait
    selectionChangedDuringBootstrap = await fetchDevices();
  }

  if (currentDeviceId.value && !selectionChangedDuringBootstrap) {
      await fetchTasks(currentDeviceId.value, false);
      startTaskPolling(currentDeviceId.value);
  }
  
  nextTick(() => {
    initSystemMonitorObserver();
    initSortable();
    initDeviceSortable();
  });
  
});

onUnmounted(() => {
  window.removeEventListener('click', closeRuntimeContextMenu);
  window.removeEventListener('resize', updateViewportWidth);
  stopTaskPolling();
  if (systemMonitorWarmupTimer !== null) {
    window.clearTimeout(systemMonitorWarmupTimer);
    systemMonitorWarmupTimer = null;
  }
  systemMonitorObserver?.disconnect();
  systemMonitorObserver = null;
  if (serviceSortableInstance) serviceSortableInstance.destroy();
  if (tabsSortableInstance) tabsSortableInstance.destroy();
});
</script>

<template>
  <DocPage title="运行管理">
    <!-- Device Tabs -->
    <el-tabs v-model="currentDeviceId" type="card" class="device-tabs" ref="tabsRef">
      <el-tab-pane
        v-for="dev in devices"
        :key="dev.id"
        :name="dev.id"
      >
        <template #label>
          <div class="device-tab-label">
            <span class="device-tab-name">{{ dev.name || dev.device_id }}</span>
            <span class="device-tab-meta">{{ getDeviceEntryMeta(dev) }}</span>
          </div>
        </template>
      </el-tab-pane>
      <el-tab-pane name="add_new" disabled>
        <template #label>
          <el-button link :icon="Plus" @click="deviceDialogVisible = true" class="add-device-btn">添加设备</el-button>
        </template>
      </el-tab-pane>
    </el-tabs>

    <!-- Device Info & Config -->
    <div v-if="currentDevice" class="device-toolbar">
      <el-button
        type="info"
        link
        size="small"
        :icon="Delete"
        @click="handleDeleteDevice(currentDevice)"
        style="color: #909399;"
      >移除设备</el-button>
      <el-button v-if="deviceError" type="danger" size="small" :icon="Connection" @click="openTokenDialog">重连 / 更新 Token</el-button>
      <el-button v-if="!isEditingDevice" :icon="Setting" size="small" @click="startEditingDevice">配置</el-button>
      <template v-else>
        <el-button size="small" @click="stopEditingDevice">取消</el-button>
        <el-button type="primary" size="small" @click="updateDeviceConfig">保存</el-button>
      </template>
    </div>

    <div v-if="currentDevice && isEditingDevice" class="device-edit-panel">
      <el-form :inline="true" label-width="100px" size="small">
        <el-form-item label="设备名称">
           <el-input
             v-model="currentDeviceConfig.new_name"
             placeholder="设备显示名称"
             style="width: 200px;"
           />
        </el-form-item>
        <el-form-item v-if="currentDevice.mode === 'remote'" label="后端地址">
           <el-input
             v-model="currentDeviceConfig.server_url"
             placeholder="例如 http://192.168.1.5:8000"
             style="width: 280px;"
           />
        </el-form-item>
        <el-form-item label="Token">
           <div class="device-token-row">
             <el-input
               :model-value="deviceTokenDisplayValue"
               class="device-token-input"
               :readonly="!isDeviceTokenVisible"
               :disabled="tokenRevealLoading"
               placeholder="点击右侧眼睛读取明文"
               @update:model-value="handleDeviceTokenInput"
             />
             <el-button
               circle
               :icon="isDeviceTokenVisible ? Hide : View"
               :loading="tokenRevealLoading"
               :title="isDeviceTokenVisible ? '隐藏 Token' : '读取并显示 Token'"
               :aria-label="isDeviceTokenVisible ? '隐藏 Token' : '读取并显示 Token'"
               @click="toggleDeviceTokenVisibility"
             />
           </div>
        </el-form-item>
      </el-form>
    </div>

    <el-alert
      v-if="runtimeLoadIssue"
      :title="runtimeLoadIssue"
      type="warning"
      :closable="false"
      show-icon
      class="runtime-load-alert"
    />

    <section ref="systemMonitorAnchorRef" class="runtime-section">
      <RuntimeSystemMetricsChart v-if="currentDeviceId && systemMonitorActivated" :entry-id="currentDeviceId" />
      <div v-else-if="currentDeviceId" class="system-monitor-placeholder">
        <div class="runtime-section-title">
          <span>资源监控</span>
        </div>
        <div class="system-monitor-placeholder-body">滚动到此区域后加载</div>
      </div>
    </section>

    <section class="runtime-section">
      <div class="runtime-section-title">
        <span>服务</span>
        <el-button
          :icon="Plus"
          circle
          size="small"
          text
          title="新建服务"
          aria-label="新建服务"
          @click="openCreateDialog('service')"
        />
      </div>
      <el-table
        ref="serviceTableRef"
        :data="serviceItems"
        v-loading="loading"
        table-layout="auto"
        :fit="false"
        class="runtime-table"
        row-key="id"
        :row-class-name="getRuntimeRowClassName"
        @row-contextmenu="handleRuntimeRowContextMenu"
      >
        <el-table-column width="60" align="center" label="序号">
          <template #default="scope">
            <SortableOrderHandle
              v-if="scope.row.source === 'command'"
              :index="scope.$index"
              :total="serviceItems.length"
              :pad="false"
              size="sm"
            />
            <span v-else class="runtime-order-badge">{{ scope.$index + 1 }}</span>
          </template>
        </el-table-column>
        <el-table-column label="名称" :width="runtimeNameColumnWidth">
          <template #default="{ row }">
            <div class="task-name">{{ row.title }}</div>
          </template>
        </el-table-column>

        <el-table-column label="执行" :min-width="runtimeCommandColumnMinWidth">
          <template #default="{ row }">
            <div v-if="row.source === 'command'" class="command-cell" :title="row.command">{{ row.command }}</div>
            <div v-else class="muted command-cell" :title="[row.schedule_label, row.description].filter(Boolean).join(' · ')">
              {{ [row.schedule_label, row.description].filter(Boolean).join(' · ') || row.title }}
            </div>
          </template>
        </el-table-column>

        <el-table-column label="状态" width="84" align="center">
          <template #default="{ row }">
            <div class="status-action">
              <el-button
                circle
                :type="row.actionLoading ? 'warning' : (row.active || row.status?.running ? 'success' : 'info')"
                :loading="row.actionLoading"
                @click="handleServiceStatusClick(row)"
                :title="getServiceStatusButtonTitle(row)"
              >
                <template #icon>
                  <component :is="row.active || row.status?.running ? VideoPause : VideoPlay" v-if="!row.actionLoading" />
                </template>
              </el-button>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="下次触发" :width="runtimeNextColumnWidth" align="right">
          <template #default="{ row }">
            <div class="runtime-next-cell">
              <span
                class="runtime-next-trigger"
                :class="{ running: row.active || row.status?.running }"
                :title="getRuntimeNextTriggerTitle(row)"
              >
                {{ formatRuntimeNextTrigger(row) }}
              </span>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <section class="runtime-section">
      <div class="runtime-section-title">
        <span>作业</span>
        <el-button
          :icon="Plus"
          circle
          size="small"
          text
          title="新建作业"
          aria-label="新建作业"
          @click="openJobCatalogDialog"
        />
      </div>
      <el-table
        :data="sortedJobItems"
        v-loading="loading"
        table-layout="auto"
        :fit="false"
        class="runtime-table"
        row-key="id"
        @row-contextmenu="handleRuntimeRowContextMenu"
      >
        <el-table-column width="60" align="center" label="序号">
          <template #default="scope">
            <span class="runtime-order-badge">{{ scope.$index + 1 }}</span>
          </template>
        </el-table-column>
        <el-table-column label="名称" :width="runtimeNameColumnWidth">
          <template #default="{ row }">
            <div class="task-name">{{ row.title }}</div>
          </template>
        </el-table-column>

        <el-table-column label="执行" :min-width="runtimeCommandColumnMinWidth">
          <template #default="{ row }">
            <div v-if="row.source === 'command'" class="command-cell" :title="row.command">{{ row.command }}</div>
            <div v-else class="muted command-cell" :title="row.description || row.title">
              {{ row.description || row.title }}
            </div>
          </template>
        </el-table-column>

        <el-table-column label="状态" width="84" align="center">
          <template #default="{ row }">
            <div class="status-action">
              <el-button
                circle
                :type="row.actionLoading ? 'warning' : jobRuntimeStatusType(row)"
                :loading="row.actionLoading"
                :disabled="isJobStatusButtonDisabled(row)"
                :title="getJobStatusButtonTitle(row)"
                @click.stop="handleJobStatusClick(row)"
              >
                <template #icon>
                  <component :is="isRuntimeItemRunning(row) ? VideoPause : VideoPlay" v-if="!row.actionLoading" />
                </template>
              </el-button>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="下次触发" :width="runtimeNextColumnWidth" align="right">
          <template #default="{ row }">
            <div class="runtime-next-cell">
              <span
                class="runtime-next-trigger"
                :class="{
                  running: row.active || row.status?.running,
                  disabled: row.source === 'builtin' && !row.enabled
                }"
                :title="getRuntimeNextTriggerTitle(row)"
              >
                {{ formatRuntimeNextTrigger(row) }}
              </span>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <div
      v-if="runtimeContextMenu.visible"
      class="runtime-context-menu"
      :style="{ left: `${runtimeContextMenu.x}px`, top: `${runtimeContextMenu.y}px` }"
      @click.stop
      @contextmenu.prevent
    >
      <button
        v-if="runtimeContextMenu.target?.kind === 'job' && runtimeContextMenu.target?.source === 'command' && isRuntimeItemRunning(runtimeContextMenu.target)"
        type="button"
        class="context-menu-item"
        @click="handleRuntimeContextExecute"
      >
        <el-icon>
          <VideoPause />
        </el-icon>
        <span>停止</span>
      </button>
      <button
        v-if="runtimeContextMenu.target?.source === 'command' || runtimeContextMenu.target?.source === 'builtin'"
        type="button"
        class="context-menu-item"
        @click="handleRuntimeContextConfigure"
      >
        <el-icon><Setting /></el-icon>
        <span>配置</span>
      </button>
      <button type="button" class="context-menu-item" @click="handleRuntimeContextLogs">
        <el-icon><Document /></el-icon>
        <span>日志</span>
      </button>
      <button
        v-if="canDeleteRuntimeItem(runtimeContextMenu.target)"
        type="button"
        class="context-menu-item danger"
        @click="handleRuntimeContextDelete"
      >
        <el-icon><Delete /></el-icon>
        <span>删除</span>
      </button>
    </div>

    <div v-if="queueSnapshot?.running || (queueSnapshot?.pending || []).length || (queueSnapshot?.recent || []).length" class="runtime-records">
      <div class="section-title">队列记录</div>
      <div v-if="queueSnapshot?.running" class="record-row">
        <span>运行中</span>
        <strong>{{ runtimeQueueRecordName(queueSnapshot.running) }}</strong>
      </div>
      <div v-for="item in queueSnapshot?.pending || []" :key="`pending-${item.id}`" class="record-row">
        <span>等待</span>
        <strong>{{ runtimeQueueRecordName(item) }}</strong>
      </div>
      <div v-for="item in queueSnapshot?.recent || []" :key="`recent-${item.id}`" class="record-row">
        <span :title="getQueueRecordTimeTitle(item.finished_at)">{{ getQueueRecordFinishedLabel(item) }}</span>
        <strong>{{ runtimeQueueRecordName(item) }}</strong>
      </div>
      <div v-if="!queueSnapshot?.running && !(queueSnapshot?.pending || []).length && !(queueSnapshot?.recent || []).length" class="empty-records">
        暂无作业队列记录
      </div>
    </div>

    <!-- Create Runtime Command Dialog -->
    <el-dialog v-model="jobCatalogDialogVisible" title="添加作业" width="640px">
      <div v-loading="jobCatalogLoading" class="job-catalog">
        <div class="job-catalog-list">
          <div
            v-for="item in jobCatalogItems"
            :key="item.key"
            class="job-catalog-row"
          >
            <div class="job-catalog-main">
              <div class="job-catalog-title">
                <strong>{{ item.title }}</strong>
                <span>{{ item.category }}</span>
              </div>
              <p :title="item.description">{{ item.description }}</p>
              <small>{{ item.schedule_label || '手动触发' }}</small>
            </div>
            <el-button
              size="small"
              :disabled="item.added"
              :loading="addJobTypeLoadingKey === item.key"
              @click="handleAddBuiltinJobType(item)"
            >
              {{ item.added ? '已添加' : '添加' }}
            </el-button>
          </div>
        </div>
        <el-empty v-if="!jobCatalogLoading && !jobCatalogItems.length" description="暂无可添加作业类型" />
      </div>
      <template #footer>
        <span class="dialog-footer">
          <el-button @click="jobCatalogDialogVisible = false">关闭</el-button>
          <el-button @click="openCustomJobDialog">自定义命令</el-button>
        </span>
      </template>
    </el-dialog>

    <el-dialog v-model="dialogVisible" :title="`${isEditingTask ? '配置' : '新建'}${runtimeKindLabel(form.runtime_kind)}`" width="560px">
      <el-form :model="form" label-width="80px">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="名称" />
        </el-form-item>
        <el-form-item label="设备">
          <el-input v-model="form.device_id" disabled />
          <div class="form-tip">默认创建在当前选中的 CodeYun 实例上</div>
        </el-form-item>
        <el-form-item label="命令" required>
          <el-input v-model="form.command" type="textarea" placeholder="例如: python -m module.name args" />
        </el-form-item>
        <el-form-item label="工作目录">
          <el-input v-model="form.cwd" placeholder="可选: 执行目录绝对路径" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" placeholder="可选: 任务描述" />
        </el-form-item>
        <el-form-item label="定时">
          <div class="schedule-editor">
            <el-select v-model="form.schedule_mode" class="schedule-mode-select">
              <el-option label="手动" value="manual" />
              <el-option label="一次" value="once" />
              <el-option label="间隔" value="interval" />
              <el-option label="每天" value="daily" />
              <el-option label="每周" value="weekly" />
              <el-option label="每月" value="monthly" />
              <el-option label="Cron" value="cron" />
            </el-select>
            <template v-if="form.schedule_mode === 'once'">
              <el-date-picker
                v-model="form.schedule_once_at"
                type="datetime"
                value-format="YYYY-MM-DDTHH:mm:ss"
                format="YYYY-MM-DD HH:mm"
                class="schedule-once"
              />
            </template>
            <template v-else-if="form.schedule_mode === 'interval'">
              <el-input-number v-model="form.schedule_interval_value" :min="1" :controls="false" class="schedule-number" />
              <el-select v-model="form.schedule_interval_unit" class="schedule-unit-select">
                <el-option label="分钟" value="minutes" />
                <el-option label="小时" value="hours" />
                <el-option label="天" value="days" />
              </el-select>
            </template>
            <template v-else-if="form.schedule_mode === 'daily'">
              <el-time-picker v-model="form.schedule_time" value-format="HH:mm" format="HH:mm" class="schedule-time" />
            </template>
            <template v-else-if="form.schedule_mode === 'weekly'">
              <el-select v-model="form.schedule_weekdays" multiple collapse-tags collapse-tags-tooltip class="schedule-weekdays">
                <el-option label="周一" :value="1" />
                <el-option label="周二" :value="2" />
                <el-option label="周三" :value="3" />
                <el-option label="周四" :value="4" />
                <el-option label="周五" :value="5" />
                <el-option label="周六" :value="6" />
                <el-option label="周日" :value="7" />
              </el-select>
              <el-time-picker v-model="form.schedule_time" value-format="HH:mm" format="HH:mm" class="schedule-time" />
            </template>
            <template v-else-if="form.schedule_mode === 'monthly'">
              <el-input-number v-model="form.schedule_month_day" :min="1" :max="31" :controls="false" class="schedule-number" />
              <span class="schedule-text">日</span>
              <el-time-picker v-model="form.schedule_time" value-format="HH:mm" format="HH:mm" class="schedule-time" />
            </template>
            <template v-else-if="form.schedule_mode === 'cron'">
              <el-input v-model="form.schedule_cron" placeholder="*/5 * * * *" class="schedule-cron" />
            </template>
          </div>
        </el-form-item>
        <el-form-item label="下次触发">
          <el-date-picker
            v-model="form.next_run_at"
            type="datetime"
            value-format="YYYY-MM-DDTHH:mm:ss"
            format="YYYY-MM-DD HH:mm"
            clearable
            class="schedule-once"
          />
        </el-form-item>
        <el-form-item v-if="form.schedule_mode !== 'manual'" label="触发动作">
          <div class="schedule-editor">
            <el-select v-model="form.scheduled_action" class="schedule-action-select">
              <el-option :label="form.runtime_kind === 'job' ? '默认：排队执行' : '默认：重启'" value="default" />
              <el-option v-if="form.runtime_kind === 'service'" label="启动" value="start" />
              <el-option v-if="form.runtime_kind === 'service'" label="重启" value="restart" />
              <el-option v-if="form.runtime_kind === 'service'" label="确保运行" value="ensure_running" />
              <el-option v-if="form.runtime_kind === 'service'" label="停止" value="stop" />
              <el-option v-if="form.runtime_kind === 'job'" label="排队执行" value="enqueue" />
              <el-option v-if="form.runtime_kind === 'job'" label="直接启动" value="start" />
            </el-select>
            <el-checkbox v-model="form.retry_enabled">失败重试</el-checkbox>
            <el-input-number
              v-if="form.retry_enabled"
              v-model="form.retry_minutes"
              :min="1"
              :controls="false"
              class="schedule-number"
            />
            <span v-if="form.retry_enabled" class="schedule-text">分钟后</span>
          </div>
        </el-form-item>
        <el-form-item label="超时时间">
          <el-input v-model.number="form.timeout" placeholder="单位: 秒" type="number" />
          <div class="form-tip">命令运行超过指定秒数后自动停止。留空表示不限制。</div>
        </el-form-item>
        <el-collapse accordion style="margin-top: 10px; width: 100%;">
          <el-collapse-item title="自然语言解析 (实验性)">
            <el-input v-model="nlpInput" placeholder="例如: 每5分钟, 超时1小时" class="input-with-select">
              <template #append>
                <el-button @click="parseNlp">解析</el-button>
              </template>
            </el-input>
          </el-collapse-item>
        </el-collapse>
      </el-form>
      <template #footer>
        <span class="dialog-footer">
          <el-button @click="dialogVisible = false">取消</el-button>
          <el-button type="primary" @click="handleSubmitTask">{{ isEditingTask ? '保存' : '创建' }}</el-button>
        </span>
      </template>
    </el-dialog>

    <el-dialog v-model="scheduleDialogVisible" :title="`配置定时${scheduleTarget?.title ? ` · ${scheduleTarget.title}` : ''}`" width="560px">
      <el-form label-width="80px">
        <el-form-item label="定时">
          <div class="schedule-editor">
            <el-select v-model="form.schedule_mode" class="schedule-mode-select">
              <el-option label="手动" value="manual" />
              <el-option label="一次" value="once" />
              <el-option label="间隔" value="interval" />
              <el-option label="每天" value="daily" />
              <el-option label="每周" value="weekly" />
              <el-option label="每月" value="monthly" />
              <el-option label="Cron" value="cron" />
            </el-select>
            <template v-if="form.schedule_mode === 'once'">
              <el-date-picker
                v-model="form.schedule_once_at"
                type="datetime"
                value-format="YYYY-MM-DDTHH:mm:ss"
                format="YYYY-MM-DD HH:mm"
                class="schedule-once"
              />
            </template>
            <template v-else-if="form.schedule_mode === 'interval'">
              <el-input-number v-model="form.schedule_interval_value" :min="1" :controls="false" class="schedule-number" />
              <el-select v-model="form.schedule_interval_unit" class="schedule-unit-select">
                <el-option label="分钟" value="minutes" />
                <el-option label="小时" value="hours" />
                <el-option label="天" value="days" />
              </el-select>
            </template>
            <template v-else-if="form.schedule_mode === 'daily'">
              <el-time-picker v-model="form.schedule_time" value-format="HH:mm" format="HH:mm" class="schedule-time" />
            </template>
            <template v-else-if="form.schedule_mode === 'weekly'">
              <el-select v-model="form.schedule_weekdays" multiple collapse-tags collapse-tags-tooltip class="schedule-weekdays">
                <el-option label="周一" :value="1" />
                <el-option label="周二" :value="2" />
                <el-option label="周三" :value="3" />
                <el-option label="周四" :value="4" />
                <el-option label="周五" :value="5" />
                <el-option label="周六" :value="6" />
                <el-option label="周日" :value="7" />
              </el-select>
              <el-time-picker v-model="form.schedule_time" value-format="HH:mm" format="HH:mm" class="schedule-time" />
            </template>
            <template v-else-if="form.schedule_mode === 'monthly'">
              <el-input-number v-model="form.schedule_month_day" :min="1" :max="31" :controls="false" class="schedule-number" />
              <span class="schedule-text">日</span>
              <el-time-picker v-model="form.schedule_time" value-format="HH:mm" format="HH:mm" class="schedule-time" />
            </template>
            <template v-else-if="form.schedule_mode === 'cron'">
              <el-input v-model="form.schedule_cron" placeholder="*/5 * * * *" class="schedule-cron" />
            </template>
          </div>
        </el-form-item>
        <el-form-item label="下次触发">
          <el-date-picker
            v-model="form.next_run_at"
            type="datetime"
            value-format="YYYY-MM-DDTHH:mm:ss"
            format="YYYY-MM-DD HH:mm"
            clearable
            class="schedule-once"
          />
        </el-form-item>
        <el-form-item v-if="form.schedule_mode !== 'manual'" label="失败处理">
          <div class="schedule-editor">
            <el-checkbox v-model="form.retry_enabled">重试</el-checkbox>
            <el-input-number
              v-if="form.retry_enabled"
              v-model="form.retry_minutes"
              :min="1"
              :controls="false"
              class="schedule-number"
            />
            <span v-if="form.retry_enabled" class="schedule-text">分钟后</span>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <span class="dialog-footer">
          <el-button @click="scheduleDialogVisible = false">取消</el-button>
          <el-button type="primary" @click="handleSubmitBuiltinSchedule">保存</el-button>
        </span>
      </template>
    </el-dialog>

    <!-- Device Manager Dialog -->
    <el-dialog v-model="deviceDialogVisible" title="设备管理" width="600px">
      <div style="margin-bottom: 20px;">
        <el-table :data="devices" border style="width: 100%">
          <el-table-column prop="name" label="名称" width="150" />
          <el-table-column label="模式" width="100">
            <template #default="{ row }">
              {{ row.mode === 'local' ? '本地' : '远程' }}
            </template>
          </el-table-column>
          <el-table-column prop="device_id" label="设备ID" width="260" />
          <el-table-column label="地址">
            <template #default="{ row }">
              {{ row.mode === 'local' ? '平台后端本地直达' : (row.server_url || '-') }}
            </template>
          </el-table-column>
          <el-table-column label="操作" width="120" align="center">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="handleSwitchDevice(row)">切换</el-button>
              <el-button 
                link 
                type="danger" 
                size="small" 
                @click="handleDeleteDevice(row)" 
              >移除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
      
      <el-divider>添加设备</el-divider>
      
      <el-form :model="deviceForm" label-width="80px">
        <el-form-item label="接入模式" required>
          <el-radio-group v-model="deviceForm.mode">
            <el-radio-button label="local">本地设备</el-radio-button>
            <el-radio-button label="remote">远程设备</el-radio-button>
          </el-radio-group>
          <div class="form-tip">本地设备由平台后端直接执行；远程设备由平台后端使用这条入口资产去代理访问。</div>
        </el-form-item>

        <el-form-item v-if="deviceForm.mode === 'remote'" label="后端地址" required>
            <el-input v-model="deviceForm.server_url" placeholder="例如 http://192.168.1.5:8000" style="width: 100%;" />
            <div class="form-tip">系统会用后端地址和 Token 自动读取目标设备身份；不允许填写 localhost、127.0.0.1、::1。</div>
        </el-form-item>
        
        <el-form-item label="别名">
            <el-input v-model="deviceForm.name" placeholder="可选: 给设备起个名字" />
        </el-form-item>
        
        <el-form-item label="Token" required>
          <el-input v-model="deviceForm.token" placeholder="请输入目标设备的 API Token" type="textarea" :rows="2" />
          <div class="form-tip">Token 属于用户自己的连接资产，填错会导致后续连接失败，但系统不会自动更正。</div>
        </el-form-item>
        
        <el-form-item>
          <el-button type="primary" :loading="addDeviceLoading" :icon="Connection" @click="handleAddDevice" style="width: 100%;">添加设备</el-button>
        </el-form-item>
      </el-form>
    </el-dialog>

    <!-- Token Update Dialog -->
    <el-dialog v-model="tokenDialogVisible" title="更新访问令牌" width="400px">
        <p style="margin-bottom: 15px; color: #E6A23C; font-size: 13px;">
            无法连接到设备。可能是 Token 已过期或网络不可达。请尝试更新 Token。
        </p>
        <el-form :model="tokenForm">
            <el-form-item label="Token">
                <el-input v-model="tokenForm.token" placeholder="输入新的 API Token" type="textarea" :rows="3" />
            </el-form-item>
        </el-form>
        <template #footer>
            <span class="dialog-footer">
                <el-button @click="tokenDialogVisible = false">取消</el-button>
                <el-button type="primary" @click="handleUpdateToken">更新并重连</el-button>
            </span>
        </template>
    </el-dialog>
  </DocPage>
</template>

<style scoped>
.muted {
  color: #909399;
  font-size: 12px;
}

.runtime-section {
  margin-top: 16px;
}

.runtime-load-alert {
  margin-bottom: 12px;
}

.runtime-section-title {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 8px;
  color: #303133;
  font-weight: 600;
}

.runtime-table {
  width: max-content;
  max-width: 100%;
}

.runtime-order-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 22px;
  height: 22px;
  padding: 0 5px;
  border-radius: 7px;
  background: rgba(226, 232, 240, 0.92);
  color: #475569;
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
  font-variant-numeric: tabular-nums;
}

.command-cell {
  max-width: 720px;
  display: -webkit-box;
  overflow: hidden;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  line-height: 1.35;
  font-size: 12px;
  overflow-wrap: anywhere;
  word-break: break-all;
}

.status-action,
.job-state {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.runtime-next-cell {
  display: flex;
  align-items: center;
  justify-content: flex-end;
}

.runtime-next-trigger {
  color: #606266;
  font-size: 13px;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

.runtime-next-trigger.running {
  color: #67c23a;
  font-weight: 600;
}

.runtime-next-trigger.disabled {
  color: #a8abb2;
}

.runtime-records {
  border-top: 1px solid #ebeef5;
}

.section-title {
  padding: 12px 0;
  font-weight: 600;
}

.record-row {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 9px 0;
  border-top: 1px solid #ebeef5;
}

.record-row span {
  width: 82px;
  color: #909399;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.record-row strong {
  color: #1f2937;
  font-size: 13px;
  font-weight: 600;
  line-height: 1.35;
}

.empty-records {
  padding: 18px 0;
  color: #909399;
}

.runtime-context-menu {
  position: fixed;
  z-index: 3000;
  min-width: 118px;
  padding: 5px;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  background: #fff;
  box-shadow: 0 6px 18px rgb(0 0 0 / 12%);
}

.context-menu-item {
  width: 100%;
  border: 0;
  background: transparent;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 9px;
  border-radius: 3px;
  color: #303133;
  cursor: pointer;
  font-size: 13px;
}

.context-menu-item:hover {
  background: #f5f7fa;
}

.context-menu-item.danger {
  color: #f56c6c;
}

.job-catalog {
  min-height: 180px;
}

.job-catalog-list {
  display: flex;
  flex-direction: column;
  gap: 0;
}

.job-catalog-row {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 10px 0;
  border-bottom: 1px solid #ebeef5;
}

.job-catalog-row:first-child {
  padding-top: 0;
}

.job-catalog-main {
  flex: 1;
  min-width: 0;
}

.job-catalog-title {
  display: flex;
  align-items: center;
  gap: 8px;
  line-height: 1.3;
}

.job-catalog-title strong {
  color: #1f2937;
  font-size: 14px;
}

.job-catalog-title span {
  color: #909399;
  font-size: 12px;
}

.job-catalog-main p {
  margin: 4px 0 2px;
  color: #606266;
  font-size: 12px;
  line-height: 1.35;
}

.job-catalog-main small {
  color: #909399;
  font-size: 12px;
}

.device-tabs {
  margin-bottom: 15px;
}

.device-tab-label {
  display: flex;
  flex-direction: column;
  line-height: 1.1;
}

.device-tab-name {
  font-weight: 500;
}

.device-tab-meta {
  color: #909399;
  font-size: 11px;
  margin-top: 3px;
}

.device-toolbar {
  margin-bottom: 12px;
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 10px;
}

.device-edit-panel {
  margin-bottom: 20px;
  padding: 15px;
  border: 1px solid #e4e7ed;
  border-radius: 4px;
  background-color: #fff;
}

.device-token-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 340px;
}

.device-token-input {
  flex: 1;
  min-width: 0;
}

.task-name {
  font-size: 13px;
  font-weight: 600;
  line-height: 1.28;
  display: inline-flex;
  align-items: center;
  max-width: 108px;
  overflow-wrap: anywhere;
}

.form-tip {
  font-size: 12px;
  color: #909399;
  line-height: 1.2;
  margin-top: 4px;
}

.schedule-editor {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.schedule-mode-select,
.schedule-unit-select {
  width: 92px;
}

.schedule-number {
  width: 82px;
}

.schedule-time {
  width: 116px;
}

.schedule-once {
  width: 190px;
}

.schedule-weekdays {
  width: 170px;
}

.schedule-cron {
  width: 220px;
}

.schedule-action-select {
  width: 140px;
}

.schedule-text {
  color: #606266;
  font-size: 13px;
}

.add-device-btn {
  color: #303133 !important;
}
.add-device-btn:hover {
  color: #409EFF !important;
}

.system-monitor-placeholder-body {
  min-height: 190px;
  border-top: 1px solid #ebeef5;
  border-bottom: 1px solid #ebeef5;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #909399;
  font-size: 13px;
  background: #fff;
}

@media (max-width: 960px) {
  .device-toolbar {
    flex-wrap: wrap;
  }

  .task-name {
    max-width: 80px;
  }

  .command-cell {
    max-width: 300px;
  }
}
</style>
