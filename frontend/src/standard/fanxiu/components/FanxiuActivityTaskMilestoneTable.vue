<script setup lang="ts">
import type { FanxiuExchangeActivityTaskMilestone } from '@/api/fanxiu'
import { formatChineseCompactNumber } from '@/utils/numberFormat'

withDefaults(defineProps<{
  rows: FanxiuExchangeActivityTaskMilestone[]
  targetLabel: string
  current?: number
  emptyText?: string
}>(), {
  current: 0,
  emptyText: '暂无活动任务',
})
</script>

<template>
  <div class="table-shell">
    <table class="milestone-table">
      <thead>
        <tr>
          <th class="number-cell">档次</th>
          <th class="number-cell">{{ targetLabel }}</th>
          <th>状态</th>
          <th>标记</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in rows"
          :key="row.task_id"
          :class="{ reached: row.finished || row.target <= current }"
        >
          <td class="number-cell">{{ row.order }}</td>
          <td class="number-cell">{{ formatChineseCompactNumber(row.target) }}</td>
          <td>{{ row.finished || row.target <= current ? '已达成' : '未达成' }}</td>
          <td>{{ row.must_get ? '必拿' : '' }}</td>
        </tr>
        <tr v-if="!rows.length">
          <td colspan="4" class="empty-cell">{{ emptyText }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.table-shell {
  max-width: 100%;
  overflow-x: auto;
}

.milestone-table {
  width: max-content;
  max-width: 100%;
  border-collapse: collapse;
  font-size: 14px;
}

th,
td {
  padding: 9px 14px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  white-space: nowrap;
}

th {
  color: var(--el-text-color-secondary);
  font-weight: 500;
  text-align: left;
  background: var(--el-fill-color-light);
}

.number-cell {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.empty-cell {
  padding: 24px;
  color: var(--el-text-color-secondary);
  text-align: center;
}

.reached td {
  background: var(--el-color-primary-light-9);
}
</style>
