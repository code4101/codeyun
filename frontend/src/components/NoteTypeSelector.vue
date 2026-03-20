<template>
  <div class="note-type-selector-wrapper">
    <div class="label-row" v-if="showLabel">
      <span class="field-label">{{ label }}:</span>
      <el-tooltip v-if="showHelpIcon" content="点击查看分类说明" placement="top">
        <el-icon class="help-icon" @click="emit('showHelp')"><QuestionFilled /></el-icon>
      </el-tooltip>
    </div>

    <el-popover
      placement="bottom-start"
      :width="420"
      trigger="click"
      popper-class="note-type-selector-popper"
      v-model:visible="popoverVisible"
      :disabled="disabled"
    >
      <template #reference>
        <div class="selector-trigger" :class="{ 'is-disabled': disabled }" :style="triggerStyle">
          <span class="trigger-text">{{ summaryText }}</span>
          <el-icon><ArrowDown /></el-icon>
        </div>
      </template>

      <div class="selector-panel">
        <div class="selector-caption">点击添加/移除分类，已选分类可单独调权重。</div>

        <div class="selected-list" v-if="sortedValue.length">
          <div v-for="item in sortedValue" :key="item.key" class="selected-row">
            <el-select
              :model-value="item.key"
              size="small"
              class="selected-type-select"
              :style="getChipSelectStyle(item.key)"
              @update:model-value="value => replaceType(item.key, value)"
            >
              <el-option
                v-for="option in getReplaceOptions(item.key)"
                :key="option.id"
                :label="option.label"
                :value="option.id"
              >
                <span class="replace-option-chip" :style="getChipStyle(option.id)">{{ option.label }}</span>
              </el-option>
            </el-select>
            <el-input-number
              :model-value="item.weight"
              :min="0"
              :max="100"
              :step="5"
              size="small"
              controls-position="right"
              class="weight-input"
              @update:model-value="value => updateWeight(item.key, value)"
            />
            <el-button text :icon="Close" @click="removeType(item.key)" />
          </div>
        </div>

        <div class="add-section">
          <el-button size="small" plain :icon="Plus" @click="showAddOptions = !showAddOptions">
            新增分类
          </el-button>
          <div v-if="showAddOptions" class="type-grid">
            <button
              v-for="item in availableOptions"
              :key="item.id"
              type="button"
              class="type-chip"
              :style="getChipStyle(item.id)"
              @click="addType(item.id)"
            >
              <span>{{ item.label }}</span>
              <el-icon class="type-chip-add"><Plus /></el-icon>
            </button>
            <div v-if="availableOptions.length === 0" class="add-empty">没有可新增的分类了</div>
          </div>
        </div>

        <div class="panel-footer">
          <span>颜色会按分类权重自动混合；总权重不足 100 时自动补白色。</span>
          <el-button size="small" plain @click="managerVisible = true">管理分类</el-button>
        </div>
      </div>
    </el-popover>

    <NoteTypeManagerDialog v-model="managerVisible" @saved="handlePaletteSaved" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { ArrowDown, Close, Plus, QuestionFilled } from '@element-plus/icons-vue';
import NoteTypeManagerDialog from './NoteTypeManagerDialog.vue';
import {
  derivePrimaryNodeType,
  ensureNoteTypePaletteLoaded,
  getNodeStyle,
  getNodeTypeConfig,
  getOrderedNodeTypes,
  normalizeNoteTypeAssignments,
  normalizeNoteTypeWeight,
  type NoteTypeAssignment
} from '@/utils/nodeConfig';

const props = defineProps<{
  modelValue: NoteTypeAssignment[] | null | undefined;
  legacyType?: string | null;
  legacyColor?: string | null;
  label?: string;
  showLabel?: boolean;
  showHelpIcon?: boolean;
  disabled?: boolean;
}>();

const emit = defineEmits<{
  (e: 'update:modelValue', value: NoteTypeAssignment[]): void;
  (e: 'change', value: NoteTypeAssignment[]): void;
  (e: 'showHelp'): void;
}>();

const popoverVisible = ref(false);
const managerVisible = ref(false);
const showAddOptions = ref(false);
const options = computed(() => getOrderedNodeTypes());
const fallbackType = computed(() => props.legacyType || 'general');
const normalizedValue = computed(() => normalizeNoteTypeAssignments(props.modelValue, fallbackType.value));
const sortedValue = computed(() => normalizedValue.value
  .map((item, originalIndex) => ({ ...item, originalIndex }))
  .sort((left, right) => {
    if (right.weight !== left.weight) return right.weight - left.weight;
    return left.originalIndex - right.originalIndex;
  }));
const primaryType = computed(() => derivePrimaryNodeType(normalizedValue.value, fallbackType.value));
const availableOptions = computed(() => options.value.filter(item => !isSelected(item.id)));

const formatSummaryEntry = (item: NoteTypeAssignment) => {
  const label = getNodeTypeConfig(item.key).label;
  return item.weight >= 100 ? label : `${label}${item.weight}`;
};

const summaryText = computed(() => sortedValue.value.map(formatSummaryEntry).join(','));

const triggerStyle = computed(() => {
  const style = getNodeStyle(primaryType.value, 'idea', props.legacyColor, normalizedValue.value);
  return {
    borderColor: style.borderColor,
    color: style.color,
    backgroundColor: style.backgroundColor,
    borderWidth: style.borderWidth,
    borderStyle: style.borderStyle
  };
});

const emitValue = (value: NoteTypeAssignment[]) => {
  emit('update:modelValue', value);
  emit('change', value);
};

onMounted(() => {
  ensureNoteTypePaletteLoaded().catch(error => {
    console.warn('Failed to load note category palette:', error);
  });
});

watch(popoverVisible, value => {
  if (!value) showAddOptions.value = false;
});

const isSelected = (typeKey: string) => normalizedValue.value.some(item => item.key === typeKey);
const getReplaceOptions = (currentKey: string) => {
  const occupiedKeys = new Set(
    normalizedValue.value
      .filter(item => item.key !== currentKey)
      .map(item => item.key)
  );
  return options.value.filter(item => item.id === currentKey || !occupiedKeys.has(item.id));
};

const getChipStyle = (typeKey: string) => {
  const style = getNodeStyle(typeKey, 'idea');
  return {
    borderColor: style.borderColor,
    color: style.color,
    backgroundColor: style.backgroundColor,
    borderWidth: style.borderWidth,
    borderStyle: style.borderStyle
  };
};

const getChipSelectStyle = (typeKey: string) => {
  const style = getChipStyle(typeKey);
  return {
    '--chip-border-color': style.borderColor,
    '--chip-text-color': style.color,
    '--chip-bg-color': style.backgroundColor,
    '--chip-border-width': style.borderWidth,
    '--chip-border-style': style.borderStyle
  };
};

const addType = (typeKey: string) => {
  if (props.disabled) return;
  const next = [...normalizedValue.value];
  if (next.some(item => item.key === typeKey)) return;
  next.push({ key: typeKey, weight: 100 });
  emitValue(normalizeNoteTypeAssignments(next, fallbackType.value));
  showAddOptions.value = false;
};

const updateWeight = (typeKey: string, value: unknown) => {
  if (props.disabled) return;
  const next = normalizedValue.value.map(item => item.key === typeKey
    ? { ...item, weight: normalizeNoteTypeWeight(value) }
    : item);
  emitValue(normalizeNoteTypeAssignments(next, fallbackType.value));
};

const replaceType = (currentKey: string, value: unknown) => {
  if (props.disabled) return;
  const nextKey = typeof value === 'string' ? value.trim() : '';
  if (!nextKey || nextKey === currentKey) return;
  const next = normalizedValue.value.map(item => item.key === currentKey
    ? { ...item, key: nextKey }
    : item);
  emitValue(normalizeNoteTypeAssignments(next, fallbackType.value));
};

const removeType = (typeKey: string) => {
  if (props.disabled) return;
  const next = normalizedValue.value.filter(item => item.key !== typeKey);
  emitValue(normalizeNoteTypeAssignments(next, fallbackType.value));
};

const handlePaletteSaved = () => {
  managerVisible.value = false;
  showAddOptions.value = false;
};
</script>

<style scoped>
.note-type-selector-wrapper{display:flex;align-items:center;gap:8px}
.label-row{display:flex;align-items:center;gap:4px}
.field-label{font-size:12px;color:#606266;white-space:nowrap}
.help-icon{font-size:14px;color:#909399;cursor:help}
.selector-trigger{display:flex;align-items:center;justify-content:space-between;padding:0 10px;height:28px;border-radius:4px;cursor:pointer;font-size:12px;min-width:150px;transition:all .2s;user-select:none}
.selector-trigger:hover{filter:brightness(.97)}.selector-trigger.is-disabled{cursor:not-allowed;opacity:.6}.selector-trigger.is-disabled:hover{filter:none}
.trigger-text{margin-right:8px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.selector-panel{display:flex;flex-direction:column;gap:12px}
.selector-caption{font-size:12px;color:#909399;line-height:1.4}
.add-section{display:flex;flex-direction:column;gap:8px}
.type-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.type-chip{display:flex;align-items:center;justify-content:space-between;gap:8px;min-height:34px;padding:0 10px;border-radius:6px;background:#fff;cursor:pointer;transition:transform .15s ease,box-shadow .15s ease}
.type-chip:hover{transform:translateY(-1px);box-shadow:0 2px 8px rgba(15,23,42,.08)}
.type-chip-weight,.type-chip-add{font-size:11px;opacity:.8}
.add-empty{grid-column:1 / -1;font-size:12px;color:#909399;padding:8px 2px}
.selected-list{display:flex;flex-direction:column;gap:8px}
.selected-row{display:grid;grid-template-columns:minmax(0,1fr) 110px 28px;align-items:center;gap:8px}
.selected-type-select{width:100%}
.selected-type-select:deep(.el-select__wrapper){
  background:var(--chip-bg-color,#fff);
  color:var(--chip-text-color,#606266);
  box-shadow:0 0 0 var(--chip-border-width,1px) var(--chip-border-color,#dcdfe6) inset;
  border-radius:6px;
}
.selected-type-select:deep(.el-select__selected-item),
.selected-type-select:deep(.el-select__caret){
  color:var(--chip-text-color,#606266);
}
.replace-option-chip{display:flex;align-items:center;justify-content:center;min-height:26px;padding:0 8px;border-radius:6px;font-size:12px}
.weight-input{width:110px}
.panel-footer{display:flex;align-items:center;justify-content:space-between;gap:12px;font-size:12px;color:#909399;line-height:1.4}
</style>
