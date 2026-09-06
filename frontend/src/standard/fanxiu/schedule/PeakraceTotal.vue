<script setup lang="ts">
import { computed } from 'vue'
import type { FanxiuExchangeActivitySnapshot, FanxiuExchangeRankingItem } from '@/api/fanxiu'
import FanxiuActivityRankingSection from '../components/FanxiuActivityRankingSection.vue'

const props = defineProps<{ snapshot: FanxiuExchangeActivitySnapshot | null }>()
const activity = computed(() => props.snapshot?.selected_activity)
const rows = computed(() => {
  const total = activity.value?.instance_data.peakrace_total as { key_points?: FanxiuExchangeRankingItem[] } | undefined
  return total?.key_points ?? []
})
const extraRewards = [
  { itemId: '52', label: '天衍古髓' },
  { itemId: '9070096', label: '巅峰神识丹' },
]
</script>

<template>
  <article>
    <header>
      <h3>天道巅峰 · 总榜</h3>
      <span v-if="activity">{{ activity.label }}</span>
    </header>
    <FanxiuActivityRankingSection
      :personal-rows="rows"
      :show-plane="false"
      :extra-reward-columns="extraRewards"
      :personal-last-captured-at="activity?.captured_at"
      score-label="积分"
      score-per-reward-label="丹均积分"
    />
  </article>
</template>

<style scoped>
header { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }
h3 { margin: 0; }
</style>
