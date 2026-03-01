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
      <div class="node-title" :style="titleStyle">{{ data.title || 'Untitled' }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { Handle, Position } from '@vue-flow/core'
import { computed } from 'vue'
import { getNodeConfig } from '@/utils/nodeConfig'

const props = defineProps<{
  data: {
    title: string,
    weight?: number,
    node_type?: string | null
  }
}>()

const BASE_WIDTH = 150;
const BASE_HEIGHT = 50;

const nodeStyle = computed(() => {
    const weight = props.data.weight || 100;
    const safeWeight = Math.max(10, weight);
    const scale = Math.sqrt(safeWeight / 100);
    
    const config = getNodeConfig(props.data.node_type);
    
    const style: any = {
        width: `${Math.round(BASE_WIDTH * scale)}px`,
        height: `${Math.round(BASE_HEIGHT * scale)}px`,
        borderColor: config.borderColor,
        borderWidth: config.borderWidth,
        borderStyle: config.borderStyle,
        background: config.backgroundColor,
        opacity: config.opacity,
    };
    
    return style;
});

const titleStyle = computed(() => {
    const weight = props.data.weight || 100;
    // Scale font size slightly less aggressively than dimensions
    const safeWeight = Math.max(10, weight);
    const scale = Math.sqrt(safeWeight / 100);
    // Base font 14px, max 24px, min 10px
    const fontSize = Math.min(24, Math.max(10, Math.round(14 * scale)));
    
    const config = getNodeConfig(props.data.node_type);
    
    const style: any = {
        fontSize: `${fontSize}px`,
        color: config.color,
        fontWeight: config.fontWeight,
        textDecoration: config.textDecoration,
    };
    
    return style;
});
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
  transition: all 0.3s;
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
}

.node-content {
    width: 100%;
    padding: 0 10px; /* Internal padding */
    box-sizing: border-box;
    overflow: hidden;
}

.custom-note-node:hover {
  border-color: #409eff;
  box-shadow: 0 4px 16px 0 rgba(0, 0, 0, 0.15);
  z-index: 10; /* Bring to front on hover */
}

.node-title {
  font-weight: 500;
  color: #303133;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  pointer-events: none; /* 防止文字干扰拖拽 */
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
