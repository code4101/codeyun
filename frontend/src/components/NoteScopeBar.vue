<template>
  <div class="scope-bar">
    <div class="scope-header">
      <div class="scope-meta">
        <div class="scope-title">{{ title }}</div>
        <div v-if="caption" class="scope-caption">{{ caption }}</div>
      </div>
      <div class="scope-actions">
        <el-button @click="$emit('reset')">清空筛选</el-button>
        <el-button type="primary" :loading="loading" @click="$emit('apply')">{{ applyText }}</el-button>
      </div>
    </div>

    <div class="scope-fields">
      <el-input
        v-if="showTitleKeyword"
        :model-value="scopeValue.titleKeyword"
        placeholder="标题包含..."
        clearable
        class="w-200"
        @update:model-value="updateScope({ titleKeyword: $event || '' })"
      />

      <el-select
        v-if="showType"
        :model-value="scopeValue.nodeType"
        placeholder="所有类型"
        clearable
        class="w-120"
        @update:model-value="updateScope({ nodeType: $event || '' })"
      >
        <el-option
          v-for="type in orderedNodeTypes"
          :key="type.id"
          :label="type.label"
          :value="type.id"
        />
      </el-select>

      <el-select
        v-if="showStatus"
        :model-value="scopeValue.nodeStatus"
        placeholder="所有状态"
        clearable
        class="w-120"
        @update:model-value="updateScope({ nodeStatus: $event || '' })"
      >
        <el-option
          v-for="status in orderedNodeStatuses"
          :key="status.id"
          :label="status.label"
          :value="status.id"
        />
      </el-select>

      <el-date-picker
        v-if="showStartRange"
        :model-value="scopeValue.startRange.length ? scopeValue.startRange : null"
        type="datetimerange"
        range-separator="至"
        start-placeholder="起始时间开始"
        end-placeholder="起始时间结束"
        value-format="x"
        class="w-320"
        @update:model-value="updateScope({ startRange: $event })"
      />

      <el-date-picker
        v-if="showUpdatedRange"
        :model-value="scopeValue.updatedRange.length ? scopeValue.updatedRange : null"
        type="datetimerange"
        range-separator="至"
        start-placeholder="更新时间开始"
        end-placeholder="更新时间结束"
        value-format="x"
        class="w-320"
        @update:model-value="updateScope({ updatedRange: $event })"
      />
    </div>

    <div class="scope-summary">
      <span class="summary-label">当前数据筛选:</span>
      <div v-if="activeTags.length" class="summary-tags">
        <el-tag v-for="tag in activeTags" :key="tag.key" size="small" :type="tag.type">{{ tag.label }}</el-tag>
      </div>
      <span v-else class="summary-empty">未附加规则，按默认范围加载</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { createEmptyNoteScopeState, normalizeNoteScopeState, type NoteScopeState } from '@/api/notes';
import { getNodeStatusConfig, getNodeTypeConfig, getOrderedNodeStatuses, getOrderedNodeTypes } from '@/utils/nodeConfig';

const props = withDefaults(defineProps<{
  modelValue?: NoteScopeState | null;
  title?: string;
  caption?: string;
  applyText?: string;
  loading?: boolean;
  showTitleKeyword?: boolean;
  showType?: boolean;
  showStatus?: boolean;
  showStartRange?: boolean;
  showUpdatedRange?: boolean;
}>(), {
  modelValue: null,
  title: '数据筛选',
  caption: '',
  applyText: '应用筛选',
  loading: false,
  showTitleKeyword: true,
  showType: true,
  showStatus: true,
  showStartRange: true,
  showUpdatedRange: true
});

const emit = defineEmits<{
  (e: 'update:modelValue', value: NoteScopeState): void;
  (e: 'apply'): void;
  (e: 'reset'): void;
}>();

const orderedNodeTypes = computed(() => getOrderedNodeTypes());
const orderedNodeStatuses = computed(() => getOrderedNodeStatuses());

const scopeValue = computed(() => normalizeNoteScopeState(props.modelValue ?? createEmptyNoteScopeState()));

const formatRange = (range: number[]) => {
  if (range.length !== 2) return '';
  return `${formatDate(range[0])} - ${formatDate(range[1])}`;
};

const formatDate = (timestamp: number) => {
  return new Date(timestamp).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  });
};

const activeTags = computed(() => {
  const tags: Array<{ key: string; label: string; type?: '' | 'success' | 'warning' | 'info' | 'danger' }> = [];
  const value = scopeValue.value;

  if (value.titleKeyword) {
    tags.push({ key: 'title', label: `标题含 "${value.titleKeyword}"` });
  }
  if (value.nodeType) {
    tags.push({ key: 'type', label: `类型: ${getNodeTypeConfig(value.nodeType).label}`, type: 'success' });
  }
  if (value.nodeStatus) {
    tags.push({ key: 'status', label: `状态: ${getNodeStatusConfig(value.nodeStatus).label}`, type: 'warning' });
  }
  if (value.startRange.length === 2) {
    tags.push({ key: 'start', label: `起始: ${formatRange(value.startRange)}`, type: 'info' });
  }
  if (value.updatedRange.length === 2) {
    tags.push({ key: 'updated', label: `更新: ${formatRange(value.updatedRange)}`, type: 'danger' });
  }

  return tags;
});

const updateScope = (patch: Partial<NoteScopeState>) => {
  emit('update:modelValue', normalizeNoteScopeState({
    ...scopeValue.value,
    ...patch
  }));
};
</script>

<style scoped>
.scope-bar {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px 16px;
  border: 1px solid #e4e7ed;
  border-radius: 10px;
  background: linear-gradient(180deg, #fbfcfe 0%, #f5f7fa 100%);
}

.scope-header {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
}

.scope-meta {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.scope-title {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}

.scope-caption {
  font-size: 12px;
  color: #606266;
  line-height: 1.5;
}

.scope-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

.scope-fields {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.scope-summary {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  min-height: 24px;
}

.summary-label {
  font-size: 12px;
  color: #606266;
  line-height: 24px;
  white-space: nowrap;
}

.summary-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.summary-empty {
  font-size: 12px;
  color: #909399;
  line-height: 24px;
}

.w-120 {
  width: 120px;
}

.w-200 {
  width: 200px;
}

.w-320 {
  width: 320px;
}
</style>
