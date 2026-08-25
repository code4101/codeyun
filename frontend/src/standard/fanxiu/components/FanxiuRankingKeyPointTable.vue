<script setup lang="ts">
import type { FanxiuExchangeRankingItem } from '@/api/fanxiu'
import { formatChineseCompactNumber } from '@/utils/numberFormat'

withDefaults(defineProps<{
  rows: FanxiuExchangeRankingItem[]
  scoreLabel: string
  scorePerRewardLabel: string
  emptyText?: string
  showRewardColumns?: boolean
}>(), {
  emptyText: '暂无榜单数据',
  showRewardColumns: true,
})
</script>

<template>
  <table class="ranking-key-point-table">
    <thead>
      <tr>
        <th v-if="showRewardColumns">奖励档位</th>
        <th v-if="showRewardColumns" class="number-cell">天资丹</th>
        <th>排名</th>
        <th>角色</th>
        <th class="number-cell">{{ scoreLabel }}</th>
        <th v-if="showRewardColumns" class="number-cell">{{ scorePerRewardLabel }}</th>
        <th>区服</th>
        <th>宗门</th>
      </tr>
    </thead>
    <tbody>
      <tr v-if="!rows.length">
        <td :colspan="showRewardColumns ? 8 : 5" class="empty-cell">{{ emptyText }}</td>
      </tr>
      <tr
        v-for="row in rows"
        :key="row.id"
        :class="{ 'is-self': row.is_self }"
      >
        <td v-if="showRewardColumns">
          <template v-if="row.reward_rank_start != null">
            {{ row.reward_rank_start === row.reward_rank_end ? row.reward_rank_start : `${row.reward_rank_start}–${row.reward_rank_end}` }}
          </template>
          <template v-else-if="row.is_last_player">末位</template>
        </td>
        <td v-if="showRewardColumns" class="number-cell">{{ row.talent_pill_count ?? '—' }}</td>
        <td>{{ row.is_self && row.rank <= 0 ? '未上榜' : row.rank }}</td>
        <td>{{ row.has_player ? row.name : '—' }}</td>
        <td class="number-cell">{{ row.has_player ? formatChineseCompactNumber(row.score) : '—' }}</td>
        <td v-if="showRewardColumns" class="number-cell">
          {{ row.score_per_talent_pill == null ? '—' : formatChineseCompactNumber(row.score_per_talent_pill) }}
        </td>
        <td>{{ row.has_player ? (row.server_name || '—') : '—' }}</td>
        <td>{{ row.has_player ? (row.club_name || '—') : '—' }}</td>
      </tr>
    </tbody>
  </table>
</template>

<style scoped>
.ranking-key-point-table {
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
