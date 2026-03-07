<template>
  <div class="program-bar">
    <div class="program-header">
      <div class="program-meta">
        <div class="program-title-row">
          <div class="program-title">{{ title }}</div>
          <div v-if="helpText" class="program-inline-help">{{ helpText }}</div>
        </div>
        <div v-if="caption" class="program-caption">{{ caption }}</div>
      </div>
    </div>

    <div v-if="hintText" class="program-hint">{{ hintText }}</div>

    <div v-if="visibleRules.length" class="rule-list">
      <div v-for="(rule, visibleIndex) in visibleRules" :key="`${getActualRuleIndex(visibleIndex)}-${rule.matcher.kind}`" class="rule-row">
        <el-select
          size="small"
          :model-value="rule.action"
          class="w-90"
          @update:model-value="value => patchRule(getActualRuleIndex(visibleIndex), draft => { draft.action = value; })"
        >
          <el-option label="包含" value="include" />
          <el-option label="排除" value="exclude" />
        </el-select>

        <el-select
          size="small"
          :model-value="getRuleTemplateValue(rule)"
          class="w-150"
          @update:model-value="value => replaceRuleTemplate(getActualRuleIndex(visibleIndex), value)"
        >
          <el-option
            v-for="item in ruleTemplates"
            :key="item.value"
            :label="item.label"
            :value="item.value"
          />
        </el-select>

        <template v-if="rule.matcher.kind === 'id'">
          <el-input
            size="small"
            :model-value="formatIdInput(rule)"
            placeholder="多个节点 ID 用逗号分隔"
            clearable
            class="w-320"
            @update:model-value="value => updateIdRule(getActualRuleIndex(visibleIndex), value)"
          />
        </template>

        <template v-else-if="rule.matcher.kind === 'field'">
          <el-select
            size="small"
            :model-value="rule.matcher.op || getDefaultOp(getMatcherField(rule))"
            class="w-110"
            @update:model-value="value => updateFieldOp(getActualRuleIndex(visibleIndex), value)"
          >
            <el-option
              v-for="op in getAllowedOps(getMatcherField(rule))"
              :key="op.value"
              :label="op.label"
              :value="op.value"
            />
          </el-select>

          <template v-if="getFieldMode(getMatcherField(rule)) === 'enum'">
            <el-select
              size="small"
              :model-value="String(rule.matcher.value ?? '')"
              clearable
              class="w-150"
              @update:model-value="value => patchRule(getActualRuleIndex(visibleIndex), draft => { draft.matcher.value = value || ''; })"
            >
              <el-option
                v-for="option in getFieldEnumOptions(getMatcherField(rule))"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              />
            </el-select>
          </template>

          <template v-else-if="getFieldMode(getMatcherField(rule)) === 'text'">
            <el-input
              size="small"
              :model-value="String(rule.matcher.value ?? '')"
              :placeholder="getTextFieldPlaceholder(getMatcherField(rule))"
              clearable
              class="w-280"
              @update:model-value="value => patchRule(getActualRuleIndex(visibleIndex), draft => { draft.matcher.value = value || ''; draft.matcher.values = []; })"
            />
          </template>

          <template v-else-if="getFieldMode(getMatcherField(rule)) === 'time'">
            <div v-if="rule.matcher.op === 'between'" class="time-range">
              <template v-for="rangeIndex in [0, 1]" :key="rangeIndex">
                <div class="range-point-editor">
                  <span class="mini-label">{{ rangeIndex === 0 ? '开始' : '结束' }}</span>

                  <el-select
                    size="small"
                    :model-value="getTimePointEditorValue(rule, rangeIndex)"
                    class="w-140"
                    @update:model-value="value => updateTimePointEditorValue(getActualRuleIndex(visibleIndex), value, rangeIndex)"
                  >
                    <el-option
                      v-for="option in timePointEditorOptions"
                      :key="option.value"
                      :label="option.label"
                      :value="option.value"
                    />
                  </el-select>

                  <el-date-picker
                    size="small"
                    v-if="getTimePointEditorValue(rule, rangeIndex) === 'absolute'"
                    :model-value="getTimeExprAbsoluteValue(rule, rangeIndex)"
                    type="datetime"
                    :placeholder="rangeIndex === 0 ? '开始时间' : '结束时间'"
                    value-format="x"
                    class="w-190"
                    @update:model-value="value => updateTimeExprAbsoluteValue(getActualRuleIndex(visibleIndex), value, rangeIndex)"
                  />

                  <template v-else-if="getTimePointEditorValue(rule, rangeIndex) === 'custom_relative'">
                    <el-select
                      size="small"
                      :model-value="getTimeExprUnit(rule, rangeIndex)"
                      class="w-80"
                      @update:model-value="value => updateTimeExprUnit(getActualRuleIndex(visibleIndex), value, rangeIndex)"
                    >
                      <el-option label="日" value="day" />
                      <el-option label="周" value="week" />
                      <el-option label="月" value="month" />
                    </el-select>

                    <el-input-number
                      size="small"
                      :model-value="getTimeExprOffset(rule, rangeIndex)"
                      controls-position="right"
                      class="w-100"
                      @update:model-value="value => updateTimeExprOffset(getActualRuleIndex(visibleIndex), value, rangeIndex)"
                    />

                    <el-select
                      size="small"
                      :model-value="getTimeExprBoundary(rule, rangeIndex)"
                      class="w-90"
                      @update:model-value="value => updateTimeExprBoundary(getActualRuleIndex(visibleIndex), value, rangeIndex)"
                    >
                      <el-option label="起点" value="start" />
                      <el-option label="终点" value="end" />
                    </el-select>
                  </template>
                </div>

                <span v-if="rangeIndex === 0" class="range-separator">至</span>
              </template>
            </div>

            <div v-else class="time-point-group">
              <el-select
                size="small"
                :model-value="getTimePointEditorValue(rule)"
                class="w-150"
                @update:model-value="value => updateTimePointEditorValue(getActualRuleIndex(visibleIndex), value)"
              >
                <el-option
                  v-for="option in timePointEditorOptions"
                  :key="option.value"
                  :label="option.label"
                  :value="option.value"
                />
              </el-select>

              <el-date-picker
                size="small"
                v-if="getTimePointEditorValue(rule) === 'absolute'"
                :model-value="getTimeExprAbsoluteValue(rule)"
                type="datetime"
                placeholder="选择时间"
                value-format="x"
                class="w-200"
                @update:model-value="value => updateTimeExprAbsoluteValue(getActualRuleIndex(visibleIndex), value)"
              />

              <template v-else-if="getTimePointEditorValue(rule) === 'custom_relative'">
                <el-select
                  size="small"
                  :model-value="getTimeExprUnit(rule)"
                  class="w-80"
                  @update:model-value="value => updateTimeExprUnit(getActualRuleIndex(visibleIndex), value)"
                >
                  <el-option label="日" value="day" />
                  <el-option label="周" value="week" />
                  <el-option label="月" value="month" />
                </el-select>

                <el-input-number
                  size="small"
                  :model-value="getTimeExprOffset(rule)"
                  controls-position="right"
                  class="w-100"
                  @update:model-value="value => updateTimeExprOffset(getActualRuleIndex(visibleIndex), value)"
                />

                <el-select
                  size="small"
                  :model-value="getTimeExprBoundary(rule)"
                  class="w-90"
                  @update:model-value="value => updateTimeExprBoundary(getActualRuleIndex(visibleIndex), value)"
                >
                  <el-option label="起点" value="start" />
                  <el-option label="终点" value="end" />
                </el-select>
              </template>
            </div>
          </template>

          <template v-else-if="getFieldMode(getMatcherField(rule)) === 'number'">
            <div v-if="rule.matcher.op === 'between'" class="number-range">
              <el-input-number
                size="small"
                :model-value="getRangeValue(rule.matcher.values, 0)"
                controls-position="right"
                class="w-130"
                @update:model-value="value => updateNumberRange(getActualRuleIndex(visibleIndex), 0, value)"
              />
              <span class="range-separator">至</span>
              <el-input-number
                size="small"
                :model-value="getRangeValue(rule.matcher.values, 1)"
                controls-position="right"
                class="w-130"
                @update:model-value="value => updateNumberRange(getActualRuleIndex(visibleIndex), 1, value)"
              />
            </div>
            <el-input-number
              size="small"
              v-else
              :model-value="Number(rule.matcher.value ?? 0)"
              controls-position="right"
              class="w-150"
              @update:model-value="value => patchRule(getActualRuleIndex(visibleIndex), draft => { draft.matcher.value = value ?? 0; draft.matcher.values = []; })"
            />
          </template>
        </template>

        <div class="row-actions">
          <el-button size="small" text :icon="ArrowUp" :disabled="visibleIndex === 0" @click="moveRule(getActualRuleIndex(visibleIndex), -1)" />
          <el-button size="small" text :icon="ArrowDown" :disabled="visibleIndex === visibleRules.length - 1" @click="moveRule(getActualRuleIndex(visibleIndex), 1)" />
          <el-button size="small" text type="danger" :icon="Delete" @click="removeRule(getActualRuleIndex(visibleIndex))" />
        </div>
      </div>

      <div class="add-row">
        <el-button size="small" type="primary" plain :icon="Plus" @click="addRule">添加</el-button>
        <el-button size="small" type="primary" :loading="loading" @click="$emit('apply')">{{ applyText }}</el-button>
        <el-button size="small" @click="$emit('reset')">{{ resetText }}</el-button>
      </div>
    </div>

    <div v-else class="empty-rules">
      {{ emptyTextValue }}
      <div class="add-row is-empty">
        <el-button size="small" type="primary" plain :icon="Plus" @click="addRule">添加</el-button>
        <el-button size="small" type="primary" :loading="loading" @click="$emit('apply')">{{ applyText }}</el-button>
        <el-button size="small" @click="$emit('reset')">{{ resetText }}</el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { ArrowDown, ArrowUp, Delete, Plus } from '@element-plus/icons-vue';
import {
  cloneNoteProgramChannel,
  createAbsoluteTimePoint,
  createEmptyNoteProgramChannel,
  createNoteProgramRule,
  createRelativeTimePoint,
  normalizeNoteProgramChannel,
  type NoteProgramMatcher,
  type NoteProgramChannel,
  type NoteProgramRule,
  type NoteTimePointBoundary,
  type NoteTimePointExpr,
  type NoteTimePointUnit
} from '@/api/notes';
import { getOrderedNodeStatuses, getOrderedNodeTypes } from '@/utils/nodeConfig';

type RuleTemplateValue =
  | 'all'
  | 'title_match'
  | 'id'
  | 'node_type'
  | 'node_status'
  | 'start_at'
  | 'updated_at'
  | 'weight'
  | 'private_level';

type RelativePresetValue =
  | 'today'
  | 'yesterday'
  | 'tomorrow'
  | 'this_week'
  | 'last_week'
  | 'next_week'
  | 'this_month'
  | 'last_month'
  | 'next_month'
  | 'custom_relative';

type TimePointEditorValue = 'absolute' | RelativePresetValue;

const props = withDefaults(defineProps<{
  modelValue?: NoteProgramChannel | null;
  title?: string;
  caption?: string;
  helpText?: string;
  hintText?: string;
  emptyText?: string;
  hiddenLeadingRuleCount?: number;
  applyText?: string;
  resetText?: string;
  loading?: boolean;
}>(), {
  modelValue: null,
  title: '数据筛选',
  caption: '',
  helpText: '',
  hintText: '规则按顺序执行，后面的包含/排除可以覆盖前面的结果。',
  emptyText: '',
  hiddenLeadingRuleCount: 0,
  applyText: '应用程序',
  resetText: '恢复默认',
  loading: false
});

const emit = defineEmits<{
  (e: 'update:modelValue', value: NoteProgramChannel): void;
  (e: 'apply'): void;
  (e: 'reset'): void;
}>();

const channelValue = computed(() => normalizeNoteProgramChannel(props.modelValue ?? createEmptyNoteProgramChannel(false)));
const visibleRuleStartIndex = computed(() => Math.min(Math.max(0, props.hiddenLeadingRuleCount), channelValue.value.rules.length));
const visibleRules = computed(() => channelValue.value.rules.slice(visibleRuleStartIndex.value));
const emptyTextValue = computed(() => props.emptyText || (props.hiddenLeadingRuleCount > 0
  ? '当前没有附加规则，当前视图会显示全部已加载结果。'
  : '当前没有附加规则，筛选链会从空结果开始。'));
const orderedNodeTypes = computed(() => getOrderedNodeTypes());
const orderedNodeStatuses = computed(() => getOrderedNodeStatuses());
const getActualRuleIndex = (visibleIndex: number) => visibleRuleStartIndex.value + visibleIndex;

const ruleTemplates: Array<{ value: RuleTemplateValue; label: string }> = [
  { value: 'title_match', label: '标题匹配' },
  { value: 'node_type', label: '类型匹配' },
  { value: 'node_status', label: '状态匹配' },
  { value: 'private_level', label: '私密值' },
  { value: 'start_at', label: '起始时间' },
  { value: 'updated_at', label: '更新时间' },
  { value: 'weight', label: '权重比较' },
  { value: 'id', label: '指定节点 ID' },
  { value: 'all', label: '全部节点' }
];

const relativePresetOptions: Array<{ value: RelativePresetValue; label: string }> = [
  { value: 'today', label: '今天' },
  { value: 'yesterday', label: '昨天' },
  { value: 'tomorrow', label: '明天' },
  { value: 'this_week', label: '本周' },
  { value: 'last_week', label: '上周' },
  { value: 'next_week', label: '下周' },
  { value: 'this_month', label: '本月' },
  { value: 'last_month', label: '上月' },
  { value: 'next_month', label: '下月' },
  { value: 'custom_relative', label: '自定义相对时间' }
];

const timePointEditorOptions: Array<{ value: TimePointEditorValue; label: string }> = [
  { value: 'absolute', label: '绝对时间' },
  ...relativePresetOptions
];

const emitChannel = (channel: NoteProgramChannel) => {
  channel.default = false;
  emit('update:modelValue', normalizeNoteProgramChannel(channel));
};

const updateChannel = (mutator: (draft: NoteProgramChannel) => void) => {
  const draft = cloneNoteProgramChannel(channelValue.value);
  draft.default = false;
  mutator(draft);
  emitChannel(draft);
};

const createRuleFromTemplate = (template: RuleTemplateValue): NoteProgramRule => {
  switch (template) {
    case 'all':
      return createNoteProgramRule('include', 'all');
    case 'title_match':
      return {
        action: 'include',
        matcher: {
          kind: 'field',
          field: 'title',
          op: 'eq',
          value: ''
        }
      };
    case 'id':
      return {
        action: 'include',
        matcher: {
          kind: 'id',
          ids: []
        }
      };
    case 'node_type':
      return {
        action: 'include',
        matcher: {
          kind: 'field',
          field: 'node_type',
          op: 'eq',
          value: ''
        }
      };
    case 'node_status':
      return {
        action: 'include',
        matcher: {
          kind: 'field',
          field: 'node_status',
          op: 'eq',
          value: ''
        }
      };
    case 'start_at':
      return {
        action: 'include',
        matcher: {
          kind: 'field',
          field: 'start_at',
          op: 'between',
          time_values: [createAbsoluteTimePoint(), createAbsoluteTimePoint()]
        }
      };
    case 'updated_at':
      return {
        action: 'include',
        matcher: {
          kind: 'field',
          field: 'updated_at',
          op: 'between',
          time_values: [createAbsoluteTimePoint(), createAbsoluteTimePoint()]
        }
      };
    case 'private_level':
      return {
        action: 'include',
        matcher: {
          kind: 'field',
          field: 'private_level',
          op: 'gte',
          value: 1
        }
      };
    case 'weight':
      return {
        action: 'include',
        matcher: {
          kind: 'field',
          field: 'weight',
          op: 'gte',
          value: 100
        }
      };
  }
};

const getRuleTemplateValue = (rule: NoteProgramRule): RuleTemplateValue => {
  if (rule.matcher.kind === 'title_contains') return 'title_match';
  if (rule.matcher.kind === 'id') return 'id';
  if (rule.matcher.kind === 'all') return 'all';
  if (rule.matcher.kind === 'field') {
    if (rule.matcher.field === 'title') return 'title_match';
    if (rule.matcher.field === 'node_type') return 'node_type';
    if (rule.matcher.field === 'node_status') return 'node_status';
    if (rule.matcher.field === 'start_at') return 'start_at';
    if (rule.matcher.field === 'updated_at') return 'updated_at';
    if (rule.matcher.field === 'private_level') return 'private_level';
    if (rule.matcher.field === 'weight') return 'weight';
  }
  return 'title_match';
};

const addRule = () => {
  updateChannel(draft => {
    draft.rules.push(createRuleFromTemplate('title_match'));
  });
};

const replaceRuleTemplate = (index: number, template: RuleTemplateValue) => {
  updateChannel(draft => {
    const previousRule = draft.rules[index];
    const nextRule = createRuleFromTemplate(template);
    nextRule.action = previousRule?.action ?? nextRule.action;
    draft.rules[index] = nextRule;
  });
};

const patchRule = (index: number, updater: (rule: NoteProgramRule) => void) => {
  updateChannel(draft => {
    updater(draft.rules[index]);
  });
};

const moveRule = (index: number, delta: number) => {
  updateChannel(draft => {
    const nextIndex = index + delta;
    if (nextIndex < 0 || nextIndex >= draft.rules.length) return;
    const [rule] = draft.rules.splice(index, 1);
    draft.rules.splice(nextIndex, 0, rule);
  });
};

const removeRule = (index: number) => {
  updateChannel(draft => {
    draft.rules.splice(index, 1);
  });
};

const getMatcherField = (rule: NoteProgramRule) => rule.matcher.field || 'node_status';

const getFieldMode = (field: string) => {
  if (field === 'title') return 'text';
  if (field === 'start_at' || field === 'updated_at') return 'time';
  if (field === 'weight' || field === 'private_level') return 'number';
  return 'enum';
};

const getAllowedOps = (field: string) => {
  if (field === 'title') {
    return [
      { value: 'eq', label: '等于' },
      { value: 'contains', label: '包含' },
      { value: 'not_contains', label: '不包含' },
      { value: 'regex_search', label: '正则search' }
    ];
  }
  if (field === 'start_at' || field === 'updated_at') {
    return [
      { value: 'between', label: '介于' },
      { value: 'gte', label: '不早于' },
      { value: 'lte', label: '不晚于' }
    ];
  }
  if (field === 'weight') {
    return [
      { value: 'gte', label: '>=' },
      { value: 'lte', label: '<=' },
      { value: 'eq', label: '=' },
      { value: 'between', label: '介于' }
    ];
  }
  if (field === 'private_level') {
    return [
      { value: 'gte', label: '>=' },
      { value: 'lte', label: '<=' },
      { value: 'eq', label: '=' },
      { value: 'between', label: '介于' }
    ];
  }
  return [
    { value: 'eq', label: '等于' },
    { value: 'neq', label: '不等于' }
  ];
};

const getDefaultOp = (field: string) => getAllowedOps(field)[0].value;
const getTextFieldPlaceholder = (field: string) => field === 'title' ? '输入标题文本或正则模式' : '输入文本';

const getLegacyTimeValue = (rawValue: unknown) => {
  if (typeof rawValue === 'number' && Number.isFinite(rawValue)) return rawValue;
  if (typeof rawValue === 'string') {
    const parsed = Number(rawValue);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
};

const getImplicitBoundary = (rule: NoteProgramRule, rangeIndex?: number): NoteTimePointBoundary => {
  if (rule.matcher.op === 'between') {
    return rangeIndex === 1 ? 'end' : 'start';
  }
  return rule.matcher.op === 'lte' ? 'end' : 'start';
};

const buildRelativePresetExpr = (
  preset: Exclude<RelativePresetValue, 'custom_relative'>,
  boundary: NoteTimePointBoundary
): NoteTimePointExpr => {
  switch (preset) {
    case 'today':
      return createRelativeTimePoint('day', 0, boundary);
    case 'yesterday':
      return createRelativeTimePoint('day', -1, boundary);
    case 'tomorrow':
      return createRelativeTimePoint('day', 1, boundary);
    case 'this_week':
      return createRelativeTimePoint('week', 0, boundary);
    case 'last_week':
      return createRelativeTimePoint('week', -1, boundary);
    case 'next_week':
      return createRelativeTimePoint('week', 1, boundary);
    case 'this_month':
      return createRelativeTimePoint('month', 0, boundary);
    case 'last_month':
      return createRelativeTimePoint('month', -1, boundary);
    case 'next_month':
      return createRelativeTimePoint('month', 1, boundary);
  }
};

const inferRelativePreset = (
  expr: NoteTimePointExpr,
  boundary: NoteTimePointBoundary
): RelativePresetValue => {
  if (expr.kind !== 'relative' || expr.boundary !== boundary) return 'custom_relative';

  const options: Array<[Exclude<RelativePresetValue, 'custom_relative'>, NoteTimePointUnit, number]> = [
    ['today', 'day', 0],
    ['yesterday', 'day', -1],
    ['tomorrow', 'day', 1],
    ['this_week', 'week', 0],
    ['last_week', 'week', -1],
    ['next_week', 'week', 1],
    ['this_month', 'month', 0],
    ['last_month', 'month', -1],
    ['next_month', 'month', 1]
  ];

  for (const [value, unit, offset] of options) {
    if (expr.unit === unit && (expr.offset || 0) === offset) return value;
  }

  return 'custom_relative';
};

const getTimeExpr = (rule: NoteProgramRule, rangeIndex?: number): NoteTimePointExpr => {
  if (rule.matcher.op === 'between') {
    const expr = typeof rangeIndex === 'number' ? rule.matcher.time_values?.[rangeIndex] : null;
    if (expr) return expr;
    const legacyValue = typeof rangeIndex === 'number' ? getLegacyTimeValue(rule.matcher.values?.[rangeIndex]) : null;
    return createAbsoluteTimePoint(legacyValue);
  }

  if (rule.matcher.time_value) return rule.matcher.time_value;
  return createAbsoluteTimePoint(getLegacyTimeValue(rule.matcher.value));
};

const patchTimeExpr = (
  index: number,
  updater: (expr: NoteTimePointExpr) => NoteTimePointExpr,
  rangeIndex?: number
) => {
  patchRule(index, draft => {
    if (draft.matcher.op === 'between') {
      const values = Array.isArray(draft.matcher.time_values) ? [...draft.matcher.time_values] : [];
      const current = values[rangeIndex ?? 0] ?? createAbsoluteTimePoint();
      values[rangeIndex ?? 0] = updater({ ...current });
      draft.matcher.time_values = values;
      draft.matcher.time_value = null;
    } else {
      draft.matcher.time_value = updater({ ...(draft.matcher.time_value ?? createAbsoluteTimePoint()) });
      draft.matcher.time_values = [];
    }
    draft.matcher.value = undefined;
    draft.matcher.values = [];
  });
};

const updateFieldOp = (index: number, op: string) => {
  patchRule(index, draft => {
    draft.matcher.op = op as NoteProgramMatcher['op'];

    if (draft.matcher.field === 'start_at' || draft.matcher.field === 'updated_at') {
      if (op === 'between') {
        const startExpr = draft.matcher.time_value ?? createRelativeTimePoint('month', 0, 'start');
        const endExpr = draft.matcher.time_values?.[1] ?? createRelativeTimePoint('month', 0, 'end');
        draft.matcher.time_values = [startExpr, endExpr];
        draft.matcher.time_value = null;
      } else {
        draft.matcher.time_value = draft.matcher.time_values?.[0] ?? draft.matcher.time_value ?? createAbsoluteTimePoint();
        draft.matcher.time_values = [];
      }
      draft.matcher.value = undefined;
      draft.matcher.values = [];
      return;
    }

    if (op === 'between') {
      const fallbackValue = draft.matcher.field === 'weight'
        ? 100
        : draft.matcher.field === 'private_level'
          ? 1
          : 0;
      const currentValue = typeof draft.matcher.value === 'number' && Number.isFinite(draft.matcher.value)
        ? draft.matcher.value
        : fallbackValue;
      draft.matcher.value = undefined;
      draft.matcher.values = [currentValue, currentValue];
      return;
    }

    if (draft.matcher.field === 'weight' || draft.matcher.field === 'private_level') {
      const fallbackValue = draft.matcher.field === 'weight' ? 100 : 1;
      draft.matcher.value = typeof draft.matcher.value === 'number' && Number.isFinite(draft.matcher.value)
        ? draft.matcher.value
        : (getRangeValue(draft.matcher.values, 0) ?? fallbackValue);
      draft.matcher.values = [];
      return;
    }

    draft.matcher.value = typeof draft.matcher.value === 'string' ? draft.matcher.value : '';
    draft.matcher.values = [];
  });
};

const getFieldEnumOptions = (field: string) => {
  if (field === 'node_type') {
    return orderedNodeTypes.value.map(item => ({ value: item.id, label: item.label }));
  }
  return orderedNodeStatuses.value.map(item => ({ value: item.id, label: item.label }));
};

const formatIdInput = (rule: NoteProgramRule) => (rule.matcher.ids || []).join(', ');

const updateIdRule = (index: number, rawValue: string) => {
  patchRule(index, draft => {
    draft.matcher.ids = rawValue
      .split(/[\s,，]+/)
      .map(item => item.trim())
      .filter(Boolean);
  });
};

const getRangeValue = (values: any[] | undefined, index: number) => {
  if (!Array.isArray(values)) return undefined;
  const value = values[index];
  if (value === undefined || value === null || value === '') return undefined;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : undefined;
};

const updateNumberRange = (index: number, rangeIndex: number, value: number | undefined) => {
  patchRule(index, draft => {
    const values = Array.isArray(draft.matcher.values) ? [...draft.matcher.values] : [];
    values[rangeIndex] = value ?? 0;
    draft.matcher.values = values;
    draft.matcher.value = undefined;
  });
};

const getTimeExprUnit = (rule: NoteProgramRule, rangeIndex?: number): NoteTimePointUnit => getTimeExpr(rule, rangeIndex).unit || 'month';
const getTimeExprOffset = (rule: NoteProgramRule, rangeIndex?: number) => getTimeExpr(rule, rangeIndex).offset || 0;
const getTimeExprBoundary = (rule: NoteProgramRule, rangeIndex?: number): NoteTimePointBoundary => getTimeExpr(rule, rangeIndex).boundary || 'start';

const getTimeExprAbsoluteValue = (rule: NoteProgramRule, rangeIndex?: number) => {
  const expr = getTimeExpr(rule, rangeIndex);
  if (expr.kind !== 'absolute') return null;
  return typeof expr.value === 'number' && Number.isFinite(expr.value) ? expr.value : null;
};

const getTimePointEditorValue = (rule: NoteProgramRule, rangeIndex?: number): TimePointEditorValue => {
  const expr = getTimeExpr(rule, rangeIndex);
  if (expr.kind === 'absolute') return 'absolute';
  return inferRelativePreset(expr, getImplicitBoundary(rule, rangeIndex));
};

const updateTimePointEditorValue = (index: number, value: string, rangeIndex?: number) => {
  if (value === 'absolute') {
    patchTimeExpr(index, () => createAbsoluteTimePoint(), rangeIndex);
    return;
  }

  const boundary = getImplicitBoundary(channelValue.value.rules[index], rangeIndex);
  if (value === 'custom_relative') {
    patchTimeExpr(index, current => current.kind === 'relative' ? current : createRelativeTimePoint('month', 0, boundary), rangeIndex);
    return;
  }

  patchTimeExpr(index, () => buildRelativePresetExpr(value as Exclude<RelativePresetValue, 'custom_relative'>, boundary), rangeIndex);
};

const updateTimeExprAbsoluteValue = (index: number, value: unknown, rangeIndex?: number) => {
  patchTimeExpr(index, current => {
    const numericValue = typeof value === 'string' ? Number(value) : value;
    return {
      ...current,
      kind: 'absolute',
      value: typeof numericValue === 'number' && Number.isFinite(numericValue) ? numericValue : null
    };
  }, rangeIndex);
};

const updateTimeExprUnit = (index: number, value: string, rangeIndex?: number) => {
  patchTimeExpr(index, current => ({
    ...current,
    kind: 'relative',
    unit: value === 'day' || value === 'week' || value === 'month' ? value : 'month'
  }), rangeIndex);
};

const updateTimeExprOffset = (index: number, value: number | undefined, rangeIndex?: number) => {
  patchTimeExpr(index, current => ({
    ...current,
    kind: 'relative',
    offset: typeof value === 'number' && Number.isFinite(value) ? value : 0
  }), rangeIndex);
};

const updateTimeExprBoundary = (index: number, value: string, rangeIndex?: number) => {
  patchTimeExpr(index, current => ({
    ...current,
    kind: 'relative',
    boundary: value === 'end' ? 'end' : 'start'
  }), rangeIndex);
};
</script>

<style scoped>
.program-bar {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 14px;
  border: 1px solid #e4e7ed;
  border-radius: 10px;
  background: linear-gradient(180deg, #fbfcfe 0%, #f5f7fa 100%);
}

.program-header {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
}

.program-meta {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.program-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.program-title {
  font-size: 13px;
  font-weight: 600;
  color: #303133;
}

.program-inline-help {
  font-size: 11px;
  line-height: 1.5;
  color: #909399;
}

.program-caption {
  font-size: 11px;
  color: #606266;
  line-height: 1.5;
}

.program-hint {
  font-size: 11px;
  color: #909399;
}

.rule-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.rule-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: nowrap;
  padding: 8px 10px;
  border: 1px solid #dcdfe6;
  border-radius: 10px;
  background: #fff;
  overflow-x: auto;
}

.row-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  margin-left: auto;
  flex-shrink: 0;
}

.time-range,
.number-range,
.time-point-group {
  display: flex;
  align-items: center;
  gap: 8px;
}

.range-point-editor {
  display: flex;
  align-items: center;
  gap: 8px;
}

.mini-label {
  font-size: 11px;
  color: #909399;
  white-space: nowrap;
}

.range-separator {
  font-size: 11px;
  color: #909399;
  white-space: nowrap;
}

.empty-rules {
  padding: 12px;
  border: 1px dashed #dcdfe6;
  border-radius: 10px;
  background: #fff;
  color: #909399;
  font-size: 12px;
}

.add-row {
  display: flex;
  justify-content: flex-start;
  align-items: center;
  gap: 8px;
  padding-top: 2px;
}

.add-row.is-empty {
  padding-top: 12px;
}

.program-bar :deep(.el-select__wrapper),
.program-bar :deep(.el-input__wrapper),
.program-bar :deep(.el-textarea__wrapper),
.program-bar :deep(.el-input-number .el-input__wrapper) {
  min-height: 28px;
}

.program-bar :deep(.el-select__selected-item),
.program-bar :deep(.el-input__inner),
.program-bar :deep(.el-input-number .el-input__inner),
.program-bar :deep(.el-date-editor .el-range-input),
.program-bar :deep(.el-date-editor .el-range-separator) {
  font-size: 12px;
}

.w-80 { width: 80px; }
.w-90 { width: 90px; }
.w-100 { width: 100px; }
.w-110 { width: 110px; }
.w-130 { width: 130px; }
.w-140 { width: 140px; }
.w-150 { width: 150px; }
.w-190 { width: 190px; }
.w-200 { width: 200px; }
.w-280 { width: 280px; }
.w-320 { width: 320px; }
</style>
