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

const STORAGE_KEY = 'zaohua:pasture:solver-v5'
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
const herbCount = ref(9)
const poolCount = ref(0)
const buildingCounts = ref<Record<number, number>>({ 0: 9, 3: 0 })

const buildingById = computed(() => new Map((meta.value?.buildings || []).map(item => [item.build_id, item])))
const selectableBuildings = computed(() => meta.value?.buildings.filter(item => item.type === 1 || item.build_id === 3) || [])
const configuredCount = computed(() => Object.values(buildingCounts.value).reduce((sum, value) => sum + (Number(value) || 0), 0))
const countIsValid = computed(() => configuredCount.value === plotCount.value)
const resultBounds = computed(() => {
  const cells = solution.value?.cells || []
  const xs = cells.map(cell => cell.x)
  const ys = cells.map(cell => cell.y)
  return { minX: Math.min(...xs), maxX: Math.max(...xs), minY: Math.min(...ys), maxY: Math.max(...ys) }
})

function isAdjacencyBonus(building: ZaohuaPastureBuilding) {
  return building.effect_range_type === 11
}

function supportsMultipleCopies(building: ZaohuaPastureBuilding) {
  return isAdjacencyBonus(building) || building.build_id === 3
}

function switchLabel(building: ZaohuaPastureBuilding) {
  return supportsMultipleCopies(building) ? '允许使用' : '必须放置'
}

function isBuildingEnabled(buildingId: number) {
  return (buildingCounts.value[buildingId] || 0) > 0
}

function restoreSettings() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}')
    plotCount.value = Math.max(1, Math.min(60, Number(saved.plotCount) || 9))
    realm.value = String(saved.realm || '炼气')
    buildingCounts.value = saved.buildingCounts && typeof saved.buildingCounts === 'object'
      ? Object.fromEntries(Object.entries(saved.buildingCounts).map(([key, value]) => [Number(key), Math.max(0, Number(value) || 0)]))
      : { 0: plotCount.value, 3: 0 }
    herbCount.value = buildingCounts.value[0] || 0
    poolCount.value = buildingCounts.value[3] || 0
  } catch {
    plotCount.value = 9
    buildingCounts.value = { 0: plotCount.value, 3: 0 }
  }
}

watch([plotCount, realm, buildingCounts], () => {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({
    plotCount: plotCount.value,
    realm: realm.value,
    buildingCounts: buildingCounts.value,
  }))
}, { deep: true })

async function solve() {
  if (!countIsValid.value) {
    ElMessage.warning(`建筑数量合计需为 ${plotCount.value} 格，当前为 ${configuredCount.value} 格`)
    return
  }
  solving.value = true
  try {
    herbCount.value = buildingCounts.value[0] || 0
    poolCount.value = buildingCounts.value[3] || 0
    const enabledIds = selectableBuildings.value.filter(item => (buildingCounts.value[item.build_id] || 0) > 0).map(item => item.build_id)
    solution.value = await solveZaohuaPasture({
      plot_count: plotCount.value,
      production_mode: 'target_ratio',
      herb_count: herbCount.value,
      pool_count: poolCount.value,
      enabled_building_ids: enabledIds,
      building_counts: buildingCounts.value,
      exact_building_counts: true,
    })
  } catch (error: any) {
    solution.value = null
    ElMessage.error(error?.response?.data?.detail || error?.message || '洞天布局求解失败')
  } finally {
    solving.value = false
  }
}

function selectRealm(value: string) {
  const previousCount = plotCount.value
  realm.value = value
  const selected = REALMS.find(item => item.label === value)
  if (selected) {
    plotCount.value = selected.count
    buildingCounts.value = {
      ...buildingCounts.value,
      0: Math.max(0, (buildingCounts.value[0] || 0) + selected.count - previousCount),
    }
  }
}


async function load() {
  loading.value = true
  try {
    meta.value = await fetchZaohuaPastureMeta()
    if (countIsValid.value) await solve()
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '灵田逆向数据加载失败')
  } finally {
    loading.value = false
  }
}

function setBuildingCount(buildingId: number, value: number | undefined, max: number) {
  buildingCounts.value = {
    ...buildingCounts.value,
    [buildingId]: Math.max(0, Math.min(max, Number(value) || 0)),
  }
}

function toggleBuilding(building: ZaohuaPastureBuilding, enabled: boolean) {
  const buildingId = building.build_id
  const max = buildingMax(building)
  const current = buildingCounts.value[buildingId] || 0
  buildingCounts.value = {
    ...buildingCounts.value,
    [buildingId]: enabled ? Math.max(1, Math.min(max, current || 1)) : 0,
  }
}

function buildingMax(building: ZaohuaPastureBuilding) {
  return supportsMultipleCopies(building) ? plotCount.value : 1
}

onMounted(() => {
  restoreSettings()
  void load()
})
</script>

<template>
  <div v-loading="loading" class="pasture-page">
    <header class="page-header">
      <h1>洞天求解</h1>
      <div class="parameter-row">
        <el-select :model-value="realm" class="realm-select" @update:model-value="selectRealm">
          <el-option v-for="item in REALMS" :key="item.label" :label="`${item.label} · ${item.count}格`" :value="item.label" />
        </el-select>
        <el-button type="primary" :loading="solving" :disabled="!countIsValid" @click="solve">联合求解形状与布局</el-button>
        <router-link class="plan-demo-link" to="/zaohua/pasture/plan-demo">查看聚元丹方案</router-link>
        <small class="count-summary" :class="{ invalid: !countIsValid }">已配置 {{ configuredCount }} / {{ plotCount }} 格</small>
      </div>
    </header>

    <main class="workspace">
      <section class="board-panel">
        <div class="section-title">
          <span>最优布局</span>
          <small v-if="solution">
            总价值 {{ solution.total_value.toFixed(1) }} · 灵田 {{ solution.herb_count }} · 灵池 {{ solution.pool_count }}
            <template v-if="solution.gain > 0"> · 加成 +{{ solution.gain.toFixed(1) }}</template>
          </small>
        </div>
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
        <div class="settings-title">建筑配置</div>
        <div class="building-row">
          <div class="plot-icon">田</div>
          <div class="building-info"><b>灵田</b><small>种植灵草</small></div>
          <el-input-number
            :model-value="buildingCounts[0] || 0" :min="0" :max="plotCount" size="small" controls-position="right"
            aria-label="灵田数量" @update:model-value="setBuildingCount(0, $event, plotCount)"
          />
        </div>
        <div v-for="building in selectableBuildings" :key="building.build_id" class="building-row">
          <img v-if="building.image_url" :src="building.image_url" alt="" />
          <div class="building-info">
            <b>{{ building.name }}</b>
            <small>{{ building.description }}</small>
          </div>
          <div class="building-switch">
            <el-switch
              :model-value="isBuildingEnabled(building.build_id)"
              :aria-label="`${building.name}：${switchLabel(building)}`"
              @update:model-value="toggleBuilding(building, Boolean($event))"
            />
            <small>{{ switchLabel(building) }}</small>
            <el-input-number
              v-if="isBuildingEnabled(building.build_id) && supportsMultipleCopies(building)"
              :model-value="buildingCounts[building.build_id] || 1"
              :min="1"
              :max="buildingMax(building)"
              size="small"
              controls-position="right"
              :aria-label="`${building.name}数量`"
              @update:model-value="setBuildingCount(building.build_id, $event, buildingMax(building))"
            />
          </div>
        </div>
      </aside>
    </main>
  </div>
</template>

<style scoped>
.pasture-page { box-sizing: border-box; height: 100%; min-height: 0; overflow: hidden; padding: 18px 22px; display: flex; flex-direction: column; gap: 16px; }
.page-header { flex: none; display: flex; flex-direction: column; align-items: flex-start; gap: 12px; }
.page-header h1 { margin: 0; font-size: 22px; }
.parameter-row { display: flex; align-items: center; flex-wrap: wrap; gap: 10px 12px; }
.parameter-row label { color: var(--el-text-color-regular); white-space: nowrap; }
.parameter-row :deep(.el-input-number) { width: 112px; }
.realm-select { width: 126px; }
.plan-demo-link { color: var(--el-color-primary); text-decoration: none; }
.plan-demo-link:hover { text-decoration: underline; }
.count-summary { color: var(--el-text-color-secondary); }
.count-summary.invalid { color: var(--el-color-danger); }
.section-title { display: flex; align-items: baseline; gap: 8px; font-weight: 600; margin-bottom: 8px; }
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
.plot-icon { width: 52px; height: 46px; display: grid; place-items: center; background: #eef5e8; color: #4d8f45; font-size: 18px; }
.building-info, .building-switch { display: flex; flex-direction: column; gap: 4px; }
.building-info b { font-weight: 600; }
.building-info small, .building-switch small { color: var(--el-text-color-secondary); line-height: 1.35; }
.building-switch { align-items: flex-end; min-width: 92px; }
.building-row :deep(.el-input-number) { width: 88px; }
@media (max-width: 900px) { .workspace { grid-template-columns: 1fr; } .building-settings { border-left: 0; border-top: 1px solid var(--el-border-color-lighter); padding: 14px 0 0; } }
</style>
