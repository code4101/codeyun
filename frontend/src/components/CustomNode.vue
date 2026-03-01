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
    
    const style: any = {
        width: `${Math.round(BASE_WIDTH * scale)}px`,
        height: `${Math.round(BASE_HEIGHT * scale)}px`,
    };

    if (props.data.node_type === 'project') {
        style.borderColor = '#9c27b0'; // Purple for projects
        style.borderWidth = '3px'; // Thicker border for project containers
        style.background = '#f3e5f5'; // Very light purple background
    } else if (props.data.node_type === 'module') {
        style.borderColor = '#ba68c8'; // Lighter purple for modules
        style.borderWidth = '2px';
        style.background = '#faf4fb'; // Even lighter purple background
    } else if (props.data.node_type === 'todo') {
        style.borderColor = '#409eff';
        style.borderWidth = '2px';
    } else if (props.data.node_type === 'doing') {
        style.borderColor = '#e6a23c'; // Warning orange
        style.borderWidth = '2px';
        style.background = '#fdf6ec'; // Light orange background
    } else if (props.data.node_type === 'pre-done') {
        style.borderColor = '#67c23a'; // Success green
        style.borderWidth = '2px';
        style.borderStyle = 'dashed'; // Dashed to indicate "pre" done
        style.background = '#f0f9eb'; // Light green background
    } else if (props.data.node_type === 'done') {
        style.borderColor = '#e6e6e6';
        style.background = '#fafafa';
    } else if (props.data.node_type === 'delete') {
        style.borderColor = '#dcdfe6';
        style.background = '#f5f7fa';
        style.opacity = '0.6';
    } else if (props.data.node_type === 'bug') {
        style.borderColor = '#f56c6c'; // Danger red
        style.background = '#fef0f0'; // Light red background
        style.borderWidth = '2px';
    } else if (props.data.node_type === 'memo') {
        style.borderColor = '#303133'; // Black/Dark gray
        style.borderWidth = '2px';
    }
    
    return style;
});

const titleStyle = computed(() => {
    const weight = props.data.weight || 100;
    // Scale font size slightly less aggressively than dimensions
    const safeWeight = Math.max(10, weight);
    const scale = Math.sqrt(safeWeight / 100);
    // Base font 14px, max 24px, min 10px
    const fontSize = Math.min(24, Math.max(10, Math.round(14 * scale)));
    
    const style: any = {
        fontSize: `${fontSize}px`
    };

    if (props.data.node_type === 'project') {
        style.color = '#7b1fa2'; // Deep purple for project title
        style.fontWeight = 'bold';
    } else if (props.data.node_type === 'module') {
        style.color = '#9c27b0'; // Purple for module title
        style.fontWeight = 'bold';
    } else if (props.data.node_type === 'todo') {
        style.color = '#409eff'; // Blue for todo
        style.fontWeight = 'bold';
    } else if (props.data.node_type === 'doing') {
        style.color = '#e6a23c'; // Warning orange
        style.fontWeight = 'bold';
    } else if (props.data.node_type === 'pre-done') {
        style.color = '#67c23a'; // Success green
        style.fontWeight = 'bold';
    } else if (props.data.node_type === 'done') {
        style.color = '#909399'; // Gray for done
    } else if (props.data.node_type === 'delete') {
        style.color = '#c0c4cc'; // Lighter gray for deleted
        style.textDecoration = 'line-through';
    } else if (props.data.node_type === 'bug') {
        style.color = '#f56c6c'; // Red for bug
        style.fontWeight = 'bold';
    } else if (props.data.node_type === 'memo') {
        style.color = '#000000'; // Pure black for memo
        style.fontWeight = 'bold';
    }
    
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
