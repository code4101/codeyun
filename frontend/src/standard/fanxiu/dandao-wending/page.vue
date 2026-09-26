<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import {
  collectFanxiuExchangeActivity,
  getFanxiuExchangeActivityRankings,
  getFanxiuExchangeActivitySnapshot,
  getFanxiuExchangeActivityTasks,
  type FanxiuExchangeActivityDetail,
  type FanxiuExchangeActivitySnapshot,
  type FanxiuExchangeActivitySummary,
  type FanxiuExchangeActivityTaskSnapshot,
  type FanxiuExchangeRankingItem,
} from '@/api/fanxiu/activities';
import FanxiuActivityRankingSection from '@/standard/fanxiu/components/FanxiuActivityRankingSection.vue'
import FanxiuActivityTaskMilestoneTable from '@/standard/fanxiu/components/FanxiuActivityTaskMilestoneTable.vue'
import FanxiuActivityToolbar from '@/standard/fanxiu/components/FanxiuActivityToolbar.vue'
import { formatActivityUpdatedAt } from '@/standard/fanxiu/components/activityStatus'
import { useFanxiuActivityRefresh } from '@/standard/fanxiu/components/useFanxiuActivityRefresh'
import { formatChineseCompactNumber } from '@/utils/numberFormat'
import { DANDAO_WENDING_ACTIVITY_TYPE, DANDAO_WENDING_OFFICIAL_NAME } from './model'

const props = defineProps<{ embedded?: boolean; initialSnapshot?: FanxiuExchangeActivitySnapshot | null }>()

const loading = ref(false)
const collecting = ref(false)
const errorText = ref('')
const activities = ref<FanxiuExchangeActivitySummary[]>([])
const selectedActivityId = ref('')
const activity = ref<FanxiuExchangeActivityDetail | null>(null)
const taskSnapshot = ref<FanxiuExchangeActivityTaskSnapshot | null>(null)
const rankings = ref<FanxiuExchangeRankingItem[]>([])
const rankingCapturedAt = ref('')

const currentScore = computed(() => {
  const selfScore = rankings.value.find(row => row.is_self)?.score
  if (selfScore != null) return selfScore
  const items = taskSnapshot.value?.items || []
  return items.length ? Math.max(...items.map(row => row.progress)) : null
})
const nextTask = computed(() => (
  (taskSnapshot.value?.items || []).find(row => currentScore.value != null && row.target > currentScore.value) || null
))

const { canCollect, maybeAutoCollect } = useFanxiuActivityRefresh({
  activity,
  capturedAts: () => [activity.value?.captured_at, taskSnapshot.value?.captured_at, rankingCapturedAt.value],
  collectSilently: () => collectFromGame(false),
})

async function loadSnapshot(activityId?: string) {
  const result = !activityId && props.initialSnapshot
    ? props.initialSnapshot
    : await getFanxiuExchangeActivitySnapshot(DANDAO_WENDING_ACTIVITY_TYPE, activityId)
  activities.value = result.activities
  activity.value = result.selected_activity || null
  selectedActivityId.value = activity.value?.id || ''
  errorText.value = activity.value ? '' : `暂无${DANDAO_WENDING_OFFICIAL_NAME}活动实例`
}

async function loadDetails() {
  taskSnapshot.value = null
  rankings.value = []
  rankingCapturedAt.value = ''
  if (!selectedActivityId.value) return
  const [tasks, personal] = await Promise.allSettled([
    getFanxiuExchangeActivityTasks(DANDAO_WENDING_ACTIVITY_TYPE, selectedActivityId.value),
    getFanxiuExchangeActivityRankings(
      DANDAO_WENDING_ACTIVITY_TYPE,
      selectedActivityId.value,
      1,
      100,
      'personal',
    ),
  ])
  if (tasks.status === 'fulfilled') taskSnapshot.value = tasks.value
  if (personal.status === 'fulfilled') {
    rankings.value = personal.value.items
    rankingCapturedAt.value = personal.value.last_captured_at || ''
  }
  errorText.value = [tasks, personal].flatMap(result => result.status === 'rejected'
    ? [result.reason?.response?.data?.detail || result.reason?.message || '读取失败'] : []).join('；')
}

async function loadPage(activityId?: string) {
  loading.value = true
  try {
    await loadSnapshot(activityId)
    await loadDetails()
  } catch (error: any) {
    errorText.value = error?.response?.data?.detail || error?.message || `读取${DANDAO_WENDING_OFFICIAL_NAME}数据失败`
  } finally {
    loading.value = false
  }
}

async function collectFromGame(showFeedback = true) {
  if (!activity.value || !canCollect.value || collecting.value) return
  collecting.value = true
  try {
    activity.value = await collectFanxiuExchangeActivity(
      DANDAO_WENDING_ACTIVITY_TYPE,
      activity.value.id,
    )
    await loadDetails()
    if (showFeedback) ElMessage.success(`已更新${DANDAO_WENDING_OFFICIAL_NAME}任务与榜单`)
  } catch (error: any) {
    if (showFeedback) ElMessage.warning(error?.response?.data?.detail || error?.message || '更新失败')
  } finally {
    collecting.value = false
  }
}

watch(selectedActivityId, value => {
  if (value && value !== activity.value?.id) void loadPage(value)
})

onMounted(async () => {
  await loadPage()
  maybeAutoCollect()
})
</script>

<template>
  <div class="dandao-page" :class="{ 'is-embedded': embedded }">
    <FanxiuActivityToolbar
      v-model="selectedActivityId"
      :activities="activities"
      :can-collect="canCollect"
      :collect-loading="collecting"
      :collect-disabled="loading"
      @collect="collectFromGame()"
    >
      <slot name="activity-type-control" />
    </FanxiuActivityToolbar>

    <div v-loading="loading" class="content">
      <el-alert v-if="errorText" :title="errorText" type="warning" :closable="false" show-icon />

      <section v-if="activity" class="task-section">
        <div class="section-heading">
          <h3>熟练度任务</h3>
          <span>
            当前炼丹熟练度 {{ currentScore == null ? '尚未读取' : formatChineseCompactNumber(currentScore) }}
            <template v-if="nextTask">
              ，距下一档还差 {{ formatChineseCompactNumber(nextTask.target - (currentScore ?? 0)) }}
            </template>
            <template v-else-if="taskSnapshot?.items.length && currentScore != null">，已达到全部任务档</template>
            <template v-if="taskSnapshot?.captured_at">
              ，最后读取 {{ formatActivityUpdatedAt(taskSnapshot.captured_at) }}
            </template>
          </span>
        </div>
        <el-alert
          v-if="taskSnapshot && !taskSnapshot.complete"
          :title="taskSnapshot.reason || '本期任务尚未完整读取'"
          type="warning"
          :closable="false"
        />
        <FanxiuActivityTaskMilestoneTable
          :rows="taskSnapshot?.items || []"
          :current="currentScore ?? 0"
          target-label="累计炼丹熟练度"
          empty-text="尚未读取到本期熟练度任务"
        />
      </section>

      <FanxiuActivityRankingSection
        v-if="activity"
        :personal-rows="rankings"
        :show-plane="false"
        score-label="炼丹熟练度"
        :personal-total="rankings.filter(row => row.has_player).length"
        :page-size="100"
        :personal-last-captured-at="rankingCapturedAt"
        :loading="loading"
        personal-empty-text="尚未参与或个人榜尚未加载"
      />
    </div>
  </div>
</template>

<style scoped>
.dandao-page,
.content,
.task-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.dandao-page:not(.is-embedded) {
  padding: 20px;
}

.content {
  min-height: 180px;
}

.task-section {
  align-items: flex-start;
}

.section-heading {
  display: flex;
  align-items: baseline;
  gap: 12px;
}

.section-heading h3 {
  margin: 0;
}

.section-heading span {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

@media (max-width: 720px) {
  .section-heading {
    align-items: flex-start;
    flex-direction: column;
    gap: 7px;
  }
}
</style>
