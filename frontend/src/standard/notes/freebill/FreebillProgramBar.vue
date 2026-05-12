<template>
  <div class="freebill-program-bar">
    <div class="program-header">
      <div class="program-title-row">
        <h2>{{ title }}</h2>
        <el-tooltip v-if="helpText" placement="top-start" effect="light">
          <template #content>
            <div class="program-help">{{ helpText }}</div>
          </template>
          <el-icon class="help-icon"><QuestionFilled /></el-icon>
        </el-tooltip>
        <el-button
          class="title-add-button"
          size="small"
          text
          :icon="Plus"
          title="新增规则"
          aria-label="新增规则"
          @click="addRule"
        />
      </div>
    </div>

    <div v-if="programValue.rules.length" ref="ruleListRef" class="rule-list">
      <div
        v-for="(rule, index) in programValue.rules"
        :key="`${index}-${rule.matcher.kind}-${rule.matcher.field}`"
        class="rule-row"
      >
        <SortableOrderHandle :index="index" :total="programValue.rules.length" size="sm" />

        <el-select
          size="small"
          :model-value="rule.action"
          class="action-select"
          @update:model-value="value => updateRuleAction(index, value)"
        >
          <el-option label="包含" value="include" />
          <el-option label="排除" value="exclude" />
          <el-option label="筛选" value="filter" />
        </el-select>

        <el-select
          size="small"
          :model-value="getRuleFieldValue(rule)"
          class="field-select"
          @update:model-value="value => replaceRuleField(index, String(value))"
        >
          <el-option
            v-for="field in resolvedFieldOptions"
            :key="field.value"
            :label="field.label"
            :value="field.value"
          />
        </el-select>

        <template v-if="getRuleMode(rule) === 'date'">
          <el-select
            size="small"
            :model-value="rule.matcher.op || 'between'"
            class="op-select"
            @update:model-value="value => updateRuleOp(index, String(value))"
          >
            <el-option label="范围" value="between" />
            <el-option label="不早于" value="gte" />
            <el-option label="不晚于" value="lte" />
            <el-option label="等于" value="eq" />
          </el-select>
          <el-date-picker
            v-if="(rule.matcher.op || 'between') === 'between'"
            :model-value="getRuleValues(rule)"
            type="daterange"
            size="small"
            value-format="YYYY-MM-DD"
            start-placeholder="开始日期"
            end-placeholder="结束日期"
            class="date-range"
            @update:model-value="value => updateRuleValues(index, Array.isArray(value) ? value : [])"
          />
          <el-date-picker
            v-else
            :model-value="String(rule.matcher.value || '')"
            type="date"
            size="small"
            value-format="YYYY-MM-DD"
            placeholder="日期"
            class="date-single"
            @update:model-value="value => updateRuleValue(index, value || '')"
          />
        </template>

        <template v-else-if="getRuleMode(rule) === 'enum'">
          <el-select
            size="small"
            :model-value="rule.matcher.op || 'eq'"
            class="op-select"
            @update:model-value="value => updateRuleOp(index, String(value))"
          >
            <el-option label="等于" value="eq" />
            <el-option label="不等于" value="neq" />
          </el-select>
          <el-select
            size="small"
            :model-value="String(rule.matcher.value || '')"
            class="value-select"
            clearable
            filterable
            @update:model-value="value => updateRuleValue(index, value || '')"
          >
            <el-option
              v-for="option in getEnumOptions(rule)"
              :key="option"
              :label="option"
              :value="option"
            />
          </el-select>
        </template>

        <template v-else-if="getRuleMode(rule) === 'number'">
          <el-select
            size="small"
            :model-value="rule.matcher.op || 'between'"
            class="op-select"
            @update:model-value="value => updateRuleOp(index, String(value))"
          >
            <el-option label="范围" value="between" />
            <el-option label="不小于" value="gte" />
            <el-option label="不大于" value="lte" />
            <el-option label="等于" value="eq" />
          </el-select>
          <div v-if="(rule.matcher.op || 'between') === 'between'" class="number-range">
            <el-input-number
              :model-value="getNumberRangeValue(rule, 0)"
              size="small"
              controls-position="right"
              @update:model-value="value => updateNumberRangeValue(index, 0, value)"
            />
            <span>至</span>
            <el-input-number
              :model-value="getNumberRangeValue(rule, 1)"
              size="small"
              controls-position="right"
              @update:model-value="value => updateNumberRangeValue(index, 1, value)"
            />
          </div>
          <el-input-number
            v-else
            :model-value="Number(rule.matcher.value || 0)"
            size="small"
            controls-position="right"
            class="number-single"
            @update:model-value="value => updateRuleValue(index, value ?? '')"
          />
        </template>

        <template v-else-if="getRuleMode(rule) === 'text' || getRuleMode(rule) === 'full_text'">
          <el-select
            v-if="getRuleMode(rule) === 'text'"
            size="small"
            :model-value="rule.matcher.op || 'contains'"
            class="op-select"
            @update:model-value="value => updateRuleOp(index, String(value))"
          >
            <el-option label="包含" value="contains" />
            <el-option label="不包含" value="not_contains" />
            <el-option label="等于" value="eq" />
            <el-option label="不等于" value="neq" />
          </el-select>
          <el-input
            size="small"
            :model-value="String(rule.matcher.value || '')"
            class="text-input"
            clearable
            :placeholder="getRuleMode(rule) === 'full_text' ? '搜索对方、商品、备注、单号' : '关键字'"
            @update:model-value="value => updateRuleValue(index, value)"
          />
        </template>

        <div class="row-actions">
          <el-button size="small" text type="danger" :icon="Delete" @click="removeRule(index)" />
        </div>
      </div>
    </div>

    <div v-else class="empty-rules">
      当前没有规则。
    </div>

    <div v-if="hasProgramActions" class="program-actions">
      <el-button
        v-if="showApply"
        size="small"
        type="primary"
        :loading="loading"
        @click="$emit('apply')"
      >
        {{ applyText }}
      </el-button>
      <el-button v-if="showReset" size="small" text @click="$emit('reset')">{{ resetText }}</el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { Delete, Plus, QuestionFilled } from '@element-plus/icons-vue'

import SortableOrderHandle from '@/components/SortableOrderHandle.vue'
import { useSortableList } from '@/utils/useSortableList'
import {
  cloneFreebillProgramChannel,
  createFreebillProgramRule,
  normalizeFreebillProgramChannel,
  type FreebillFilterOptions,
  type FreebillProgramChannel,
  type FreebillProgramOperator,
  type FreebillProgramRule,
  type FreebillProgramRuleAction,
} from '@/api/freebill'

type FieldMode = 'all' | 'full_text' | 'text' | 'enum' | 'date' | 'number'

export interface FreebillProgramFieldOption {
  value: string
  label: string
  field?: string
  mode: FieldMode
  enumKey?: 'sources' | 'directions' | 'categories'
}

const ALL_FIELD_VALUE = '__all'
const FULL_TEXT_FIELD_VALUE = '__full_text'

const props = withDefaults(defineProps<{
  modelValue?: FreebillProgramChannel | null
  title: string
  helpText?: string
  applyText?: string
  resetText?: string
  loading?: boolean
  showApply?: boolean
  showReset?: boolean
  filterOptions?: FreebillFilterOptions
  fieldOptions?: FreebillProgramFieldOption[]
}>(), {
  modelValue: null,
  helpText: '',
  applyText: '执行',
  resetText: '恢复默认',
  loading: false,
  showApply: true,
  showReset: true,
  filterOptions: () => ({ sources: [], directions: [], categories: [] }),
  fieldOptions: undefined,
})

const emit = defineEmits<{
  (event: 'update:modelValue', value: FreebillProgramChannel): void
  (event: 'apply'): void
  (event: 'reset'): void
}>()

const defaultFieldOptions: FreebillProgramFieldOption[] = [
  { value: ALL_FIELD_VALUE, label: '全部记录', mode: 'all' },
  { value: 'create_time', label: '交易时间', field: 'create_time', mode: 'date' },
  { value: 'source', label: '来源', field: 'source', mode: 'enum', enumKey: 'sources' },
  { value: 'direction', label: '收支', field: 'direction', mode: 'enum', enumKey: 'directions' },
  { value: 'type', label: '分类', field: 'type', mode: 'enum', enumKey: 'categories' },
  { value: FULL_TEXT_FIELD_VALUE, label: '全文搜索', mode: 'full_text' },
  { value: 'counterparty', label: '交易对方', field: 'counterparty', mode: 'text' },
  { value: 'product_name', label: '商品', field: 'product_name', mode: 'text' },
  { value: 'amount', label: '金额', field: 'amount', mode: 'number' },
  { value: 'status', label: '状态', field: 'status', mode: 'text' },
]

const hasProgramActions = computed(() => props.showApply || props.showReset)
const programValue = computed(() => normalizeFreebillProgramChannel(props.modelValue))
const resolvedFieldOptions = computed(() => (
  props.fieldOptions?.length ? props.fieldOptions : defaultFieldOptions
))
const ruleListRef = ref<HTMLElement | null>(null)

function emitProgram(program: FreebillProgramChannel) {
  emit('update:modelValue', normalizeFreebillProgramChannel(program))
}

function updateProgram(mutator: (draft: FreebillProgramChannel) => void) {
  const draft = cloneFreebillProgramChannel(programValue.value)
  mutator(draft)
  emitProgram(draft)
}

function getFieldOptionByValue(value: string) {
  return resolvedFieldOptions.value.find((option) => option.value === value) ?? resolvedFieldOptions.value[0]
}

function getFieldOptionByRule(rule: FreebillProgramRule) {
  if (rule.matcher.kind === 'all') return getFieldOptionByValue(ALL_FIELD_VALUE)
  if (rule.matcher.kind === 'full_text_contains') return getFieldOptionByValue(FULL_TEXT_FIELD_VALUE)
  return resolvedFieldOptions.value.find((option) => option.field === rule.matcher.field || option.value === rule.matcher.field)
    ?? getFieldOptionByValue(ALL_FIELD_VALUE)
}

function getRuleFieldValue(rule: FreebillProgramRule) {
  return getFieldOptionByRule(rule)?.value ?? ALL_FIELD_VALUE
}

function getRuleMode(rule: FreebillProgramRule): FieldMode {
  return getFieldOptionByRule(rule)?.mode ?? 'all'
}

function getDefaultFieldValue() {
  return resolvedFieldOptions.value.find((option) => option.mode === 'date')?.value
    ?? resolvedFieldOptions.value.find((option) => option.mode === 'full_text')?.value
    ?? resolvedFieldOptions.value[0]?.value
    ?? ALL_FIELD_VALUE
}

function createRuleForField(value: string): FreebillProgramRule {
  const option = getFieldOptionByValue(value)
  if (!option || option.mode === 'all') return createFreebillProgramRule('include', 'all')
  if (option.mode === 'full_text') {
    return {
      action: 'filter',
      matcher: {
        kind: 'full_text_contains',
        value: '',
        values: [],
        ignore_case: true,
      },
    }
  }
  return {
    action: 'filter',
    matcher: {
      kind: 'field',
      field: option.field ?? option.value,
      op: getDefaultOperator(option.mode),
      value: option.mode === 'number' ? 0 : '',
      values: option.mode === 'date' || option.mode === 'number' ? [] : [],
      ignore_case: true,
    },
  }
}

function getDefaultOperator(mode: FieldMode): FreebillProgramOperator | null {
  if (mode === 'date' || mode === 'number') return 'between'
  if (mode === 'enum') return 'eq'
  if (mode === 'text') return 'contains'
  return null
}

function addRule() {
  updateProgram((draft) => {
    draft.rules.push(createRuleForField(getDefaultFieldValue()))
  })
}

function removeRule(index: number) {
  updateProgram((draft) => {
    draft.rules.splice(index, 1)
  })
}

function moveRule(fromIndex: number, toIndex: number) {
  updateProgram((draft) => {
    if (fromIndex < 0 || fromIndex >= draft.rules.length || toIndex < 0 || toIndex >= draft.rules.length) return
    const [rule] = draft.rules.splice(fromIndex, 1)
    if (rule) draft.rules.splice(toIndex, 0, rule)
  })
}

function patchRule(index: number, mutator: (rule: FreebillProgramRule) => void) {
  updateProgram((draft) => {
    const rule = draft.rules[index]
    if (!rule) return
    mutator(rule)
  })
}

function updateRuleAction(index: number, value: string | number | boolean) {
  patchRule(index, (rule) => {
    const action = String(value)
    rule.action = (action === 'exclude' || action === 'filter' ? action : 'include') as FreebillProgramRuleAction
  })
}

function replaceRuleField(index: number, value: string) {
  updateProgram((draft) => {
    const previousAction = draft.rules[index]?.action
    draft.rules[index] = createRuleForField(value)
    if (previousAction && draft.rules[index]) {
      draft.rules[index].action = previousAction
    }
  })
}

function updateRuleOp(index: number, value: string) {
  patchRule(index, (rule) => {
    rule.matcher.op = value as FreebillProgramOperator
    if (value === 'between') {
      rule.matcher.value = undefined
      rule.matcher.values = Array.isArray(rule.matcher.values) ? rule.matcher.values : []
    } else {
      rule.matcher.values = []
    }
  })
}

function updateRuleValue(index: number, value: unknown) {
  patchRule(index, (rule) => {
    rule.matcher.value = value
    rule.matcher.values = []
  })
}

function updateRuleValues(index: number, values: unknown[]) {
  patchRule(index, (rule) => {
    rule.matcher.value = undefined
    rule.matcher.values = values
  })
}

function getRuleValues(rule: FreebillProgramRule) {
  return Array.isArray(rule.matcher.values) ? rule.matcher.values.map((item) => String(item || '')) : []
}

function getNumberRangeValue(rule: FreebillProgramRule, index: number) {
  const value = Array.isArray(rule.matcher.values) ? rule.matcher.values[index] : undefined
  const numberValue = Number(value)
  return Number.isFinite(numberValue) ? numberValue : undefined
}

function updateNumberRangeValue(index: number, valueIndex: number, value: number | undefined) {
  patchRule(index, (rule) => {
    const values = Array.isArray(rule.matcher.values) ? [...rule.matcher.values] : []
    values[valueIndex] = value ?? ''
    rule.matcher.values = values
    rule.matcher.value = undefined
  })
}

function getEnumOptions(rule: FreebillProgramRule) {
  const option = getFieldOptionByRule(rule)
  if (!option?.enumKey) return []
  return props.filterOptions[option.enumKey] ?? []
}

useSortableList({
  listRef: ruleListRef,
  getDeps: () => [programValue.value.rules.length] as const,
  isEnabled: () => programValue.value.rules.length > 1,
  ghostClass: 'freebill-program-ghost',
  onReorder: (oldIndex, newIndex) => moveRule(oldIndex, newIndex),
})
</script>

<style scoped>
.freebill-program-bar {
  display: grid;
  gap: 8px;
  border: 1px solid #dfe5ee;
  background: #fff;
  padding: 10px 12px;
}

.program-header,
.program-title-row,
.program-actions,
.rule-row,
.row-actions,
.number-range {
  display: flex;
  align-items: center;
}

.program-header {
  justify-content: flex-start;
  gap: 12px;
}

.program-title-row {
  gap: 6px;
  min-width: 0;
}

.program-title-row h2 {
  margin: 0;
  color: #0f172a;
  font-size: 15px;
  font-weight: 650;
  letter-spacing: 0;
}

.help-icon {
  color: #64748b;
  cursor: help;
}

.title-add-button {
  width: 24px;
  height: 24px;
  padding: 0;
}

.program-help {
  max-width: 340px;
  line-height: 1.6;
}

.program-actions {
  flex-wrap: wrap;
  justify-content: flex-start;
  gap: 6px;
}

.rule-list {
  display: grid;
  gap: 6px;
}

.rule-row {
  min-width: 0;
  gap: 6px;
}

.action-select {
  width: 76px;
}

.field-select {
  width: 126px;
}

.op-select {
  width: 82px;
}

.value-select {
  width: 150px;
}

.date-range {
  width: 238px;
}

.date-single {
  width: 136px;
}

.text-input {
  width: 260px;
}

.number-range {
  gap: 6px;
}

.number-range :deep(.el-input-number),
.number-single {
  width: 120px;
}

.number-range span,
.empty-rules {
  color: #64748b;
  font-size: 12px;
}

.row-actions {
  margin-left: auto;
}

:deep(.freebill-program-ghost) {
  opacity: 0.45;
}

@media (max-width: 980px) {
  .program-header {
    align-items: stretch;
    flex-direction: column;
  }

  .program-actions {
    justify-content: flex-start;
  }

  .rule-row {
    align-items: stretch;
    flex-wrap: wrap;
  }

  .text-input,
  .date-range {
    width: 100%;
  }
}
</style>
