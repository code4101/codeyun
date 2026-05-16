<template>
  <section class="doc-custom-fields-panel" :class="{ 'is-collapsed': props.collapsed }" aria-label="自定义属性">
    <div class="custom-fields-header">
      <button
        type="button"
        class="custom-fields-toggle"
        :aria-expanded="String(!props.collapsed)"
        @click="toggleCollapsed"
      >
        <el-icon class="custom-fields-toggle-icon">
          <ArrowRight v-if="props.collapsed" />
          <ArrowDown v-else />
        </el-icon>
        <span class="custom-fields-title">自定义属性</span>
        <span class="custom-fields-count" :class="{ 'is-empty': customFieldsTotalCount === 0 }">
          {{ customFieldsSummary }}
        </span>
      </button>
      <el-button link type="primary" size="small" :disabled="props.readonly" @click="addCustomField">
        <el-icon><Plus /></el-icon>
      </el-button>
    </div>

    <div
      v-if="!props.collapsed && (customFieldsList.length > 0 || inheritedFieldEntries.length > 0)"
      class="custom-fields-container"
      :style="customFieldColumnStyle"
    >
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
            :disabled="props.readonly"
          />

          <el-input
            v-model="item.key"
            size="small"
            placeholder="Key"
            class="field-key"
            :readonly="props.readonly"
            @input="emitCustomFields"
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
            :disabled="props.readonly"
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
              :readonly="props.readonly"
              @update:model-value="value => setTextFieldValue(item, value)"
            />

            <div v-else-if="item.type === 'richtext'" class="field-richtext-editor">
              <NoteEditor
                :model-value="getTextFieldValue(item)"
                layout="flow"
                :read-only="Boolean(props.readonly)"
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
              :readonly="props.readonly"
              @update:model-value="value => setNumberFieldValue(item, value)"
            />

            <el-switch
              v-else
              :model-value="getBooleanFieldValue(item)"
              size="small"
              :disabled="props.readonly"
              @update:model-value="value => setBooleanFieldValue(item, value)"
            />
          </div>

          <el-button
            link
            type="danger"
            size="small"
            class="field-action-btn"
            :disabled="props.readonly"
            @click="removeCustomField(index)"
          >
            <el-icon><Close /></el-icon>
          </el-button>
        </div>
      </div>

      <div
        v-for="item in inheritedFieldEntries"
        :key="`${item.scope}-${item.key}`"
        class="custom-field-item inherited-field"
        :class="[{ 'ancestor-field': item.scope === 'ancestor' }, { 'is-richtext-field': item.type === 'richtext' }]"
      >
        <div class="inherited-indicator" :class="{ ancestor: item.scope === 'ancestor' }">
          {{ item.scope === 'ancestor' ? '祖' : '父' }}
        </div>
        <span class="field-key-read">{{ item.key }}</span>
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
          :disabled="props.readonly"
          @click="addInheritedField(item.key, item.value, item.type)"
        >
          <el-icon><Plus /></el-icon>
        </el-button>
      </div>
    </div>

    <div v-else-if="!props.collapsed" class="custom-fields-empty">暂无自定义属性</div>
  </section>
</template>

<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { ArrowDown, ArrowRight, Close, Plus } from '@element-plus/icons-vue'
import type { NoteNode } from '@/api/notes'
import NoteEditor from '@/components/NoteEditor.vue'
import SortableOrderHandle from '@/components/SortableOrderHandle.vue'
import {
  convertNoteCustomFieldValue,
  createNoteCustomFieldItem,
  noteCustomFieldItemsToList,
  noteCustomFieldsToItems,
  normalizeNoteCustomFieldType,
  type NoteCustomFieldItem,
  type NoteCustomFieldTuple,
  type NoteCustomFieldType,
} from '@/utils/noteAutoSave'
import { isNoteSystemCustomFieldKey, stripNoteSystemCustomFields } from '@/utils/noteProgress'
import { useSortableList } from '@/utils/useSortableList'

interface InheritedFieldEntry {
  key: string
  type: NoteCustomFieldType
  value: string | number | boolean
  scope: 'direct' | 'ancestor'
}

const props = defineProps<{
  modelValue?: unknown
  inheritedFields?: NoteNode['inherited_fields'] | null
  readonly?: boolean
  collapsed?: boolean
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: NoteCustomFieldTuple[]): void
  (e: 'change', value: NoteCustomFieldTuple[]): void
  (e: 'update:collapsed', value: boolean): void
}>()

const EMPTY_RICH_TEXT_HTML_VALUES = new Set(['', '<p><br></p>', '<p></p>'])
const CUSTOM_FIELD_KEY_WIDTH_MIN = 96
const CUSTOM_FIELD_KEY_WIDTH_MAX = 360
const CUSTOM_FIELD_KEY_WIDTH_PADDING = 32

const customFieldsList = ref<NoteCustomFieldItem[]>([])
const ownCustomFieldsListRef = ref<HTMLElement | null>(null)
const customFieldKeyWidthMode = ref<'auto' | 'manual'>('auto')
const customFieldKeyWidth = ref(120)
let lastVisibleFieldsSerialized = ''
let customFieldKeyResizePointerId: number | null = null
let customFieldKeyResizeStartX = 0
let customFieldKeyResizeStartWidth = customFieldKeyWidth.value
let customFieldKeyMeasureCanvas: HTMLCanvasElement | null = null

const normalizeVisibleFields = (fields: unknown) => (
  noteCustomFieldItemsToList(noteCustomFieldsToItems(stripNoteSystemCustomFields(fields)))
)

const getSystemCustomFields = () => (
  noteCustomFieldItemsToList(noteCustomFieldsToItems(props.modelValue))
    .filter(([key]) => isNoteSystemCustomFieldKey(key))
)

const customFieldKeyTexts = computed(() => {
  const texts = [
    ...customFieldsList.value.map(item => item.key),
    ...inheritedFieldEntries.value.map(item => item.key),
  ]
    .map(item => String(item ?? '').trim())
    .filter(Boolean)
  return texts.length ? texts : ['Key']
})

const autoCustomFieldKeyWidth = computed(() => {
  const longestWidth = customFieldKeyTexts.value.reduce((maxWidth, text) => (
    Math.max(maxWidth, measureCustomFieldKeyTextWidth(text))
  ), 0)
  return clampCustomFieldKeyWidth(longestWidth + CUSTOM_FIELD_KEY_WIDTH_PADDING)
})

const resolvedCustomFieldKeyWidth = computed(() => (
  customFieldKeyWidthMode.value === 'manual' ? customFieldKeyWidth.value : autoCustomFieldKeyWidth.value
))

const customFieldColumnStyle = computed(() => ({
  '--custom-field-key-width': `${resolvedCustomFieldKeyWidth.value}px`,
}))
const customFieldsTotalCount = computed(() => customFieldsList.value.length + inheritedFieldEntries.value.length)
const customFieldsSummary = computed(() => {
  if (customFieldsTotalCount.value === 0) return '暂无'
  if (inheritedFieldEntries.value.length > 0) {
    return `${customFieldsTotalCount.value} 项，含继承 ${inheritedFieldEntries.value.length}`
  }
  return `${customFieldsTotalCount.value} 项`
})

const inheritedFieldEntries = computed<InheritedFieldEntry[]>(() => {
  const ownKeys = new Set(customFieldsList.value.map(item => item.key.trim()).filter(Boolean))
  const seenInheritedKeys = new Set<string>()
  const entries: InheritedFieldEntry[] = []

  const appendFields = (scope: InheritedFieldEntry['scope'], list: unknown) => {
    if (!Array.isArray(list)) return
    for (const item of list) {
      if (!Array.isArray(item) || item.length < 3 || typeof item[0] !== 'string') continue
      const key = item[0].trim()
      if (!key || ownKeys.has(key) || seenInheritedKeys.has(key)) continue
      const normalizedType = normalizeNoteCustomFieldType(item[1])
      const rawValue = item[2]
      entries.push({
        key,
        type: normalizedType,
        value: typeof rawValue === 'boolean' || typeof rawValue === 'number' ? rawValue : String(rawValue ?? ''),
        scope,
      })
      seenInheritedKeys.add(key)
    }
  }

  appendFields('direct', props.inheritedFields?.direct)
  appendFields('ancestor', props.inheritedFields?.ancestors)
  return entries
})

const clampCustomFieldKeyWidth = (value: number) => Math.max(
  CUSTOM_FIELD_KEY_WIDTH_MIN,
  Math.min(CUSTOM_FIELD_KEY_WIDTH_MAX, Math.round(value)),
)

const getCustomFieldKeyMeasureFont = () => {
  const bodyStyle = window.getComputedStyle(document.body)
  const fontSize = bodyStyle.fontSize || '14px'
  const fontFamily = bodyStyle.fontFamily || 'sans-serif'
  return `${fontSize} ${fontFamily}`
}

const measureCustomFieldKeyTextWidth = (text: string) => {
  customFieldKeyMeasureCanvas ??= document.createElement('canvas')
  const context = customFieldKeyMeasureCanvas.getContext('2d')
  if (!context) return text.length * 9
  context.font = getCustomFieldKeyMeasureFont()
  return context.measureText(text).width
}

const startCustomFieldKeyResize = (event: PointerEvent) => {
  customFieldKeyWidthMode.value = 'manual'
  customFieldKeyResizePointerId = event.pointerId
  customFieldKeyResizeStartX = event.clientX
  customFieldKeyResizeStartWidth = resolvedCustomFieldKeyWidth.value
  customFieldKeyWidth.value = resolvedCustomFieldKeyWidth.value
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
  window.addEventListener('pointermove', handleCustomFieldKeyResizeMove)
  window.addEventListener('pointerup', stopCustomFieldKeyResize)
  window.addEventListener('pointercancel', stopCustomFieldKeyResize)
  event.preventDefault()
}

function handleCustomFieldKeyResizeMove(event: PointerEvent) {
  if (customFieldKeyResizePointerId === null) return
  const deltaX = event.clientX - customFieldKeyResizeStartX
  customFieldKeyWidth.value = clampCustomFieldKeyWidth(customFieldKeyResizeStartWidth + deltaX)
}

const stopCustomFieldKeyResize = () => {
  if (customFieldKeyResizePointerId === null) return
  customFieldKeyResizePointerId = null
  window.removeEventListener('pointermove', handleCustomFieldKeyResizeMove)
  window.removeEventListener('pointerup', stopCustomFieldKeyResize)
  window.removeEventListener('pointercancel', stopCustomFieldKeyResize)
  document.body.style.cursor = ''
  document.body.style.userSelect = ''
}

const resetCustomFieldKeyWidthAuto = () => {
  customFieldKeyWidthMode.value = 'auto'
  customFieldKeyWidth.value = autoCustomFieldKeyWidth.value
}

const emitCustomFields = () => {
  if (props.readonly) return
  const nextFields = [
    ...noteCustomFieldItemsToList(customFieldsList.value),
    ...getSystemCustomFields(),
  ]
  lastVisibleFieldsSerialized = JSON.stringify(normalizeVisibleFields(nextFields))
  emit('update:modelValue', nextFields)
  emit('change', nextFields)
}

const toggleCollapsed = () => {
  emit('update:collapsed', !props.collapsed)
}

const addCustomField = () => {
  if (props.readonly) return
  emit('update:collapsed', false)
  customFieldsList.value.push(createNoteCustomFieldItem())
  emitCustomFields()
}

const addInheritedField = (key: string, value: string | number | boolean, typeFromInheritance?: string) => {
  if (props.readonly) return
  customFieldsList.value.push(createNoteCustomFieldItem(key, typeFromInheritance, value))
  emitCustomFields()
}

const removeCustomField = (index: number) => {
  if (props.readonly) return
  customFieldsList.value.splice(index, 1)
  emitCustomFields()
}

const handleCustomFieldTypeChange = (item: NoteCustomFieldItem) => {
  if (props.readonly) return
  item.value = convertNoteCustomFieldValue(item.type, item.value)
  emitCustomFields()
}

const getTextFieldValue = (item: NoteCustomFieldItem) => item.type === 'boolean' ? '' : String(item.value)
const getBooleanFieldValue = (item: NoteCustomFieldItem) => item.type === 'boolean' ? Boolean(item.value) : false

const setTextFieldValue = (item: NoteCustomFieldItem, value: string | number) => {
  if (props.readonly || item.type === 'boolean') return
  item.value = String(value ?? '')
  emitCustomFields()
}

const setNumberFieldValue = (item: NoteCustomFieldItem, value: string | number) => {
  if (props.readonly || item.type !== 'number') return
  item.value = String(value ?? '')
  emitCustomFields()
}

const setBooleanFieldValue = (item: NoteCustomFieldItem, value: string | number | boolean) => {
  if (props.readonly || item.type !== 'boolean') return
  item.value = Boolean(value)
  emitCustomFields()
}

const getInheritedRichTextHtml = (value: unknown) => {
  const html = String(value ?? '').trim()
  if (EMPTY_RICH_TEXT_HTML_VALUES.has(html)) return '<p class="field-richtext-empty">空</p>'
  return html
}

const formatInheritedValue = (value: unknown) => (
  typeof value === 'boolean' ? (value ? 'True' : 'False') : String(value ?? '')
)

const getFieldTypeLabel = (type: unknown, value?: unknown) => {
  if (type === 'string' || type === 'richtext' || type === 'number' || type === 'boolean') {
    const normalizedType = normalizeNoteCustomFieldType(type)
    if (normalizedType === 'boolean') return '布尔'
    if (normalizedType === 'number') return '数值'
    if (normalizedType === 'richtext') return '富文本'
    return '文本'
  }
  if (typeof type === 'boolean') return '布尔'
  if (typeof type === 'number') return '数值'
  const rawValue = value === undefined ? type : value
  const text = String(rawValue ?? '')
  return !Number.isNaN(Number(text)) && text.trim() !== '' ? '数值' : '文本'
}

useSortableList({
  listRef: ownCustomFieldsListRef,
  getDeps: () => [props.readonly, customFieldsList.value.length] as const,
  isEnabled: () => !props.readonly && customFieldsList.value.length > 1,
  ghostClass: 'custom-field-sortable-ghost',
  onReorder: (oldIndex, newIndex) => {
    const reordered = [...customFieldsList.value]
    const [movedItem] = reordered.splice(oldIndex, 1)
    if (!movedItem) return
    reordered.splice(Math.min(newIndex, reordered.length), 0, movedItem)
    customFieldsList.value = reordered
    emitCustomFields()
  },
})

watch(() => props.modelValue, (value) => {
  const visibleFields = normalizeVisibleFields(value)
  const serialized = JSON.stringify(visibleFields)
  if (serialized === lastVisibleFieldsSerialized) return
  lastVisibleFieldsSerialized = serialized
  customFieldsList.value = noteCustomFieldsToItems(visibleFields)
}, { immediate: true, deep: true })

onUnmounted(() => {
  stopCustomFieldKeyResize()
})
</script>

<style scoped>
.doc-custom-fields-panel {
  flex: 0 0 auto;
  box-sizing: border-box;
  padding: 8px 14px 10px;
  border-bottom: 1px solid #e5e7eb;
  background: #fbfcfe;
  overflow: visible;
}

.doc-custom-fields-panel.is-collapsed {
  padding-bottom: 8px;
}

.custom-fields-header {
  display: flex;
  align-items: center;
  gap: 5px;
  min-height: 24px;
}

.custom-fields-toggle {
  display: inline-flex;
  align-items: center;
  padding: 0;
  border: 0;
  background: transparent;
  color: #606266;
  cursor: pointer;
  gap: 4px;
}

.custom-fields-toggle:hover .custom-fields-title,
.custom-fields-toggle:focus-visible .custom-fields-title {
  color: #409eff;
}

.custom-fields-toggle:focus-visible {
  outline: 1px solid #a0cfff;
  outline-offset: 2px;
  border-radius: 3px;
}

.custom-fields-toggle-icon {
  color: #909399;
  font-size: 12px;
}

.custom-fields-title {
  color: #606266;
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
}

.custom-fields-count {
  color: #909399;
  font-size: 12px;
  font-weight: 400;
  white-space: nowrap;
}

.custom-fields-count.is-empty {
  color: #c0c4cc;
}

.custom-fields-empty {
  padding: 4px 0 0;
  color: #a8abb2;
  font-size: 12px;
}

.custom-fields-container {
  width: 100%;
  margin-top: 4px;
  display: flex;
  flex-direction: column;
  border: 1px solid #edf0f5;
  border-radius: 4px;
  overflow: hidden;
}

.custom-fields-list {
  display: flex;
  flex-direction: column;
}

.custom-field-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px;
  border-bottom: 1px solid #edf0f5;
}

.custom-field-item:last-child {
  border-bottom: none;
}

.custom-field-item.is-richtext-field {
  align-items: flex-start;
}

.own-field {
  background: #f0f9eb;
}

.inherited-field {
  background: #fdf6ec;
  opacity: .88;
}

.ancestor-field {
  background: #f4f4f5;
  opacity: .78;
}

.field-key,
.field-key-read {
  width: var(--custom-field-key-width, 120px);
  min-width: var(--custom-field-key-width, 120px);
}

.field-key-read,
.field-type-read {
  display: inline-flex;
  align-items: center;
}

.field-width-resizer,
.field-width-resizer-spacer {
  width: 10px;
  min-width: 10px;
  flex: 0 0 10px;
}

.field-width-resizer {
  position: relative;
  align-self: stretch;
  padding: 0;
  border: none;
  background: transparent;
  cursor: col-resize;
  touch-action: none;
}

.field-width-resizer::before {
  content: '';
  position: absolute;
  top: 4px;
  bottom: 4px;
  left: 50%;
  width: 1px;
  background: #dcdfe6;
  transform: translateX(-50%);
  transition: background-color .15s ease;
}

.field-width-resizer:hover::before,
.field-width-resizer:focus-visible::before {
  background: #409eff;
}

.field-width-resizer:focus-visible {
  outline: none;
}

.field-type-select,
.field-type-read {
  width: 70px;
  min-width: 70px;
  color: #909399;
  font-size: 12px;
}

.field-value-container {
  flex: 1;
  display: flex;
  align-items: center;
  min-width: 0;
}

.custom-field-item.is-richtext-field .field-value-container {
  align-self: stretch;
  align-items: stretch;
}

.field-value,
.field-value-read {
  width: 100%;
}

.field-value-read {
  color: #606266;
  padding-top: 2px;
}

.field-action-btn {
  margin-left: auto;
}

.field-richtext-editor {
  width: 100%;
  min-width: 0;
}

.field-value-richtext-read {
  width: 100%;
  padding: 8px 10px;
  color: #606266;
  background: rgba(255, 255, 255, .75);
  border: 1px solid rgba(220, 223, 230, .75);
  border-radius: 4px;
  overflow: auto;
}

.field-value-richtext-read :deep(p),
.field-value-richtext-read :deep(li),
.field-value-richtext-read :deep(blockquote),
.field-value-richtext-read :deep(td),
.field-value-richtext-read :deep(th) {
  line-height: 1;
}

.field-value-richtext-read :deep(p) {
  margin: 6px 0;
}

.field-value-richtext-read :deep(img) {
  max-width: 100%;
  height: auto;
  display: block;
  margin: 8px 0;
}

.field-value-richtext-read :deep(.field-richtext-empty) {
  margin: 0;
  color: #909399;
}

.inherited-indicator {
  color: #fff;
  background: #e6a23c;
  padding: 1px 4px;
  border-radius: 2px;
  font-size: 10px;
  line-height: 1.2;
}

.inherited-indicator.ancestor {
  background: #909399;
}

.custom-field-sortable-ghost {
  opacity: .7;
  background-color: #ecf5ff !important;
}
</style>
