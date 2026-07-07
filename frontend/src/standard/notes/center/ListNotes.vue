<template>
  <div class="list-notes-layout">
    <div class="filter-section">
      <div v-if="!backendFilterExpanded" class="front-filter-collapsed">
        <div class="front-filter-collapsed__meta">
          <div class="front-filter-collapsed__title">后端筛选</div>
          <div class="front-filter-collapsed__summary">{{ dataProgramSummary }}</div>
        </div>
        <div class="collapsed-filter-actions">
          <el-button size="small" @click="expandBackendFilter">编辑规则</el-button>
          <el-button size="small" type="primary" :loading="loading" @click="applyDataProgram">执行</el-button>
        </div>
      </div>
      <NoteProgramBar
        v-else
        v-model="dataProgram"
        title="后端筛选"
        help-text="决定从后端加载哪些节点，点击“执行”后生效并保存；规则按顺序执行，后面的添加/移除/筛选可以覆盖前面的结果。"
        hint-text=""
        apply-text="执行"
        reset-text="恢复默认"
        enable-full-text
        :loading="loading"
        @apply="applyDataProgram"
        @reset="resetDataProgram"
      />
    </div>

    <div class="filter-section front-filter-section">
      <div v-if="!frontFilterExpanded" class="front-filter-collapsed">
        <div class="front-filter-collapsed__meta">
          <div class="front-filter-collapsed__title">前端筛选</div>
          <div class="front-filter-collapsed__summary">{{ frontFilterSummary }}</div>
        </div>
        <el-button size="small" @click="expandFrontFilter">编辑规则</el-button>
      </div>
      <NoteProgramBar
        v-else
        v-model="viewProgram"
        title="前端筛选"
        help-text="基于后端筛选的数据源实时筛选并渲染，修改后立即生效并保存。"
        hint-text=""
        apply-text="即时生效"
        reset-text="恢复默认"
        @apply="applyViewProgram"
        @reset="resetViewProgram"
      />
    </div>

    <div class="toolbar-section">
      <div v-if="selectedCount > 0" class="bulk-actions">
        <el-tag type="info">已选 {{ selectedCount }} 项</el-tag>
        <el-button size="small" @click="selectAllVisible" :disabled="visiblePageNotes.length === 0 || allVisibleSelected">全选当前页</el-button>
        <el-button size="small" @click="clearSelection">清空选择</el-button>
        <el-button size="small" type="primary" plain @click="batchEditVisible = true">批量编辑</el-button>
      </div>

      <div class="toolbar-actions">
        <el-button type="primary" :icon="Plus" @click="createNewNote">新建节点</el-button>
        <el-button :icon="Refresh" @click="refreshData">重载工作集</el-button>
      </div>
    </div>

    <NoteSplitView
      class="notes-workspace"
      :top-height="listHeight"
      :show-editor="Boolean(currentNoteId)"
      empty-description="请选择一个节点进行编辑"
      @resize-start="startResizing"
    >
      <template #main>
        <div class="list-container">
          <div class="list-summary-bar">
            <span>共 {{ filteredNotes.length }} 条</span>
            <span v-if="filteredNotes.length > visiblePageRows.length">
              当前显示 {{ visiblePageRows.length }} 条
            </span>
          </div>
          <el-table
            ref="tableRef"
            v-loading="loading"
            :data="visiblePageRows"
            class="notes-table"
            table-layout="fixed"
            highlight-current-row
            @current-change="handleCurrentChange"
            @selection-change="handleSelectionChange"
            row-key="id"
            border
            size="small"
          >
            <el-table-column type="selection" width="48" reserve-selection />

            <el-table-column prop="title" label="标题" min-width="200" sortable show-overflow-tooltip>
              <template #default="{ row }">
                <span class="note-title" :style="row._ui.titleStyle">
                  <span class="note-title-text">{{ row.title || '无标题' }}</span>
                </span>
              </template>
            </el-table-column>

            <el-table-column prop="primary_category" label="分类" width="100" sortable>
              <template #default="{ row }">
                <span
                  class="node-type-text"
                  :style="row._ui.typeTagStyle"
                >
                  {{ row._ui.categoryLabel }}
                </span>
              </template>
            </el-table-column>

            <el-table-column prop="note_form" label="形态" width="88" sortable>
              <template #default="{ row }">
                <span class="form-badge-text">{{ row._ui.noteFormLabel }}</span>
              </template>
            </el-table-column>

            <el-table-column prop="lifecycle_stage" label="阶段" width="100" sortable>
              <template #default="{ row }">
                <span
                  v-if="row._ui.useSplitStatusBadge"
                  class="node-badge node-badge--split"
                  :style="row._ui.statusStyle"
                >
                  <span class="node-badge-layer" :style="row._ui.statusFillLayerStyle">
                    {{ row._ui.lifecycleStageLabel }}
                  </span>
                  <span class="node-badge-layer" :style="row._ui.statusEmptyLayerStyle">
                    {{ row._ui.lifecycleStageLabel }}
                  </span>
                </span>
                <span
                  v-else
                  class="node-badge"
                  :style="row._ui.statusStyle"
                >
                  {{ row._ui.lifecycleStageLabel }}
                </span>
              </template>
            </el-table-column>

            <el-table-column prop="weight" label="权重" width="80" sortable />

            <el-table-column prop="private_level" label="私密" width="88" sortable>
              <template #default="{ row }">
                <span
                  class="private-level-badge"
                  :class="row._ui.privateLevelClass"
                >
                  {{ row._ui.privateLevelLabel }}
                </span>
              </template>
            </el-table-column>

            <el-table-column prop="start_at" label="起始时间" width="176" sortable>
              <template #default="{ row }">
                <span class="start-at-badge" :style="row._ui.startAtBadgeStyle">
                  {{ row._ui.formattedStartAt }}
                </span>
              </template>
            </el-table-column>
          </el-table>
          <div class="list-pagination-bar">
            <StandardPagination
              v-model:page="currentPage"
              v-model:page-size="pageSize"
              :page-size-options="PAGE_SIZE_OPTIONS"
              :total="filteredNotes.length"
            />
          </div>
        </div>
      </template>

      <template #editor>
        <NoteDetailPanel
          :noteId="currentNoteId"
          editor-layout="fill"
          @update="handleNoteUpdate"
          @delete="handleNoteDelete"
          @create="handleNoteCreate"
        />
      </template>
    </NoteSplitView>

    <BatchNoteEditDialog
      v-if="batchEditVisible"
      v-model="batchEditVisible"
      :note-ids="selectedNoteIds"
      @saved="handleBatchEditSaved"
    />
  </div>
</template>

<script setup lang="ts">
import { defineAsyncComponent, ref, computed, onMounted, watch, nextTick } from 'vue';
import {
  useNoteStore,
  type NoteNode,
  applyNoteProgramChannelLocally,
  buildScanNoteProgramRequest,
  cloneNoteProgramChannel,
  createDefaultRecentMonthProgram,
  createIncludeAllProgram,
  noteProgramChannelNeedsCustomFieldsLocally,
  noteKey,
  normalizeNoteProgramChannel
} from '@/api/notes';
import { Plus, Refresh } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import NoteSplitView from '@/components/NoteSplitView.vue';
import StandardPagination from '@/components/StandardPagination.vue';
import { ensureNoteTypePaletteLoaded, getNodeDisplayStyleFromTheme, getNodeTheme, getNodeTypeConfig, getNodeStatusConfig, getNoteFormConfig } from '@/utils/nodeConfig';
import { isBootPerfEnabled, markBootPerf } from '@/utils/bootPerf';
import { formatNoteDateTime } from '@/utils/noteDate';
import { useResizablePane } from '@/utils/useResizablePane';
import { resolveCompletionProgressFillRatio } from '@/utils/noteProgress';
import { getStableBadgeStyle } from '@/utils/stableVisualColor';

const noteStore = useNoteStore();
markBootPerf('notes-list.module');
const NoteProgramBar = defineAsyncComponent(() => import('@/components/NoteProgramBar.vue'));
const NoteDetailPanel = defineAsyncComponent(() => import('@/components/NoteDetailPanel.vue'));
const BatchNoteEditDialog = defineAsyncComponent(() => import('@/components/BatchNoteEditDialog.vue'));
const NOTES_LIST_QUERY_CACHE_KEY = 'codeyun:notes:list-query-cache:v1';
const NOTES_LIST_QUERY_CACHE_TTL_MS = 5 * 60 * 1000;
const props = defineProps<{
  tabId: string;
  active?: boolean;
}>();

const session = computed(() => noteStore.getTabSession(props.tabId));
const listPerfEnabled = isBootPerfEnabled();
const getPerfNow = () => (typeof performance !== 'undefined' ? performance.now() : Date.now());
let lastFilteredNotesPerfVersion = '';
let lastVisibleRowsPerfVersion = '';
let initialRowsMarked = false;
const getAppliedDataProgram = () => normalizeNoteProgramChannel(
  session.value?.viewState.dataProgram ?? createDefaultRecentMonthProgram('start_at')
);
const getViewProgram = () => normalizeNoteProgramChannel(
  session.value?.viewState.viewProgram ?? createIncludeAllProgram()
);
const isIncludeAllProgram = (channel: NoteProgramChannel) => {
  const normalized = normalizeNoteProgramChannel(channel);
  return normalized.default === false
    && normalized.rules.length === 1
    && normalized.rules[0]?.action === 'include'
    && normalized.rules[0]?.matcher.kind === 'all';
};

// State
const dataProgram = ref(normalizeNoteProgramChannel(
  getAppliedDataProgram()
));
const viewProgram = ref(normalizeNoteProgramChannel(
  getViewProgram()
));
const backendFilterExpanded = ref(false);
const frontFilterExpanded = ref(!isIncludeAllProgram(getViewProgram()));
const currentNoteId = ref('');
const loading = ref(false);
const tableRef = ref<any>(null);
const selectedNoteIds = ref<string[]>([]);
const batchEditVisible = ref(false);
const PAGE_SIZE_OPTIONS = [20, 50, 100, 200];
const currentPage = ref(1);
const pageSize = ref(50);
const isActive = computed(() => props.active !== false);
const listViewNeedsCustomFields = computed(() => noteProgramChannelNeedsCustomFieldsLocally(viewProgram.value));
const frontFilterSummary = computed(() => (
  isIncludeAllProgram(viewProgram.value)
    ? '默认显示当前已加载的全部节点。'
    : `已配置 ${normalizeNoteProgramChannel(viewProgram.value).rules.length} 条规则。`
));
const dataProgramSummary = computed(() => {
  const normalized = normalizeNoteProgramChannel(dataProgram.value);
  const rules = normalized.rules.length;
  if (rules === 0) {
    return normalized.default ? '当前默认加载全部节点。' : '当前默认不加载节点。';
  }

  const defaultRecentMonth = JSON.stringify(normalized) === JSON.stringify(createDefaultRecentMonthProgram('start_at'));
  if (defaultRecentMonth) {
    return '当前默认加载最近 1 个月到未来 1 个月的节点。';
  }

  return `已配置 ${rules} 条规则；点击“编辑规则”可调整加载范围。`;
});

type ListQueryCachePayload = {
  savedAt: number;
  request: NoteProgramRequest;
  data: NoteProgramResponse;
};

const buildListQueryRequest = (program = getAppliedDataProgram()) => buildScanNoteProgramRequest(program, {
  limit: 1000,
  include_custom_fields: listViewNeedsCustomFields.value,
  include_edges: false
});

const getListQueryCacheStorageKey = () => `${NOTES_LIST_QUERY_CACHE_KEY}:${props.tabId}`;

const readListQueryCache = (request: NoteProgramRequest): ListQueryCachePayload | null => {
  if (typeof window === 'undefined') {
    return null;
  }
  try {
    const raw = window.sessionStorage.getItem(getListQueryCacheStorageKey());
    if (!raw) {
      return null;
    }
    const payload = JSON.parse(raw) as Partial<ListQueryCachePayload>;
    if (
      typeof payload.savedAt !== 'number'
      || !payload.request
      || !payload.data
      || Date.now() - payload.savedAt > NOTES_LIST_QUERY_CACHE_TTL_MS
      || JSON.stringify(payload.request) !== JSON.stringify(request)
    ) {
      return null;
    }
    return payload as ListQueryCachePayload;
  } catch {
    return null;
  }
};

const writeListQueryCache = (request: NoteProgramRequest, data: NoteProgramResponse) => {
  if (typeof window === 'undefined') {
    return;
  }
  try {
    const payload: ListQueryCachePayload = {
      savedAt: Date.now(),
      request,
      data,
    };
    window.sessionStorage.setItem(getListQueryCacheStorageKey(), JSON.stringify(payload));
  } catch {
    // Ignore sessionStorage failures and keep the live query as the source of truth.
  }
};

const hydrateListQueryCache = (request: NoteProgramRequest) => {
  const cached = readListQueryCache(request);
  if (!cached) {
    return false;
  }
  noteStore.applyQueryResponseToTab(props.tabId, cached.request, cached.data);
  if (listPerfEnabled) {
    markBootPerf('notes-list.cacheHydrated', {
      noteCount: cached.data.nodes.length,
      ageMs: Date.now() - cached.savedAt,
    });
  }
  return true;
};

// Computed
const filteredNotes = computed(() => {
  const startedAt = listPerfEnabled ? getPerfNow() : 0;
  const result = applyNoteProgramChannelLocally(noteStore.getTabNotes(props.tabId), viewProgram.value);
  const sorted = [...result].sort((a, b) => b.updated_at - a.updated_at);
  if (listPerfEnabled) {
    const perfVersion = `${session.value?.noteDataVersion ?? 0}:${noteStore.noteRevision}`;
    if (perfVersion !== lastFilteredNotesPerfVersion) {
      lastFilteredNotesPerfVersion = perfVersion;
      markBootPerf('notes-list.filteredNotes.ready', {
        count: sorted.length,
        duration: Math.round((getPerfNow() - startedAt) * 10) / 10,
      });
    }
  }
  return sorted;
});
const visiblePageNotes = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value;
  return filteredNotes.value.slice(start, start + pageSize.value);
});
const visiblePageRows = computed(() => {
  const startedAt = listPerfEnabled ? getPerfNow() : 0;
  const rows = visiblePageNotes.value.map(note => {
    const ui = buildRowUi(note);
    return {
      ...note,
      _ui: ui
    };
  });
  if (listPerfEnabled) {
    const perfVersion = `${session.value?.noteDataVersion ?? 0}:${currentPage.value}:${pageSize.value}`;
    if (perfVersion !== lastVisibleRowsPerfVersion) {
      lastVisibleRowsPerfVersion = perfVersion;
      markBootPerf('notes-list.visibleRows.ready', {
        count: rows.length,
        duration: Math.round((getPerfNow() - startedAt) * 10) / 10,
      });
    }
  }
  return rows;
});
const selectedCount = computed(() => selectedNoteIds.value.length);
const allVisibleSelected = computed(() => (
  visiblePageRows.value.length > 0
  && visiblePageRows.value.every(note => selectedNoteIds.value.includes(noteKey(note.id)))
));
const filteredNotesVersion = computed(() => JSON.stringify([
  session.value?.noteDataVersion ?? 0,
  noteStore.noteRevision,
  normalizeNoteProgramChannel(viewProgram.value),
]));

// Actions
const runDataProgram = async (
  program = getAppliedDataProgram(),
  persist: boolean = false,
  options: { silent?: boolean } = {}
) => {
  if (!options.silent) {
    loading.value = true;
  }
  try {
    const normalizedProgram = normalizeNoteProgramChannel(program);
    const request = buildListQueryRequest(normalizedProgram);
    const startedAt = listPerfEnabled ? getPerfNow() : 0;
    const result = await noteStore.queryNoteProgramForTab(props.tabId, request);
    if (result?.data) {
      writeListQueryCache(request, result.data);
    }
    if (listPerfEnabled) {
      markBootPerf('notes-list.runDataProgram.ready', {
        persist,
        silent: Boolean(options.silent),
        duration: Math.round((getPerfNow() - startedAt) * 10) / 10,
        noteCount: noteStore.getTabNotes(props.tabId).length,
      });
    }
    if (persist) {
      noteStore.updateTabViewState(props.tabId, {
        dataProgram: normalizedProgram
      });
    }
  } finally {
    if (!options.silent) {
      loading.value = false;
    }
  }
};

const applyDataProgram = async () => {
  await runDataProgram(dataProgram.value, true);
  backendFilterExpanded.value = false;
};

const refreshData = async () => {
  await runDataProgram(getAppliedDataProgram(), false);
};

const resetDataProgram = () => {
  dataProgram.value = createDefaultRecentMonthProgram('start_at');
  backendFilterExpanded.value = false;
};

const expandFrontFilter = () => {
  frontFilterExpanded.value = true;
};

const expandBackendFilter = () => {
  backendFilterExpanded.value = true;
};

const applyViewProgram = () => {
  const normalizedProgram = cloneNoteProgramChannel(viewProgram.value);
  viewProgram.value = normalizedProgram;
  frontFilterExpanded.value = true;
  noteStore.updateTabViewState(props.tabId, {
    viewProgram: normalizedProgram
  });
};

const resetViewProgram = () => {
  viewProgram.value = createIncludeAllProgram();
  frontFilterExpanded.value = false;
};

const createNewNote = async () => {
  const now = new Date();
  const title = `${now.getFullYear()}${String(now.getMonth()+1).padStart(2,'0')}${String(now.getDate()).padStart(2,'0')}_${String(now.getHours()).padStart(2,'0')}${String(now.getMinutes()).padStart(2,'0')}`;
  
  const newNote = await noteStore.createNote(
    title,
    '',
    undefined,
    undefined,
    'note',
    'idea',
    [],
    0,
    null,
    null,
    'note',
    [],
    [],
    null
  );
  if (newNote) {
    noteStore.addNoteToTab(props.tabId, newNote.id);
    currentNoteId.value = noteKey(newNote.id);
    ElMessage.success('创建成功');
  }
};

const handleCurrentChange = (val: NoteNode | undefined) => {
  if (val) {
    currentNoteId.value = noteKey(val.id);
  }
};

const handleSelectionChange = (rows: NoteNode[]) => {
  selectedNoteIds.value = rows.map(row => noteKey(row.id));
};

const selectAllVisible = async () => {
  await nextTick();
  visiblePageRows.value.forEach(note => {
    tableRef.value?.toggleRowSelection(note, true);
  });
};

const clearSelection = () => {
  tableRef.value?.clearSelection();
  selectedNoteIds.value = [];
};

const handleBatchEditSaved = (result: { updated_count: number }) => {
  clearSelection();
  if (result.updated_count > 0) {
    ElMessage.success(`已更新 ${result.updated_count} 个节点`);
  } else {
    ElMessage.info('没有需要更新的节点');
  }
};

const handleNoteUpdate = (note: NoteNode) => {
  // Store updates automatically
};

const handleNoteDelete = (id: string) => {
  if (currentNoteId.value === id) {
    currentNoteId.value = '';
  }
};

const handleNoteCreate = (note: NoteNode) => {
  noteStore.addNoteToTab(props.tabId, note.id);
  currentNoteId.value = noteKey(note.id);
};

// Helpers
const formatDate = (ts: number) => {
  return formatNoteDateTime(ts);
};

const padDatePart = (value: number) => String(value).padStart(2, '0');
const getMonthKey = (timestamp: number) => {
  if (!timestamp) return '';
  const date = new Date(timestamp);
  return `${date.getFullYear()}-${padDatePart(date.getMonth() + 1)}`;
};

const getStartAtBadgeStyle = (timestamp: number) => {
  return getStableBadgeStyle(getMonthKey(timestamp));
};

const mixHexWithWhite = (hex: string, ratio: number) => {
  const normalized = hex.trim().replace(/^#/, '');
  if (!/^[0-9a-fA-F]{6}$/.test(normalized)) return hex;

  const amount = Math.min(1, Math.max(0, ratio));
  const mixChannel = (channel: number) => Math.round(channel * (1 - amount) + 255 * amount);
  const r = Number.parseInt(normalized.slice(0, 2), 16);
  const g = Number.parseInt(normalized.slice(2, 4), 16);
  const b = Number.parseInt(normalized.slice(4, 6), 16);

  return `#${[mixChannel(r), mixChannel(g), mixChannel(b)]
    .map(channel => channel.toString(16).padStart(2, '0'))
    .join('')}`.toUpperCase();
};

const getTitleColor = (themeBaseColor: string, statusId: string) => {
  if (statusId === 'done') {
    // Keep the hue family but avoid reusing the full fill color on plain text.
    return mixHexWithWhite(themeBaseColor, 0.32);
  }

  return themeBaseColor;
};

const getCategoryLabel = (type: string | null) => type ? getNodeTypeConfig(type).label : '-';
const getTitleStyle = (themeBaseColor: string, statusId: string) => {
    return {
        color: getTitleColor(themeBaseColor, statusId),
        fontWeight: '500',
        display: 'flex',
        alignItems: 'center',
        gap: '4px',
        minWidth: 0
    };
};

const getTypeTagStyle = (color: string) => {
    return {
        color,
        fontWeight: 'bold',
        backgroundColor: 'transparent',
        border: 'none',
        padding: '0'
    };
};

const getLifecycleStageLabel = (status: string | null) => getNodeStatusConfig(status || 'idea').label;
const buildRowUi = (note: NoteNode) => {
  const noteTypeKey = note.primary_category ?? note.node_type;
  const noteTypeAssignments = note.note_categories ?? note.note_types;
  const lifecycleStage = note.lifecycle_stage ?? note.node_status;
  const statusConfig = getNodeStatusConfig(lifecycleStage || 'idea');
  const noteTheme = getNodeTheme(noteTypeKey, note.color, noteTypeAssignments);
  const statusStyle = getNodeDisplayStyleFromTheme(
    noteTheme,
    lifecycleStage,
    resolveCompletionProgressFillRatio({
      lifecycleStage,
      completionProgress: note.completion_progress,
      completionProgressExpr: note.completion_progress_expr,
      customFields: note.custom_fields,
    })
  );
  const ratio = statusStyle.partialFillRatio;

  return {
    titleStyle: getTitleStyle(noteTheme.baseColor, statusConfig.id),
    typeTagStyle: getTypeTagStyle(statusStyle.color),
    categoryLabel: getCategoryLabel(note.primary_category),
    noteFormLabel: getNoteFormConfig(note.note_form).label,
    lifecycleStageLabel: statusConfig.label,
    statusStyle,
    useSplitStatusBadge: typeof ratio === 'number' && ratio > 0 && ratio < 1,
    statusFillLayerStyle: {
      color: statusStyle.fillTextColor,
      clipPath: `inset(0 ${(100 - (ratio ?? 0) * 100).toFixed(2)}% 0 0)`
    },
    statusEmptyLayerStyle: {
      color: statusStyle.emptyTextColor,
      clipPath: `inset(0 0 0 ${((ratio ?? 0) * 100).toFixed(2)}%)`
    },
    startAtBadgeStyle: getStartAtBadgeStyle(note.start_at),
    formattedStartAt: formatDate(note.start_at),
    privateLevelLabel: note.private_level > 0 ? `开(${note.private_level})` : '关',
    privateLevelClass: note.private_level > 0 ? 'is-danger' : 'is-info',
  };
};

const calculateListBounds = () => {
    const vh = window.innerHeight;
    const reservedHeight = 260;
    const availableHeight = vh - reservedHeight;
    const minEditorHeight = 200;

    return {
        adaptiveHeight: Math.max(300, Math.floor(availableHeight * 0.5)),
        maxHeight: Math.max(200, availableHeight - minEditorHeight),
    };
};

const {
    paneHeight: listHeight,
    startResizing,
} = useResizablePane({
    initialHeight: 400,
    getAdaptiveHeight: () => calculateListBounds().adaptiveHeight,
    getResizeBounds: () => ({
        min: 200,
        max: calculateListBounds().maxHeight,
    }),
});

const currentListQueryIncludesCustomFields = () => {
  const lastQuery = session.value?.lastQuery as { result?: { include_custom_fields?: boolean } } | null | undefined;
  return Boolean(lastQuery?.result?.include_custom_fields);
};

onMounted(() => {
  const hasCachedNotes = noteStore.getTabNotes(props.tabId).length > 0;
  const queryRequest = buildListQueryRequest();
  const hydratedFromCache = !hasCachedNotes && hydrateListQueryCache(queryRequest);
  if (listPerfEnabled) {
    markBootPerf('notes-list.mounted', {
      hasCachedNotes,
      hydratedFromCache,
      needsCustomFields: listViewNeedsCustomFields.value,
    });
  }
  const paletteStartedAt = listPerfEnabled ? getPerfNow() : 0;
  ensureNoteTypePaletteLoaded().catch(error => {
    console.warn('Failed to load note category palette:', error);
  }).finally(() => {
    if (listPerfEnabled) {
      markBootPerf('notes-list.palette.ready', {
        duration: Math.round((getPerfNow() - paletteStartedAt) * 10) / 10,
      });
    }
  });
  if (!hasCachedNotes || (listViewNeedsCustomFields.value && !currentListQueryIncludesCustomFields())) {
    void runDataProgram(getAppliedDataProgram(), false, {
      silent: hydratedFromCache,
    });
  }
});

watch(viewProgram, (value) => {
  if (!isIncludeAllProgram(value)) {
    frontFilterExpanded.value = true;
  }
  noteStore.updateTabViewState(props.tabId, {
    viewProgram: normalizeNoteProgramChannel(value)
  });
  if (isActive.value && listViewNeedsCustomFields.value && !currentListQueryIncludesCustomFields()) {
    void refreshData();
  }
}, { deep: true });

const clampCurrentPage = () => {
  const maxPage = Math.max(1, Math.ceil(filteredNotes.value.length / pageSize.value));
  if (currentPage.value > maxPage) {
    currentPage.value = maxPage;
  }
};

const pruneSelectionToVisibleNotes = async () => {
  if (!isActive.value) return;
  if (selectedNoteIds.value.length === 0) return;
  const visibleNoteIds = new Set(filteredNotes.value.map(note => noteKey(note.id)));
  const nextSelectedIds = selectedNoteIds.value.filter(id => visibleNoteIds.has(id));
  if (nextSelectedIds.length === selectedNoteIds.value.length) return;
  selectedNoteIds.value = nextSelectedIds;
  await nextTick();
  if (nextSelectedIds.length === 0) {
    tableRef.value?.clearSelection();
  }
};

watch([filteredNotesVersion, pageSize], () => {
  if (!isActive.value) return;
  clampCurrentPage();
});

watch(filteredNotesVersion, async () => {
  await pruneSelectionToVisibleNotes();
});

watch([loading, visiblePageRows], async ([nextLoading, rows]) => {
  if (!listPerfEnabled || initialRowsMarked) return;
  if (nextLoading || rows.length === 0) return;
  await nextTick();
  if (initialRowsMarked || loading.value || visiblePageRows.value.length === 0) return;
  initialRowsMarked = true;
  markBootPerf('notes-list.initialRows.painted', {
    count: visiblePageRows.value.length,
  });
}, { deep: false });

watch(isActive, async (active) => {
  if (!active) return;
  if (listViewNeedsCustomFields.value && !currentListQueryIncludesCustomFields()) {
    await refreshData();
  }
  clampCurrentPage();
  await pruneSelectionToVisibleNotes();
});

</script>

<style scoped>
.list-notes-layout {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background-color: #fff;
  overflow: hidden;
}

.filter-section {
  padding: 16px 20px 12px;
  background-color: #fff;
  border-bottom: 1px solid #ebeef5;
  flex-shrink: 0;
}

.front-filter-section {
  padding-top: 0;
}

.front-filter-collapsed {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border: 1px solid #e4e7ed;
  border-radius: 10px;
  background: linear-gradient(180deg, #fbfcfe 0%, #f5f7fa 100%);
}
.front-filter-collapsed__meta {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.front-filter-collapsed__title {
  font-size: 13px;
  font-weight: 600;
  color: #303133;
}

.front-filter-collapsed__summary {
  font-size: 12px;
  color: #606266;
}

.notes-workspace {
  flex: 1;
  min-height: 0;
}

.toolbar-section {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  padding: 0 20px 12px;
  background-color: #fff;
  border-bottom: 1px solid #ebeef5;
  flex-shrink: 0;
  flex-wrap: wrap;
}

.bulk-actions {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}

.toolbar-actions {
  display: flex;
  gap: 10px;
  align-items: center;
}

.list-container {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.list-summary-bar {
  display: flex;
  gap: 12px;
  align-items: center;
  padding: 8px 12px;
  color: #606266;
  font-size: 12px;
  border-bottom: 1px solid #ebeef5;
  flex-shrink: 0;
}

.notes-table {
  width: 100%;
  flex: 1;
  min-height: 0;
}

.list-pagination-bar {
  display: flex;
  justify-content: flex-end;
  padding: 8px 12px;
  border-top: 1px solid #ebeef5;
  flex-shrink: 0;
}

.note-title {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
  font-weight: 500;
  color: #303133;
}

.note-title-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.node-type-text {
  font-size: 13px;
  /* No badge styling, just text */
}

.form-badge-text {
  display: inline-flex;
  align-items: center;
  font-size: 12px;
  line-height: 1.2;
  white-space: nowrap;
}

.private-level-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 30px;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 12px;
  line-height: 1.2;
  white-space: nowrap;
}

.private-level-badge.is-info {
  color: #606266;
  background: #f4f4f5;
}

.private-level-badge.is-danger {
  color: #f56c6c;
  background: #fef0f0;
}

.start-at-badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border: 1px solid transparent;
  border-radius: 999px;
  line-height: 20px;
  white-space: nowrap;
}

.node-badge {
  display: inline-block;
  padding: 0 8px;
  font-size: 12px;
  line-height: 20px;
  border-radius: 4px;
  box-sizing: border-box;
  white-space: nowrap;
  transition: all 0.2s;
  text-align: center;
  min-width: 40px; /* Optional: ensures minimum width for very short labels */
}

.node-badge--split {
  display: grid;
  overflow: hidden;
}

.node-badge-layer {
  grid-area: 1 / 1;
}
.collapsed-filter-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}
</style>
