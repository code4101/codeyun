<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import {
  collectFanxiuExchangeActivity,
  getFanxiuExchangeActivityRankings,
  getFanxiuExchangeActivitySnapshot,
  getFanxiuYuandingSanshengTasks,
  type FanxiuExchangeActivityDetail,
  type FanxiuExchangeActivitySummary,
  type FanxiuExchangeRankingItem,
  type FanxiuYuandingSanshengTaskMilestone,
} from '@/api/fanxiu'
import FanxiuActivityToolbar from '@/standard/fanxiu/components/FanxiuActivityToolbar.vue'
import FanxiuActivityRankingSection from '@/standard/fanxiu/components/FanxiuActivityRankingSection.vue'
import { useFanxiuActivityRefresh } from '@/standard/fanxiu/components/useFanxiuActivityRefresh'
import { formatChineseCompactNumber } from '@/utils/numberFormat'

defineProps<{ embedded?: boolean }>()

const ACTIVITY_TYPE = 'yuanding-sansheng'
const loading = ref(false)
const rankingLoading = ref(false)
const collecting = ref(false)
const errorText = ref('')
const activities = ref<FanxiuExchangeActivitySummary[]>([])
const selectedActivityId = ref('')
const activity = ref<FanxiuExchangeActivityDetail | null>(null)
const rankings = ref<FanxiuExchangeRankingItem[]>([])
const groupRankings = ref<FanxiuExchangeRankingItem[]>([])
const tasks = ref<FanxiuYuandingSanshengTaskMilestone[]>([])
const rankingCapturedAt = ref('')
const groupCapturedAt = ref('')

const currentScore = computed(() => rankings.value.find(row => row.is_self)?.score ?? 0)
const nextTask = computed(() => tasks.value.find(row => row.target > currentScore.value) || null)

const { canCollect, maybeAutoCollect } = useFanxiuActivityRefresh({
  activity,
  collectSilently: () => collectFromGame(false),
})

async function loadSnapshot(activityId?: string) {
  loading.value = true
  try {
    const result = await getFanxiuExchangeActivitySnapshot(ACTIVITY_TYPE, activityId)
    activities.value = result.activities
    activity.value = result.selected_activity || null
    selectedActivityId.value = activity.value?.id || ''
    errorText.value = activity.value ? '' : '暂无缘定三生活动实例'
  } catch (error: any) {
    errorText.value = error?.response?.data?.detail || error?.message || '读取缘定三生活动失败'
  } finally {
    loading.value = false
  }
}

async function loadDetails() {
  if (!selectedActivityId.value) return
  rankingLoading.value = true
  try {
    const [personal, group, taskResult] = await Promise.all([
      getFanxiuExchangeActivityRankings(ACTIVITY_TYPE, selectedActivityId.value, 1, 100, 'personal'),
      getFanxiuExchangeActivityRankings(ACTIVITY_TYPE, selectedActivityId.value, 1, 100, 'plane'),
      getFanxiuYuandingSanshengTasks(selectedActivityId.value),
    ])
    rankings.value = personal.items
    groupRankings.value = group.items
    tasks.value = taskResult.items
    rankingCapturedAt.value = personal.last_captured_at || ''
    groupCapturedAt.value = group.last_captured_at || ''
  } catch (error: any) {
    errorText.value = error?.response?.data?.detail || error?.message || '读取缘定三生数据失败'
  } finally {
    rankingLoading.value = false
  }
}

async function collectFromGame(showFeedback = true) {
  if (!activity.value || collecting.value) return
  collecting.value = true
  try {
    activity.value = await collectFanxiuExchangeActivity(ACTIVITY_TYPE, activity.value.id)
    await loadDetails()
    if (showFeedback) ElMessage.success('已从游戏更新联姻榜单')
  } catch (error: any) {
    if (showFeedback) ElMessage.warning(error?.response?.data?.detail || error?.message || '更新榜单失败')
  } finally {
    collecting.value = false
  }
}

watch(selectedActivityId, value => {
  if (value && value !== activity.value?.id) {
    void loadSnapshot(value).then(loadDetails)
  }
})

onMounted(async () => {
  await loadSnapshot()
  await loadDetails()
  maybeAutoCollect()
})
</script>

<template>
  <div class="yuanding-page" :class="{ 'is-embedded': embedded }">
    <FanxiuActivityToolbar
      v-model="selectedActivityId"
      :activities="activities"
      :can-collect="canCollect"
      :collect-loading="collecting"
      :collect-disabled="loading || rankingLoading"
      @collect="collectFromGame()"
    >
      <slot name="activity-type-control" />
    </FanxiuActivityToolbar>

    <div v-loading="loading || rankingLoading" class="content">
      <el-alert v-if="errorText" :title="errorText" type="warning" :closable="false" show-icon />

      <section v-if="activity" class="task-section">
        <div class="section-heading">
          <h3>评分任务</h3>
          <span>
            当前 {{ formatChineseCompactNumber(currentScore) }}
            <template v-if="nextTask">，距下一档还差 {{ formatChineseCompactNumber(nextTask.target - currentScore) }}</template>
            <template v-else>，已达到全部档位</template>
          </span>
        </div>
        <div class="table-shell">
          <table>
            <thead><tr><th>档次</th><th>累计联姻评分</th><th>天资丹</th><th>标记</th></tr></thead>
            <tbody>
              <tr v-for="row in tasks" :key="row.task_id" :class="{ reached: row.target <= currentScore }">
                <td>{{ row.order }}</td>
                <td>{{ formatChineseCompactNumber(row.target) }}</td>
                <td>{{ row.talent_pill_count || '—' }}</td>
                <td>{{ row.must_get ? '必拿' : '' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <FanxiuActivityRankingSection
        v-if="activity"
        :personal-rows="rankings"
        :plane-rows="groupRankings"
        :show-plane="true"
        score-label="联姻评分"
        score-per-reward-label="丹均评分"
        plane-title="分组排名"
        personal-empty-text="尚未参与或个人榜尚未加载"
        plane-empty-text="分组榜尚未加载"
        :personal-last-captured-at="rankingCapturedAt"
        :plane-last-captured-at="groupCapturedAt"
      />
    </div>
  </div>
</template>

<style scoped>
.yuanding-page { display: flex; flex-direction: column; gap: 14px; padding: 20px; }
.yuanding-page.is-embedded { padding: 0; }
.content { min-height: 180px; }
.section-heading h3 { margin: 0; }
.section-heading span { color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.6; }
.task-section { margin-bottom: 20px; }
.section-heading { display: flex; align-items: baseline; gap: 12px; margin-bottom: 10px; }
.table-shell { max-width: 100%; overflow-x: auto; }
table { width: max-content; min-width: 520px; border-collapse: collapse; font-size: 14px; }
th, td { padding: 9px 14px; border-bottom: 1px solid var(--el-border-color-lighter); text-align: right; white-space: nowrap; }
th { color: var(--el-text-color-secondary); font-weight: 500; background: var(--el-fill-color-light); }
tr.reached td { color: var(--el-color-success); }
</style>
