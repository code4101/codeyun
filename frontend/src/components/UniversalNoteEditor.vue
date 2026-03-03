<template>
  <div class="universal-note-editor">
    <div v-if="loading" class="loading-placeholder">
      <el-icon class="is-loading"><Loading /></el-icon> 加载内容中...
    </div>
    <div v-else-if="!currentNote" class="empty-state">
      <el-empty :description="emptyText || '未选择内容'" />
    </div>
    <div v-else class="panel-content">
      <div class="editor-header">
        <!-- Row 1: Title and Actions -->
        <div class="header-row-primary">
            <el-input 
              v-model="currentNote.title" 
              placeholder="标题" 
              class="title-input" 
              @input="handleChange" 
              :readonly="readonly"
            />
            <slot name="actions"></slot>
        </div>
        
        <!-- Row 2: Meta Info and Save Status -->
        <div class="header-row-secondary">
            <div class="meta-group">
                <span class="time-tag">
                  <el-icon><Calendar /></el-icon> 起始: 
                  <el-date-picker
                      v-model="currentNote.start_at"
                      type="datetime"
                      placeholder="选择起始时间"
                      size="small"
                      :clearable="false"
                      format="YYYY/MM/DD HH:mm:ss"
                      value-format="x"
                      @change="handleChange"
                      style="width: 180px; margin-left: 5px;"
                      :readonly="readonly"
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
                  @change="handleChange"
                  :disabled="readonly"
              />
            </div>
            
            <div class="status-control">
               <span class="label">节点类型:</span>
               <el-select 
                v-model="nodeTypeProxy"
                size="small" 
                placeholder="选择类型"
                clearable
                @change="handleChange"
                style="width: 120px;"
                :disabled="readonly"
              >
                <el-option label="普通 (None)" value="" />
                <el-option 
                  v-for="config in orderedNodeConfigs" 
                  :key="config.id" 
                  :label="config.label" 
                  :value="config.id" 
                />
              </el-select>
              <el-tooltip effect="light" placement="top">
                <template #content>
                  <div style="line-height: 1.6; max-width: 300px;">
                    <b>节点类型说明:</b><br/>
                    <div v-for="config in orderedNodeConfigs" :key="config.id">
                      - <b>{{ config.label.split(' ')[0] }}</b>: {{ config.description }}
                    </div>
                  </div>
                </template>
                <el-icon class="help-icon"><QuestionFilled /></el-icon>
              </el-tooltip>
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
      
      <NoteEditor 
        :key="currentNote.id || 'new'" 
        v-model="currentNote.content" 
        :readOnly="readonly"
        @change="handleContentChange" 
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onBeforeUnmount, nextTick } from 'vue';
import { Calendar, Clock, Check, Loading, QuestionFilled, List } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import NoteEditor from './NoteEditor.vue';
import type { NoteNode } from '@/api/notes';
import { getNodeConfig, getOrderedNodeConfigs } from '@/utils/nodeConfig';

// Props definition
const props = defineProps<{
  modelValue?: NoteNode; 
  loading?: boolean;
  readonly?: boolean;
  emptyText?: string;
  onSave?: (note: NoteNode) => Promise<void>;
}>();

const emit = defineEmits<{
  (e: 'update:modelValue', note: NoteNode): void;
  (e: 'change', note: NoteNode): void;
}>();

// Local state
const currentNote = ref<NoteNode | undefined>(undefined);
const saveStatus = ref<'saved' | 'saving' | 'unsaved'>('saved');
const showHistory = ref(false);

let saveTimeout: any = null;

// Helper to handle null/empty node_type
const nodeTypeProxy = computed<string>({
    get: () => {
        if (!currentNote.value) return '';
        return currentNote.value.node_type ?? '';
    },
    set: (value) => {
        if (!currentNote.value) return;
        currentNote.value.node_type = value === '' ? null : value;
    }
});

const orderedNodeConfigs = computed(() => getOrderedNodeConfigs());

const sortedHistory = computed(() => {
    if (!currentNote.value || !currentNote.value.history) return [];
    return [...currentNote.value.history].sort((a, b) => b.ts - a.ts);
});

// Watch modelValue to sync local state
watch(() => props.modelValue, (newVal) => {
    if (newVal) {
        // If switching notes or initial load, force update
        if (!currentNote.value || currentNote.value.id !== newVal.id) {
            const note = JSON.parse(JSON.stringify(newVal));
            
            // Normalize timestamps to milliseconds for frontend components (el-date-picker, etc)
            if (note.start_at && note.start_at < 10000000000) note.start_at *= 1000;
            if (note.updated_at && note.updated_at < 10000000000) note.updated_at *= 1000;
            if (note.created_at && note.created_at < 10000000000) note.created_at *= 1000;

            currentNote.value = note;
            saveStatus.value = 'saved';
            showHistory.value = false;
        } 
    } else {
        currentNote.value = undefined;
    }
}, { immediate: true });

// Change Handlers
const handleChange = () => {
    if (!currentNote.value) return;
    saveStatus.value = 'unsaved';
    
    if (saveTimeout) clearTimeout(saveTimeout);
    saveTimeout = setTimeout(triggerSave, 2000);
};

const handleContentChange = (html: string) => {
    if (!currentNote.value) return;
    currentNote.value.content = html;
    handleChange();
};

const triggerSave = async () => {
    if (!currentNote.value || !props.onSave) return;
    
    const noteToSave = JSON.parse(JSON.stringify(currentNote.value));
    if (noteToSave.start_at && noteToSave.start_at > 10000000000) noteToSave.start_at /= 1000;
    if (noteToSave.updated_at && noteToSave.updated_at > 10000000000) noteToSave.updated_at /= 1000;
    if (noteToSave.created_at && noteToSave.created_at > 10000000000) noteToSave.created_at /= 1000;

    saveStatus.value = 'saving';
    try {
        await props.onSave(noteToSave);
        saveStatus.value = 'saved';
        emit('change', currentNote.value);
    } catch (e) {
        saveStatus.value = 'unsaved';
        console.error(e);
    }
};

// Cleanup
onBeforeUnmount(() => {
    if (saveTimeout) clearTimeout(saveTimeout);
    if (saveStatus.value === 'unsaved' && currentNote.value && props.onSave) {
        props.onSave(currentNote.value);
    }
});

// Formatting Helpers (... same as before)
const formatDateDetailed = (timestamp: number) => {
    if (!timestamp) return '-';
    let ts = timestamp;
    if (ts < 10000000000) ts *= 1000;
    
    const dateObj = new Date(ts);
    const now = new Date();
    const isCurrentYear = dateObj.getFullYear() === now.getFullYear();
    
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
    
    return dateObj.toLocaleString('zh-CN', options).replace(/-/g, '/');
};

const getFieldName = (f: string) => {
    const map: Record<string, string> = {
        'n': '类型',
        's': '类型',
        't': '标题',
        'w': '权重',
        'c': '内容'
    };
    return map[f] || f;
};

const getFieldTagType = (f: string) => {
    const map: Record<string, string> = {
        'n': 'warning',
        's': 'warning',
        't': '',
        'w': 'success',
        'c': 'info'
    };
    return map[f] || '';
};

const formatHistoryValue = (f: string, v: any) => {
    if (f === 'n' || f === 's') return getNodeConfig(v).label;
    if (f === 'c') return `${v} 字`;
    return v;
};

</script>

<style scoped>
.universal-note-editor {
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

.help-icon {
    margin-left: 5px;
    font-size: 14px;
    color: #909399;
    cursor: help;
}

.history-toggle {
    margin-left: auto;
}

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

/* Image Merge Styles */
.merge-dialog-content {
    display: flex;
    flex-direction: column;
    gap: 15px;
}

.merge-settings {
    display: flex;
    align-items: center;
    gap: 10px;
}

.image-preview-list {
    border: 1px solid #ebeef5;
    padding: 10px;
    border-radius: 4px;
}

.preview-scroll {
    display: flex;
    gap: 10px;
    overflow-x: auto;
    padding-bottom: 5px;
}

.preview-thumb {
    height: 80px;
    object-fit: cover;
    border: 1px solid #dcdfe6;
    border-radius: 2px;
}

.merge-result {
    margin-top: 10px;
}

.result-container {
    max-height: 400px;
    overflow-y: auto;
    border: 1px solid #dcdfe6;
    background: #f5f7fa;
    text-align: center;
    padding: 10px;
}

.result-img {
    max-width: 100%;
    box-shadow: 0 2px 12px 0 rgba(0, 0, 0, 0.1);
}
</style>
