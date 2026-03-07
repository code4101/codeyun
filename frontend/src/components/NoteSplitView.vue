<template>
  <div class="note-split-view">
    <div class="note-main-pane" :style="{ height: `${topHeight}px` }">
      <slot name="main" />
      <div class="note-main-resizer" @mousedown="event => emit('resizeStart', event)">
        <div class="note-main-resizer-indicator"></div>
      </div>
    </div>

    <div
      class="note-editor-pane"
      :class="{
        'is-fill': editorMode === 'fill',
        'is-flow': editorMode === 'flow',
      }"
      :style="editorPaneStyle"
    >
      <div v-if="showEditor" class="note-editor-content">
        <slot name="editor" />
      </div>
      <div v-else class="note-empty-state">
        <el-empty :description="emptyDescription" />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';

const props = withDefaults(defineProps<{
  topHeight: number;
  showEditor: boolean;
  emptyDescription: string;
  editorMode?: 'fill' | 'flow';
  editorMinHeight?: number;
}>(), {
  editorMode: 'fill',
  editorMinHeight: 0,
});

const emit = defineEmits<{
  (e: 'resizeStart', event: MouseEvent): void;
}>();

const editorPaneStyle = computed(() => (
  props.editorMinHeight > 0
    ? { minHeight: `${props.editorMinHeight}px` }
    : undefined
));
</script>

<style scoped>
.note-split-view {
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.note-main-pane {
  position: relative;
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  min-height: 0;
}

.note-main-resizer {
  height: 8px;
  width: 100%;
  background-color: #f5f7fa;
  cursor: ns-resize;
  position: absolute;
  bottom: 0;
  left: 0;
  display: flex;
  justify-content: center;
  align-items: center;
  z-index: 10;
  transition: background-color 0.2s;
  border-top: 1px solid #e6e6e6;
}

.note-main-resizer:hover {
  background-color: #ecf5ff;
}

.note-main-resizer-indicator {
  width: 40px;
  height: 4px;
  border-top: 1px solid #dcdfe6;
  border-bottom: 1px solid #dcdfe6;
}

.note-editor-pane {
  display: flex;
  flex-direction: column;
  background-color: #fff;
}

.note-editor-pane.is-fill {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.note-editor-pane.is-flow {
  flex: none;
  overflow: visible;
  border-top: 1px solid #ebeef5;
}

.note-editor-content {
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
}

.note-editor-pane.is-fill .note-editor-content {
  flex: 1;
  min-height: 0;
  height: 100%;
  padding: 20px;
  overflow-y: auto;
}

.note-editor-pane.is-flow .note-editor-content {
  height: auto;
  padding: 20px;
}

.note-empty-state {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 100%;
  color: #909399;
}
</style>
