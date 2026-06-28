<template>
  <div class="note-type-selector-wrapper" :style="replaceDropdownStyle">
    <div class="label-row" v-if="showLabel">
      <span class="field-label">{{ label }}:</span>
      <el-tooltip v-if="showHelpIcon" content="点击查看分类说明" placement="top">
        <el-icon class="help-icon" @click="emit('showHelp')"><QuestionFilled /></el-icon>
      </el-tooltip>
    </div>

    <el-popover
      placement="bottom-start"
      :width="456"
      trigger="click"
      popper-class="note-type-selector-popper"
      v-model:visible="popoverVisible"
      :disabled="disabled"
    >
      <template #reference>
        <div
          class="selector-trigger"
          :class="{ 'is-disabled': disabled }"
          :style="triggerStyle"
          :title="mixedColorTooltip"
        >
          <span class="trigger-text">{{ summaryText }}</span>
          <el-icon><ArrowDown /></el-icon>
        </div>
      </template>

      <div class="selector-panel">
        <div class="selector-caption">点击添加/移除分类，已选分类可单独调权重。</div>
        <div class="mix-preview" :title="mixedColorTooltip">
          <span class="mix-preview-label">混色映射</span>
          <div class="mix-preview-value">
            <span class="mix-preview-swatch" :style="{ backgroundColor: mixedColorHex }" />
            <div class="mix-preview-text">
              <span class="mix-preview-primary">{{ mixedColorPrimaryText }}</span>
              <span class="mix-preview-secondary">{{ mixedColorSecondaryText }}</span>
            </div>
          </div>
        </div>

        <div class="selected-list" v-if="sortedValue.length">
          <div v-for="item in sortedValue" :key="item.key" class="selected-row">
            <el-select
              :model-value="item.key"
              size="small"
              class="selected-type-select"
              :teleported="false"
              popper-class="note-type-replace-popper"
              :style="getChipSelectStyle(item.key)"
              @visible-change="visible => handleReplaceSelectVisibleChange(item.key, visible)"
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
              :title="item.label"
              @click="addType(item.id)"
            >
              <span class="type-chip-label">{{ item.label }}</span>
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
  getNodeTypeConfig,
  getOrderedNodeTypes,
  normalizeNoteTypeAssignments,
  normalizeNoteTypeWeight,
  resolveNoteTypesColor,
  type NoteTypeAssignment
} from '@/utils/nodeConfig';
import { resolveMappedStandardColor } from '@/features/color-tools';
import { fromHex, getReadableTextColor } from '@/utils/colorToolkit';

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
const replaceDropdownColumns = ref(1);
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
const REPLACE_OPTION_ROWS_PER_COLUMN = 10;

const formatSummaryEntry = (item: NoteTypeAssignment) => {
  const label = getNodeTypeConfig(item.key).label;
  return item.weight >= 100 ? label : `${label}${item.weight}`;
};

const summaryText = computed(() => sortedValue.value.map(formatSummaryEntry).join(','));
const mixedColorHex = computed(() => (
  resolveNoteTypesColor(normalizedValue.value, fallbackType.value)
  ?? getNodeTypeConfig(primaryType.value).baseColor
));
const mixedStandardColor = computed(() => resolveMappedStandardColor(mixedColorHex.value, { range: 2, method: 'cie76' }));
const mixedColorPrimaryText = computed(() => mixedStandardColor.value.displayName);
const mixedColorSecondaryText = computed(() => (
  mixedStandardColor.value.hex === mixedColorHex.value
    ? mixedColorHex.value
    : `${mixedColorHex.value} -> ${mixedStandardColor.value.hex}`
));
const mixedColorTooltip = computed(() => {
  const color = mixedStandardColor.value;
  const labels = [color.zhNames[0], color.enNames[0]].filter(Boolean);
  return `当前混色：${mixedColorHex.value}；最接近标准色：${labels.join(' / ') || color.displayName} · ${color.hex}`;
});

const replaceDropdownStyle = computed(() => {
  const columns = Math.max(1, replaceDropdownColumns.value);
  const width = Math.max(196, columns * 160 + Math.max(0, columns - 1) * 6 + 12);
  return {
    '--replace-option-columns': String(columns),
    '--replace-option-rows': String(REPLACE_OPTION_ROWS_PER_COLUMN),
    '--replace-option-dropdown-width': `${width}px`
  };
});

const triggerStyle = computed(() => {
  const color = mixedColorHex.value;
  return {
    borderColor: color,
    color: getReadableTextColor(fromHex(color)),
    backgroundColor: color,
    borderWidth: '1px',
    borderStyle: 'solid'
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

const getReplaceOptionColumnCount = (currentKey: string) => (
  Math.max(1, Math.ceil(getReplaceOptions(currentKey).length / REPLACE_OPTION_ROWS_PER_COLUMN))
);

const getChipStyle = (typeKey: string) => {
  const color = getNodeTypeConfig(typeKey).baseColor;
  return {
    borderColor: color,
    color: getReadableTextColor(fromHex(color)),
    backgroundColor: color,
    borderWidth: '1px',
    borderStyle: 'solid'
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

const handleReplaceSelectVisibleChange = (currentKey: string, visible: boolean) => {
  replaceDropdownColumns.value = visible ? getReplaceOptionColumnCount(currentKey) : 1;
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
.mix-preview{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:8px 10px;border:1px solid #ebeef5;border-radius:8px;background:#f8fafc}
.mix-preview-label{flex:none;font-size:12px;color:#7a8799;white-space:nowrap}
.mix-preview-value{display:flex;align-items:center;gap:8px;min-width:0}
.mix-preview-swatch{flex:none;width:16px;height:16px;border-radius:999px;border:1px solid rgba(15,23,42,.12);box-shadow:inset 0 0 0 1px rgba(255,255,255,.35)}
.mix-preview-text{display:flex;flex-direction:column;gap:2px;min-width:0}
.mix-preview-primary,.mix-preview-secondary{display:block;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mix-preview-primary{font-size:12px;line-height:1.25;color:#243046;font-weight:600}
.mix-preview-secondary{font-size:11px;line-height:1.2;color:#7a8799}
.add-section{display:flex;flex-direction:column;gap:8px}
.type-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
.type-chip{display:flex;align-items:center;justify-content:space-between;gap:8px;min-height:34px;padding:0 12px;border-radius:6px;background:#fff;cursor:pointer;transition:transform .15s ease,box-shadow .15s ease}
.type-chip:hover{transform:translateY(-1px);box-shadow:0 2px 8px rgba(15,23,42,.08)}
.type-chip-label{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.type-chip-weight,.type-chip-add{font-size:11px;opacity:.8}
.type-chip-add{flex-shrink:0}
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
:deep(.note-type-selector-popper.el-popper){width:min(456px, calc(100vw - 32px)) !important;max-width:calc(100vw - 32px) !important}
:deep(.note-type-replace-popper.el-popper){width:min(var(--replace-option-dropdown-width, 320px), calc(100vw - 48px)) !important;max-width:calc(100vw - 48px) !important;min-width:min(var(--replace-option-dropdown-width, 320px), calc(100vw - 48px)) !important}
:deep(.note-type-replace-popper .el-select-dropdown__wrap){max-height:none !important}
:deep(.note-type-replace-popper .el-select-dropdown__list),
:deep(.note-type-replace-popper .el-scrollbar__view){display:grid;grid-auto-flow:column;grid-template-rows:repeat(var(--replace-option-rows, 10), minmax(0, auto));grid-auto-columns:minmax(156px, 1fr);gap:4px;padding:4px}
:deep(.note-type-replace-popper .el-select-dropdown__item){display:flex;align-items:stretch;height:auto;min-height:0;padding:0;line-height:1;background:transparent}
:deep(.note-type-replace-popper .el-select-dropdown__item.is-hovering),
:deep(.note-type-replace-popper .el-select-dropdown__item.is-selected){background:transparent}
:deep(.note-type-replace-popper .el-select-dropdown__item .replace-option-chip){width:100%;min-height:0;justify-content:flex-start;padding:4px 8px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;line-height:1.2}
:deep(.note-type-replace-popper .el-select-dropdown__item.is-selected .replace-option-chip){box-shadow:0 0 0 1px rgba(64,158,255,.28) inset}
:deep(.note-type-replace-popper .el-select-dropdown__item.is-hovering .replace-option-chip){transform:translateY(-1px);box-shadow:0 2px 8px rgba(15,23,42,.08)}

@media (max-width: 540px) {
  .mix-preview{align-items:flex-start;flex-direction:column}
  .type-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
}
</style>
