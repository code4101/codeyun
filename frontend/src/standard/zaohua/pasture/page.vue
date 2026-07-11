<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import {
  fetchZaohuaPastureMeta,
  solveZaohuaPasture,
  type ZaohuaPastureBuilding,
  type ZaohuaPastureMeta,
  type ZaohuaPastureSolution,
} from '@/api/zaohua'

const STORAGE_KEY = 'zaohua:pasture:solver-v2'
const REALMS = [
  { label: '炼气', count: 9 }, { label: '筑基', count: 14 }, { label: '结丹', count: 19 },
  { label: '元婴', count: 24 }, { label: '化神', count: 29 }, { label: '炼虚', count: 34 },
]
const loading = ref(false)
const solving = ref(false)
const meta = ref<ZaohuaPastureMeta | null>(null)
const solution = ref<ZaohuaPastureSolution | null>(null)
const plotCount = ref(9)
const realm = ref('炼气')
const productionMode = ref<'free' | 'exact' | 'target_ratio'>('target_ratio')
const herbCount = ref(9)
const poolCount = ref(0)
const enabledBuildingIds = ref<number[]>([])
const buildingCounts = ref<Record<number, number>>({})

const buildingById = computed(() => new Map((meta.value?.buildings || []).map(item => [item.build_id, item])))
const selectableBuildings = computed(() => meta.value?.buildings.filter(item => item.type === 1 && item.build_id !== 3) || [])
const resultBounds = computed(() => {
  const cells = solution.value?.cells || []
  const xs = cells.map(cell => cell.x)
  const ys = cells.map(cell => cell.y)
  return { minX: Math.min(...xs), maxX: Math.max(...xs), minY: Math.min(...ys), maxY: Math.max(...ys) }
})

function isAdjacencyBonus(building: ZaohuaPastureBuilding) {
  return building.effect_range_type === 11
}

function switchLabel(building: ZaohuaPastureBuilding) {
  return isAdjacencyBonus(building) ? '允许使用' : '必须放置'
}

function restoreSettings() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}')
    plotCount.value = Math.max(1, Math.min(60, Number(saved.plotCount) || 9))
    realm.value = String(saved.realm || '炼气')
    productionMode.value = ['free', 'exact', 'target_ratio'].includes(saved.productionMode) ? saved.productionMode : 'target_ratio'
    herbCount.value = Math.max(0, saved.herbCount == null ? 9 : Number(saved.herbCount) || 0)
    poolCount.value = Math.max(0, Number(saved.poolCount) || 0)
    enabledBuildingIds.value = Array.isArray(saved.enabledBuildingIds)
      ? saved.enabledBuildingIds.map(Number).filter(Number.isFinite)
      : []
    buildingCounts.value = saved.buildingCounts && typeof saved.buildingCounts === 'object'
      ? Object.fromEntries(Object.entries(saved.buildingCounts).map(([key, value]) => [Number(key), Math.max(1, Number(value) || 1)]))
      : {}
  } catch {
    plotCount.value = 9
    enabledBuildingIds.value = []
    buildingCounts.value = {}
  }
}

watch([plotCount, realm, productionMode, herbCount, poolCount, enabledBuildingIds, buildingCounts], () => {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({
    plotCount: plotCount.value,
    realm: realm.value, productionMode: productionMode.value, herbCount: herbCount.value, poolCount: poolCount.value,
    enabledBuildingIds: enabledBuildingIds.value,
    buildingCounts: buildingCounts.value,
  }))
}, { deep: true })

async function solve() {
  solving.value = true
  try {
    solution.value = await solveZaohuaPasture({
      plot_count: plotCount.value,
      production_mode: productionMode.value,
      herb_count: herbCount.value,
      pool_count: poolCount.value,
      enabled_building_ids: enabledBuildingIds.value,
      building_counts: buildingCounts.value,
    })
  } catch (error: any) {
    solution.value = null
    ElMessage.error(error?.response?.data?.detail || error?.message || '洞天布局求解失败')
  } finally {
    solving.value = false
  }
}

function selectRealm(value: string) {
  realm.value = value
  const selected = REALMS.find(item => item.label === value)
  if (selected) plotCount.value = selected.count
}

function markCustomCount() {
  const matched = REALMS.find(item => item.count === plotCount.value)
  realm.value = matched?.label || '自定义'
}


async function load() {
  loading.value = true
  try {
    meta.value = await fetchZaohuaPastureMeta()
    await solve()
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '灵田逆向数据加载失败')
  } finally {
    loading.value = false
  }
}

function toggleBuilding(buildingId: number, enabled: boolean) {
  enabledBuildingIds.value = enabled
    ? [...new Set([...enabledBuildingIds.value, buildingId])]
    : enabledBuildingIds.value.filter(id => id !== buildingId)
  if (enabled && !buildingCounts.value[buildingId]) {
    buildingCounts.value = { ...buildingCounts.value, [buildingId]: 1 }
  }
}

function setBuildingCount(buildingId: number, value: number | undefined) {
  buildingCounts.value = {
    ...buildingCounts.value,
    [buildingId]: Math.max(1, Math.min(plotCount.value, Number(value) || 1)),
  }
}

onMounted(() => {
  restoreSettings()
  void load()
})
</script>

<template>
  <div v-loading="loading" class="pasture-page">
    <header class="toolbar">
      <h1>洞天求解</h1>
      <label>境界</label>
      <el-select :model-value="realm" class="realm-select" @update:model-value="selectRealm">
        <el-option v-for="item in REALMS" :key="item.label" :label="`${item.label} · ${item.count}格`" :value="item.label" />
        <el-option v-if="realm === '自定义'" label="自定义" value="自定义" />
      </el-select>
      <label>格子数</label>
      <el-input-number v-model="plotCount" :min="1" :max="60" controls-position="right" @change="markCustomCount" />
      <label>生产计划</label>
      <el-select v-model="productionMode" class="mode-select">
        <el-option label="保持比例" value="target_ratio" />
        <el-option label="精确数量" value="exact" />
        <el-option label="完全自由" value="free" />
      </el-select>
      <template v-if="productionMode !== 'free'">
        <label>灵田</label><el-input-number v-model="herbCount" :min="0" :max="plotCount" controls-position="right" />
        <label>灵池</label><el-input-number v-model="poolCount" :min="0" :max="plotCount" controls-position="right" />
      </template>
      <el-button type="primary" :loading="solving" @click="solve">联合求解形状与布局</el-button>
      <span v-if="solution" class="result">
        总价值 {{ solution.total_value.toFixed(1) }} · 灵田 {{ solution.herb_count }} · 灵池 {{ solution.pool_count }}
        <template v-if="solution.gain > 0"> · 加成 +{{ solution.gain.toFixed(1) }}</template>
      </span>
    </header>

    <main class="workspace">
      <section class="board-panel">
        <div class="section-title">最优布局</div>
        <div
          v-if="solution"
          class="board"
          :style="{
            gridTemplateColumns: `repeat(${resultBounds.maxX - resultBounds.minX + 1}, 88px)`,
            gridTemplateRows: `repeat(${resultBounds.maxY - resultBounds.minY + 1}, 88px)`,
          }"
        >
          <div
            v-for="cell in solution.cells"
            :key="cell.index"
            class="plot-cell"
            :class="{ building: cell.kind === 'building' }"
            :style="{
              gridColumn: cell.x - resultBounds.minX + 1,
              gridRow: cell.y - resultBounds.minY + 1,
            }"
          >
            <template v-if="cell.kind === 'plot'">
              <span class="plot-name">灵田</span>
              <small>×{{ cell.coefficient || 1 }}</small>
              <small v-if="(cell.speed_count || 0) || (cell.yield_count || 0)" class="factor-detail">
                泉×{{ 1 + (cell.speed_count || 0) }} · 枢×{{ 1 + (cell.yield_count || 0) }}
              </small>
            </template>
            <template v-else>
              <img v-if="buildingById.get(cell.building_id || 0)?.image_url" :src="buildingById.get(cell.building_id || 0)?.image_url" alt="" />
              <span>{{ buildingById.get(cell.building_id || 0)?.name }}</span>
              <small v-if="cell.productive">×{{ cell.coefficient || 1 }}</small>
              <small v-if="cell.productive && (cell.yield_count || 0)" class="factor-detail">
                枢×{{ 1 + (cell.yield_count || 0) }}
              </small>
            </template>
          </div>
        </div>
      </section>

      <aside class="building-settings">
        <div class="settings-title">求解建筑</div>
        <div v-for="building in selectableBuildings" :key="building.build_id" class="building-row">
          <img v-if="building.image_url" :src="building.image_url" alt="" />
          <div class="building-info">
            <b>{{ building.name }}</b>
            <small>{{ building.description }}</small>
          </div>
          <div class="building-switch">
            <el-switch
              :model-value="enabledBuildingIds.includes(building.build_id)"
              :aria-label="`${building.name}：${switchLabel(building)}`"
              @update:model-value="toggleBuilding(building.build_id, Boolean($event))"
            />
            <small>{{ switchLabel(building) }}</small>
            <el-input-number
              v-if="enabledBuildingIds.includes(building.build_id) && !isAdjacencyBonus(building)"
              :model-value="buildingCounts[building.build_id] || 1"
              :min="1"
              :max="plotCount"
              size="small"
              controls-position="right"
              :disabled="!enabledBuildingIds.includes(building.build_id)"
              aria-label="放置数量"
              @update:model-value="setBuildingCount(building.build_id, $event)"
            />
          </div>
        </div>
      </aside>
    </main>
  </div>
</template>

<style scoped>
.pasture-page { box-sizing: border-box; height: 100%; min-height: 0; overflow: hidden; padding: 18px 22px; display: flex; flex-direction: column; gap: 16px; }
.toolbar { display: flex; align-items: center; gap: 12px; flex: none; }
.toolbar { flex-wrap: wrap; }
.toolbar h1 { margin: 0 10px 0 0; font-size: 22px; }
.toolbar label { color: var(--el-text-color-regular); }
.toolbar :deep(.el-input-number) { width: 112px; }
.realm-select { width: 126px; }
.mode-select { width: 112px; }
.result { color: var(--el-text-color-secondary); margin-left: 4px; }
.section-title { font-weight: 600; margin-bottom: 8px; }
.section-title small { margin-left: 8px; color: var(--el-text-color-secondary); font-weight: 400; }
.workspace { min-height: 0; flex: 1; display: grid; grid-template-columns: minmax(420px, 1fr) 380px; gap: 20px; }
.board-panel, .building-settings { min-height: 0; overflow: auto; }
.board { display: grid; gap: 8px; width: max-content; padding: 2px; }
.plot-cell { width: 88px; height: 88px; box-sizing: border-box; border: 1px solid var(--el-border-color); background: #eef5e8; color: var(--el-text-color-primary); display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 4px; }
.plot-cell.building { background: var(--el-fill-color-light); }
.plot-cell img { width: 56px; height: 48px; object-fit: contain; }
.plot-cell small { color: var(--el-color-success); }
.building-settings { border-left: 1px solid var(--el-border-color-lighter); padding-left: 18px; }
.settings-title { font-weight: 600; margin-bottom: 8px; }
.building-row { min-height: 66px; border-bottom: 1px solid var(--el-border-color-lighter); display: grid; grid-template-columns: 54px minmax(0, 1fr) auto; align-items: center; gap: 10px; padding: 7px 2px; }
.building-row > img { width: 52px; height: 46px; object-fit: contain; background: #eef5e8; }
.building-info, .building-switch { display: flex; flex-direction: column; gap: 4px; }
.building-info b { font-weight: 600; }
.building-info small, .building-switch small { color: var(--el-text-color-secondary); line-height: 1.35; }
.building-switch { flex-direction: row; align-items: center; justify-content: flex-end; min-width: 92px; }
.building-switch :deep(.el-input-number) { width: 82px; }
@media (max-width: 900px) { .workspace { grid-template-columns: 1fr; } .building-settings { border-left: 0; border-top: 1px solid var(--el-border-color-lighter); padding: 14px 0 0; } }
</style>
