<script setup lang="ts">
import { EditPen } from '@element-plus/icons-vue';
import { computed, nextTick, ref } from 'vue';
import SortableOrderHandle from '@/components/SortableOrderHandle.vue';
import { useSortableList } from '@/utils/useSortableList';

interface RequirementRowState {
  requirement: {
    id: string;
    text: string;
    effectText: string;
    effectDetail: string;
  };
  triggered: boolean;
  invalid: boolean;
  pending: boolean;
  progressText: string;
}

const props = defineProps<{
  states: RequirementRowState[];
}>();

const emit = defineEmits<{
  (e: 'update-text', requirementId: string, text: string): void;
  (e: 'update-effect-text', requirementId: string, text: string): void;
  (e: 'update-effect-detail', requirementId: string, text: string): void;
  (e: 'remove', requirementId: string): void;
  (e: 'reorder', payload: { oldIndex: number; newIndex: number }): void;
}>();

const requirementListRef = ref<HTMLElement | null>(null);
const editingDetailIds = ref<Record<string, true>>({});
const detailInputRefs = new Map<string, HTMLTextAreaElement | null>();
let measureDisplayTextWidthCanvas: HTMLCanvasElement | null = null;
let resolvedMeasureFont = '';

function setDetailInputRef(requirementId: string, element: unknown) {
  const textarea = element && typeof element === 'object' && '$el' in element
    ? (element as { $el?: HTMLElement | null }).$el?.querySelector('textarea') ?? null
    : null;
  detailInputRefs.set(requirementId, textarea);
}

function isDetailEditing(requirementId: string) {
  return Boolean(editingDetailIds.value[requirementId]);
}

function openDetailEditor(requirementId: string) {
  editingDetailIds.value[requirementId] = true;
  void nextTick(() => {
    detailInputRefs.get(requirementId)?.focus();
  });
}

function closeDetailEditor(requirementId: string) {
  if (!editingDetailIds.value[requirementId]) return;
  delete editingDetailIds.value[requirementId];
}

function estimateDisplayUnits(text: string) {
  return [...text].reduce((sum, char) => {
    if (/[\u0000-\u00ff]/.test(char)) return sum + 0.6;
    return sum + 1;
  }, 0);
}

function getMeasureFont(sizePx = 14, weight = 400) {
  if (resolvedMeasureFont) {
    return `${weight} ${sizePx}px ${resolvedMeasureFont}`;
  }

  if (typeof window === 'undefined' || typeof document === 'undefined') {
    resolvedMeasureFont = 'sans-serif';
    return `${weight} ${sizePx}px ${resolvedMeasureFont}`;
  }

  const rootStyle = window.getComputedStyle(document.documentElement);
  const bodyStyle = window.getComputedStyle(document.body);
  const family =
    rootStyle.getPropertyValue('--el-font-family').trim()
    || bodyStyle.fontFamily.trim()
    || 'sans-serif';
  resolvedMeasureFont = family;
  return `${weight} ${sizePx}px ${family}`;
}

function measureDisplayTextWidth(text: string, font = getMeasureFont()) {
  const normalized = String(text || '');
  if (typeof document === 'undefined') {
    return estimateDisplayUnits(normalized) * 11;
  }

  const canvas = measureDisplayTextWidthCanvas ??= document.createElement('canvas');
  const context = canvas.getContext('2d');
  if (!context) {
    return estimateDisplayUnits(normalized) * 11;
  }

  context.font = font;
  return context.measureText(normalized).width;
}

const listStyle = computed(() => {
  const textSamples = props.states.map(state => state.requirement.text.trim() || '一条触发条件');
  const effectSamples = props.states.map(state => state.requirement.effectText.trim() || '词条效果');
  const progressSamples = props.states.map(state => state.progressText || '0/0');
  const maxTextWidth = Math.max(
    measureDisplayTextWidth('一条触发条件'),
    ...textSamples.map(text => measureDisplayTextWidth(text)),
  );
  const maxEffectWidth = Math.max(
    measureDisplayTextWidth('词条效果'),
    ...effectSamples.map(text => measureDisplayTextWidth(text)),
  );
  const maxProgressWidth = Math.max(
    measureDisplayTextWidth('0/0', getMeasureFont(12)),
    ...progressSamples.map(text => measureDisplayTextWidth(text, getMeasureFont(12))),
  );
  return {
    '--req-text-width': `${Math.max(156, Math.min(380, Math.ceil(maxTextWidth + 54)))}px`,
    '--req-effect-width': `${Math.max(120, Math.min(252, Math.ceil(maxEffectWidth + 44)))}px`,
    '--req-progress-width': `${Math.max(56, Math.min(148, Math.ceil(maxProgressWidth + 14)))}px`,
  };
});

useSortableList({
  listRef: requirementListRef,
  getDeps: () => [
    props.states.length,
    props.states.map(state => state.requirement.id).join('|'),
  ] as const,
  isEnabled: () => props.states.length > 1,
  ghostClass: 'formation-requirement-ghost',
  onReorder: (oldIndex, newIndex) => emit('reorder', { oldIndex, newIndex }),
});
</script>

<template>
  <div v-if="states.length" ref="requirementListRef" class="requirement-list" :style="listStyle">
    <div
      v-for="(state, index) in states"
      :key="state.requirement.id"
      class="requirement-row"
      :class="{
        triggered: state.triggered,
        invalid: state.invalid,
        pending: state.pending,
      }"
    >
      <SortableOrderHandle :index="index" :total="states.length" size="sm" />

      <el-input
        :model-value="state.requirement.text"
        class="req-text"
        size="small"
        clearable
        placeholder="一行一条，例如：入阵1个仙品白玉棋石"
        @update:model-value="value => emit('update-text', state.requirement.id, String(value ?? ''))"
      />

      <el-input
        :model-value="state.requirement.effectText"
        class="req-effect"
        size="small"
        clearable
        placeholder="词条效果"
        @update:model-value="value => emit('update-effect-text', state.requirement.id, String(value ?? ''))"
      />

      <div class="req-progress">{{ state.progressText }}</div>

      <el-button
        size="small"
        type="danger"
        text
        class="req-remove-button"
        title="删除条件"
        aria-label="删除条件"
        @click="emit('remove', state.requirement.id)"
      >
        -
      </el-button>

      <el-button
        size="small"
        text
        class="req-edit-button"
        :icon="EditPen"
        title="编辑词条说明"
        aria-label="编辑词条说明"
        @click="openDetailEditor(state.requirement.id)"
      />

      <div
        v-if="!isDetailEditing(state.requirement.id) && state.requirement.effectDetail"
        class="req-effect-detail-display"
      >
        {{ state.requirement.effectDetail }}
      </div>

      <el-input
        v-else-if="isDetailEditing(state.requirement.id)"
        :ref="element => setDetailInputRef(state.requirement.id, element)"
        type="textarea"
        :model-value="state.requirement.effectDetail"
        class="req-effect-detail-input"
        resize="none"
        :autosize="{ minRows: 1 }"
        placeholder="词条说明，支持换行"
        @update:model-value="value => emit('update-effect-detail', state.requirement.id, String(value ?? ''))"
        @blur="closeDetailEditor(state.requirement.id)"
      />
    </div>
  </div>

  <div v-else class="rule-empty">
    这张卡片还没有触发条件，点“添加条件”开始配。
  </div>
</template>

<style scoped>
.requirement-list {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  overflow-x: auto;
}

.requirement-row {
  display: grid;
  align-items: center;
  gap: 6px;
  grid-template-columns:
    22px
    var(--req-text-width, 320px)
    var(--req-effect-width, 160px)
    var(--req-progress-width, 72px)
    20px
    20px;
  width: max-content;
  max-width: 100%;
  padding: 0;
}

.req-effect-detail-display,
.req-effect-detail-input {
  grid-column: 2 / 6;
  margin-top: -2px;
}

.req-effect-detail-display {
  min-height: 24px;
  padding: 4px 2px 0 10px;
  font-size: 12px;
  line-height: 1.45;
  color: #6f7f99;
  white-space: pre-wrap;
}

.req-effect-detail-input :deep(.el-textarea__inner) {
  min-height: 28px !important;
  padding: 5px 9px;
  font-size: 12px;
  line-height: 1.45;
  color: #6f7f99;
  overflow: hidden;
}

.requirement-row :deep(.el-input) {
  width: auto;
}

.requirement-row :deep(.el-input__wrapper) {
  min-height: 28px;
}

.req-text {
  width: var(--req-text-width, 320px);
}

.req-effect {
  width: var(--req-effect-width, 160px);
}

.requirement-row.triggered .req-text :deep(.el-input__wrapper) {
  border-color: #c9defd;
  background: #edf5ff;
}

.requirement-row.invalid .req-text :deep(.el-input__wrapper) {
  border-color: #f3b3b3;
  background: #fff2f2;
}

.req-progress {
  font-size: 12px;
  color: #7a879d;
  text-align: right;
  white-space: nowrap;
}

.requirement-row > .el-button {
  justify-self: end;
}

.req-edit-button,
.req-remove-button {
  min-width: 20px;
  width: 20px;
  height: 20px;
  padding: 0;
}

.req-remove-button {
  font-size: 16px;
  line-height: 1;
  font-weight: 600;
}

.req-edit-button {
  font-size: 13px;
  color: #5c7cbe;
}

.rule-empty {
  padding: 12px 10px;
  border-radius: 12px;
  border: 1px dashed #d7e2f2;
  font-size: 13px;
  color: #7a879d;
}

:deep(.formation-requirement-ghost) {
  opacity: 0.75;
}

@media (max-width: 760px) {
  .requirement-row {
    width: 100%;
    grid-template-columns: 22px 1fr 20px 20px;
  }

  .req-effect,
  .req-progress,
  .requirement-row > .el-button {
    grid-row: 2;
  }

  .req-effect-detail-input {
    grid-column: 2 / 5;
    grid-row: 4;
  }

  .req-effect-detail-display {
    grid-column: 2 / 5;
    grid-row: 4;
  }

  .req-effect {
    grid-column: 2;
  }

  .req-progress {
    grid-column: 2;
    justify-self: start;
    text-align: left;
  }

  .req-progress {
    grid-row: 3;
  }

  .requirement-row > .el-button {
    grid-column: 4;
    grid-row: 2 / 5;
  }

  .req-edit-button {
    grid-column: 3;
    grid-row: 2 / 5;
  }
}
</style>
