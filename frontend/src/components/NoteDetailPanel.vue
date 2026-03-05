<template>
  <div class="note-detail-panel">
    <div v-if="!props.noteId" class="empty-state">
      <el-empty description="未选择节点" />
    </div>
    <div v-else-if="isFetchingContent || !isReady" class="loading-placeholder">
      <el-icon class="is-loading"><Loading /></el-icon> 加载内容中...
    </div>
    <div v-else class="panel-content">
      <div class="editor-header">
        <!-- Row 1: Title and Actions -->
        <div class="header-row-primary">
            <el-input 
              v-model="currentNote.title" 
              placeholder="节点标题" 
              class="title-input" 
              @input="onTitleChange" 
            />
            <el-button type="primary" plain text circle :icon="CopyDocument" @click="showCopyDialog = true" title="复制节点"></el-button>
            <el-button type="danger" plain text circle :icon="Delete" @click="deleteCurrentNote" title="删除节点"></el-button>
            <slot name="actions"></slot>
        </div>
        
        <!-- Row 2: Meta Info and Save Status -->
        <div class="header-row-secondary">
            <div class="meta-group">
                <span class="time-tag">
                  <el-icon><Calendar /></el-icon> 起始: 
                  <el-date-picker
                      v-model="startDateProxy"
                      type="date"
                      placeholder="日期"
                      size="small"
                      :clearable="false"
                      format="YYYY/MM/DD"
                      style="width: 130px; margin-left: 5px;"
                  />
                  <SmartTimeInput
                      v-model="timeInputString"
                      size="small"
                      :input-style="{ width: '100px', marginLeft: '5px' }"
                      @change="handleTimeChange"
                  />
                </span>
                <span class="time-tag">
                  <el-icon><Clock /></el-icon> 更新: {{ formatDateDetailed(currentNote.updated_at) }}
                </span>
            </div>
            <div class="save-status">
                <span v-if="saveStatus === 'saved'" class="status-saved"><el-icon><Check /></el-icon> 已保存</span>
                <span v-else-if="saveStatus === 'saving'" class="status-saving"><el-icon class="is-loading"><Loading /></el-icon> 保存中...</span>
                <span v-else class="status-unsaved">未保存</span>
            </div>
        </div>

        <!-- Row 3: Weight and Node Type Configuration -->
        <div class="header-row-tertiary">
            <div class="weight-control">
              <span class="label">权重:</span>
              <el-input-number 
                  v-model="currentNote.weight" 
                  :min="1" 
                  :max="10000" 
                  :step="10" 
                  size="small"
                  :controls="false"
                  @change="onWeightChange"
                  @blur="onWeightBlur"
              />
            </div>
            
            <!-- Node Type Selector -->
            <div class="status-control">
              <NodeSelector 
                mode="type" 
                v-model="currentNote.node_type"
                label="类型"
                :show-label="true"
                :show-help-icon="true"
                @change="checkAndSave"
                @show-help="showHelpDialog = true"
              />
            </div>

            <!-- Node Status Selector -->
            <div class="status-control" style="margin-left: 15px;">
              <NodeSelector 
                mode="status" 
                v-model="currentNote.node_status"
                :related-type="currentNote.node_type"
                label="状态"
                :show-label="true"
                :show-help-icon="false"
                @change="checkAndSave"
                @show-help="showHelpDialog = true"
              />
            </div>

            <div class="history-toggle">
              <el-button 
                size="small" 
                :type="showHistory ? 'primary' : ''" 
                :icon="List" 
                @click="showHistory = !showHistory"
              >
                操作日志
              </el-button>
            </div>
        </div>
        
        <!-- History Section -->
        <div v-if="showHistory" class="history-panel">
          <div v-if="!currentNote.history || currentNote.history.length === 0" class="history-empty">
            暂无操作记录
          </div>
          <div v-else class="history-list">
            <div v-for="(entry, index) in sortedHistory" :key="index" class="history-item">
              <span class="history-time">{{ formatDateDetailed(entry.ts * 1000) }}</span>
              <span class="history-content">
                <el-tag size="small" :type="getFieldTagType(entry.f)" class="field-tag">{{ getFieldName(entry.f) }}</el-tag>
                <span class="history-value">{{ formatHistoryValue(entry.f, entry.v) }}</span>
              </span>
            </div>
          </div>
        </div>
      </div>
      <NoteEditor :key="currentNote.id" v-model="currentNote.content" @change="onContentChange" />
    </div>
    
    <NodeHelpDialog v-model="showHelpDialog" />
    <NoteCopyDialog 
      v-if="currentNote"
      v-model="showCopyDialog" 
      :source-note="currentNote"
      @success="handleCopySuccess"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onBeforeUnmount, nextTick } from 'vue';
import { Delete, Calendar, Clock, Check, Loading, List, ArrowDown, CopyDocument } from '@element-plus/icons-vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import NoteEditor from './NoteEditor.vue';
import NodeSelector from './NodeSelector.vue';
import NodeHelpDialog from './NodeHelpDialog.vue';
import NoteCopyDialog from './NoteCopyDialog.vue';
import SmartTimeInput from './SmartTimeInput.vue';
import { useNoteStore, type NoteNode } from '@/api/notes';
import { 
    getNodeTypeConfig, 
    getNodeStatusConfig,
    getOrderedNodeTypes,
    getOrderedNodeStatuses
} from '@/utils/nodeConfig';

const props = defineProps<{
  noteId: string;
}>();

const emit = defineEmits<{
  (e: 'update', note: NoteNode): void;
  (e: 'delete', noteId: string): void;
  (e: 'create', note: NoteNode): void;
}>();

const noteStore = useNoteStore();
const currentNote = ref<NoteNode | undefined>(undefined);
const isFetchingContent = ref(false);
const saveStatus = ref<'saved' | 'saving' | 'unsaved'>('saved');
const showHistory = ref(false);
const showHelpDialog = ref(false);
const showCopyDialog = ref(false);
let saveTimeout: any = null;

const isReady = computed(() => {
    return !!currentNote.value && !isFetchingContent.value && !!originalData.value && currentNote.value.content !== undefined;
});

const nodeTypeProxy = computed<string>({
    get: () => {
        if (!currentNote.value) return 'note';
        return currentNote.value.node_type || 'note';
    },
    set: (value) => {
        if (!currentNote.value) return;
        currentNote.value.node_type = value;
    }
});

const nodeStatusProxy = computed<string>({
    get: () => {
        if (!currentNote.value) return 'idea';
        return currentNote.value.node_status || 'idea';
    },
    set: (value) => {
        if (!currentNote.value) return;
        currentNote.value.node_status = value;
    }
});

// Track original values for change detection
const originalData = ref<{
    title: string;
    content: string;
    weight: number;
    start_at: number;
    node_type: string | null;
    node_status: string | null;
} | null>(null);

const orderedNodeTypes = computed(() => getOrderedNodeTypes());
const orderedNodeStatuses = computed(() => getOrderedNodeStatuses());

const sortedHistory = computed(() => {
    if (!currentNote.value || !currentNote.value.history) return [];
    return [...currentNote.value.history].sort((a, b) => b.ts - a.ts);
});

// Watch noteId change to load data
watch(() => props.noteId, async (newId) => {
    if (!newId) {
        currentNote.value = undefined;
        originalData.value = null;
        isFetchingContent.value = false;
        return;
    }
    
    // Check if we need to save previous note
    if (currentNote.value && saveStatus.value === 'unsaved' && isReady.value) {
        if (saveTimeout) clearTimeout(saveTimeout);
        await saveNote(currentNote.value);
    }

    await loadNote(newId);
}, { immediate: true });

async function loadNote(id: string) {
    saveStatus.value = 'saved';
    showHistory.value = false;

    currentNote.value = undefined;
    originalData.value = null;
    isFetchingContent.value = true;

    const detailed = await noteStore.fetchNoteDetail(id);
    isFetchingContent.value = false;

    if (!detailed) {
        ElMessage.error('无法加载节点详情');
        return;
    }

    const note = noteStore.notes.find(n => n.id === id) || detailed;
    currentNote.value = note;
    currentNote.value.content = detailed.content;
    originalData.value = {
        title: detailed.title,
        content: detailed.content || '',
        weight: detailed.weight,
        start_at: detailed.start_at,
        node_type: detailed.node_type || 'note',
        node_status: detailed.node_status || 'idea'
    };
}

const checkAndSave = () => {
    if (!isReady.value || !currentNote.value || !originalData.value) return;
    
    const curr = currentNote.value;
    const orig = originalData.value;
    
    const isChanged = 
        curr.content !== orig.content ||
        curr.title !== orig.title ||
        curr.weight !== orig.weight ||
        curr.start_at !== orig.start_at ||
        curr.node_type !== orig.node_type ||
        curr.node_status !== orig.node_status;
    
    if (!isChanged) {
        if (saveStatus.value === 'unsaved') saveStatus.value = 'saved';
        return;
    }
    
    saveStatus.value = 'unsaved';
    
    if (saveTimeout) clearTimeout(saveTimeout);
    saveTimeout = setTimeout(() => {
        saveNote(curr);
    }, 2000);
};

const saveNote = async (note: NoteNode) => {
    if (!note) return;
    saveStatus.value = 'saving';
    
    try {
        await noteStore.updateNote(note.id, {
            title: note.title,
            content: note.content,
            weight: note.weight,
            start_at: note.start_at,
            node_type: note.node_type,
            node_status: note.node_status
        });
        
        // Update original data to current
        originalData.value = {
            title: note.title,
            content: note.content || '',
            weight: note.weight,
            start_at: note.start_at,
            node_type: note.node_type || 'note',
            node_status: note.node_status || 'idea'
        };
        
        saveStatus.value = 'saved';
        emit('update', note); // Notify parent
    } catch (e) {
        saveStatus.value = 'unsaved'; // Retry?
        console.error(e);
        ElMessage.error('保存失败');
    }
};

// Field Change Handlers
const onContentChange = (html: string) => {
    if (currentNote.value) {
        currentNote.value.content = html;
        checkAndSave();
    }
};

const onTitleChange = () => {
    checkAndSave();
};

const startDateProxy = computed<Date | undefined>({
    get: () => {
        if (!currentNote.value) return undefined;
        return new Date(currentNote.value.start_at);
    },
    set: (val) => {
        if (!currentNote.value || !val) return;
        const original = new Date(currentNote.value.start_at);
        // Update Year/Month/Day
        original.setFullYear(val.getFullYear());
        original.setMonth(val.getMonth());
        original.setDate(val.getDate());
        
        currentNote.value.start_at = original.getTime();
        checkAndSave();
    }
});

const timeInputString = ref('');

// Watch currentNote change to update timeInputString
watch(() => currentNote.value?.start_at, (val) => {
    if (val) {
        const d = new Date(val);
        const h = String(d.getHours()).padStart(2, '0');
        const m = String(d.getMinutes()).padStart(2, '0');
        const s = String(d.getSeconds()).padStart(2, '0');
        timeInputString.value = `${h}:${m}:${s}`;
    }
}, { immediate: true });

const handleTimeChange = (val: string) => {
    if (!val) return;
    
    timeInputString.value = val;
    
    if (currentNote.value) {
        const original = new Date(currentNote.value.start_at);
        const [h, m, s] = val.split(':').map(Number);
        
        // Only update if changed
        if (original.getHours() !== h || original.getMinutes() !== m || original.getSeconds() !== s) {
            original.setHours(h);
            original.setMinutes(m);
            original.setSeconds(s);
            
            currentNote.value.start_at = original.getTime();
            checkAndSave();
        }
    }
};



const onWeightChange = (value: number | undefined) => {
    if (!currentNote.value) return;
    if (value === undefined || value === null || isNaN(value)) {
         if (originalData.value) currentNote.value.weight = originalData.value.weight;
         return;
    }
    currentNote.value.weight = Math.round(value);
    if (currentNote.value.weight < 1) currentNote.value.weight = 1;
    checkAndSave();
};

const onWeightBlur = () => {
    if (currentNote.value) onWeightChange(currentNote.value.weight);
};

const onNodeTypeChange = (value: string) => {
    if (!currentNote.value) return;
    currentNote.value.node_type = value;
    checkAndSave();
};

const onNodeStatusChange = (value: string) => {
    if (!currentNote.value) return;
    currentNote.value.node_status = value;
    checkAndSave();
};

const deleteCurrentNote = async () => {
    if (!currentNote.value) return;
    try {
        await ElMessageBox.confirm('确定要删除这个节点吗？', '警告', {
            confirmButtonText: '删除',
            cancelButtonText: '取消',
            type: 'warning'
        });
        
        const id = currentNote.value.id;
        await noteStore.deleteNote(id);
        emit('delete', id);
        
    } catch (e) {
        // Cancelled
    }
};

const handleCopySuccess = (newNote: NoteNode) => {
    emit('create', newNote);
};

// Formatting Helpers
const formatDateDetailed = (timestamp: number) => {
    if (!timestamp) return '-';
    const date = new Date(timestamp);
    const now = new Date();
    const isCurrentYear = date.getFullYear() === now.getFullYear();
    
    const options: Intl.DateTimeFormatOptions = {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false
    };
    
    if (!isCurrentYear) {
        options.year = 'numeric';
    }
    
    return date.toLocaleString('zh-CN', options).replace(/-/g, '/');
};

const getFieldName = (f: string) => {
    const map: Record<string, string> = {
        'n': '类型',
        's': '状态',
        't': '标题',
        'w': '权重',
        'c': '内容'
    };
    return map[f] || f;
};

const getFieldTagType = (f: string) => {
    const map: Record<string, string> = {
        'n': 'primary',
        's': 'warning',
        't': '',
        'w': 'success',
        'c': 'info'
    };
    return map[f] || '';
};

const formatHistoryValue = (f: string, v: any) => {
    if (f === 'n') return getNodeTypeConfig(v).label;
    if (f === 's') return getNodeStatusConfig(v).label;
    if (f === 'c') return `${v} 字`;
    return v;
};

const getTypeTriggerStyle = (type: string | null) => {
    const config = getNodeTypeConfig(type || 'note');
    return {
        borderColor: config.baseColor,
        color: config.baseColor,
        backgroundColor: '#fff'
    };
};

const getNodeTypeLabel = (type: string | null) => {
    return getNodeTypeConfig(type || 'note').label;
};

const getNodeStatusLabel = (status: string | null) => {
    return getNodeStatusConfig(status || 'idea').label;
};

// Cleanup
onBeforeUnmount(() => {
    if (saveTimeout) clearTimeout(saveTimeout);
    // Ensure last save happens? 
    // Async in unmount is tricky. Ideally we rely on the user not closing the tab immediately.
    if (currentNote.value && saveStatus.value === 'unsaved') {
        saveNote(currentNote.value);
    }
});
</script>

<style scoped>
.note-detail-panel {
    min-height: 320px;
    display: flex;
    flex-direction: column;
}

.panel-content {
    display: flex;
    flex-direction: column;
    min-height: 320px;
}

.editor-header {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-bottom: 20px;
  padding-bottom: 15px;
  border-bottom: 1px solid #f0f0f0;
}

.header-row-primary {
    display: flex;
    align-items: center;
    gap: 10px;
}

.header-row-secondary {
    display: flex;
    align-items: center;
    justify-content: space-between;
    font-size: 12px;
    color: #909399;
}

.header-row-tertiary {
    display: flex;
    align-items: center;
}

.meta-group {
    display: flex;
    gap: 15px;
}

.title-input {
  flex: 1;
  font-size: 18px;
}

.time-tag {
  display: flex;
  align-items: center;
  gap: 4px;
  cursor: default;
}

.weight-control {
    width: 150px;
    margin-right: 20px;
    display: flex;
    align-items: center;
    gap: 10px;
}

.weight-control .label {
    font-size: 12px;
    color: #606266;
    white-space: nowrap;
}

.status-control {
    display: flex;
    align-items: center;
    gap: 10px;
}

.status-control .label {
    font-size: 12px;
    color: #606266;
    white-space: nowrap;
}

.history-toggle {
    margin-left: auto;
}

.help-icon {
    margin-left: 5px;
    font-size: 14px;
    color: #909399;
    cursor: help;
}

/* Node Type Grid Styles - DEPRECATED / REMOVED */
/* ... Styles moved to NodeSelector.vue ... */

.history-panel {
    margin-top: 15px;
    padding: 10px;
    background: #f8f9fb;
    border-radius: 4px;
    max-height: 200px;
    overflow-y: auto;
    font-size: 13px;
    border: 1px solid #ebeef5;
}

.history-empty {
    text-align: center;
    color: #909399;
    padding: 10px;
}

.history-item {
    display: flex;
    align-items: flex-start;
    gap: 15px;
    padding: 6px 0;
    border-bottom: 1px dashed #ebeef5;
}

.history-item:last-child {
    border-bottom: none;
}

.history-time {
    color: #909399;
    white-space: nowrap;
    font-family: monospace;
}

.history-content {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
}

.field-tag {
    min-width: 40px;
    text-align: center;
}

.history-value {
    color: #303133;
    word-break: break-all;
}

.save-status {
    font-size: 12px;
    margin-right: 15px;
    display: flex;
    align-items: center;
}

.status-saved {
    color: #67c23a;
    display: flex;
    align-items: center;
    gap: 4px;
}

.status-saving {
    color: #e6a23c;
    display: flex;
    align-items: center;
    gap: 4px;
}

.status-unsaved {
    color: #909399;
}

.empty-state {
    display: flex;
    justify-content: center;
    align-items: center;
    height: 100%;
    color: #909399;
}

.loading-placeholder {
    display: flex;
    justify-content: center;
    align-items: center;
    height: 100%;
    color: #909399;
    gap: 8px;
    font-size: 14px;
}
</style>
