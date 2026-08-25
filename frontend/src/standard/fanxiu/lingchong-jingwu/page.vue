<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import {
  collectFanxiuExchangeActivity,
  collectFanxiuLingchongJingwuResources,
  getFanxiuExchangeActivityRankings,
  getFanxiuExchangeActivitySnapshot,
  getFanxiuLingchongJingwuResources,
  getFanxiuLingchongJingwuTasks,
  type FanxiuExchangeActivityDetail,
  type FanxiuExchangeActivitySummary,
  type FanxiuExchangeRankingItem,
  type FanxiuLingchongJingwuResourceSnapshot,
  type FanxiuLingchongJingwuTaskMilestone,
} from '@/api/fanxiu'
import FanxiuActivityRankingSection from '@/standard/fanxiu/components/FanxiuActivityRankingSection.vue'
import FanxiuActivityToolbar from '@/standard/fanxiu/components/FanxiuActivityToolbar.vue'
import FanxiuTalentPillMilestoneTable from '@/standard/fanxiu/components/FanxiuTalentPillMilestoneTable.vue'
import { formatActivityUpdatedAt } from '@/standard/fanxiu/components/activityStatus'
import { useFanxiuActivityRefresh } from '@/standard/fanxiu/components/useFanxiuActivityRefresh'
import { formatChineseCompactNumber } from '@/utils/numberFormat'

defineProps<{ embedded?: boolean }>()

const ACTIVITY_TYPE = 'lingchong-jingwu'
const loading = ref(false)
const rankingLoading = ref(false)
const collecting = ref(false)
const errorText = ref('')
const activities = ref<FanxiuExchangeActivitySummary[]>([])
const selectedActivityId = ref('')
const activity = ref<FanxiuExchangeActivityDetail | null>(null)
const resources = ref<FanxiuLingchongJingwuResourceSnapshot | null>(null)
const tasks = ref<FanxiuLingchongJingwuTaskMilestone[]>([])
const taskSnapshot = ref<Awaited<ReturnType<typeof getFanxiuLingchongJingwuTasks>> | null>(null)
const personalRows = ref<FanxiuExchangeRankingItem[]>([])
const planeRows = ref<FanxiuExchangeRankingItem[]>([])
const personalCapturedAt = ref('')
const planeCapturedAt = ref('')

const currentTaskProgress = computed(() => Math.max(0, ...tasks.value.map(row => row.progress)))
const petTypeLabels: Record<number, string> = {
  1: '神兽',
  2: '仙兽',
  3: '灵兽',
  4: '妖兽',
  5: '珍兽',
}

function aptitudeDescription(row: FanxiuLingchongJingwuResourceSnapshot['items'][number]) {
  const values = Object.entries(row.aptitude_gain_by_pet_type)
    .filter(([, value]) => value > 0)
    .map(([type, value]) => `${petTypeLabels[Number(type)] || `类型${type}`} +${value}`)
  return values.join('、') || '无资质增量'
}

const { canCollect, maybeAutoCollect } = useFanxiuActivityRefresh({
  activity,
  capturedAts: () => [resources.value?.captured_at, personalCapturedAt.value, planeCapturedAt.value],
  collectSilently: () => collectFromGame(false),
})

async function loadSnapshot(activityId?: string) {
  loading.value = true
  try {
    const result = await getFanxiuExchangeActivitySnapshot(ACTIVITY_TYPE, activityId)
    activities.value = result.activities
    activity.value = result.selected_activity || null
    selectedActivityId.value = activity.value?.id || ''
    errorText.value = activity.value ? '' : '暂无8跨灵宠竞武活动实例'
  } catch (error: any) {
    errorText.value = error?.response?.data?.detail || error?.message || '读取灵宠竞武活动失败'
  } finally {
    loading.value = false
  }
}

async function loadDetails() {
  if (!selectedActivityId.value) return
  rankingLoading.value = true
  try {
    const [taskResult, resourceResult, personalResult, planeResult] = await Promise.all([
      getFanxiuLingchongJingwuTasks(selectedActivityId.value),
      getFanxiuLingchongJingwuResources(selectedActivityId.value),
      getFanxiuExchangeActivityRankings(ACTIVITY_TYPE, selectedActivityId.value, 1, 100, 'personal'),
      getFanxiuExchangeActivityRankings(ACTIVITY_TYPE, selectedActivityId.value, 1, 100, 'plane'),
    ])
    taskSnapshot.value = taskResult
    tasks.value = taskResult.items
    resources.value = resourceResult
    personalRows.value = personalResult.items
    planeRows.value = planeResult.items
    personalCapturedAt.value = personalResult.last_captured_at || ''
    planeCapturedAt.value = planeResult.last_captured_at || ''
  } catch (error: any) {
    errorText.value = error?.response?.data?.detail || error?.message || '读取灵宠竞武数据失败'
  } finally {
    rankingLoading.value = false
  }
}

async function collectFromGame(showFeedback = true) {
  if (!activity.value || !canCollect.value || collecting.value) return
  collecting.value = true
  try {
    const [activityResult, resourceResult] = await Promise.all([
      collectFanxiuExchangeActivity(ACTIVITY_TYPE, activity.value.id),
      collectFanxiuLingchongJingwuResources(activity.value.id),
    ])
    activity.value = activityResult
    resources.value = resourceResult
    await loadDetails()
    if (showFeedback) ElMessage.success('已更新灵宠竞武资源、任务与榜单')
  } catch (error: any) {
    if (showFeedback) ElMessage.warning(error?.response?.data?.detail || error?.message || '更新失败')
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
  <div class="lingchong-page" :class="{ 'is-embedded': embedded }">
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

      <section v-if="activity" class="section-block">
        <div class="section-heading">
          <h3>资源现状</h3>
          <span>
            饲灵丸 {{ formatChineseCompactNumber(resources?.total_count || 0) }} 个
            <template v-if="resources?.captured_at">，最后读取 {{ formatActivityUpdatedAt(resources.captured_at) }}</template>
          </span>
        </div>
        <el-alert
          v-if="resources && !resources.complete"
          :title="resources.reason || '资源尚未从游戏完整读取'"
          type="info"
          :closable="false"
        />
        <div class="table-shell">
          <table>
            <thead><tr><th>资源</th><th>持有</th><th>适用灵兽及单个资质</th></tr></thead>
            <tbody>
              <tr v-for="row in resources?.items || []" :key="row.item_id">
                <td>{{ row.name }}</td>
                <td class="number-cell">{{ formatChineseCompactNumber(row.count) }}</td>
                <td>{{ aptitudeDescription(row) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section v-if="activity" class="section-block">
        <div class="section-heading">
          <h3>灵兽任务</h3>
          <span>当前累计资质 {{ formatChineseCompactNumber(currentTaskProgress) }}</span>
        </div>
        <el-alert
          v-if="taskSnapshot && !taskSnapshot.complete"
          :title="taskSnapshot.reason"
          type="warning"
          :closable="false"
        />
        <FanxiuTalentPillMilestoneTable
          :rows="tasks"
          :current="currentTaskProgress"
          target-label="累计资质"
          per-pill-label="丹均资质"
          empty-text="尚未读取到本期天资丹任务"
        />
      </section>

      <FanxiuActivityRankingSection
        v-if="activity"
        :personal-rows="personalRows"
        :plane-rows="planeRows"
        :show-plane="true"
        score-label="资质积分"
        score-per-reward-label="每颗天资丹所需积分"
        :personal-total="personalRows.filter(row => row.has_player).length"
        :page-size="100"
        :personal-last-captured-at="personalCapturedAt"
        :plane-last-captured-at="planeCapturedAt"
        :loading="rankingLoading"
        plane-title="位面情况"
        plane-subject-label="位面"
      />
    </div>
  </div>
</template>

<style scoped>
.lingchong-page,
.content,
.section-block { display: flex; flex-direction: column; gap: 12px; }
.lingchong-page:not(.is-embedded) { padding: 20px; }
.section-heading { display: flex; align-items: baseline; gap: 12px; }
.section-heading h3 { margin: 0; }
.section-heading span { color: var(--el-text-color-secondary); }
.table-shell { max-width: 100%; overflow-x: auto; }
table { min-width: 680px; border-collapse: collapse; }
th, td { padding: 9px 12px; border-bottom: 1px solid var(--el-border-color-lighter); text-align: left; }
th { color: var(--el-text-color-secondary); font-weight: 500; }
.number-cell { text-align: right; font-variant-numeric: tabular-nums; }
.empty-cell { color: var(--el-text-color-secondary); text-align: center; }
</style>
