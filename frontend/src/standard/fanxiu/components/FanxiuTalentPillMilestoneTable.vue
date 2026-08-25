<script setup lang="ts">
import { computed } from 'vue'

import { formatChineseCompactNumber } from '@/utils/numberFormat'
import { projectTalentPillMilestones, type TalentPillMilestone } from './talentPillMilestones'

const props = withDefaults(defineProps<{
  rows: TalentPillMilestone[]
  targetLabel: string
  perPillLabel: string
  current?: number
  emptyText?: string
}>(), {
  current: 0,
  emptyText: '暂无天资丹任务',
})

const milestones = computed(() => projectTalentPillMilestones(props.rows))
</script>

<template>
  <div class="table-shell">
    <table class="milestone-table">
      <thead>
        <tr>
          <th class="number-cell">档次</th>
          <th class="number-cell">{{ targetLabel }}</th>
          <th class="number-cell">天资丹</th>
          <th class="number-cell">{{ perPillLabel }}</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in milestones"
          :key="row.task_id"
          :class="{ reached: row.target <= current }"
        >
          <td class="number-cell">{{ row.order }}</td>
          <td class="number-cell">{{ formatChineseCompactNumber(row.target) }}</td>
          <td class="number-cell">{{ formatChineseCompactNumber(row.talent_pill_count) }}</td>
          <td class="number-cell">{{ formatChineseCompactNumber(row.costPerTalentPill) }}</td>
        </tr>
        <tr v-if="!milestones.length">
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
