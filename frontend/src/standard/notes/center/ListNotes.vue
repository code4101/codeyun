<template>
  <div class="list-notes-layout">
    <div class="filter-section">
      <NoteProgramBar
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
      <NoteProgramBar
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
            <span v-if="filteredNotes.length > visiblePageNotes.length">
              当前显示 {{ visiblePageNotes.length }} 条
            </span>
          </div>
          <el-table
            ref="tableRef"
            v-loading="loading"
            :data="visiblePageNotes"
            class="notes-table"
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
                <span class="note-title" :style="getTitleStyle(row)">
                  <NoteFormBadge :form="row.note_form" compact />
                  <span class="note-title-text">{{ row.title || '无标题' }}</span>
                </span>
              </template>
            </el-table-column>

            <el-table-column prop="primary_category" label="分类" width="100" sortable>
              <template #default="{ row }">
                <span
                  class="node-type-text"
                  :style="getTypeTagStyle(row)"
                >
                  {{ getCategoryLabel(row.primary_category) }}
                </span>
              </template>
            </el-table-column>

            <el-table-column prop="note_form" label="形态" width="88" sortable>
              <template #default="{ row }">
                <span class="form-badge-wrap">
                  <NoteFormBadge :form="row.note_form" :show-label="true" />
                </span>
              </template>
            </el-table-column>

            <el-table-column prop="lifecycle_stage" label="阶段" width="100" sortable>
              <template #default="{ row }">
                <span
                  v-if="useSplitStatusBadge(row)"
                  class="node-badge node-badge--split"
                  :style="getStatusBadgeStyle(row)"
                >
                  <span class="node-badge-layer" :style="getStatusBadgeSplitLayerStyle(row, 'fill')">
                    {{ getLifecycleStageLabel(row.lifecycle_stage) }}
                  </span>
                  <span class="node-badge-layer" :style="getStatusBadgeSplitLayerStyle(row, 'empty')">
                    {{ getLifecycleStageLabel(row.lifecycle_stage) }}
                  </span>
                </span>
                <span
                  v-else
                  class="node-badge"
                  :style="getStatusBadgeStyle(row)"
                >
                  {{ getLifecycleStageLabel(row.lifecycle_stage) }}
                </span>
              </template>
            </el-table-column>

            <el-table-column prop="weight" label="权重" width="80" sortable />

            <el-table-column prop="private_level" label="私密" width="88" sortable>
              <template #default="{ row }">
                <el-tag :type="row.private_level > 0 ? 'danger' : 'info'" size="small">
                  {{ row.private_level > 0 ? `开(${row.private_level})` : '关' }}
                </el-tag>
              </template>
            </el-table-column>

            <el-table-column prop="start_at" label="起始时间" width="176" sortable>
              <template #default="{ row }">
                <span class="start-at-badge" :style="getStartAtBadgeStyle(row.start_at)">
                  {{ formatDate(row.start_at) }}
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
import NoteProgramBar from '@/components/NoteProgramBar.vue';
import NoteFormBadge from '@/components/NoteFormBadge.vue';
import StandardPagination from '@/components/StandardPagination.vue';
import { getNodeDisplayStyle, getNodeTheme, getNodeTypeConfig, getNodeStatusConfig } from '@/utils/nodeConfig';
import { formatNoteDateTime } from '@/utils/noteDate';
import { useResizablePane } from '@/utils/useResizablePane';
import { resolveCompletionProgressFillRatio } from '@/utils/noteProgress';
import { getStableBadgeStyle } from '@/utils/stableVisualColor';

const noteStore = useNoteStore();
const NoteDetailPanel = defineAsyncComponent(() => import('@/components/NoteDetailPanel.vue'));
const BatchNoteEditDialog = defineAsyncComponent(() => import('@/components/BatchNoteEditDialog.vue'));
const props = defineProps<{
  tabId: string;
  active?: boolean;
}>();

const session = computed(() => noteStore.getTabSession(props.tabId));
const getAppliedDataProgram = () => normalizeNoteProgramChannel(
  session.value?.viewState.dataProgram ?? createDefaultRecentMonthProgram('start_at')
);
const getViewProgram = () => normalizeNoteProgramChannel(
  session.value?.viewState.viewProgram ?? createIncludeAllProgram()
);

// State
const dataProgram = ref(normalizeNoteProgramChannel(
  getAppliedDataProgram()
));
const viewProgram = ref(normalizeNoteProgramChannel(
  getViewProgram()
));
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

// Computed
const filteredNotes = computed(() => {
  const result = applyNoteProgramChannelLocally(noteStore.getTabNotes(props.tabId), viewProgram.value);
  return [...result].sort((a, b) => b.updated_at - a.updated_at);
});
const visiblePageNotes = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value;
  return filteredNotes.value.slice(start, start + pageSize.value);
});
const selectedCount = computed(() => selectedNoteIds.value.length);
const allVisibleSelected = computed(() => (
  visiblePageNotes.value.length > 0
  && visiblePageNotes.value.every(note => selectedNoteIds.value.includes(noteKey(note.id)))
));
const filteredNotesVersion = computed(() => JSON.stringify([
  session.value?.noteDataVersion ?? 0,
  noteStore.noteRevision,
  normalizeNoteProgramChannel(viewProgram.value),
]));

// Actions
const runDataProgram = async (program = getAppliedDataProgram(), persist: boolean = false) => {
  loading.value = true;
  try {
    const normalizedProgram = normalizeNoteProgramChannel(program);
    await noteStore.queryNoteProgramForTab(props.tabId, buildScanNoteProgramRequest(normalizedProgram, {
      limit: 1000,
      include_custom_fields: listViewNeedsCustomFields.value,
      include_edges: false
    }));
    if (persist) {
      noteStore.updateTabViewState(props.tabId, {
        dataProgram: normalizedProgram
      });
    }
  } finally {
    loading.value = false;
  }
};

const applyDataProgram = async () => {
  await runDataProgram(dataProgram.value, true);
};

const refreshData = async () => {
  await runDataProgram(getAppliedDataProgram(), false);
};

const resetDataProgram = () => {
  dataProgram.value = createDefaultRecentMonthProgram('start_at');
};

const applyViewProgram = () => {
  const normalizedProgram = cloneNoteProgramChannel(viewProgram.value);
  viewProgram.value = normalizedProgram;
  noteStore.updateTabViewState(props.tabId, {
    viewProgram: normalizedProgram
  });
};

const resetViewProgram = () => {
  viewProgram.value = createIncludeAllProgram();
};

const createNewNote = async () => {
  const now = new Date();
  const title = `${now.getFullYear()}${String(now.getMonth()+1).padStart(2,'0')}${String(now.getDate()).padStart(2,'0')}_${String(now.getHours()).padStart(2,'0')}${String(now.getMinutes()).padStart(2,'0')}`;
  
  const newNote = await noteStore.createNote(title, '');
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
  visiblePageNotes.value.forEach(note => {
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

const getTitleColor = (note: NoteNode) => {
  const theme = getNodeTheme(
    note.primary_category ?? note.node_type,
    note.color,
    note.note_categories ?? note.note_types
  );
  const statusId = getNodeStatusConfig(note.lifecycle_stage ?? note.node_status ?? 'idea').id;

  if (statusId === 'done') {
    // Keep the hue family but avoid reusing the full fill color on plain text.
    return mixHexWithWhite(theme.baseColor, 0.32);
  }

  return theme.baseColor;
};

const getCategoryLabel = (type: string | null) => getNodeTypeConfig(type || 'general').label;
const getTitleStyle = (note: NoteNode) => {
    return {
        color: getTitleColor(note),
        fontWeight: '500',
        display: 'flex',
        alignItems: 'center',
        gap: '4px',
        minWidth: 0
    };
};

const getTypeTagStyle = (note: NoteNode) => {
    const config = getNodeDisplayStyle(note.primary_category ?? note.node_type, 'idea', note.color, note.note_categories ?? note.note_types);
    return {
        color: config.color,
        fontWeight: 'bold',
        backgroundColor: 'transparent',
        border: 'none',
        padding: '0'
    };
};

const getLifecycleStageLabel = (status: string | null) => getNodeStatusConfig(status || 'idea').label;
const getStatusBadgeStyle = (note: NoteNode) => {
    return getNodeDisplayStyle(
      note.primary_category ?? note.node_type,
      note.lifecycle_stage ?? note.node_status,
      note.color,
      note.note_categories ?? note.note_types,
      resolveCompletionProgressFillRatio({
        lifecycleStage: note.lifecycle_stage ?? note.node_status,
        completionProgress: note.completion_progress,
        completionProgressExpr: note.completion_progress_expr,
        customFields: note.custom_fields,
      })
    );
};

const useSplitStatusBadge = (note: NoteNode) => {
  const ratio = getStatusBadgeStyle(note).partialFillRatio;
  return typeof ratio === 'number' && ratio > 0 && ratio < 1;
};

const getStatusBadgeSplitLayerStyle = (note: NoteNode, mode: 'fill' | 'empty') => {
  const style = getStatusBadgeStyle(note);
  const ratio = style.partialFillRatio ?? 0;
  return {
    color: mode === 'fill' ? style.fillTextColor : style.emptyTextColor,
    clipPath: mode === 'fill'
      ? `inset(0 ${(100 - ratio * 100).toFixed(2)}% 0 0)`
      : `inset(0 0 0 ${(ratio * 100).toFixed(2)}%)`
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
  if (!hasCachedNotes || (listViewNeedsCustomFields.value && !currentListQueryIncludesCustomFields())) {
    void refreshData();
  }
});

watch(viewProgram, (value) => {
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

.form-badge-wrap {
  display: inline-flex;
  align-items: center;
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
</style>
