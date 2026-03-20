<template>
  <div class="node-selector-wrapper">
    <div class="label-row" v-if="showLabel">
      <span class="field-label">{{ label }}:</span>
      <el-tooltip v-if="showHelpIcon" content="点击查看属性说明" placement="top">
        <el-icon class="help-icon" @click="emit('showHelp')"><QuestionFilled /></el-icon>
      </el-tooltip>
    </div>
    
    <el-popover
      placement="bottom-start"
      :width="mode === 'type' ? 320 : mode === 'form' ? 200 : 160"
      trigger="click"
      popper-class="node-selector-popper"
      v-model:visible="popoverVisible"
      :disabled="disabled"
    >
      <template #reference>
        <div class="selector-trigger" :class="{ 'is-disabled': disabled }" :style="triggerStyle">
          <div v-if="mode === 'form'" class="trigger-form-content">
            <NoteFormBadge :form="modelValue || 'note'" :show-label="true" />
          </div>
          <span v-else class="trigger-text">{{ currentLabel }}</span>
          <el-icon><ArrowDown /></el-icon>
        </div>
      </template>
      
      <div class="selector-options" :class="{ 'is-grid': mode === 'type', 'is-list': mode !== 'type' }">
        <div 
          v-for="item in options" 
          :key="item.id"
          class="selector-item"
          :class="{ active: modelValue === item.id }"
          @click="selectItem(item.id)"
        >
          <div class="item-preview" :style="getItemStyle(item)">
            <NoteFormBadge
              v-if="mode === 'form'"
              :form="item.id"
              :show-label="true"
            />
            <template v-else>
              {{ item.label }}
            </template>
          </div>
        </div>
      </div>
    </el-popover>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { QuestionFilled, ArrowDown } from '@element-plus/icons-vue';
import NoteFormBadge from './NoteFormBadge.vue';
import { 
  getOrderedNodeTypes, 
  getOrderedNoteForms,
  getOrderedNodeStatuses, 
  getNodeStyle,
  getNoteFormConfig,
  getNodeTypeConfig,
  getNodeStatusConfig,
  type NoteTypeAssignment,
  type NoteFormItem,
  type NodeTypeItem,
  type NodeStatusItem
} from '@/utils/nodeConfig';

const props = defineProps<{
  modelValue: string | null | undefined;
  mode: 'type' | 'status' | 'form';
  relatedType?: string | null; // For status mode to know color context
  customColor?: string | null;
  noteTypes?: NoteTypeAssignment[] | null;
  label?: string;
  showLabel?: boolean;
  showHelpIcon?: boolean;
  triggerMinWidth?: string | number | null;
  disabled?: boolean;
}>();

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void;
  (e: 'change', value: string): void;
  (e: 'showHelp'): void;
}>();

const popoverVisible = ref(false);

const options = computed(() => {
  if (props.mode === 'type') {
    return getOrderedNodeTypes();
  } else if (props.mode === 'form') {
    return getOrderedNoteForms();
  } else {
    return getOrderedNodeStatuses();
  }
});

const currentLabel = computed(() => {
  if (props.mode === 'type') {
    return getNodeTypeConfig(props.modelValue || 'general').label;
  } else if (props.mode === 'form') {
    return getNoteFormConfig(props.modelValue || 'note').label;
  } else {
    return getNodeStatusConfig(props.modelValue || 'idea').label;
  }
});

// Trigger Style (The button itself)
const triggerStyle = computed(() => {
  const minWidth = props.triggerMinWidth == null
    ? undefined
    : (typeof props.triggerMinWidth === 'number' ? `${props.triggerMinWidth}px` : props.triggerMinWidth);
  // We want the trigger to look like the node
  const typeId = props.mode === 'type' ? (props.modelValue || 'general') : (props.relatedType || 'general');
  const statusId = props.mode === 'status' ? (props.modelValue || 'idea') : 'idea'; // If type mode, use idea (default) style or just color?
  
  // If mode is type, we just show the color and a simple box
  if (props.mode === 'type') {
    const style = getNodeStyle(typeId, 'idea', props.customColor, props.noteTypes);
    return {
      borderColor: style.borderColor,
      color: style.color,
      backgroundColor: style.backgroundColor,
      borderWidth: style.borderWidth,
      borderStyle: style.borderStyle,
      minWidth
    };
  } else if (props.mode === 'form') {
    const style = getNodeStyle(typeId, 'idea', props.customColor, props.noteTypes);
    return {
      borderColor: style.borderColor,
      color: style.color,
      backgroundColor: style.backgroundColor,
      borderWidth: style.borderWidth,
      borderStyle: style.borderStyle,
      minWidth
    };
  } else {
    // If mode is status, we show the status style (border type etc)
    // We need to use getNodeStyle with the relatedType
    const style = getNodeStyle(typeId, statusId, props.customColor, props.noteTypes);
    return {
      borderColor: style.borderColor,
      color: style.color, // Usually type color
      backgroundColor: style.backgroundColor, // White or light color
      borderWidth: style.borderWidth,
      borderStyle: style.borderStyle,
      textDecoration: style.textDecoration,
      opacity: style.opacity,
      minWidth
    };
  }
});

// Item Style (In the dropdown)
const getItemStyle = (item: NodeTypeItem | NodeStatusItem | NoteFormItem) => {
  if (props.mode === 'type') {
    const i = item as NodeTypeItem;
    // For type options, use 'idea' style (default)
    const style = getNodeStyle(i.id, 'idea');
    return {
      borderColor: style.borderColor,
      color: style.color,
      borderWidth: style.borderWidth,
      borderStyle: style.borderStyle,
      backgroundColor: style.backgroundColor
    };
  } else if (props.mode === 'form') {
    const typeId = props.relatedType || 'general';
    const style = getNodeStyle(typeId, 'idea', props.customColor, props.noteTypes);
    return {
      borderColor: style.borderColor,
      color: style.color,
      backgroundColor: style.backgroundColor,
      borderWidth: style.borderWidth,
      borderStyle: style.borderStyle,
      opacity: style.opacity
    };
  } else {
    // Status Preview
    // Use relatedType if available, else use a default type (e.g. 'task' or 'note') for context
    const typeId = props.relatedType || 'general';
    const style = getNodeStyle(typeId, item.id, props.customColor, props.noteTypes);
    return {
      borderColor: style.borderColor,
      color: style.color,
      backgroundColor: style.backgroundColor,
      borderWidth: style.borderWidth,
      borderStyle: style.borderStyle,
      textDecoration: style.textDecoration,
      opacity: style.opacity
    };
  }
};

const selectItem = (id: string) => {
  if (props.disabled) return;
  emit('update:modelValue', id);
  emit('change', id);
  popoverVisible.value = false;
};
</script>

<style scoped>
.node-selector-wrapper {
  display: flex;
  align-items: center;
  gap: 8px;
}

.label-row {
  display: flex;
  align-items: center;
  gap: 4px;
}

.field-label {
  font-size: 12px;
  color: #606266;
  white-space: nowrap;
}

.help-icon {
  font-size: 14px;
  color: #909399;
  cursor: help;
}

.selector-trigger {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 10px;
  height: 28px; /* Slightly taller than mini */
  border-radius: 4px;
  cursor: pointer;
  font-size: 12px;
  min-width: 100px;
  transition: all 0.2s;
  user-select: none;
}

.selector-trigger:hover {
  filter: brightness(0.95);
}

.selector-trigger.is-disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.selector-trigger.is-disabled:hover {
  filter: none;
}

.trigger-text {
  margin-right: 5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.trigger-form-content {
  display: flex;
  align-items: center;
  min-width: 0;
  margin-right: 5px;
}

/* Dropdown Options */
.selector-options {
  padding: 5px;
}

.selector-options.is-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
}

.selector-options.is-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.selector-item {
  cursor: pointer;
}

.item-preview {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 32px;
  border-radius: 4px;
  font-size: 12px;
  transition: all 0.1s;
  padding: 0 4px;
}

.item-preview:hover {
  transform: translateY(-1px);
  box-shadow: 0 2px 6px rgba(0,0,0,0.1);
  opacity: 1 !important; /* Ensure visibility */
}
</style>
