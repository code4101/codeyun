<script setup lang="ts">
import type { FanxiuExchangeRankingItem } from '@/api/fanxiu'
import { formatChineseCompactNumber } from '@/utils/numberFormat'

withDefaults(defineProps<{
  rows: FanxiuExchangeRankingItem[]
  scoreLabel: string
  emptyText?: string
  subjectLabel?: string
}>(), {
  emptyText: '暂无位面榜数据',
  subjectLabel: '位面',
})

function rankingSubjectText(row: FanxiuExchangeRankingItem): string {
  return row.server_name
    || row.subject?.server_name
    || row.name
    || row.subject?.name
    || '—'
}
</script>

<template>
  <table class="plane-ranking-table">
    <thead>
      <tr>
        <th>排名</th>
        <th>{{ subjectLabel }}</th>
        <th class="number-cell">{{ scoreLabel }}</th>
      </tr>
    </thead>
    <tbody>
      <tr v-if="!rows.length">
        <td colspan="3" class="empty-cell">{{ emptyText }}</td>
      </tr>
      <tr
        v-for="row in rows"
        :key="row.id"
        :class="{ 'is-self': row.is_self }"
      >
        <td>{{ row.rank }}</td>
        <td>{{ rankingSubjectText(row) }}</td>
        <td class="number-cell">{{ formatChineseCompactNumber(row.score) }}</td>
      </tr>
    </tbody>
  </table>
</template>

<style scoped>
.plane-ranking-table {
  width: max-content;
  max-width: 100%;
  border-collapse: collapse;
  font-size: 14px;
}

th,
td {
  padding: 9px 14px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  text-align: left;
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

.is-self td {
  background: var(--el-color-primary-light-9);
}
</style>
