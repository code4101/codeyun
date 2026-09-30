<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  getFanxiuScheduleRankings,
  type FanxiuExchangeActivitySnapshot,
  type FanxiuScheduleRankingSnapshot,
} from '@/api/fanxiu/activities';
import ResourceRankingPage from '../resource-ranking/page.vue'
import TopActivityPage from '../top-activity/page.vue'

type GameplayActivityType =
  | 'yunmeng-trial'
  | 'xianyuan-duokui'
  | 'xutian-palace'
  | 'magic-invasion'
  | 'beast-abyss'
  | 'tiandi-yiju'
  | 'shengxian-hui'

type ResourceActivityType =
  | 'xiling-zhengwu'
  | 'lingzhuang-huadao'
  | 'yaochi-flower-festival'
  | 'yuanding-sansheng'
  | 'lingchong-jingwu'
  | 'shequn-lingchong'
  | 'lianti-faxiang'
  | 'dandao-wending'
  | 'peakrace'

const gameplayTypes = new Set<GameplayActivityType>([
  'yunmeng-trial',
  'xianyuan-duokui',
  'xutian-palace',
  'magic-invasion',
  'beast-abyss',
  'tiandi-yiju',
  'shengxian-hui',
])
const resourceTypes = new Set<ResourceActivityType>([
  'xiling-zhengwu',
  'lingzhuang-huadao',
  'yaochi-flower-festival',
  'yuanding-sansheng',
  'lingchong-jingwu',
  'shequn-lingchong',
  'lianti-faxiang',
  'dandao-wending',
  'peakrace',
])

const loading = ref(true)
const errorMessage = ref('')
const schedule = ref<FanxiuScheduleRankingSnapshot | null>(null)
const route = useRoute()
const router = useRouter()

const gameplayType = computed<GameplayActivityType | null>(() => {
  const value = String(schedule.value?.gameplay_rank.activity_type || '') as GameplayActivityType
  return gameplayTypes.has(value) ? value : null
})
const resourceType = computed<ResourceActivityType | null>(() => {
  const value = String(schedule.value?.resource_rank.activity_type || '') as ResourceActivityType
  return resourceTypes.has(value) ? value : null
})
const gameplaySnapshot = computed<FanxiuExchangeActivitySnapshot | null>(() => (
  gameplayType.value === schedule.value?.gameplay_rank.activity_type
    ? (schedule.value?.gameplay_rank.snapshot ?? null)
    : null
))

onMounted(async () => {
  try {
    if ('activity' in route.query) {
      const query = { ...route.query }
      delete query.activity
      void router.replace({ query })
    }
    schedule.value = await getFanxiuScheduleRankings()
  } catch (error: any) {
    errorMessage.value = error?.response?.data?.detail || error?.message || '读取日程失败'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <main class="schedule-page" v-loading="loading">
    <header class="page-header">
      <h2>日程</h2>
    </header>

    <div v-if="errorMessage" class="page-error">{{ errorMessage }}</div>

    <template v-else-if="schedule">
      <section class="ranking-section">
        <h3>玩法榜</h3>
        <TopActivityPage
          embedded
          :initial-activity-type="gameplayType"
          :initial-snapshot="gameplaySnapshot"
        />
      </section>

      <section class="ranking-section">
        <h3>资源榜</h3>
        <ResourceRankingPage
          v-if="resourceType"
          embedded
          :initial-activity-type="resourceType"
          :initial-snapshot="schedule.resource_rank.snapshot ?? null"
        />
        <p v-else>暂无资源榜活动记录</p>
      </section>
    </template>
  </main>
</template>

<style scoped>
.schedule-page {
  display: flex;
  flex-direction: column;
  gap: 22px;
  box-sizing: border-box;
  min-height: 100%;
  padding: 20px;
}

.page-header h2,
.ranking-section h3 {
  margin: 0;
}

.ranking-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.ranking-section + .ranking-section {
  padding-top: 22px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.page-error {
  padding: 22px 0;
  color: var(--el-text-color-secondary);
}

.page-error {
  color: var(--el-color-danger);
}
</style>
