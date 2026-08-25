<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import {
  collectFanxiuExchangeActivity,
  getFanxiuExchangeActivityRankings,
  getFanxiuExchangeActivitySnapshot,
  getFanxiuExchangeActivityTasks,
  type FanxiuExchangeActivityDetail,
  type FanxiuExchangeActivitySummary,
  type FanxiuExchangeActivityTaskSnapshot,
  type FanxiuExchangeRankingItem,
} from '@/api/fanxiu'
import FanxiuActivityRankingSection from '@/standard/fanxiu/components/FanxiuActivityRankingSection.vue'
import FanxiuActivityTaskMilestoneTable from '@/standard/fanxiu/components/FanxiuActivityTaskMilestoneTable.vue'
import FanxiuActivityToolbar from '@/standard/fanxiu/components/FanxiuActivityToolbar.vue'
import { formatActivityUpdatedAt } from '@/standard/fanxiu/components/activityStatus'
import { useFanxiuActivityRefresh } from '@/standard/fanxiu/components/useFanxiuActivityRefresh'
import { formatChineseCompactNumber } from '@/utils/numberFormat'
import { DANDAO_WENDING_ACTIVITY_TYPE, DANDAO_WENDING_OFFICIAL_NAME } from './model'

defineProps<{ embedded?: boolean }>()

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
  return Math.max(0, ...(taskSnapshot.value?.items || []).map(row => row.progress))
})
const nextTask = computed(() => (
  (taskSnapshot.value?.items || []).find(row => row.target > currentScore.value) || null
))

const { canCollect, maybeAutoCollect } = useFanxiuActivityRefresh({
  activity,
  capturedAts: () => [activity.value?.captured_at, taskSnapshot.value?.captured_at, rankingCapturedAt.value],
  collectSilently: () => collectFromGame(false),
})

async function loadSnapshot(activityId?: string) {
  const result = await getFanxiuExchangeActivitySnapshot(DANDAO_WENDING_ACTIVITY_TYPE, activityId)
  activities.value = result.activities
  activity.value = result.selected_activity || null
  selectedActivityId.value = activity.value?.id || ''
  errorText.value = activity.value ? '' : `暂无${DANDAO_WENDING_OFFICIAL_NAME}活动实例`
}

async function loadDetails() {
  if (!selectedActivityId.value) return
  const [tasks, personal] = await Promise.all([
    getFanxiuExchangeActivityTasks(DANDAO_WENDING_ACTIVITY_TYPE, selectedActivityId.value),
    getFanxiuExchangeActivityRankings(
      DANDAO_WENDING_ACTIVITY_TYPE,
      selectedActivityId.value,
      1,
      100,
      'personal',
    ),
  ])
  taskSnapshot.value = tasks
  rankings.value = personal.items
  rankingCapturedAt.value = personal.last_captured_at || ''
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
            当前炼丹熟练度 {{ formatChineseCompactNumber(currentScore) }}
            <template v-if="nextTask">
              ，距下一档还差 {{ formatChineseCompactNumber(nextTask.target - currentScore) }}
            </template>
            <template v-else>，已达到全部任务档</template>
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
          :current="currentScore"
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
