<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'

import {
  getFanxiuExchangeActivityRankings,
  getFanxiuExchangeActivitySnapshot,
  type FanxiuExchangeActivitySummary,
  type FanxiuExchangeRankingPage,
} from '@/api/fanxiu/activities';
import FanxiuActivityRankingSection from '../components/FanxiuActivityRankingSection.vue'

defineProps<{ embedded?: boolean }>()

const ACTIVITY_TYPE = 'shequn-lingchong'
const activities = ref<FanxiuExchangeActivitySummary[]>([])
const selectedId = ref('')
const alliance = ref<FanxiuExchangeRankingPage | null>(null)
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
    const result = await getFanxiuExchangeActivityRankings(
      ACTIVITY_TYPE,
      selectedId.value,
      page.value,
      20,
      'alliance',
    )
    if (current === requestId) alliance.value = result
  } catch (error: any) {
    if (current === requestId) {
      errorText.value = error?.response?.data?.detail || error?.message || '读取社团榜失败'
    }
  } finally {
    if (current === requestId) loading.value = false
  }
}

watch(selectedId, () => {
  page.value = 1
  alliance.value = null
  void loadRankings()
})

onMounted(async () => {
  try {
    const snapshot = await getFanxiuExchangeActivitySnapshot(ACTIVITY_TYPE)
    activities.value = snapshot.activities
    selectedId.value = snapshot.selected_activity?.id || snapshot.activities[0]?.id || ''
  } catch (error: any) {
    errorText.value = error?.response?.data?.detail || error?.message || '读取社团灵宠活动失败'
  }
})
</script>

<template>
  <section class="shequn-lingchong-page">
    <header class="activity-controls">
      <slot name="activity-type-control"><h3>社团灵宠</h3></slot>
      <el-select v-model="selectedId" aria-label="选择社团灵宠活动" style="width: 280px">
        <el-option v-for="item in activities" :key="item.id" :value="item.id" :label="item.label" />
      </el-select>
    </header>
    <p v-if="errorText" role="alert">{{ errorText }}</p>
    <p v-else-if="alliance && !alliance.loaded_entry_count">
      活动已入库；实时社团名次尚未采集，以下显示已知奖励档次。
    </p>
    <FanxiuActivityRankingSection
      v-if="selectedId"
      :personal-rows="alliance?.items || []"
      :personal-total="alliance?.total || 0"
      :page="page"
      :loading="loading"
      :personal-last-captured-at="alliance?.last_captured_at || ''"
      :show-plane="false"
      score-label="资质积分"
      score-per-reward-label="积分/天资丹"
      personal-empty-text="暂无社团榜名次"
      @page-change="page = $event; loadRankings()"
    />
  </section>
</template>

<style scoped>
.shequn-lingchong-page { display: flex; flex-direction: column; gap: 12px; }
.activity-controls { display: flex; align-items: center; gap: 12px; }
h3, p { margin: 0; }
</style>
