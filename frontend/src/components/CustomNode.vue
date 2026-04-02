<template>
  <div class="custom-note-node" :style="nodeStyle">
    <!-- 四个方向的句柄，每个方向同时提供 source 和 target，并分配唯一 ID -->
    <!-- Top -->
    <Handle id="t-t" type="target" :position="Position.Top" />
    <Handle id="t-s" type="source" :position="Position.Top" />
    
    <!-- Bottom -->
    <Handle id="b-t" type="target" :position="Position.Bottom" />
    <Handle id="b-s" type="source" :position="Position.Bottom" />
    
    <!-- Left -->
    <Handle id="l-t" type="target" :position="Position.Left" />
    <Handle id="l-s" type="source" :position="Position.Left" />
    
    <!-- Right -->
    <Handle id="r-t" type="target" :position="Position.Right" />
    <Handle id="r-s" type="source" :position="Position.Right" />

    <div class="node-content">
      <button
        type="button"
        class="node-ai-button nodrag nopan"
        :disabled="data.is_ai_categorizing"
        :title="data.is_ai_categorizing ? 'AI分类中...' : 'AI分类'"
        @click.stop="handleAiClick"
      >
        {{ data.is_ai_categorizing ? '...' : 'AI' }}
      </button>
      <div v-if="useSplitTitle" class="node-title node-title--split" :style="titleStyle">
        <div class="node-title-layer" :style="filledTitleLayerStyle">
          <NoteFormBadge :form="data.note_form" compact />
          <span class="node-title-text">{{ data.title || 'Untitled' }}</span>
        </div>
        <div class="node-title-layer" :style="emptyTitleLayerStyle">
          <NoteFormBadge :form="data.note_form" compact />
          <span class="node-title-text">{{ data.title || 'Untitled' }}</span>
        </div>
      </div>
      <div v-else class="node-title" :style="titleStyle">
        <NoteFormBadge :form="data.note_form" compact />
        <span class="node-title-text">{{ data.title || 'Untitled' }}</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { Handle, Position } from '@vue-flow/core'
import { computed } from 'vue'
import { getNodeDisplayStyle } from '@/utils/nodeConfig'
import { getNoteWeightScaleFactor } from '@/utils/noteWeight'
import NoteFormBadge from './NoteFormBadge.vue'
import { resolveCompletionProgressFillRatio } from '@/utils/noteProgress'

const props = defineProps<{
  data: {
    title: string,
    weight?: number,
    node_type?: string | null,
    note_types?: { key: string; weight: number }[] | null,
    primary_category?: string | null,
    note_categories?: { key: string; weight: number }[] | null,
    note_form?: string | null,
    weight_mode?: string | null,
    node_status?: string | null,
    lifecycle_stage?: string | null,
    color?: string | null,
    custom_fields?: unknown,
    completion_progress_expr?: string | null,
    completion_progress?: number | null,
    is_ai_categorizing?: boolean,
    on_ai_categorize?: (() => void) | null
  }
}>()

const BASE_WIDTH = 150;
const BASE_HEIGHT = 50;

const computedStyle = computed(() => {
    const completionProgress = resolveCompletionProgressFillRatio({
      lifecycleStage: props.data.lifecycle_stage ?? props.data.node_status,
      completionProgress: props.data.completion_progress,
      completionProgressExpr: props.data.completion_progress_expr,
      customFields: props.data.custom_fields,
    });
    return getNodeDisplayStyle(
      props.data.primary_category ?? props.data.node_type,
      props.data.lifecycle_stage ?? props.data.node_status,
      props.data.color,
      props.data.note_categories ?? props.data.note_types,
      completionProgress
    );
});

const nodeStyle = computed(() => {
    const scale = getNoteWeightScaleFactor(props.data.weight, props.data.node_type, props.data.weight_mode);
    
    const style = computedStyle.value;
    
    return {
        width: `${Math.round(BASE_WIDTH * scale)}px`,
        height: `${Math.round(BASE_HEIGHT * scale)}px`,
        borderColor: style.borderColor,
        borderWidth: style.borderWidth,
        borderStyle: style.borderStyle,
        backgroundColor: style.backgroundColor,
        backgroundImage: style.backgroundImage,
        opacity: style.opacity,
    };
});

const titleStyle = computed(() => {
    const scale = getNoteWeightScaleFactor(props.data.weight, props.data.node_type, props.data.weight_mode);
    // Scale font size slightly less aggressively than dimensions
    // Base font 14px, max 24px, min 10px
    const fontSize = Math.min(24, Math.max(10, Math.round(14 * scale)));
    
    const style = computedStyle.value;
    
    return {
        fontSize: `${fontSize}px`,
        color: style.color,
        fontWeight: style.fontWeight,
        textDecoration: style.textDecoration,
    };
});

const useSplitTitle = computed(() => {
    const ratio = computedStyle.value.partialFillRatio;
    return typeof ratio === 'number' && ratio > 0 && ratio < 1;
});

const filledTitleLayerStyle = computed(() => {
    const ratio = computedStyle.value.partialFillRatio ?? 0;
    return {
        color: computedStyle.value.fillTextColor,
        clipPath: `inset(0 ${(100 - ratio * 100).toFixed(2)}% 0 0)`,
    };
});

const emptyTitleLayerStyle = computed(() => {
  const ratio = computedStyle.value.partialFillRatio ?? 0;
  return {
    color: computedStyle.value.emptyTextColor,
    clipPath: `inset(0 0 0 ${(ratio * 100).toFixed(2)}%)`,
  };
});

const handleAiClick = () => {
  if (props.data.is_ai_categorizing) {
    return;
  }
  props.data.on_ai_categorize?.();
};
</script>

<style scoped>
.custom-note-node {
  padding: 0; /* Remove padding to control size exactly via width/height */
  border-radius: 8px;
  background: #fff;
  border: 1px solid #dcdfe6;
  /* min-width removed to allow scaling down */
  text-align: center;
  box-shadow: 0 2px 12px 0 rgba(0, 0, 0, 0.1);
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: border-color 0.2s, box-shadow 0.2s, opacity 0.2s;
}

.node-content {
    width: 100%;
    padding: 0 10px; /* Internal padding */
    box-sizing: border-box;
    overflow: hidden;
    position: relative;
}

.custom-note-node:hover {
  border-color: #409eff;
  box-shadow: 0 4px 16px 0 rgba(0, 0, 0, 0.15);
  z-index: 10; /* Bring to front on hover */
}

.node-title {
  display:flex;
  align-items:center;
  justify-content:center;
  gap:4px;
  font-weight: 500;
  color: #303133;
  pointer-events: none; /* 防止文字干扰拖拽 */
}

.node-title--split {
  display: grid;
  width: 100%;
}

.node-title-layer {
  grid-area: 1 / 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  min-width: 0;
  overflow: hidden;
}

.node-title-text{
  flex: 1;
  min-width:0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.node-ai-button {
  position: absolute;
  top: 4px;
  right: 4px;
  z-index: 2;
  min-width: 28px;
  height: 20px;
  padding: 0 6px;
  border: 1px solid rgba(14, 116, 144, 0.2);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.92);
  color: #0f766e;
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
  cursor: pointer;
  opacity: 0;
  transform: translateY(-2px);
  transition: opacity 0.16s ease, transform 0.16s ease, border-color 0.16s ease;
}

.custom-note-node:hover .node-ai-button,
.node-ai-button:focus-visible {
  opacity: 1;
  transform: translateY(0);
}

.node-ai-button:hover:not(:disabled) {
  border-color: rgba(14, 116, 144, 0.45);
}

.node-ai-button:disabled {
  cursor: wait;
  color: #94a3b8;
}

/* 默认隐藏句柄 */
:deep(.vue-flow__handle) {
  width: 8px;
  height: 8px;
  background: #409eff;
  border: 2px solid #fff;
  opacity: 0;
  transition: opacity 0.2s;
}

/* 鼠标悬浮到节点时显示句柄 */
.custom-note-node:hover :deep(.vue-flow__handle) {
  opacity: 1;
}

/* 句柄悬浮时变大 */
:deep(.vue-flow__handle:hover) {
  width: 10px;
  height: 10px;
  background: #66b1ff;
}
</style>
