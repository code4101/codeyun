<template>
  <div class="note-detail-panel">
    <div v-if="!props.noteId" class="empty-state">
      <el-empty description="未选择节点" />
    </div>
    <div v-else-if="isFetchingContent || !isReady" class="loading-placeholder">
      <el-icon class="is-loading"><Loading /></el-icon> 加载内容中...
    </div>
    <div v-else-if="!currentNote" class="empty-state">
      <el-empty description="节点未就绪" />
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
                <el-tooltip content="全景图：展示该节点所在的完整关联网络" placement="top">
                    <el-button 
                        size="small" 
                        :disabled="!hasConnections"
                        @click="openPlanetaryGraph('planetary')"
                        style="margin-left: 10px;"
                    >
                        行星图
                    </el-button>
                </el-tooltip>
                <el-tooltip content="衍生图：仅展示该节点向下延伸的发展网络（忽略来源）" placement="top">
                    <el-button 
                        size="small" 
                        :disabled="!hasOutConnections"
                        @click="openPlanetaryGraph('satellite')"
                    >
                        卫星图
                    </el-button>
                </el-tooltip>
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
                <el-button link type="primary" size="small" @click="addCustomField" title="添加自定义属性">
                    <el-icon><Plus /></el-icon>
                </el-button>
            </div>
            
            <div class="custom-fields-container">
                <!-- Group 1: Own Fields (Editable) -->
                <div v-for="(item, index) in customFieldsList" :key="'own-'+index" class="custom-field-item own-field">
                    <div class="indicator-placeholder"></div>
                    <el-input 
                        v-model="item.key" 
                        size="small" 
                        placeholder="Key" 
                        class="field-key"
                        @input="handleCustomFieldChange"
                    />
                    <el-select 
                        v-model="item.type" 
                        size="small" 
                        class="field-type-select" 
                        @change="handleCustomFieldTypeChange(item)"
                        placeholder="类型"
                    >
                        <el-option label="文本" value="string" />
                        <el-option label="数值" value="number" />
                        <el-option label="布尔" value="boolean" />
                    </el-select>
                    
                    <div class="field-value-container">
                        <!-- String Input -->
                        <el-input 
                            v-if="item.type === 'string'"
                            :model-value="getTextFieldValue(item)"
                            size="small" 
                            type="textarea"
                            autosize
                            placeholder="Value" 
                            class="field-value"
                            @update:model-value="value => setTextFieldValue(item, value)"
                        />
                        
                        <!-- Number Input (String storage, Number UI) -->
                        <div v-else-if="item.type === 'number'" class="number-input-wrapper">
                            <el-input 
                                :model-value="getTextFieldValue(item)" 
                                size="small" 
                                placeholder="0" 
                                class="field-value"
                                @update:model-value="value => setNumberFieldValue(item, value)"
                            />
                        </div>
                        
                        <!-- Boolean Input -->
                        <div v-else-if="item.type === 'boolean'" class="boolean-input-wrapper">
                            <el-switch 
                                :model-value="getBooleanFieldValue(item)" 
                                size="small" 
                                @update:model-value="value => setBooleanFieldValue(item, value)"
                            />
                        </div>
                    </div>

                    <div class="action-col">
                        <el-button link type="danger" size="small" @click="removeCustomField(index)" title="移除" class="remove-btn">
                            <el-icon><Close /></el-icon>
                        </el-button>
                    </div>
                </div>

                <!-- Group 2: Direct Parent Inherited (Read-only, Addable) -->
                <div v-for="(item, key) in inheritedDirectFields" :key="'direct-'+key" class="custom-field-item inherited-field direct">
                    <div class="indicator-col">
                        <el-tooltip content="来自直接父节点" placement="top" :show-after="500">
                            <div class="inherited-indicator">父</div>
                        </el-tooltip>
                    </div>
                    <span class="field-key-read">{{ key }}</span>
                    <span class="field-type-read">{{ getFieldTypeLabel(item.value) }}</span>
                    <div class="field-value-container">
                        <span class="field-value-read">{{ formatInheritedValue(item.value) }}</span>
                    </div>
                    <div class="action-col">
                        <el-tooltip content="添加此属性到当前节点" placement="top">
                            <el-button link type="primary" size="small" @click="addInheritedField(String(key), item.value, item.type)" class="add-btn">
                                <el-icon><Plus /></el-icon>
                            </el-button>
                        </el-tooltip>
                    </div>
                </div>

                <!-- Group 3: Ancestor Inherited (Read-only, Addable) -->
                <div v-for="(item, key) in inheritedAncestorFields" :key="'ancestor-'+key" class="custom-field-item inherited-field ancestor">
                    <div class="indicator-col">
                        <el-tooltip content="来自间接祖先节点" placement="top" :show-after="500">
                            <div class="inherited-indicator ancestor">祖</div>
                        </el-tooltip>
                    </div>
                    <span class="field-key-read">{{ key }}</span>
                    <span class="field-type-read">{{ getFieldTypeLabel(item.value) }}</span>
                    <div class="field-value-container">
                        <span class="field-value-read">{{ formatInheritedValue(item.value) }}</span>
                    </div>
                    <div class="action-col">
                        <el-tooltip content="添加此属性到当前节点" placement="top">
                            <el-button link type="primary" size="small" @click="addInheritedField(String(key), item.value, item.type)" class="add-btn">
                                <el-icon><Plus /></el-icon>
                            </el-button>
                        </el-tooltip>
                    </div>
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
import { ref, computed, watch, onBeforeUnmount, defineAsyncComponent } from 'vue';
import { Delete, Calendar, Clock, Check, Loading, List, CopyDocument, Plus, Close } from '@element-plus/icons-vue';
import { ElMessage, ElMessageBox } from 'element-plus';
const NoteEditor = defineAsyncComponent(() => import('./NoteEditor.vue'));
import NodeSelector from './NodeSelector.vue';
import NodeHelpDialog from './NodeHelpDialog.vue';
import NoteCopyDialog from './NoteCopyDialog.vue';
import SmartTimeInput from './SmartTimeInput.vue';
import { useNoteStore, type NoteNode } from '@/api/notes';
import { 
    getNodeTypeConfig, 
    getNodeStatusConfig
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

const hasConnections = computed(() => {
    return (currentNote.value?.edge_count || 0) > 0;
});

const hasOutConnections = computed(() => {
    return (currentNote.value?.out_degree || 0) > 0;
});

const openPlanetaryGraph = (mode: 'planetary' | 'satellite' = 'planetary') => {
    if (!currentNote.value) return;
    const suffix = mode === 'satellite' ? '卫星图' : '行星图';
    noteStore.addTab({
        id: `planet-${currentNote.value.id}-${mode}`,
        label: `${currentNote.value.title ? currentNote.value.title.slice(0, 8) : 'Untitled'} - ${suffix}`,
        type: 'planet',
        data: { noteId: currentNote.value.id, mode },
        closable: true
    });
};
const saveStatus = ref<'saved' | 'saving' | 'unsaved'>('saved');
const showHistory = ref(false);
const showHelpDialog = ref(false);
const showCopyDialog = ref(false);
const historyButtonType = computed<'primary' | undefined>(() => showHistory.value ? 'primary' : undefined);

type CustomFieldType = 'string' | 'number' | 'boolean';

interface CustomFieldItem {
    key: string;
    value: string | boolean;
    type: CustomFieldType;
}

interface InheritedFieldItem {
    type: CustomFieldType;
    value: string | number | boolean;
}

const customFieldsList = ref<CustomFieldItem[]>([]);
const inheritedDirectFields = ref<Record<string, InheritedFieldItem>>({});
const inheritedAncestorFields = ref<Record<string, InheritedFieldItem>>({});

let saveTimeout: any = null;

const isReady = computed(() => {
    return !!currentNote.value && !isFetchingContent.value && !!originalData.value && currentNote.value.content !== undefined;
});

// Track original values for change detection
const originalData = ref<{
    title: string;
    content: string;
    weight: number;
    start_at: number;
    node_type: string | null;
    node_status: string | null;
    custom_fields: string; // JSON string for easy comparison
} | null>(null);

const sortedHistory = computed(() => {
    if (!currentNote.value || !currentNote.value.history) return [];
    return [...currentNote.value.history].sort((a, b) => b.ts - a.ts);
});

const normalizeCustomFieldType = (type: unknown): CustomFieldType => {
    if (type === 'number' || type === 'boolean') return type;
    return 'string';
};

const createCustomFieldItem = (key: string, type: unknown, value: unknown): CustomFieldItem => {
    const normalizedType = normalizeCustomFieldType(type);

    if (normalizedType === 'boolean') {
        return {
            key,
            type: 'boolean',
            value: value === true || value === 'true'
        };
    }

    return {
        key,
        type: normalizedType,
        value: value == null ? '' : String(value)
    };
};

const assignInheritedField = (
    target: Record<string, InheritedFieldItem>,
    ownKeys: Set<string>,
    key: unknown,
    type: unknown,
    value: unknown
) => {
    if (typeof key !== 'string' || ownKeys.has(key)) return;
    target[key] = {
        type: normalizeCustomFieldType(type),
        value: typeof value === 'boolean' || typeof value === 'number' ? value : String(value ?? '')
    };
    ownKeys.add(key);
};

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
    currentNote.value = {
        ...note,
        content: detailed.content || ''
    };
    
    // Initialize Custom Fields List
    if (note.custom_fields) {
        if (Array.isArray(note.custom_fields)) {
            // New List Format: [["key", "type", "value"], ...] or [{"key":...}]
            customFieldsList.value = note.custom_fields.map((item: any) => {
                if (Array.isArray(item) && item.length >= 3) {
                    return createCustomFieldItem(item[0], item[1], item[2]);
                } else if (typeof item === 'object') {
                    return createCustomFieldItem(item.key, item.type, item.value);
                }
                return null;
            }).filter((item: CustomFieldItem | null): item is CustomFieldItem => item !== null);
        } else if (typeof note.custom_fields === 'object') {
            // Legacy Dict Format support
            customFieldsList.value = Object.entries(note.custom_fields).map(([k, v]) => {
                const inferredType: CustomFieldType = typeof v === 'boolean'
                    ? 'boolean'
                    : typeof v === 'number'
                        ? 'number'
                        : 'string';
                return createCustomFieldItem(k, inferredType, v);
            });
        }
    } else {
        customFieldsList.value = [];
    }

    // Process Inherited Fields (Deduplicate against own fields)
    const ownKeys = new Set(customFieldsList.value.map(i => i.key));
    inheritedDirectFields.value = {};
    inheritedAncestorFields.value = {};

    if (note.inherited_fields) {
        // 1. Direct Parent
        if (note.inherited_fields.direct) {
             // Now it's a List of Lists: [["k", "t", "v"], ...]
             const list = note.inherited_fields.direct as any[];
             list.forEach((item: any) => {
                 if (Array.isArray(item) && item.length >= 3) {
                     const [k, t, v] = item;
                     assignInheritedField(inheritedDirectFields.value, ownKeys, k, t, v);
                 }
             });
        }
        
        // 2. Ancestors
        if (note.inherited_fields.ancestors) {
             const list = note.inherited_fields.ancestors as any[];
             list.forEach((item: any) => {
                 if (Array.isArray(item) && item.length >= 3) {
                     const [k, t, v] = item;
                     assignInheritedField(inheritedAncestorFields.value, ownKeys, k, t, v);
                 }
             });
        }
    }

    originalData.value = {
        title: detailed.title,
        content: detailed.content || '',
        weight: detailed.weight,
        start_at: detailed.start_at,
        node_type: detailed.node_type || 'note',
        node_status: detailed.node_status || 'idea',
        custom_fields: JSON.stringify(detailed.custom_fields || {})
    };
}

const checkAndSave = () => {
    if (!isReady.value || !currentNote.value || !originalData.value) return;
    
    const curr = currentNote.value;
    const orig = originalData.value;
    
    // Construct current custom fields list
    const currentFieldsList: any[] = [];
    customFieldsList.value.forEach(item => {
        if (item.key && item.key.trim()) {
            let val: any = item.value;
            // Type is already selected by user, trust it or validate?
            // User selected type in UI.
            
            // Format for List: [key, type, value]
            currentFieldsList.push([item.key.trim(), item.type, val]);
        }
    });
    // Update the object in currentNote so it's ready to be saved
    curr.custom_fields = currentFieldsList;

    const isChanged = 
        curr.content !== orig.content ||
        curr.title !== orig.title ||
        curr.weight !== orig.weight ||
        curr.start_at !== orig.start_at ||
        curr.node_type !== orig.node_type ||
        curr.node_status !== orig.node_status ||
        JSON.stringify(curr.custom_fields) !== orig.custom_fields;
    
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
            node_status: note.node_status,
            custom_fields: note.custom_fields
        });
        
        // Update original data to current
        originalData.value = {
            title: note.title,
            content: note.content || '',
            weight: note.weight,
            start_at: note.start_at,
            node_type: note.node_type || 'note',
            node_status: note.node_status || 'idea',
            custom_fields: JSON.stringify(note.custom_fields || {})
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

const addCustomField = () => {
    customFieldsList.value.push({ key: '', value: '', type: 'string' });
};

const addInheritedField = (key: string, val: string | number | boolean, typeFromInheritance?: string) => {
    // Determine type from parent value or use explicit type if available
    let type: CustomFieldType = 'string';
    
    if (typeFromInheritance && ['string', 'number', 'boolean'].includes(typeFromInheritance)) {
        type = typeFromInheritance as CustomFieldType;
    } else {
        // Fallback inference
        if (typeof val === 'boolean') type = 'boolean';
        else if (typeof val === 'number') type = 'number';
        else if (!isNaN(Number(val)) && String(val).trim() !== '') {
            // Looks like number, but stored as string? Default to string to be safe.
        }
    }

    // Add to own fields with empty value (but correct type)
    customFieldsList.value.push({ 
        key: key, 
        value: type === 'boolean' ? false : (type === 'number' ? "0" : ""), 
        type: type 
    });
    
    // Remove from inherited display lists
    if (inheritedDirectFields.value[key]) delete inheritedDirectFields.value[key];
    if (inheritedAncestorFields.value[key]) delete inheritedAncestorFields.value[key];
    
    checkAndSave();
};

const formatInheritedValue = (val: any) => {
    if (typeof val === 'boolean') return val ? 'True' : 'False';
    return String(val);
};

const removeCustomField = (index: number) => {
    customFieldsList.value.splice(index, 1);
    checkAndSave();
};

const handleCustomFieldChange = () => {
    checkAndSave();
};

const getTextFieldValue = (item: CustomFieldItem) => item.type === 'boolean' ? '' : String(item.value);

const setTextFieldValue = (item: CustomFieldItem, value: string | number) => {
    if (item.type === 'boolean') return;
    item.value = String(value ?? '');
    handleCustomFieldChange();
};

const setNumberFieldValue = (item: CustomFieldItem, value: string | number) => {
    if (item.type !== 'number') return;
    const nextValue = String(value ?? '');
    if (!/^-?\d*\.?\d*$/.test(nextValue)) return;
    item.value = nextValue;
    handleCustomFieldChange();
};

const getBooleanFieldValue = (item: CustomFieldItem) => item.type === 'boolean' ? Boolean(item.value) : false;

const setBooleanFieldValue = (item: CustomFieldItem, value: string | number | boolean) => {
    if (item.type !== 'boolean') return;
    item.value = Boolean(value);
    handleCustomFieldChange();
};

const handleCustomFieldTypeChange = (item: CustomFieldItem) => {
    // Reset value when type changes to avoid type mismatch confusion
    if (item.type === 'boolean') {
        item.value = false;
    } else if (item.type === 'number') {
        // Default to "0" string for number type
        item.value = "0";
    } else {
        item.value = '';
    }
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

const getFieldTypeLabel = (val: any) => {
    if (typeof val === 'boolean') return '布尔';
    if (typeof val === 'number') return '数值';
    const str = String(val);
    if (!isNaN(Number(str)) && str.trim() !== '') return '数值'; // Treat numeric string as number type for display
    return '文本';
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

/* Row 4: Custom Fields */
.header-row-custom {
    display: flex;
    flex-direction: column; /* Stack label and container vertically if needed, or keep horizontal but ensure container is full width */
    gap: 4px;
    margin-top: 5px;
}

.custom-fields-label {
    display: flex;
    align-items: center;
    gap: 5px;
}

.custom-fields-container {
    width: 100%; /* Ensure container takes full width */
    display: flex;
    flex-direction: column;
    gap: 0; /* Removed gap between groups */
}

.custom-fields-label .label {
    font-size: 12px;
    color: #606266;
    white-space: nowrap;
}

.custom-fields-list {
    display: flex;
    flex-direction: column; /* Change to column for table-like layout */
    gap: 0; /* No gap between items */
    flex: 1;
}

.custom-field-item {
    display: flex;
    align-items: flex-start;
    gap: 6px; /* Reduced from 10px */
    background: transparent;
    padding: 2px 0;
    border-radius: 0;
    border: none;
    border-bottom: 1px solid #f2f2f2;
}

.custom-field-item:last-child {
    border-bottom: none;
}

/* Specific styles for inherited fields to keep background but aligned */
.custom-field-item.inherited-field {
    background-color: #fdf6ec; 
    border-radius: 0; /* Remove individual radius */
    padding: 4px 8px;
    border: none;
    opacity: 0.8;
}

.custom-field-item.inherited-field.ancestor {
    background-color: #f4f4f5;
    opacity: 0.6;
}

/* Remove green border for own fields, use transparent background */
.custom-field-item.own-field {
    border: none;
    background-color: #f0f9eb; /* Keep light green background */
    box-shadow: none;
    opacity: 1;
    padding: 4px 8px; /* Consistent padding */
    border-radius: 0; /* Remove individual radius */
}

/* Add radius to container instead */
.custom-fields-container {
    width: 100%;
    display: flex;
    flex-direction: column;
    gap: 0;
    border-radius: 4px;
    overflow: hidden; /* Clips children to radius */
    border: 1px solid #f2f2f2;
}

/* Key Column */
.indicator-col,
.indicator-placeholder {
    width: 20px;
    flex: 0 0 20px;
    display: flex;
    align-items: center;
}

.custom-field-item .field-key,
.custom-field-item .field-key-read {
    width: 120px;
    min-width: 120px;
    flex: 0 0 120px;
    margin-top: 1px;
}

/* Type Column */
.field-type-select {
    width: 65px; /* Increased to avoid truncation */
    flex: 0 0 65px;
    margin-top: 1px;
}

/* Override Element Plus input padding to be extremely tight */
.field-type-select :deep(.el-input__wrapper) {
    padding: 0 2px !important;
}

/* Ensure suffix icon (arrow) is extremely close to text */
.field-type-select :deep(.el-input__suffix) {
    position: absolute;
    right: 2px;
    margin: 0;
}

/* Center text and reduce its own padding */
.field-type-select :deep(.el-input__inner) {
    text-align: left;
    padding-left: 4px;
    padding-right: 0;
    font-size: 12px;
}

.field-type-read {
    font-size: 12px;
    color: #909399;
    width: 65px;
    flex: 0 0 65px;
    display: flex;
    align-items: center;
    justify-content: flex-start;
    padding-left: 6px;
}

/* Value Column */
.field-value-container {
    flex: 1;
    display: flex;
    align-items: center;
    min-width: 0;
}

.custom-field-item .field-value,
.custom-field-item .field-value-read,
.number-input-wrapper,
.boolean-input-wrapper {
    width: 100%;
}

/* Ensure el-input takes full width of its container */
.custom-field-item .field-value :deep(.el-textarea__inner),
.custom-field-item .field-value :deep(.el-input__wrapper) {
    width: 100%;
}

/* Action Column */
.action-col {
    width: 32px;
    flex: 0 0 32px;
    display: flex;
    justify-content: flex-end;
    align-items: center;
}

.remove-btn, .add-btn {
    padding: 0;
    margin: 0;
}

.custom-field-item .field-value-read {
    padding-top: 2px;
    color: #606266;
}

/* Hide separator */
.custom-field-item .separator {
    display: none;
}

.inherited-field {
    /* Base style for inherited fields */
}

.inherited-field .field-key-read {
    font-size: 12px;
    color: #606266;
    width: 80px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    padding-left: 2px;
}

.inherited-field .field-value-read {
    font-size: 12px;
    color: #909399;
    width: 180px;
    min-width: 120px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

.inherited-indicator {
    font-size: 10px;
    color: #fff;
    background-color: #e6a23c; /* Direct parent: Warning/Orange */
    padding: 1px 4px;
    border-radius: 2px;
    line-height: 1.2;
    transform: scale(0.9);
    cursor: help;
}

.inherited-indicator.ancestor {
    background-color: #909399; /* Ancestor: Info/Grey */
}

.add-btn {
    padding: 0 4px;
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
