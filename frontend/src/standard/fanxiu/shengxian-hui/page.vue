<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import {
  getFanxiuExchangeActivityRankings, getFanxiuExchangeActivitySnapshot,
  type FanxiuExchangeActivitySummary, type FanxiuExchangeRankingPage,
} from '@/api/fanxiu/activities'
import FanxiuActivityRankingSection from '../components/FanxiuActivityRankingSection.vue'

defineProps<{ embedded?: boolean }>()
const activities = ref<FanxiuExchangeActivitySummary[]>([])
const selectedId = ref('')
const ranking = ref<FanxiuExchangeRankingPage | null>(null)
const loading = ref(false)
const errorText = ref('')
const page = ref(1)
let requestId = 0

async function loadRankings() {
  if (!selectedId.value) return
  const current = ++requestId
  loading.value = true
  errorText.value = ''
  try {
    const result = await getFanxiuExchangeActivityRankings('shengxian-hui', selectedId.value, page.value, 20, 'personal')
    if (current === requestId) ranking.value = result
  } catch (error: any) {
    if (current === requestId) errorText.value = error?.response?.data?.detail || error?.message || '读取巅峰榜失败'
  } finally {
    if (current === requestId) loading.value = false
  }
}
watch(selectedId, () => { page.value = 1; ranking.value = null; void loadRankings() })
onMounted(async () => {
  try {
    const snapshot = await getFanxiuExchangeActivitySnapshot('shengxian-hui')
    activities.value = snapshot.activities
    selectedId.value = snapshot.selected_activity?.id || snapshot.activities[0]?.id || ''
  } catch (error: any) {
    errorText.value = error?.response?.data?.detail || error?.message || '读取升仙会失败'
  }
})
</script>

<template>
  <section class="shengxian-page">
    <header class="activity-controls">
      <slot name="activity-type-control"><h3>升仙会</h3></slot>
      <el-select v-model="selectedId" aria-label="选择升仙会活动" style="width: 280px">
        <el-option v-for="item in activities" :key="item.id" :value="item.id" :label="item.label" />
      </el-select>
    </header>
    <p>最终巅峰榜 · 活动结束当晚采集</p>
    <p v-if="errorText" role="alert">{{ errorText }}</p>
    <FanxiuActivityRankingSection
      v-if="selectedId"
      :personal-rows="ranking?.entries || []" :personal-total="ranking?.entry_total || 0"
      :page="page" :loading="loading" :show-plane="false"
      :show-reward-columns="false"
      :personal-last-captured-at="ranking?.last_captured_at || ''"
      score-label="积分" score-per-reward-label="积分/天资丹"
      personal-empty-text="本期最终巅峰榜尚未采集"
      @page-change="page = $event; loadRankings()"
    />
  </section>
</template>

<style scoped>
.shengxian-page { display: flex; flex-direction: column; gap: 12px; }
.activity-controls { display: flex; align-items: center; gap: 12px; }
</style>
