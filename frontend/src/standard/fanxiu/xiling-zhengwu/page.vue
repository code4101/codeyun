<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import {
  getFanxiuExchangeActivitySnapshot,
  getFanxiuExchangeActivityRankings,
  type FanxiuExchangeActivitySummary,
  type FanxiuExchangeActivitySnapshot,
  type FanxiuExchangeRankingPage,
} from '@/api/fanxiu/activities';
import FanxiuActivityRankingSection from '../components/FanxiuActivityRankingSection.vue'

const props = defineProps<{ embedded?: boolean; initialSnapshot?: FanxiuExchangeActivitySnapshot | null }>()
const activities = ref<FanxiuExchangeActivitySummary[]>([])
const selectedId = ref('')
const personal = ref<FanxiuExchangeRankingPage | null>(null)
const plane = ref<FanxiuExchangeRankingPage | null>(null)
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
    const result = await Promise.all([
      getFanxiuExchangeActivityRankings('xiling-zhengwu', selectedId.value, page.value, 20, 'personal'),
      getFanxiuExchangeActivityRankings('xiling-zhengwu', selectedId.value, 1, 100, 'plane'),
    ])
    if (current !== requestId) return
    ;[personal.value, plane.value] = result
  } catch (error: any) {
    if (current === requestId) errorText.value = error?.response?.data?.detail || error?.message || '读取榜单失败'
  } finally {
    if (current === requestId) loading.value = false
  }
}

watch(selectedId, () => {
  page.value = 1
  personal.value = null
  plane.value = null
  void loadRankings()
})
onMounted(async () => {
  try {
    const snapshot = props.initialSnapshot ?? await getFanxiuExchangeActivitySnapshot('xiling-zhengwu')
    activities.value = snapshot.activities
    selectedId.value = snapshot.selected_activity?.id || snapshot.activities[0]?.id || ''
  } catch (error: any) {
    errorText.value = error?.response?.data?.detail || error?.message || '读取活动失败'
  }
})
</script>

<template>
  <section class="xiling-page">
    <header class="activity-controls">
      <slot name="activity-type-control"><h3>洗灵证武</h3></slot>
      <el-select v-model="selectedId" aria-label="选择洗灵证武活动" style="width: 280px">
        <el-option v-for="item in activities" :key="item.id" :value="item.id" :label="item.label" />
      </el-select>
    </header>
    <p v-if="errorText" role="alert">{{ errorText }}</p>
    <p v-else-if="personal && !personal.loaded_entry_count">活动已入库；实时名次尚未采集，以下显示已知奖励档次。</p>
    <FanxiuActivityRankingSection
      v-if="selectedId"
      :personal-rows="personal?.items || []"
      :plane-rows="plane?.items || []"
      :personal-total="personal?.total || 0"
      :page="page"
      :loading="loading"
      :personal-last-captured-at="personal?.last_captured_at || ''"
      :plane-last-captured-at="plane?.last_captured_at || ''"
      score-label="积分"
      score-per-reward-label="积分/天资丹"
      @page-change="page = $event; loadRankings()"
    />
  </section>
</template>

<style scoped>
.xiling-page { display: flex; flex-direction: column; gap: 12px; }
.activity-controls { display: flex; align-items: center; gap: 12px; }
h3, p { margin: 0; }
</style>
