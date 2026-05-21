<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { FolderOpened, Refresh, WarningFilled } from '@element-plus/icons-vue';
import {
  getFanxiuBehaviorTreeService,
  getFanxiuProcesses,
  getFanxiuSunloginRotateStatus,
  getFanxiuStatus,
  getLocalScriptProcesses,
  parseFanxiuStatus,
  saveFanxiuStatus,
  startFanxiuBehaviorTreeService,
  startFanxiuSunloginRotate,
  stopFanxiuBehaviorTreeService,
  stopFanxiuSunloginRotate,
  updateFanxiuStatusConfig,
  type FanxiuAccountStatusItem,
  type FanxiuBehaviorTreeServiceStatus,
  type FanxiuProcessItem,
  type FanxiuStatusSnapshot,
  type FanxiuSunloginRotateStatus,
  type LocalScriptProcessItem,
} from '@/api/fanxiu';
import { fetchDeviceDirectoryItems, fetchDeviceFileText, saveDeviceFileText, type DeviceDirectoryItem } from '@/api/deviceFiles';
import { taskStore } from '@/store/taskStore';
import { useUserStore } from '@/store/userStore';

type FanxiuRawStatus = Record<string, unknown>;
type SourceMode = 'local' | 'device';

const DEVICE_ROOT_SENTINEL = '__device_root__';
const DEVICE_ROOT_LABEL = '设备根目录';

const userStore = useUserStore();
const sourceMode = ref<SourceMode>('local');
const loading = ref(false);
const saving = ref(false);
const savingStatusFile = ref(false);
const isLoadingProcesses = ref(false);
const isLoadingScriptProcesses = ref(false);
const isTerminatingProcesses = ref(false);
const isLoadingBehaviorTreeService = ref(false);
const isTogglingBehaviorTreeService = ref(false);
const isLoadingSunloginRotate = ref(false);
const isTogglingSunloginRotate = ref(false);
const isLoadingDevices = ref(false);
const isLoadingDeviceDirectory = ref(false);
const isLoadingDeviceFile = ref(false);
const snapshot = ref<FanxiuStatusSnapshot | null>(null);
const fanxiuProcesses = ref<FanxiuProcessItem[]>([]);
const localScriptProcesses = ref<LocalScriptProcessItem[]>([]);
const behaviorTreeService = ref<FanxiuBehaviorTreeServiceStatus | null>(null);
const sunloginRotateStatus = ref<FanxiuSunloginRotateStatus | null>(null);
const loadError = ref('');
const statusPathInput = ref('');
const localPathBaseline = ref('');
const selectedEntryId = ref('');
const devicePath = ref<string>(DEVICE_ROOT_SENTINEL);
const devicePathInput = ref(DEVICE_ROOT_LABEL);
const deviceListingItems = ref<DeviceDirectoryItem[]>([]);
const selectedDeviceFilePath = ref('');
const sourceRawStatus = ref<FanxiuRawStatus | null>(null);
const draftRawStatus = ref<FanxiuRawStatus | null>(null);

let refreshTimer: number | undefined;
let suppressSourceModeWatch = false;
let suppressEntryWatch = false;

const isAbsolutePath = (value: string) => /^(?:[a-zA-Z]:[\\/]|\\\\|\/|~(?:[\\/]|$))/.test((value || '').trim());
const isDeviceRootPath = (value: string) => (value || '').trim() === DEVICE_ROOT_SENTINEL;
const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
const cloneJson = <T>(value: T): T => JSON.parse(JSON.stringify(value));
const sanitizeRawStatus = (rawStatus: FanxiuRawStatus | null): FanxiuRawStatus | null => {
  if (!rawStatus) return null;
  const sanitized = cloneJson(rawStatus);
  delete sanitized['需要回到世界'];
  return sanitized;
};
const serializeJson = (value: unknown) => JSON.stringify(value ?? null);
const formatDevicePathInput = (value: string) => (isDeviceRootPath(value) ? DEVICE_ROOT_LABEL : value);
const normalizeDevicePathInput = (value: string) => {
  const trimmed = (value || '').trim();
  if (!trimmed || trimmed === DEVICE_ROOT_LABEL || trimmed === DEVICE_ROOT_SENTINEL) {
    return DEVICE_ROOT_SENTINEL;
  }
  return isAbsolutePath(trimmed) ? trimmed : '';
};
const getAbsoluteParentPath = (value: string) => {
  const trimmed = (value || '').trim();
  if (!isAbsolutePath(trimmed)) return DEVICE_ROOT_SENTINEL;
  const normalized = trimmed.replace(/[\\/]+$/, '');
  if (/^[a-zA-Z]:$/.test(normalized) || normalized === '/' || normalized === '\\\\') {
    return DEVICE_ROOT_SENTINEL;
  }
  const parent = normalized.replace(/[\\/][^\\/]+$/, '');
  return !parent || parent === normalized ? DEVICE_ROOT_SENTINEL : parent;
};
const formatTaskDelta = (seconds: number | null | undefined) => {
  if (seconds === null || seconds === undefined) return '--';
  const abs = Math.abs(seconds);
  let body = '1 分钟内';
  if (abs >= 86400) body = `${Math.floor(abs / 86400)} 天`;
  else if (abs >= 3600) body = `${Math.floor(abs / 3600)} 小时`;
  else if (abs >= 60) body = `${Math.floor(abs / 60)} 分钟`;
  return seconds <= 0 ? (abs < 60 ? '已到期' : `已超时 ${body}`) : `${body}后`;
};
const taskTagType = (due: boolean, isNext: boolean) => (due ? 'danger' : isNext ? 'warning' : 'info');
const formatProcessRuntime = (seconds: number | null | undefined) => {
  if (seconds === null || seconds === undefined) return '--';
  if (seconds >= 86400) return `${Math.floor(seconds / 86400)} 天`;
  if (seconds >= 3600) return `${Math.floor(seconds / 3600)} 小时`;
  if (seconds >= 60) return `${Math.floor(seconds / 60)} 分钟`;
  return `${seconds} 秒`;
};

const canConfigure = computed(() => {
  const username = userStore.user?.username;
  return username === '凡修手游' || userStore.isAdmin;
});
const devices = computed(() => taskStore.devices);
const selectedDevice = computed(() => devices.value.find((device) => device.id === selectedEntryId.value) ?? null);
const canUseDeviceSource = computed(() => userStore.isAuthenticated);
const directoryEntries = computed(() =>
  deviceListingItems.value.filter((item) => item.is_dir).slice().sort((a, b) => a.name.localeCompare(b.name, 'zh-CN')),
);
const jsonFileEntries = computed(() =>
  deviceListingItems.value
    .filter((item) => !item.is_dir && /\.json$/i.test(item.name))
    .slice()
    .sort((a, b) => {
      const aPriority = a.name.toLowerCase() === 'status.json' ? 0 : 1;
      const bPriority = b.name.toLowerCase() === 'status.json' ? 0 : 1;
      return aPriority !== bPriority ? aPriority - bPriority : a.name.localeCompare(b.name, 'zh-CN');
    }),
);
const accountCards = computed<FanxiuAccountStatusItem[]>(() => snapshot.value?.accounts ?? []);
const fanxiuProcessCount = computed(() => fanxiuProcesses.value.length);
const localScriptProcessCount = computed(() => localScriptProcesses.value.length);
const behaviorTreeRunning = computed(() => Boolean(behaviorTreeService.value?.running));
const behaviorTreeStateText = computed(() => behaviorTreeService.value?.state_label || '未知');
const behaviorTreeButtonText = computed(() => behaviorTreeRunning.value ? '重启凡修服务' : '启动凡修服务');
const sunloginRotateRunning = computed(() => Boolean(sunloginRotateStatus.value?.running));
const sunloginRotateButtonText = computed(() => sunloginRotateRunning.value ? '关闭投屏旋转' : '开启投屏旋转');
const pageStatusType = computed(() => {
  if (loadError.value || snapshot.value?.error) return 'danger';
  if (sourceMode.value === 'device' && !selectedDeviceFilePath.value) return 'warning';
  if (!snapshot.value?.effective_path) return 'warning';
  return 'success';
});
const pageStatusText = computed(() => {
  if (loadError.value) return '接口异常';
  if (snapshot.value?.error) return '状态异常';
  if (sourceMode.value === 'device' && !selectedDeviceFilePath.value) return '待选文件';
  if (!snapshot.value?.effective_path) return '未配置';
  return '已加载';
});
const pathModeText = computed(() => snapshot.value?.mode === 'configured' ? '已配置' : snapshot.value?.mode === 'auto' ? '自动探测' : '未配置');
const pathModeTagType = computed(() => snapshot.value?.mode === 'configured' ? 'success' : snapshot.value?.mode === 'auto' ? 'warning' : 'info');
const summaryTags = computed(() => {
  if (!snapshot.value) return [];
  const tags: Array<{ label: string; type: 'success' | 'warning' | 'info' | 'danger' }> = [];
  if (snapshot.value.current_account) tags.push({ label: `当前账号 ${snapshot.value.current_account}`, type: 'info' });
  if (snapshot.value.recommended_account) tags.push({ label: `建议执行 ${snapshot.value.recommended_account}`, type: 'warning' });
  if (snapshot.value.program_initialized) tags.push({ label: '程序初始化', type: 'success' });
  if (snapshot.value.all_tasks_completed) tags.push({ label: '已执行完所有任务', type: 'success' });
  return tags;
});
const isLocalPathDirty = computed(() => statusPathInput.value.trim() !== localPathBaseline.value.trim());
const canBrowseDevice = computed(() => Boolean(selectedEntryId.value && (isDeviceRootPath(devicePath.value) || isAbsolutePath(devicePath.value))));
const canGoUpDevice = computed(() => Boolean(selectedEntryId.value) && !isDeviceRootPath(devicePath.value) && getAbsoluteParentPath(devicePath.value) !== devicePath.value);
const emptyDescription = computed(() => sourceMode.value === 'device' ? '选择设备里的 status.json 后显示任务' : '当前没有账号任务');
const hasDraftChanges = computed(() => serializeJson(draftRawStatus.value) !== serializeJson(sourceRawStatus.value));
const canEditTasks = computed(() => {
  if (!draftRawStatus.value) return false;
  if (sourceMode.value === 'local') {
    return canConfigure.value && Boolean(snapshot.value?.effective_path);
  }
  return canUseDeviceSource.value && Boolean(selectedEntryId.value && selectedDeviceFilePath.value);
});

const syncLocalPathInput = () => { statusPathInput.value = localPathBaseline.value.trim(); };
const syncDevicePathInput = () => { devicePathInput.value = formatDevicePathInput(devicePath.value); };
const resetDraftState = (rawStatus: FanxiuRawStatus | null) => {
  const sanitized = sanitizeRawStatus(rawStatus);
  sourceRawStatus.value = sanitized ? cloneJson(sanitized) : null;
  draftRawStatus.value = sanitized ? cloneJson(sanitized) : null;
};
const blockIfDraftDirty = (message = '当前有未保存修改，请先保存或撤销。') => {
  if (!hasDraftChanges.value) return false;
  ElMessage.warning(message);
  return true;
};
const buildDraftSnapshot = (derived: FanxiuStatusSnapshot): FanxiuStatusSnapshot => {
  if (sourceMode.value === 'device') {
    return {
      ...derived,
      status_path: selectedDeviceFilePath.value || null,
      auto_detected_path: null,
      effective_path: selectedDeviceFilePath.value || null,
      mode: 'configured',
      file_exists: Boolean(selectedDeviceFilePath.value),
      error: null,
      raw_status: draftRawStatus.value ? cloneJson(draftRawStatus.value) : null,
    };
  }
  return {
    ...derived,
    status_path: snapshot.value?.status_path ?? null,
    auto_detected_path: snapshot.value?.auto_detected_path ?? null,
    effective_path: snapshot.value?.effective_path ?? null,
    mode: snapshot.value?.mode ?? 'unset',
    file_exists: snapshot.value?.file_exists ?? false,
    error: null,
    raw_status: draftRawStatus.value ? cloneJson(draftRawStatus.value) : null,
  };
};
const refreshDraftPreview = async () => {
  if (!draftRawStatus.value) return;
  try {
    const sanitized = sanitizeRawStatus(draftRawStatus.value);
    if (!sanitized) return;
    const derived = await parseFanxiuStatus(sanitized);
    snapshot.value = buildDraftSnapshot(derived);
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '更新任务预览失败');
  }
};
const ensureDraftAccount = (accountName: string) => {
  if (!draftRawStatus.value) return null;
  const raw = draftRawStatus.value as Record<string, unknown>;
  const current = raw[accountName];
  if (isRecord(current)) {
    return current;
  }
  const created: Record<string, unknown> = {};
  raw[accountName] = created;
  return created;
};
const getDraftTaskTime = (accountName: string, taskName: string, fallback: string) => {
  const account = draftRawStatus.value?.[accountName];
  if (isRecord(account) && typeof account[taskName] === 'string' && account[taskName].trim()) {
    return account[taskName] as string;
  }
  return fallback;
};
const setDraftTaskTime = (accountName: string, taskName: string, value: string | null | undefined) => {
  const nextValue = String(value || '').trim();
  if (!nextValue) return false;
  const account = ensureDraftAccount(accountName);
  if (!account) return false;
  account[taskName] = nextValue;
  return true;
};

const ensureDevicesLoaded = async () => {
  if (!canUseDeviceSource.value || isLoadingDevices.value) return;
  isLoadingDevices.value = true;
  try {
    await taskStore.fetchDevices();
    if (!selectedEntryId.value && taskStore.devices.length) {
      selectedEntryId.value = taskStore.devices[0].id;
    }
  } finally {
    isLoadingDevices.value = false;
  }
};

const loadLocalSnapshot = async (preserveInput = false) => {
  loading.value = true;
  loadError.value = '';
  try {
    const data = await getFanxiuStatus();
    snapshot.value = data;
    localPathBaseline.value = data.status_path ?? data.effective_path ?? '';
    resetDraftState(isRecord(data.raw_status) ? data.raw_status as FanxiuRawStatus : null);
    if (!preserveInput || !isLocalPathDirty.value) syncLocalPathInput();
  } catch (error: any) {
    resetDraftState(null);
    loadError.value = error?.response?.data?.detail || error?.message || '读取任务状态失败';
  } finally {
    loading.value = false;
  }
};

const loadDeviceDirectory = async () => {
  if (!canBrowseDevice.value) return;
  isLoadingDeviceDirectory.value = true;
  loadError.value = '';
  try {
    const listing = await fetchDeviceDirectoryItems(selectedEntryId.value, { absolute_path: devicePath.value });
    deviceListingItems.value = listing.items ?? [];
    if (!isDeviceRootPath(devicePath.value)) {
      devicePath.value = listing.absolute_path || devicePath.value;
    }
    syncDevicePathInput();
  } catch (error: any) {
    loadError.value = error?.response?.data?.detail || error?.message || '读取设备目录失败';
  } finally {
    isLoadingDeviceDirectory.value = false;
  }
};

const loadDeviceSnapshot = async (absolutePath: string) => {
  if (!selectedEntryId.value) return;
  if (absolutePath !== selectedDeviceFilePath.value && blockIfDraftDirty('请先保存或撤销当前修改，再切换文件。')) {
    return;
  }
  loading.value = true;
  isLoadingDeviceFile.value = true;
  loadError.value = '';
  try {
    const textResult = await fetchDeviceFileText(selectedEntryId.value, { absolute_path: absolutePath, encoding: 'utf-8' });
    const raw = JSON.parse(textResult.text);
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
      throw new Error('status.json 根节点不是对象');
    }
    const sanitizedRaw = sanitizeRawStatus(raw as FanxiuRawStatus);
    if (!sanitizedRaw) {
      throw new Error('status.json 根节点不是对象');
    }
    const derived = await parseFanxiuStatus(sanitizedRaw);
    const nextSnapshot: FanxiuStatusSnapshot = {
      ...derived,
      status_path: absolutePath,
      auto_detected_path: null,
      effective_path: absolutePath,
      mode: 'configured',
      file_exists: true,
      error: null,
      raw_status: cloneJson(sanitizedRaw),
    };
    snapshot.value = nextSnapshot;
    resetDraftState(sanitizedRaw);
    selectedDeviceFilePath.value = absolutePath;
  } catch (error: any) {
    resetDraftState(null);
    loadError.value = error?.response?.data?.detail || error?.message || '读取设备 status.json 失败';
  } finally {
    loading.value = false;
    isLoadingDeviceFile.value = false;
  }
};

const refreshNow = async () => {
  if (blockIfDraftDirty()) return;
  if (sourceMode.value === 'device') {
    if (selectedDeviceFilePath.value) await loadDeviceSnapshot(selectedDeviceFilePath.value);
    else await loadDeviceDirectory();
  } else {
    await loadLocalSnapshot(true);
  }
  void loadFanxiuProcesses();
  void loadLocalScriptProcesses();
  void loadBehaviorTreeService();
  void loadSunloginRotateStatus();
};

const loadLocalScriptProcesses = async () => {
  if (!canConfigure.value || isLoadingScriptProcesses.value) return;
  isLoadingScriptProcesses.value = true;
  try {
    const data = await getLocalScriptProcesses();
    localScriptProcesses.value = data.items ?? [];
  } catch {
    localScriptProcesses.value = [];
  } finally {
    isLoadingScriptProcesses.value = false;
  }
};

const loadFanxiuProcesses = async () => {
  if (!canConfigure.value || isLoadingProcesses.value) return;
  isLoadingProcesses.value = true;
  try {
    const data = await getFanxiuProcesses();
    fanxiuProcesses.value = data.items ?? [];
  } catch {
    fanxiuProcesses.value = [];
  } finally {
    isLoadingProcesses.value = false;
  }
};

const loadBehaviorTreeService = async () => {
  if (!canConfigure.value || isLoadingBehaviorTreeService.value) return;
  isLoadingBehaviorTreeService.value = true;
  try {
    behaviorTreeService.value = await getFanxiuBehaviorTreeService();
  } catch {
    behaviorTreeService.value = null;
  } finally {
    isLoadingBehaviorTreeService.value = false;
  }
};

const startOrRestartBehaviorTreeService = async () => {
  if (!canConfigure.value || isTogglingBehaviorTreeService.value) return;
  const running = behaviorTreeRunning.value;
  if (running) {
    try {
      await ElMessageBox.confirm(
        '会先终止当前凡修行为树，再以 CodeYun 服务入口重新启动。',
        '重启凡修服务',
        {
          type: 'warning',
          confirmButtonText: '重启',
          cancelButtonText: '取消',
          distinguishCancelAndClose: true,
        },
      );
    } catch {
      return;
    }
  }

  isTogglingBehaviorTreeService.value = true;
  try {
    const result = await startFanxiuBehaviorTreeService();
    behaviorTreeService.value = result.service;
    await loadFanxiuProcesses();
    await loadLocalScriptProcesses();
    ElMessage.success(running ? '已重启凡修服务' : '已启动凡修服务');
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '启动凡修服务失败');
    await loadBehaviorTreeService();
  } finally {
    isTogglingBehaviorTreeService.value = false;
  }
};

const stopBehaviorTreeService = async () => {
  if (!canConfigure.value || isTerminatingProcesses.value) return;
  try {
    await ElMessageBox.confirm(
      '会按凡修服务登记和进程扫描结果终止当前行为树，并标记登记文件为已停止。',
      '停止凡修服务',
      {
        type: 'warning',
        confirmButtonText: '停止',
        cancelButtonText: '取消',
        distinguishCancelAndClose: true,
      },
    );
  } catch {
    return;
  }

  isTerminatingProcesses.value = true;
  try {
    const result = await stopFanxiuBehaviorTreeService();
    behaviorTreeService.value = result.service;
    await loadFanxiuProcesses();
    await loadLocalScriptProcesses();
    ElMessage.success('已停止凡修服务');
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '停止凡修服务失败');
    await loadBehaviorTreeService();
    await loadFanxiuProcesses();
  } finally {
    isTerminatingProcesses.value = false;
  }
};

const loadSunloginRotateStatus = async () => {
  if (!canConfigure.value || isLoadingSunloginRotate.value) return;
  isLoadingSunloginRotate.value = true;
  try {
    sunloginRotateStatus.value = await getFanxiuSunloginRotateStatus();
  } catch {
    sunloginRotateStatus.value = null;
  } finally {
    isLoadingSunloginRotate.value = false;
  }
};

const toggleSunloginRotate = async () => {
  if (!canConfigure.value || isTogglingSunloginRotate.value) return;
  isTogglingSunloginRotate.value = true;
  try {
    const nextStatus = sunloginRotateRunning.value
      ? await stopFanxiuSunloginRotate()
      : await startFanxiuSunloginRotate();
    sunloginRotateStatus.value = nextStatus;
    ElMessage.success(nextStatus.running ? '已开启投屏旋转' : '已关闭投屏旋转');
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '切换投屏旋转失败');
    await loadSunloginRotateStatus();
  } finally {
    isTogglingSunloginRotate.value = false;
  }
};

const savePathConfig = async () => {
  if (!canConfigure.value) return;
  saving.value = true;
  try {
    await updateFanxiuStatusConfig(statusPathInput.value.trim() || null);
    await loadLocalSnapshot(false);
    ElMessage.success('任务状态路径已保存');
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '保存路径失败');
  } finally {
    saving.value = false;
  }
};

const resetToAuto = async () => {
  if (blockIfDraftDirty()) return;
  statusPathInput.value = '';
  await savePathConfig();
};

const handleDevicePathSubmit = async () => {
  if (blockIfDraftDirty('请先保存或撤销当前修改，再切换目录。')) return;
  const normalizedPath = normalizeDevicePathInput(devicePathInput.value);
  if (!normalizedPath) {
    syncDevicePathInput();
    ElMessage.warning('请输入绝对路径');
    return;
  }
  devicePath.value = normalizedPath;
  syncDevicePathInput();
  await loadDeviceDirectory();
};

const handleDevicePathBlur = () => {
  if (hasDraftChanges.value) {
    syncDevicePathInput();
    return;
  }
  const normalizedPath = normalizeDevicePathInput(devicePathInput.value);
  if (!normalizedPath) syncDevicePathInput();
  else {
    devicePath.value = normalizedPath;
    syncDevicePathInput();
  }
};

const openDeviceDirectory = async (path: string) => {
  if (blockIfDraftDirty('请先保存或撤销当前修改，再切换目录。')) return;
  devicePath.value = path;
  syncDevicePathInput();
  await loadDeviceDirectory();
};

const goUpDeviceDirectory = async () => {
  if (blockIfDraftDirty('请先保存或撤销当前修改，再切换目录。')) return;
  if (!canGoUpDevice.value) return;
  devicePath.value = getAbsoluteParentPath(devicePath.value);
  syncDevicePathInput();
  await loadDeviceDirectory();
};

const updateTaskTime = async (accountName: string, taskName: string, value: string | null | undefined) => {
  if (!setDraftTaskTime(accountName, taskName, value)) return;
  await refreshDraftPreview();
};

const updateTaskTimeDraft = (accountName: string, taskName: string, value: unknown) => {
  if (typeof value !== 'string') return;
  setDraftTaskTime(accountName, taskName, value);
};

const updateTaskTimeFromPicker = async (accountName: string, taskName: string, value: unknown) => {
  await updateTaskTime(accountName, taskName, typeof value === 'string' ? value : null);
};

const deleteTask = async (accountName: string, taskName: string) => {
  const account = ensureDraftAccount(accountName);
  if (!account) return;

  try {
    await ElMessageBox.confirm(`删除后会把 ${accountName}/${taskName} 记为禁用，不再参与调度。`, '删除任务', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      distinguishCancelAndClose: true,
    });
  } catch {
    return;
  }

  account[taskName] = null;
  await refreshDraftPreview();
};

const resetDraftChanges = async () => {
  if (!sourceRawStatus.value) return;
  draftRawStatus.value = cloneJson(sourceRawStatus.value);
  await refreshDraftPreview();
};

const saveStatusFile = async () => {
  if (!draftRawStatus.value || !canEditTasks.value) return;
  savingStatusFile.value = true;
  try {
    const sanitizedRaw = sanitizeRawStatus(draftRawStatus.value);
    if (!sanitizedRaw) return;
    if (sourceMode.value === 'device') {
      await saveDeviceFileText(selectedEntryId.value, {
        absolute_path: selectedDeviceFilePath.value,
        text: `${JSON.stringify(sanitizedRaw, null, 2)}\n`,
        encoding: 'utf-8',
      });
      await loadDeviceSnapshot(selectedDeviceFilePath.value);
    } else {
      const data = await saveFanxiuStatus(sanitizedRaw);
      snapshot.value = data;
      localPathBaseline.value = data.status_path ?? data.effective_path ?? '';
      resetDraftState(isRecord(data.raw_status) ? data.raw_status as FanxiuRawStatus : null);
      syncLocalPathInput();
    }
    ElMessage.success('status.json 已保存');
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '保存 status.json 失败');
  } finally {
    savingStatusFile.value = false;
  }
};

watch(sourceMode, async (nextMode, previousMode) => {
  if (suppressSourceModeWatch) {
    suppressSourceModeWatch = false;
    return;
  }
  if (hasDraftChanges.value) {
    suppressSourceModeWatch = true;
    sourceMode.value = previousMode;
    ElMessage.warning('请先保存或撤销当前修改，再切换来源。');
    return;
  }
  loadError.value = '';
  snapshot.value = null;
  if (nextMode === 'device') {
    if (!canUseDeviceSource.value) return;
    await ensureDevicesLoaded();
    if (selectedEntryId.value && canBrowseDevice.value) await loadDeviceDirectory();
  } else {
    await loadLocalSnapshot(false);
  }
});

watch(selectedEntryId, async (nextEntryId, previousEntryId) => {
  if (suppressEntryWatch) {
    suppressEntryWatch = false;
    return;
  }
  if (hasDraftChanges.value) {
    suppressEntryWatch = true;
    selectedEntryId.value = previousEntryId;
    ElMessage.warning('请先保存或撤销当前修改，再切换设备。');
    return;
  }
  if (sourceMode.value !== 'device') return;
  deviceListingItems.value = [];
  selectedDeviceFilePath.value = '';
  snapshot.value = null;
  resetDraftState(null);
  if (!nextEntryId) return;
  if (!isDeviceRootPath(devicePath.value) && !isAbsolutePath(devicePath.value)) {
    devicePath.value = DEVICE_ROOT_SENTINEL;
  }
  syncDevicePathInput();
  await loadDeviceDirectory();
});

onMounted(async () => {
  syncDevicePathInput();
  await loadLocalSnapshot(false);
  void loadFanxiuProcesses();
  void loadLocalScriptProcesses();
  void loadBehaviorTreeService();
  void loadSunloginRotateStatus();
  if (userStore.isAuthenticated) void ensureDevicesLoaded();
  refreshTimer = window.setInterval(() => {
    if (document.hidden) return;
    if (hasDraftChanges.value) return;
    if (sourceMode.value === 'device') {
      if (selectedDeviceFilePath.value) void loadDeviceSnapshot(selectedDeviceFilePath.value);
    } else {
      void loadLocalSnapshot(true);
    }
    void loadFanxiuProcesses();
    void loadLocalScriptProcesses();
    void loadBehaviorTreeService();
    void loadSunloginRotateStatus();
  }, 30000);
});

onUnmounted(() => {
  if (refreshTimer !== undefined) window.clearInterval(refreshTimer);
});
</script>

<template>
  <div class="task-status-page">
    <div class="page-shell">
      <div class="page-header">
        <div>
          <h2 class="page-title">任务状态</h2>
        </div>
        <div class="header-actions">
          <el-tag :type="pageStatusType" effect="dark">{{ pageStatusText }}</el-tag>
          <el-tag v-if="canConfigure" :type="behaviorTreeRunning ? 'success' : 'info'" effect="plain">
            服务 {{ behaviorTreeStateText }}
          </el-tag>
          <el-popover v-if="canConfigure" placement="bottom-end" trigger="click" :width="860">
            <template #reference>
              <el-button
                :type="fanxiuProcessCount ? 'danger' : 'success'"
                plain
                :loading="isLoadingScriptProcesses"
              >
                脚本 {{ localScriptProcessCount }}
              </el-button>
            </template>
            <div class="process-popover">
              <div class="process-popover-header">
                <div class="process-popover-title">
                  <span>本机脚本清单</span>
                  <el-tag v-if="fanxiuProcessCount" type="danger" effect="plain">凡修 {{ fanxiuProcessCount }}</el-tag>
                </div>
                <el-button text :icon="Refresh" :loading="isLoadingScriptProcesses" @click="loadLocalScriptProcesses">刷新</el-button>
              </div>
              <el-table
                v-if="localScriptProcesses.length"
                :data="localScriptProcesses"
                size="small"
                max-height="360"
                table-layout="auto"
                :fit="false"
              >
                <el-table-column label="PID" prop="pid" width="76" />
                <el-table-column label="类型" width="116">
                  <template #default="{ row }">
                    <el-tag :type="row.is_fanxiu ? 'danger' : 'info'" effect="plain">
                      {{ row.project_hint || row.kind }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="脚本" min-width="170">
                  <template #default="{ row }">
                    <span class="process-script" :title="row.script_path || row.command_line">{{ row.script }}</span>
                  </template>
                </el-table-column>
                <el-table-column label="已运行" width="92">
                  <template #default="{ row }">{{ formatProcessRuntime(row.runtime_seconds) }}</template>
                </el-table-column>
                <el-table-column label="命令" min-width="360">
                  <template #default="{ row }">
                    <span class="process-command" :title="row.command_line">{{ row.command_line }}</span>
                  </template>
                </el-table-column>
              </el-table>
              <div v-else class="empty-inline">当前没有探测到脚本进程</div>
            </div>
          </el-popover>
          <el-button
            v-if="canConfigure"
            :type="sunloginRotateRunning ? 'warning' : 'primary'"
            plain
            :loading="isTogglingSunloginRotate || isLoadingSunloginRotate"
            @click="toggleSunloginRotate"
          >
            {{ sunloginRotateButtonText }}
          </el-button>
          <el-button
            v-if="canConfigure"
            :type="behaviorTreeRunning ? 'warning' : 'primary'"
            plain
            :loading="isTogglingBehaviorTreeService || isLoadingBehaviorTreeService"
            @click="startOrRestartBehaviorTreeService"
          >
            {{ behaviorTreeButtonText }}
          </el-button>
          <el-button
            v-if="canConfigure"
            type="danger"
            plain
            :loading="isTerminatingProcesses"
            :disabled="isLoadingProcesses"
            @click="stopBehaviorTreeService"
          >
            停止凡修服务
          </el-button>
          <el-button :icon="Refresh" :loading="loading" @click="refreshNow">刷新</el-button>
        </div>
      </div>

      <div class="summary-grid">
        <el-card class="summary-card" shadow="never">
          <template #header>
            <div class="card-header">
              <span>运行标记</span>
              <el-icon><WarningFilled /></el-icon>
            </div>
          </template>
          <div class="tag-list">
            <el-tag v-for="tag in summaryTags" :key="tag.label" :type="tag.type" effect="plain">
              {{ tag.label }}
            </el-tag>
            <span v-if="!summaryTags.length" class="empty-inline">暂无特殊标记</span>
          </div>
          <div class="timer-list">
            <div v-for="item in snapshot?.runtime_timers || []" :key="item.name" class="timer-row">
              <span class="timer-name">{{ item.name }}</span>
              <span class="timer-time">{{ item.scheduled_at }}</span>
              <el-tag :type="item.due ? 'danger' : 'success'" effect="plain">
                {{ formatTaskDelta(item.seconds_until_due) }}
              </el-tag>
            </div>
            <div v-if="!(snapshot?.runtime_timers?.length)" class="empty-inline">暂无运行计时器</div>
          </div>
          <div v-if="snapshot?.watchdog_hash" class="hash-line">
            <span class="hash-label">卡死检测哈希</span>
            <code>{{ snapshot.watchdog_hash }}</code>
          </div>
        </el-card>

        <el-card class="summary-card source-card" shadow="never">
          <template #header>
            <div class="card-header">
              <span>数据来源</span>
              <el-icon><FolderOpened /></el-icon>
            </div>
          </template>

          <div class="source-mode-switch">
            <el-radio-group v-model="sourceMode" size="large">
              <el-radio-button label="local">本机路径</el-radio-button>
              <el-radio-button label="device" :disabled="!canUseDeviceSource">设备文件</el-radio-button>
            </el-radio-group>
          </div>

          <template v-if="sourceMode === 'local'">
            <div class="path-meta">
              <el-tag :type="pathModeTagType" effect="plain">{{ pathModeText }}</el-tag>
              <el-tag :type="snapshot?.file_exists ? 'success' : 'warning'" effect="plain">
                {{ snapshot?.file_exists ? '文件存在' : '文件未找到' }}
              </el-tag>
            </div>
            <div v-if="canConfigure" class="path-editor">
              <el-input v-model="statusPathInput" placeholder="填写 status.json 绝对路径" clearable />
              <div class="path-actions">
                <el-button type="primary" :loading="saving" :disabled="!isLocalPathDirty" @click="savePathConfig">保存路径</el-button>
                <el-button text :disabled="saving" @click="resetToAuto">恢复自动探测</el-button>
              </div>
            </div>
            <div v-else class="readonly-path">
              {{ snapshot?.effective_path || '未配置' }}
            </div>
            <div v-if="snapshot?.auto_detected_path && snapshot.auto_detected_path !== snapshot.effective_path" class="path-note">
              自动探测：{{ snapshot.auto_detected_path }}
            </div>
            <div v-if="snapshot?.effective_path && snapshot.status_path && snapshot.status_path !== snapshot.effective_path" class="path-note">
              当前生效：{{ snapshot.effective_path }}
            </div>
          </template>

          <template v-else>
            <div v-if="!canUseDeviceSource" class="readonly-path">登录后可使用设备文件浏览。</div>
            <div v-else-if="!devices.length && !isLoadingDevices" class="readonly-path">
              还没有可用设备，请先到“运行管理”添加设备入口。
            </div>
            <div v-else class="device-browser">
              <div class="device-config-field">
                <span class="device-config-label">设备</span>
                <el-select v-model="selectedEntryId" class="device-config-select" placeholder="选择设备" :loading="isLoadingDevices" :disabled="isLoadingDevices || !devices.length">
                  <el-option v-for="device in devices" :key="device.id" :label="device.name || device.device_id" :value="device.id" />
                </el-select>
              </div>

              <div class="directory-toolbar">
                <el-input
                  v-model="devicePathInput"
                  class="directory-path-input"
                  placeholder="输入绝对路径，例如 D:\home\chenkunze\data\m2508凡修\mainwin"
                  :disabled="!selectedEntryId"
                  @keyup.enter="handleDevicePathSubmit"
                  @blur="handleDevicePathBlur"
                />
                <el-button type="primary" :loading="isLoadingDeviceDirectory" :disabled="!selectedEntryId" @click="handleDevicePathSubmit">进入目录</el-button>
                <el-button :disabled="!canGoUpDevice || isLoadingDeviceDirectory" @click="goUpDeviceDirectory">上一级</el-button>
              </div>

              <div class="path-note">
                当前设备：{{ selectedDevice?.name || '--' }}
                <span v-if="selectedDeviceFilePath"> · 当前文件：{{ selectedDeviceFilePath }}</span>
              </div>

              <div v-if="directoryEntries.length" class="directory-strip">
                <button v-for="entry in directoryEntries" :key="entry.path" type="button" class="directory-chip" @click="openDeviceDirectory(entry.path)">
                  <span class="directory-chip-name" :title="entry.name">{{ entry.name }}</span>
                </button>
              </div>
              <div v-else class="empty-inline">当前目录下没有子目录</div>

              <div class="file-block">
                <div class="file-block-header">
                  <span>JSON 文件</span>
                  <span>{{ jsonFileEntries.length }} 项</span>
                </div>
                <div v-if="jsonFileEntries.length" class="file-strip">
                  <button
                    v-for="entry in jsonFileEntries"
                    :key="entry.path"
                    type="button"
                    class="file-chip"
                    :class="{ 'is-active': selectedDeviceFilePath === entry.path }"
                    :disabled="isLoadingDeviceFile"
                    @click="loadDeviceSnapshot(entry.path)"
                  >
                    <span class="file-chip-name" :title="entry.name">{{ entry.name }}</span>
                    <span v-if="entry.name.toLowerCase() === 'status.json'" class="file-chip-badge">推荐</span>
                  </button>
                </div>
                <div v-else class="empty-inline">当前目录下没有 JSON 文件</div>
              </div>
            </div>
          </template>

          <div v-if="canEditTasks" class="status-save-bar">
            <el-tag :type="hasDraftChanges ? 'warning' : 'success'" effect="plain">
              {{ hasDraftChanges ? '有未保存修改' : '已与文件同步' }}
            </el-tag>
            <el-button type="primary" :loading="savingStatusFile" :disabled="!hasDraftChanges" @click="saveStatusFile">
              保存 status.json
            </el-button>
            <el-button text :disabled="!hasDraftChanges || savingStatusFile" @click="resetDraftChanges">撤销修改</el-button>
          </div>
        </el-card>
      </div>

      <el-alert v-if="loadError" :title="loadError" type="error" show-icon :closable="false" class="page-alert" />
      <el-alert v-else-if="snapshot?.error" :title="snapshot.error" type="warning" show-icon :closable="false" class="page-alert" />

      <div class="account-grid">
        <el-card v-for="account in accountCards" :key="account.name" class="account-card" shadow="never">
          <template #header>
            <div class="account-header">
              <div>
                <div class="account-title-row">
                  <h3 class="account-title">{{ account.name }}</h3>
                  <el-tag v-if="account.is_current" type="info" effect="dark">当前账号</el-tag>
                  <el-tag v-if="account.has_due_task" type="danger" effect="plain">有到期任务</el-tag>
                </div>
                <div class="account-meta">
                  <span v-if="account.phone">{{ account.phone }}</span>
                  <span>共 {{ account.task_count }} 项</span>
                  <span>到期 {{ account.due_count }} 项</span>
                </div>
              </div>
            </div>
          </template>

          <el-table :data="account.tasks" size="small" class="task-table">
            <el-table-column label="状态" width="92" align="center">
              <template #default="{ row }">
                <el-tag :type="taskTagType(row.due, row.is_next)" effect="plain">
                  {{ row.due ? '到期' : row.is_next ? '下一项' : '排队中' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="name" label="任务" min-width="150" />
            <el-table-column label="计划时间" min-width="210">
              <template #default="{ row }">
                <el-date-picker
                  v-if="canEditTasks"
                  :model-value="getDraftTaskTime(account.name, row.name, row.scheduled_at)"
                  type="datetime"
                  value-format="YYYY-MM-DD HH:mm:ss"
                  format="YYYY-MM-DD HH:mm:ss"
                  class="task-time-picker"
                  :clearable="false"
                  @update:model-value="(value) => updateTaskTimeDraft(account.name, row.name, value)"
                  @change="(value) => updateTaskTimeFromPicker(account.name, row.name, value)"
                />
                <span v-else>{{ row.scheduled_at }}</span>
              </template>
            </el-table-column>
            <el-table-column label="剩余" width="128" align="center">
              <template #default="{ row }">
                <span :class="['delta-text', { overdue: row.due }]">{{ formatTaskDelta(row.seconds_until_due) }}</span>
              </template>
            </el-table-column>
            <el-table-column v-if="canEditTasks" label="操作" width="88" align="center">
              <template #default="{ row }">
                <el-button text type="danger" @click="deleteTask(account.name, row.name)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </div>

      <el-empty v-if="!accountCards.length && !loading && !loadError && !snapshot?.error" :description="emptyDescription" class="empty-state" />
    </div>
  </div>
</template>

<style scoped>
.task-status-page {
  min-height: 100%;
  padding: 24px;
  background:
    radial-gradient(circle at top right, rgba(215, 157, 44, 0.16), transparent 24%),
    linear-gradient(180deg, #fff8ec 0, #f6f8fc 220px, #ffffff 100%);
}

.page-shell {
  max-width: 1480px;
}

.page-header,
.card-header,
.header-actions,
.process-popover-header,
.process-popover-title,
.tag-list,
.path-meta,
.path-actions,
.directory-toolbar,
.account-header,
.account-title-row,
.account-meta,
.hash-line,
.file-block-header {
  display: flex;
}

.page-header,
.card-header,
.account-header,
.file-block-header {
  justify-content: space-between;
}

.page-header,
.summary-grid,
.timer-list,
.device-browser,
.file-block,
.account-grid {
  gap: 16px;
}

.page-header {
  align-items: flex-start;
  margin-bottom: 18px;
}

.page-title {
  margin: 0;
  font-size: 32px;
  color: #3f2b0c;
}

.header-actions,
.path-actions,
.directory-toolbar,
.account-title-row,
.account-meta,
.hash-line,
.tag-list {
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.summary-card,
.account-card {
  border-radius: 22px;
  border: 1px solid #ead9b5;
  background: rgba(255, 255, 255, 0.94);
}

.card-header {
  align-items: center;
  font-size: 16px;
  font-weight: 600;
  color: #47300d;
}

.readonly-path {
  padding: 14px;
  border-radius: 16px;
  background: linear-gradient(180deg, rgba(255, 248, 231, 0.9), rgba(250, 252, 255, 0.9));
}

.hash-label,
.device-config-label,
.file-block-header {
  font-size: 12px;
  color: #8a7453;
}

.account-meta,
.path-note,
.readonly-path,
.empty-inline {
  color: #71604a;
  font-size: 13px;
  line-height: 1.5;
}

.tag-list {
  gap: 8px;
}

.timer-list {
  display: flex;
  flex-direction: column;
  margin-top: 14px;
}

.timer-row {
  display: grid;
  grid-template-columns: minmax(0, 88px) minmax(0, 1fr) auto;
  gap: 10px;
  align-items: center;
}

.timer-name,
.timer-time,
.directory-chip-name,
.file-chip-name,
.process-script,
.process-command {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.process-popover {
  min-width: 0;
}

.process-popover-header {
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
}

.process-popover-title {
  align-items: center;
  gap: 8px;
  font-weight: 600;
  color: #47300d;
}

.process-script,
.process-command {
  display: inline-block;
  max-width: 100%;
}

.process-command {
  color: #71604a;
}

.hash-line {
  margin-top: 14px;
  color: #58472e;
  font-size: 13px;
}

.hash-line code {
  padding: 2px 6px;
  border-radius: 8px;
  background: #f6efe2;
  color: #614315;
}

.source-mode-switch,
.path-editor,
.page-alert {
  margin-top: 14px;
}

.status-save-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid rgba(234, 217, 181, 0.8);
}

.device-browser,
.timer-list,
.file-block {
  display: flex;
  flex-direction: column;
}

.device-config-field {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.directory-path-input {
  flex: 1 1 320px;
  min-width: 220px;
}

.directory-strip,
.file-strip,
.account-grid {
  display: grid;
}

.directory-strip,
.file-strip {
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 8px;
}

.directory-chip,
.file-chip {
  width: 100%;
  border: 1px solid rgba(148, 163, 184, 0.18);
  border-radius: 12px;
  background: #faf7f0;
  padding: 9px 11px;
  display: inline-flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  color: #4d3920;
  cursor: pointer;
  transition: background-color 0.16s ease, border-color 0.16s ease, color 0.16s ease;
}

.directory-chip:hover,
.file-chip:hover,
.file-chip.is-active {
  background: #fff2d7;
  border-color: rgba(196, 138, 22, 0.34);
  color: #8a4d00;
}

.file-chip-badge {
  flex-shrink: 0;
  padding: 2px 7px;
  border-radius: 999px;
  background: rgba(196, 138, 22, 0.12);
  color: #a16207;
  font-size: 11px;
  font-weight: 600;
}

.account-grid {
  grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
  gap: 16px;
  margin-top: 18px;
}

.account-header {
  align-items: flex-start;
}

.account-title {
  margin: 0;
  font-size: 20px;
  color: #3f2b0c;
}

.task-table :deep(.el-table__header-wrapper th.el-table__cell) {
  padding: 8px 0;
}

.task-table :deep(.el-table__body-wrapper td.el-table__cell) {
  padding: 9px 0;
}

.task-table :deep(.cell) {
  padding: 0 8px;
}

.task-time-picker {
  width: 190px;
}

.task-table :deep(.task-time-picker .el-input__wrapper) {
  border-radius: 10px;
}

.delta-text {
  color: #5c4a31;
}

.delta-text.overdue {
  color: #c45656;
  font-weight: 600;
}

.empty-state {
  margin-top: 24px;
  border-radius: 20px;
  background: rgba(255, 255, 255, 0.88);
}

@media (max-width: 1180px) {
  .summary-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 768px) {
  .task-status-page {
    padding: 16px;
  }

  .page-header,
  .directory-toolbar,
  .path-actions,
  .account-header {
    flex-direction: column;
    align-items: stretch;
  }

  .page-title {
    font-size: 28px;
  }

  .directory-path-input {
    min-width: 0;
  }

  .account-grid {
    grid-template-columns: 1fr;
  }

  .timer-row {
    grid-template-columns: 1fr;
  }
}
</style>
