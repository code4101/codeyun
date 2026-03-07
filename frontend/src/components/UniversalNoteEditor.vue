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
              @input="handleMetaChange" 
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
                      @change="handleMetaChange"
                      class="start-at-picker"
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
                  @change="handleMetaChange"
                  :disabled="readonly"
              />
            </div>
            
            <div class="status-control">
               <span class="label">类型:</span>
               <el-select 
                v-model="nodeTypeProxy"
                size="small" 
                placeholder="选择类型"
                clearable
                @change="handleMetaImmediateChange"
                class="type-select"
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
                  <div class="help-tooltip-content">
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
                :type="historyButtonType" 
                :icon="List" 
                @click="showHistory = !showHistory"
              >
                操作日志
              </el-button>
            </div>
        </div>

        <!-- Row 4: Custom Fields -->
        <div class="header-row-custom">
            <div class="custom-fields-label">
                <span class="label">自定义属性:</span>
                <el-button link type="primary" size="small" @click="addCustomField" :disabled="readonly">
                    <el-icon><Plus /></el-icon>
                </el-button>
            </div>
            <div class="custom-fields-list">
                <div v-for="(item, index) in customFieldsList" :key="index" class="custom-field-item">
                    <el-input 
                        v-model="item.key" 
                        size="small" 
                        placeholder="Key" 
                        class="field-key"
                        @input="handleCustomFieldChange"
                        :readonly="readonly"
                    />
                    <span class="separator">:</span>
                    <el-input 
                        v-model="item.value" 
                        size="small" 
                        placeholder="Value" 
                        class="field-value"
                        @input="handleCustomFieldChange"
                        :readonly="readonly"
                    />
                    <el-button link type="danger" size="small" @click="removeCustomField(index)" :disabled="readonly">
                        <el-icon><Close /></el-icon>
                    </el-button>
                </div>
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
import { ref, computed, watch, defineAsyncComponent } from 'vue';
import { Calendar, Clock, Check, Loading, QuestionFilled, List, Plus, Close } from '@element-plus/icons-vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import type { NoteNode } from '@/api/notes';
import { formatNoteDateTimeDetailed } from '@/utils/noteDate';
import {
    applyEditableNoteSnapshot,
    areEditableNoteSnapshotsEqual,
    buildEditableNotePatch,
    buildNoteDraftStorageKey,
    createEditableNoteSnapshot,
    noteCustomFieldsToItems,
    noteCustomFieldItemsToList,
    noteSnapshotToNode,
    type EditableNotePatch,
    type EditableNoteSnapshot,
    type NoteCustomFieldItem
} from '@/utils/noteAutoSave';
import { useAutoSave } from '@/utils/useAutoSave';
import { getNodeTypeConfig, getNodeStatusConfig, getOrderedNodeTypes } from '@/utils/nodeConfig';

const NoteEditor = defineAsyncComponent(() => import('./NoteEditor.vue'));

// Props definition
const props = defineProps<{
  modelValue?: NoteNode; 
  loading?: boolean;
  readonly?: boolean;
  emptyText?: string;
  onSave?: (note: NoteNode, patch?: EditableNotePatch) => Promise<NoteNode | void>;
  onSaveKeepalive?: (note: NoteNode, patch?: EditableNotePatch) => void;
}>();

const emit = defineEmits<{
  (e: 'update:modelValue', note: NoteNode): void;
  (e: 'change', note: NoteNode): void;
}>();

// Local state
const currentNote = ref<NoteNode | undefined>(undefined);
const saveStatus = ref<'saved' | 'saving' | 'unsaved'>('saved');
const showHistory = ref(false);
const historyButtonType = computed<'primary' | undefined>(() => showHistory.value ? 'primary' : undefined);

const customFieldsList = ref<NoteCustomFieldItem[]>([]);
const currentDraftKey = ref<string | null>(null);
const CONTENT_SAVE_DELAY_MS = 1800;
const META_SAVE_DELAY_MS = 450;
let loadRequestToken = 0;

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

const orderedNodeConfigs = computed(() => getOrderedNodeTypes());

const sortedHistory = computed(() => {
    if (!currentNote.value || !currentNote.value.history) return [];
    return [...currentNote.value.history].sort((a, b) => b.ts - a.ts);
});

const buildCurrentSnapshot = (): EditableNoteSnapshot | null => createEditableNoteSnapshot(
    currentNote.value,
    noteCustomFieldItemsToList(customFieldsList.value)
);

const normalizeIncomingNote = (note: NoteNode) => {
    const cloned = JSON.parse(JSON.stringify(note)) as NoteNode;
    if (cloned.start_at && cloned.start_at < 10000000000) cloned.start_at *= 1000;
    if (cloned.updated_at && cloned.updated_at < 10000000000) cloned.updated_at *= 1000;
    if (cloned.created_at && cloned.created_at < 10000000000) cloned.created_at *= 1000;
    return cloned;
};

const syncCurrentNoteFromSnapshot = (snapshot: EditableNoteSnapshot, source?: Partial<NoteNode> | null) => {
    if (!currentNote.value && !source) return;
    currentNote.value = applyEditableNoteSnapshot(
        (source ? { ...(currentNote.value as NoteNode | undefined), ...source } : currentNote.value!) as NoteNode,
        snapshot
    );
    customFieldsList.value = noteCustomFieldsToItems(snapshot.custom_fields);
};

const toOutgoingNote = (snapshot: EditableNoteSnapshot) => {
    const outgoing = noteSnapshotToNode(currentNote.value, snapshot);
    if (outgoing.start_at && outgoing.start_at > 10000000000) outgoing.start_at /= 1000;
    if (outgoing.updated_at && outgoing.updated_at > 10000000000) outgoing.updated_at /= 1000;
    if (outgoing.created_at && outgoing.created_at > 10000000000) outgoing.created_at /= 1000;
    return outgoing;
};

const toOutgoingPatch = (patch: EditableNotePatch): EditableNotePatch => {
    const outgoing = { ...patch };
    if (typeof outgoing.start_at === 'number' && outgoing.start_at > 10000000000) {
        outgoing.start_at /= 1000;
    }
    return outgoing;
};

const autoSave = useAutoSave<EditableNoteSnapshot>({
    debounceMs: 2000,
    equals: areEditableNoteSnapshotsEqual,
    storageKey: () => currentDraftKey.value,
    save: async (snapshot) => {
        if (!props.onSave) return snapshot;

        const baseline = autoSave.getBaselineSnapshot();
        const patch = buildEditableNotePatch(snapshot, baseline);
        if (!Object.keys(patch).length) {
            return snapshot;
        }

        const updatedNote = await props.onSave(toOutgoingNote(snapshot), toOutgoingPatch(patch));
        const normalizedSavedNote = updatedNote ? normalizeIncomingNote(updatedNote) : null;
        const canonicalSnapshot = createEditableNoteSnapshot(normalizedSavedNote || noteSnapshotToNode(currentNote.value, snapshot)) ?? snapshot;
        if (currentNote.value?.id === snapshot.id) {
            syncCurrentNoteFromSnapshot(canonicalSnapshot, normalizedSavedNote);
            emit('change', currentNote.value);
        }
        return canonicalSnapshot;
    },
    onError: (error) => {
        console.error(error);
    },
    saveOnPageHide: (snapshot, baselineSnapshot) => {
        if (!props.onSaveKeepalive) return;
        const patch = buildEditableNotePatch(snapshot, baselineSnapshot);
        if (!Object.keys(patch).length) return;
        props.onSaveKeepalive(toOutgoingNote(snapshot), toOutgoingPatch(patch));
    }
});

watch(autoSave.saveStatus, value => {
    saveStatus.value = value;
});

// Watch modelValue to sync local state
watch(() => props.modelValue, async (newVal) => {
    const requestToken = ++loadRequestToken;

    if (currentNote.value && autoSave.hasUnsavedChanges.value) {
        await autoSave.flush();
    }

    if (newVal) {
        if (!currentNote.value || currentNote.value.id !== newVal.id) {
            const note = normalizeIncomingNote(newVal);
            const serverSnapshot = createEditableNoteSnapshot(note);
            if (!serverSnapshot) return;

            currentDraftKey.value = buildNoteDraftStorageKey(note.id, note.title);
            const {
                snapshot: loadedSnapshot,
                pendingDraft,
                expiredDraft
            } = autoSave.loadSnapshot(serverSnapshot);
            let activeSnapshot = loadedSnapshot ?? serverSnapshot;

            if (expiredDraft) {
                ElMessage.info('发现过期本地草稿，已忽略');
            }

            if (pendingDraft) {
                const promptMessage = pendingDraft.hasConflict
                    ? `检测到 ${formatDateDetailed(pendingDraft.updatedAt)} 的本地草稿，且服务器版本之后还有更新。是否恢复本地草稿？`
                    : `检测到 ${formatDateDetailed(pendingDraft.updatedAt)} 的本地草稿。是否恢复继续编辑？`;

                try {
                    await ElMessageBox.confirm(promptMessage, '恢复本地草稿', {
                        confirmButtonText: '恢复草稿',
                        cancelButtonText: '使用服务器版本',
                        type: pendingDraft.hasConflict ? 'warning' : 'info'
                    });

                    if (requestToken !== loadRequestToken || props.modelValue?.id !== note.id) {
                        return;
                    }

                    autoSave.restoreDraft(pendingDraft.snapshot);
                    activeSnapshot = pendingDraft.snapshot;
                    ElMessage.warning(pendingDraft.hasConflict ? '已恢复本地草稿，请留意与服务器版本的差异' : '已恢复本地草稿');
                } catch {
                    if (requestToken !== loadRequestToken || props.modelValue?.id !== note.id) {
                        return;
                    }
                    autoSave.clearDraft();
                }
            }

            syncCurrentNoteFromSnapshot(activeSnapshot, note);
            saveStatus.value = autoSave.saveStatus.value;
            showHistory.value = false;
        }
        return;
    }

    currentDraftKey.value = null;
    currentNote.value = undefined;
    customFieldsList.value = [];
    autoSave.loadSnapshot(null, { draftStrategy: 'discard' });
}, { immediate: true });

const queueAutoSave = (options: { immediate?: boolean; delayMs?: number } = {}) => {
    if (!currentNote.value) return;
    const snapshot = buildCurrentSnapshot();
    if (!snapshot) return;
    currentNote.value.custom_fields = snapshot.custom_fields;
    autoSave.markDirty(snapshot, options);
};

const handleMetaChange = () => {
    queueAutoSave({ delayMs: META_SAVE_DELAY_MS });
};

const handleMetaImmediateChange = () => {
    queueAutoSave({ immediate: true, delayMs: 0 });
};

const handleContentChange = (html: string) => {
    if (!currentNote.value) return;
    currentNote.value.content = html;
    queueAutoSave({ delayMs: CONTENT_SAVE_DELAY_MS });
};

const syncCustomFields = () => {
    if (!currentNote.value) return;
    currentNote.value.custom_fields = noteCustomFieldItemsToList(customFieldsList.value);
    handleMetaChange();
};

const addCustomField = () => {
    customFieldsList.value.push({ key: '', value: '', type: 'string' });
};

const removeCustomField = (index: number) => {
    customFieldsList.value.splice(index, 1);
    syncCustomFields();
};

const handleCustomFieldChange = () => {
    syncCustomFields();
};

// Formatting Helpers (... same as before)
const formatDateDetailed = (timestamp: number) => formatNoteDateTimeDetailed(timestamp);

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

const getFieldTagType = (f: string): 'primary' | 'success' | 'info' | 'warning' | 'danger' | undefined => {
    const map: Record<string, 'primary' | 'success' | 'info' | 'warning' | 'danger' | undefined> = {
        'n': 'primary',
        's': 'warning',
        't': undefined,
        'w': 'success',
        'c': 'info'
    };
    return map[f];
};

const formatHistoryValue = (f: string, v: any) => {
    if (f === 'n') return getNodeTypeConfig(v).label;
    if (f === 's') return getNodeStatusConfig(v).label;
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

.header-row-custom {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    flex-wrap: wrap;
}

.custom-fields-label {
    display: flex;
    align-items: center;
    gap: 5px;
    padding-top: 4px; /* Align with input */
}

.custom-fields-label .label {
    font-size: 12px;
    color: #606266;
    white-space: nowrap;
}

.custom-fields-list {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    flex: 1;
}

.custom-field-item {
    display: flex;
    align-items: center;
    gap: 5px;
    background: #f8f9fa;
    padding: 2px 8px;
    border-radius: 4px;
    border: 1px solid #ebeef5;
}

.custom-field-item .field-key {
    width: 80px;
}

.custom-field-item .field-value {
    width: 120px;
}

.custom-field-item .separator {
    color: #909399;
    font-weight: bold;
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

.start-at-picker {
    width: 180px;
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

.type-select {
    width: 120px;
}

.help-tooltip-content {
    line-height: 1.6;
    max-width: 300px;
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
