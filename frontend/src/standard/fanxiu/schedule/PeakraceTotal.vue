<script setup lang="ts">
import { computed } from 'vue'
import type { FanxiuExchangeActivitySnapshot } from '@/api/fanxiu'

interface Rank { rank: number; name: string; score: number }
interface Tier { rank_start: number; rank_end: number; amounts: Record<string, number> }
interface Total {
  self_ranking: Rank
  rankings: Rank[]
  rank_list_size: number
  items: { id: number; name: string }[]
  reward_tiers: Tier[]
}
const props = defineProps<{ snapshot: FanxiuExchangeActivitySnapshot | null }>()
const activity = computed(() => props.snapshot?.selected_activity)
const total = computed(() => activity.value?.instance_data.peakrace_total as Total | undefined)
const rankLabel = (tier: Tier) => tier.rank_start === tier.rank_end
  ? `${tier.rank_start}` : `${tier.rank_start}–${tier.rank_end}`
const rewardColumns = computed(() => total.value?.reward_tiers.map(tier => ({
  key: String(tier.rank_start), label: rankLabel(tier),
})) ?? [])
const rewardRows = computed(() => total.value?.items.map(item => ({
  name: item.name,
  ...Object.fromEntries(total.value!.reward_tiers.map(tier => [
    String(tier.rank_start), tier.amounts[String(item.id)] ?? '—',
  ])),
})) ?? [])
</script>

<template>
  <article class="peakrace-total">
    <header>
      <h3>天道巅峰 · 总榜</h3>
      <span v-if="activity">{{ activity.label }}</span>
      <el-tag type="info">研发中 · 未自动运行</el-tag>
    </header>
    <template v-if="total">
      <p>我的排名：<strong>{{ total.self_ranking.rank }}</strong>　积分：<strong>{{ total.self_ranking.score }}</strong></p>
      <p class="muted">采集时间：{{ activity?.captured_at }}。积分相同按最后一日榜单排名排序。</p>
      <el-tabs>
        <el-tab-pane label="奖励档次">
          <p class="muted">列为总榜名次，数量为对应档次奖励。</p>
          <el-table :data="rewardRows" border max-height="680" aria-label="巅峰赛总榜完整奖励">
            <el-table-column prop="name" label="道具" fixed min-width="200" />
            <el-table-column v-for="column in rewardColumns" :key="column.key" :prop="column.key" :label="column.label" min-width="85" align="right" />
          </el-table>
        </el-tab-pane>
        <el-tab-pane label="总榜排名">
          <p class="muted">已采集 {{ total.rankings.length }} 条排名，榜单人数 {{ total.rank_list_size }}；未采集的名次暂不展示。</p>
          <el-table :data="total.rankings" max-height="600">
            <el-table-column prop="rank" label="排名" width="90" />
            <el-table-column prop="name" label="玩家" min-width="180" />
            <el-table-column prop="score" label="积分" min-width="120" />
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </template>
    <p v-else>暂无总榜快照</p>
  </article>
</template>

<style scoped>
header { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
h3 { margin: 0; }
.muted { color: var(--el-text-color-secondary); font-size: 13px; }
</style>
