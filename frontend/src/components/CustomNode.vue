<template>
  <div class="custom-note-node" :style="props.data.nodeStyle">
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
      <div v-if="props.data.useSplitTitle" class="node-title node-title--split" :style="props.data.titleStyle">
        <div class="node-title-layer" :style="props.data.filledTitleLayerStyle">
          <NoteFormBadge :form="data.note_form" compact />
          <span class="node-title-text">{{ data.title || 'Untitled' }}</span>
        </div>
        <div class="node-title-layer" :style="props.data.emptyTitleLayerStyle">
          <NoteFormBadge :form="data.note_form" compact />
          <span class="node-title-text">{{ data.title || 'Untitled' }}</span>
        </div>
      </div>
      <div v-else class="node-title" :style="props.data.titleStyle">
        <NoteFormBadge :form="data.note_form" compact />
        <span class="node-title-text">{{ data.title || 'Untitled' }}</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { Handle, Position } from '@vue-flow/core'
import NoteFormBadge from './NoteFormBadge.vue'

const props = defineProps<{
  data: {
    title: string,
    note_form?: string | null,
    nodeStyle: Record<string, string | number>,
    titleStyle: Record<string, string | number>,
    useSplitTitle: boolean,
    filledTitleLayerStyle: Record<string, string | number>,
    emptyTitleLayerStyle: Record<string, string | number>,
  }
}>()
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
