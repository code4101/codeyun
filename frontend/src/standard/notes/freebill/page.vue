<script setup lang="ts">
import { ref } from 'vue'
import type { FreebillDataset } from '@/api/freebill'
import FreebillWorkspace from './FreebillWorkspace.vue'

// Mount only the selected workspace; opening the demo never requests personal bills.
const dataset = ref<FreebillDataset>('demo')
</script>

<template>
  <div class="freebill-datasets">
    <el-tabs v-model="dataset" class="dataset-tabs" stretch>
      <el-tab-pane label="官方示例数据" name="demo" />
      <el-tab-pane label="个人真实数据" name="personal" />
    </el-tabs>
    <p class="dataset-description">
      {{ dataset === 'demo'
        ? '独立编造的示例账单，可自由修改、导入和测试，随时重置。'
        : '查看和管理个人账单；没有账单时，可导入数据或切换到官方示例体验。' }}
    </p>
    <FreebillWorkspace :key="dataset" :dataset="dataset" />
  </div>
</template>

<style scoped>
.freebill-datasets { height: 100%; overflow: auto; }
.dataset-tabs { padding: 8px 20px 0; }
.dataset-tabs :deep(.el-tabs__item) { font-size: 17px; height: 52px; font-weight: 600; }
.dataset-tabs :deep(.el-tabs__header) { margin-bottom: 0; }
.dataset-description { margin: 8px 20px 0; color: var(--el-text-color-secondary); font-size: 13px; }
</style>
