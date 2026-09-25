<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  getFanxiuExchangeActivitySnapshot,
} from '@/api/fanxiu/activities';
import type {
  FanxiuExchangeActivitySnapshot,
  FanxiuExchangeRankingItem,
} from '@/api/fanxiu/activities';
import FanxiuActivityRankingSection from '../components/FanxiuActivityRankingSection.vue'

const props = defineProps<{ snapshot: FanxiuExchangeActivitySnapshot | null }>()
const loadedSnapshot = ref<FanxiuExchangeActivitySnapshot | null>(null)
const errorMessage = ref('')
watch(() => props.snapshot, async snapshot => {
  if (snapshot) return
  errorMessage.value = ''
  try {
    loadedSnapshot.value = await getFanxiuExchangeActivitySnapshot('peakrace')
  } catch (error: any) {
    errorMessage.value = error?.response?.data?.detail || error?.message || '读取天道巅峰失败'
  }
}, { immediate: true })
const activity = computed(() => (props.snapshot ?? loadedSnapshot.value)?.selected_activity)
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
      <slot name="activity-type-control"><h3>天道巅峰 · 总榜</h3></slot>
      <span v-if="activity">{{ activity.label }}</span>
    </header>
    <div v-if="errorMessage" role="alert">{{ errorMessage }}</div>
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
