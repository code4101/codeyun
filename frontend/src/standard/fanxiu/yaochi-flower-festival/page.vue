<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import {
  collectFanxiuExchangeActivity,
  collectFanxiuYaochiFlowerResources,
  getFanxiuExchangeActivityRankings,
  getFanxiuExchangeActivitySnapshot,
  getFanxiuYaochiFlowerFestivalTasks,
  getFanxiuYaochiFlowerResources,
  type FanxiuExchangeActivityDetail,
  type FanxiuExchangeActivitySummary,
  type FanxiuExchangeRankingItem,
  type FanxiuYaochiFlowerResourceSnapshot,
  type FanxiuYaochiFlowerTaskMilestone,
} from '@/api/fanxiu'
import RelationshipScatterPlot from '@/components/RelationshipScatterPlot.vue'
import FanxiuLinkedItemChip from '@/standard/fanxiu/FanxiuLinkedItemChip.vue'
import FanxiuActivityToolbar from '@/standard/fanxiu/components/FanxiuActivityToolbar.vue'
import FanxiuActivityRankingSection from '@/standard/fanxiu/components/FanxiuActivityRankingSection.vue'
import FanxiuTalentPillMilestoneTable from '@/standard/fanxiu/components/FanxiuTalentPillMilestoneTable.vue'
import { projectTalentPillMilestones } from '@/standard/fanxiu/components/talentPillMilestones'
import { formatActivityUpdatedAt } from '@/standard/fanxiu/components/activityStatus'
import { useFanxiuActivityRefresh } from '@/standard/fanxiu/components/useFanxiuActivityRefresh'
import { formatChineseCompactNumber } from '@/utils/numberFormat'

defineProps<{ embedded?: boolean }>()

const ACTIVITY_TYPE = 'yaochi-flower-festival'
const RESOURCE_SORT_STORAGE_KEY = 'fanxiu-yaochi-flower-resource-sort'
type ResourceSortKey = 'name' | 'count' | 'friendship' | 'total_friendship'
type SortDirection = 'asc' | 'desc'

function loadResourceSort(): { key: ResourceSortKey | null; direction: SortDirection } {
  try {
    const saved = JSON.parse(localStorage.getItem(RESOURCE_SORT_STORAGE_KEY) || 'null')
    const validKeys: ResourceSortKey[] = ['name', 'count', 'friendship', 'total_friendship']
    if (validKeys.includes(saved?.key) && ['asc', 'desc'].includes(saved?.direction)) {
      return saved
    }
  } catch {
    // Keep the business-defined order when no valid UI preference exists.
  }
  return { key: 'total_friendship', direction: 'desc' }
}

const loading = ref(false)
const rankingLoading = ref(false)
const collectingFromGame = ref(false)
const errorText = ref('')
const activities = ref<FanxiuExchangeActivitySummary[]>([])
const selectedActivityId = ref('')
const activity = ref<FanxiuExchangeActivityDetail | null>(null)
const rankings = ref<FanxiuExchangeRankingItem[]>([])
const planeRankings = ref<FanxiuExchangeRankingItem[]>([])
const taskMilestones = ref<FanxiuYaochiFlowerTaskMilestone[]>([])
const flowerResources = ref<FanxiuYaochiFlowerResourceSnapshot | null>(null)
const rankingLastCapturedAt = ref('')
const planeRankingLastCapturedAt = ref('')
const resourceSort = ref(loadResourceSort())

const currentFriendship = computed(() =>
  rankings.value.find(row => row.is_self)?.score ?? 0,
)
const availableFlowerResources = computed(() => {
  const items = (flowerResources.value?.items || []).filter(item => (item.count || 0) > 0)
  const { key, direction } = resourceSort.value
  if (!key) return items
  return [...items].sort((left, right) => {
    const compared = key === 'name'
      ? left.name.localeCompare(right.name, 'zh-CN')
      : Number(left[key] || 0) - Number(right[key] || 0)
    return direction === 'asc' ? compared : -compared
  })
})

function setResourceSort(key: ResourceSortKey) {
  resourceSort.value = resourceSort.value.key === key
    ? { key, direction: resourceSort.value.direction === 'asc' ? 'desc' : 'asc' }
    : { key, direction: 'asc' }
  localStorage.setItem(RESOURCE_SORT_STORAGE_KEY, JSON.stringify(resourceSort.value))
}

function resourceSortIndicator(key: ResourceSortKey) {
  if (resourceSort.value.key !== key) return '↕'
  return resourceSort.value.direction === 'asc' ? '↑' : '↓'
}

function resourceAriaSort(key: ResourceSortKey) {
  if (resourceSort.value.key !== key) return 'none'
  return resourceSort.value.direction === 'asc' ? 'ascending' : 'descending'
}
const talentPillTasks = computed(() => projectTalentPillMilestones(taskMilestones.value))
const taskChartSeries = computed(() => {
  const current = currentFriendship.value
  const observed = [{ x: 0, y: 0 }]
  const projected: Array<{ x: number; y: number }> = []
  for (const row of talentPillTasks.value) {
    const point = { x: row.target, y: row.cumulativeTalentPills }
    if (row.target <= current) observed.push(point)
    else projected.push(point)
  }
  const earned = talentPillTasks.value
    .filter(row => row.target <= current)
    .reduce((total, row) => total + row.talent_pill_count, 0)
  if (current > observed[observed.length - 1].x) observed.push({ x: current, y: earned })
  return [{
    label: '累计天资丹',
    color: '#409eff',
    observed,
    keyPoints: talentPillTasks.value
      .filter(row => row.target <= current)
    .map(row => ({ x: row.target, y: row.cumulativeTalentPills })),
    currentPoint: current > 0 ? { x: current, y: earned, label: '当前' } : undefined,
    projected,
  }]
})

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
    errorText.value = activity.value ? '' : '暂无瑶池花会活动实例'
  } catch (error: any) {
    errorText.value = error?.response?.data?.detail || error?.message || '读取瑶池花会活动失败'
  } finally {
    loading.value = false
  }
}

async function loadRankings() {
  if (!selectedActivityId.value) return
  rankingLoading.value = true
  try {
    const [personalResult, planeResult] = await Promise.all([
      getFanxiuExchangeActivityRankings(
        ACTIVITY_TYPE,
        selectedActivityId.value,
        1,
        100,
        'personal',
      ),
      activity.value && activity.value.cross_count > 1
        ? getFanxiuExchangeActivityRankings(
            ACTIVITY_TYPE,
            selectedActivityId.value,
            1,
            100,
            'plane',
          )
        : Promise.resolve(null),
    ])
    rankings.value = personalResult.items
    rankingLastCapturedAt.value = personalResult.last_captured_at || ''
    planeRankings.value = planeResult?.items || []
    planeRankingLastCapturedAt.value = planeResult?.last_captured_at || ''
  } catch (error: any) {
    errorText.value = error?.response?.data?.detail || error?.message || '读取瑶池花会榜单失败'
  } finally {
    rankingLoading.value = false
  }
}

async function loadTasks() {
  if (!selectedActivityId.value) return
  try {
    const result = await getFanxiuYaochiFlowerFestivalTasks(selectedActivityId.value)
    taskMilestones.value = result.items
  } catch (error: any) {
    errorText.value = error?.response?.data?.detail || error?.message || '读取瑶池花会任务失败'
  }
}

async function loadFlowerResources() {
  try {
    flowerResources.value = await getFanxiuYaochiFlowerResources()
  } catch (error: any) {
    errorText.value = error?.response?.data?.detail || error?.message || '读取仙花资源失败'
  }
}

async function collectFromGame(showFeedback = true) {
  if (!activity.value || !canCollect.value || collectingFromGame.value) return
  collectingFromGame.value = true
  try {
    const [activityResult, resourceResult] = await Promise.all([
      collectFanxiuExchangeActivity(ACTIVITY_TYPE, activity.value.id),
      collectFanxiuYaochiFlowerResources(activity.value.id),
    ])
    activity.value = activityResult
    flowerResources.value = resourceResult
    await loadRankings()
    if (showFeedback) ElMessage.success('已从游戏更新榜单与仙花资源')
  } catch (error: any) {
    if (showFeedback) {
      ElMessage.warning(error?.response?.data?.detail || error?.message || '更新榜单失败')
    }
  } finally {
    collectingFromGame.value = false
  }
}

watch(selectedActivityId, value => {
  if (value && value !== activity.value?.id) {
    void loadSnapshot(value).then(() => Promise.all([loadRankings(), loadTasks()]))
  }
})

onMounted(async () => {
  await loadSnapshot()
  await Promise.all([loadRankings(), loadTasks(), loadFlowerResources()])
  maybeAutoCollect()
})
</script>

<template>
  <div class="yaochi-page" :class="{ 'is-embedded': embedded }">
    <FanxiuActivityToolbar
      v-model="selectedActivityId"
      :activities="activities"
      :can-collect="canCollect"
      :collect-loading="collectingFromGame"
      :collect-disabled="loading || rankingLoading"
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

      <section v-if="activity" class="section-block">
        <div class="section-heading">
          <h3>仙花资源</h3>
          <span>
            储物袋可用 {{ availableFlowerResources.length }} 种，基础友好度
            {{ formatChineseCompactNumber(flowerResources?.total_friendship || 0) }}
            <template v-if="flowerResources?.captured_at">，最后读取 {{ formatActivityUpdatedAt(flowerResources.captured_at) }}</template>
          </span>
        </div>
        <div class="table-shell">
          <table class="resource-table">
            <thead>
              <tr>
                <th :aria-sort="resourceAriaSort('name')">
                  <button type="button" class="sort-button" @click="setResourceSort('name')">
                    赠礼资源 <span>{{ resourceSortIndicator('name') }}</span>
                  </button>
                </th>
                <th class="number-cell" :aria-sort="resourceAriaSort('count')">
                  <button type="button" class="sort-button number-sort-button" @click="setResourceSort('count')">
                    持有数量 <span>{{ resourceSortIndicator('count') }}</span>
                  </button>
                </th>
                <th class="number-cell" :aria-sort="resourceAriaSort('friendship')">
                  <button type="button" class="sort-button number-sort-button" @click="setResourceSort('friendship')">
                    单个友好度 <span>{{ resourceSortIndicator('friendship') }}</span>
                  </button>
                </th>
                <th class="number-cell" :aria-sort="resourceAriaSort('total_friendship')">
                  <button type="button" class="sort-button number-sort-button" @click="setResourceSort('total_friendship')">
                    可提供友好度 <span>{{ resourceSortIndicator('total_friendship') }}</span>
                  </button>
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in availableFlowerResources" :key="item.item_id">
                <td>
                  <FanxiuLinkedItemChip
                    :item="{
                      id: item.item_id,
                      name: item.name,
                      icon: item.icon,
                      small_icon: item.small_icon,
                      description: item.description,
                      quality_color: item.quality_color,
                    }"
                    compact
                  />
                </td>
                <td class="number-cell amount-cell">{{ formatChineseCompactNumber(item.count || 0) }}</td>
                <td class="number-cell">{{ formatChineseCompactNumber(item.friendship) }}</td>
                <td class="number-cell amount-cell">{{ formatChineseCompactNumber(item.total_friendship || 0) }}</td>
              </tr>
              <tr v-if="!availableFlowerResources.length">
                <td colspan="4" class="empty-cell">尚未读取到仙花库存，点击“从游戏更新”读取当前储物袋</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section v-if="activity && talentPillTasks.length" class="section-block">
        <div class="task-analysis-row">
          <div class="task-table-block">
            <div class="task-table-heading">
              <h3>仙花任务</h3>
              <span>当前累计友好度 {{ formatChineseCompactNumber(currentFriendship) }}</span>
            </div>
            <FanxiuTalentPillMilestoneTable
              :rows="taskMilestones"
              :current="currentFriendship"
              target-label="累计友好度"
              per-pill-label="丹均友好度"
            />
          </div>
          <div class="task-chart-block">
            <div class="chart-note">纵轴：累计天资丹</div>
            <RelationshipScatterPlot
              class="task-chart"
              x-label="友好度"
              y-label="累计天资丹"
              :x-data-max="talentPillTasks[talentPillTasks.length - 1]?.target || 0"
              :series="taskChartSeries"
            />
          </div>
        </div>
      </section>

      <FanxiuActivityRankingSection
        v-if="activity"
        :personal-rows="rankings"
        :plane-rows="planeRankings"
        :show-plane="activity.cross_count > 1"
        score-label="友好度"
        score-per-reward-label="丹均友好度"
        :personal-last-captured-at="rankingLastCapturedAt"
        :plane-last-captured-at="planeRankingLastCapturedAt"
      />
    </div>
  </div>
</template>

<style scoped>
.yaochi-page {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 20px;
}

.yaochi-page.is-embedded {
  padding: 0;
}

.ranking-content {
  min-height: 180px;
}

.section-block {
  margin-top: 18px;
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

.table-shell {
  max-width: 100%;
  overflow-x: auto;
}

.task-analysis-row {
  display: grid;
  grid-template-columns: max-content minmax(360px, 560px);
  gap: 24px;
  align-items: start;
}

.task-table-block,
.task-chart-block {
  min-width: 0;
}

.task-table-heading {
  display: flex;
  gap: 12px;
  align-items: baseline;
  margin-bottom: 8px;
}

.task-table-heading h3 {
  margin: 0;
  font-size: 17px;
}

.task-table-heading span,
.chart-note {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.task-progress-table {
  width: max-content;
  max-width: 100%;
  border-collapse: collapse;
  font-size: 14px;
}

.resource-table {
  width: max-content;
  min-width: 560px;
  max-width: 100%;
  border-collapse: collapse;
  font-size: 14px;
}

.resource-table th,
.resource-table td {
  padding: 9px 14px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  white-space: nowrap;
}

.resource-table th {
  color: var(--el-text-color-secondary);
  font-weight: 500;
  background: var(--el-fill-color-light);
}

.sort-button {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-width: 100%;
  padding: 0;
  border: 0;
  color: inherit;
  font: inherit;
  white-space: nowrap;
  cursor: pointer;
  background: transparent;
}

.sort-button span {
  width: 1em;
  color: var(--el-color-primary);
  text-align: center;
}

.number-sort-button {
  justify-content: flex-end;
}

.sort-button:hover,
.sort-button:focus-visible {
  color: var(--el-text-color-primary);
}

.amount-cell {
  color: var(--el-color-primary);
  font-weight: 600;
}

.empty-cell {
  color: var(--el-text-color-secondary);
  text-align: center;
}

.task-progress-table th,
.task-progress-table td {
  padding: 9px 14px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  white-space: nowrap;
}

.task-progress-table th {
  color: var(--el-text-color-secondary);
  font-weight: 500;
  background: var(--el-fill-color-light);
}

.number-cell {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.chart-note {
  margin-bottom: 4px;
}

.task-chart {
  width: 100%;
  height: 250px;
}

@media (max-width: 720px) {
  .task-analysis-row {
    grid-template-columns: minmax(0, 1fr);
  }

  .task-chart {
    max-width: 560px;
  }

  .section-heading {
    align-items: flex-start;
    flex-direction: column;
    gap: 7px;
  }
}
</style>
