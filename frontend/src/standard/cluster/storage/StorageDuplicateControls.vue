<template>
  <section class="duplicate-toolbar">
    <label class="duplicate-field duplicate-field-rules">
      <span class="storage-field-label">判重规则</span>
      <el-checkbox-group v-model="ruleFieldsModel" class="duplicate-rule-group">
        <el-checkbox label="size" disabled>大小</el-checkbox>
        <el-checkbox label="name">名称</el-checkbox>
        <el-checkbox label="extension">扩展名</el-checkbox>
        <el-checkbox label="modified_at">修改时间</el-checkbox>
        <el-checkbox label="sha256">SHA256</el-checkbox>
      </el-checkbox-group>
    </label>

    <label class="duplicate-field duplicate-field-min-size">
      <span class="storage-field-label">最小大小</span>
      <el-input-number
        v-model="minSizeMbModel"
        class="duplicate-number-input"
        :min="0"
        :max="1048576"
        :step="100"
        :precision="0"
        controls-position="right"
      />
    </label>

    <label class="duplicate-field duplicate-field-select">
      <span class="storage-field-label">排序</span>
      <el-select v-model="sortModeModel" class="duplicate-select">
        <el-option label="可释放空间" value="reclaimable" />
        <el-option label="单文件大小" value="file_size" />
        <el-option label="整组大小" value="group_total" />
      </el-select>
    </label>

    <label class="duplicate-field duplicate-field-select">
      <span class="storage-field-label">来源</span>
      <el-select v-model="sourceModel" class="duplicate-select">
        <el-option label="自动" value="auto" />
        <el-option label="Everything" value="everything" />
        <el-option label="遍历" value="filesystem" />
      </el-select>
    </label>

    <label class="duplicate-field duplicate-field-filter">
      <span class="storage-field-label">筛选</span>
      <el-popover placement="bottom-start" trigger="click" :width="620" popper-class="duplicate-filter-popover">
        <template #reference>
          <el-button class="duplicate-filter-button">
            路径规则 {{ enabledFilterCount }}/{{ filterRulesModel.length }}
          </el-button>
        </template>
        <div class="duplicate-filter-panel">
          <div class="duplicate-filter-header">
            <strong>路径规则</strong>
            <div class="duplicate-filter-actions">
              <el-button size="small" @click="emit('add-filter-rule')">+</el-button>
              <el-button size="small" @click="emit('reset-filter-rules')">默认</el-button>
            </div>
          </div>
          <div
            v-for="(rule, index) in filterRulesModel"
            :key="index"
            class="duplicate-filter-rule"
          >
            <el-checkbox v-model="rule.enabled" />
            <el-select v-model="rule.action" class="duplicate-filter-action" size="small">
              <el-option label="排除" value="exclude" />
              <el-option label="包含" value="include" />
            </el-select>
            <el-select v-model="rule.match" class="duplicate-filter-match" size="small">
              <el-option label="包含" value="contains" />
              <el-option label="前缀" value="prefix" />
              <el-option label="后缀" value="suffix" />
              <el-option label="等于" value="equals" />
              <el-option label="glob" value="glob" />
            </el-select>
            <el-input
              v-model="rule.value"
              class="duplicate-filter-value"
              size="small"
              placeholder="$Recycle.Bin"
            />
            <button
              type="button"
              class="duplicate-filter-remove"
              title="删除规则"
              @click="emit('remove-filter-rule', index)"
            >
              -
            </button>
          </div>
        </div>
      </el-popover>
    </label>

    <el-button
      type="primary"
      :icon="duplicateLoading ? undefined : Search"
      :disabled="!canBrowse || duplicateLoading"
      @click="emit('analyze')"
    >
      {{ duplicateLoading ? '分析中' : '分析' }}
    </el-button>
  </section>

  <section class="duplicate-pagination">
    <StandardPagination
      :page="page"
      :page-size="pageSize"
      :page-count="pageCount"
      :show-page-size="false"
      :disabled="duplicateLoading"
      @page-change="emit('page-change', $event)"
    />
  </section>
</template>

<script setup lang="ts">
import { Search } from '@element-plus/icons-vue';
import StandardPagination from '@/components/StandardPagination.vue';
import type {
  DeviceDuplicateFilterRule,
  DeviceDuplicateRule,
  DeviceDuplicateSortMode,
  DeviceDuplicateSource,
} from '@/api/deviceFiles';

defineProps<{
  canBrowse: boolean;
  duplicateLoading: boolean;
  enabledFilterCount: number;
  page: number;
  pageCount: number;
  pageSize: number;
}>();

const emit = defineEmits<{
  (event: 'add-filter-rule'): void;
  (event: 'analyze'): void;
  (event: 'page-change', page: number): void;
  (event: 'remove-filter-rule', index: number): void;
  (event: 'reset-filter-rules'): void;
}>();

const ruleFieldsModel = defineModel<DeviceDuplicateRule[]>('ruleFields', { required: true });
const minSizeMbModel = defineModel<number>('minSizeMb', { required: true });
const sortModeModel = defineModel<DeviceDuplicateSortMode>('sortMode', { required: true });
const sourceModel = defineModel<DeviceDuplicateSource>('source', { required: true });
const filterRulesModel = defineModel<DeviceDuplicateFilterRule[]>('filterRules', { required: true });
</script>

<style scoped>
.duplicate-toolbar {
  padding: 0 16px 4px;
  display: flex;
  align-items: end;
  gap: 12px;
  flex-wrap: wrap;
}

.duplicate-field {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.storage-field-label {
  color: #64748b;
  font-size: 12px;
  font-weight: 600;
}

.duplicate-field-rules {
  min-width: 420px;
}

.duplicate-field-min-size {
  width: 140px;
}

.duplicate-field-select {
  width: 136px;
}

.duplicate-field-filter {
  width: 132px;
}

.duplicate-rule-group {
  min-height: 32px;
  padding: 0 10px;
  border: 1px solid #d5dde8;
  border-radius: 4px;
  background: #ffffff;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.duplicate-rule-group :deep(.el-checkbox) {
  margin-right: 10px;
}

.duplicate-number-input,
.duplicate-select {
  width: 100%;
}

.duplicate-filter-button {
  width: 100%;
}

.duplicate-filter-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.duplicate-filter-header,
.duplicate-filter-rule {
  display: flex;
  align-items: center;
  gap: 8px;
}

.duplicate-filter-header {
  justify-content: space-between;
}

.duplicate-filter-actions {
  display: flex;
  gap: 6px;
}

.duplicate-filter-action {
  width: 82px;
}

.duplicate-filter-match {
  width: 86px;
}

.duplicate-filter-value {
  flex: 1;
}

.duplicate-filter-remove {
  width: 24px;
  height: 24px;
  border: 1px solid #fecaca;
  border-radius: 4px;
  background: #fff5f5;
  color: #b42318;
  line-height: 1;
  cursor: pointer;
}

.duplicate-filter-remove:hover {
  background: #fee2e2;
}

.duplicate-pagination {
  min-height: 36px;
  padding: 0 16px;
  display: flex;
  align-items: center;
  gap: 10px;
}

@media (max-width: 980px) {
  .duplicate-field-rules,
  .duplicate-field-min-size,
  .duplicate-field-select,
  .duplicate-field-filter {
    width: 100%;
    min-width: 0;
  }

  .duplicate-rule-group {
    flex-wrap: wrap;
    min-height: 40px;
    padding: 6px 10px;
  }
}
</style>
