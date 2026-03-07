<template>
  <div class="list-notes-layout">
    <div class="filter-section">
      <NoteProgramBar
        v-model="dataProgram"
        title="后端筛选"
        help-text="决定从后端加载哪些节点，点击“执行”后生效并保存；规则按顺序执行，后面的包含/排除可以覆盖前面的结果。"
        hint-text=""
        apply-text="执行"
        reset-text="恢复默认"
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
        <el-button size="small" @click="selectAllVisible" :disabled="filteredNotes.length === 0 || allVisibleSelected">全选当前可见</el-button>
        <el-button size="small" @click="clearSelection">清空选择</el-button>
        <el-button size="small" type="danger" plain @click="applyPrivateLevelToSelection(1)">设为私密</el-button>
        <el-button size="small" plain @click="applyPrivateLevelToSelection(0)">取消私密</el-button>
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
          <el-table
            ref="tableRef"
            v-loading="loading"
            :data="filteredNotes"
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
                <span class="note-title" :style="getTitleStyle(row.node_type)">{{ row.title || '无标题' }}</span>
              </template>
            </el-table-column>

            <el-table-column prop="node_type" label="类型" width="100" sortable>
              <template #default="{ row }">
                <span
                  class="node-type-text"
                  :style="getTypeTagStyle(row.node_type)"
                >
                  {{ getTypeLabel(row.node_type) }}
                </span>
              </template>
            </el-table-column>

            <el-table-column prop="node_status" label="状态" width="100" sortable>
              <template #default="{ row }">
                <span
                  class="node-badge"
                  :style="getStatusBadgeStyle(row.node_status)"
                >
                  {{ getStatusLabel(row.node_status) }}
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

            <el-table-column prop="start_at" label="起始时间" width="160" sortable>
              <template #default="{ row }">
                {{ formatDate(row.start_at) }}
              </template>
            </el-table-column>

            <el-table-column prop="updated_at" label="更新时间" width="160" sortable>
              <template #default="{ row }">
                {{ formatDate(row.updated_at) }}
              </template>
            </el-table-column>

            <el-table-column prop="created_at" label="创建时间" width="160" sortable>
              <template #default="{ row }">
                {{ formatDate(row.created_at) }}
              </template>
            </el-table-column>
          </el-table>
        </div>
      </template>

      <template #editor>
        <NoteDetailPanel
          :noteId="currentNoteId"
          @update="handleNoteUpdate"
          @delete="handleNoteDelete"
          @create="handleNoteCreate"
        />
      </template>
    </NoteSplitView>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch, nextTick } from 'vue';
import {
  useNoteStore,
  type NoteNode,
  applyNoteProgramChannelLocally,
  buildScanNoteProgramRequest,
  cloneNoteProgramChannel,
  createDefaultRecentMonthProgram,
  createIncludeAllProgram,
  normalizeNoteProgramChannel
} from '@/api/notes';
import { Plus, Refresh } from '@element-plus/icons-vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import NoteDetailPanel from '@/components/NoteDetailPanel.vue';
import NoteSplitView from '@/components/NoteSplitView.vue';
import NoteProgramBar from '@/components/NoteProgramBar.vue';
import { getNodeTypeConfig, getNodeStatusConfig, getNodeStyle } from '@/utils/nodeConfig';
import { formatNoteDateTime } from '@/utils/noteDate';
import { useResizablePane } from '@/utils/useResizablePane';

const noteStore = useNoteStore();
const props = defineProps<{
  tabId: string;
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

// Computed
const filteredNotes = computed(() => {
  const result = applyNoteProgramChannelLocally(noteStore.getTabNotes(props.tabId), viewProgram.value);
  return [...result].sort((a, b) => b.updated_at - a.updated_at);
});
const visibleNoteIds = computed(() => new Set(filteredNotes.value.map(note => note.id)));
const selectedCount = computed(() => selectedNoteIds.value.length);
const allVisibleSelected = computed(() => (
  filteredNotes.value.length > 0
  && filteredNotes.value.every(note => selectedNoteIds.value.includes(note.id))
));

// Actions
const runDataProgram = async (program = getAppliedDataProgram(), persist: boolean = false) => {
  loading.value = true;
  try {
    const normalizedProgram = normalizeNoteProgramChannel(program);
    await noteStore.queryNoteProgramForTab(props.tabId, buildScanNoteProgramRequest(normalizedProgram, {
      limit: 1000,
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
    currentNoteId.value = newNote.id;
    ElMessage.success('创建成功');
  }
};

const handleCurrentChange = (val: NoteNode | undefined) => {
  if (val) {
    currentNoteId.value = val.id;
  }
};

const handleSelectionChange = (rows: NoteNode[]) => {
  selectedNoteIds.value = rows.map(row => row.id);
};

const selectAllVisible = async () => {
  await nextTick();
  filteredNotes.value.forEach(note => {
    tableRef.value?.toggleRowSelection(note, true);
  });
};

const clearSelection = () => {
  tableRef.value?.clearSelection();
  selectedNoteIds.value = [];
};

const applyPrivateLevelToSelection = async (privateLevel: number) => {
  if (selectedNoteIds.value.length === 0) return;

  const actionLabel = privateLevel > 0 ? '设为私密' : '取消私密';
  const confirmed = await ElMessageBox.confirm(
    `确定要将已选中的 ${selectedNoteIds.value.length} 个节点${actionLabel}吗？`,
    '批量操作确认',
    {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning'
    }
  ).catch(() => false);
  if (!confirmed) return;

  const result = await noteStore.batchUpdateNotes({
    ids: [...selectedNoteIds.value],
    patch: { private_level: privateLevel }
  });

  if (!result) return;

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
    // Handled by store
};

// Helpers
const formatDate = (ts: number) => {
  return formatNoteDateTime(ts);
};

const getTypeLabel = (type: string | null) => getNodeTypeConfig(type || 'note').label;
const getTitleStyle = (type: string | null) => {
    const config = getNodeTypeConfig(type || 'note');
    return {
        color: config.baseColor,
        fontWeight: '500'
    };
};

const getTypeTagStyle = (type: string | null) => {
    const config = getNodeTypeConfig(type || 'note');
    return {
        color: config.baseColor,
        fontWeight: 'bold',
        backgroundColor: 'transparent',
        border: 'none',
        padding: '0'
    };
};

const getStatusLabel = (status: string | null) => getNodeStatusConfig(status || 'idea').label;
const getStatusBadgeStyle = (status: string | null) => {
    // Pass 'note' as type to ensure status style is orthogonal (independent of the actual node type)
    // This will use the default gray/neutral color scheme for borders/backgrounds
    return getNodeStyle('note', status);
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

onMounted(() => {
  if (noteStore.getTabNotes(props.tabId).length === 0) {
    refreshData();
  }
});

watch(viewProgram, (value) => {
  noteStore.updateTabViewState(props.tabId, {
    viewProgram: normalizeNoteProgramChannel(value)
  });
}, { deep: true });

watch(filteredNotes, async () => {
  const nextSelectedIds = selectedNoteIds.value.filter(id => visibleNoteIds.value.has(id));
  if (nextSelectedIds.length === selectedNoteIds.value.length) return;
  selectedNoteIds.value = nextSelectedIds;
  await nextTick();
  if (nextSelectedIds.length === 0) {
    tableRef.value?.clearSelection();
  }
}, { deep: true });

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

.notes-table {
  width: 100%;
  height: 100%;
}

.note-title {
  font-weight: 500;
  color: #303133;
}

.node-type-text {
  font-size: 13px;
  /* No badge styling, just text */
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
</style>
