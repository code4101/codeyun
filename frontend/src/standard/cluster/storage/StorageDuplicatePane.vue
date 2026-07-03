<template>
  <template v-if="hasDuplicateScope">
    <StorageDuplicateControls
      v-model:rule-fields="duplicateRuleFields"
      v-model:min-size-mb="duplicateMinSizeMb"
      v-model:sort-mode="duplicateSortMode"
      v-model:source="duplicateSource"
      v-model:filter-rules="duplicateFilterRules"
      :can-browse="canBrowse"
      :duplicate-loading="duplicateLoading"
      :enabled-filter-count="duplicateEnabledFilterCount"
      :page="duplicateListing?.page ?? 1"
      :page-size="duplicateListing?.page_size ?? 1"
      :page-count="duplicateTotalPages"
      @add-filter-rule="addDuplicateFilterRule"
      @analyze="analyzeDuplicates(1, false)"
      @page-change="loadDuplicatePage"
      @remove-filter-rule="removeDuplicateFilterRule"
      @reset-filter-rules="resetDuplicateFilterRules"
    />

    <section v-if="duplicateListing" class="storage-summary duplicate-summary">
      <span class="summary-path" :title="duplicateDisplayPath">
        <strong>范围</strong>
        <code>{{ duplicateDisplayPath }}</code>
      </span>
      <span><strong>重复组</strong>{{ duplicateListing.total_groups }}</span>
      <span><strong>重复文件</strong>{{ duplicateListing.duplicate_file_count }}</span>
      <span><strong>可释放</strong>{{ formatBytes(duplicateListing.total_reclaimable_bytes) }}</span>
      <span><strong>已扫描</strong>{{ duplicateListing.scanned_file_count }}</span>
      <span><strong>候选</strong>{{ duplicateListing.candidate_file_count }}</span>
      <span><strong>状态</strong>{{ formatDuplicateTaskStatus(duplicateListing) }}</span>
      <span><strong>来源</strong>{{ formatDuplicateSource(duplicateListing.source) }}</span>
      <span v-if="duplicateListing.hit_scan_limit" class="summary-warning">
        <strong>未完成</strong>已达到扫描上限
      </span>
    </section>

    <section class="storage-table-shell duplicate-table-shell">
      <div v-if="duplicateError" class="storage-error">
        {{ duplicateError }}
      </div>
      <div v-else-if="!duplicateListing" class="storage-empty">
        设置规则后开始分析。
      </div>
      <div v-else-if="!duplicateListing.groups.length" class="storage-empty">
        当前页没有重复文件组。
      </div>
      <table v-else class="storage-table duplicate-table" aria-label="重复文件组">
        <thead>
          <tr class="storage-table-head">
            <th class="storage-cell duplicate-cell-name" scope="col">组 / 文件</th>
            <th class="storage-cell duplicate-cell-size" scope="col">单文件</th>
            <th class="storage-cell duplicate-cell-total" scope="col">整组</th>
            <th class="storage-cell duplicate-cell-total" scope="col">可释放</th>
            <th class="storage-cell duplicate-cell-count" scope="col">数量</th>
            <th class="storage-cell duplicate-cell-time" scope="col">修改时间</th>
            <th class="storage-cell duplicate-cell-path" scope="col">路径</th>
            <th class="storage-cell storage-cell-spacer" scope="col" aria-hidden="true"></th>
          </tr>
        </thead>
        <tbody>
          <template v-for="group in duplicateListing.groups" :key="group.id">
            <tr class="storage-table-row duplicate-group-row">
              <td class="storage-cell duplicate-cell-name">
                <span class="duplicate-group-title" :title="group.key_label">
                  {{ group.key_label }}
                </span>
              </td>
              <td class="storage-cell duplicate-cell-size">{{ formatBytes(group.file_size) }}</td>
              <td class="storage-cell duplicate-cell-total">{{ formatBytes(group.group_total_bytes) }}</td>
              <td class="storage-cell duplicate-cell-total">{{ formatBytes(group.reclaimable_bytes) }}</td>
              <td class="storage-cell duplicate-cell-count">{{ group.file_count }}</td>
              <td class="storage-cell duplicate-cell-time">--</td>
              <td class="storage-cell duplicate-cell-path">--</td>
              <td class="storage-cell storage-cell-spacer" aria-hidden="true"></td>
            </tr>
            <tr
              v-for="file in group.files"
              :key="file.absolute_path"
              class="storage-table-row duplicate-file-row"
            >
              <td class="storage-cell duplicate-cell-name">
                <div class="entry-label duplicate-file-label">
                  <span class="entry-toggle"></span>
                  <span class="entry-name" :title="file.name">{{ file.name }}</span>
                </div>
              </td>
              <td class="storage-cell duplicate-cell-size">{{ formatBytes(file.size) }}</td>
              <td class="storage-cell duplicate-cell-total">--</td>
              <td class="storage-cell duplicate-cell-total">--</td>
              <td class="storage-cell duplicate-cell-count">1</td>
              <td class="storage-cell duplicate-cell-time">{{ formatTime(file.modified_at) }}</td>
              <td class="storage-cell duplicate-cell-path">
                <span :title="file.absolute_path || file.path">{{ file.absolute_path || file.path }}</span>
              </td>
              <td class="storage-cell storage-cell-spacer" aria-hidden="true"></td>
            </tr>
          </template>
        </tbody>
      </table>
    </section>
  </template>

  <section v-else class="storage-empty duplicate-prerequisite">
    请先进入具体磁盘或目录，再分析重复文件。
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';

import {
  fetchDeviceDuplicateAnalysis,
  startDeviceDuplicateAnalysis,
  type DeviceDuplicateAnalysis,
  type DeviceDuplicateFilterAction,
  type DeviceDuplicateFilterMatch,
  type DeviceDuplicateFilterRule,
  type DeviceDuplicateRule,
  type DeviceDuplicateSortMode,
  type DeviceDuplicateSource,
  type DeviceFileSelector,
} from '@/api/deviceFiles';
import { monitorPolledTask } from '@/utils/longTask';

import StorageDuplicateControls from './StorageDuplicateControls.vue';

const DEVICE_ROOT_SENTINEL = '__device_root__';
const DUPLICATE_SETTINGS_STORAGE_KEY = 'codeyun.storage.duplicateSettings.v1';
const DUPLICATE_PAGE_SIZE = 10;
const DEFAULT_DUPLICATE_FILTER_RULES: DeviceDuplicateFilterRule[] = [
  { enabled: true, action: 'exclude', match: 'contains', value: '$Recycle.Bin' },
  { enabled: true, action: 'exclude', match: 'contains', value: 'System Volume Information' },
];

interface DuplicateSettingsState {
  version: 1;
  rules: DeviceDuplicateRule[];
  filterRules: DeviceDuplicateFilterRule[];
  minSizeMb: number;
  sortMode: DeviceDuplicateSortMode;
  source: DeviceDuplicateSource;
}

const props = defineProps<{
  canBrowse: boolean;
  entryId: string;
  request: DeviceFileSelector | null;
}>();

const duplicateRuleFields = ref<DeviceDuplicateRule[]>(['size']);
const duplicateFilterRules = ref<DeviceDuplicateFilterRule[]>(cloneDefaultDuplicateFilterRules());
const duplicateMinSizeMb = ref(100);
const duplicateSortMode = ref<DeviceDuplicateSortMode>('reclaimable');
const duplicateSource = ref<DeviceDuplicateSource>('auto');
const duplicateListing = ref<DeviceDuplicateAnalysis | null>(null);
const duplicateLoading = ref(false);
const duplicateError = ref('');
let duplicateTaskPollVersion = 0;

const hasDuplicateScope = computed(() =>
  Boolean(props.request && props.request.absolute_path !== DEVICE_ROOT_SENTINEL)
);

const duplicateDisplayPath = computed(() => {
  if (duplicateListing.value?.absolute_path) {
    return duplicateListing.value.absolute_path;
  }
  if (duplicateListing.value?.path) {
    return duplicateListing.value.path;
  }
  if (!props.request) {
    return '未选择范围';
  }
  if (props.request.absolute_path === DEVICE_ROOT_SENTINEL) {
    return '请选择具体磁盘或目录';
  }
  return props.request.absolute_path || props.request.path || '根目录';
});

const duplicateTotalPages = computed(() => {
  const listing = duplicateListing.value;
  if (!listing) {
    return 1;
  }
  return Math.max(1, Math.ceil(listing.total_groups / Math.max(1, listing.page_size)));
});

const duplicateEnabledFilterCount = computed(() =>
  duplicateFilterRules.value.filter((rule) => rule.enabled && rule.value.trim()).length
);

function normalizeDuplicateRuleFields(value: unknown): DeviceDuplicateRule[] {
  const allowed = new Set<DeviceDuplicateRule>(['size', 'name', 'extension', 'modified_at', 'sha256']);
  const normalized: DeviceDuplicateRule[] = ['size'];
  if (Array.isArray(value)) {
    for (const item of value) {
      if (typeof item !== 'string' || !allowed.has(item as DeviceDuplicateRule)) {
        continue;
      }
      const rule = item as DeviceDuplicateRule;
      if (!normalized.includes(rule)) {
        normalized.push(rule);
      }
    }
  }
  return normalized;
}

function cloneDefaultDuplicateFilterRules(): DeviceDuplicateFilterRule[] {
  return DEFAULT_DUPLICATE_FILTER_RULES.map((rule) => ({ ...rule }));
}

function normalizeDuplicateFilterAction(value: unknown): DeviceDuplicateFilterAction {
  return value === 'include' || value === 'exclude' ? value : 'exclude';
}

function normalizeDuplicateFilterMatch(value: unknown): DeviceDuplicateFilterMatch {
  return value === 'contains' || value === 'prefix' || value === 'suffix' || value === 'equals' || value === 'glob'
    ? value
    : 'contains';
}

function normalizeDuplicateFilterRules(
  value: unknown,
  fallback: DeviceDuplicateFilterRule[] = cloneDefaultDuplicateFilterRules(),
): DeviceDuplicateFilterRule[] {
  if (!Array.isArray(value)) {
    return fallback.map((rule) => ({ ...rule }));
  }
  return value
    .map((item) => {
      const raw = item && typeof item === 'object' ? item as Record<string, unknown> : null;
      if (!raw) {
        return null;
      }
      const ruleValue = typeof raw.value === 'string' ? raw.value.trim() : '';
      if (!ruleValue) {
        return null;
      }
      return {
        enabled: raw.enabled !== false,
        action: normalizeDuplicateFilterAction(raw.action),
        match: normalizeDuplicateFilterMatch(raw.match),
        value: ruleValue,
      };
    })
    .filter((rule): rule is DeviceDuplicateFilterRule => rule !== null)
    .slice(0, 50);
}

function normalizeDuplicateSortMode(value: unknown): DeviceDuplicateSortMode {
  return value === 'file_size' || value === 'group_total' || value === 'reclaimable'
    ? value
    : 'reclaimable';
}

function normalizeDuplicateSource(value: unknown): DeviceDuplicateSource {
  return value === 'auto' || value === 'everything' || value === 'filesystem'
    ? value
    : 'auto';
}

function normalizeDuplicateMinSizeMb(value: unknown): number {
  const numericValue = Number(value);
  if (!Number.isFinite(numericValue) || numericValue < 0) {
    return 100;
  }
  return Math.min(1048576, Math.floor(numericValue));
}

function readDuplicateSettings(): DuplicateSettingsState | null {
  if (typeof window === 'undefined') {
    return null;
  }
  try {
    const raw = window.localStorage.getItem(DUPLICATE_SETTINGS_STORAGE_KEY);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as Partial<DuplicateSettingsState>;
    if (parsed.version !== 1) {
      return null;
    }
    return {
      version: 1,
      rules: normalizeDuplicateRuleFields(parsed.rules),
      filterRules: normalizeDuplicateFilterRules(parsed.filterRules),
      minSizeMb: normalizeDuplicateMinSizeMb(parsed.minSizeMb),
      sortMode: normalizeDuplicateSortMode(parsed.sortMode),
      source: normalizeDuplicateSource(parsed.source),
    };
  } catch {
    return null;
  }
}

function persistDuplicateSettings() {
  if (typeof window === 'undefined') {
    return;
  }
  const payload: DuplicateSettingsState = {
    version: 1,
    rules: normalizeDuplicateRuleFields(duplicateRuleFields.value),
    filterRules: normalizeDuplicateFilterRules(duplicateFilterRules.value, []),
    minSizeMb: normalizeDuplicateMinSizeMb(duplicateMinSizeMb.value),
    sortMode: duplicateSortMode.value,
    source: duplicateSource.value,
  };
  window.localStorage.setItem(DUPLICATE_SETTINGS_STORAGE_KEY, JSON.stringify(payload));
}

function addDuplicateFilterRule() {
  duplicateFilterRules.value.push({
    enabled: true,
    action: 'exclude',
    match: 'contains',
    value: '',
  });
}

function removeDuplicateFilterRule(index: number) {
  duplicateFilterRules.value.splice(index, 1);
}

function resetDuplicateFilterRules() {
  duplicateFilterRules.value = cloneDefaultDuplicateFilterRules();
}

function buildDuplicateRules(): DeviceDuplicateRule[] {
  return normalizeDuplicateRuleFields(duplicateRuleFields.value);
}

function stopDuplicateTaskPolling() {
  duplicateTaskPollVersion += 1;
}

function clearDuplicateListing() {
  stopDuplicateTaskPolling();
  duplicateListing.value = null;
  duplicateError.value = '';
  duplicateLoading.value = false;
}

function buildDuplicateRequest(page: number, reuseSnapshot: boolean) {
  if (!props.request || props.request.absolute_path === DEVICE_ROOT_SENTINEL) {
    throw new Error('请先进入具体磁盘或目录，再分析重复文件。');
  }
  return {
    ...props.request,
    recursive: true,
    rules: buildDuplicateRules(),
    filter_rules: normalizeDuplicateFilterRules(duplicateFilterRules.value, []),
    sort_mode: duplicateSortMode.value,
    source: duplicateSource.value,
    min_size: duplicateMinSizeMb.value * 1024 * 1024,
    scan_limit: 200000,
    page,
    page_size: DUPLICATE_PAGE_SIZE,
    snapshot_id: reuseSnapshot ? duplicateListing.value?.snapshot_id || '' : '',
  };
}

async function refreshDuplicateTask(taskId: string, page: number, showError = false) {
  if (!props.entryId || !taskId) {
    return;
  }
  try {
    const analysis = await fetchDeviceDuplicateAnalysis(props.entryId, taskId, {
      page,
      page_size: DUPLICATE_PAGE_SIZE,
    });
    duplicateListing.value = analysis;
    duplicateLoading.value = analysis.running;
    if (!analysis.running) {
      stopDuplicateTaskPolling();
      if (analysis.status === 'failed') {
        const detail = analysis.error || analysis.message || '重复文件分析失败';
        duplicateError.value = detail;
        if (showError) {
          throw new Error(detail);
        }
      }
    }
  } catch (error: any) {
    if (!showError) {
      return;
    }
    throw error;
  }
}

function startDuplicateTaskPolling(taskId: string) {
  const entryId = props.entryId;
  const initial = duplicateListing.value;
  if (!entryId || !initial?.running) {
    return;
  }
  const pollVersion = ++duplicateTaskPollVersion;
  void monitorPolledTask<DeviceDuplicateAnalysis>({
    initial,
    poll: async (task) => {
      if (pollVersion !== duplicateTaskPollVersion) {
        return { ...task, running: false };
      }
      const page = duplicateListing.value?.page ?? task.page ?? 1;
      return fetchDeviceDuplicateAnalysis(entryId, taskId, {
        page,
        page_size: DUPLICATE_PAGE_SIZE,
      });
    },
    isRunning: (task) => task.running && pollVersion === duplicateTaskPollVersion,
    getUpdatedAt: (task) => task.updated_at,
    getError: (task) => task.status === 'failed' ? (task.error || task.message || '重复文件分析失败') : '',
    pollIntervalMs: 1200,
    idleTimeoutMs: 30_000,
    onUpdate: (analysis) => {
      if (pollVersion !== duplicateTaskPollVersion) {
        return;
      }
      duplicateListing.value = analysis;
      duplicateLoading.value = analysis.running;
    },
  }).then((analysis) => {
    if (pollVersion !== duplicateTaskPollVersion) {
      return;
    }
    duplicateListing.value = analysis;
    duplicateLoading.value = false;
  }).catch((error: any) => {
    if (pollVersion !== duplicateTaskPollVersion) {
      return;
    }
    const detail = error?.message || '重复文件分析失败';
    duplicateError.value = detail;
    duplicateLoading.value = false;
  });
}

async function analyzeDuplicates(page = 1, reuseSnapshot = false) {
  if (!props.entryId) {
    return;
  }
  duplicateLoading.value = true;
  duplicateError.value = '';
  stopDuplicateTaskPolling();
  try {
    if (reuseSnapshot && duplicateListing.value?.task_id) {
      await refreshDuplicateTask(duplicateListing.value.task_id, page, true);
      return;
    }
    const payload = buildDuplicateRequest(page, false);
    const analysis = await startDeviceDuplicateAnalysis(props.entryId, payload);
    duplicateListing.value = analysis;
    duplicateLoading.value = analysis.running;
    if (analysis.running) {
      startDuplicateTaskPolling(analysis.task_id);
    }
  } catch (error: any) {
    const detail = error?.response?.data?.detail || error?.message || '重复文件分析失败';
    duplicateError.value = detail;
  } finally {
    if (!duplicateListing.value?.running) {
      duplicateLoading.value = false;
    }
  }
}

async function loadDuplicatePage(page: number) {
  const normalizedPage = Math.max(1, page);
  await analyzeDuplicates(normalizedPage, true);
}

function formatDuplicateSource(source: string): string {
  if (source === 'everything') {
    return 'Everything';
  }
  if (source === 'filesystem') {
    return '遍历';
  }
  return source || '--';
}

function formatDuplicateTaskStatus(task: DeviceDuplicateAnalysis): string {
  if (task.status === 'queued') {
    return '排队';
  }
  if (task.status === 'failed') {
    return '失败';
  }
  if (task.running) {
    return task.message || '分析中';
  }
  if (task.stage === 'cached') {
    return task.message || '缓存';
  }
  return '完成';
}

function formatBytes(value: number | null): string {
  if (value == null) {
    return '未统计';
  }
  if (value < 1024) {
    return `${value} B`;
  }
  const units = ['KB', 'MB', 'GB', 'TB', 'PB'];
  let current = value / 1024;
  let unitIndex = 0;
  while (current >= 1024 && unitIndex < units.length - 1) {
    current /= 1024;
    unitIndex += 1;
  }
  return `${current >= 100 ? current.toFixed(0) : current >= 10 ? current.toFixed(1) : current.toFixed(2)} ${units[unitIndex]}`;
}

function formatTime(value: number | null): string {
  if (value == null) {
    return '--';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return '--';
  }
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

watch([duplicateRuleFields, duplicateFilterRules, duplicateMinSizeMb, duplicateSortMode, duplicateSource], () => {
  if (!duplicateRuleFields.value.includes('size')) {
    duplicateRuleFields.value = normalizeDuplicateRuleFields(duplicateRuleFields.value);
  }
  clearDuplicateListing();
  persistDuplicateSettings();
}, { deep: true });

onMounted(() => {
  const duplicateSettings = readDuplicateSettings();
  if (!duplicateSettings) {
    return;
  }
  duplicateRuleFields.value = duplicateSettings.rules;
  duplicateFilterRules.value = duplicateSettings.filterRules;
  duplicateMinSizeMb.value = duplicateSettings.minSizeMb;
  duplicateSortMode.value = duplicateSettings.sortMode;
  duplicateSource.value = duplicateSettings.source;
});
</script>

<style scoped>
.storage-summary {
  min-height: 38px;
  padding: 0 16px;
  display: flex;
  align-items: center;
  gap: 18px;
  flex-wrap: wrap;
  color: #475569;
  font-size: 13px;
}

.storage-summary strong {
  margin-right: 6px;
  color: #334155;
}

.summary-path {
  min-width: 0;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.summary-path code {
  max-width: 52vw;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #0f766e;
  background: transparent;
}

.summary-warning {
  color: #b45309;
}

.storage-table-shell {
  flex: 1;
  min-height: 360px;
  overflow: auto;
  border: 1px solid #dfe5ee;
  background: #ffffff;
}

.storage-table {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  table-layout: auto;
}

.storage-table-row {
  height: 38px;
}

.storage-table-head {
  color: #475569;
  font-size: 12px;
  font-weight: 700;
}

.storage-table-head .storage-cell {
  position: sticky;
  top: 0;
  z-index: 1;
  background: #f1f5f9;
}

.storage-cell {
  min-width: 0;
  padding: 7px 10px;
  box-sizing: border-box;
  border-bottom: 1px solid #edf1f6;
  font-size: 13px;
  text-align: left;
  vertical-align: middle;
  white-space: nowrap;
}

.storage-cell-spacer {
  width: auto;
  min-width: 24px;
  padding: 0;
}

.duplicate-cell-name {
  width: 1%;
  min-width: 300px;
  max-width: 520px;
}

.duplicate-cell-size,
.duplicate-cell-total,
.duplicate-cell-count,
.duplicate-cell-time,
.duplicate-cell-path {
  width: 1%;
}

.duplicate-cell-size,
.duplicate-cell-total {
  min-width: 110px;
}

.duplicate-cell-count {
  min-width: 72px;
}

.duplicate-cell-time {
  min-width: 150px;
}

.duplicate-cell-path span {
  display: inline-block;
  max-width: 420px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #64748b;
}

.duplicate-group-row .storage-cell {
  background: #f8fafc;
  font-weight: 600;
}

.duplicate-group-title {
  display: inline-block;
  max-width: 520px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #334155;
}

.duplicate-file-label {
  padding-inline-start: 18px;
}

.entry-label {
  width: max-content;
  max-width: 360px;
  min-width: 0;
  box-sizing: border-box;
  overflow: hidden;
  color: #111827;
  font: inherit;
  display: flex;
  align-items: center;
  gap: 7px;
  text-align: left;
}

.entry-toggle {
  width: 18px;
  height: 18px;
  flex: 0 0 18px;
}

.entry-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.storage-empty,
.storage-error {
  min-height: 180px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #64748b;
  text-align: center;
}

.storage-error {
  color: #b42318;
}

@media (max-width: 980px) {
  .summary-path code {
    max-width: 78vw;
  }
}
</style>
