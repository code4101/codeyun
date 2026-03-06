<template>
  <div class="list-notes-layout">
    <!-- Top: Filter & Actions -->
    <div class="filter-section">
      <div class="filters">
        <el-input
          v-model="searchKeyword"
          placeholder="搜索标题..."
          :prefix-icon="Search"
          clearable
          style="width: 200px"
        />
        
        <el-select v-model="filterType" placeholder="所有类型" clearable style="width: 120px">
          <el-option
            v-for="type in orderedNodeTypes"
            :key="type.id"
            :label="type.label"
            :value="type.id"
          />
        </el-select>

        <el-select v-model="filterStatus" placeholder="所有状态" clearable style="width: 120px">
          <el-option
            v-for="status in orderedNodeStatuses"
            :key="status.id"
            :label="status.label"
            :value="status.id"
          />
        </el-select>

        <el-input-number 
          v-model="filterMinWeight" 
          placeholder="最小权重" 
          controls-position="right"
          style="width: 100px"
          :min="0"
        />
      </div>
      
      <div class="actions">
        <el-button type="primary" :icon="Plus" @click="createNewNote">新建节点</el-button>
        <el-button :icon="Refresh" @click="refreshData">刷新</el-button>
      </div>
    </div>

    <!-- Middle: Table List -->
    <div class="list-container" :style="{ height: listHeight + 'px' }">
      <el-table
        v-loading="loading"
        :data="filteredNotes"
        style="width: 100%; height: 100%"
        highlight-current-row
        @current-change="handleCurrentChange"
        row-key="id"
        border
        size="small"
      >
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

      <!-- Resizer -->
      <div class="list-resizer" @mousedown="startResizing">
          <div class="resizer-indicator"></div>
      </div>
    </div>

    <!-- Bottom: Editor -->
    <div class="editor-section" :class="{ 'is-collapsed': !currentNoteId }">
      <NoteDetailPanel 
        v-if="currentNoteId" 
        :noteId="currentNoteId" 
        class="editor-wrapper"
        @update="handleNoteUpdate"
        @delete="handleNoteDelete"
        @create="handleNoteCreate"
      />
      <div v-else class="empty-state">
        <el-empty description="请选择一个节点进行编辑" />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue';
import { useNoteStore, type NoteNode } from '@/api/notes';
import { Search, Plus, Refresh, Delete } from '@element-plus/icons-vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import NoteDetailPanel from '@/components/NoteDetailPanel.vue';
import { 
    getNodeTypeConfig, 
    getNodeStatusConfig,
    getOrderedNodeTypes,
    getOrderedNodeStatuses,
    getNodeStyle
} from '@/utils/nodeConfig';

const noteStore = useNoteStore();

// State
const searchKeyword = ref('');
const filterType = ref('');
const filterStatus = ref('');
const filterMinWeight = ref<number | undefined>(undefined);
const currentNoteId = ref('');
const loading = ref(false);

// Configs
const orderedNodeTypes = computed(() => getOrderedNodeTypes());
const orderedNodeStatuses = computed(() => getOrderedNodeStatuses());

// Layout State
const listHeight = ref(400);
const isResizing = ref(false);
const startY = ref(0);
const startHeight = ref(0);

// Computed
const filteredNotes = computed(() => {
  let result = noteStore.notes;

  if (searchKeyword.value) {
    const k = searchKeyword.value.toLowerCase();
    result = result.filter(n => n.title.toLowerCase().includes(k));
  }

  if (filterType.value) {
    result = result.filter(n => (n.node_type || 'note') === filterType.value);
  }

  if (filterStatus.value) {
    result = result.filter(n => (n.node_status || 'idea') === filterStatus.value);
  }

  if (filterMinWeight.value !== undefined && filterMinWeight.value !== null) {
    result = result.filter(n => (n.weight || 0) >= filterMinWeight.value!);
  }

  // Default Sort by Updated At Desc if not handled by table sort
  // Actually table sort handles it visually, but initial order matters
  return [...result].sort((a, b) => b.updated_at - a.updated_at);
});

// Actions
const refreshData = async () => {
  loading.value = true;
  try {
    await noteStore.fetchNotes({ limit: 1000 });
  } finally {
    loading.value = false;
  }
};

const createNewNote = async () => {
  const now = new Date();
  const title = `${now.getFullYear()}${String(now.getMonth()+1).padStart(2,'0')}${String(now.getDate()).padStart(2,'0')}_${String(now.getHours()).padStart(2,'0')}${String(now.getMinutes()).padStart(2,'0')}`;
  
  const newNote = await noteStore.createNote(title, '');
  if (newNote) {
    currentNoteId.value = newNote.id;
    ElMessage.success('创建成功');
  }
};

const handleCurrentChange = (val: NoteNode | undefined) => {
  if (val) {
    currentNoteId.value = val.id;
  }
};

const handleDelete = (note: NoteNode) => {
  ElMessageBox.confirm(`确定要删除 "${note.title}" 吗？`, '警告', {
    confirmButtonText: '删除',
    cancelButtonText: '取消',
    type: 'warning'
  }).then(async () => {
    await noteStore.deleteNote(note.id);
    if (currentNoteId.value === note.id) {
      currentNoteId.value = '';
    }
    ElMessage.success('删除成功');
  }).catch(() => {});
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
  if (!ts) return '-';
  return new Date(ts).toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  });
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

// Layout Logic
const calculateOptimalHeight = () => {
    const vh = window.innerHeight;
    const reservedHeight = 160; // Header + Filters
    // Default split 50/50
    return Math.max(300, Math.floor((vh - reservedHeight) * 0.5));
};

const updateAdaptiveHeight = () => {
    // Only update if not manually resized? Or always responsive?
    // Let's stick to initial responsive then manual override
    if (!isResizing.value && startHeight.value === 0) {
        listHeight.value = calculateOptimalHeight();
    }
};

const startResizing = (e: MouseEvent) => {
    isResizing.value = true;
    startY.value = e.clientY;
    startHeight.value = listHeight.value;
    
    window.addEventListener('mousemove', handleResizing);
    window.addEventListener('mouseup', stopResizing);
    document.body.style.userSelect = 'none';
};

const handleResizing = (e: MouseEvent) => {
    if (!isResizing.value) return;
    const delta = e.clientY - startY.value;
    const vh = window.innerHeight;
    const reservedHeight = 160;
    const availableHeight = vh - reservedHeight;
    const minEditorHeight = 200;
    
    const newHeight = Math.max(200, Math.min(availableHeight - minEditorHeight, startHeight.value + delta));
    listHeight.value = newHeight;
};

const stopResizing = () => {
    isResizing.value = false;
    window.removeEventListener('mousemove', handleResizing);
    window.removeEventListener('mouseup', stopResizing);
    document.body.style.userSelect = '';
};

onMounted(() => {
  if (noteStore.notes.length === 0) {
    refreshData();
  }
  updateAdaptiveHeight();
  window.addEventListener('resize', updateAdaptiveHeight);
});

onUnmounted(() => {
    window.removeEventListener('resize', updateAdaptiveHeight);
});

</script>

<style scoped>
.list-notes-layout {
  display: flex;
  flex-direction: column;
  height: 100%;
  background-color: #fff;
  overflow: hidden;
}

.filter-section {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 20px;
  background-color: #f5f7fa;
  border-bottom: 1px solid #e6e6e6;
  flex-shrink: 0;
}

.filters {
  display: flex;
  gap: 10px;
}

.actions {
  display: flex;
  gap: 10px;
}

.list-container {
  position: relative;
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
}

.list-resizer {
  height: 8px;
  width: 100%;
  background-color: #f5f7fa;
  cursor: ns-resize;
  position: absolute;
  bottom: 0;
  left: 0;
  display: flex;
  justify-content: center;
  align-items: center;
  z-index: 10;
  border-top: 1px solid #e6e6e6;
  transition: background-color 0.2s;
}

.list-resizer:hover {
  background-color: #ecf5ff;
}

.resizer-indicator {
  width: 40px;
  height: 4px;
  border-top: 1px solid #dcdfe6;
  border-bottom: 1px solid #dcdfe6;
}

.editor-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  background-color: #fff;
  min-height: 0;
  overflow: hidden;
}

.editor-wrapper {
  padding: 20px;
  height: 100%;
  overflow-y: auto;
  box-sizing: border-box;
}

.empty-state {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 100%;
  color: #909399;
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
