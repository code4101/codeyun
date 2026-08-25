<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import FanxiuActivityRankingSection from '@/standard/fanxiu/components/FanxiuActivityRankingSection.vue'
import FanxiuActivityToolbar from '@/standard/fanxiu/components/FanxiuActivityToolbar.vue'
import RelationshipScatterPlot from '@/components/RelationshipScatterPlot.vue'
import { formatActivityUpdatedAt } from '@/standard/fanxiu/components/activityStatus'
import { useFanxiuActivityRefresh } from '@/standard/fanxiu/components/useFanxiuActivityRefresh'
import { formatChineseCompactNumber } from '@/utils/numberFormat'
import { estimateXForY } from '@/utils/relationshipAnalysis'

import {
  collectFanxiuLingzhuangStrengtheningSnapshot,
  collectFanxiuExchangeActivity,
  getFanxiuExchangeActivityRankings,
  getFanxiuExchangeActivitySnapshot,
  getFanxiuLingzhuangStrengtheningSnapshot,
  getFanxiuLingzhuangRelationshipSamples,
  recordFanxiuLingzhuangRelationshipSample,
  type FanxiuExchangeActivityDetail,
  type FanxiuExchangeActivitySummary,
  type FanxiuExchangeRankingItem,
  type FanxiuLingzhuangStrengtheningSnapshot,
  type RelationshipDataset,
} from '@/api/fanxiu'

defineProps<{ embedded?: boolean }>()

const ACTIVITY_TYPE = 'lingzhuang-huadao'
const loading = ref(false)
const rankingLoading = ref(false)
const collectingFromGame = ref(false)
const errorText = ref('')
const activities = ref<FanxiuExchangeActivitySummary[]>([])
const selectedActivityId = ref('')
const activity = ref<FanxiuExchangeActivityDetail | null>(null)
const rankings = ref<FanxiuExchangeRankingItem[]>([])
const planeRankings = ref<FanxiuExchangeRankingItem[]>([])
const rankingLastCapturedAt = ref('')
const planeRankingLastCapturedAt = ref('')
const strengtheningLoading = ref(false)
const strengthening = ref<FanxiuLingzhuangStrengtheningSnapshot | null>(null)
const relationship = ref<RelationshipDataset | null>(null)

const {
  canCollect,
  maybeAutoCollect: maybeAutoCollectFromGame,
} = useFanxiuActivityRefresh({
  activity,
  capturedAts: () => [activity.value?.captured_at, strengthening.value?.captured_at],
  collectSilently: () => collectFromGame(false),
})
const totalMaterialCount = computed(() => {
  const rows = strengthening.value?.rows || []
  if (!rows.length || rows.some(row => row.initial.material_count == null)) return null
  return rows.reduce((total, row) => total + (row.initial.material_count || 0), 0)
})
const equipmentRewardTasks = computed(() =>
  (strengthening.value?.equipment_tasks || []).filter(row => row.talent_pill_count > 0),
)
const equipmentRewardAnalysis = computed(() => {
  let cumulativeTalentPills = 0
  return equipmentRewardTasks.value.map(task => {
    cumulativeTalentPills += task.talent_pill_count
    return {
      ...task,
      cumulativeTalentPills,
      materialPerTalentPill: cumulativeTalentPills > 0 ? task.target / cumulativeTalentPills : null,
    }
  })
})
const taskScorePoints = computed(() =>
  (relationship.value?.samples || [])
    .map(point => ({ x: point.x, y: point.values.task_score }))
    .filter(point => Number.isFinite(point.y)),
)
const scoreRoundProjections = computed(() => {
  let cumulativeScore = 0
  return (strengthening.value?.score_rounds || []).map(round => {
    cumulativeScore += round.target
    return {
      ...round,
      cumulativeScore,
      estimatedMaterial: estimateXForY(taskScorePoints.value, cumulativeScore),
    }
  })
})
const currentCumulativeScore = computed(() => {
  if (strengthening.value?.score_current == null) return null
  const currentRound = strengthening.value.score_round || 1
  const completedScore = (strengthening.value.score_rounds || [])
    .filter(round => round.round < currentRound)
    .reduce((total, round) => total + round.target, 0)
  return completedScore + strengthening.value.score_current
})

function uniqueChartPoints(points: Array<{ x: number; y: number; label?: string }>) {
  const unique = new Map<string, { x: number; y: number; label?: string }>()
  for (const point of points) {
    const key = `${point.x}:${point.y}`
    const previous = unique.get(key)
    unique.set(key, point.label || !previous ? point : previous)
  }
  return [...unique.values()].sort((left, right) => left.x - right.x)
}

function actualThresholdPoint(
  points: Array<{ x: number; y: number }>,
  targetY: number,
  label: string,
) {
  const sorted = [{ x: 0, y: 0 }, ...points].sort((left, right) => left.x - right.x)
  for (let index = 1; index < sorted.length; index += 1) {
    const previous = sorted[index - 1]
    const current = sorted[index]
    if (current.y < targetY || previous.y > targetY) continue
    if (current.y === previous.y) return { x: current.x, y: targetY, label }
    const ratio = (targetY - previous.y) / (current.y - previous.y)
    return {
      x: previous.x + (current.x - previous.x) * ratio,
      y: targetY,
      label,
    }
  }
  return null
}

const equipmentChartSeries = computed(() => {
  const current = strengthening.value?.equipment_current || 0
  let cumulativeReward = 0
  const observed = [{ x: 0, y: 0 }]
  const projected: Array<{ x: number; y: number }> = []
  for (const task of equipmentRewardAnalysis.value) {
    cumulativeReward = task.cumulativeTalentPills
    const point = { x: task.target, y: cumulativeReward }
    if (task.target <= current) observed.push(point)
    else projected.push(point)
  }
  const earned = equipmentRewardTasks.value
    .filter(task => task.target <= current)
    .reduce((total, task) => total + task.talent_pill_count, 0)
  if (current > observed[observed.length - 1].x) {
    observed.push({ x: current, y: earned })
  }
  const keyPoints = equipmentRewardAnalysis.value
    .filter(task => task.target <= current)
    .map(task => ({ x: task.target, y: task.cumulativeTalentPills }))
  const currentPoint = current > 0 ? { x: current, y: earned, label: '当前' } : undefined
  return [{ label: '累计天资丹', color: '#409eff', observed, keyPoints, currentPoint, projected }]
})
const scoreChartSeries = computed(() => {
  const observed = [{ x: 0, y: 0 }, ...taskScorePoints.value]
  const currentPoint = taskScorePoints.value.reduce<null | { x: number; y: number }>(
    (latest, point) => latest == null || point.x > latest.x ? point : latest,
    null,
  )
  const achievedRoundPoints = scoreRoundProjections.value
    .filter(round => currentPoint != null && round.cumulativeScore <= currentPoint.y)
    .map(round => actualThresholdPoint(taskScorePoints.value, round.cumulativeScore, `第${round.round}轮`))
    .filter((point): point is { x: number; y: number; label: string } => point != null)
  const projected = scoreRoundProjections.value
    .filter(round => round.estimatedMaterial != null && round.cumulativeScore > (currentPoint?.y || 0))
    .map(round => {
      return round.estimatedMaterial == null
        ? null
        : {
            x: Math.ceil(round.estimatedMaterial),
            y: round.cumulativeScore,
            label: `第${round.round}轮`,
          }
    })
    .filter((point): point is { x: number; y: number } => point != null)
  return [{
    label: '玄铁消耗',
    color: '#e6a23c',
    observed,
    keyPoints: uniqueChartPoints(achievedRoundPoints),
    currentPoint: currentPoint ? { ...currentPoint, label: '当前' } : undefined,
    projected,
    showProjectedLabels: true,
  }]
})

async function loadSnapshot(activityId?: string) {
  loading.value = true
  try {
    const result = await getFanxiuExchangeActivitySnapshot(ACTIVITY_TYPE, activityId)
    activities.value = result.activities
    activity.value = result.selected_activity || null
    selectedActivityId.value = activity.value?.id || ''
    errorText.value = activity.value ? '' : '暂无灵装化道活动实例'
  } catch (error: any) {
    errorText.value = error?.response?.data?.detail || error?.message || '读取灵装化道活动失败'
  } finally {
    loading.value = false
  }
}

async function loadRankings() {
  if (!selectedActivityId.value) return
  rankingLoading.value = true
  try {
    const [personal, plane] = await Promise.all([
      getFanxiuExchangeActivityRankings(ACTIVITY_TYPE, selectedActivityId.value, 1, 100, 'personal'),
      getFanxiuExchangeActivityRankings(ACTIVITY_TYPE, selectedActivityId.value, 1, 100, 'plane'),
    ])
    rankings.value = personal.items
    planeRankings.value = plane.items
    rankingLastCapturedAt.value = personal.last_captured_at || ''
    planeRankingLastCapturedAt.value = plane.last_captured_at || ''
  } catch (error: any) {
    errorText.value = error?.response?.data?.detail || error?.message || '读取灵装化道榜单失败'
  } finally {
    rankingLoading.value = false
  }
}

async function loadStrengthening() {
  strengtheningLoading.value = true
  try {
    strengthening.value = await getFanxiuLingzhuangStrengtheningSnapshot()
  } catch (error: any) {
    errorText.value = error?.response?.data?.detail || error?.message || '读取强化现状失败'
  } finally {
    strengtheningLoading.value = false
  }
}

async function loadRelationshipSamples() {
  if (!selectedActivityId.value) return
  relationship.value = await getFanxiuLingzhuangRelationshipSamples(selectedActivityId.value)
}

async function collectFromGame(showFeedback = true) {
  if (!activity.value || !canCollect.value || collectingFromGame.value) return
  collectingFromGame.value = true
  const failures: string[] = []
  let rankingsUpdated = false
  let strengtheningUpdated = false
  try {
    activity.value = await collectFanxiuExchangeActivity(ACTIVITY_TYPE, activity.value.id)
    await loadRankings()
    rankingsUpdated = true
  } catch (error: any) {
    failures.push(`榜单：${error?.response?.data?.detail || error?.message || '更新失败'}`)
  }
  try {
    strengthening.value = await collectFanxiuLingzhuangStrengtheningSnapshot(activity.value.id)
    if (!strengthening.value.complete) {
      failures.push('强化现状：部分数据未加载，已保留原读取时间')
    } else {
      strengtheningUpdated = true
    }
  } catch (error: any) {
    failures.push(`强化现状：${error?.response?.data?.detail || error?.message || '更新失败'}`)
  }
  if (rankingsUpdated && strengtheningUpdated) {
    try {
      relationship.value = await recordFanxiuLingzhuangRelationshipSample(activity.value.id)
    } catch (error: any) {
      failures.push(`关系样本：${error?.response?.data?.detail || error?.message || '记录失败'}`)
    }
  }
  collectingFromGame.value = false
  if (!showFeedback) return
  if (failures.length) {
    ElMessage.warning(`部分更新未完成：${failures.join('；')}`)
  } else {
    ElMessage.success('已从游戏更新全部数据')
  }
}

function formatLevel(level?: number | null, equipped?: boolean | null): string {
  if (equipped === false) return ''
  return level == null ? '—' : String(level)
}

function formatCount(count?: number | null): string {
  return count == null ? '—' : formatChineseCompactNumber(count)
}

function formatEstimatedMaterial(estimate: number | null): string {
  return estimate == null ? '—' : `约 ${formatChineseCompactNumber(Math.ceil(estimate))}`
}

watch(selectedActivityId, value => {
  if (value && value !== activity.value?.id) {
    void loadSnapshot(value).then(() => Promise.all([loadRankings(), loadRelationshipSamples()]))
  }
})

onMounted(async () => {
  await Promise.all([
    loadSnapshot().then(() => Promise.all([loadRankings(), loadRelationshipSamples()])),
    loadStrengthening(),
  ])
  maybeAutoCollectFromGame()
})
</script>

<template>
  <div class="lingzhuang-page" :class="{ 'is-embedded': embedded }">
    <FanxiuActivityToolbar
      v-model="selectedActivityId"
      :activities="activities"
      :can-collect="canCollect"
      :collect-loading="collectingFromGame"
      :collect-disabled="loading || rankingLoading || strengtheningLoading"
      @collect="collectFromGame()"
    >
      <slot name="activity-type-control" />
    </FanxiuActivityToolbar>

    <div v-loading="loading || rankingLoading" class="ranking-content">
      <el-alert
        v-if="errorText"
        :title="errorText"
        type="warning"
        :closable="false"
        show-icon
      />

      <section class="resource-reference">
        <div class="subsection-heading">
          <h4>强化现状</h4>
          <span v-if="strengthening?.captured_at">最后更新 {{ formatActivityUpdatedAt(strengthening.captured_at) }}</span>
        </div>
        <div v-loading="strengtheningLoading" class="table-shell strengthening-table-shell">
          <table class="resource-reference-table">
            <thead>
              <tr>
                <th class="number-cell">编号</th>
                <th>部位</th>
                <th>强化原料</th>
                <th class="number-cell">拥有</th>
                <th class="group-heading number-cell">初灵等级</th>
                <th class="group-heading number-cell">洞玄等级</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, index) in strengthening?.rows || []" :key="row.part">
                <td class="number-cell">{{ index + 1 }}</td>
                <td>{{ row.part }}</td>
                <td>{{ row.initial.material_name }}</td>
                <td class="number-cell amount-cell">{{ formatCount(row.initial.material_count) }}</td>
                <td class="level-cell number-cell">{{ formatLevel(row.initial.equipment_level, row.initial.equipped) }}</td>
                <td class="level-cell number-cell">{{ formatLevel(row.dongxuan.equipment_level, row.dongxuan.equipped) }}</td>
              </tr>
            </tbody>
            <tfoot>
              <tr>
                <td colspan="3" class="total-label">累计</td>
                <td class="number-cell amount-cell">{{ formatCount(totalMaterialCount) }}</td>
                <td></td>
                <td></td>
              </tr>
            </tfoot>
          </table>
        </div>

        <div v-if="equipmentRewardTasks.length || strengthening?.score_rounds.length" class="task-analyses">
          <div class="task-analysis-row">
            <div class="task-table-block">
              <div class="task-table-heading">
                <h5>装备任务</h5>
                <span v-if="strengthening?.equipment_current != null">已消耗 {{ formatCount(strengthening.equipment_current) }}</span>
              </div>
              <div class="table-shell">
                <table class="task-progress-table">
                  <thead>
                    <tr>
                      <th class="number-cell">档次</th>
                      <th class="number-cell">预计原料</th>
                      <th class="number-cell">天资丹</th>
                      <th class="number-cell">丹均原料</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="row in equipmentRewardAnalysis" :key="row.task_id">
                      <td class="number-cell">{{ row.order }}</td>
                      <td class="number-cell amount-cell">{{ formatCount(row.target) }}</td>
                      <td class="number-cell amount-cell">{{ formatCount(row.talent_pill_count) }}</td>
                      <td class="number-cell amount-cell">{{ formatCount(row.materialPerTalentPill) }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
            <div class="task-chart-block">
              <div class="chart-note">纵轴：累计天资丹</div>
              <RelationshipScatterPlot
                class="task-chart"
                x-label="玄铁消耗"
                y-label="累计天资丹"
                :x-data-max="equipmentRewardTasks[equipmentRewardTasks.length - 1]?.target || 0"
                :series="equipmentChartSeries"
              />
            </div>
          </div>

          <div class="task-analysis-row">
            <div class="task-table-block">
              <div class="task-table-heading">
                <h5>积分任务</h5>
                <span v-if="currentCumulativeScore != null">当前累计积分 {{ formatCount(currentCumulativeScore) }}</span>
              </div>
              <div class="table-shell">
                <table class="task-progress-table">
                  <thead>
                    <tr>
                      <th class="number-cell">轮次</th>
                      <th class="number-cell">目标总分</th>
                      <th class="number-cell">预计原料</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr
                      v-for="row in scoreRoundProjections"
                      :key="row.round"
                      :class="{ 'is-current-round': row.round === strengthening.score_round }"
                    >
                      <td class="number-cell">{{ row.round }}</td>
                      <td class="number-cell amount-cell">{{ formatCount(row.target) }}</td>
                      <td class="number-cell amount-cell">{{ formatEstimatedMaterial(row.estimatedMaterial) }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
            <div class="task-chart-block">
              <div class="chart-note">纵轴：累计灵装积分</div>
              <RelationshipScatterPlot
                class="task-chart"
                x-label="玄铁消耗"
                y-label="累计灵装积分"
                :x-data-max="scoreRoundProjections[scoreRoundProjections.length - 1]?.estimatedMaterial || 0"
                :series="scoreChartSeries"
              />
            </div>
          </div>
        </div>
      </section>

      <FanxiuActivityRankingSection
        v-if="activity"
        :personal-rows="rankings"
        :plane-rows="planeRankings"
        score-label="玄铁消耗"
        score-per-reward-label="丹均玄铁"
        :personal-last-captured-at="rankingLastCapturedAt"
        :plane-last-captured-at="planeRankingLastCapturedAt"
        plane-empty-text="尚未加载位面榜运行态数据"
      />

    </div>
  </div>
</template>

<style scoped>
.lingzhuang-page {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 20px;
}

.lingzhuang-page.is-embedded {
  padding: 0;
}

.ranking-content {
  min-height: 180px;
}

.section-block {
  margin-top: 18px;
}

.resource-reference {
  margin-top: 20px;
}

.strengthening-table-shell {
  min-height: 80px;
}

.task-analyses {
  display: flex;
  flex-direction: column;
  gap: 22px;
  margin-top: 18px;
}

.task-analysis-row {
  display: flex;
  align-items: flex-start;
  gap: 28px;
}

.task-table-block {
  flex: 0 0 auto;
  min-width: 0;
}

.task-chart-block {
  flex: 1 1 520px;
  max-width: 620px;
  min-width: 0;
}

.task-chart {
  flex: 1 1 520px;
  max-width: 620px;
  min-width: 360px;
}

.chart-note {
  margin-bottom: 2px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.task-table-heading {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 8px;
}

.task-table-heading h5 {
  margin: 0;
  font-size: 14px;
}

.task-table-heading span {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.section-heading {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin-bottom: 10px;
}

.section-heading h3 {
  margin: 0;
  font-size: 17px;
}

.section-heading span {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.subsection-heading {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin: 20px 0 10px;
}

.subsection-heading h4 {
  margin: 0;
  font-size: 15px;
}

.subsection-heading span {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.score-cell {
  font-weight: 600;
}

.table-shell {
  max-width: 100%;
  overflow-x: auto;
}

table {
  width: max-content;
  max-width: 100%;
  border-collapse: collapse;
  font-size: 14px;
}

th,
td {
  padding: 9px 14px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  text-align: left;
  white-space: nowrap;
}

th {
  color: var(--el-text-color-secondary);
  font-weight: 500;
  background: var(--el-fill-color-light);
}

.group-heading {
  border-left: 1px solid var(--el-border-color-lighter);
  text-align: center;
}

.level-cell,
.amount-cell {
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.number-cell {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.total-label,
tfoot td {
  font-weight: 600;
}

.total-label {
  text-align: right;
}

.empty-cell {
  padding: 24px;
  color: var(--el-text-color-secondary);
  text-align: center;
}

.is-self td {
  background: var(--el-color-primary-light-9);
}

.is-current-round td {
  background: var(--el-color-primary-light-9);
}

@media (max-width: 720px) {
  .section-heading,
  .subsection-heading {
    align-items: flex-start;
    flex-direction: column;
  }

  .section-heading,
  .subsection-heading {
    gap: 7px;
  }

  .task-analysis-row {
    flex-direction: column;
  }

  .task-chart {
    width: 100%;
    min-width: 0;
  }
}
</style>
