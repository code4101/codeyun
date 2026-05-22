<template>
  <div
    class="shared-note-editor"
    :class="[`is-${effectiveEditorLayout}`, { 'is-readonly-presentation': readonlyPresentationActive }]"
    @keydown.capture="handleLocalUndoRedoKeydown"
  >
    <div v-if="props.loading" class="state-line">
      <el-icon class="is-loading"><Loading /></el-icon> 加载内容中...
    </div>
    <div v-else-if="!currentNote" class="state-block">
      <el-empty :description="props.emptyText || '未选择内容'" />
    </div>
    <div v-else class="panel-content" :class="`is-${effectiveEditorLayout}`">
      <div class="editor-header">
        <div class="header-row primary-row">
          <el-input
            v-if="!readonlyPresentationActive"
            v-model="currentNote.title"
            placeholder="节点标题"
            class="title-input"
            :readonly="titleReadonly"
            @focus="beginLocalEditHistory('title')"
            @input="handleTitleInput"
            @blur="endLocalEditHistory"
          />
          <h1 v-else class="readonly-title">{{ currentNote.title || '未命名文档' }}</h1>
          <slot name="actions" :note="currentNote" :readonly="effectiveReadonly" />
        </div>

        <div v-if="!readonlyPresentationActive" class="header-row secondary-row">
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

        <div v-if="!readonlyPresentationActive" class="header-row tertiary-row">
          <div class="inline-control weight-control">
            <span class="label">权重:</span>
            <el-input-number
              v-model="currentNote.weight"
              :min="NOTE_WEIGHT_MIN"
              :step="1"
              size="small"
              controls-position="right"
              class="weight-value-input"
              :disabled="effectiveReadonly"
              @change="onWeightChange"
              @blur="onWeightBlur"
            />
          </div>

          <NoteTypeSelector
            :model-value="currentNote.note_categories"
            :legacy-type="currentNote.primary_category"
            :legacy-color="currentNote.color"
            label="分类"
            :show-label="true"
            :show-help-icon="true"
            :disabled="nodeTypeReadonly"
            @update:model-value="handleNoteCategoriesChange"
            @show-help="showHelpDialog = true"
          />

          <NodeSelector
            mode="form"
            v-model="currentNote.note_form"
            :related-type="currentPrimaryType"
            :custom-color="currentNote.color"
            :note-types="currentNote.note_categories"
            label="形态"
            :show-label="true"
            :show-help-icon="false"
            trigger-min-width="72px"
            :disabled="formReadonly"
            @change="handleNoteFormChange"
          />

          <NodeSelector
            mode="status"
            v-model="currentNote.lifecycle_stage"
            :related-type="currentPrimaryType"
            :custom-color="currentNote.color"
            :note-types="currentNote.note_categories"
            :completion-progress="currentCompletionProgress"
            label="阶段"
            :show-label="true"
            :show-help-icon="false"
            trigger-min-width="72px"
            :disabled="effectiveReadonly"
            @change="handleLifecycleStageChange"
          />

          <div v-if="showCompletionProgressControl" class="inline-control progress-control">
            <span class="label">进度:</span>
            <el-input
              :model-value="completionProgressExpr"
              size="small"
              class="progress-expr-input"
              placeholder="支持 0.41、41/83、(1+2*2)/(4+7)"
              :readonly="effectiveReadonly"
              @focus="beginLocalEditHistory('completion-progress')"
              @update:model-value="handleCompletionProgressExprChange"
              @blur="handleCompletionProgressExprEditBlur"
            />
          </div>

          <div v-if="resolvedShowPrivateToggle" class="inline-control private-control">
            <span class="label">私密:</span>
            <el-input-number
              v-model="currentNote.private_level"
              :min="0"
              :step="1"
              size="small"
              controls-position="right"
              class="private-level-control"
              :disabled="effectiveReadonly"
              @change="onPrivateLevelChange"
              @blur="onPrivateLevelBlur"
            />
          </div>

        </div>

        <div v-if="!readonlyPresentationActive" class="custom-fields-row" :class="{ 'is-collapsed': customFieldsCollapsed }">
          <div class="custom-fields-label">
            <div class="custom-fields-main">
              <button
                type="button"
                class="custom-fields-toggle"
                :aria-expanded="String(!customFieldsCollapsed)"
                @click="toggleCustomFieldsCollapsed"
              >
                <el-icon class="custom-fields-toggle-icon">
                  <ArrowRight v-if="customFieldsCollapsed" />
                  <ArrowDown v-else />
                </el-icon>
                <span class="label">自定义属性</span>
                <span class="custom-fields-count" :class="{ 'is-empty': customFieldsTotalCount === 0 }">
                  {{ customFieldsSummary }}
                </span>
              </button>
              <el-button link type="primary" size="small" :disabled="effectiveReadonly" @click="addCustomField">
                <el-icon><Plus /></el-icon>
              </el-button>
            </div>
            <div class="history-toggle">
              <el-button size="small" :type="historyButtonType" :icon="List" @click="showHistory = !showHistory">
                操作日志
              </el-button>
            </div>
          </div>

          <div v-show="!customFieldsCollapsed" class="custom-fields-container" :style="customFieldColumnStyle">
            <div ref="ownCustomFieldsListRef" class="custom-fields-list">
              <div
                v-for="(item, index) in customFieldsList"
                :key="item.localId"
                class="custom-field-item own-field"
                :class="{ 'is-richtext-field': item.type === 'richtext' }"
              >
                <SortableOrderHandle
                  :index="index"
                  :total="customFieldsList.length"
                  size="xs"
                  :disabled="effectiveReadonly"
                />

                <el-input
                  v-model="item.key"
                  size="small"
                  placeholder="Key"
                  class="field-key"
                  :readonly="effectiveReadonly"
                  @focus="beginLocalEditHistory('custom-field')"
                  @input="handleCustomFieldChange"
                  @blur="endLocalEditHistory"
                />

                <button
                  type="button"
                  class="field-width-resizer"
                  title="拖拽调整名称列宽度，双击恢复自动宽度"
                  aria-label="拖拽调整名称列宽度，双击恢复自动宽度"
                  @pointerdown="startCustomFieldKeyResize"
                  @dblclick="resetCustomFieldKeyWidthAuto"
                ></button>

                <el-select
                  v-model="item.type"
                  size="small"
                  class="field-type-select"
                  :disabled="effectiveReadonly"
                  @change="handleCustomFieldTypeChange(item)"
                >
                  <el-option label="文本" value="string" />
                  <el-option label="富文本" value="richtext" />
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
                    @focus="beginLocalEditHistory('custom-field')"
                    @update:model-value="value => setTextFieldValue(item, value)"
                    @blur="endLocalEditHistory"
                  />

                  <div v-else-if="item.type === 'richtext'" class="field-richtext-editor">
                    <NoteEditor
                      :model-value="getTextFieldValue(item)"
                      layout="flow"
                      :readOnly="effectiveReadonly"
                      :show-toolbar="false"
                      :auto-focus-on-empty="false"
                      :min-height="84"
                      @update:model-value="value => setTextFieldValue(item, value)"
                    />
                  </div>

                  <el-input
                    v-else-if="item.type === 'number'"
                    :model-value="getTextFieldValue(item)"
                    size="small"
                    class="field-value"
                    :readonly="effectiveReadonly"
                    @focus="beginLocalEditHistory('custom-field')"
                    @update:model-value="value => setNumberFieldValue(item, value)"
                    @blur="endLocalEditHistory"
                  />

                  <el-switch
                    v-else
                    :model-value="getBooleanFieldValue(item)"
                    size="small"
                    :disabled="effectiveReadonly"
                    @update:model-value="value => setBooleanFieldValue(item, value)"
                  />
                </div>

                <el-button
                  link
                  type="danger"
                  size="small"
                  class="field-action-btn"
                  :disabled="effectiveReadonly"
                  @click="removeCustomField(index)"
                >
                  <el-icon><Close /></el-icon>
                </el-button>
              </div>
            </div>

            <div
              v-for="(item, key) in inheritedDirectFields"
              :key="`direct-${key}`"
              class="custom-field-item inherited-field"
              :class="{ 'is-richtext-field': item.type === 'richtext' }"
            >
              <div class="inherited-indicator">父</div>
              <span class="field-key-read">{{ key }}</span>
              <div class="field-width-resizer-spacer" aria-hidden="true"></div>
              <span class="field-type-read">{{ getFieldTypeLabel(item.type, item.value) }}</span>
              <div class="field-value-container">
                <div
                  v-if="item.type === 'richtext'"
                  class="field-value-richtext-read"
                  v-html="getInheritedRichTextHtml(item.value)"
                ></div>
                <span v-else class="field-value-read">{{ formatInheritedValue(item.value) }}</span>
              </div>
              <el-button
                link
                type="primary"
                size="small"
                class="field-action-btn"
                :disabled="effectiveReadonly"
                @click="addInheritedField(String(key), item.value, item.type)"
              >
                <el-icon><Plus /></el-icon>
              </el-button>
            </div>

            <div
              v-for="(item, key) in inheritedAncestorFields"
              :key="`ancestor-${key}`"
              class="custom-field-item inherited-field ancestor-field"
              :class="{ 'is-richtext-field': item.type === 'richtext' }"
            >
              <div class="inherited-indicator ancestor">祖</div>
              <span class="field-key-read">{{ key }}</span>
              <div class="field-width-resizer-spacer" aria-hidden="true"></div>
              <span class="field-type-read">{{ getFieldTypeLabel(item.type, item.value) }}</span>
              <div class="field-value-container">
                <div
                  v-if="item.type === 'richtext'"
                  class="field-value-richtext-read"
                  v-html="getInheritedRichTextHtml(item.value)"
                ></div>
                <span v-else class="field-value-read">{{ formatInheritedValue(item.value) }}</span>
              </div>
              <el-button
                link
                type="primary"
                size="small"
                class="field-action-btn"
                :disabled="effectiveReadonly"
                @click="addInheritedField(String(key), item.value, item.type)"
              >
                <el-icon><Plus /></el-icon>
              </el-button>
            </div>
          </div>
        </div>

        <div v-if="!readonlyPresentationActive && showHistory" class="history-panel">
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

      <div
        v-if="showSourceHtmlEditor"
        ref="sourceHtmlEditorRef"
        class="source-html-preview"
        :class="`is-${effectiveEditorLayout}`"
        :contenteditable="!effectiveReadonly"
        :aria-readonly="String(effectiveReadonly)"
        @click="handleSourceHtmlClick"
        @focus="beginLocalEditHistory('content')"
        @input="handleSourceHtmlInput"
        @blur="handleSourceHtmlEditBlur"
      ></div>
      <NoteEditor
        v-else
        :key="currentNote.id || 'new'"
        v-model="currentNote.content"
        :layout="effectiveEditorLayout"
        :readOnly="effectiveReadonly"
        :show-toolbar="!readonlyPresentationActive"
        :show-wrap-toggle="!readonlyPresentationActive"
        @change="handleContentChange"
      />
      <div
        v-if="sourceImageMenu.visible"
        class="source-image-menu"
        :style="sourceImageMenuStyle"
        @mousedown.prevent
        @click.stop
      >
        <el-button-group>
          <el-button size="small" @click="setSourceImageWidth('30%')">30%</el-button>
          <el-button size="small" @click="setSourceImageWidth('50%')">50%</el-button>
          <el-button size="small" @click="setSourceImageWidth('100%')">100%</el-button>
          <el-button size="small" @click="openSourceImage">原图</el-button>
          <el-button size="small" @click="copySourceImageUrl">复制链接</el-button>
          <el-button v-if="!effectiveReadonly" size="small" type="danger" @click="deleteSourceImage">删除</el-button>
        </el-button-group>
      </div>
    </div>

    <NodeHelpDialog v-model="showHelpDialog" />
  </div>
</template>

<script setup lang="ts">
import { computed, defineAsyncComponent, onMounted, onUnmounted, ref, watch } from 'vue';
import { ArrowDown, ArrowRight, Calendar, Check, Clock, Close, List, Loading, Plus } from '@element-plus/icons-vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import type { NoteNode } from '@/api/notes';
import NodeHelpDialog from './NodeHelpDialog.vue';
import NodeSelector from './NodeSelector.vue';
import NoteTypeSelector from './NoteTypeSelector.vue';
import SortableOrderHandle from './SortableOrderHandle.vue';
import SmartTimeInput from './SmartTimeInput.vue';
import { formatNoteDateTimeDetailed } from '@/utils/noteDate';
import {
  applyEditableNoteSnapshot,
  areEditableNoteSnapshotsEqual,
  buildEditableNotePatch,
  buildNoteDraftStorageKey,
  cloneEditableNoteSnapshot,
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
import {
  evaluateCompletionProgressExpr,
  getCompletionProgressExprFromCustomFields,
  normalizeCompletionProgressExpr,
  resolveCompletionProgressFillRatio,
  stripNoteSystemCustomFields,
  upsertCompletionProgressExprInCustomFields,
} from '@/utils/noteProgress';
import { NOTE_WEIGHT_DEFAULT, NOTE_WEIGHT_MIN, normalizeNoteWeight } from '@/utils/noteWeight';
import { useAutoSave } from '@/utils/useAutoSave';
import { derivePrimaryNodeType, getNodeStatusConfig, getNodeTypeConfig, normalizeNoteTypeAssignments } from '@/utils/nodeConfig';
import { useSortableList } from '@/utils/useSortableList';
import {
  deriveLegacySemanticsFromTaxonomy,
  deriveNoteTaxonomyFromLegacy,
  NOTE_CATEGORY_DEFAULT,
  NOTE_FORM_DEFAULT,
  NOTE_LIFECYCLE_STAGE_DEFAULT,
  NOTE_SCENE_DEFAULT
} from '@/utils/noteSemantics';

const NoteEditor = defineAsyncComponent(() => import('./NoteEditor.vue'));

const props = defineProps<{
  modelValue?: NoteNode;
  loading?: boolean;
  readonly?: boolean;
  emptyText?: string;
  showPrivateToggle?: boolean;
  lockTitle?: boolean;
  lockNodeType?: boolean;
  lockNoteForm?: boolean;
  readonlyPresentation?: boolean;
  editorLayout?: 'fill' | 'flow';
  onSave?: (note: NoteNode, patch?: EditableNotePatch) => Promise<NoteNode | void>;
  onSaveKeepalive?: (note: NoteNode, patch?: EditableNotePatch) => void;
}>();

const effectiveEditorLayout = computed(() => props.editorLayout || 'fill');

const emit = defineEmits<{
  (e: 'update:modelValue', note: NoteNode): void;
  (e: 'change', note: NoteNode): void;
}>();

interface InheritedFieldItem {
  type: NoteCustomFieldType;
  value: string | number | boolean;
}

interface LocalNoteHistoryEntry {
  snapshot: EditableNoteSnapshot;
  key: string;
  reason: string;
  createdAt: number;
}

const CONTENT_SAVE_DELAY_MS = 1800;
const META_SAVE_DELAY_MS = 450;
const LOCAL_NOTE_UNDO_STACK_LIMIT = 40;
const EMPTY_RICH_TEXT_HTML_VALUES = new Set(['', '<p><br></p>', '<p></p>']);
const CUSTOM_FIELD_KEY_WIDTH_MIN = 96;
const CUSTOM_FIELD_KEY_WIDTH_MAX = 360;
const CUSTOM_FIELD_KEY_WIDTH_PADDING = 32;
const CUSTOM_FIELDS_COLLAPSED_KEY = 'codeyun.note-editor.custom-fields-collapsed.v1';
const CUSTOM_FIELDS_COLLAPSED_EVENT = 'codeyun:note-editor-custom-fields-collapsed';
const smartTimeInputStyle = { width: '100px', marginLeft: '5px' } as const;

const currentNote = ref<NoteNode | undefined>();
const saveStatus = ref<'saved' | 'saving' | 'unsaved'>('saved');
const showHistory = ref(false);
const showHelpDialog = ref(false);
const historyButtonType = computed<'primary' | undefined>(() => showHistory.value ? 'primary' : undefined);
const effectiveReadonly = computed(() => Boolean(props.readonly) || currentNote.value?.can_edit === false);
const titleReadonly = computed(() => effectiveReadonly.value || Boolean(props.lockTitle));
const nodeTypeReadonly = computed(() => effectiveReadonly.value || Boolean(props.lockNodeType));
const formReadonly = computed(() => effectiveReadonly.value || Boolean(props.lockNoteForm));
const resolvedShowPrivateToggle = computed(() => Boolean(props.showPrivateToggle));
const readonlyPresentationActive = computed(() => Boolean(props.readonlyPresentation) && effectiveReadonly.value);
const isReady = computed(() => !!currentNote.value && currentNote.value.content !== undefined);
const currentPrimaryType = computed(() => derivePrimaryNodeType(currentNote.value?.note_categories, currentNote.value?.primary_category ?? NOTE_CATEGORY_DEFAULT));
const showCompletionProgressControl = computed(() => getNodeStatusConfig(currentNote.value?.lifecycle_stage ?? NOTE_LIFECYCLE_STAGE_DEFAULT).id === 'done');
const isOneNoteImportedNote = computed(() => {
  const source = String(getStoredCustomFieldValue(currentNote.value?.custom_fields, 'source') ?? '');
  const sourceKind = String(getStoredCustomFieldValue(currentNote.value?.custom_fields, 'source_kind') ?? '');
  return source.startsWith('onenote') || sourceKind === 'onenote_page';
});
const showSourceHtmlEditor = computed(() => (
  isOneNoteImportedNote.value
  && !readonlyPresentationActive.value
  && Boolean(currentNote.value?.content)
));

const customFieldsList = ref<NoteCustomFieldItem[]>([]);
const ownCustomFieldsListRef = ref<HTMLElement | null>(null);
const sourceHtmlEditorRef = ref<HTMLElement | null>(null);
const sourceImageMenu = ref({
  visible: false,
  left: 0,
  top: 0,
  src: '',
  alt: '',
});
const localUndoStack = ref<LocalNoteHistoryEntry[]>([]);
const localRedoStack = ref<LocalNoteHistoryEntry[]>([]);
const sourceImageMenuStyle = computed(() => ({
  left: `${sourceImageMenu.value.left}px`,
  top: `${sourceImageMenu.value.top}px`,
}));
const customFieldKeyWidthMode = ref<'auto' | 'manual'>('auto');
const customFieldKeyWidth = ref(120);
const customFieldsCollapsed = ref(readCustomFieldsCollapsed());
const inheritedDirectFields = ref<Record<string, InheritedFieldItem>>({});
const inheritedAncestorFields = ref<Record<string, InheritedFieldItem>>({});
const inheritedFieldSource = ref<NoteNode['inherited_fields'] | null>(null);
const timeInputString = ref('');
const currentDraftKey = ref<string | null>(null);
let loadRequestToken = 0;
let customFieldKeyResizePointerId: number | null = null;
let customFieldKeyResizeStartX = 0;
let customFieldKeyResizeStartWidth = customFieldKeyWidth.value;
let customFieldKeyMeasureCanvas: HTMLCanvasElement | null = null;
let selectedSourceImageElement: HTMLImageElement | null = null;
let suppressLocalHistory = false;
let activeLocalEditHistoryEntry: LocalNoteHistoryEntry | null = null;
let activeLocalEditHistoryCommitted = false;

const removeLocalDraftByKey = (draftKey: string | null) => {
  if (!draftKey || typeof window === 'undefined' || typeof window.localStorage === 'undefined') return;
  try {
    window.localStorage.removeItem(draftKey);
  } catch {
    // Ignore storage access errors. The in-memory autosave state is still reset below when active.
  }
};

function readCustomFieldsCollapsed() {
  if (typeof window === 'undefined' || typeof window.localStorage === 'undefined') return false;
  return window.localStorage.getItem(CUSTOM_FIELDS_COLLAPSED_KEY) === '1';
}

const setCustomFieldsCollapsed = (collapsed: boolean, options: { broadcast?: boolean } = {}) => {
  customFieldsCollapsed.value = collapsed;
  if (typeof window === 'undefined' || typeof window.localStorage === 'undefined') return;
  window.localStorage.setItem(CUSTOM_FIELDS_COLLAPSED_KEY, collapsed ? '1' : '0');
  if (options.broadcast !== false) {
    window.dispatchEvent(new CustomEvent(CUSTOM_FIELDS_COLLAPSED_EVENT, { detail: { collapsed } }));
  }
};

const toggleCustomFieldsCollapsed = () => {
  setCustomFieldsCollapsed(!customFieldsCollapsed.value);
};

const handleCustomFieldsCollapsedStorage = (event: StorageEvent) => {
  if (event.key !== CUSTOM_FIELDS_COLLAPSED_KEY || event.newValue == null) return;
  customFieldsCollapsed.value = event.newValue === '1';
};

const handleCustomFieldsCollapsedEvent = (event: Event) => {
  const collapsed = (event as CustomEvent<{ collapsed?: unknown }>).detail?.collapsed;
  if (typeof collapsed === 'boolean') {
    customFieldsCollapsed.value = collapsed;
  }
};

const sortedHistory = computed(() => currentNote.value?.history ? [...currentNote.value.history].sort((a, b) => b.ts - a.ts) : []);
const completionProgressExpr = computed(() => normalizeCompletionProgressExpr(currentNote.value?.completion_progress_expr));
const currentCompletionProgress = computed(() => resolveCompletionProgressFillRatio({
  lifecycleStage: currentNote.value?.lifecycle_stage,
  completionProgress: currentNote.value?.completion_progress,
  completionProgressExpr: currentNote.value?.completion_progress_expr,
  customFields: currentNote.value?.custom_fields,
}));
const customFieldKeyTexts = computed(() => {
  const texts = [
    ...customFieldsList.value.map(item => item.key),
    ...Object.keys(inheritedDirectFields.value),
    ...Object.keys(inheritedAncestorFields.value),
  ]
    .map(item => String(item ?? '').trim())
    .filter(Boolean);
  return texts.length ? texts : ['Key'];
});
const autoCustomFieldKeyWidth = computed(() => {
  const longestWidth = customFieldKeyTexts.value.reduce((maxWidth, text) => (
    Math.max(maxWidth, measureCustomFieldKeyTextWidth(text))
  ), 0);
  return clampCustomFieldKeyWidth(longestWidth + CUSTOM_FIELD_KEY_WIDTH_PADDING);
});
const resolvedCustomFieldKeyWidth = computed(() => (
  customFieldKeyWidthMode.value === 'manual' ? customFieldKeyWidth.value : autoCustomFieldKeyWidth.value
));
const customFieldColumnStyle = computed(() => ({
  '--custom-field-key-width': `${resolvedCustomFieldKeyWidth.value}px`
}));
const inheritedCustomFieldCount = computed(() => (
  Object.keys(inheritedDirectFields.value).length + Object.keys(inheritedAncestorFields.value).length
));
const customFieldsTotalCount = computed(() => customFieldsList.value.length + inheritedCustomFieldCount.value);
const customFieldsSummary = computed(() => {
  if (customFieldsTotalCount.value === 0) return '暂无';
  if (inheritedCustomFieldCount.value > 0) {
    return `${customFieldsTotalCount.value} 项，含继承 ${inheritedCustomFieldCount.value}`;
  }
  return `${customFieldsTotalCount.value} 项`;
});

const clampCustomFieldKeyWidth = (value: number) => Math.max(
  CUSTOM_FIELD_KEY_WIDTH_MIN,
  Math.min(CUSTOM_FIELD_KEY_WIDTH_MAX, Math.round(value))
);

const getCustomFieldKeyMeasureFont = () => {
  if (typeof window === 'undefined') return '14px sans-serif';
  const bodyStyle = window.getComputedStyle(document.body);
  const fontSize = bodyStyle.fontSize || '14px';
  const fontFamily = bodyStyle.fontFamily || 'sans-serif';
  return `${fontSize} ${fontFamily}`;
};

const measureCustomFieldKeyTextWidth = (text: string) => {
  if (typeof document === 'undefined') return text.length * 9;
  customFieldKeyMeasureCanvas ??= document.createElement('canvas');
  const context = customFieldKeyMeasureCanvas.getContext('2d');
  if (!context) return text.length * 9;
  context.font = getCustomFieldKeyMeasureFont();
  return context.measureText(text).width;
};

const getStoredCustomFieldValue = (fields: unknown, key: string): unknown => {
  if (!Array.isArray(fields)) return undefined;
  for (const item of fields) {
    if (Array.isArray(item) && item[0] === key) return item[2];
    if (item && typeof item === 'object') {
      const record = item as Record<string, unknown>;
      if ((record.key === key || record.name === key) && 'value' in record) return record.value;
    }
  }
  return undefined;
};

const sanitizeSourcePreviewHtml = (html: string) => {
  const source = String(html || '');
  if (!source || typeof DOMParser === 'undefined') return source;

  try {
    const doc = new DOMParser().parseFromString(source, 'text/html');
    doc.body.querySelectorAll('script, iframe, object, embed, link, meta, title').forEach(element => element.remove());
    doc.body.querySelectorAll<HTMLElement>('*').forEach(element => {
      Array.from(element.attributes).forEach(attribute => {
        const name = attribute.name.toLowerCase();
        const value = attribute.value.trim().toLowerCase();
        if (name.startsWith('on')) {
          element.removeAttribute(attribute.name);
          return;
        }
        if ((name === 'href' || name === 'src') && value.startsWith('javascript:')) {
          element.removeAttribute(attribute.name);
        }
      });
    });
    return doc.body.innerHTML;
  } catch {
    return source;
  }
};

const syncSourceHtmlEditor = () => {
  const element = sourceHtmlEditorRef.value;
  if (!element || !showSourceHtmlEditor.value) return;
  if (typeof document !== 'undefined' && document.activeElement === element) return;
  const nextHtml = sanitizeSourcePreviewHtml(currentNote.value?.content || '');
  if (element.innerHTML !== nextHtml) element.innerHTML = nextHtml;
};

const hideSourceImageMenu = () => {
  sourceImageMenu.value.visible = false;
  sourceImageMenu.value.src = '';
  sourceImageMenu.value.alt = '';
  selectedSourceImageElement = null;
};

const getSourceImageAbsoluteUrl = () => {
  const src = sourceImageMenu.value.src;
  if (!src || typeof window === 'undefined') return src;
  try {
    return new URL(src, window.location.href).href;
  } catch {
    return src;
  }
};

const persistSourceHtmlEditorContent = (options: { immediate?: boolean } = {}) => {
  if (!currentNote.value || effectiveReadonly.value) return;
  const element = sourceHtmlEditorRef.value;
  if (!element) return;
  const sanitizedHtml = sanitizeSourcePreviewHtml(element.innerHTML);
  if (element.innerHTML !== sanitizedHtml) element.innerHTML = sanitizedHtml;
  if (currentNote.value.content !== sanitizedHtml) currentNote.value.content = sanitizedHtml;
  queueAutoSave({ immediate: options.immediate, delayMs: options.immediate ? 0 : CONTENT_SAVE_DELAY_MS });
};

const showSourceImageMenu = (image: HTMLImageElement, event: MouseEvent) => {
  selectedSourceImageElement = image;
  const menuWidth = 390;
  const menuHeight = 38;
  const margin = 8;
  const viewportWidth = typeof window === 'undefined' ? menuWidth : window.innerWidth;
  const viewportHeight = typeof window === 'undefined' ? menuHeight : window.innerHeight;
  sourceImageMenu.value = {
    visible: true,
    left: Math.max(margin, Math.min(event.clientX + margin, viewportWidth - menuWidth - margin)),
    top: Math.max(margin, Math.min(event.clientY + margin, viewportHeight - menuHeight - margin)),
    src: image.getAttribute('src') || image.currentSrc || '',
    alt: image.getAttribute('alt') || '',
  };
};

const setSourceImageWidth = (width: string) => {
  if (!selectedSourceImageElement || effectiveReadonly.value) return;
  pushLocalUndoSnapshot('source-image');
  selectedSourceImageElement.style.width = width;
  selectedSourceImageElement.style.maxWidth = '100%';
  selectedSourceImageElement.style.height = 'auto';
  persistSourceHtmlEditorContent({ immediate: true });
};

const openSourceImage = () => {
  const url = getSourceImageAbsoluteUrl();
  if (!url || typeof window === 'undefined') return;
  const opened = window.open(url, '_blank');
  if (opened) opened.opener = null;
  hideSourceImageMenu();
};

const copySourceImageUrl = async () => {
  const url = getSourceImageAbsoluteUrl();
  if (!url) return;
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(url);
    } else {
      const textarea = document.createElement('textarea');
      textarea.value = url;
      textarea.style.position = 'fixed';
      textarea.style.opacity = '0';
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand('copy');
      textarea.remove();
    }
    ElMessage.success('已复制图片链接');
  } catch {
    ElMessage.error('复制失败');
  }
};

const deleteSourceImage = () => {
  if (!selectedSourceImageElement || effectiveReadonly.value) return;
  pushLocalUndoSnapshot('source-image');
  selectedSourceImageElement.remove();
  hideSourceImageMenu();
  persistSourceHtmlEditorContent({ immediate: true });
};

const handleSourceHtmlInput = () => {
  if (!currentNote.value || effectiveReadonly.value) return;
  const element = sourceHtmlEditorRef.value;
  if (!element) return;
  hideSourceImageMenu();
  currentNote.value.content = element.innerHTML;
  recordLocalEditHistory('content');
  queueAutoSave({ delayMs: CONTENT_SAVE_DELAY_MS });
};

const handleSourceHtmlClick = (event: MouseEvent) => {
  const targetNode = event.target instanceof Node ? event.target : null;
  const target = targetNode instanceof Element ? targetNode : targetNode?.parentElement ?? null;
  const root = sourceHtmlEditorRef.value;

  const image = target?.closest('img[src]') as HTMLImageElement | null;
  if (image && root?.contains(image)) {
    event.preventDefault();
    event.stopPropagation();
    showSourceImageMenu(image, event);
    return;
  }

  hideSourceImageMenu();

  const link = target?.closest('a[href]') as HTMLAnchorElement | null;
  if (!link || !root?.contains(link)) return;

  const rawHref = link.getAttribute('href')?.trim() || '';
  if (!rawHref || rawHref.toLowerCase().startsWith('javascript:')) return;

  event.preventDefault();
  event.stopPropagation();
  const opened = window.open(link.href, '_blank');
  if (opened) {
    opened.opener = null;
  } else {
    window.location.assign(link.href);
  }
};

const handleSourceHtmlBlur = () => {
  if (!currentNote.value || effectiveReadonly.value) return;
  const element = sourceHtmlEditorRef.value;
  if (!element) return;
  hideSourceImageMenu();
  const sanitizedHtml = sanitizeSourcePreviewHtml(element.innerHTML);
  if (element.innerHTML !== sanitizedHtml) element.innerHTML = sanitizedHtml;
  if (currentNote.value.content !== sanitizedHtml) {
    currentNote.value.content = sanitizedHtml;
    queueAutoSave({ delayMs: CONTENT_SAVE_DELAY_MS });
  }
};

const handleSourceHtmlEditBlur = () => {
  handleSourceHtmlBlur();
  endLocalEditHistory();
};

const startDateProxy = computed<Date | undefined>({
  get: () => currentNote.value ? new Date(currentNote.value.start_at) : undefined,
  set: value => {
    if (!currentNote.value || !value || effectiveReadonly.value) return;
    const original = new Date(currentNote.value.start_at);
    const previousTime = original.getTime();
    original.setFullYear(value.getFullYear());
    original.setMonth(value.getMonth());
    original.setDate(value.getDate());
    if (original.getTime() === previousTime) return;
    pushLocalUndoSnapshot('start-date');
    currentNote.value.start_at = original.getTime();
    queueMetaAutoSave();
  }
});

const buildStoredCustomFields = () => upsertCompletionProgressExprInCustomFields(
  noteCustomFieldItemsToList(customFieldsList.value),
  currentNote.value?.completion_progress_expr ?? null
);

const syncCompletionProgressState = (
  customFields: unknown,
  source?: Partial<NoteNode> | null
) => {
  if (!currentNote.value) return;
  const expr = normalizeCompletionProgressExpr(
    source?.completion_progress_expr ?? getCompletionProgressExprFromCustomFields(customFields)
  );
  currentNote.value.completion_progress_expr = expr || null;
  currentNote.value.completion_progress = evaluateCompletionProgressExpr(expr);
};

const buildCurrentSnapshot = (): EditableNoteSnapshot | null => createEditableNoteSnapshot(currentNote.value, buildStoredCustomFields());

const getLocalHistorySnapshotKey = (snapshot: EditableNoteSnapshot) => JSON.stringify(snapshot);

const trimLocalHistoryStack = (stack: LocalNoteHistoryEntry[]) => {
  while (stack.length > LOCAL_NOTE_UNDO_STACK_LIMIT) {
    stack.shift();
  }
};

const createLocalHistoryEntry = (reason: string): LocalNoteHistoryEntry | null => {
  if (suppressLocalHistory || effectiveReadonly.value) return null;
  const snapshot = buildCurrentSnapshot();
  if (!snapshot) return null;
  const clonedSnapshot = cloneEditableNoteSnapshot(snapshot);
  return {
    snapshot: clonedSnapshot,
    key: getLocalHistorySnapshotKey(clonedSnapshot),
    reason,
    createdAt: Date.now()
  };
};

const pushLocalUndoEntry = (
  entry: LocalNoteHistoryEntry | null | undefined,
  options: { skipCurrentCompare?: boolean } = {}
) => {
  if (!entry || suppressLocalHistory) return;
  const currentSnapshot = buildCurrentSnapshot();
  if (!currentSnapshot) return;
  if (!options.skipCurrentCompare && getLocalHistorySnapshotKey(currentSnapshot) === entry.key) return;
  const lastEntry = localUndoStack.value[localUndoStack.value.length - 1];
  if (lastEntry?.key === entry.key) return;
  localUndoStack.value.push(entry);
  trimLocalHistoryStack(localUndoStack.value);
  localRedoStack.value = [];
};

const pushLocalUndoSnapshot = (reason: string) => {
  pushLocalUndoEntry(createLocalHistoryEntry(reason), { skipCurrentCompare: true });
};

const resetLocalUndoHistory = () => {
  localUndoStack.value = [];
  localRedoStack.value = [];
  activeLocalEditHistoryEntry = null;
  activeLocalEditHistoryCommitted = false;
};

const beginLocalEditHistory = (reason: string) => {
  if (effectiveReadonly.value) return;
  activeLocalEditHistoryEntry = createLocalHistoryEntry(reason);
  activeLocalEditHistoryCommitted = false;
};

const recordLocalEditHistory = (reason: string) => {
  if (activeLocalEditHistoryCommitted) return;
  if (!activeLocalEditHistoryEntry) {
    activeLocalEditHistoryEntry = createLocalHistoryEntry(reason);
  }
  pushLocalUndoEntry(activeLocalEditHistoryEntry);
  activeLocalEditHistoryCommitted = true;
};

const endLocalEditHistory = () => {
  activeLocalEditHistoryEntry = null;
  activeLocalEditHistoryCommitted = false;
};

const restoreLocalHistorySnapshot = (snapshot: EditableNoteSnapshot) => {
  if (!currentNote.value) return;
  suppressLocalHistory = true;
  try {
    syncCurrentNoteFromSnapshot(cloneEditableNoteSnapshot(snapshot), currentNote.value);
    if (currentNote.value) {
      emit('change', currentNote.value);
    }
  } finally {
    suppressLocalHistory = false;
  }
  queueAutoSave({ immediate: true, delayMs: 0 });
  window.setTimeout(syncSourceHtmlEditor, 0);
};

const undoLocalNoteChange = () => {
  const entry = localUndoStack.value.pop();
  if (!entry) return false;
  const currentEntry = createLocalHistoryEntry('redo');
  if (currentEntry) {
    localRedoStack.value.push(currentEntry);
    trimLocalHistoryStack(localRedoStack.value);
  }
  restoreLocalHistorySnapshot(entry.snapshot);
  return true;
};

const redoLocalNoteChange = () => {
  const entry = localRedoStack.value.pop();
  if (!entry) return false;
  const currentEntry = createLocalHistoryEntry('undo');
  if (currentEntry) {
    localUndoStack.value.push(currentEntry);
    trimLocalHistoryStack(localUndoStack.value);
  }
  restoreLocalHistorySnapshot(entry.snapshot);
  return true;
};

const isUndoShortcut = (event: KeyboardEvent) => (
  (event.ctrlKey || event.metaKey)
  && !event.altKey
  && event.key.toLowerCase() === 'z'
  && !event.shiftKey
);

const isRedoShortcut = (event: KeyboardEvent) => (
  (event.ctrlKey || event.metaKey)
  && !event.altKey
  && (
    event.key.toLowerCase() === 'y'
    || (event.key.toLowerCase() === 'z' && event.shiftKey)
  )
);

const shouldLetFocusedEditorHandleUndoRedo = (target: EventTarget | null) => {
  if (!(target instanceof HTMLElement)) return false;
  if (target.closest('[data-slate-editor]')) return true;
  if (target.closest('.field-richtext-editor')) return true;
  if (target.closest('.title-input, .field-key, .field-value, .progress-expr-input, .source-html-preview')) {
    return true;
  }
  const tagName = target.tagName.toLowerCase();
  if (tagName === 'textarea' || target.isContentEditable) return true;
  return false;
};

const handleLocalUndoRedoKeydown = (event: KeyboardEvent) => {
  if (effectiveReadonly.value || event.defaultPrevented) return;
  if (!isUndoShortcut(event) && !isRedoShortcut(event)) return;
  if (shouldLetFocusedEditorHandleUndoRedo(event.target)) return;

  const handled = isUndoShortcut(event)
    ? undoLocalNoteChange()
    : redoLocalNoteChange();
  if (!handled) return;
  event.preventDefault();
  event.stopPropagation();
};

const normalizeIncomingNote = (note: NoteNode) => {
  const cloned = JSON.parse(JSON.stringify(note)) as NoteNode;
  if (cloned.start_at && cloned.start_at < 10000000000) cloned.start_at *= 1000;
  if (cloned.updated_at && cloned.updated_at < 10000000000) cloned.updated_at *= 1000;
  if (cloned.created_at && cloned.created_at < 10000000000) cloned.created_at *= 1000;
  const taxonomy = Array.isArray(cloned.note_categories) || cloned.primary_category || cloned.note_form || cloned.note_scene || cloned.lifecycle_stage
    ? deriveLegacySemanticsFromTaxonomy(
      cloned.note_categories,
      cloned.primary_category ?? NOTE_CATEGORY_DEFAULT,
      cloned.note_form ?? NOTE_FORM_DEFAULT,
      cloned.note_scene ?? cloned.note_kind ?? NOTE_SCENE_DEFAULT,
      cloned.lifecycle_stage ?? cloned.node_status ?? NOTE_LIFECYCLE_STAGE_DEFAULT
    )
    : deriveNoteTaxonomyFromLegacy(
      cloned.note_types,
      cloned.node_type ?? 'note',
      cloned.note_kind ?? NOTE_SCENE_DEFAULT,
      cloned.node_status ?? NOTE_LIFECYCLE_STAGE_DEFAULT
    );
  cloned.note_categories = taxonomy.note_categories;
  cloned.primary_category = taxonomy.primary_category;
  cloned.note_form = taxonomy.note_form;
  cloned.note_scene = taxonomy.note_scene;
  cloned.lifecycle_stage = taxonomy.lifecycle_stage;
  cloned.note_types = taxonomy.note_types ?? cloned.note_types;
  cloned.node_type = taxonomy.node_type ?? cloned.node_type;
  cloned.note_kind = taxonomy.note_kind ?? cloned.note_kind;
  cloned.node_status = taxonomy.node_status ?? cloned.node_status;
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
  syncCompletionProgressState(snapshot.custom_fields, source);
  customFieldsList.value = noteCustomFieldsToItems(stripNoteSystemCustomFields(snapshot.custom_fields));
  refreshInheritedFields(source ?? currentNote.value);
  if (currentNote.value) emit('update:modelValue', currentNote.value);
};

const stopCustomFieldKeyResize = () => {
  if (customFieldKeyResizePointerId === null) return;
  customFieldKeyResizePointerId = null;
  window.removeEventListener('pointermove', handleCustomFieldKeyResizeMove);
  window.removeEventListener('pointerup', stopCustomFieldKeyResize);
  window.removeEventListener('pointercancel', stopCustomFieldKeyResize);
  document.body.style.cursor = '';
  document.body.style.userSelect = '';
};

function handleCustomFieldKeyResizeMove(event: PointerEvent) {
  if (customFieldKeyResizePointerId === null) return;
  const deltaX = event.clientX - customFieldKeyResizeStartX;
  const nextWidth = clampCustomFieldKeyWidth(customFieldKeyResizeStartWidth + deltaX);
  customFieldKeyWidth.value = nextWidth;
}

const startCustomFieldKeyResize = (event: PointerEvent) => {
  customFieldKeyWidthMode.value = 'manual';
  customFieldKeyResizePointerId = event.pointerId;
  customFieldKeyResizeStartX = event.clientX;
  customFieldKeyResizeStartWidth = resolvedCustomFieldKeyWidth.value;
  customFieldKeyWidth.value = resolvedCustomFieldKeyWidth.value;
  document.body.style.cursor = 'col-resize';
  document.body.style.userSelect = 'none';
  window.addEventListener('pointermove', handleCustomFieldKeyResizeMove);
  window.addEventListener('pointerup', stopCustomFieldKeyResize);
  window.addEventListener('pointercancel', stopCustomFieldKeyResize);
  event.preventDefault();
};

const resetCustomFieldKeyWidthAuto = () => {
  customFieldKeyWidthMode.value = 'auto';
  customFieldKeyWidth.value = autoCustomFieldKeyWidth.value;
};

useSortableList({
  listRef: ownCustomFieldsListRef,
  getDeps: () => [currentNote.value?.id ?? '', effectiveReadonly.value, customFieldsList.value.length] as const,
  isEnabled: () => Boolean(currentNote.value?.id) && !effectiveReadonly.value && customFieldsList.value.length > 1,
  ghostClass: 'custom-field-sortable-ghost',
  onReorder: (oldIndex, newIndex) => {
    const reordered = [...customFieldsList.value];
    const [movedItem] = reordered.splice(oldIndex, 1);
    if (!movedItem) return;
    pushLocalUndoSnapshot('custom-field-reorder');
    reordered.splice(Math.min(newIndex, reordered.length), 0, movedItem);
    customFieldsList.value = reordered;
    syncCustomFields();
  }
});

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
    resetLocalUndoHistory();
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
    const draftKeyForPrompt = currentDraftKey.value;
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
      removeLocalDraftByKey(draftKeyForPrompt);
      if (requestToken !== loadRequestToken || props.modelValue?.id !== note.id) return;
      autoSave.clearDraft();
    }
  }

  syncCurrentNoteFromSnapshot(activeSnapshot, note);
  resetLocalUndoHistory();
  saveStatus.value = autoSave.saveStatus.value;
  showHistory.value = false;
}, { immediate: true });

watch(
  () => [showSourceHtmlEditor.value, currentNote.value?.id, currentNote.value?.content] as const,
  () => {
    window.setTimeout(syncSourceHtmlEditor, 0);
  },
  { immediate: true }
);

watch(() => currentNote.value?.start_at, value => {
  if (!value) {
    timeInputString.value = '';
    return;
  }
  const d = new Date(value);
  timeInputString.value = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`;
}, { immediate: true });

onMounted(() => {
  customFieldsCollapsed.value = readCustomFieldsCollapsed();
  window.addEventListener('storage', handleCustomFieldsCollapsedStorage);
  window.addEventListener(CUSTOM_FIELDS_COLLAPSED_EVENT, handleCustomFieldsCollapsedEvent);
  window.addEventListener('click', hideSourceImageMenu);
});

onUnmounted(() => {
  stopCustomFieldKeyResize();
  window.removeEventListener('storage', handleCustomFieldsCollapsedStorage);
  window.removeEventListener(CUSTOM_FIELDS_COLLAPSED_EVENT, handleCustomFieldsCollapsedEvent);
  window.removeEventListener('click', hideSourceImageMenu);
});

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

const handleTitleInput = () => {
  recordLocalEditHistory('title');
  queueMetaAutoSave();
};

const handleContentChange = (html: string) => {
  if (!currentNote.value || effectiveReadonly.value) return;
  currentNote.value.content = html;
  queueAutoSave({ delayMs: CONTENT_SAVE_DELAY_MS });
};

const syncCustomFields = (options: { immediate?: boolean } = {}) => {
  if (!currentNote.value) return;
  currentNote.value.custom_fields = buildStoredCustomFields();
  syncCompletionProgressState(currentNote.value.custom_fields, currentNote.value);
  refreshInheritedFields(currentNote.value);
  queueMetaAutoSave(options);
};

const onWeightChange = (value: number | undefined) => {
  if (!currentNote.value || effectiveReadonly.value) return;
  if (value == null || Number.isNaN(value)) {
    const baseline = autoSave.getBaselineSnapshot();
    const nextWeight = baseline ? normalizeNoteWeight(baseline.weight) : NOTE_WEIGHT_DEFAULT;
    if (currentNote.value.weight !== nextWeight) {
      pushLocalUndoSnapshot('weight');
      currentNote.value.weight = nextWeight;
    }
    return;
  }
  const nextWeight = normalizeNoteWeight(value);
  if (currentNote.value.weight === nextWeight) return;
  pushLocalUndoSnapshot('weight');
  currentNote.value.weight = nextWeight;
  queueMetaAutoSave();
};

const onWeightBlur = () => { if (currentNote.value) onWeightChange(currentNote.value.weight); };

const normalizePrivateLevel = (value: number) => Math.max(0, Math.trunc(value));

const onPrivateLevelChange = (value: number | undefined) => {
  if (!currentNote.value || effectiveReadonly.value) return;
  if (value == null || Number.isNaN(value)) {
    const baseline = autoSave.getBaselineSnapshot();
    const nextPrivateLevel = baseline ? normalizePrivateLevel(baseline.private_level) : 0;
    if (currentNote.value.private_level !== nextPrivateLevel) {
      pushLocalUndoSnapshot('private-level');
      currentNote.value.private_level = nextPrivateLevel;
    }
    return;
  }
  const nextPrivateLevel = normalizePrivateLevel(value);
  if (currentNote.value.private_level === nextPrivateLevel) return;
  pushLocalUndoSnapshot('private-level');
  currentNote.value.private_level = nextPrivateLevel;
  queueMetaAutoSave();
};

const onPrivateLevelBlur = () => { if (currentNote.value) onPrivateLevelChange(currentNote.value.private_level); };

const handleTimeChange = (value: string) => {
  if (!value || !currentNote.value || effectiveReadonly.value) return;
  const d = new Date(currentNote.value.start_at);
  const [h, m, s] = value.split(':').map(Number);
  if (d.getHours() === h && d.getMinutes() === m && d.getSeconds() === s) return;
  d.setHours(h);
  d.setMinutes(m);
  d.setSeconds(s);
  pushLocalUndoSnapshot('start-time');
  currentNote.value.start_at = d.getTime();
  queueMetaAutoSave();
};

const syncLegacyFieldsFromTaxonomy = () => {
  if (!currentNote.value) return;
  const legacy = deriveLegacySemanticsFromTaxonomy(
    currentNote.value.note_categories,
    currentNote.value.primary_category ?? NOTE_CATEGORY_DEFAULT,
    currentNote.value.note_form ?? NOTE_FORM_DEFAULT,
    currentNote.value.note_scene ?? currentNote.value.note_kind ?? NOTE_SCENE_DEFAULT,
    currentNote.value.lifecycle_stage ?? NOTE_LIFECYCLE_STAGE_DEFAULT
  );
  currentNote.value.note_categories = legacy.note_categories;
  currentNote.value.primary_category = legacy.primary_category;
  currentNote.value.note_form = legacy.note_form;
  currentNote.value.note_scene = legacy.note_scene;
  currentNote.value.lifecycle_stage = legacy.lifecycle_stage;
  currentNote.value.note_types = legacy.note_types;
  currentNote.value.node_type = legacy.node_type;
  currentNote.value.note_kind = legacy.note_kind;
  currentNote.value.node_status = legacy.node_status;
};

const handleNoteCategoriesChange = (value: unknown) => {
  if (!currentNote.value || nodeTypeReadonly.value) return;
  const nextNoteCategories = normalizeNoteTypeAssignments(value, currentNote.value.primary_category ?? NOTE_CATEGORY_DEFAULT);
  if (JSON.stringify(nextNoteCategories) === JSON.stringify(currentNote.value.note_categories ?? [])) return;
  pushLocalUndoSnapshot('note-categories');
  currentNote.value.note_categories = nextNoteCategories;
  currentNote.value.primary_category = derivePrimaryNodeType(nextNoteCategories, currentNote.value.primary_category ?? NOTE_CATEGORY_DEFAULT);
  syncLegacyFieldsFromTaxonomy();
  queueMetaAutoSave({ immediate: true });
};

const handleNoteFormChange = (value: string) => {
  if (!currentNote.value || effectiveReadonly.value) return;
  const nextNoteForm = value || NOTE_FORM_DEFAULT;
  if (currentNote.value.note_form === nextNoteForm) return;
  pushLocalUndoSnapshot('note-form');
  currentNote.value.note_form = nextNoteForm;
  syncLegacyFieldsFromTaxonomy();
  queueMetaAutoSave({ immediate: true });
};

const handleLifecycleStageChange = (value: string) => {
  if (!currentNote.value || effectiveReadonly.value) return;
  const nextLifecycleStage = value || NOTE_LIFECYCLE_STAGE_DEFAULT;
  if (currentNote.value.lifecycle_stage === nextLifecycleStage) return;
  pushLocalUndoSnapshot('lifecycle-stage');
  currentNote.value.lifecycle_stage = nextLifecycleStage;
  syncLegacyFieldsFromTaxonomy();
  queueMetaAutoSave({ immediate: true });
};

const handleCompletionProgressExprChange = (value: string | number) => {
  if (!currentNote.value || effectiveReadonly.value) return;
  const expr = normalizeCompletionProgressExpr(value);
  if ((currentNote.value.completion_progress_expr ?? null) === (expr || null)) return;
  currentNote.value.completion_progress_expr = expr || null;
  currentNote.value.completion_progress = evaluateCompletionProgressExpr(expr);
  recordLocalEditHistory('completion-progress');
  syncCustomFields();
};

const handleCompletionProgressExprBlur = () => {
  if (!currentNote.value || effectiveReadonly.value) return;
  const normalizedExpr = normalizeCompletionProgressExpr(currentNote.value.completion_progress_expr);
  if ((currentNote.value.completion_progress_expr ?? null) !== (normalizedExpr || null)) {
    currentNote.value.completion_progress_expr = normalizedExpr || null;
    currentNote.value.completion_progress = evaluateCompletionProgressExpr(normalizedExpr);
    recordLocalEditHistory('completion-progress');
    syncCustomFields({ immediate: true });
    return;
  }
  currentNote.value.completion_progress_expr = normalizedExpr || null;
  currentNote.value.completion_progress = evaluateCompletionProgressExpr(normalizedExpr);
  syncCustomFields({ immediate: true });
};

const handleCompletionProgressExprEditBlur = () => {
  handleCompletionProgressExprBlur();
  endLocalEditHistory();
};

const addCustomField = () => {
  if (effectiveReadonly.value) return;
  pushLocalUndoSnapshot('custom-field');
  setCustomFieldsCollapsed(false);
  customFieldsList.value.push(createNoteCustomFieldItem());
};

const addInheritedField = (key: string, value: string | number | boolean, typeFromInheritance?: string) => {
  if (effectiveReadonly.value) return;
  let type: NoteCustomFieldType = 'string';
  if (typeFromInheritance && ['string', 'richtext', 'number', 'boolean'].includes(typeFromInheritance)) type = typeFromInheritance as NoteCustomFieldType;
  else if (typeof value === 'boolean') type = 'boolean';
  else if (typeof value === 'number') type = 'number';
  pushLocalUndoSnapshot('custom-field');
  customFieldsList.value.push(createNoteCustomFieldItem(key, type, value));
  syncCustomFields();
};

const removeCustomField = (index: number) => {
  if (effectiveReadonly.value) return;
  pushLocalUndoSnapshot('custom-field');
  customFieldsList.value.splice(index, 1);
  syncCustomFields();
};

const handleCustomFieldChange = () => {
  if (effectiveReadonly.value) return;
  recordLocalEditHistory('custom-field');
  syncCustomFields();
};

const handleCustomFieldTypeChange = (item: NoteCustomFieldItem) => {
  if (effectiveReadonly.value) return;
  pushLocalUndoSnapshot('custom-field');
  item.value = convertNoteCustomFieldValue(item.type, item.value);
  syncCustomFields({ immediate: true });
};

const getTextFieldValue = (item: NoteCustomFieldItem) => item.type === 'boolean' ? '' : String(item.value);
const getBooleanFieldValue = (item: NoteCustomFieldItem) => item.type === 'boolean' ? Boolean(item.value) : false;

const setTextFieldValue = (item: NoteCustomFieldItem, value: string | number) => {
  if (effectiveReadonly.value || item.type === 'boolean') return;
  const nextValue = String(value ?? '');
  if (item.value === nextValue) return;
  if (!activeLocalEditHistoryEntry) pushLocalUndoSnapshot('custom-field');
  item.value = nextValue;
  handleCustomFieldChange();
};

const setNumberFieldValue = (item: NoteCustomFieldItem, value: string | number) => {
  if (effectiveReadonly.value || item.type !== 'number') return;
  const nextValue = String(value ?? '');
  if (item.value === nextValue) return;
  if (!activeLocalEditHistoryEntry) pushLocalUndoSnapshot('custom-field');
  item.value = nextValue;
  handleCustomFieldChange();
};

const setBooleanFieldValue = (item: NoteCustomFieldItem, value: string | number | boolean) => {
  if (effectiveReadonly.value || item.type !== 'boolean') return;
  const nextValue = Boolean(value);
  if (item.value === nextValue) return;
  pushLocalUndoSnapshot('custom-field');
  item.value = nextValue;
  handleCustomFieldChange();
};

const formatDateDetailed = (timestamp: number) => formatNoteDateTimeDetailed(timestamp);
const getFieldName = (field: string) => ({ n: '主分类', nt: '分类组', s: '阶段', t: '标题', w: '权重', cl: '颜色', p: '私密', c: '内容' }[field] || field);
const getFieldTagType = (field: string): 'primary' | 'success' | 'info' | 'warning' | 'danger' | undefined => (({ n: 'primary', nt: 'primary', s: 'warning', t: undefined, w: 'success', cl: 'success', p: 'danger', c: 'info' } as const)[field]);

const formatHistoryValue = (field: string, value: any) => {
  if (field === 'n') return getNodeTypeConfig(value).label;
  if (field === 'nt') return normalizeNoteTypeAssignments(value).map(item => `${getNodeTypeConfig(item.key).label}${item.weight >= 100 ? '' : `(${item.weight})`}`).join('，');
  if (field === 's') return getNodeStatusConfig(value).label;
  if (field === 'cl') return value ? String(value).toUpperCase() : '跟随类型';
  if (field === 'p') return `值 ${normalizePrivateLevel(Number(value) || 0)}`;
  if (field === 'c') return `${value} 字`;
  return value;
};

const getInheritedRichTextHtml = (value: unknown) => {
  const html = String(value ?? '').trim();
  if (EMPTY_RICH_TEXT_HTML_VALUES.has(html)) return '<p class="field-richtext-empty">空</p>';
  return html;
};

const formatInheritedValue = (value: any) => typeof value === 'boolean' ? (value ? 'True' : 'False') : String(value);

const getFieldTypeLabel = (type: unknown, value?: any) => {
  if (type === 'string' || type === 'richtext' || type === 'number' || type === 'boolean') {
    const normalizedType = normalizeNoteCustomFieldType(type);
    return normalizedType === 'boolean'
      ? '布尔'
      : normalizedType === 'number'
        ? '数值'
        : normalizedType === 'richtext'
          ? '富文本'
          : '文本';
  }
  if (typeof type === 'boolean') return '布尔';
  if (typeof type === 'number') return '数值';
  const rawValue = value === undefined ? type : value;
  const text = String(rawValue ?? '');
  return !Number.isNaN(Number(text)) && text.trim() !== '' ? '数值' : '文本';
};
</script>

<style scoped>
.shared-note-editor{display:flex;flex-direction:column;flex:1;min-height:0}
.shared-note-editor.is-fill{overflow-y:auto;overflow-x:hidden}
.shared-note-editor.is-flow{overflow:visible}
.panel-content{display:flex;flex-direction:column;flex:1;min-height:0}
.panel-content.is-fill{flex:1 0 auto;min-height:100%;overflow:visible}
.panel-content.is-flow{flex:none;overflow:visible}
.editor-header{display:flex;flex-direction:column;gap:12px;margin-bottom:20px;padding-bottom:15px;border-bottom:1px solid #f0f0f0}
.header-row{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.primary-row{gap:10px}.secondary-row{justify-content:space-between;font-size:12px;color:#909399}
.title-input{flex:1;font-size:18px}.readonly-title{flex:1;min-width:0;margin:0;color:#1f2933;font-size:22px;font-weight:700;line-height:1.4;word-break:break-word}
.meta-group,.meta-actions-slot,.inline-control,.save-status,.time-tag{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.weight-control,.private-control{flex-wrap:nowrap}
.weight-value-input{width:68px}.private-level-control{width:58px}.progress-control{flex-wrap:nowrap}.progress-expr-input{width:136px}.label,.custom-fields-label .label{font-size:12px;color:#606266;white-space:nowrap}
.history-toggle{margin-left:auto}.start-date-picker{width:130px;margin-left:5px}
.custom-fields-row{display:flex;flex-direction:column;gap:4px;margin-top:5px}.custom-fields-row.is-collapsed{gap:0}.custom-fields-label{display:flex;align-items:center;justify-content:space-between;gap:10px;min-height:24px}
.custom-fields-main{display:flex;align-items:center;gap:5px;min-width:0}
.custom-fields-toggle{display:inline-flex;align-items:center;gap:4px;padding:0;border:0;background:transparent;color:#606266;cursor:pointer}
.custom-fields-toggle:hover .label,.custom-fields-toggle:focus-visible .label{color:#409eff}.custom-fields-toggle:focus-visible{outline:1px solid #a0cfff;outline-offset:2px;border-radius:3px}
.custom-fields-toggle-icon{font-size:12px;color:#909399}.custom-fields-count{color:#909399;font-size:12px;white-space:nowrap}.custom-fields-count.is-empty{color:#c0c4cc}
.custom-fields-container{width:100%;display:flex;flex-direction:column;border:1px solid #f2f2f2;border-radius:4px;overflow:hidden}
.custom-fields-list{display:flex;flex-direction:column}
.custom-field-item{display:flex;align-items:center;gap:6px;padding:4px 8px;border-bottom:1px solid #f2f2f2}
.custom-field-item:last-child{border-bottom:none}
.custom-field-item.is-richtext-field{align-items:flex-start}
.own-field{background:#f0f9eb}.inherited-field{background:#fdf6ec;opacity:.85}.ancestor-field{background:#f4f4f5;opacity:.7}
.field-key,.field-key-read{width:var(--custom-field-key-width,120px);min-width:var(--custom-field-key-width,120px)}
.field-key-read,.field-type-read{display:inline-flex;align-items:center}
.field-width-resizer,.field-width-resizer-spacer{width:10px;min-width:10px;flex:0 0 10px}
.field-width-resizer{position:relative;align-self:stretch;padding:0;border:none;background:transparent;cursor:col-resize;touch-action:none}
.field-width-resizer::before{content:'';position:absolute;top:4px;bottom:4px;left:50%;width:1px;background:#dcdfe6;transform:translateX(-50%);transition:background-color .15s ease}
.field-width-resizer:hover::before,.field-width-resizer:focus-visible::before{background:#409eff}
.field-width-resizer:focus-visible{outline:none}
.field-type-select,.field-type-read{width:70px;min-width:70px;font-size:12px;color:#909399}
.field-value-container{flex:1;display:flex;align-items:center;min-width:0}
.custom-field-item.is-richtext-field .field-value-container{align-self:stretch;align-items:stretch}
.field-value,.field-value-read{width:100%}
.field-value-read{color:#606266;padding-top:2px}
.field-action-btn{margin-left:auto}
.field-richtext-editor{width:100%;min-width:0}
.field-value-richtext-read{width:100%;padding:8px 10px;color:#606266;background:rgba(255,255,255,.75);border:1px solid rgba(220,223,230,.75);border-radius:4px;overflow:auto}
.field-value-richtext-read :deep(p),
.field-value-richtext-read :deep(li),
.field-value-richtext-read :deep(blockquote),
.field-value-richtext-read :deep(td),
.field-value-richtext-read :deep(th){line-height:1}
.field-value-richtext-read :deep(p){margin:6px 0}
.field-value-richtext-read :deep(img){max-width:100%;height:auto;display:block;margin:8px 0}
.field-value-richtext-read :deep(.field-richtext-empty){margin:0;color:#909399}
.inherited-indicator{font-size:10px;color:#fff;background:#e6a23c;padding:1px 4px;border-radius:2px;line-height:1.2}.inherited-indicator.ancestor{background:#909399}
.custom-field-sortable-ghost{opacity:.7;background-color:#ecf5ff !important}
.history-panel{margin-top:15px;padding:10px;background:#f8f9fb;border-radius:4px;max-height:200px;overflow-y:auto;font-size:13px;border:1px solid #ebeef5}
.history-list{display:flex;flex-direction:column}.history-item{display:flex;align-items:flex-start;gap:15px;padding:6px 0;border-bottom:1px dashed #ebeef5}.history-item:last-child{border-bottom:none}
.history-time{color:#909399;white-space:nowrap;font-family:monospace}.history-content{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.field-tag{min-width:40px;text-align:center}.history-value{color:#303133;word-break:break-all}
.status-saved{color:#67c23a}.status-saving{color:#e6a23c}.status-unsaved,.status-readonly{color:#909399}
.state-line,.state-block{display:flex;justify-content:center;align-items:center;color:#909399}.state-line{gap:8px;font-size:14px}.state-block{flex:1;min-height:0}
.source-html-preview{box-sizing:border-box;width:100%;padding:12px;color:#1f2933;background:#fff;border:1px solid #dcdfe6;overflow:auto}
.source-html-preview[contenteditable='true']{cursor:text}
.source-html-preview[contenteditable='true']:focus{outline:1px solid #409eff;outline-offset:-1px}
.source-html-preview.is-fill{flex:1;min-height:320px}
.source-html-preview.is-flow{min-height:260px}
.source-html-preview :deep(table){max-width:100%;border-collapse:collapse}
.source-html-preview :deep(td),
.source-html-preview :deep(th){border:1px solid #c8cdd3}
.source-html-preview :deep(img){max-width:100%;height:auto}
.source-html-preview :deep(a){color:#2f7edb;cursor:pointer}
.source-image-menu{position:fixed;z-index:2600;padding:4px;background:#fff;border:1px solid #dcdfe6;border-radius:4px;box-shadow:0 6px 18px rgba(31,41,51,.16)}
.shared-note-editor.is-readonly-presentation .editor-header{margin-bottom:8px;padding-bottom:0;border-bottom:none}
.shared-note-editor.is-readonly-presentation :deep(.editor-container){border:0;cursor:default}
.shared-note-editor.is-readonly-presentation :deep(.w-e-text-container){background:transparent !important}
.shared-note-editor.is-readonly-presentation :deep(.w-e-text-container [data-slate-editor]){padding-top:8px}
</style>
