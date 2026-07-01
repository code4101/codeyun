<template>
  <div class="storage-page">
    <header class="storage-toolbar">
      <div class="storage-fields">
        <label class="storage-field storage-field-device">
          <span class="storage-field-label">设备</span>
          <el-select
            v-model="selectedEntryId"
            class="storage-select"
            placeholder="选择设备"
            :disabled="loadingDevices"
            @change="handleDeviceChange"
          >
            <el-option
              v-for="device in devices"
              :key="device.id"
              :label="device.name || device.device_id"
              :value="device.id"
            />
          </el-select>
        </label>

        <label class="storage-field storage-field-root">
          <span class="storage-field-label">范围</span>
          <el-select
            v-model="selectedRootKey"
            class="storage-select"
            placeholder="选择范围"
            :disabled="!selectedEntryId || loadingRoots"
            @change="handleRootChange"
          >
            <el-option
              v-for="root in rootOptions"
              :key="root.key"
              :label="root.label"
              :value="root.key"
            >
              <div class="root-option">
                <span>{{ root.label }}</span>
                <small>{{ root.path }}</small>
              </div>
            </el-option>
          </el-select>
        </label>

        <label class="storage-field storage-field-path">
          <span class="storage-field-label">路径</span>
          <el-input
            v-model="pathInput"
            clearable
            :placeholder="pathPlaceholder"
            :disabled="!selectedEntryId"
            @keyup.enter="loadFromInputs"
          />
        </label>
      </div>

      <div class="storage-actions">
        <el-button
          type="primary"
          :loading="loading"
          :disabled="!canBrowse"
          @click="loadFromInputs"
        >
          进入
        </el-button>
        <el-button
          :icon="Refresh"
          :loading="refreshing"
          :disabled="!canBrowse || !currentRequest"
          title="刷新当前目录"
          aria-label="刷新当前目录"
          @click="reloadCurrent"
        />
        <el-tooltip effect="light" placement="bottom-end">
          <template #content>
            <div class="storage-help">
              大小可切换包含总量或直接文件口径；条形图会跟随大小口径，并可单独切换参照和配色。
            </div>
          </template>
          <button type="button" class="storage-help-button" aria-label="TreeSize说明">
            <el-icon><QuestionFilled /></el-icon>
          </button>
        </el-tooltip>
      </div>
    </header>

    <section v-if="!devices.length && !loadingDevices" class="storage-empty">
      {{ noAvailableNodeLabel }}
    </section>

    <template v-else>
      <nav v-if="shouldShowDuplicates" class="storage-view-tabs" aria-label="TreeSize子页">
        <button
          type="button"
          :class="{ 'is-active': activeView === 'tree' }"
          @click="setActiveView('tree')"
        >
          目录树
        </button>
        <button
          type="button"
          :class="{ 'is-active': activeView === 'duplicates' }"
          @click="setActiveView('duplicates')"
        >
          重复文件
        </button>
      </nav>

      <template v-if="activeView === 'tree'">
      <section class="storage-summary">
        <span class="summary-path" :title="currentDisplayPath">
          <strong>当前位置</strong>
          <code>{{ currentDisplayPath }}</code>
        </span>
        <span><strong>当前层</strong>{{ rootNodes.length }} 项</span>
        <span><strong>已知大小</strong>{{ formatBytes(currentKnownBytes) }}</span>
        <span><strong>目录</strong>{{ rootDirectoryCount }}</span>
        <span><strong>文件</strong>{{ rootFileCount }}</span>
        <span v-if="unknownRootCount"><strong>未统计</strong>{{ unknownRootCount }}</span>
        <span
          v-if="activeDeleteTask && !isWechatMode"
          class="summary-delete-task"
          :class="`is-${activeDeleteTask.status}`"
          :title="activeDeleteTask.path"
        >
          <strong>删除任务</strong>{{ formatDeleteTask(activeDeleteTask) }}
        </span>
      </section>

      <section
        ref="tableShellRef"
        class="storage-table-shell"
        v-loading="loading && !visibleRows.length"
        @scroll="handleTableScroll"
      >
        <div v-if="loadError" class="storage-error">
          {{ loadError }}
        </div>

        <div v-else-if="!visibleRows.length && !loading" class="storage-empty">
          当前范围没有可展示的文件或目录。
        </div>

        <table
          v-else
          class="storage-table"
          :class="{ 'is-wechat-storage': isWechatMode }"
          aria-label="目录大小树"
        >
          <thead>
            <tr class="storage-table-head">
              <th class="storage-cell storage-cell-name" scope="col">名称</th>
              <th
                class="storage-cell storage-cell-size storage-configurable-head"
                scope="col"
                :title="`右键配置：${currentSizeValueModeOption.label}`"
                @contextmenu.prevent.stop="openStorageConfigMenu($event)"
              >
                大小
              </th>
              <th
                class="storage-cell storage-cell-percent storage-configurable-head"
                scope="col"
                :title="`右键配置：${currentSizeBarModeOption.label} / ${currentSizeBarColorModeOption.label}`"
                @contextmenu.prevent.stop="openStorageConfigMenu($event)"
              >
                {{ sizeBarColumnTitle }}
              </th>
              <th class="storage-cell storage-cell-count" scope="col">文件数/剩余</th>
              <th class="storage-cell storage-cell-time" scope="col">修改时间</th>
              <th class="storage-cell storage-cell-path" scope="col">{{ isWechatMode ? '说明' : '路径' }}</th>
              <th class="storage-cell storage-cell-spacer" scope="col" aria-hidden="true"></th>
            </tr>
          </thead>
          <tbody>
            <template v-for="row in visibleRows" :key="row.id">
              <tr
                v-if="row.kind === 'node'"
                class="storage-table-row"
                :class="{ 'is-context-target': shouldShowContextMenu && contextMenu.node?.id === row.node.id }"
                @contextmenu.prevent.stop="openNodeContextMenu(row.node, $event)"
              >
                <td class="storage-cell storage-cell-name">
                  <button
                    v-if="row.node.isDir"
                    type="button"
                    class="entry-button"
                    :style="getIndentStyle(row.node)"
                    :disabled="row.node.loading"
                    @click="toggleNode(row.node)"
                  >
                    <span class="entry-toggle">
                      <el-icon v-if="row.node.loading" class="is-loading"><Loading /></el-icon>
                      <span v-else>{{ row.node.expanded ? '-' : '+' }}</span>
                    </span>
                    <el-icon class="entry-icon entry-icon-dir"><FolderOpened /></el-icon>
                    <span class="entry-name" :title="row.node.name">{{ row.node.name }}</span>
                  </button>
                  <div
                    v-else
                    class="entry-label"
                    :style="getIndentStyle(row.node)"
                  >
                    <span class="entry-toggle"></span>
                    <el-icon class="entry-icon entry-icon-file"><Document /></el-icon>
                    <span class="entry-name" :title="row.node.name">{{ row.node.name }}</span>
                  </div>
                </td>

                <td class="storage-cell storage-cell-size">
                  {{ formatNodeSize(row.node) }}
                </td>

                <td class="storage-cell storage-cell-percent">
                  <div
                    class="usage-bar"
                    :class="{ 'is-unknown': getUsageDenominator(row.node) <= 0 || getNodeSize(row.node) == null }"
                    :style="getUsageLevelStyle(row.node)"
                  >
                    <span class="usage-fill" :style="{ width: getUsageWidth(row.node) }"></span>
                    <span class="usage-text">{{ formatSizeBarText(row.node) }}</span>
                  </div>
                </td>

                <td class="storage-cell storage-cell-count">
                  {{ formatFileCount(row.node) }}
                </td>

                <td class="storage-cell storage-cell-time">
                  {{ formatTime(row.node.modifiedAt) }}
                </td>

                <td class="storage-cell storage-cell-path">
                  <span
                    v-if="isWechatMode"
                    :title="describeWechatStorageNodeDetail(row.node)"
                  >{{ describeWechatStorageNodeBrief(row.node) }}</span>
                  <span v-else :title="row.node.path">{{ displayNodePath(row.node.path) }}</span>
                </td>

                <td class="storage-cell storage-cell-spacer" aria-hidden="true"></td>
              </tr>
              <tr
                v-else
                class="storage-table-row storage-more-row"
              >
                <td class="storage-cell storage-more-cell" colspan="7">
                  <button
                    type="button"
                    class="load-more-button"
                    :style="getMoreIndentStyle(row)"
                    @click="revealMore(row)"
                  >
                    <span class="entry-toggle more-toggle">...</span>
                    <span>再显示 {{ getNextVisibleCount(row) }} 项</span>
                    <small>已显示 {{ row.visibleCount }} / {{ row.totalCount }}</small>
                  </button>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </section>
      </template>

      <template v-else-if="shouldShowDuplicates">
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

        <section class="storage-summary duplicate-summary">
          <span class="summary-path" :title="duplicateDisplayPath">
            <strong>范围</strong>
            <code>{{ duplicateDisplayPath }}</code>
          </span>
          <span><strong>重复组</strong>{{ duplicateListing?.total_groups ?? 0 }}</span>
          <span><strong>重复文件</strong>{{ duplicateListing?.duplicate_file_count ?? 0 }}</span>
          <span><strong>可释放</strong>{{ formatBytes(duplicateListing?.total_reclaimable_bytes ?? 0) }}</span>
          <span><strong>已扫描</strong>{{ duplicateListing?.scanned_file_count ?? 0 }}</span>
          <span><strong>候选</strong>{{ duplicateListing?.candidate_file_count ?? 0 }}</span>
          <span v-if="duplicateListing"><strong>状态</strong>{{ formatDuplicateTaskStatus(duplicateListing) }}</span>
          <span v-if="duplicateListing"><strong>来源</strong>{{ formatDuplicateSource(duplicateListing.source) }}</span>
          <span v-if="duplicateListing?.hit_scan_limit" class="summary-warning">
            <strong>未完成</strong>已达到扫描上限
          </span>
        </section>

        <section
          class="storage-table-shell duplicate-table-shell"
        >
          <div v-if="duplicateError" class="storage-error">
            {{ duplicateError }}
          </div>
          <div v-else-if="!duplicateListing" class="storage-empty">
            设置范围和规则后开始分析。
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
                      <el-icon class="entry-icon entry-icon-file"><Document /></el-icon>
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
    </template>

    <teleport to="body">
      <div
        v-if="shouldShowContextMenu && contextMenu.visible"
        class="storage-context-menu"
        :style="{ left: `${contextMenu.x}px`, top: `${contextMenu.y}px` }"
        @click.stop
        @contextmenu.prevent
      >
        <button
          type="button"
          class="context-menu-item is-danger"
          :disabled="!contextMenu.node || deleteSubmittingNodeId === contextMenu.node.id || !canDeleteNode(contextMenu.node)"
          @click="contextMenu.node && confirmDeleteNode(contextMenu.node)"
        >
          <el-icon><Delete /></el-icon>
          <span>{{ contextMenu.node && canDeleteNode(contextMenu.node) ? '永久删除' : '根目录不可删除' }}</span>
        </button>
      </div>
      <div
        v-if="tableConfigMenu.visible"
        class="storage-context-menu storage-config-menu"
        :style="{ left: `${tableConfigMenu.x}px`, top: `${tableConfigMenu.y}px` }"
        @click.stop
        @contextmenu.prevent
      >
        <div class="storage-config-menu-section">
          <strong>大小</strong>
          <button
            v-for="option in sizeValueModeOptions"
            :key="option.value"
            type="button"
            class="context-menu-item"
            :class="{ 'is-active': sizeValueMode === option.value }"
            @click="chooseSizeValueMode(option.value)"
          >
            <span>{{ option.label }}</span>
          </button>
        </div>
        <div class="storage-config-menu-section">
          <strong>参照</strong>
          <button
            v-for="option in sizeBarModeOptions"
            :key="option.value"
            type="button"
            class="context-menu-item"
            :class="{ 'is-active': sizeBarMode === option.value }"
            @click="chooseSizeBarMode(option.value)"
          >
            <span>{{ option.label }}</span>
          </button>
        </div>
        <div class="storage-config-menu-section">
          <strong>颜色</strong>
          <button
            v-for="option in sizeBarColorModeOptions"
            :key="option.value"
            type="button"
            class="context-menu-item"
            :class="{ 'is-active': sizeBarColorMode === option.value }"
            @click="chooseSizeBarColorMode(option.value)"
          >
            <span>{{ option.label }}</span>
          </button>
        </div>
      </div>
    </teleport>
  </div>
</template>

<script setup lang="ts">
import { computed, defineAsyncComponent, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import {
  Delete,
  Document,
  FolderOpened,
  Loading,
  QuestionFilled,
  Refresh,
} from '@element-plus/icons-vue';
import { useRoute } from 'vue-router';

import {
  fetchDeviceEntryDeleteTask,
  fetchDeviceEntryDeleteTasks,
  fetchDeviceDirectoryItems,
  fetchDeviceDuplicateAnalysis,
  startDeviceDuplicateAnalysis,
  startDeviceEntryDelete,
  type DeviceDirectoryItem,
  type DeviceDirectoryListing,
  type DeviceDirectorySortProgram,
  type DeviceDeleteTask,
  type DeviceDuplicateFilterAction,
  type DeviceDuplicateFilterMatch,
  type DeviceDuplicateFilterRule,
  type DeviceDuplicateAnalysis,
  type DeviceDuplicateRule,
  type DeviceDuplicateSortMode,
  type DeviceDuplicateSource,
  type DeviceFileSelector,
} from '@/api/deviceFiles';
import {
  type WeChatStorageDirectoryItem,
  type WeChatStorageDirectoryListing,
  type WeChatStorageRoot,
} from '@/api/wechatArchive';
import { taskStore, type Device } from '@/store/taskStore';
import { monitorPolledTask } from '@/utils/longTask';

const StorageDuplicateControls = defineAsyncComponent(() => import('./StorageDuplicateControls.vue'));

type StorageSource = 'cluster' | 'wechat';
type WechatArchiveApi = typeof import('@/api/wechatArchive');

const props = defineProps<{ source?: StorageSource }>();

const route = useRoute();
const storageSource = computed<StorageSource>(() => {
  if (props.source === 'cluster' || props.source === 'wechat') {
    return props.source;
  }
  return route.path.includes('/notes/wechat-data/storage') ? 'wechat' : 'cluster';
});

const DEVICE_ROOT_SENTINEL = '__device_root__';
const SYSTEM_ROOT_KEY = '__system_root__';
const SIZE_BAR_MODE_STORAGE_KEY = 'codeyun.storage.sizeBarMode';
const SIZE_BAR_COLOR_MODE_STORAGE_KEY = 'codeyun.storage.sizeBarColorMode';
const SIZE_VALUE_MODE_STORAGE_KEY = 'codeyun.storage.sizeValueMode';
const STORAGE_VIEW_STORAGE_KEY = 'codeyun.storage.activeView';
const DUPLICATE_SETTINGS_STORAGE_KEY = 'codeyun.storage.duplicateSettings.v1';
const WORKSPACE_STATE_STORAGE_KEY = 'codeyun.storage.workspaceState.v1';
const WECHAT_WORKSPACE_STATE_STORAGE_KEY = 'codeyun.storage.wechat.workspaceState.v1';
const WECHAT_ACTIVE_VIEW_STORAGE_KEY = 'codeyun.storage.wechat.activeView';
const NODE_PAGE_SIZE = 100;
const MAX_VISIBLE_LIMIT = 100000;
const DUPLICATE_PAGE_SIZE = 10;
const DEFAULT_DUPLICATE_FILTER_RULES: DeviceDuplicateFilterRule[] = [
  { enabled: true, action: 'exclude', match: 'contains', value: '$Recycle.Bin' },
  { enabled: true, action: 'exclude', match: 'contains', value: 'System Volume Information' },
];
type SizeValueMode = 'total' | 'direct';
type SizeBarMode = 'siblingMax' | 'siblingTotal' | 'globalMax';
type SizeBarColorMode = 'depth' | 'uniform';
type StorageView = 'tree' | 'duplicates';

interface SizeValueModeOption {
  value: SizeValueMode;
  label: string;
}

interface SizeBarModeOption {
  value: SizeBarMode;
  label: string;
  columnTitle: string;
}

interface SizeBarColorModeOption {
  value: SizeBarColorMode;
  label: string;
}

interface RootOption {
  key: string;
  label: string;
  path: string;
  rootKey: string | null;
  absolutePath: string | null;
  system: boolean;
}

interface StorageNode {
  id: string;
  name: string;
  path: string;
  isDir: boolean;
  totalSizeBytes: number | null;
  directSizeBytes: number | null;
  diskTotalBytes: number | null;
  diskFreeBytes: number | null;
  recursiveFileCount: number | null;
  modifiedAt: number | null;
  totalSiblingTotalBytes: number;
  totalSiblingMaxBytes: number;
  totalSiblingUnknownCount: number;
  directSiblingTotalBytes: number;
  directSiblingMaxBytes: number;
  directSiblingUnknownCount: number;
  depth: number;
  request: DeviceFileSelector;
  children: StorageNode[];
  childrenLoaded: boolean;
  expanded: boolean;
  loading: boolean;
  loadError: string;
  visibleChildLimit: number;
}

interface StorageNodeRow {
  kind: 'node';
  id: string;
  node: StorageNode;
}

interface StorageMoreRow {
  kind: 'more';
  id: string;
  depth: number;
  visibleCount: number;
  totalCount: number;
  remainingCount: number;
  parent: StorageNode | null;
}

type StorageVisibleRow = StorageNodeRow | StorageMoreRow;

interface ContextMenuState {
  visible: boolean;
  x: number;
  y: number;
  node: StorageNode | null;
}

interface TableConfigMenuState {
  visible: boolean;
  x: number;
  y: number;
}

interface ActiveDeleteTask {
  id: string;
  status: DeviceDeleteTask['status'];
  name: string;
  path: string;
  pid: number | null;
  updatedAt: number | null;
  errorMessage: string | null;
}

interface StorageWorkspaceState {
  version: 1;
  selectedEntryId: string;
  selectedRootKey: string;
  currentRequest: DeviceFileSelector | null;
  expandedKeys: string[];
  visibleLimitByKey: Record<string, number>;
  rootVisibleLimit: number;
  scrollTop: number;
  scrollLeft: number;
  updatedAt: number;
}

interface WeChatSourceEntry {
  id: string;
  device_id: string;
  name: string;
}

interface DuplicateSettingsState {
  version: 1;
  rules: DeviceDuplicateRule[];
  filterRules: DeviceDuplicateFilterRule[];
  minSizeMb: number;
  sortMode: DeviceDuplicateSortMode;
  source: DeviceDuplicateSource;
}

interface LoadRootOptions {
  workspaceState?: StorageWorkspaceState | null;
  restoreExpanded?: boolean;
  prefetchedListing?: DeviceDirectoryListing | null;
}

interface LoadChildrenOptions {
  workspaceState?: StorageWorkspaceState | null;
  restoring?: boolean;
  silent?: boolean;
}

const DIRECTORY_SORT_PROGRAM: DeviceDirectorySortProgram = {
  rules: [
    {
      field: 'recursive_total_bytes',
      direction: 'desc',
      nulls: 'last',
    },
  ],
};

const isWechatMode = computed(() => storageSource.value === 'wechat');
const wechatRoots = ref<WeChatStorageRoot[]>([]);
let wechatArchiveApiPromise: Promise<WechatArchiveApi> | null = null;
const devices = computed<(Device | WeChatSourceEntry)[]>(() => isWechatMode.value
  ? wechatRoots.value.map((root) => ({
    id: root.device_id,
    device_id: root.device_id,
    name: `${root.label}${root.current ? '（当前）' : ''}`,
  }))
  : taskStore.devices
);
const selectedEntryId = ref('');
const diskRootOptions = ref<RootOption[]>([]);
const selectedRootKey = ref(SYSTEM_ROOT_KEY);
const pathInput = ref('');
const rootNodes = ref<StorageNode[]>([]);
const currentListing = ref<DeviceDirectoryListing | null>(null);
const currentRequest = ref<DeviceFileSelector | null>(null);
const activeView = ref<StorageView>('tree');
const sizeValueMode = ref<SizeValueMode>('total');
const sizeBarMode = ref<SizeBarMode>('siblingMax');
const sizeBarColorMode = ref<SizeBarColorMode>('depth');
const duplicateRuleFields = ref<DeviceDuplicateRule[]>(['size']);
const duplicateFilterRules = ref<DeviceDuplicateFilterRule[]>(DEFAULT_DUPLICATE_FILTER_RULES.map((rule) => ({ ...rule })));
const duplicateMinSizeMb = ref(100);
const duplicateSortMode = ref<DeviceDuplicateSortMode>('reclaimable');
const duplicateSource = ref<DeviceDuplicateSource>('auto');
const duplicateListing = ref<DeviceDuplicateAnalysis | null>(null);
const rootVisibleLimit = ref(NODE_PAGE_SIZE);
const loadingDevices = ref(false);
const loadingRoots = ref(false);
const loading = ref(false);
const refreshing = ref(false);
const duplicateLoading = ref(false);
const loadError = ref('');
const duplicateError = ref('');
const deleteSubmittingNodeId = ref('');
const activeDeleteTask = ref<ActiveDeleteTask | null>(null);
const tableShellRef = ref<HTMLElement | null>(null);
const contextMenu = ref<ContextMenuState>({
  visible: false,
  x: 0,
  y: 0,
  node: null,
});
const tableConfigMenu = ref<TableConfigMenuState>({
  visible: false,
  x: 0,
  y: 0,
});
let nodeSeq = 0;
let deleteTaskPollVersion = 0;
let duplicateTaskPollVersion = 0;
let workspaceStateReady = false;
let workspacePersistTimer: number | null = null;
let lastTableScrollTop = 0;
let lastTableScrollLeft = 0;
let rootLoadVersion = 0;

const rootOptions = computed<RootOption[]>(() => [
  ...(isWechatMode.value
    ? wechatRoots.value.map((root) => ({
      key: root.device_id,
      label: root.label || root.device_id,
      path: root.device_root,
      rootKey: root.device_id,
      absolutePath: root.device_root,
      system: false,
    }))
    : [
      {
        key: SYSTEM_ROOT_KEY,
        label: '设备根目录',
        path: '列出磁盘或系统根',
        rootKey: null,
        absolutePath: null,
        system: true,
      },
      ...diskRootOptions.value,
    ]),
]);

const selectedRoot = computed(() =>
  rootOptions.value.find((root) => root.key === selectedRootKey.value) ?? rootOptions.value[0]
);

const canBrowse = computed(() => Boolean(selectedEntryId.value && selectedRoot.value));

const pathPlaceholder = computed(() =>
  selectedRoot.value?.system
    ? '留空列出磁盘，也可输入绝对路径'
    : isWechatMode.value
      ? '留空表示当前范围根，也可输入微信相关绝对路径'
      : '留空表示磁盘根目录，或输入相对路径'
);

const shouldShowDuplicates = computed(() => !isWechatMode.value);
const shouldShowContextMenu = computed(() => !isWechatMode.value);
const noAvailableNodeLabel = computed(() => (isWechatMode.value ? '当前没有可用微信目录' : '当前没有可用设备，请先在运行管理里添加本机或远程设备。'));

const currentDisplayPath = computed(() => {
  if (currentListing.value?.absolute_path) {
    return currentListing.value.absolute_path === DEVICE_ROOT_SENTINEL
      ? '设备根目录'
      : currentListing.value.absolute_path;
  }
  if (!currentRequest.value) {
    return '未加载';
  }
  if (currentRequest.value.absolute_path === DEVICE_ROOT_SENTINEL) {
    return '设备根目录';
  }
  return currentRequest.value.absolute_path || currentRequest.value.path || '根目录';
});

const duplicateDisplayPath = computed(() => {
  if (duplicateListing.value?.absolute_path) {
    return duplicateListing.value.absolute_path;
  }
  if (duplicateListing.value?.path) {
    return duplicateListing.value.path;
  }
  const request = currentRequest.value ?? buildInputRequest();
  if (request.absolute_path === DEVICE_ROOT_SENTINEL) {
    return '请选择具体磁盘或目录';
  }
  return request.absolute_path || request.path || '根目录';
});

const visibleRows = computed<StorageVisibleRow[]>(() => {
  const rows: StorageVisibleRow[] = [];
  appendVisibleGroup(rows, rootNodes.value, null);
  return rows;
});

const rootDirectoryCount = computed(() => rootNodes.value.filter((node) => node.isDir).length);
const rootFileCount = computed(() => rootNodes.value.filter((node) => !node.isDir).length);
const unknownRootCount = computed(() => rootNodes.value.filter((node) => getNodeSize(node) == null).length);
const currentKnownBytes = computed(() =>
  rootNodes.value.reduce((total, node) => total + (getNodeSize(node) ?? 0), 0)
);
const globalReferenceBytes = computed(() =>
  rootNodes.value.reduce((max, node) => Math.max(max, getNodeSize(node) ?? 0), 0)
);
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
const sizeValueModeOptions: SizeValueModeOption[] = [
  { value: 'total', label: '包含总量' },
  { value: 'direct', label: '直接文件' },
];
const sizeBarModeOptions: SizeBarModeOption[] = [
  { value: 'siblingMax', label: '同层最大', columnTitle: '同层参照' },
  { value: 'siblingTotal', label: '同层总量', columnTitle: '同层占比' },
  { value: 'globalMax', label: '全局最大', columnTitle: '全局参照' },
];
const sizeBarColorModeOptions: SizeBarColorModeOption[] = [
  { value: 'depth', label: '层级色' },
  { value: 'uniform', label: '统一色' },
];
const currentSizeValueModeOption = computed(() =>
  sizeValueModeOptions.find((option) => option.value === sizeValueMode.value) ?? sizeValueModeOptions[0]
);
const currentSizeBarModeOption = computed(() =>
  sizeBarModeOptions.find((option) => option.value === sizeBarMode.value) ?? sizeBarModeOptions[0]
);
const currentSizeBarColorModeOption = computed(() =>
  sizeBarColorModeOptions.find((option) => option.value === sizeBarColorMode.value) ?? sizeBarColorModeOptions[0]
);
const workspaceStateStorageKey = computed(() =>
  isWechatMode.value ? WECHAT_WORKSPACE_STATE_STORAGE_KEY : WORKSPACE_STATE_STORAGE_KEY
);
const sizeBarColumnTitle = computed(() => currentSizeBarModeOption.value.columnTitle);
const activeViewStorageKey = computed(() =>
  isWechatMode.value ? WECHAT_ACTIVE_VIEW_STORAGE_KEY : STORAGE_VIEW_STORAGE_KEY
);

function normalizeMaybeNumber(value: unknown): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return null;
  }
  return value;
}

function normalizeVisibleLimit(value: unknown): number {
  const numericValue = Number(value);
  if (!Number.isFinite(numericValue) || numericValue <= NODE_PAGE_SIZE) {
    return NODE_PAGE_SIZE;
  }
  return Math.min(MAX_VISIBLE_LIMIT, Math.max(NODE_PAGE_SIZE, Math.floor(numericValue)));
}

function normalizeDeviceFileSelector(value: unknown): DeviceFileSelector | null {
  if (!value || typeof value !== 'object') {
    return null;
  }
  const raw = value as Record<string, unknown>;
  const absolutePath = typeof raw.absolute_path === 'string' ? raw.absolute_path : '';
  if (absolutePath) {
    return { absolute_path: absolutePath };
  }

  const root = typeof raw.root === 'string' ? raw.root : '';
  const path = typeof raw.path === 'string' ? raw.path : '';
  if (root) {
    return { root, path };
  }
  return null;
}

function normalizeStorageView(value: string | null): StorageView | null {
  return value === 'tree' || value === 'duplicates' ? value : null;
}

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

function getRequestStateKey(request: DeviceFileSelector | null | undefined): string {
  if (!request) {
    return '';
  }
  if (request.absolute_path) {
    return `abs:${request.absolute_path}`;
  }
  return `root:${request.root ?? ''}|path:${request.path ?? ''}`;
}

function getNodeStateKey(node: StorageNode): string {
  return getRequestStateKey(node.request);
}

function readWorkspaceState(): StorageWorkspaceState | null {
  if (typeof window === 'undefined') {
    return null;
  }
  try {
    const raw = window.localStorage.getItem(workspaceStateStorageKey.value);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as Partial<StorageWorkspaceState>;
    if (parsed.version !== 1) {
      return null;
    }
    const selectedEntryId = typeof parsed.selectedEntryId === 'string' ? parsed.selectedEntryId : '';
    const selectedRootKey = typeof parsed.selectedRootKey === 'string' ? parsed.selectedRootKey : SYSTEM_ROOT_KEY;
    const currentRequest = normalizeDeviceFileSelector(parsed.currentRequest);
    const expandedKeys = Array.isArray(parsed.expandedKeys)
      ? parsed.expandedKeys.filter((key): key is string => typeof key === 'string' && Boolean(key))
      : [];
    const visibleLimitByKey: Record<string, number> = {};
    if (parsed.visibleLimitByKey && typeof parsed.visibleLimitByKey === 'object') {
      for (const [key, value] of Object.entries(parsed.visibleLimitByKey)) {
        if (typeof key === 'string' && key) {
          visibleLimitByKey[key] = normalizeVisibleLimit(value);
        }
      }
    }
    return {
      version: 1,
      selectedEntryId,
      selectedRootKey,
      currentRequest,
      expandedKeys,
      visibleLimitByKey,
      rootVisibleLimit: normalizeVisibleLimit(parsed.rootVisibleLimit),
      scrollTop: Math.max(0, Number(parsed.scrollTop) || 0),
      scrollLeft: Math.max(0, Number(parsed.scrollLeft) || 0),
      updatedAt: Number(parsed.updatedAt) || 0,
    };
  } catch {
    return null;
  }
}

function collectWorkspaceTreeState(
  nodes: StorageNode[],
  expandedKeys: Set<string>,
  visibleLimitByKey: Record<string, number>,
) {
  for (const node of nodes) {
    const key = getNodeStateKey(node);
    if (node.expanded && key) {
      expandedKeys.add(key);
    }
    if (node.visibleChildLimit > NODE_PAGE_SIZE && key) {
      visibleLimitByKey[key] = normalizeVisibleLimit(node.visibleChildLimit);
    }
    if (node.children.length) {
      collectWorkspaceTreeState(node.children, expandedKeys, visibleLimitByKey);
    }
  }
}

function buildWorkspaceState(): StorageWorkspaceState {
  const expandedKeys = new Set<string>();
  const visibleLimitByKey: Record<string, number> = {};
  collectWorkspaceTreeState(rootNodes.value, expandedKeys, visibleLimitByKey);
  return {
    version: 1,
    selectedEntryId: selectedEntryId.value,
    selectedRootKey: selectedRootKey.value,
    currentRequest: currentRequest.value ? { ...currentRequest.value } : null,
    expandedKeys: [...expandedKeys],
    visibleLimitByKey,
    rootVisibleLimit: normalizeVisibleLimit(rootVisibleLimit.value),
    scrollTop: lastTableScrollTop,
    scrollLeft: lastTableScrollLeft,
    updatedAt: Date.now(),
  };
}

function persistWorkspaceState() {
  if (!workspaceStateReady || typeof window === 'undefined') {
    return;
  }
  try {
    window.localStorage.setItem(workspaceStateStorageKey.value, JSON.stringify(buildWorkspaceState()));
  } catch {
    // localStorage quota or privacy errors should not break browsing.
  }
}

function schedulePersistWorkspaceState() {
  if (!workspaceStateReady || typeof window === 'undefined' || workspacePersistTimer != null) {
    return;
  }
  workspacePersistTimer = window.setTimeout(() => {
    workspacePersistTimer = null;
    persistWorkspaceState();
  }, 180);
}

function stopWorkspacePersistTimer() {
  if (workspacePersistTimer != null && typeof window !== 'undefined') {
    window.clearTimeout(workspacePersistTimer);
  }
  workspacePersistTimer = null;
}

function handleTableScroll(event: Event) {
  const target = event.currentTarget as HTMLElement | null;
  if (!target) {
    return;
  }
  lastTableScrollTop = target.scrollTop;
  lastTableScrollLeft = target.scrollLeft;
  schedulePersistWorkspaceState();
}

async function restoreTableScroll(state: StorageWorkspaceState | null | undefined) {
  if (!state) {
    return;
  }
  lastTableScrollTop = Math.max(0, state.scrollTop || 0);
  lastTableScrollLeft = Math.max(0, state.scrollLeft || 0);
  await nextTick();
  if (tableShellRef.value) {
    tableShellRef.value.scrollTop = lastTableScrollTop;
    tableShellRef.value.scrollLeft = lastTableScrollLeft;
  }
}

async function resetTableScroll() {
  lastTableScrollTop = 0;
  lastTableScrollLeft = 0;
  await nextTick();
  if (tableShellRef.value) {
    tableShellRef.value.scrollTop = 0;
    tableShellRef.value.scrollLeft = 0;
  }
}

function getSavedVisibleLimit(
  state: StorageWorkspaceState | null | undefined,
  key: string,
): number {
  if (!state || !key) {
    return NODE_PAGE_SIZE;
  }
  return normalizeVisibleLimit(state.visibleLimitByKey[key]);
}

function requestMatchesAvailableScope(request: DeviceFileSelector | null): boolean {
  if (!request) {
    return false;
  }
  if (isWechatMode.value) {
    const wechatRoot = selectedRoot.value?.absolutePath || '';
    if (request.absolute_path) {
      return isPathWithinScope(request.absolute_path, wechatRoot);
    }
    if (request.root) {
      return request.root === selectedEntryId.value;
    }
    return false;
  }
  if (request.absolute_path) {
    return true;
  }
  return Boolean(request.root && rootOptions.value.some((root) => root.rootKey === request.root));
}

function isAbsolutePathInput(path: string): boolean {
  const normalized = path.trim();
  return /^[a-zA-Z]:[\\/]/.test(normalized)
    || normalized.startsWith('\\\\')
    || normalized.startsWith('/')
    || normalized.startsWith('\\');
}

function normalizePathForScopeCompare(path: string): string {
  return path
    .trim()
    .replace(/\\/g, '/')
    .replace(/\/+$/g, '')
    .toLowerCase();
}

function isPathWithinScope(path: string, scopePath: string): boolean {
  const normalizedPath = normalizePathForScopeCompare(path);
  const normalizedScope = normalizePathForScopeCompare(scopePath);
  if (!normalizedPath || !normalizedScope) {
    return false;
  }
  if (normalizedScope === '/') {
    return true;
  }
  if (normalizedPath === normalizedScope) {
    return true;
  }
  return normalizedPath.startsWith(`${normalizedScope}/`);
}

function normalizePathForCompare(path: string): string {
  const trimmed = path.trim();
  if (trimmed === '/' || trimmed === '\\') {
    return trimmed;
  }
  const normalized = trimmed.replace(/\//g, '\\').replace(/[\\]+$/g, '');
  return normalized.toLowerCase();
}

function isDiskRootPath(path: string): boolean {
  const normalized = path.trim();
  return /^[a-zA-Z]:[\\/]?$/.test(normalized) || normalized === '/' || normalized === '\\';
}

function buildDiskRootKey(path: string): string {
  return `disk:${normalizePathForCompare(path) || path.trim()}`;
}

function joinRootPath(rootPath: string, rawPath: string): string {
  const normalizedRoot = rootPath.trim();
  const normalizedPath = rawPath.trim();
  if (!normalizedPath) {
    return normalizedRoot;
  }
  if (isAbsolutePathInput(normalizedPath)) {
    return normalizedPath;
  }
  const separator = normalizedRoot.includes('\\') ? '\\' : '/';
  const relativePath = normalizedPath.replace(/^[\\/]+/, '');
  if (normalizedRoot === '/' || normalizedRoot === '\\') {
    return `${separator}${relativePath}`;
  }
  return `${normalizedRoot.replace(/[\\/]+$/, '')}${separator}${relativePath}`;
}

function getPathRelativeToRoot(rootPath: string, absolutePath: string): string | null {
  const normalizedRoot = normalizePathForCompare(rootPath);
  const normalizedAbsolute = normalizePathForCompare(absolutePath);
  if (!normalizedRoot || !normalizedAbsolute) {
    return null;
  }
  if (normalizedRoot === '/' || normalizedRoot === '\\') {
    if (normalizedRoot === normalizedAbsolute) {
      return '';
    }
    return absolutePath.replace(/^[\\/]/, '');
  }
  if (normalizedRoot === normalizedAbsolute) {
    return '';
  }
  const separator = rootPath.includes('/') && !rootPath.includes('\\') ? '/' : '\\';
  const prefix = `${normalizedRoot}\\`;
  if (!normalizedAbsolute.startsWith(prefix)) {
    return null;
  }
  return absolutePath.slice(rootPath.replace(/[\\/]+$/, '').length).replace(/^[\\/]+/, '').replace(/[\\/]/g, separator);
}

function displayNodePath(path: string): string {
  if (!isWechatMode.value) {
    return path;
  }
  const rootPath = selectedRoot.value?.absolutePath;
  if (!rootPath) {
    return path;
  }
  const relative = getPathRelativeToRoot(rootPath, path);
  if (relative == null) {
    return path;
  }
  if (!relative) {
    return '/';
  }
  return relative.replace(/[\\/]/g, '/');
}

function getWechatNodeRelativePath(node: StorageNode): string {
  const relative = displayNodePath(node.path);
  return relative === '/' ? '' : relative.replace(/\\/g, '/');
}

function getWechatStorageNodeDescription(node: StorageNode): { brief: string; detail: string } {
  const name = node.name.toLowerCase();
  const relativePath = getWechatNodeRelativePath(node).toLowerCase();
  const segments = relativePath.split('/').filter(Boolean);
  const accountIndex = segments.findIndex((segment) => segment.startsWith('wxid_'));
  const accountSubdir = accountIndex >= 0 ? segments[accountIndex + 1] || '' : '';

  if (name.startsWith('wxid_')) return { brief: '账号数据', detail: '微信账号数据目录，包含该账号的消息、缓存、资源和本地配置。' };
  if (name === 'all_users' || name === 'all users') return { brief: '全局共享', detail: '全局用户共享数据，通常是登录状态、公共配置和跨账号缓存。' };
  if (name === 'backup') return { brief: '聊天备份', detail: '微信备份目录，用于聊天记录迁移或备份恢复产生的数据。' };
  if (name === 'msg') return { brief: '聊天消息', detail: '聊天消息主体数据，通常包含会话消息库、索引和消息相关资源，是最核心的占用来源。' };
  if (name === 'cache') return { brief: '运行缓存', detail: '运行缓存和临时下载内容，可用于加速加载，通常不是原始聊天数据库主体。' };
  if (name === 'db_storage') return { brief: '数据库', detail: '微信 4.x 本地数据库存储区，包含加密/分片后的底层数据库文件。' };
  if (name === 'temp' || name === 'tmp') return { brief: '临时文件', detail: '临时文件目录，常见于下载、预览、发送或转码过程中的中间文件。' };
  if (name === 'business') return { brief: '业务插件', detail: '微信业务插件和功能模块数据，例如小程序、公众号或业务场景缓存。' };
  if (name === 'resource' || name === 'resources') return { brief: '资源素材', detail: '资源文件目录，常见图片、表情、预览素材或界面资源缓存。' };
  if (name === 'config') return { brief: '本地配置', detail: '本地配置目录，保存账号、客户端或功能模块的配置项。' };
  if (name === 'apm_record') return { brief: '诊断记录', detail: '性能与诊断记录目录，通常用于客户端监控、崩溃或性能分析。' };
  if (name === 'decrypted') return { brief: '解密结果', detail: '逆向流程生成的解密数据目录，通常包含已解密数据库和导出资源。' };
  if (name === 'raw_snapshot') return { brief: '原始快照', detail: '逆向前保存的原始快照，用于保留官方微信目录或数据库的现场副本。' };
  if (name === 'reports') return { brief: '分析报告', detail: '逆向分析报告和中间结论目录。' };
  if (name === 'scripts' || name === 'work') return { brief: '工程文件', detail: '逆向工程脚本或临时工作目录。' };
  if (name === 'secrets') return { brief: '敏感信息', detail: '密钥、参数或敏感中间信息目录，应谨慎查看和同步。' };
  if (name === 'wechat-ilink') return { brief: '接入桥接', detail: 'CodeYun 微信接入数据，通常保存接入账号、桥接配置和运行状态。' };
  if (name.endsWith('.db') || name.endsWith('.sqlite') || name.endsWith('.sqlite3')) return { brief: '数据库文件', detail: 'SQLite 数据库文件，可进一步解析表结构和业务字段。' };
  if (name.endsWith('.db-wal') || name.endsWith('.db-shm')) return { brief: '数据库日志', detail: 'SQLite 运行辅助文件，和对应数据库一起表示最近写入状态。' };
  if (accountSubdir === 'msg') return { brief: '消息子项', detail: '某个账号下的消息相关子目录，包含聊天记录数据库、索引或消息资源。' };
  if (accountSubdir === 'db_storage') return { brief: '库文件子项', detail: '某个账号下的数据库存储子目录，可重点关注数据库分片和消息表。' };
  if (node.isDir) return { brief: '微信目录', detail: '微信相关子目录，建议展开后结合文件类型和体积继续判断用途。' };
  return { brief: '微信文件', detail: '微信相关文件，可结合扩展名、大小和所在目录进一步判断。' };
}

function describeWechatStorageNodeBrief(node: StorageNode): string {
  return getWechatStorageNodeDescription(node).brief;
}

function describeWechatStorageNodeDetail(node: StorageNode): string {
  return getWechatStorageNodeDescription(node).detail;
}

function buildDiskRootOptions(listing: DeviceDirectoryListing): RootOption[] {
  const seen = new Set<string>();
  const options: RootOption[] = [];
  for (const item of listing.items) {
    const path = item.path.trim();
    if (!item.is_dir || !path || !isDiskRootPath(path)) {
      continue;
    }
    const key = buildDiskRootKey(path);
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    options.push({
      key,
      label: item.name || path.replace(/[\\/]$/, ''),
      path,
      rootKey: null,
      absolutePath: path,
      system: false,
    });
  }
  return options.sort((left, right) => left.label.localeCompare(right.label, 'zh-CN'));
}

function getItemTotalSize(item: DeviceDirectoryItem): number | null {
  if (item.disk_used_bytes != null) {
    return normalizeMaybeNumber(item.disk_used_bytes);
  }
  if (item.is_dir) {
    return normalizeMaybeNumber(item.recursive_total_bytes);
  }
  return normalizeMaybeNumber(item.size);
}

function getItemDirectSize(item: DeviceDirectoryItem): number | null {
  if (!item.is_dir) {
    return normalizeMaybeNumber(item.size);
  }
  return normalizeMaybeNumber(item.direct_file_bytes);
}

function getItemDiskTotalBytes(item: DeviceDirectoryItem): number | null {
  return normalizeMaybeNumber(item.disk_total_bytes);
}

function getItemDiskFreeBytes(item: DeviceDirectoryItem): number | null {
  return normalizeMaybeNumber(item.disk_free_bytes);
}

function getItemModifiedAt(item: DeviceDirectoryItem): number | null {
  if (item.is_dir) {
    return normalizeMaybeNumber(item.latest_descendant_modified_at ?? item.modified_at);
  }
  return normalizeMaybeNumber(item.modified_at);
}

function compareItems(left: DeviceDirectoryItem, right: DeviceDirectoryItem): number {
  const leftSize = getItemTotalSize(left);
  const rightSize = getItemTotalSize(right);
  if (leftSize != null || rightSize != null) {
    if (leftSize == null) return 1;
    if (rightSize == null) return -1;
    if (leftSize !== rightSize) return rightSize - leftSize;
  }
  if (left.is_dir !== right.is_dir) {
    return left.is_dir ? -1 : 1;
  }
  return left.name.localeCompare(right.name, 'zh-CN');
}

function normalizeWechatDirectoryItem(_item: WeChatStorageDirectoryItem): DeviceDirectoryItem {
  return {
    name: _item.name,
    path: _item.path,
    is_dir: _item.is_dir,
    size: _item.size,
    modified_at: _item.modified_at,
    direct_file_bytes: _item.direct_file_bytes,
    direct_file_count: _item.direct_file_count,
    recursive_total_bytes: _item.recursive_total_bytes,
    recursive_file_count: _item.recursive_file_count,
    latest_descendant_modified_at: _item.latest_descendant_modified_at,
    max_weight: _item.max_weight,
    weighted_file_count: _item.weighted_file_count,
    disk_total_bytes: _item.disk_total_bytes,
    disk_free_bytes: _item.disk_free_bytes,
    disk_used_bytes: _item.disk_used_bytes,
  };
}

function normalizeWechatDirectoryListing(listing: WeChatStorageDirectoryListing): DeviceDirectoryListing {
  return {
    root: null,
    current_path: listing.current_path,
    absolute_path: listing.absolute_path,
    items: listing.items.map((item) => normalizeWechatDirectoryItem(item)),
  };
}

function loadWechatArchiveApi(): Promise<WechatArchiveApi> {
  wechatArchiveApiPromise ||= import('@/api/wechatArchive');
  return wechatArchiveApiPromise;
}

function createRequestForItem(listing: DeviceDirectoryListing, item: DeviceDirectoryItem): DeviceFileSelector {
  if (listing.root) {
    return {
      root: listing.root,
      path: item.path,
    };
  }
  return {
    absolute_path: item.path,
  };
}

function createNodes(
  listing: DeviceDirectoryListing,
  depth: number,
  workspaceState?: StorageWorkspaceState | null,
): StorageNode[] {
  const sortedItems = [...listing.items].sort(compareItems);
  const totalSiblingTotalBytes = sortedItems.reduce((total, item) => total + (getItemTotalSize(item) ?? 0), 0);
  const totalSiblingMaxBytes = sortedItems.reduce((max, item) => Math.max(max, getItemTotalSize(item) ?? 0), 0);
  const totalSiblingUnknownCount = sortedItems.filter((item) => getItemTotalSize(item) == null).length;
  const directSiblingTotalBytes = sortedItems.reduce((total, item) => total + (getItemDirectSize(item) ?? 0), 0);
  const directSiblingMaxBytes = sortedItems.reduce((max, item) => Math.max(max, getItemDirectSize(item) ?? 0), 0);
  const directSiblingUnknownCount = sortedItems.filter((item) => getItemDirectSize(item) == null).length;
  return sortedItems.map((item) => {
    const request = createRequestForItem(listing, item);
    return {
      id: `${Date.now()}-${++nodeSeq}`,
      name: item.name,
      path: item.path,
      isDir: item.is_dir,
      totalSizeBytes: getItemTotalSize(item),
      directSizeBytes: getItemDirectSize(item),
      diskTotalBytes: getItemDiskTotalBytes(item),
      diskFreeBytes: getItemDiskFreeBytes(item),
      recursiveFileCount: normalizeMaybeNumber(item.recursive_file_count),
      modifiedAt: getItemModifiedAt(item),
      totalSiblingTotalBytes,
      totalSiblingMaxBytes,
      totalSiblingUnknownCount,
      directSiblingTotalBytes,
      directSiblingMaxBytes,
      directSiblingUnknownCount,
      depth,
      request,
      children: [],
      childrenLoaded: false,
      expanded: false,
      loading: false,
      loadError: '',
      visibleChildLimit: getSavedVisibleLimit(workspaceState, getRequestStateKey(request)),
    };
  });
}

function appendVisibleGroup(
  rows: StorageVisibleRow[],
  nodes: StorageNode[],
  parent: StorageNode | null,
) {
  const limit = parent ? parent.visibleChildLimit : rootVisibleLimit.value;
  const sortedNodes = [...nodes].sort(compareStorageNodes);
  const visibleCount = Math.min(limit, sortedNodes.length);
  const visibleNodes = sortedNodes.slice(0, visibleCount);

  for (const node of visibleNodes) {
    rows.push({
      kind: 'node',
      id: `node-${node.id}`,
      node,
    });
    if (node.expanded && node.children.length) {
      appendVisibleGroup(rows, node.children, node);
    }
  }

  if (visibleCount < sortedNodes.length) {
    rows.push({
      kind: 'more',
      id: `more-${parent?.id ?? 'root'}-${visibleCount}-${sortedNodes.length}`,
      depth: parent ? parent.depth + 1 : 0,
      visibleCount,
      totalCount: sortedNodes.length,
      remainingCount: sortedNodes.length - visibleCount,
      parent,
    });
  }
}

function compareStorageNodes(left: StorageNode, right: StorageNode): number {
  const leftSize = getNodeSize(left);
  const rightSize = getNodeSize(right);
  if (leftSize != null || rightSize != null) {
    if (leftSize == null) return 1;
    if (rightSize == null) return -1;
    if (leftSize !== rightSize) return rightSize - leftSize;
  }
  if (left.isDir !== right.isDir) {
    return left.isDir ? -1 : 1;
  }
  return left.name.localeCompare(right.name, 'zh-CN');
}

async function fetchListing(request: DeviceFileSelector): Promise<DeviceDirectoryListing> {
  if (isWechatMode.value) {
    const { fetchWechatStorageDirectory } = await loadWechatArchiveApi();
    const listing = await fetchWechatStorageDirectory({
      device_id: selectedEntryId.value,
      ...(request.absolute_path ? { absolute_path: request.absolute_path } : {}),
    });
    return normalizeWechatDirectoryListing(listing);
  }
  return fetchDeviceDirectoryItems(selectedEntryId.value, {
    ...request,
    sort_program: DIRECTORY_SORT_PROGRAM,
    recursive_stats_source: 'filesystem',
  });
}

async function restoreExpandedNodes(
  nodes: StorageNode[],
  state: StorageWorkspaceState | null | undefined,
) {
  if (!state?.expandedKeys.length) {
    return;
  }
  const expandedKeys = new Set(state.expandedKeys);
  for (const node of nodes) {
    if (!node.isDir || !expandedKeys.has(getNodeStateKey(node))) {
      continue;
    }
    await loadChildren(node, {
      workspaceState: state,
      restoring: true,
      silent: true,
    });
    await restoreExpandedNodes(node.children, state);
  }
}

async function continueRestoreExpandedState(
  loadVersion: number,
  state: StorageWorkspaceState | null | undefined,
) {
  // Let the root rows render first, then continue restoring the expanded tree.
  await nextTick();
  if (loadVersion !== rootLoadVersion) {
    return;
  }
  await restoreExpandedNodes(rootNodes.value, state);
  if (loadVersion !== rootLoadVersion) {
    return;
  }
  await restoreTableScroll(state);
}

async function loadRoot(
  request: DeviceFileSelector,
  options: LoadRootOptions = {},
): Promise<boolean> {
  if (!selectedEntryId.value) {
    return false;
  }

  loading.value = true;
  loadError.value = '';
  const loadVersion = ++rootLoadVersion;
  try {
    const listing = canReusePrefetchedListing(request, options.prefetchedListing)
      ? options.prefetchedListing
      : await fetchListing(request);
    currentListing.value = listing;
    currentRequest.value = request;
    rootVisibleLimit.value = options.workspaceState
      ? normalizeVisibleLimit(options.workspaceState.rootVisibleLimit)
      : NODE_PAGE_SIZE;
    lastTableScrollTop = 0;
    lastTableScrollLeft = 0;
    rootNodes.value = createNodes(listing, 0, options.workspaceState);
    syncPathInputFromRequest(request);
    if (options.restoreExpanded) {
      void continueRestoreExpandedState(loadVersion, options.workspaceState);
    } else {
      await resetTableScroll();
    }
    persistWorkspaceState();
    return true;
  } catch (error: any) {
    rootNodes.value = [];
    currentListing.value = null;
    const detail = error?.response?.data?.detail || error?.message || '加载目录失败';
    loadError.value = detail;
    ElMessage.error(detail);
    return false;
  } finally {
    loading.value = false;
  }
}

async function loadChildren(
  node: StorageNode,
  options: LoadChildrenOptions = {},
) {
  node.loading = true;
  node.loadError = '';
  try {
    const listing = await fetchListing(node.request);
    node.children = createNodes(listing, node.depth + 1, options.workspaceState);
    node.visibleChildLimit = normalizeVisibleLimit(node.visibleChildLimit);
    node.childrenLoaded = true;
    node.expanded = true;
    if (!options.restoring) {
      persistWorkspaceState();
    }
  } catch (error: any) {
    const detail = error?.response?.data?.detail || error?.message || '加载子目录失败';
    node.loadError = detail;
    if (!options.silent) {
      ElMessage.error(detail);
    }
  } finally {
    node.loading = false;
  }
}

function syncPathInputFromRequest(request: DeviceFileSelector) {
  if (request.absolute_path === DEVICE_ROOT_SENTINEL) {
    pathInput.value = '';
    return;
  }
  if (request.absolute_path && selectedRoot.value.absolutePath) {
    const relativePath = getPathRelativeToRoot(selectedRoot.value.absolutePath, request.absolute_path);
    if (relativePath != null) {
      pathInput.value = relativePath;
      return;
    }
  }
  pathInput.value = request.absolute_path || request.path || '';
}

function buildInputRequest(): DeviceFileSelector {
  const rawPath = pathInput.value.trim();
  if (selectedRoot.value.system) {
    return {
      absolute_path: rawPath || DEVICE_ROOT_SENTINEL,
    };
  }
  if (selectedRoot.value.absolutePath) {
    return {
      absolute_path: joinRootPath(selectedRoot.value.absolutePath, rawPath),
    };
  }
  return {
    root: selectedRoot.value.rootKey ?? undefined,
    path: rawPath,
  };
}

function handleRootChange() {
  pathInput.value = '';
  rootNodes.value = [];
  currentListing.value = null;
  currentRequest.value = null;
  loadError.value = '';
  if (isWechatMode.value && selectedRootKey.value) {
    selectedEntryId.value = selectedRootKey.value;
  }
  clearDuplicateListing();
  schedulePersistWorkspaceState();
}

async function loadFromInputs() {
  if (!canBrowse.value) {
    return;
  }
  await loadRoot(buildInputRequest());
}

async function reloadCurrent() {
  const request = currentRequest.value;
  if (!request) {
    await loadFromInputs();
    return;
  }
  const restoreState = buildWorkspaceState();
  refreshing.value = true;
  try {
    await loadRoot(request, {
      workspaceState: restoreState,
      restoreExpanded: true,
    });
  } finally {
    refreshing.value = false;
  }
}

function setActiveView(view: StorageView) {
  activeView.value = view;
  if (typeof window !== 'undefined') {
    window.localStorage.setItem(activeViewStorageKey.value, view);
  }
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

function buildDuplicateRequest(page: number, reuseSnapshot: boolean) {
  const request = buildInputRequest();
  if (request.absolute_path === DEVICE_ROOT_SENTINEL) {
    throw new Error('请先进入具体磁盘或目录，再分析重复文件。');
  }
  return {
    ...request,
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

function stopDuplicateTaskPolling() {
  duplicateTaskPollVersion += 1;
}

async function refreshDuplicateTask(taskId: string, page: number, showError = false) {
  if (!selectedEntryId.value || !taskId) {
    return;
  }
  try {
    const analysis = await fetchDeviceDuplicateAnalysis(selectedEntryId.value, taskId, {
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
          ElMessage.error(detail);
        }
      }
    }
  } catch (error: any) {
    if (!showError) {
      return;
    }
    const detail = error?.response?.data?.detail || error?.message || '重复文件分析失败';
    duplicateError.value = detail;
    ElMessage.error(detail);
  }
}

function startDuplicateTaskPolling(taskId: string) {
  const entryId = selectedEntryId.value;
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
    ElMessage.error(detail);
  });
}

async function analyzeDuplicates(page = 1, reuseSnapshot = false) {
  if (!selectedEntryId.value) {
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
    const analysis = await startDeviceDuplicateAnalysis(selectedEntryId.value, payload);
    duplicateListing.value = analysis;
    duplicateLoading.value = analysis.running;
    if (analysis.running) {
      startDuplicateTaskPolling(analysis.task_id);
    }
  } catch (error: any) {
    const detail = error?.response?.data?.detail || error?.message || '重复文件分析失败';
    duplicateError.value = detail;
    ElMessage.error(detail);
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

function clearDuplicateListing() {
  stopDuplicateTaskPolling();
  duplicateListing.value = null;
  duplicateError.value = '';
  duplicateLoading.value = false;
}

async function toggleNode(node: StorageNode) {
  if (!node.isDir || node.loading) {
    return;
  }
  if (node.expanded) {
    node.expanded = false;
    persistWorkspaceState();
    return;
  }
  if (node.childrenLoaded) {
    node.expanded = true;
    persistWorkspaceState();
    return;
  }
  await loadChildren(node);
}

function canReusePrefetchedListing(
  request: DeviceFileSelector,
  prefetchedListing: DeviceDirectoryListing | null | undefined,
): prefetchedListing is DeviceDirectoryListing {
  if (!prefetchedListing) {
    return false;
  }
  if (request.absolute_path) {
    return prefetchedListing.absolute_path === request.absolute_path;
  }
  if (request.root) {
    return prefetchedListing.root === request.root && prefetchedListing.current_path === (request.path || '');
  }
  return false;
}

async function loadRootsForDevice(): Promise<DeviceDirectoryListing | null> {
  if (!selectedEntryId.value) {
    diskRootOptions.value = [];
    return null;
  }
  loadingRoots.value = true;
  try {
    if (isWechatMode.value) {
      if (!wechatRoots.value.length) {
        const { fetchWechatStorageRoots } = await loadWechatArchiveApi();
        const payload = await fetchWechatStorageRoots();
        wechatRoots.value = payload.items;
      }
      if (!rootOptions.value.some((root) => root.key === selectedRootKey.value)) {
        const currentRoot = wechatRoots.value.find((root) => root.current);
        selectedRootKey.value = (currentRoot?.device_id || wechatRoots.value[0]?.device_id || selectedEntryId.value);
      }
      return null;
    }
    const listing = await fetchDeviceDirectoryItems(selectedEntryId.value, {
      absolute_path: DEVICE_ROOT_SENTINEL,
      sort_program: DIRECTORY_SORT_PROGRAM,
    });
    diskRootOptions.value = buildDiskRootOptions(listing);
    if (!rootOptions.value.some((root) => root.key === selectedRootKey.value)) {
      selectedRootKey.value = SYSTEM_ROOT_KEY;
    }
    return listing;
  } catch (error: any) {
    diskRootOptions.value = [];
    ElMessage.error(error?.response?.data?.detail || error?.message || '加载磁盘根目录失败');
    return null;
  } finally {
    loadingRoots.value = false;
  }
}

async function handleDeviceChange() {
  rootNodes.value = [];
  currentListing.value = null;
  currentRequest.value = null;
  pathInput.value = '';
  clearDuplicateListing();
  activeDeleteTask.value = null;
  stopDeleteTaskPolling();
  if (isWechatMode.value) {
    if (selectedEntryId.value) {
      selectedRootKey.value = selectedEntryId.value;
    }
  }
  const prefetchedRootListing = await loadRootsForDevice();
  if (!isWechatMode.value) {
    await syncLatestDeleteTaskForCurrentDevice();
  }
  await loadRoot(buildInputRequest(), {
    prefetchedListing: prefetchedRootListing,
  });
}

function closeContextMenu() {
  contextMenu.value.visible = false;
}

function closeTableConfigMenu() {
  tableConfigMenu.value.visible = false;
}

function handleGlobalClick() {
  closeContextMenu();
  closeTableConfigMenu();
}

function handleGlobalKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    closeContextMenu();
    closeTableConfigMenu();
  }
}

function openStorageConfigMenu(event: MouseEvent) {
  closeContextMenu();
  const menuWidth = 180;
  const menuHeight = 248;
  const viewportWidth = typeof window === 'undefined' ? event.clientX + menuWidth : window.innerWidth;
  const viewportHeight = typeof window === 'undefined' ? event.clientY + menuHeight : window.innerHeight;
  tableConfigMenu.value = {
    visible: true,
    x: Math.max(8, Math.min(event.clientX, viewportWidth - menuWidth - 8)),
    y: Math.max(8, Math.min(event.clientY, viewportHeight - menuHeight - 8)),
  };
}

function openNodeContextMenu(node: StorageNode, event: MouseEvent) {
  if (!shouldShowContextMenu.value) {
    return;
  }
  closeTableConfigMenu();
  const menuWidth = 180;
  const menuHeight = 42;
  const viewportWidth = typeof window === 'undefined' ? event.clientX + menuWidth : window.innerWidth;
  const viewportHeight = typeof window === 'undefined' ? event.clientY + menuHeight : window.innerHeight;
  contextMenu.value = {
    visible: true,
    x: Math.max(8, Math.min(event.clientX, viewportWidth - menuWidth - 8)),
    y: Math.max(8, Math.min(event.clientY, viewportHeight - menuHeight - 8)),
    node,
  };
}

function isFilesystemRootNode(node: StorageNode): boolean {
  const normalizedPath = node.path.trim();
  if (!normalizedPath || normalizedPath === DEVICE_ROOT_SENTINEL) {
    return true;
  }
  if (node.depth === 0 && node.diskTotalBytes != null) {
    return true;
  }
  return /^[a-zA-Z]:[\\/]?$/.test(normalizedPath) || normalizedPath === '/' || normalizedPath === '\\';
}

function canDeleteNode(node: StorageNode): boolean {
  return !isFilesystemRootNode(node);
}

function isActiveDeleteStatus(status: DeviceDeleteTask['status']): boolean {
  return status === 'pending' || status === 'running';
}

function toActiveDeleteTask(task: DeviceDeleteTask): ActiveDeleteTask {
  return {
    id: task.task_id || task.id,
    status: task.status,
    name: task.entry_name || String(task.metadata?.entry_name ?? '') || '目标',
    path: task.target_path || String(task.metadata?.target_path ?? ''),
    pid: task.pid,
    updatedAt: task.updated_at,
    errorMessage: task.error_message,
  };
}

function stopDeleteTaskPolling() {
  deleteTaskPollVersion += 1;
}

function startDeleteTaskPolling() {
  const entryId = selectedEntryId.value;
  const initial = activeDeleteTask.value;
  if (!entryId || !initial || !isActiveDeleteStatus(initial.status)) {
    return;
  }
  const pollVersion = ++deleteTaskPollVersion;
  void monitorPolledTask<ActiveDeleteTask>({
    initial,
    poll: async (task) => {
      if (pollVersion !== deleteTaskPollVersion) {
        return { ...task, status: 'unknown' };
      }
      return toActiveDeleteTask(await fetchDeviceEntryDeleteTask(entryId, task.id));
    },
    isRunning: (task) => isActiveDeleteStatus(task.status) && pollVersion === deleteTaskPollVersion,
    getUpdatedAt: (task) => task.updatedAt,
    getError: (task) => task.status === 'failed' ? (task.errorMessage || '后台删除失败') : '',
    pollIntervalMs: 2500,
    idleTimeoutMs: 60_000,
    onUpdate: (task) => {
      if (pollVersion === deleteTaskPollVersion) {
        activeDeleteTask.value = task;
      }
    },
  }).then(async (task) => {
    if (pollVersion !== deleteTaskPollVersion) {
      return;
    }
    activeDeleteTask.value = task;
    if (task.status === 'completed') {
      ElMessage.success(`后台删除完成：${task.name}`);
      activeDeleteTask.value = null;
      await reloadCurrent();
      return;
    }
    if (task.status === 'partial_failed') {
      ElMessage.warning(task.errorMessage || '后台删除部分完成，少数路径被跳过');
      await reloadCurrent();
    }
  }).catch((error: any) => {
    if (pollVersion !== deleteTaskPollVersion || !activeDeleteTask.value) {
      return;
    }
    activeDeleteTask.value = {
      ...activeDeleteTask.value,
      status: 'unknown',
      errorMessage: error?.message || '删除任务状态读取失败',
    };
    ElMessage.error(activeDeleteTask.value.errorMessage);
  });
}

async function syncLatestDeleteTaskForCurrentDevice() {
  if (!selectedEntryId.value) {
    activeDeleteTask.value = null;
    stopDeleteTaskPolling();
    return;
  }
  try {
    const tasks = await fetchDeviceEntryDeleteTasks(selectedEntryId.value);
    const runningTask = tasks.find((task) => isActiveDeleteStatus(task.status));
    activeDeleteTask.value = runningTask ? toActiveDeleteTask(runningTask) : null;
    if (runningTask) {
      startDeleteTaskPolling();
    } else {
      stopDeleteTaskPolling();
    }
  } catch {
    activeDeleteTask.value = null;
    stopDeleteTaskPolling();
  }
}

function formatDeleteTask(task: ActiveDeleteTask): string {
  const statusLabel: Record<DeviceDeleteTask['status'], string> = {
    pending: '排队',
    running: '删除中',
    completed: '已完成',
    partial_failed: '部分失败',
    failed: '失败',
    unknown: '未知',
  };
  const pidText = task.pid ? ` · PID ${task.pid}` : '';
  return `${statusLabel[task.status] ?? '未知'} ${task.name}${pidText}`;
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

async function confirmDeleteNode(node: StorageNode) {
  closeContextMenu();
  if (!selectedEntryId.value || !canDeleteNode(node)) {
    return;
  }

  const targetPath = node.path;
  const entryType = node.isDir ? '目录' : '文件';
  try {
    await ElMessageBox.confirm(
      `将永久删除${entryType}：\n${targetPath}\n\n此操作会绕过回收站，删除后不可恢复。`,
      '确认永久删除',
      {
        type: 'warning',
        confirmButtonText: '永久删除',
        cancelButtonText: '取消',
        distinguishCancelAndClose: true,
      },
    );
  } catch {
    return;
  }

  deleteSubmittingNodeId.value = node.id;
  try {
    const result = await startDeviceEntryDelete(selectedEntryId.value, {
      ...node.request,
      recursive: node.isDir,
    });
    activeDeleteTask.value = toActiveDeleteTask(result.task);
    startDeleteTaskPolling();
    ElMessage.success(`已提交后台删除：${node.name}`);
  } catch (error: any) {
    const detail = error?.response?.data?.detail || error?.message || '删除失败';
    ElMessage.error(detail);
  } finally {
    deleteSubmittingNodeId.value = '';
  }
}

function getIndentStyle(node: StorageNode) {
  return {
    paddingInlineStart: `${node.depth * 18}px`,
  };
}

function getMoreIndentStyle(row: StorageMoreRow) {
  return {
    paddingInlineStart: `${row.depth * 18}px`,
  };
}

function getNextVisibleCount(row: StorageMoreRow): number {
  return Math.min(NODE_PAGE_SIZE, row.remainingCount);
}

function revealMore(row: StorageMoreRow) {
  if (row.parent) {
    row.parent.visibleChildLimit = Math.min(row.totalCount, row.parent.visibleChildLimit + NODE_PAGE_SIZE);
    persistWorkspaceState();
    return;
  }
  rootVisibleLimit.value = Math.min(row.totalCount, rootVisibleLimit.value + NODE_PAGE_SIZE);
  persistWorkspaceState();
}

function getNodeSize(node: StorageNode): number | null {
  return sizeValueMode.value === 'direct'
    ? node.directSizeBytes
    : node.totalSizeBytes;
}

function getNodeSiblingTotalBytes(node: StorageNode): number {
  return sizeValueMode.value === 'direct'
    ? node.directSiblingTotalBytes
    : node.totalSiblingTotalBytes;
}

function getNodeSiblingMaxBytes(node: StorageNode): number {
  return sizeValueMode.value === 'direct'
    ? node.directSiblingMaxBytes
    : node.totalSiblingMaxBytes;
}

function getUsageWidth(node: StorageNode): string {
  const denominator = getUsageDenominator(node);
  const sizeBytes = getNodeSize(node);
  if (sizeBytes == null || denominator <= 0) {
    return '0%';
  }
  const percent = (sizeBytes / denominator) * 100;
  if (percent <= 0) {
    return '0%';
  }
  return `${Math.max(2, Math.min(100, percent))}%`;
}

function getUsageDenominator(node: StorageNode): number {
  if (sizeBarMode.value === 'globalMax') {
    return globalReferenceBytes.value;
  }
  if (sizeBarMode.value === 'siblingTotal') {
    return getNodeSiblingTotalBytes(node);
  }
  return getNodeSiblingMaxBytes(node);
}

function hashStringToHue(value: string): number {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return Math.abs(hash) % 360;
}

function getUsageLevelStyle(node: StorageNode): Record<string, string> {
  const hue = sizeBarColorMode.value === 'uniform'
    ? 204
    : hashStringToHue(`storage-depth-${node.depth}`);
  const endHue = (hue + 28) % 360;
  return {
    '--usage-track-color': `hsl(${hue} 48% 98%)`,
    '--usage-border-color': `hsl(${hue} 38% 88%)`,
    '--usage-fill-start': `hsl(${hue} 76% 88%)`,
    '--usage-fill-end': `hsl(${endHue} 78% 86%)`,
  };
}

function formatSizePercent(percent: number): string {
  if (!Number.isFinite(percent) || percent <= 0) {
    return '0%';
  }
  return `${Math.min(100, percent).toPrecision(4)}%`;
}

function formatSizeBarText(node: StorageNode): string {
  const denominator = getUsageDenominator(node);
  const sizeBytes = getNodeSize(node);
  if (sizeBytes == null || denominator <= 0) {
    return '--';
  }
  const percent = (sizeBytes / denominator) * 100;
  return formatSizePercent(percent);
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

function formatNodeSize(node: StorageNode): string {
  return formatBytes(getNodeSize(node));
}

function formatFileCount(node: StorageNode): string {
  if (node.diskFreeBytes != null) {
    return `剩余 ${formatBytes(node.diskFreeBytes)}`;
  }
  if (!node.isDir) {
    return '1';
  }
  return node.recursiveFileCount == null ? '--' : String(node.recursiveFileCount);
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

function normalizeSizeBarMode(value: string | null): SizeBarMode | null {
  if (value === 'max') {
    return 'siblingMax';
  }
  if (value === 'total') {
    return 'siblingTotal';
  }
  if (value === 'siblingMax' || value === 'siblingTotal' || value === 'globalMax') {
    return value;
  }
  return null;
}

function normalizeSizeValueMode(value: string | null): SizeValueMode | null {
  if (value === 'total' || value === 'direct') {
    return value;
  }
  return null;
}

function normalizeSizeBarColorMode(value: string | null): SizeBarColorMode | null {
  if (value === 'depth' || value === 'uniform') {
    return value;
  }
  return null;
}

function setSizeValueMode(mode: SizeValueMode) {
  sizeValueMode.value = mode;
}

function setSizeBarMode(mode: SizeBarMode) {
  sizeBarMode.value = mode;
}

function setSizeBarColorMode(mode: SizeBarColorMode) {
  sizeBarColorMode.value = mode;
}

function chooseSizeValueMode(mode: SizeValueMode) {
  setSizeValueMode(mode);
  closeTableConfigMenu();
}

function chooseSizeBarMode(mode: SizeBarMode) {
  setSizeBarMode(mode);
  closeTableConfigMenu();
}

function chooseSizeBarColorMode(mode: SizeBarColorMode) {
  setSizeBarColorMode(mode);
  closeTableConfigMenu();
}

watch(sizeBarMode, (mode) => {
  if (typeof window !== 'undefined') {
    window.localStorage.setItem(SIZE_BAR_MODE_STORAGE_KEY, mode);
  }
});

watch(sizeValueMode, (mode) => {
  if (typeof window !== 'undefined') {
    window.localStorage.setItem(SIZE_VALUE_MODE_STORAGE_KEY, mode);
  }
});

watch(sizeBarColorMode, (mode) => {
  if (typeof window !== 'undefined') {
    window.localStorage.setItem(SIZE_BAR_COLOR_MODE_STORAGE_KEY, mode);
  }
});

watch([duplicateRuleFields, duplicateFilterRules, duplicateMinSizeMb, duplicateSortMode, duplicateSource], () => {
  if (!duplicateRuleFields.value.includes('size')) {
    duplicateRuleFields.value = normalizeDuplicateRuleFields(duplicateRuleFields.value);
  }
  clearDuplicateListing();
  persistDuplicateSettings();
}, { deep: true });

onMounted(async () => {
  const savedWorkspaceState = readWorkspaceState();
  if (typeof window !== 'undefined') {
    window.addEventListener('click', handleGlobalClick);
    window.addEventListener('keydown', handleGlobalKeydown);
    const savedView = normalizeStorageView(window.localStorage.getItem(activeViewStorageKey.value));
    if (savedView) {
      activeView.value = savedView;
      if (isWechatMode.value && savedView === 'duplicates') {
        activeView.value = 'tree';
      }
    }
    const duplicateSettings = readDuplicateSettings();
    if (duplicateSettings) {
      duplicateRuleFields.value = duplicateSettings.rules;
      duplicateFilterRules.value = duplicateSettings.filterRules;
      duplicateMinSizeMb.value = duplicateSettings.minSizeMb;
      duplicateSortMode.value = duplicateSettings.sortMode;
      duplicateSource.value = duplicateSettings.source;
    }
    const savedSizeValueMode = window.localStorage.getItem(SIZE_VALUE_MODE_STORAGE_KEY);
    const normalizedSizeValueMode = normalizeSizeValueMode(savedSizeValueMode);
    if (normalizedSizeValueMode) {
      sizeValueMode.value = normalizedSizeValueMode;
    }
    const savedMode = window.localStorage.getItem(SIZE_BAR_MODE_STORAGE_KEY);
    const normalizedMode = normalizeSizeBarMode(savedMode);
    if (normalizedMode) {
      sizeBarMode.value = normalizedMode;
    }
    const savedColorMode = window.localStorage.getItem(SIZE_BAR_COLOR_MODE_STORAGE_KEY);
    const normalizedColorMode = normalizeSizeBarColorMode(savedColorMode);
    if (normalizedColorMode) {
      sizeBarColorMode.value = normalizedColorMode;
    }
  }

  loadingDevices.value = true;
  try {
    let canRestoreSelectedEntry = false;
    if (isWechatMode.value) {
      const { fetchWechatStorageRoots } = await loadWechatArchiveApi();
      const payload = await fetchWechatStorageRoots();
      wechatRoots.value = payload.items;
      const preferred = payload.items.find((item) => item.current) ?? payload.items[0] ?? null;
      const isSavedDeviceAvailable = payload.items.some((item) => item.device_id === savedWorkspaceState?.selectedEntryId);
      canRestoreSelectedEntry = isSavedDeviceAvailable;
      selectedEntryId.value = isSavedDeviceAvailable && savedWorkspaceState?.selectedEntryId
        ? savedWorkspaceState.selectedEntryId
        : (preferred?.device_id || payload.items[0]?.device_id || '');
      if (selectedEntryId.value) {
        const isSavedRootAvailable = payload.items.some((item) => item.device_id === savedWorkspaceState?.selectedRootKey);
        selectedRootKey.value = isSavedRootAvailable && savedWorkspaceState?.selectedRootKey
          ? savedWorkspaceState.selectedRootKey
          : selectedEntryId.value;
        await loadRootsForDevice();
      }
    } else {
      await taskStore.fetchDevices();
      const resolvedDevices = devices.value as Device[];
      const savedDevice = resolvedDevices.find((device) => device.id === savedWorkspaceState?.selectedEntryId);
      const localDevice = resolvedDevices.find((device) => device.mode === 'local');
      selectedEntryId.value = (savedDevice ?? localDevice ?? devices.value[0])?.id ?? '';
      canRestoreSelectedEntry = Boolean(savedDevice);
      const prefetchedRootListing = await loadRootsForDevice();
      await syncLatestDeleteTaskForCurrentDevice();
      if (selectedEntryId.value) {
        const savedRequest = canRestoreSelectedEntry && requestMatchesAvailableScope(savedWorkspaceState?.currentRequest ?? null)
          ? savedWorkspaceState?.currentRequest ?? null
          : null;
        const restored = savedRequest
          ? await loadRoot(savedRequest, {
            workspaceState: savedWorkspaceState,
            restoreExpanded: true,
            prefetchedListing: prefetchedRootListing,
          })
          : false;
        if (!restored) {
          await loadRoot(buildInputRequest(), {
            prefetchedListing: prefetchedRootListing,
          });
        }
      }
      return;
    }

    if (selectedEntryId.value) {
      const savedRequest = canRestoreSelectedEntry && requestMatchesAvailableScope(savedWorkspaceState?.currentRequest ?? null)
        ? savedWorkspaceState?.currentRequest ?? null
        : null;
      const restored = savedRequest
        ? await loadRoot(savedRequest, {
          workspaceState: savedWorkspaceState,
          restoreExpanded: true,
        })
        : false;
      if (!restored) {
        await loadFromInputs();
      }
    }
  } finally {
    loadingDevices.value = false;
    workspaceStateReady = true;
    persistWorkspaceState();
  }
});

onBeforeUnmount(() => {
  stopDeleteTaskPolling();
  stopDuplicateTaskPolling();
  stopWorkspacePersistTimer();
  if (typeof window !== 'undefined') {
    window.removeEventListener('click', handleGlobalClick);
    window.removeEventListener('keydown', handleGlobalKeydown);
  }
});
</script>

<style scoped>
.storage-page {
  min-height: 100%;
  padding: 18px 20px;
  box-sizing: border-box;
  background: #f6f8fb;
  color: #1f2937;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.storage-toolbar {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 16px;
  border-bottom: 1px solid #dfe5ee;
  background: #ffffff;
}

.storage-fields {
  flex: 1;
  min-width: 0;
  display: grid;
  grid-template-columns: minmax(180px, 240px) minmax(200px, 280px) minmax(260px, 1fr);
  gap: 12px;
  align-items: end;
}

.storage-field {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.storage-field-label {
  color: #64748b;
  font-size: 12px;
  font-weight: 600;
}

.storage-select {
  width: 100%;
}

.root-option {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  min-width: 0;
}

.root-option small {
  color: #94a3b8;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.storage-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.storage-help-button {
  width: 32px;
  height: 32px;
  border: 1px solid #d5dde8;
  border-radius: 50%;
  background: #ffffff;
  color: #64748b;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}

.storage-help-button:hover {
  color: #2563eb;
  border-color: #93c5fd;
  background: #eff6ff;
}

.storage-help {
  max-width: 280px;
  line-height: 1.6;
}

.storage-view-tabs {
  min-height: 32px;
  padding: 0 16px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.storage-view-tabs button {
  height: 28px;
  padding: 0 12px;
  border: 1px solid transparent;
  border-radius: 4px;
  background: transparent;
  color: #64748b;
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}

.storage-view-tabs button:hover {
  color: #1d4ed8;
  background: #eff6ff;
}

.storage-view-tabs button.is-active {
  border-color: #93c5fd;
  background: #ffffff;
  color: #1d4ed8;
  font-weight: 600;
}

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

.summary-delete-task {
  color: #475569;
}

.summary-delete-task.is-running,
.summary-delete-task.is-pending {
  color: #1d4ed8;
}

.summary-delete-task.is-failed {
  color: #b42318;
}

.summary-delete-task.is-partial_failed {
  color: #b45309;
}

.summary-warning {
  color: #b45309;
}

.duplicate-toolbar {
  padding: 0 16px 4px;
  display: flex;
  align-items: end;
  gap: 12px;
  flex-wrap: wrap;
}

.duplicate-field {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.duplicate-field-rules {
  min-width: 420px;
}

.duplicate-field-min-size {
  width: 140px;
}

.duplicate-field-select {
  width: 136px;
}

.duplicate-field-filter {
  width: 132px;
}

.duplicate-rule-group {
  min-height: 32px;
  padding: 0 10px;
  border: 1px solid #d5dde8;
  border-radius: 4px;
  background: #ffffff;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.duplicate-rule-group :deep(.el-checkbox) {
  margin-right: 10px;
}

.duplicate-number-input,
.duplicate-select {
  width: 100%;
}

.duplicate-filter-button {
  width: 100%;
}

.duplicate-filter-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.duplicate-filter-header,
.duplicate-filter-rule {
  display: flex;
  align-items: center;
  gap: 8px;
}

.duplicate-filter-header {
  justify-content: space-between;
}

.duplicate-filter-actions {
  display: flex;
  gap: 6px;
}

.duplicate-filter-action {
  width: 82px;
}

.duplicate-filter-match {
  width: 86px;
}

.duplicate-filter-value {
  flex: 1;
}

.duplicate-filter-remove {
  width: 24px;
  height: 24px;
  border: 1px solid #fecaca;
  border-radius: 4px;
  background: #fff5f5;
  color: #b42318;
  line-height: 1;
  cursor: pointer;
}

.duplicate-filter-remove:hover {
  background: #fee2e2;
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

.storage-table-row.is-context-target .storage-cell {
  background: #f8fafc;
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

.storage-cell-name {
  width: 1%;
  min-width: 240px;
  max-width: 380px;
}

.storage-cell-count,
.storage-cell-time,
.storage-cell-path {
  width: 1%;
}

.storage-cell-size {
  width: 150px;
  min-width: 150px;
}

.storage-cell-percent {
  width: 290px;
  min-width: 290px;
}

.storage-cell-size,
.storage-cell-count,
.storage-cell-time {
  color: #334155;
}

.storage-configurable-head {
  cursor: context-menu;
  user-select: none;
}

.storage-cell-path span {
  display: inline-block;
  max-width: 360px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #64748b;
}

.storage-table.is-wechat-storage .storage-cell-path {
  min-width: 120px;
}

.storage-table.is-wechat-storage .storage-cell-path span {
  max-width: 180px;
  color: #475569;
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

.duplicate-pagination {
  min-height: 36px;
  padding: 0 16px;
  display: flex;
  align-items: center;
  gap: 10px;
}

.duplicate-page-label {
  color: #475569;
  font-size: 13px;
  font-weight: 600;
}

.storage-more-row {
  color: #64748b;
}

.storage-more-cell {
  padding-top: 5px;
  padding-bottom: 5px;
}

.entry-button,
.entry-label {
  width: max-content;
  max-width: 360px;
  min-width: 0;
  box-sizing: border-box;
  overflow: hidden;
  border: none;
  background: transparent;
  color: #111827;
  font: inherit;
  display: flex;
  align-items: center;
  gap: 7px;
  text-align: left;
}

.entry-button {
  cursor: pointer;
}

.entry-button:hover .entry-name {
  color: #1d4ed8;
}

.entry-button:disabled {
  cursor: default;
  opacity: 0.72;
}

.entry-toggle {
  width: 18px;
  height: 18px;
  flex: 0 0 18px;
  border-radius: 4px;
  color: #64748b;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 700;
}

.entry-button .entry-toggle {
  border: 1px solid #d5dde8;
  background: #f8fafc;
}

.load-more-button {
  width: max-content;
  max-width: 360px;
  border: none;
  background: transparent;
  color: #475569;
  font: inherit;
  display: inline-flex;
  align-items: center;
  gap: 7px;
  text-align: left;
  cursor: pointer;
}

.load-more-button:hover {
  color: #1d4ed8;
}

.load-more-button small {
  color: #94a3b8;
}

.more-toggle {
  border: 1px solid #d5dde8;
  background: #f8fafc;
  font-size: 11px;
}

.storage-context-menu {
  position: fixed;
  z-index: 3000;
  min-width: 168px;
  padding: 4px;
  border: 1px solid #d5dde8;
  border-radius: 6px;
  background: #ffffff;
  box-shadow: 0 10px 28px rgb(15 23 42 / 16%);
}

.context-menu-item {
  width: 100%;
  min-height: 30px;
  padding: 0 9px;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: #334155;
  font: inherit;
  font-size: 13px;
  display: flex;
  align-items: center;
  gap: 8px;
  text-align: left;
  cursor: pointer;
}

.context-menu-item:hover:not(:disabled) {
  background: #f8fafc;
}

.storage-config-menu {
  min-width: 174px;
  padding: 6px;
}

.storage-config-menu-section {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 3px 0;
}

.storage-config-menu-section + .storage-config-menu-section {
  margin-top: 4px;
  padding-top: 7px;
  border-top: 1px solid #edf1f6;
}

.storage-config-menu-section strong {
  padding: 0 8px 3px;
  color: #94a3b8;
  font-size: 12px;
  font-weight: 600;
}

.context-menu-item.is-active {
  background: #eff6ff;
  color: #1d4ed8;
  font-weight: 600;
}

.context-menu-item.is-danger {
  color: #b42318;
}

.context-menu-item:disabled {
  color: #94a3b8;
  cursor: not-allowed;
}

.entry-icon {
  flex: 0 0 auto;
  font-size: 16px;
}

.entry-icon-dir {
  color: #b45309;
}

.entry-icon-file {
  color: #64748b;
}

.entry-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.usage-bar {
  position: relative;
  width: 160px;
  height: 22px;
  border-radius: 4px;
  overflow: hidden;
  background: var(--usage-track-color, #f8fafc);
  box-shadow: inset 0 0 0 1px var(--usage-border-color, #e2e8f0);
}

.usage-fill {
  position: absolute;
  inset: 0 auto 0 0;
  background: linear-gradient(90deg, var(--usage-fill-start, #d1fae5), var(--usage-fill-end, #bfdbfe));
}

.usage-text {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  height: 100%;
  padding: 0 8px;
  color: #0f172a;
  font-size: 12px;
  font-weight: 600;
}

.usage-bar.is-unknown .usage-text {
  color: #94a3b8;
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
  .storage-toolbar {
    align-items: stretch;
    flex-direction: column;
  }

  .storage-fields {
    grid-template-columns: 1fr;
  }

  .storage-actions {
    justify-content: flex-end;
  }

  .duplicate-field-rules,
  .duplicate-field-min-size,
  .duplicate-field-select,
  .duplicate-field-filter {
    width: 100%;
    min-width: 0;
  }

  .duplicate-rule-group {
    flex-wrap: wrap;
    min-height: 40px;
    padding: 6px 10px;
  }

  .summary-path code {
    max-width: 78vw;
  }
}
</style>
