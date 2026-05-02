<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { Clock, QuestionFilled, RefreshRight } from '@element-plus/icons-vue';

import {
  fetchCodexDailySummaryLatestForEntries,
  fetchCodexDailySummaryLatestForEntry,
  fetchCodexDailySummaryRunForEntries,
  fetchCodexDailySummaryRunForEntry,
  startCodexDailySummaryRunForEntries,
  startCodexDailySummaryRunForEntry,
  type CodexDailySummaryResponse,
  type CodexDailySummaryRunRead,
  type CodexDailySummaryThread,
} from '@/api/codexSessions';
import { taskStore, type Device } from '@/store/taskStore';

const route = useRoute();
const router = useRouter();

const RUN_STAGE_ORDER = [
  { key: 'queued', label: '排队' },
  { key: 'loading_cache', label: '读取缓存' },
  { key: 'building_prompt', label: '整理提纲' },
  { key: 'running_codex', label: '调用 Codex' },
  { key: 'completed', label: '完成' },
] as const;

const ALL_DEVICES_ENTRY_ID = '__all__';

const isLoadingDevices = ref(false);
const isLoadingLatest = ref(false);
const isStartingRun = ref(false);
const selectedEntryId = ref('');
const rootDirInput = ref('');
const summaryDate = ref('');
const summaryRun = ref<CodexDailySummaryRunRead | null>(null);
const errorMessage = ref('');
const nowTick = ref(Date.now());

const devices = computed(() => taskStore.devices);
const showDeviceEmptyState = computed(() => !isLoadingDevices.value && !devices.value.length);
const isAllDevicesMode = computed(() => selectedEntryId.value === ALL_DEVICES_ENTRY_ID);
const selectedDevice = computed(() => (
  devices.value.find((device) => device.id === selectedEntryId.value) ?? null
));
const selectedSourceDevices = computed<Device[]>(() => {
  if (isAllDevicesMode.value) return devices.value.slice();
  return selectedDevice.value ? [selectedDevice.value] : [];
});
const canOperate = computed(() => Boolean(selectedSourceDevices.value.length && summaryDate.value.trim()));
const report = computed<CodexDailySummaryResponse | null>(() => summaryRun.value?.result ?? null);
const isGenerating = computed(() => ['queued', 'running'].includes(summaryRun.value?.status || ''));
const hasCompletedSummary = computed(() => summaryRun.value?.status === 'completed' && Boolean(summaryRun.value?.result));
const primaryActionLabel = computed(() => (hasCompletedSummary.value ? '重新生成' : '生成总结'));
const liveThreadCount = computed(() => summaryRun.value?.thread_count ?? report.value?.thread_count ?? 0);
const liveTurnCount = computed(() => summaryRun.value?.turn_count ?? report.value?.turn_count ?? 0);
const liveMessageCount = computed(() => {
  const run = summaryRun.value;
  if (!run) return '0 / 0';
  return `${run.user_message_count || 0} / ${run.assistant_message_count || 0}`;
});

let runPollTimer: number | null = null;
let runPollInFlight = false;
let latestReloadTimer: number | null = null;
let clockTimer: number | null = null;

const formatDateInput = (date: Date) => (
  [
    date.getFullYear(),
    String(date.getMonth() + 1).padStart(2, '0'),
    String(date.getDate()).padStart(2, '0'),
  ].join('-')
);

const resolveDefaultSummaryDate = () => {
  if (typeof route.query.date === 'string' && route.query.date.trim()) {
    return route.query.date.trim();
  }
  const date = new Date();
  date.setDate(date.getDate() - 1);
  return formatDateInput(date);
};

const normalizeRootDirForRequest = () => {
  if (isAllDevicesMode.value) return undefined;
  const value = rootDirInput.value.trim();
  return value || undefined;
};

const buildCodexRouteQuery = () => ({
  ...(selectedEntryId.value ? { entryId: selectedEntryId.value } : {}),
  ...(normalizeRootDirForRequest() ? { rootDir: normalizeRootDirForRequest() } : {}),
  ...(summaryDate.value.trim() ? { date: summaryDate.value.trim() } : {}),
});

const extractErrorMessage = (error: any, fallback: string) =>
  error?.response?.data?.detail || error?.message || fallback;

const formatDeviceLabel = (device?: Pick<Device, 'name' | 'device_id'> | null) => (
  device?.name || device?.device_id || ''
);

const getSelectedEntryIds = () => selectedSourceDevices.value.map(device => device.id);

const resolveDateValue = (value?: number | string | null) => {
  if (value === null || value === undefined || value === '') return null;
  const date = typeof value === 'number'
    ? new Date(value < 1_000_000_000_000 ? value * 1000 : value)
    : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
};

const formatDateTime = (value?: number | string | null) => {
  const date = resolveDateValue(value);
  if (!date) return '未记录';
  return date.toLocaleString('zh-CN', { hour12: false });
};

const formatProjectLabel = (thread: CodexDailySummaryThread) => (
  [thread.project_label, thread.project_secondary_label].filter(Boolean).join(' · ')
);

const formatDuration = (seconds: number) => {
  const safeSeconds = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(safeSeconds / 3600);
  const minutes = Math.floor((safeSeconds % 3600) / 60);
  const remainSeconds = safeSeconds % 60;
  if (hours > 0) return `${hours}小时${String(minutes).padStart(2, '0')}分`;
  if (minutes > 0) return `${minutes}分${String(remainSeconds).padStart(2, '0')}秒`;
  return `${remainSeconds}秒`;
};

const formatElapsed = (run: CodexDailySummaryRunRead | null) => {
  if (!run) return '0秒';
  const endAt = run.finished_at ?? nowTick.value / 1000;
  return formatDuration(Math.max(0, endAt - run.created_at));
};

const formatHeartbeatAge = (value?: number | null) => {
  if (!value) return '未记录';
  const seconds = Math.max(0, Math.floor(nowTick.value / 1000 - value));
  if (seconds < 5) return '刚刚';
  return `${seconds} 秒前`;
};

const getStageState = (stageKey: string) => {
  const currentStage = summaryRun.value?.stage || '';
  const currentIndex = RUN_STAGE_ORDER.findIndex(item => item.key === currentStage);
  const itemIndex = RUN_STAGE_ORDER.findIndex(item => item.key === stageKey);
  if (summaryRun.value?.status === 'failed') {
    if (stageKey === currentStage) return 'current';
    return itemIndex >= 0 && currentIndex >= 0 && itemIndex < currentIndex ? 'done' : 'idle';
  }
  if (summaryRun.value?.status === 'completed') {
    return itemIndex >= 0 ? 'done' : 'idle';
  }
  if (itemIndex < 0 || currentIndex < 0) return 'idle';
  if (itemIndex < currentIndex) return 'done';
  if (itemIndex === currentIndex) return 'current';
  return 'idle';
};

const stopRunPolling = () => {
  if (runPollTimer !== null) {
    window.clearInterval(runPollTimer);
    runPollTimer = null;
  }
};

const startRunPolling = (entryId: string, runId: string) => {
  stopRunPolling();
  void refreshRunSilently(entryId, runId);
  runPollTimer = window.setInterval(() => {
    void refreshRunSilently(entryId, runId);
  }, 1500);
};

const refreshRunSilently = async (entryId: string, runId: string) => {
  if (runPollInFlight) return;
  runPollInFlight = true;
  try {
    const nextRun = entryId === ALL_DEVICES_ENTRY_ID
      ? await fetchCodexDailySummaryRunForEntries(runId)
      : await fetchCodexDailySummaryRunForEntry(entryId, runId);
    summaryRun.value = nextRun;
    if (nextRun.status === 'completed' || nextRun.status === 'failed') {
      stopRunPolling();
    }
  } catch (error: any) {
    stopRunPolling();
    errorMessage.value = extractErrorMessage(error, '读取日报任务状态失败');
  } finally {
    runPollInFlight = false;
  }
};

const ensureDevicesLoaded = async () => {
  if (isLoadingDevices.value) return;
  isLoadingDevices.value = true;
  try {
    await taskStore.fetchDevices();
    if (!selectedEntryId.value && taskStore.devices.length) {
      selectedEntryId.value = taskStore.devices.length > 1 ? ALL_DEVICES_ENTRY_ID : taskStore.devices[0].id;
    } else if (selectedEntryId.value === ALL_DEVICES_ENTRY_ID && taskStore.devices.length < 2) {
      selectedEntryId.value = taskStore.devices[0]?.id || '';
    }
  } finally {
    isLoadingDevices.value = false;
  }
};

const loadLatestSummary = async (options: { silent?: boolean } = {}) => {
  if (!canOperate.value) {
    stopRunPolling();
    summaryRun.value = null;
    if (!options.silent) errorMessage.value = '';
    return;
  }
  if (!options.silent) {
    isLoadingLatest.value = true;
    errorMessage.value = '';
  }
  stopRunPolling();
  try {
    const nextRun = isAllDevicesMode.value
      ? await fetchCodexDailySummaryLatestForEntries(getSelectedEntryIds(), {
        date: summaryDate.value.trim(),
      })
      : await fetchCodexDailySummaryLatestForEntry(selectedEntryId.value, {
        date: summaryDate.value.trim(),
        root_dir: normalizeRootDirForRequest(),
      });
    summaryRun.value = nextRun;
    if (nextRun.status === 'running' || nextRun.status === 'queued') {
      startRunPolling(selectedEntryId.value, nextRun.id);
    }
  } catch (error: any) {
    const status = error?.response?.status;
    if (status === 404) {
      summaryRun.value = null;
      if (!options.silent) errorMessage.value = '';
      return;
    }
    summaryRun.value = null;
    errorMessage.value = extractErrorMessage(error, '读取已存日报失败');
  } finally {
    if (!options.silent) {
      isLoadingLatest.value = false;
    }
  }
};

const scheduleLatestReload = () => {
  if (latestReloadTimer !== null) {
    window.clearTimeout(latestReloadTimer);
  }
  latestReloadTimer = window.setTimeout(() => {
    void loadLatestSummary({ silent: true });
  }, 250);
};

const startRun = async (force = false) => {
  if (!canOperate.value || isStartingRun.value) return;
  isStartingRun.value = true;
  errorMessage.value = '';
  try {
    const nextRun = isAllDevicesMode.value
      ? await startCodexDailySummaryRunForEntries(getSelectedEntryIds(), {
        date: summaryDate.value.trim(),
        force,
      })
      : await startCodexDailySummaryRunForEntry(selectedEntryId.value, {
        date: summaryDate.value.trim(),
        root_dir: normalizeRootDirForRequest(),
        force,
      });
    summaryRun.value = nextRun;
    if (nextRun.status === 'running' || nextRun.status === 'queued') {
      startRunPolling(selectedEntryId.value, nextRun.id);
    }
  } catch (error: any) {
    errorMessage.value = extractErrorMessage(error, '启动日报任务失败');
  } finally {
    isStartingRun.value = false;
  }
};

const handlePrimaryAction = async () => {
  await startRun(hasCompletedSummary.value);
};

const handleRefresh = async () => {
  await loadLatestSummary();
};

const goToSessions = () => {
  void router.push({
    path: '/cluster/codex',
    query: buildCodexRouteQuery(),
  });
};

const goToClusterTasks = () => {
  void router.push('/cluster/tasks');
};

const applyRouteSeed = () => {
  if (typeof route.query.entryId === 'string' && route.query.entryId.trim()) {
    selectedEntryId.value = route.query.entryId.trim();
  }
  if (typeof route.query.rootDir === 'string') {
    rootDirInput.value = route.query.rootDir.trim();
  }
  summaryDate.value = resolveDefaultSummaryDate();
};

watch([selectedEntryId, summaryDate], () => {
  errorMessage.value = '';
  scheduleLatestReload();
});

watch(rootDirInput, () => {
  errorMessage.value = '';
  scheduleLatestReload();
});

onMounted(async () => {
  applyRouteSeed();
  await ensureDevicesLoaded();
  await loadLatestSummary();
  clockTimer = window.setInterval(() => {
    nowTick.value = Date.now();
  }, 1000);
});

onBeforeUnmount(() => {
  stopRunPolling();
  if (latestReloadTimer !== null) {
    window.clearTimeout(latestReloadTimer);
  }
  if (clockTimer !== null) {
    window.clearInterval(clockTimer);
  }
});
</script>

<template>
  <div class="codex-daily-page">
    <section v-if="showDeviceEmptyState" class="codex-daily-empty">
      <div class="codex-daily-empty-badge">日报</div>
      <h2>先添加设备</h2>
      <p>日报总结需要按设备读取本机或远端节点上的 `.codex` 会话数据，所以要先在集群管理里准备可用设备。</p>
      <el-button type="primary" @click="goToClusterTasks">去设备任务</el-button>
    </section>

    <section v-else class="codex-daily-shell">
      <section class="codex-daily-toolbar">
        <div class="codex-daily-toolbar-head">
          <div>
            <h2>日报总结</h2>
            <p>按自然日汇总单设备或全部设备的聊天记录，优先参考星图笔记类型归类，再交给本机 `codex` 生成层次日报。</p>
          </div>
          <el-button @click="goToSessions">会话页</el-button>
        </div>

        <div class="codex-daily-toolbar-grid">
          <label class="codex-daily-field">
            <span class="codex-daily-field-label">设备</span>
            <el-select
              v-model="selectedEntryId"
              class="codex-daily-field-control"
              size="large"
              placeholder="选择设备"
              :disabled="isLoadingDevices || !devices.length"
            >
              <el-option
                v-if="devices.length > 1"
                :key="ALL_DEVICES_ENTRY_ID"
                label="全部设备"
                :value="ALL_DEVICES_ENTRY_ID"
              />
              <el-option
                v-for="device in devices"
                :key="device.id"
                :label="formatDeviceLabel(device)"
                :value="device.id"
              />
            </el-select>
          </label>

          <label class="codex-daily-field codex-daily-field-root">
            <span class="codex-daily-field-label">Codex 根目录</span>
            <el-input
              v-model="rootDirInput"
              class="codex-daily-field-control"
              size="large"
              clearable
              :disabled="isAllDevicesMode"
              :placeholder="isAllDevicesMode ? '全部设备使用各自默认 .codex' : '例如 C:\Users\kzche\.codex'"
            />
          </label>

          <label class="codex-daily-field codex-daily-field-date">
            <span class="codex-daily-field-label-row">
              <span class="codex-daily-field-label">日期</span>
              <el-tooltip effect="light" placement="top">
                <template #content>
                  先读取数据库里该天的已存结果；只有你点“重新生成”时，才会按最新提示词和算法再跑一遍。
                </template>
                <button type="button" class="codex-daily-help-button" aria-label="日期说明">
                  <el-icon><QuestionFilled /></el-icon>
                </button>
              </el-tooltip>
            </span>
            <el-date-picker
              v-model="summaryDate"
              class="codex-daily-field-control"
              type="date"
              size="large"
              value-format="YYYY-MM-DD"
              format="YYYY-MM-DD"
              placeholder="选择日期"
            />
          </label>

          <div class="codex-daily-actions">
            <el-button
              size="large"
              :disabled="!canOperate || isLoadingLatest"
              @click="handleRefresh"
            >
              刷新已存
            </el-button>
            <el-button
              type="primary"
              size="large"
              :loading="isStartingRun || isGenerating"
              :disabled="!canOperate"
              @click="handlePrimaryAction"
            >
              <el-icon><RefreshRight /></el-icon>
              <span>{{ primaryActionLabel }}</span>
            </el-button>
          </div>
        </div>
      </section>

      <section v-if="summaryRun" class="codex-daily-stats">
        <div class="codex-daily-stat-card">
          <span>会话</span>
          <strong>{{ liveThreadCount }}</strong>
        </div>
        <div class="codex-daily-stat-card">
          <span>轮次</span>
          <strong>{{ liveTurnCount }}</strong>
        </div>
        <div class="codex-daily-stat-card">
          <span>消息</span>
          <strong>{{ liveMessageCount }}</strong>
        </div>
      </section>

      <section class="codex-daily-result" v-loading="isLoadingLatest">
        <el-alert
          v-if="errorMessage"
          type="error"
          :closable="false"
          show-icon
          :title="errorMessage"
        />

        <template v-if="summaryRun">
          <div class="codex-daily-progress">
            <div class="codex-daily-progress-head">
              <div>
                <h3>{{ summaryRun.date }} 状态</h3>
                <p>{{ summaryRun.stage_label }}</p>
              </div>
              <div class="codex-daily-progress-meta">
                <span>
                  <el-icon><Clock /></el-icon>
                  已运行 {{ formatElapsed(summaryRun) }}
                </span>
                <span>最近心跳 {{ formatHeartbeatAge(summaryRun.heartbeat_at) }}</span>
                <span v-if="summaryRun.model">{{ summaryRun.model }}</span>
              </div>
            </div>

            <div class="codex-daily-stage-list">
              <div
                v-for="stage in RUN_STAGE_ORDER"
                :key="stage.key"
                class="codex-daily-stage-item"
                :class="`is-${getStageState(stage.key)}`"
              >
                <span class="codex-daily-stage-dot" />
                <span>{{ stage.label }}</span>
              </div>
            </div>
          </div>

          <div v-if="report && report.generated_by !== 'empty'" class="codex-daily-summary-panel">
            <div class="codex-daily-result-head">
              <div class="codex-daily-result-title">
                <h3>{{ report.date }} 日报总结</h3>
                <p>结果已持久化到数据库；再次点击“重新生成”会按最新机制重跑。</p>
              </div>
              <div class="codex-daily-result-meta">
                <span>{{ report.prompt_version }}</span>
                <span v-if="report.generated_at">{{ formatDateTime(report.generated_at) }}</span>
              </div>
            </div>

            <pre class="codex-daily-summary-text">{{ report.summary_text }}</pre>

            <section class="codex-daily-source-list">
              <div class="codex-daily-source-head">
                <strong>来源会话</strong>
                <span>{{ report.thread_count }} 个</span>
              </div>

              <article
                v-for="thread in report.threads"
                :key="thread.thread_id"
                class="codex-daily-source-item"
              >
                <div class="codex-daily-source-item-head">
                  <strong>{{ thread.title }}</strong>
                  <span>{{ formatDateTime(thread.start_at) }} ~ {{ formatDateTime(thread.end_at) }}</span>
                </div>
                <div class="codex-daily-source-item-meta">
                  <span v-if="isAllDevicesMode && thread.source_device_name">{{ thread.source_device_name }}</span>
                  <span>{{ formatProjectLabel(thread) || '未标记项目' }}</span>
                  <span>{{ thread.turn_count }} 轮</span>
                </div>
                <p v-if="thread.preview" class="codex-daily-source-item-preview">{{ thread.preview }}</p>
              </article>
            </section>
          </div>

          <div v-else-if="report && report.generated_by === 'empty'" class="codex-daily-placeholder">
            {{ report.date }} 没有可总结的聊天记录。
          </div>

          <div v-else-if="summaryRun.status === 'failed'" class="codex-daily-placeholder">
            这次生成失败了，可以直接点击“重新生成”再跑一遍。
          </div>

          <div v-else class="codex-daily-placeholder">
            正在生成中，结果完成后会自动写入数据库并显示在这里。
          </div>
        </template>

        <div v-else class="codex-daily-placeholder">
          先读取当天的已存结果；如果还没有，再点“生成总结”。
        </div>
      </section>
    </section>
  </div>
</template>

<style scoped>
.codex-daily-page {
  min-height: calc(100dvh - 60px);
}

.codex-daily-empty,
.codex-daily-shell {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.codex-daily-empty {
  align-items: center;
  justify-content: center;
  min-height: calc(100dvh - 108px);
  padding: 32px 20px;
  text-align: center;
  color: var(--el-text-color-regular);
}

.codex-daily-empty h2 {
  margin: 0;
  font-size: 24px;
  color: var(--el-text-color-primary);
}

.codex-daily-empty p {
  max-width: 520px;
  margin: 0;
  line-height: 1.6;
}

.codex-daily-empty-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 58px;
  height: 30px;
  padding: 0 14px;
  border-radius: 999px;
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
  font-size: 13px;
  font-weight: 600;
}

.codex-daily-toolbar,
.codex-daily-result {
  border: 1px solid var(--el-border-color-light);
  border-radius: 20px;
  background: var(--el-bg-color);
}

.codex-daily-toolbar {
  padding: 22px 24px;
}

.codex-daily-toolbar-head,
.codex-daily-result-head,
.codex-daily-progress-head,
.codex-daily-source-item-head {
  display: flex;
  justify-content: space-between;
  gap: 16px;
}

.codex-daily-toolbar-head h2,
.codex-daily-result-title h3,
.codex-daily-progress-head h3 {
  margin: 0;
  font-size: 16px;
  color: var(--el-text-color-primary);
}

.codex-daily-toolbar-head p,
.codex-daily-result-title p,
.codex-daily-progress-head p {
  margin: 6px 0 0;
  color: var(--el-text-color-regular);
  line-height: 1.6;
}

.codex-daily-toolbar-grid {
  display: grid;
  grid-template-columns: minmax(180px, 240px) minmax(240px, 1fr) minmax(180px, 220px) auto;
  gap: 14px;
  margin-top: 20px;
  align-items: end;
}

.codex-daily-field {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.codex-daily-field-label,
.codex-daily-field-label-row {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.codex-daily-field-control {
  width: 100%;
}

.codex-daily-help-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--el-color-primary);
  cursor: pointer;
}

.codex-daily-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  flex-wrap: wrap;
}

.codex-daily-stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.codex-daily-stat-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 18px 20px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 18px;
  background: linear-gradient(180deg, var(--el-fill-color-extra-light), var(--el-bg-color));
}

.codex-daily-stat-card span {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.codex-daily-stat-card strong {
  font-size: 24px;
  color: var(--el-text-color-primary);
  line-height: 1;
}

.codex-daily-result {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 20px;
}

.codex-daily-progress,
.codex-daily-summary-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 18px 20px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 18px;
  background: var(--el-fill-color-extra-light);
}

.codex-daily-progress-meta,
.codex-daily-result-meta,
.codex-daily-source-item-meta {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}

.codex-daily-progress-meta span,
.codex-daily-result-meta span,
.codex-daily-source-item-meta span,
.codex-daily-source-item-head span {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 5px 10px;
  border-radius: 999px;
  background: var(--el-bg-color);
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.codex-daily-stage-list {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.codex-daily-stage-item {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 999px;
  border: 1px solid var(--el-border-color);
  background: var(--el-bg-color);
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.codex-daily-stage-item.is-done {
  border-color: var(--el-color-success-light-5);
  color: var(--el-color-success);
}

.codex-daily-stage-item.is-current {
  border-color: var(--el-color-primary-light-5);
  color: var(--el-color-primary);
}

.codex-daily-stage-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: currentColor;
}

.codex-daily-summary-text {
  margin: 0;
  padding: 18px;
  border-radius: 16px;
  background: var(--el-bg-color);
  color: var(--el-text-color-primary);
  font: inherit;
  line-height: 1.8;
  white-space: pre-wrap;
  word-break: break-word;
}

.codex-daily-source-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.codex-daily-source-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  color: var(--el-text-color-secondary);
}

.codex-daily-source-item {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 16px 18px;
  border-radius: 16px;
  border: 1px solid var(--el-border-color-lighter);
  background: var(--el-bg-color);
}

.codex-daily-source-item strong {
  color: var(--el-text-color-primary);
}

.codex-daily-source-item-preview,
.codex-daily-placeholder {
  margin: 0;
  color: var(--el-text-color-regular);
  line-height: 1.7;
}

.codex-daily-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 180px;
  padding: 20px;
  border: 1px dashed var(--el-border-color);
  border-radius: 18px;
  background: var(--el-fill-color-extra-light);
  text-align: center;
}

@media (max-width: 1200px) {
  .codex-daily-toolbar-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .codex-daily-actions {
    justify-content: flex-start;
  }
}

@media (max-width: 760px) {
  .codex-daily-toolbar,
  .codex-daily-result {
    padding: 16px;
    border-radius: 16px;
  }

  .codex-daily-toolbar-grid,
  .codex-daily-stats {
    grid-template-columns: 1fr;
  }

  .codex-daily-toolbar-head,
  .codex-daily-result-head,
  .codex-daily-progress-head,
  .codex-daily-source-item-head {
    flex-direction: column;
  }

  .codex-daily-progress-meta,
  .codex-daily-result-meta,
  .codex-daily-source-item-meta {
    justify-content: flex-start;
  }
}
</style>
