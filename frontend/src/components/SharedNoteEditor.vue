<template>
  <div class="shared-note-editor">
    <div v-if="props.loading" class="state-line">
      <el-icon class="is-loading"><Loading /></el-icon> 加载内容中...
    </div>
    <div v-else-if="!currentNote" class="state-block">
      <el-empty :description="props.emptyText || '未选择内容'" />
    </div>
    <div v-else class="panel-content">
      <div class="editor-header">
        <div class="header-row primary-row">
          <el-input
            v-model="currentNote.title"
            placeholder="节点标题"
            class="title-input"
            :readonly="titleReadonly"
            @input="queueMetaAutoSave()"
          />
          <slot name="actions" :note="currentNote" :readonly="effectiveReadonly" />
        </div>

        <div class="header-row secondary-row">
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
                class="start-date-picker"
                :disabled="effectiveReadonly"
              />
              <SmartTimeInput
                v-model="timeInputString"
                size="small"
                :input-style="smartTimeInputStyle"
                :disabled="effectiveReadonly"
                @change="handleTimeChange"
              />
            </span>
            <span class="time-tag">
              <el-icon><Clock /></el-icon> 更新: {{ formatDateDetailed(currentNote.updated_at) }}
            </span>
            <div class="meta-actions-slot">
              <slot name="meta-actions" :note="currentNote" :readonly="effectiveReadonly" />
            </div>
          </div>
          <div class="save-status">
            <span v-if="effectiveReadonly" class="status-readonly">只读</span>
            <span v-else-if="saveStatus === 'saved'" class="status-saved"><el-icon><Check /></el-icon> 已保存</span>
            <span v-else-if="saveStatus === 'saving'" class="status-saving"><el-icon class="is-loading"><Loading /></el-icon> 保存中...</span>
            <span v-else class="status-unsaved">未保存</span>
          </div>
        </div>

        <div class="header-row tertiary-row">
          <div class="inline-control weight-control">
            <span class="label">权重:</span>
            <el-input-number
              v-model="currentNote.weight"
              :min="0"
              :step="1"
              size="small"
              controls-position="right"
              :disabled="effectiveReadonly"
              @change="onWeightChange"
              @blur="onWeightBlur"
            />
          </div>

          <NodeSelector
            mode="type"
            v-model="currentNote.node_type"
            :custom-color="currentNote.color"
            label="类型"
            :show-label="true"
            :show-help-icon="true"
            :disabled="nodeTypeReadonly"
            @change="queueMetaAutoSave({ immediate: true })"
            @show-help="showHelpDialog = true"
          />

          <NodeSelector
            mode="status"
            v-model="currentNote.node_status"
            :related-type="currentNote.node_type"
            :custom-color="currentNote.color"
            label="状态"
            :show-label="true"
            :show-help-icon="false"
            :disabled="effectiveReadonly"
            @change="queueMetaAutoSave({ immediate: true })"
          />

          <div class="inline-control">
            <span class="label">颜色:</span>
            <div class="color-control-body">
              <el-popover
                v-model:visible="colorPickerVisible"
                trigger="click"
                placement="bottom-start"
                :width="320"
                :disabled="effectiveReadonly"
                popper-class="note-color-picker-popover"
              >
                <template #reference>
                  <button
                    type="button"
                    class="note-color-trigger"
                    :class="{ 'is-empty': !currentNote.color, 'is-disabled': effectiveReadonly }"
                    :disabled="effectiveReadonly"
                    aria-label="选择颜色"
                  >
                    <span class="note-color-trigger__swatch" :style="noteColorDisplayStyle">
                      <el-icon v-if="!currentNote.color" class="note-color-trigger__empty-icon"><Close /></el-icon>
                    </span>
                  </button>
                </template>

                <div class="note-color-panel-shell" @click="handleColorPanelClick">
                  <ElColorPickerPanel
                    :model-value="noteColorProxy"
                    :predefine="noteColorPresets"
                    color-format="hex"
                    :border="false"
                    :disabled="effectiveReadonly"
                    :validate-event="false"
                    @update:model-value="handleColorChange"
                  >
                    <template #footer>
                      <el-button
                        size="small"
                        class="color-panel-auto-close-btn"
                        :class="{ 'is-active': colorPickerAutoClose }"
                        @click.stop="toggleColorPickerAutoClose"
                      >
                        自动关闭窗口
                      </el-button>
                    </template>
                  </ElColorPickerPanel>
                </div>
              </el-popover>
              <el-button size="small" plain :disabled="effectiveReadonly || !currentNote.color" @click="resetNoteColor">
                默认
              </el-button>
            </div>
          </div>

          <div v-if="resolvedShowPrivateToggle" class="inline-control">
            <span class="label">私密:</span>
            <el-button
              size="small"
              plain
              :type="isPrivateEnabled ? 'danger' : undefined"
              :disabled="effectiveReadonly"
              @click="togglePrivateLevel"
            >
              {{ isPrivateEnabled ? '已开启' : '已关闭' }}
            </el-button>
            <span class="private-hint">值 {{ currentNote.private_level }}</span>
          </div>

          <div class="history-toggle">
            <el-button size="small" :type="historyButtonType" :icon="List" @click="showHistory = !showHistory">
              操作日志
            </el-button>
          </div>
        </div>

        <div class="custom-fields-row">
          <div class="custom-fields-label">
            <span class="label">自定义属性:</span>
            <el-button link type="primary" size="small" :disabled="effectiveReadonly" @click="addCustomField">
              <el-icon><Plus /></el-icon>
            </el-button>
          </div>

          <div class="custom-fields-container">
            <div ref="ownCustomFieldsListRef" class="custom-fields-list">
              <div v-for="(item, index) in customFieldsList" :key="item.localId" class="custom-field-item own-field">
                <button
                  type="button"
                  class="drag-handle-btn"
                  :disabled="effectiveReadonly"
                  title="拖拽调整顺序"
                  aria-label="拖拽调整顺序"
                >
                  <el-icon><Rank /></el-icon>
                </button>

                <el-input
                  v-model="item.key"
                  size="small"
                  placeholder="Key"
                  class="field-key"
                  :readonly="effectiveReadonly"
                  @input="handleCustomFieldChange"
                />

                <el-select
                  v-model="item.type"
                  size="small"
                  class="field-type-select"
                  :disabled="effectiveReadonly"
                  @change="handleCustomFieldTypeChange(item)"
                >
                  <el-option label="文本" value="string" />
                  <el-option label="数值" value="number" />
                  <el-option label="布尔" value="boolean" />
                </el-select>

                <div class="field-value-container">
                  <el-input
                    v-if="item.type === 'string'"
                    :model-value="getTextFieldValue(item)"
                    size="small"
                    type="textarea"
                    autosize
                    class="field-value"
                    :readonly="effectiveReadonly"
                    @update:model-value="value => setTextFieldValue(item, value)"
                  />

                  <el-input
                    v-else-if="item.type === 'number'"
                    :model-value="getTextFieldValue(item)"
                    size="small"
                    class="field-value"
                    :readonly="effectiveReadonly"
                    @update:model-value="value => setNumberFieldValue(item, value)"
                  />

                  <el-switch
                    v-else
                    :model-value="getBooleanFieldValue(item)"
                    size="small"
                    :disabled="effectiveReadonly"
                    @update:model-value="value => setBooleanFieldValue(item, value)"
                  />
                </div>

                <el-button link type="danger" size="small" :disabled="effectiveReadonly" @click="removeCustomField(index)">
                  <el-icon><Close /></el-icon>
                </el-button>
              </div>
            </div>

            <div v-for="(item, key) in inheritedDirectFields" :key="`direct-${key}`" class="custom-field-item inherited-field">
              <div class="inherited-indicator">父</div>
              <span class="field-key-read">{{ key }}</span>
              <span class="field-type-read">{{ getFieldTypeLabel(item.type, item.value) }}</span>
              <div class="field-value-container">
                <span class="field-value-read">{{ formatInheritedValue(item.value) }}</span>
              </div>
              <el-button link type="primary" size="small" :disabled="effectiveReadonly" @click="addInheritedField(String(key), item.value, item.type)">
                <el-icon><Plus /></el-icon>
              </el-button>
            </div>

            <div v-for="(item, key) in inheritedAncestorFields" :key="`ancestor-${key}`" class="custom-field-item inherited-field ancestor-field">
              <div class="inherited-indicator ancestor">祖</div>
              <span class="field-key-read">{{ key }}</span>
              <span class="field-type-read">{{ getFieldTypeLabel(item.type, item.value) }}</span>
              <div class="field-value-container">
                <span class="field-value-read">{{ formatInheritedValue(item.value) }}</span>
              </div>
              <el-button link type="primary" size="small" :disabled="effectiveReadonly" @click="addInheritedField(String(key), item.value, item.type)">
                <el-icon><Plus /></el-icon>
              </el-button>
            </div>
          </div>
        </div>

        <div v-if="showHistory" class="history-panel">
          <div v-if="!currentNote.history || currentNote.history.length === 0" class="state-line">暂无操作记录</div>
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

      <NoteEditor :key="currentNote.id || 'new'" v-model="currentNote.content" :readOnly="effectiveReadonly" @change="handleContentChange" />
    </div>

    <NodeHelpDialog v-model="showHelpDialog" />
  </div>
</template>

<script setup lang="ts">
import 'element-plus/es/components/color-picker-panel/style/css';
import { computed, defineAsyncComponent, nextTick, onUnmounted, ref, watch } from 'vue';
import Sortable from 'sortablejs';
import { Calendar, Check, Clock, Close, List, Loading, Plus, Rank } from '@element-plus/icons-vue';
import { ElColorPickerPanel } from 'element-plus';
import { ElMessage, ElMessageBox } from 'element-plus';
import type { NoteNode } from '@/api/notes';
import NodeHelpDialog from './NodeHelpDialog.vue';
import NodeSelector from './NodeSelector.vue';
import SmartTimeInput from './SmartTimeInput.vue';
import { formatNoteDateTimeDetailed } from '@/utils/noteDate';
import {
  applyEditableNoteSnapshot,
  areEditableNoteSnapshotsEqual,
  buildEditableNotePatch,
  buildNoteDraftStorageKey,
  convertNoteCustomFieldValue,
  createEditableNoteSnapshot,
  createNoteCustomFieldItem,
  noteCustomFieldItemsToList,
  noteCustomFieldsToItems,
  noteSnapshotToNode,
  normalizeNoteCustomFieldType,
  type EditableNotePatch,
  type EditableNoteSnapshot,
  type NoteCustomFieldItem,
  type NoteCustomFieldType
} from '@/utils/noteAutoSave';
import { NOTE_WEIGHT_DEFAULT, normalizeNoteWeight } from '@/utils/noteWeight';
import { useAutoSave } from '@/utils/useAutoSave';
import { getNodeStatusConfig, getNodeTypeConfig, normalizeNodeColor, NOTE_COLOR_PRESETS } from '@/utils/nodeConfig';

const NoteEditor = defineAsyncComponent(() => import('./NoteEditor.vue'));

const props = defineProps<{
  modelValue?: NoteNode;
  loading?: boolean;
  readonly?: boolean;
  emptyText?: string;
  showPrivateToggle?: boolean;
  lockTitle?: boolean;
  lockNodeType?: boolean;
  onSave?: (note: NoteNode, patch?: EditableNotePatch) => Promise<NoteNode | void>;
  onSaveKeepalive?: (note: NoteNode, patch?: EditableNotePatch) => void;
}>();

const emit = defineEmits<{
  (e: 'update:modelValue', note: NoteNode): void;
  (e: 'change', note: NoteNode): void;
}>();

interface InheritedFieldItem {
  type: NoteCustomFieldType;
  value: string | number | boolean;
}

const CONTENT_SAVE_DELAY_MS = 1800;
const META_SAVE_DELAY_MS = 450;
const smartTimeInputStyle = { width: '100px', marginLeft: '5px' } as const;

const currentNote = ref<NoteNode | undefined>();
const saveStatus = ref<'saved' | 'saving' | 'unsaved'>('saved');
const showHistory = ref(false);
const showHelpDialog = ref(false);
const historyButtonType = computed<'primary' | undefined>(() => showHistory.value ? 'primary' : undefined);
const effectiveReadonly = computed(() => Boolean(props.readonly) || currentNote.value?.can_edit === false);
const titleReadonly = computed(() => effectiveReadonly.value || Boolean(props.lockTitle));
const nodeTypeReadonly = computed(() => effectiveReadonly.value || Boolean(props.lockNodeType));
const resolvedShowPrivateToggle = computed(() => typeof props.showPrivateToggle === 'boolean' ? props.showPrivateToggle : currentNote.value?.node_type === 'note');
const isPrivateEnabled = computed(() => (currentNote.value?.private_level ?? 0) > 0);
const isReady = computed(() => !!currentNote.value && currentNote.value.content !== undefined);
const noteColorPresets = NOTE_COLOR_PRESETS;

const customFieldsList = ref<NoteCustomFieldItem[]>([]);
const ownCustomFieldsListRef = ref<HTMLElement | null>(null);
const inheritedDirectFields = ref<Record<string, InheritedFieldItem>>({});
const inheritedAncestorFields = ref<Record<string, InheritedFieldItem>>({});
const inheritedFieldSource = ref<NoteNode['inherited_fields'] | null>(null);
const timeInputString = ref('');
const currentDraftKey = ref<string | null>(null);
const colorPickerVisible = ref(false);
const colorPickerAutoClose = ref(false);
let loadRequestToken = 0;
let customFieldsSortable: Sortable | null = null;

const noteColorProxy = computed<string>({
  get: () => currentNote.value?.color ?? '',
  set: value => {
    if (!currentNote.value) return;
    currentNote.value.color = normalizeNodeColor(value) ?? null;
  }
});

const noteColorDisplayStyle = computed(() => ({ backgroundColor: currentNote.value?.color ?? 'transparent' }));

const sortedHistory = computed(() => currentNote.value?.history ? [...currentNote.value.history].sort((a, b) => b.ts - a.ts) : []);

const startDateProxy = computed<Date | undefined>({
  get: () => currentNote.value ? new Date(currentNote.value.start_at) : undefined,
  set: value => {
    if (!currentNote.value || !value || effectiveReadonly.value) return;
    const original = new Date(currentNote.value.start_at);
    original.setFullYear(value.getFullYear());
    original.setMonth(value.getMonth());
    original.setDate(value.getDate());
    currentNote.value.start_at = original.getTime();
    queueMetaAutoSave();
  }
});

const buildCurrentSnapshot = (): EditableNoteSnapshot | null => createEditableNoteSnapshot(currentNote.value, noteCustomFieldItemsToList(customFieldsList.value));

const normalizeIncomingNote = (note: NoteNode) => {
  const cloned = JSON.parse(JSON.stringify(note)) as NoteNode;
  if (cloned.start_at && cloned.start_at < 10000000000) cloned.start_at *= 1000;
  if (cloned.updated_at && cloned.updated_at < 10000000000) cloned.updated_at *= 1000;
  if (cloned.created_at && cloned.created_at < 10000000000) cloned.created_at *= 1000;
  cloned.can_edit = Boolean(cloned.can_edit);
  return cloned;
};

const cloneInheritedFieldSource = (source: NoteNode['inherited_fields'] | null | undefined) => (
  source ? JSON.parse(JSON.stringify(source)) as NoteNode['inherited_fields'] : null
);

const assignInheritedField = (
  target: Record<string, InheritedFieldItem>,
  ownKeys: Set<string>,
  seenInheritedKeys: Set<string>,
  key: unknown,
  type: unknown,
  value: unknown
) => {
  if (typeof key !== 'string') return;
  const normalizedKey = key.trim();
  if (!normalizedKey || ownKeys.has(normalizedKey) || seenInheritedKeys.has(normalizedKey)) return;
  target[normalizedKey] = {
    type: normalizeNoteCustomFieldType(type),
    value: typeof value === 'boolean' || typeof value === 'number' ? value : String(value ?? '')
  };
  seenInheritedKeys.add(normalizedKey);
};

const refreshInheritedFields = (source?: Partial<NoteNode> | null) => {
  inheritedDirectFields.value = {};
  inheritedAncestorFields.value = {};
  if (source?.inherited_fields) {
    inheritedFieldSource.value = cloneInheritedFieldSource(source.inherited_fields);
  }

  const inheritedFields = source?.inherited_fields || inheritedFieldSource.value;
  if (!inheritedFields) return;

  const ownKeys = new Set(customFieldsList.value.map(item => item.key.trim()).filter(Boolean));
  const seenInheritedKeys = new Set<string>();
  const appendFields = (target: Record<string, InheritedFieldItem>, list: unknown) => {
    if (!Array.isArray(list)) return;
    list.forEach(item => {
      if (Array.isArray(item) && item.length >= 3) {
        assignInheritedField(target, ownKeys, seenInheritedKeys, item[0], item[1], item[2]);
      }
    });
  };

  appendFields(inheritedDirectFields.value, inheritedFields.direct);
  appendFields(inheritedAncestorFields.value, inheritedFields.ancestors);
};

const syncCurrentNoteFromSnapshot = (snapshot: EditableNoteSnapshot, source?: Partial<NoteNode> | null) => {
  if (!currentNote.value && !source) return;
  currentNote.value = applyEditableNoteSnapshot((source ? { ...(currentNote.value as NoteNode | undefined), ...source } : currentNote.value!) as NoteNode, snapshot);
  customFieldsList.value = noteCustomFieldsToItems(snapshot.custom_fields);
  refreshInheritedFields(source ?? currentNote.value);
  if (currentNote.value) emit('update:modelValue', currentNote.value);
};

const destroyCustomFieldSortable = () => {
  if (!customFieldsSortable) return;
  customFieldsSortable.destroy();
  customFieldsSortable = null;
};

const initCustomFieldSortable = () => {
  destroyCustomFieldSortable();
  if (!ownCustomFieldsListRef.value || effectiveReadonly.value) return;
  customFieldsSortable = Sortable.create(ownCustomFieldsListRef.value, {
    handle: '.drag-handle-btn',
    animation: 150,
    ghostClass: 'custom-field-sortable-ghost',
    onEnd: ({ oldIndex, newIndex }) => {
      if (oldIndex == null || newIndex == null || oldIndex === newIndex) return;
      const reordered = [...customFieldsList.value];
      const [movedItem] = reordered.splice(oldIndex, 1);
      if (!movedItem) return;
      reordered.splice(Math.min(newIndex, reordered.length), 0, movedItem);
      customFieldsList.value = reordered;
      syncCustomFields();
    }
  });
};

const autoSave = useAutoSave<EditableNoteSnapshot>({
  debounceMs: 2000,
  equals: areEditableNoteSnapshotsEqual,
  storageKey: () => currentDraftKey.value,
  save: async snapshot => {
    if (!props.onSave) return snapshot;
    const baseline = autoSave.getBaselineSnapshot();
    const patch = buildEditableNotePatch(snapshot, baseline);
    if (!Object.keys(patch).length) return snapshot;
    const updatedNote = await props.onSave(noteSnapshotToNode(currentNote.value, snapshot), patch);
    const normalizedSavedNote = updatedNote ? normalizeIncomingNote(updatedNote) : null;
    const canonicalSnapshot = createEditableNoteSnapshot(normalizedSavedNote || noteSnapshotToNode(currentNote.value, snapshot)) ?? snapshot;
    if (currentNote.value?.id === snapshot.id) {
      const latestSnapshot = buildCurrentSnapshot() ?? autoSave.getLatestSnapshot();
      const hasNewerLocalDraft = latestSnapshot
        ? !areEditableNoteSnapshotsEqual(latestSnapshot, snapshot)
        : false;
      syncCurrentNoteFromSnapshot(
        hasNewerLocalDraft && latestSnapshot ? latestSnapshot : canonicalSnapshot,
        normalizedSavedNote
      );
      emit('change', currentNote.value);
    }
    return canonicalSnapshot;
  },
  onError: error => console.error(error),
  saveOnPageHide: (snapshot, baselineSnapshot) => {
    if (!props.onSaveKeepalive) return;
    const patch = buildEditableNotePatch(snapshot, baselineSnapshot);
    if (!Object.keys(patch).length) return;
    props.onSaveKeepalive(noteSnapshotToNode(currentNote.value, snapshot), patch);
  }
});

watch(autoSave.saveStatus, value => { saveStatus.value = value; });

watch(() => props.modelValue, async newVal => {
  const requestToken = ++loadRequestToken;

  if (currentNote.value && autoSave.hasUnsavedChanges.value && (!newVal || currentNote.value.id !== newVal.id)) {
    await autoSave.flush();
  }

  if (!newVal) {
    currentDraftKey.value = null;
    currentNote.value = undefined;
    customFieldsList.value = [];
    inheritedFieldSource.value = null;
    refreshInheritedFields(null);
    autoSave.loadSnapshot(null, { draftStrategy: 'discard' });
    return;
  }

  const note = normalizeIncomingNote(newVal);
  const serverSnapshot = createEditableNoteSnapshot(note);
  if (!serverSnapshot) return;
  currentDraftKey.value = buildNoteDraftStorageKey(note.id, note.title);
  if ((!currentNote.value || currentNote.value.id !== note.id) && note.inherited_fields == null) {
    inheritedFieldSource.value = null;
  }

  if (currentNote.value && currentNote.value.id === note.id) {
    if (autoSave.hasUnsavedChanges.value) {
      const liveSnapshot = buildCurrentSnapshot() ?? autoSave.getLatestSnapshot();
      const mergedSource = { ...(currentNote.value as NoteNode), ...note };
      if (liveSnapshot) syncCurrentNoteFromSnapshot(liveSnapshot, mergedSource);
      else {
        currentNote.value = mergedSource;
        refreshInheritedFields(currentNote.value);
        emit('update:modelValue', currentNote.value);
      }
      return;
    }

    const { snapshot: cleanSnapshot } = autoSave.loadSnapshot(serverSnapshot, { draftStrategy: 'discard' });
    syncCurrentNoteFromSnapshot(cleanSnapshot ?? serverSnapshot, note);
    saveStatus.value = autoSave.saveStatus.value;
    showHistory.value = false;
    return;
  }

  const { snapshot: loadedSnapshot, pendingDraft, expiredDraft } = autoSave.loadSnapshot(serverSnapshot);
  let activeSnapshot = loadedSnapshot ?? serverSnapshot;

  if (expiredDraft) ElMessage.info('发现过期本地草稿，已忽略');

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

      if (requestToken !== loadRequestToken || props.modelValue?.id !== note.id) return;
      autoSave.restoreDraft(pendingDraft.snapshot);
      activeSnapshot = pendingDraft.snapshot;
      ElMessage.warning(pendingDraft.hasConflict ? '已恢复本地草稿，请留意与服务器版本的差异' : '已恢复本地草稿');
    } catch {
      if (requestToken !== loadRequestToken || props.modelValue?.id !== note.id) return;
      autoSave.clearDraft();
    }
  }

  syncCurrentNoteFromSnapshot(activeSnapshot, note);
  saveStatus.value = autoSave.saveStatus.value;
  showHistory.value = false;
}, { immediate: true });

watch([() => currentNote.value?.id, () => effectiveReadonly.value], async ([noteId, readonly]) => {
  colorPickerVisible.value = false;
  if (!noteId || readonly) {
    destroyCustomFieldSortable();
    return;
  }
  await nextTick();
  initCustomFieldSortable();
}, { immediate: true });

watch(() => currentNote.value?.start_at, value => {
  if (!value) {
    timeInputString.value = '';
    return;
  }
  const d = new Date(value);
  timeInputString.value = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`;
}, { immediate: true });

onUnmounted(() => destroyCustomFieldSortable());

const queueAutoSave = (options: { immediate?: boolean; delayMs?: number } = {}) => {
  if (!isReady.value || effectiveReadonly.value) return;
  const snapshot = buildCurrentSnapshot();
  if (!snapshot) return;
  currentNote.value!.custom_fields = snapshot.custom_fields;
  autoSave.markDirty(snapshot, options);
};

const queueMetaAutoSave = (options: { immediate?: boolean } = {}) => {
  queueAutoSave({ immediate: options.immediate, delayMs: options.immediate ? 0 : META_SAVE_DELAY_MS });
};

const handleContentChange = (html: string) => {
  if (!currentNote.value || effectiveReadonly.value) return;
  currentNote.value.content = html;
  queueAutoSave({ delayMs: CONTENT_SAVE_DELAY_MS });
};

const syncCustomFields = (options: { immediate?: boolean } = {}) => {
  if (!currentNote.value) return;
  currentNote.value.custom_fields = noteCustomFieldItemsToList(customFieldsList.value);
  refreshInheritedFields(currentNote.value);
  queueMetaAutoSave(options);
};

const onWeightChange = (value: number | undefined) => {
  if (!currentNote.value || effectiveReadonly.value) return;
  if (value == null || Number.isNaN(value)) {
    const baseline = autoSave.getBaselineSnapshot();
    currentNote.value.weight = baseline ? normalizeNoteWeight(baseline.weight) : NOTE_WEIGHT_DEFAULT;
    return;
  }
  currentNote.value.weight = normalizeNoteWeight(value);
  queueMetaAutoSave();
};

const onWeightBlur = () => { if (currentNote.value) onWeightChange(currentNote.value.weight); };

const handleTimeChange = (value: string) => {
  if (!value || !currentNote.value || effectiveReadonly.value) return;
  const d = new Date(currentNote.value.start_at);
  const [h, m, s] = value.split(':').map(Number);
  if (d.getHours() === h && d.getMinutes() === m && d.getSeconds() === s) return;
  d.setHours(h);
  d.setMinutes(m);
  d.setSeconds(s);
  currentNote.value.start_at = d.getTime();
  queueMetaAutoSave();
};

const handleColorChange = (value: string | null) => {
  if (!currentNote.value || effectiveReadonly.value) return;
  currentNote.value.color = normalizeNodeColor(value) ?? null;
  queueMetaAutoSave();
};

const toggleColorPickerAutoClose = () => {
  colorPickerAutoClose.value = !colorPickerAutoClose.value;
};

const handleColorPanelClick = (event: MouseEvent) => {
  if (!colorPickerAutoClose.value) return;
  const target = event.target as HTMLElement | null;
  if (target?.closest('.el-color-predefine__color-selector')) {
    colorPickerVisible.value = false;
  }
};

const resetNoteColor = () => {
  if (!currentNote.value || !currentNote.value.color || effectiveReadonly.value) return;
  currentNote.value.color = null;
  queueMetaAutoSave({ immediate: true });
};

const togglePrivateLevel = () => {
  if (!currentNote.value || effectiveReadonly.value) return;
  currentNote.value.private_level = currentNote.value.private_level > 0 ? 0 : 1;
  queueMetaAutoSave({ immediate: true });
};

const addCustomField = () => {
  if (effectiveReadonly.value) return;
  customFieldsList.value.push(createNoteCustomFieldItem());
  nextTick(() => initCustomFieldSortable());
};

const addInheritedField = (key: string, value: string | number | boolean, typeFromInheritance?: string) => {
  if (effectiveReadonly.value) return;
  let type: NoteCustomFieldType = 'string';
  if (typeFromInheritance && ['string', 'number', 'boolean'].includes(typeFromInheritance)) type = typeFromInheritance as NoteCustomFieldType;
  else if (typeof value === 'boolean') type = 'boolean';
  else if (typeof value === 'number') type = 'number';
  customFieldsList.value.push(createNoteCustomFieldItem(key, type, value));
  syncCustomFields();
  nextTick(() => initCustomFieldSortable());
};

const removeCustomField = (index: number) => {
  if (effectiveReadonly.value) return;
  customFieldsList.value.splice(index, 1);
  syncCustomFields();
  nextTick(() => initCustomFieldSortable());
};

const handleCustomFieldChange = () => {
  if (effectiveReadonly.value) return;
  syncCustomFields();
};

const handleCustomFieldTypeChange = (item: NoteCustomFieldItem) => {
  if (effectiveReadonly.value) return;
  item.value = convertNoteCustomFieldValue(item.type, item.value);
  syncCustomFields({ immediate: true });
};

const getTextFieldValue = (item: NoteCustomFieldItem) => item.type === 'boolean' ? '' : String(item.value);
const getBooleanFieldValue = (item: NoteCustomFieldItem) => item.type === 'boolean' ? Boolean(item.value) : false;

const setTextFieldValue = (item: NoteCustomFieldItem, value: string | number) => {
  if (effectiveReadonly.value || item.type === 'boolean') return;
  item.value = String(value ?? '');
  handleCustomFieldChange();
};

const setNumberFieldValue = (item: NoteCustomFieldItem, value: string | number) => {
  if (effectiveReadonly.value || item.type !== 'number') return;
  item.value = String(value ?? '');
  handleCustomFieldChange();
};

const setBooleanFieldValue = (item: NoteCustomFieldItem, value: string | number | boolean) => {
  if (effectiveReadonly.value || item.type !== 'boolean') return;
  item.value = Boolean(value);
  handleCustomFieldChange();
};

const formatDateDetailed = (timestamp: number) => formatNoteDateTimeDetailed(timestamp);
const getFieldName = (field: string) => ({ n: '类型', s: '状态', t: '标题', w: '权重', cl: '颜色', p: '私密', c: '内容' }[field] || field);
const getFieldTagType = (field: string): 'primary' | 'success' | 'info' | 'warning' | 'danger' | undefined => (({ n: 'primary', s: 'warning', t: undefined, w: 'success', cl: 'success', p: 'danger', c: 'info' } as const)[field]);

const formatHistoryValue = (field: string, value: any) => {
  if (field === 'n') return getNodeTypeConfig(value).label;
  if (field === 's') return getNodeStatusConfig(value).label;
  if (field === 'cl') return value ? String(value).toUpperCase() : '跟随类型';
  if (field === 'p') return Number(value) > 0 ? `开启 (${value})` : '关闭';
  if (field === 'c') return `${value} 字`;
  return value;
};

const formatInheritedValue = (value: any) => typeof value === 'boolean' ? (value ? 'True' : 'False') : String(value);

const getFieldTypeLabel = (type: unknown, value?: any) => {
  if (type === 'string' || type === 'number' || type === 'boolean') {
    const normalizedType = normalizeNoteCustomFieldType(type);
    return normalizedType === 'boolean' ? '布尔' : normalizedType === 'number' ? '数值' : '文本';
  }
  if (typeof type === 'boolean') return '布尔';
  if (typeof type === 'number') return '数值';
  const rawValue = value === undefined ? type : value;
  const text = String(rawValue ?? '');
  return !Number.isNaN(Number(text)) && text.trim() !== '' ? '数值' : '文本';
};
</script>

<style scoped>
.shared-note-editor,.panel-content{display:flex;flex-direction:column;min-height:320px}
.editor-header{display:flex;flex-direction:column;gap:12px;margin-bottom:20px;padding-bottom:15px;border-bottom:1px solid #f0f0f0}
.header-row{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.primary-row{gap:10px}.secondary-row{justify-content:space-between;font-size:12px;color:#909399}
.title-input{flex:1;font-size:18px}
.meta-group,.meta-actions-slot,.inline-control,.color-control-body,.save-status,.time-tag{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.note-color-trigger{display:inline-flex;align-items:center;justify-content:center;width:32px;height:32px;padding:4px;border:1px solid var(--el-border-color);border-radius:4px;background:#fff;cursor:pointer;transition:border-color .2s ease,box-shadow .2s ease}
.note-color-trigger:hover:not(:disabled){border-color:var(--el-border-color-hover)}.note-color-trigger:focus-visible{outline:2px solid var(--el-color-primary);outline-offset:1px}
.note-color-trigger.is-disabled{cursor:not-allowed;background:var(--el-fill-color-light);opacity:.8}.note-color-trigger__swatch{display:inline-flex;align-items:center;justify-content:center;width:100%;height:100%;border:1px solid var(--el-text-color-secondary);border-radius:var(--el-border-radius-small);background-clip:padding-box}
.note-color-trigger.is-empty .note-color-trigger__swatch{background-image:linear-gradient(45deg,#f2f3f5 25%,transparent 25%),linear-gradient(135deg,#f2f3f5 25%,transparent 25%),linear-gradient(45deg,transparent 75%,#f2f3f5 75%),linear-gradient(135deg,transparent 75%,#f2f3f5 75%);background-position:0 0,6px 0,6px -6px,0 6px;background-size:12px 12px}
.note-color-trigger__empty-icon{font-size:12px;color:var(--el-text-color-secondary)}
.note-color-panel-shell{display:flex}
.weight-control{width:150px}.label,.private-hint,.custom-fields-label .label{font-size:12px;color:#606266;white-space:nowrap}
.history-toggle{margin-left:auto}.start-date-picker{width:130px;margin-left:5px}
.custom-fields-row{display:flex;flex-direction:column;gap:4px;margin-top:5px}.custom-fields-label{display:flex;align-items:center;gap:5px}
.custom-fields-container{width:100%;display:flex;flex-direction:column;border:1px solid #f2f2f2;border-radius:4px;overflow:hidden}
.custom-fields-list{display:flex;flex-direction:column}.custom-field-item{display:flex;align-items:flex-start;gap:6px;padding:4px 8px;border-bottom:1px solid #f2f2f2}
.custom-field-item:last-child{border-bottom:none}.own-field{background:#f0f9eb}.inherited-field{background:#fdf6ec;opacity:.85}.ancestor-field{background:#f4f4f5;opacity:.7}
.drag-handle-btn{display:inline-flex;align-items:center;justify-content:center;width:20px;height:20px;padding:0;border:none;background:transparent;color:#909399;cursor:move;border-radius:4px}
.drag-handle-btn:hover:not(:disabled){background:rgba(64,158,255,.08);color:#409eff}.drag-handle-btn:disabled{cursor:not-allowed;opacity:.45}
.field-key,.field-key-read{width:120px;min-width:120px}.field-type-select,.field-type-read{width:70px;min-width:70px;font-size:12px;color:#909399}
.field-value-container{flex:1;display:flex;align-items:center;min-width:0}.field-value,.field-value-read{width:100%}.field-value-read{color:#606266;padding-top:2px}
.inherited-indicator{font-size:10px;color:#fff;background:#e6a23c;padding:1px 4px;border-radius:2px;line-height:1.2}.inherited-indicator.ancestor{background:#909399}
.custom-field-sortable-ghost{opacity:.7;background-color:#ecf5ff !important}
.history-panel{margin-top:15px;padding:10px;background:#f8f9fb;border-radius:4px;max-height:200px;overflow-y:auto;font-size:13px;border:1px solid #ebeef5}
.history-list{display:flex;flex-direction:column}.history-item{display:flex;align-items:flex-start;gap:15px;padding:6px 0;border-bottom:1px dashed #ebeef5}.history-item:last-child{border-bottom:none}
.history-time{color:#909399;white-space:nowrap;font-family:monospace}.history-content{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.field-tag{min-width:40px;text-align:center}.history-value{color:#303133;word-break:break-all}
.status-saved{color:#67c23a}.status-saving{color:#e6a23c}.status-unsaved,.status-readonly{color:#909399}
.state-line,.state-block{display:flex;justify-content:center;align-items:center;color:#909399}.state-line{gap:8px;font-size:14px}.state-block{height:100%}
:deep(.note-color-picker-popover){padding:0;border:none !important;box-shadow:var(--el-box-shadow-light)}
:deep(.note-color-picker-popover .el-popover__title){display:none}
:deep(.note-color-picker-popover .el-color-picker-panel){width:300px;padding:12px}
:deep(.note-color-picker-popover .el-color-picker-panel__footer){align-items:center;gap:8px}
:deep(.note-color-picker-popover .el-color-picker-panel__footer .el-input){width:150px}
.color-panel-auto-close-btn{border-color:var(--el-border-color);color:var(--el-text-color-secondary);background:var(--el-fill-color-light)}
.color-panel-auto-close-btn.is-active{border-color:#0f766e;color:#fff;background:linear-gradient(135deg,#14b8a6,#0f766e)}
</style>
