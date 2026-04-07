<template>
  <div class="program-bar">
    <div class="program-header">
      <div class="program-meta">
        <div class="program-title-row">
          <div class="program-title">{{ title }}</div>
          <el-tooltip
            v-if="collapseMetaToTooltip && tooltipLines.length"
            placement="top-start"
            :show-after="120"
            effect="light"
          >
            <template #content>
              <div class="program-tooltip">
                <div v-for="(line, index) in tooltipLines" :key="`${index}-${line}`" class="program-tooltip-line">
                  {{ line }}
                </div>
              </div>
            </template>
            <button type="button" class="program-info-button" :aria-label="`${title}说明`">
              ?
            </button>
          </el-tooltip>
        </div>
        <div v-if="!collapseMetaToTooltip && showHelpText && helpText" class="program-inline-help">{{ helpText }}</div>
        <div v-if="!collapseMetaToTooltip && showCaption && caption" class="program-caption">{{ caption }}</div>
      </div>
    </div>

    <div v-if="!collapseMetaToTooltip && showHint" class="program-hint">
      规则按顺序比较，前面的规则优先；只有前面结果相同，后面的规则才会继续参与排序。
    </div>

    <div v-if="programValue.rules.length" ref="ruleListRef" class="rule-list">
      <div v-for="(rule, index) in programValue.rules" :key="`${index}-${rule.field}`" class="rule-row">
        <SortableOrderHandle
          :index="index"
          :total="programValue.rules.length"
        />

        <el-select
          size="small"
          :model-value="rule.field"
          class="rule-field-select"
          @update:model-value="value => updateRuleField(index, value)"
        >
          <el-option
            v-for="field in resolvedFieldOptions"
            :key="field.value"
            :label="field.label"
            :value="field.value"
          />
        </el-select>

        <el-select
          size="small"
          :model-value="rule.direction"
          class="rule-direction-select"
          @update:model-value="value => updateRuleDirection(index, value)"
        >
          <el-option label="升序" value="asc" />
          <el-option label="降序" value="desc" />
        </el-select>

        <div class="row-actions">
          <el-button size="small" text type="danger" :icon="Delete" @click="removeRule(index)" />
        </div>
      </div>

      <div class="add-row">
        <el-button size="small" type="primary" plain :icon="Plus" @click="addRule">添加</el-button>
        <el-button size="small" @click="resetProgram">{{ resetText }}</el-button>
      </div>
    </div>

    <div v-else class="empty-rules">
      <div v-if="emptyText" class="empty-rules-text">{{ emptyText }}</div>
      <div class="add-row is-empty">
        <el-button size="small" type="primary" plain :icon="Plus" @click="addRule">添加</el-button>
        <el-button size="small" @click="resetProgram">{{ resetText }}</el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { Delete, Plus } from '@element-plus/icons-vue';
import SortableOrderHandle from './SortableOrderHandle.vue';
import {
  createDefaultGallerySortProgram,
  getGallerySortFieldLabel,
  type GallerySortField,
} from '@/utils/imageGallery';
import { useSortableList } from '@/utils/useSortableList';

type SortDirection = 'asc' | 'desc';
type SortNulls = 'first' | 'last';

interface SortRuleLike {
  field: string;
  direction: SortDirection;
  nulls: SortNulls;
}

interface SortProgramLike {
  rules: SortRuleLike[];
}

interface SortFieldOption {
  value: string;
  label: string;
}

const props = withDefaults(
  defineProps<{
    modelValue?: SortProgramLike | null;
    title?: string;
    caption?: string;
    helpText?: string;
    emptyText?: string;
    resetText?: string;
    showCaption?: boolean;
    showHelpText?: boolean;
    showHint?: boolean;
    fieldOptions?: SortFieldOption[];
    defaultProgram?: SortProgramLike | null;
    collapseMetaToTooltip?: boolean;
  }>(),
  {
    modelValue: null,
    title: '排序规则',
    caption: '',
    helpText: '',
    emptyText: '当前没有排序规则，会回退到稳定的默认顺序。',
    resetText: '恢复默认',
    showCaption: true,
    showHelpText: true,
    showHint: true,
    fieldOptions: undefined,
    defaultProgram: null,
    collapseMetaToTooltip: false,
  }
);

const emit = defineEmits<{
  (e: 'update:modelValue', value: SortProgramLike): void;
  (e: 'reset'): void;
}>();

const defaultFieldSequence: GallerySortField[] = [
  'random',
  'duplicate_cluster',
  'weight',
  'modified_at',
  'size',
  'duration',
  'relative_path',
  'name',
  'folder_path',
  'kind',
  'resolution_area',
  'width',
  'height',
];

const defaultFieldOptions = defaultFieldSequence.map((field) => ({
  value: field,
  label: getGallerySortFieldLabel(field),
}));

const tooltipLines = computed(() => {
  const lines: string[] = [];
  if (props.showHelpText && props.helpText) {
    lines.push(props.helpText);
  }
  if (props.showCaption && props.caption) {
    lines.push(props.caption);
  }
  if (props.showHint) {
    lines.push('规则按顺序比较，前面的规则优先；只有前面结果相同，后面的规则才会继续参与排序。');
  }
  return lines;
});

const resolvedFieldOptions = computed(() =>
  Array.isArray(props.fieldOptions) && props.fieldOptions.length ? props.fieldOptions : defaultFieldOptions
);

const ruleListRef = ref<HTMLElement | null>(null);

const getFallbackField = () => resolvedFieldOptions.value[0]?.value || 'name';

const normalizeRule = (value?: Partial<SortRuleLike> | null): SortRuleLike => ({
  field: typeof value?.field === 'string' && value.field ? value.field : getFallbackField(),
  direction: value?.direction === 'asc' ? 'asc' : 'desc',
  nulls: value?.nulls === 'first' ? 'first' : 'last',
});

const createDefaultProgramValue = (): SortProgramLike => {
  const fallback = props.defaultProgram?.rules?.length ? props.defaultProgram : createDefaultGallerySortProgram();
  return {
    rules: Array.isArray(fallback.rules) ? fallback.rules.map((rule) => normalizeRule(rule)) : [normalizeRule()],
  };
};

const normalizeProgram = (value?: Partial<SortProgramLike> | null): SortProgramLike => ({
  rules: Array.isArray(value?.rules)
    ? value.rules.map((rule) => normalizeRule(rule))
    : createDefaultProgramValue().rules,
});

const cloneProgram = (value?: Partial<SortProgramLike> | null): SortProgramLike =>
  JSON.parse(JSON.stringify(normalizeProgram(value)));

const createSortRule = (
  field = getFallbackField(),
  direction: SortDirection = 'desc',
  nulls: SortNulls = 'last'
): SortRuleLike => ({
  field,
  direction,
  nulls,
});

const programValue = computed(() => normalizeProgram(props.modelValue));

const emitProgram = (program: SortProgramLike) => {
  emit('update:modelValue', normalizeProgram(program));
};

const updateProgram = (mutator: (draft: SortProgramLike) => void) => {
  const draft = cloneProgram(programValue.value);
  mutator(draft);
  emitProgram(draft);
};

const findNextField = () => {
  const usedFields = new Set(programValue.value.rules.map((rule) => rule.field));
  return resolvedFieldOptions.value.find((field) => !usedFields.has(field.value))?.value ?? getFallbackField();
};

const addRule = () => {
  updateProgram((draft) => {
    draft.rules.push(createSortRule(findNextField(), 'desc', 'last'));
  });
};

const moveRule = (fromIndex: number, toIndex: number) => {
  updateProgram((draft) => {
    if (fromIndex < 0 || fromIndex >= draft.rules.length || toIndex < 0 || toIndex >= draft.rules.length) {
      return;
    }
    const [rule] = draft.rules.splice(fromIndex, 1);
    if (!rule) {
      return;
    }
    draft.rules.splice(toIndex, 0, rule);
  });
};

const removeRule = (index: number) => {
  updateProgram((draft) => {
    draft.rules.splice(index, 1);
  });
};

const updateRuleField = (index: number, value: string | number | boolean) => {
  updateProgram((draft) => {
    draft.rules[index].field = String(value);
  });
};

const updateRuleDirection = (index: number, value: string | number | boolean) => {
  updateProgram((draft) => {
    draft.rules[index].direction = String(value) === 'asc' ? 'asc' : 'desc';
  });
};

const resetProgram = () => {
  emitProgram(createDefaultProgramValue());
  emit('reset');
};

useSortableList({
  listRef: ruleListRef,
  getDeps: () => [programValue.value.rules.length] as const,
  isEnabled: () => programValue.value.rules.length > 1,
  ghostClass: 'gallery-sort-program-ghost',
  onReorder: (oldIndex, newIndex) => moveRule(oldIndex, newIndex),
});
</script>

<style scoped>
.program-bar {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px;
  border: 1px solid #e2e8f0;
  border-radius: 18px;
  background: linear-gradient(180deg, #ffffff 0%, #f8fbff 100%);
}

.program-header,
.program-meta,
.rule-list,
.empty-rules {
  display: flex;
  flex-direction: column;
}

.program-title {
  font-size: 14px;
  font-weight: 700;
  color: #0f172a;
}

.program-inline-help,
.program-caption,
.program-hint,
.empty-rules,
.empty-rules-text {
  color: #64748b;
  font-size: 12px;
}

.program-meta {
  gap: 6px;
}

.program-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.program-hint {
  line-height: 1.5;
}

.program-info-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  padding: 0;
  border: 1px solid #cbd5e1;
  border-radius: 999px;
  background: #f8fafc;
  color: #475569;
  font-size: 12px;
  font-weight: 700;
  cursor: help;
}

.program-tooltip {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-width: 320px;
  line-height: 1.55;
}

.program-tooltip-line {
  color: #334155;
  font-size: 12px;
}

.rule-list {
  gap: 10px;
}

.rule-row {
  display: flex;
  align-items: center;
  gap: 3px;
  padding: 8px 10px;
  border-radius: 14px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  cursor: default;
}

.rule-field-select {
  flex: 1 1 132px;
  min-width: 100px;
}

.rule-direction-select {
  flex: 0 0 72px;
  min-width: 0;
  width: 100%;
}

.row-actions {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 0;
  margin-left: auto;
}

.row-actions :deep(.el-button) {
  min-width: 20px;
  padding: 4px 1px;
}

.row-actions :deep(.el-button + .el-button) {
  margin-left: 0;
}

.rule-list :deep(.gallery-sort-program-ghost) {
  opacity: 0.45;
}

.add-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.is-empty {
  margin-top: 10px;
}

@media (max-width: 780px) {
  .rule-row {
    flex-wrap: wrap;
  }

  .rule-field-select {
    min-width: 0;
    width: 100%;
  }

  .rule-direction-select {
    flex: 0 0 76px;
  }

  .row-actions {
    margin-left: 0;
  }
}
</style>
