<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted, nextTick } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import DocPage from '@/components/DocPage.vue';
import api, { getDeviceEntryPath } from '@/api';
import { ElMessage, ElMessageBox } from 'element-plus';
import { Edit, VideoPlay, VideoPause, Search, Delete, Connection, Hide, View, Plus, Minus } from '@element-plus/icons-vue';
import { taskStore } from '@/store/taskStore';
import { fetchRuntimeItemLogs, stopRuntimeItem, triggerRuntimeItem, type RuntimeSource } from '@/api/runtime';
import { useGlobalLogHeight } from '@/utils/useGlobalLogHeight';
import {
  createClusterServiceToken,
  deleteClusterServiceToken,
  fetchClusterServiceTokens,
  revealClusterServiceToken,
  updateClusterServiceToken,
  type ServiceAccessToken,
} from '@/api/services';
import { useUserStore } from '@/store/userStore';

interface TaskStatus {
  id: string;
  running: boolean;
  pid?: number;
  started_at?: number | string | null;
  finished_at?: number | string | null;
  cpu_percent?: number;
  memory_rss?: number;
  message?: string;
}

interface Task {
  id: string;
  name: string;
  kind?: string;
  command: string;
  description?: string;
  cwd?: string;
  device_id?: string;
  entry_id?: string;
  schedule?: string;
  schedule_label?: string;
  next_run_at?: string | null;
  timeout?: number | null;
  status: TaskStatus;
  action_labels?: Record<string, string>;
  action_descriptions?: Record<string, string>;
  action_success_messages?: Record<string, string>;
  action_error_messages?: Record<string, string>;
  actionLoading?: boolean;
}

const route = useRoute();
const router = useRouter();
const userStore = useUserStore();
const taskId = route.params.id as string;
const runtimeSource: RuntimeSource = route.query.source === 'builtin' ? 'builtin' : 'command';
const isBuiltinRuntime = runtimeSource === 'builtin';
const targetEntryId = Array.isArray(route.query.entry_id)
  ? (route.query.entry_id[0] || '')
  : ((route.query.entry_id as string) || (Array.isArray(route.query.device_id)
      ? (route.query.device_id[0] || '')
      : ((route.query.device_id as string) || '')));
const resolvedEntryId = ref(targetEntryId);
const deviceName = ref('');

const logs = ref<string[]>([]);
const task = ref<Task | null>(null);
const autoScroll = ref(true);
const logContainer = ref<HTMLElement | null>(null);
const {
  paneHeight: logHeight,
  isResizing: isLogResizing,
  startResizing: startLogResize,
} = useGlobalLogHeight();
let pollInterval: number | null = null;
let refreshAllInFlight = false;

const tokens = ref<ServiceAccessToken[]>([]);
const tokensLoading = ref(false);
const creatingToken = ref(false);
const updatingTokenId = ref('');
const deletingTokenId = ref('');
const revealingTokenId = ref('');
const tokenPlaintexts = ref<Record<string, string>>({});
const enabledTokenCount = computed(() => tokens.value.filter(token => token.enabled).length);
const isOcrRuntime = computed(() => isBuiltinRuntime && taskId === 'ocr');
const isBuiltinServiceRuntime = computed(() => isBuiltinRuntime && task.value?.kind === 'service');
const runtimeActionDisabled = computed(() => (
  isBuiltinRuntime && Boolean(task.value?.status.running) && !isBuiltinServiceRuntime.value
));
const runtimeActionButtonType = computed<'primary' | 'success' | 'warning' | 'danger' | 'info'>(() => {
  if (!task.value) return 'primary';
  if (task.value.actionLoading) return 'warning';
  if (task.value.status.running) return runtimeActionDisabled.value ? 'info' : 'danger';
  return 'success';
});
const runtimeActionButtonIcon = computed(() => (
  task.value?.status.running && (!isBuiltinRuntime || isBuiltinServiceRuntime.value) ? VideoPause : VideoPlay
));
const taskActionLabel = (actionKey: string, fallback: string) => {
  const value = task.value?.action_labels?.[actionKey];
  return typeof value === 'string' && value.trim() ? value.trim() : fallback;
};
const taskActionMessage = (
  field: 'action_success_messages' | 'action_error_messages',
  actionKey: string,
  fallback: string,
) => {
  const value = task.value?.[field]?.[actionKey];
  return typeof value === 'string' && value.trim() ? value.trim() : fallback;
};
const builtinServiceActionLabels = computed(() => {
  if (!isBuiltinServiceRuntime.value) {
    return {
      start: '启动服务',
      stop: '停止服务',
      startSuccess: '服务已启动',
      stopSuccess: '服务已停止',
    };
  }
  if (task.value?.action_labels?.trigger || task.value?.action_labels?.stop) {
    return {
      start: taskActionLabel('trigger', '启动服务'),
      stop: taskActionLabel('stop', '停止服务'),
      startSuccess: taskActionMessage('action_success_messages', 'trigger', '服务已启动'),
      stopSuccess: taskActionMessage('action_success_messages', 'stop', '服务已停止'),
    };
  }
  return {
    start: '启动服务',
    stop: '停止服务',
    startSuccess: '服务已启动',
    stopSuccess: '服务已停止',
  };
});
const runtimeActionButtonText = computed(() => {
  if (!task.value) return '';
  if (!isBuiltinRuntime) return task.value.status.running ? '停止任务' : '启动任务';
  if (isBuiltinServiceRuntime.value) {
    return task.value.status.running ? builtinServiceActionLabels.value.stop : builtinServiceActionLabels.value.start;
  }
  return task.value.status.running ? '运行中' : '执行';
});

// Edit Mode
const isEditing = ref(false);
const editForm = ref({
  name: '',
  command: '',
  cwd: '',
  description: '',
  device_id: '',
  schedule: '',
  timeout: null as number | null
});

// Related Process Scan
const scanDialogVisible = ref(false);
const scanLoading = ref(false);
const relatedProcesses = ref<any[]>([]);

const resolveDevice = async () => {
  if (!targetEntryId) {
    ElMessage.error('缺少设备入口参数');
    return false;
  }
  
  // Ensure store is loaded
  if (taskStore.devices.length === 0) {
      await taskStore.fetchDevices();
  }
  
  const device = taskStore.devices.find(d => d.id === targetEntryId);
  const matchedDevice = device || taskStore.devices.find(d => d.device_id === targetEntryId);
  if (matchedDevice) {
    resolvedEntryId.value = matchedDevice.id;
    deviceName.value = matchedDevice.name || matchedDevice.device_id;
    if (matchedDevice.id !== targetEntryId) {
      router.replace({
        name: 'TaskLogs',
        params: { id: taskId },
        query: { entry_id: matchedDevice.id, source: runtimeSource },
      });
    }
    return true;
  }

  ElMessage.error('无法找到设备或无权访问');
  if (targetEntryId) {
       console.warn(`Device entry ${targetEntryId} not found in store. Store devices:`, taskStore.devices);
       return false;
  }
  return true;
};

const handleStatusClick = async () => {
  if (!task.value || task.value.actionLoading) return;
  
  task.value.actionLoading = true;
  try {
    if (isBuiltinRuntime) {
      if (isBuiltinServiceRuntime.value && task.value.status.running) {
        await stopRuntimeItem(resolvedEntryId.value, runtimeSource, taskId);
        ElMessage.success(builtinServiceActionLabels.value.stopSuccess);
      } else {
        await triggerRuntimeItem(resolvedEntryId.value, runtimeSource, taskId);
        ElMessage.success(isBuiltinServiceRuntime.value ? builtinServiceActionLabels.value.startSuccess : '作业已提交');
      }
      await refreshAll();
      return;
    }

    if (task.value.status.running) {
      await api.post(getDeviceEntryPath(resolvedEntryId.value, `/task/${task.value.id}/stop`));
      ElMessage.success(`Task "${task.value.name}" stopping...`);
    } else {
      try {
        const relatedRes = await api.get(getDeviceEntryPath(resolvedEntryId.value, `/task/${task.value.id}/related_processes`));
        const exactMatches = relatedRes.data.filter((p: any) => p.score >= 3);
        
        if (exactMatches.length > 0) {
            try {
               await ElMessageBox.confirm(
                 `检测到系统中已有 ${exactMatches.length} 个完全匹配的进程 (PID: ${exactMatches[0].pid}) 在运行。继续启动将产生新的实例。`, 
                 '重复进程警告', 
                 {
                   confirmButtonText: '继续启动',
                   cancelButtonText: '取消',
                   type: 'warning'
                 }
               );
            } catch {
               task.value.actionLoading = false;
               return; // Cancelled
            }
        }
      } catch (e) {
        // Ignore scan error, proceed to start
      }

      await api.post(getDeviceEntryPath(resolvedEntryId.value, `/task/${task.value.id}/start`));
      ElMessage.success(`Task "${task.value.name}" started`);
    }
    await refreshAll();
  } catch (err: any) {
    if (isBuiltinRuntime && isBuiltinServiceRuntime.value) {
      const actionKey = task.value?.status.running ? 'stop' : 'trigger';
      ElMessage.error(err.response?.data?.detail || taskActionMessage('action_error_messages', actionKey, '运行单元操作失败'));
    } else {
      ElMessage.error(err.response?.data?.detail || 'Operation failed');
    }
  } finally {
    if (task.value) task.value.actionLoading = false;
  }
};

const nlpInput = ref('');

const parseNlp = () => {
    const text = nlpInput.value.trim();
    if (!text) return;
    
    // Simple regex rules
    const minuteMatch = text.match(/每(\d+)分钟/);
    if (minuteMatch) {
        editForm.value.schedule = `*/${minuteMatch[1]} * * * *`;
        ElMessage.success('已解析为 Cron 表达式');
        return;
    }
    
    if (text.includes('每小时')) {
        editForm.value.schedule = '0 * * * *';
        ElMessage.success('已解析为 Cron 表达式');
        return;
    }

    const dailyMatch = text.match(/每天(\d+)点/);
    if (dailyMatch) {
        editForm.value.schedule = `0 ${dailyMatch[1]} * * *`;
        ElMessage.success('已解析为 Cron 表达式');
        return;
    }

    // New: Timeout parsing (e.g., "超时1小时", "超时30分钟")
    const timeoutHour = text.match(/超时(\d+)小时/);
    if (timeoutHour) {
        editForm.value.timeout = parseInt(timeoutHour[1]) * 3600;
        ElMessage.success(`已设置超时: ${timeoutHour[1]} 小时`);
        return;
    }
    const timeoutMin = text.match(/超时(\d+)分钟/);
    if (timeoutMin) {
        editForm.value.timeout = parseInt(timeoutMin[1]) * 60;
        ElMessage.success(`已设置超时: ${timeoutMin[1]} 分钟`);
        return;
    }

    ElMessage.warning('无法解析该自然语言描述，请尝试标准格式或手动输入 Cron');
};

const startEditing = () => {
  if (!task.value) return;
  editForm.value = {
    name: task.value.name,
    command: task.value.command,
    cwd: task.value.cwd || '',
    description: task.value.description || '',
    device_id: task.value.device_id || '',
    schedule: task.value.schedule || '',
    timeout: task.value.timeout || null
  };
  isEditing.value = true;
};

const cancelEditing = () => {
  isEditing.value = false;
};

const saveEditing = async () => {
  try {
    await api.post(getDeviceEntryPath(resolvedEntryId.value, `/task/${taskId}/update`), editForm.value);
    ElMessage.success('Task updated');
    isEditing.value = false;
    await fetchTask();
  } catch (err: any) {
    ElMessage.error(err.response?.data?.detail || 'Failed to update task');
  }
};

const normalizeTimestampSeconds = (value: unknown): number | undefined => {
  if (value === undefined || value === null || value === '') return undefined;
  if (typeof value === 'number') {
    if (!Number.isFinite(value) || value <= 0) return undefined;
    return value > 100000000000 ? value / 1000 : value;
  }

  const text = String(value).trim();
  if (!text) return undefined;
  const numeric = Number(text);
  if (Number.isFinite(numeric) && numeric > 0) {
    return numeric > 100000000000 ? numeric / 1000 : numeric;
  }

  const parsed = new Date(text.replace(' ', 'T')).getTime();
  return Number.isFinite(parsed) ? parsed / 1000 : undefined;
};

const formatTime = (timestamp: unknown) => {
  const seconds = normalizeTimestampSeconds(timestamp);
  if (!seconds) return '-';
  return new Date(seconds * 1000).toLocaleString();
};

const formatBytes = (bytes: number | undefined) => {
  if (bytes === undefined || bytes === null) return '-';
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
};

const formatDuration = (seconds: number | undefined | null) => {
  if (seconds === undefined || seconds === null || !Number.isFinite(seconds) || seconds < 0) return '不限制';
  if (seconds === 0) return '0秒';
  
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  
  let result = '';
  if (h > 0) result += `${h}小时`;
  if (m > 0) result += `${m}分`;
  if (s > 0) result += `${s}秒`;
  
  return result || `${seconds}秒`;
};

// Composable for dynamic duration updates without polling
const useDuration = (taskRef: any) => {
  const duration = ref('');
  let timer: any = null;

  const update = () => {
    const startedAt = normalizeTimestampSeconds(taskRef.value?.status?.started_at);
    if (!taskRef.value || !taskRef.value.status.running || !startedAt) {
      duration.value = '-';
      return;
    }
    const now = Date.now() / 1000;
    const elapsed = Math.floor(now - startedAt);
    duration.value = formatDuration(elapsed);
  };

  onMounted(() => {
    update();
    timer = setInterval(update, 1000);
  });

  onUnmounted(() => {
    if (timer) clearInterval(timer);
  });

  return duration;
};

const dynamicDuration = useDuration(task);

const applyRuntimeLogPayload = (payload: any) => {
  const status = payload.status || {};
  const latestRun = status.latest_run || {};
  const stateMessage = [status.state_label, status.last_error].filter(Boolean).join(' · ');
  task.value = {
    id: payload.key || taskId,
    name: payload.title || payload.key || taskId,
    kind: payload.kind || '',
    command: payload.command || payload.description || '',
    description: payload.description || '',
    cwd: payload.cwd || '',
    device_id: resolvedEntryId.value,
    entry_id: resolvedEntryId.value,
    schedule: payload.schedule || '',
    schedule_label: payload.schedule_label || '',
    next_run_at: payload.next_run_at || status.next_run_at || null,
    timeout: payload.timeout ?? null,
    action_labels: payload.action_labels || {},
    action_descriptions: payload.action_descriptions || {},
    action_success_messages: payload.action_success_messages || {},
    action_error_messages: payload.action_error_messages || {},
    status: {
      id: payload.key || taskId,
      running: Boolean(status.running || payload.active),
      pid: status.pid || undefined,
      started_at: normalizeTimestampSeconds(status.started_at ?? latestRun.started_at),
      finished_at: normalizeTimestampSeconds(status.finished_at ?? latestRun.finished_at),
      cpu_percent: status.cpu_percent,
      memory_rss: status.memory_rss,
      message: status.message || status.stage_label || latestRun.stage_label || stateMessage || '',
    },
  };
  logs.value = Array.isArray(payload.logs) ? payload.logs : [];
};

const fetchRuntimeLogs = async () => {
  try {
    const payload = await fetchRuntimeItemLogs(resolvedEntryId.value, runtimeSource, taskId, 500);
    applyRuntimeLogPayload(payload);
    if (autoScroll.value) {
      scrollToBottom();
    }
  } catch (err) {
    console.error('Failed to fetch runtime logs', err);
  }
};

const fetchTask = async () => {
  if (isBuiltinRuntime) {
    await fetchRuntimeLogs();
    return;
  }
  try {
    const res = await api.get(getDeviceEntryPath(resolvedEntryId.value, `/task/${taskId}`));
    task.value = { ...res.data, entry_id: resolvedEntryId.value };
  } catch (err) {
    // console.error('Failed to fetch task details', err);
  }
};

const fetchLogs = async () => {
  if (isBuiltinRuntime) {
    await fetchRuntimeLogs();
    return;
  }
  try {
    const payload = await fetchRuntimeItemLogs(resolvedEntryId.value, runtimeSource, taskId, 500);
    logs.value = Array.isArray(payload.logs) ? payload.logs : [];
    if (autoScroll.value) {
      scrollToBottom();
    }
  } catch (runtimeErr: any) {
    try {
      const res = await api.get(getDeviceEntryPath(resolvedEntryId.value, `/task/${taskId}/logs`), {
        params: { n: 500 },
      });
      logs.value = res.data.logs;
      if (autoScroll.value) {
        scrollToBottom();
      }
    } catch (err) {
      console.error('Failed to fetch logs', runtimeErr, err);
    }
  }
};

const refreshAll = async () => {
  if (refreshAllInFlight) {
    return;
  }
  refreshAllInFlight = true;
  try {
    if (isBuiltinRuntime) {
      await fetchRuntimeLogs();
      return;
    }
    await Promise.all([fetchTask(), fetchLogs()]);
  } finally {
    refreshAllInFlight = false;
  }
};

const startPolling = () => {
  if (pollInterval) {
    window.clearInterval(pollInterval);
  }
  pollInterval = window.setInterval(() => {
    void refreshAll();
  }, 3000);
};

// const checkStatusOnly = async () => {
//     // ... implementation
// };

const scrollToBottom = async () => {
  await nextTick();
  if (logContainer.value) {
    logContainer.value.scrollTop = logContainer.value.scrollHeight;
  }
};

const goBack = () => {
  const entryId = resolvedEntryId.value || route.query.entry_id || route.query.device_id;
  router.push({
    name: 'RuntimeManagement',
    query: entryId ? { entry_id: entryId } : {},
  });
};

const handleScanProcesses = async () => {
  scanDialogVisible.value = true;
  scanLoading.value = true;
  try {
    const res = await api.get(getDeviceEntryPath(resolvedEntryId.value, `/task/${taskId}/related_processes`));
    relatedProcesses.value = res.data;
  } catch (err: any) {
    ElMessage.error(err.response?.data?.detail || 'Scan failed');
  } finally {
    scanLoading.value = false;
  }
};

const killProcess = async (pid: number) => {
  try {
    await ElMessageBox.confirm(`Are you sure to kill process PID ${pid}?`, 'Warning', {
      type: 'warning',
      confirmButtonText: 'Kill',
      confirmButtonClass: 'el-button--danger',
    });
    
    await api.post(getDeviceEntryPath(resolvedEntryId.value, '/task/process/kill'), { pid });
    ElMessage.success(`Process ${pid} killed`);
    
    // Refresh list
    handleScanProcesses();
  } catch (err) {
    if (err !== 'cancel') {
        console.error(err);
        ElMessage.error('Failed to kill process');
    }
  }
};

const associateProcess = async (pid: number) => {
  try {
    await ElMessageBox.confirm(`确定要将当前任务关联到进程 PID ${pid} 吗？\n这将更新任务的命令、工作目录等信息，并以该进程为准进行监控。`, '确认关联', {
      type: 'warning',
      confirmButtonText: '关联',
      cancelButtonText: '取消'
    });
    
    await api.post(getDeviceEntryPath(resolvedEntryId.value, `/task/${taskId}/associate`), { pid });
    ElMessage.success(`已关联到进程 PID ${pid}`);
    
    scanDialogVisible.value = false;
    refreshAll();
  } catch (err: any) {
    if (err !== 'cancel') {
      console.error(err);
      ElMessage.error(err.response?.data?.detail || '关联失败');
    }
  }
};

const getErrorMessage = (error: unknown) => {
  if (typeof error === 'object' && error && 'response' in error) {
    const maybeError = error as { response?: { data?: { detail?: string } }, message?: string };
    return maybeError.response?.data?.detail || maybeError.message || '请求失败';
  }
  return error instanceof Error ? error.message : '请求失败';
};

const loadTokens = async () => {
  if (!isOcrRuntime.value || !resolvedEntryId.value || !userStore.isAdmin) return;
  tokensLoading.value = true;
  try {
    tokens.value = await fetchClusterServiceTokens(resolvedEntryId.value);
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  } finally {
    tokensLoading.value = false;
  }
};

const createToken = async () => {
  if (!resolvedEntryId.value || !userStore.isAdmin) return;
  creatingToken.value = true;
  try {
    const token = await createClusterServiceToken(resolvedEntryId.value, {});
    tokens.value = [...tokens.value, token];
    if (token.plaintext_value) {
      tokenPlaintexts.value = { ...tokenPlaintexts.value, [token.id]: token.plaintext_value };
    }
    ElMessage.success('已新增服务 Token');
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  } finally {
    creatingToken.value = false;
  }
};

const toggleToken = async (token: ServiceAccessToken, enabled: boolean) => {
  if (!resolvedEntryId.value || !userStore.isAdmin) return;
  updatingTokenId.value = token.id;
  try {
    const updated = await updateClusterServiceToken(resolvedEntryId.value, token.id, { enabled });
    tokens.value = tokens.value.map(item => item.id === token.id ? updated : item);
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  } finally {
    updatingTokenId.value = '';
  }
};

const isTokenVisible = (tokenId: string) => Boolean(tokenPlaintexts.value[tokenId]);

const getTokenDisplayValue = (token: ServiceAccessToken) => tokenPlaintexts.value[token.id] || token.masked_value;

const toggleTokenReveal = async (token: ServiceAccessToken) => {
  if (tokenPlaintexts.value[token.id]) {
    const next = { ...tokenPlaintexts.value };
    delete next[token.id];
    tokenPlaintexts.value = next;
    return;
  }
  if (!resolvedEntryId.value || !userStore.isAdmin) return;
  revealingTokenId.value = token.id;
  try {
    const revealed = await revealClusterServiceToken(resolvedEntryId.value, token.id);
    tokenPlaintexts.value = { ...tokenPlaintexts.value, [token.id]: revealed.plaintext_value || '' };
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  } finally {
    revealingTokenId.value = '';
  }
};

const removeToken = async (token: ServiceAccessToken) => {
  if (!resolvedEntryId.value || !userStore.isAdmin) return;
  try {
    await ElMessageBox.confirm('将删除这个服务 Token，外部调用会立即失效。', '删除 Token', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    });
  } catch {
    return;
  }
  deletingTokenId.value = token.id;
  try {
    await deleteClusterServiceToken(resolvedEntryId.value, token.id);
    tokens.value = tokens.value.filter(item => item.id !== token.id);
    const next = { ...tokenPlaintexts.value };
    delete next[token.id];
    tokenPlaintexts.value = next;
    ElMessage.success('已删除服务 Token');
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  } finally {
    deletingTokenId.value = '';
  }
};

onMounted(async () => {
  const success = await resolveDevice();
  if (success) {
      if (userStore.token && !userStore.user) {
        await userStore.fetchUserProfile();
      }
      await refreshAll();
      await loadTokens();
      startPolling();
  }
});

onUnmounted(() => {
  if (pollInterval) {
    window.clearInterval(pollInterval);
    pollInterval = null;
  }
});
</script>

<template>
  <DocPage title="运行详情" :description="task?.name ? `运行: ${task.name}` : `运行ID: ${taskId}`">
    <div class="toolbar">
      <el-button @click="goBack">返回列表</el-button>
      <div style="flex: 1"></div>
      
      <div v-if="!isEditing" style="display: flex; gap: 10px;">
        <el-button v-if="!isBuiltinRuntime" :icon="Edit" @click="startEditing">编辑</el-button>
        <el-button v-if="!isBuiltinRuntime" :icon="Search" @click="handleScanProcesses">同名进程</el-button>
        <el-checkbox v-model="autoScroll" style="margin-right: 10px;">自动滚动</el-checkbox>
        <el-button type="primary" link @click="refreshAll">刷新</el-button>

        <el-button 
          v-if="task"
          :type="runtimeActionButtonType"
          :loading="task.actionLoading"
          :disabled="runtimeActionDisabled"
          @click="handleStatusClick"
          :icon="runtimeActionButtonIcon"
        >
          {{ runtimeActionButtonText }}
        </el-button>
      </div>
      <div v-else style="display: flex; gap: 10px;">
        <el-button @click="cancelEditing">取消</el-button>
        <el-button type="primary" @click="saveEditing">保存修改</el-button>
      </div>
    </div>

    <div v-if="task" class="task-info">
      <template v-if="!isEditing">
      <el-descriptions :column="2" border size="small" style="margin-top: 10px;">
        <el-descriptions-item label="名称">{{ task.name }}</el-descriptions-item>
        <el-descriptions-item label="设备">{{ deviceName || task.device_id || 'Local' }}</el-descriptions-item>

        <el-descriptions-item label="状态">
           <el-tag :type="task.status.running ? 'success' : 'info'" size="small">
              {{ task.status.running ? '运行' : '停止' }}
           </el-tag>
           <span v-if="task.status.pid" style="margin-left: 10px; color: #909399;">
              PID: {{ task.status.pid }}
           </span>
        </el-descriptions-item>
        <el-descriptions-item :label="isBuiltinRuntime ? '阶段' : '资源使用'">
          <span v-if="isBuiltinRuntime">{{ task.status.message || '-' }}</span>
          <span v-else-if="task.status.running">
             CPU: {{ task.status.cpu_percent }}% | Mem: {{ formatBytes(task.status.memory_rss) }}
          </span>
          <span v-else style="color: #909399;">-</span>
        </el-descriptions-item>

        <el-descriptions-item label="启动时间">
          {{ formatTime(task.status.started_at) }}
        </el-descriptions-item>
        <el-descriptions-item label="结束/时长">
           <span v-if="!task.status.running && task.status.finished_at">结束: {{ formatTime(task.status.finished_at) }}</span>
           <span v-if="task.status.running && task.status.started_at">时长: {{ dynamicDuration }}</span>
           <span v-if="!task.status.running && !task.status.finished_at">-</span>
        </el-descriptions-item>

        <el-descriptions-item label="定时调度">{{ task.schedule_label || task.schedule || '手动' }}</el-descriptions-item>
        <el-descriptions-item label="超时时间">{{ formatDuration(task.timeout) }}</el-descriptions-item>

        <el-descriptions-item label="执行" :span="2">
          {{ task.command || task.description || '-' }}
        </el-descriptions-item>
        
        <el-descriptions-item v-if="!isBuiltinRuntime" label="工作目录" :span="2">{{ task.cwd || '默认' }}</el-descriptions-item>
        
        <el-descriptions-item v-if="!isBuiltinRuntime && task.description" label="描述" :span="2">{{ task.description }}</el-descriptions-item>
      </el-descriptions>

      <section v-if="isOcrRuntime" class="runtime-config-section">
        <div class="runtime-config-title">
          <span>Token</span>
          <span v-if="userStore.isAdmin" class="runtime-config-meta">{{ tokens.length }} 个，{{ enabledTokenCount }} 启用</span>
          <span v-else class="runtime-config-meta">管理员可管理</span>
          <el-button
            v-if="userStore.isAdmin"
            text
            size="small"
            :icon="Plus"
            :loading="creatingToken"
            title="新增 Token"
            aria-label="新增 Token"
            @click="createToken"
          />
        </div>
        <div v-if="userStore.isAdmin" class="token-panel">
          <div v-if="tokensLoading" class="empty-text">加载中</div>
          <div v-else-if="tokens.length" class="token-list">
            <div v-for="token in tokens" :key="token.id" class="token-row">
              <el-switch
                size="small"
                :model-value="token.enabled"
                :loading="updatingTokenId === token.id"
                @change="(value: string | number | boolean) => toggleToken(token, Boolean(value))"
              />
              <code class="token-value" :class="{ disabled: !token.enabled }">{{ getTokenDisplayValue(token) }}</code>
              <el-button
                text
                size="small"
                :icon="isTokenVisible(token.id) ? Hide : View"
                :loading="revealingTokenId === token.id"
                :title="isTokenVisible(token.id) ? '隐藏 Token' : '显示 Token'"
                :aria-label="isTokenVisible(token.id) ? '隐藏 Token' : '显示 Token'"
                @click="toggleTokenReveal(token)"
              />
              <span class="token-stat">调用 {{ token.call_count }}</span>
              <el-button
                text
                type="danger"
                size="small"
                :icon="Minus"
                :loading="deletingTokenId === token.id"
                title="删除 Token"
                aria-label="删除 Token"
                @click="removeToken(token)"
              />
            </div>
          </div>
          <div v-else class="empty-text">暂无服务 Token</div>
        </div>
      </section>
      </template>

      <!-- Inline Edit Form -->
      <el-form v-else :model="editForm" label-width="80px" class="edit-form">
        <el-row :gutter="20">
          <el-col :span="12">
            <el-form-item label="名称" required>
              <el-input v-model="editForm.name" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="定时调度">
               <el-input v-model="editForm.schedule" placeholder="Cron 表达式 (例如: */5 * * * *)">
                  <template #append>
                    <el-popover placement="bottom" title="自然语言解析" :width="300" trigger="click">
                      <template #reference>
                        <el-button :icon="Edit" />
                      </template>
                      <el-input v-model="nlpInput" placeholder="例如: 每5分钟, 超时1小时" size="small">
                        <template #append>
                          <el-button @click="parseNlp">解析</el-button>
                        </template>
                      </el-input>
                    </el-popover>
                  </template>
               </el-input>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="超时时间">
               <el-input v-model.number="editForm.timeout" placeholder="单位: 秒" type="number" />
            </el-form-item>
          </el-col>
        </el-row>
        
        <el-form-item label="命令" required>
          <el-input v-model="editForm.command" type="textarea" :rows="3" />
        </el-form-item>
        
        <el-form-item label="工作目录">
          <el-input v-model="editForm.cwd" placeholder="可选: 执行目录绝对路径" />
        </el-form-item>
        
        <el-form-item label="描述">
          <el-input v-model="editForm.description" placeholder="可选: 任务描述" />
        </el-form-item>
      </el-form>
    </div>

    <div class="log-panel" :style="{ height: `${logHeight}px` }">
      <div class="log-container" ref="logContainer">
        <div v-if="logs.length === 0" class="no-logs">暂无日志</div>
        <div v-else v-for="(log, index) in logs" :key="index" class="log-line">
          {{ log }}
        </div>
      </div>
      <button
        type="button"
        class="log-resize-handle"
        :class="{ 'is-resizing': isLogResizing }"
        title="拖拽调整日志高度"
        aria-label="调整日志高度"
        @mousedown.prevent="startLogResize"
      >
        <span class="log-resize-handle__indicator" aria-hidden="true"></span>
      </button>
    </div>

    <!-- Scan Result Dialog -->
    <el-dialog v-model="scanDialogVisible" title="同名进程检测" width="800px">
      <div v-loading="scanLoading">
        <el-alert
          type="info"
          show-icon
          :closable="false"
          style="margin-bottom: 15px;"
        >
          <template #title>
             <div>
               以下是系统中与当前任务命令相似的进程。
               <div style="margin-top: 4px; font-weight: normal;">
                 <span style="color: #606266;">可执行文件: </span>
                 <el-tag size="small" type="info" effect="plain" v-if="relatedProcesses.length > 0 && relatedProcesses[0].exe">
                    {{ relatedProcesses[0].exe }}
                 </el-tag>
                 <span v-else>{{ relatedProcesses.length > 0 ? relatedProcesses[0].name : '未知' }}</span>
               </div>
               <div style="margin-top: 2px; color: #909399; font-size: 12px;">如果发现重复实例，可以尝试手动清理。</div>
             </div>
          </template>
        </el-alert>
        
        <el-table :data="relatedProcesses" border style="width: 100%" height="400">
          <el-table-column prop="pid" label="PID" width="80" />
          <!-- <el-table-column prop="name" label="进程名" width="150" show-overflow-tooltip /> -->
          <el-table-column label="命令行" show-overflow-tooltip>
            <template #default="{ row }">
              <span v-if="row.cmd_args" :title="row.cmdline">{{ row.cmd_args }}</span>
              <span v-else>{{ row.cmdline }}</span>
            </template>
          </el-table-column>
          <el-table-column label="启动时间" width="160">
            <template #default="{ row }">
              {{ formatTime(row.started_at) }}
            </template>
          </el-table-column>
          <el-table-column label="内存" width="100">
            <template #default="{ row }">
              {{ formatBytes(row.memory_rss) }}
            </template>
          </el-table-column>
          <el-table-column label="匹配度" width="80" align="center">
             <template #default="{ row }">
                <el-tag size="small" :type="row.score >= 3 ? 'success' : (row.score === 2 ? 'warning' : 'info')">
                  {{ row.score >= 3 ? '完全' : (row.score === 2 ? '部分' : '名称') }}
                </el-tag>
             </template>
          </el-table-column>
          <el-table-column label="操作" width="120" align="center">
            <template #default="{ row }">
              <el-button 
                type="primary" 
                :icon="Connection" 
                circle 
                plain
                size="small" 
                title="关联此进程"
                @click="associateProcess(row.pid)"
                style="margin-right: 5px;"
              />
              <el-button 
                type="danger" 
                :icon="Delete" 
                circle 
                size="small" 
                title="强制结束"
                @click="killProcess(row.pid)"
              />
            </template>
          </el-table-column>
        </el-table>
      </div>
      <template #footer>
        <el-button @click="scanDialogVisible = false">关闭</el-button>
        <el-button type="primary" :icon="Search" :loading="scanLoading" @click="handleScanProcesses">重新扫描</el-button>
      </template>
    </el-dialog>
  </DocPage>
</template>

<style scoped>
.toolbar {
  display: flex;
  gap: 15px;
  align-items: center;
  margin-bottom: 15px;
}

.task-info {
  margin-bottom: 20px;
}

.pid-tag {
  margin-left: 8px;
  font-size: 0.9em;
  color: #888;
}

.code-block {
  font-family: 'Consolas', 'Monaco', monospace;
  background-color: #f5f7fa;
  padding: 8px;
  border-radius: 4px;
  white-space: pre-wrap;
  word-break: break-all;
  border: 1px solid #e4e7ed;
  max-height: 200px;
  overflow-y: auto;
}

.edit-form {
  padding: 20px;
  background-color: #f5f7fa;
  border: 1px solid #e4e7ed;
  border-radius: 4px;
}

.runtime-config-section {
  margin-top: 14px;
  border-top: 1px solid #ebeef5;
  padding-top: 10px;
}

.runtime-config-title {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 8px;
}

.runtime-config-meta {
  font-size: 12px;
  font-weight: 400;
  color: #909399;
}

.token-panel {
  max-width: 720px;
}

.token-list {
  border-top: 1px solid #ebeef5;
}

.token-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 34px;
  border-bottom: 1px solid #ebeef5;
}

.token-value {
  font-family: 'Consolas', 'Monaco', monospace;
  font-size: 12px;
  color: #303133;
  min-width: 170px;
}

.token-value.disabled {
  color: #a8abb2;
}

.token-stat,
.empty-text {
  font-size: 12px;
  color: #909399;
}

.log-panel {
  background: #1e1e1e;
  color: #d4d4d4;
  border-radius: 4px;
  display: flex;
  flex-direction: column;
  min-height: 220px;
  overflow: hidden;
}

.log-container {
  flex: 1;
  min-height: 0;
  padding: 15px;
  font-family: 'Consolas', 'Monaco', monospace;
  overflow-y: auto;
  white-space: pre-wrap;
}

.log-resize-handle {
  flex: 0 0 9px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border-top: 1px solid #333;
  border-right: 0;
  border-bottom: 0;
  border-left: 0;
  background: #252526;
  cursor: ns-resize;
  outline: none;
  touch-action: none;
}

.log-resize-handle__indicator {
  width: 36px;
  height: 2px;
  border-radius: 999px;
  background: #6a6a6a;
}

.log-resize-handle:hover .log-resize-handle__indicator,
.log-resize-handle:focus-visible .log-resize-handle__indicator,
.log-resize-handle.is-resizing .log-resize-handle__indicator {
  background: #9cdcfe;
}

.log-line {
  line-height: 1.5;
  border-bottom: 1px solid #333;
}

.no-logs {
  color: #666;
  text-align: center;
  padding: 20px;
}

.form-tip {
  font-size: 12px;
  color: #909399;
  line-height: 1.2;
  margin-top: 4px;
}
</style>
