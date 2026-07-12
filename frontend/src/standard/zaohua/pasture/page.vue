<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  fetchZaohuaPastureMeta,
  fetchZaohuaHerbMeta,
  fetchZaohuaHerbs,
  solveZaohuaPasture,
  type ZaohuaHerb,
  type ZaohuaHerbMeta,
  type ZaohuaPastureMeta,
  type ZaohuaPastureSolution,
} from '@/api/zaohua'

const STORAGE_KEY = 'zaohua:pasture:solver-v6'
const SAVED_PLANS_KEY = 'zaohua:pasture:saved-plans-v1'
const REALMS = [
  { label: '炼气', count: 9 }, { label: '筑基', count: 14 }, { label: '结丹', count: 19 },
  { label: '元婴', count: 24 }, { label: '化神', count: 29 }, { label: '炼虚', count: 34 },
]
const loading = ref(false)
const solving = ref(false)
const meta = ref<ZaohuaPastureMeta | null>(null)
const solution = ref<ZaohuaPastureSolution | null>(null)
const solvedConfigKey = ref('')
const plotCount = ref(9)
const realm = ref('炼气')
const productionType = ref<'herb' | 'pool'>('herb')
const specialCellCount = ref(0)
const enabledBonusIds = ref<number[]>([4, 5])
const herbMeta = ref<ZaohuaHerbMeta | null>(null)
const herbs = ref<ZaohuaHerb[]>([])
const strategyRows = ref<Array<{ grade: string; herbId: number | ''; demand: number }>>([])
type SavedPlan = {
  id: string
  name: string
  updatedAt: number
  plotCount: number
  realm: string
  productionType: 'herb' | 'pool'
  specialCellCount: number
  enabledBonusIds: number[]
  solution: ZaohuaPastureSolution
  strategyRows: Array<{ grade: string; herbId: number | ''; demand: number }>
}
const savedPlans = ref<SavedPlan[]>([])
const selectedPlanId = ref('')

function cloneData<T>(value: T): T {
  return JSON.parse(JSON.stringify(value))
}

const buildingById = computed(() => new Map((meta.value?.buildings || []).map(item => [item.build_id, item])))
const relevantBonusIds = computed(() => productionType.value === 'herb' ? [4, 5] : [5])
const bonusBuildings = computed(() => meta.value?.buildings.filter(item => relevantBonusIds.value.includes(item.build_id)) || [])
const resultBounds = computed(() => {
  const cells = solution.value?.cells || []
  const xs = cells.map(cell => cell.x)
  const ys = cells.map(cell => cell.y)
  return { minX: Math.min(...xs), maxX: Math.max(...xs), minY: Math.min(...ys), maxY: Math.max(...ys) }
})
const maxPlotCoefficient = computed(() => Math.max(
  1,
  ...(solution.value?.cells.filter(cell => cell.kind === 'plot' || cell.productive).map(cell => Number(cell.coefficient) || 1) || []),
))
const strategyPlan = computed(() => {
  if (!solution.value || productionType.value !== 'herb') return null
  const rows = strategyRows.value.map(row => ({
    ...row,
    herb: herbs.value.find(item => item.item_id === row.herbId),
  })).filter(row => row.herb && row.demand > 0)
  const plots = solution.value.cells.filter(cell => cell.kind === 'plot')
  if (!rows.length || plots.length < rows.length) return null
  const coefficients = plots.map(cell => Math.max(1, Number(cell.coefficient) || 1))
  let states = new Map<string, { capacities: number[]; assignments: number[] }>()
  states.set(rows.map(() => 0).join(','), { capacities: rows.map(() => 0), assignments: [] })
  for (const coefficient of coefficients) {
    const next = new Map<string, { capacities: number[]; assignments: number[] }>()
    for (const state of states.values()) {
      rows.forEach((_, rowIndex) => {
        const capacities = [...state.capacities]
        capacities[rowIndex] += coefficient
        const key = capacities.join(',')
        if (!next.has(key)) next.set(key, { capacities, assignments: [...state.assignments, rowIndex] })
      })
    }
    states = next
  }
  const daysForGrade = (name: string) => name.includes('下品') ? 10 : name.includes('中品') ? 20 : name.includes('上品') ? 30 : 40
  let best: { capacities: number[]; assignments: number[]; rate: number; waste: number } | null = null
  for (const state of states.values()) {
    if (state.capacities.some(value => value <= 0)) continue
    const weights = rows.map(row => row.demand * daysForGrade(row.grade))
    const rate = Math.min(...state.capacities.map((value, index) => value / weights[index]))
    const waste = state.capacities.reduce((sum, value, index) => sum + value - rate * weights[index], 0)
    if (!best || rate > best.rate + 1e-9 || (Math.abs(rate - best.rate) < 1e-9 && waste < best.waste)) {
      best = { ...state, rate, waste }
    }
  }
  if (!best) return null
  const byCell = new Map(plots.map((cell, index) => [cell.index, rows[best!.assignments[index]].herb!]))
  const ratios = new Map<number, number>()
  const overflow: Array<{ name: string; percent: number }> = []
  rows.forEach((row, index) => {
    const weight = row.demand * daysForGrade(row.grade)
    const ratio = (best!.capacities[index] / weight) / best!.rate
    ratios.set(row.herb!.item_id, ratio)
    if (ratio > 1.0001) overflow.push({ name: row.herb!.name, percent: (ratio - 1) * 100 })
  })
  return { byCell, daysPerUnit: 1 / best.rate, waste: best.waste, ratios, overflow }
})

function currentConfigKey() {
  return JSON.stringify({
    plotCount: plotCount.value,
    productionType: productionType.value,
    specialCellCount: specialCellCount.value,
    enabledBonusIds: [...enabledBonusIds.value].sort((a, b) => a - b),
  })
}

function restoreSettings() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}')
    plotCount.value = Math.max(1, Math.min(60, Number(saved.plotCount) || 9))
    realm.value = String(saved.realm || '炼气')
    productionType.value = saved.productionType === 'pool' ? 'pool' : 'herb'
    specialCellCount.value = Math.max(0, Math.min(plotCount.value, Number(saved.specialCellCount) || 0))
    enabledBonusIds.value = Array.isArray(saved.enabledBonusIds) ? saved.enabledBonusIds.map(Number) : [4, 5]
    strategyRows.value = Array.isArray(saved.strategyRows)
      ? saved.strategyRows.map((row: any) => ({ ...row, herbId: Number(row.herbId) > 0 ? Number(row.herbId) : '' }))
      : []
    if (saved.solution && saved.solutionKey === currentConfigKey() && Array.isArray(saved.solution.cells)) {
      solution.value = saved.solution
      solvedConfigKey.value = saved.solutionKey
    }
  } catch {
    plotCount.value = 9
    specialCellCount.value = 0
    enabledBonusIds.value = [4, 5]
    strategyRows.value = []
    solution.value = null
    solvedConfigKey.value = ''
  }
}

function restoreSavedPlans() {
  try {
    const saved = JSON.parse(localStorage.getItem(SAVED_PLANS_KEY) || '[]')
    savedPlans.value = Array.isArray(saved) ? saved.filter(item => item?.id && item?.solution?.cells) : []
  } catch {
    savedPlans.value = []
  }
}

function persistSavedPlans() {
  localStorage.setItem(SAVED_PLANS_KEY, JSON.stringify(savedPlans.value))
}

function persistSettings() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({
    plotCount: plotCount.value,
    realm: realm.value,
    productionType: productionType.value,
    specialCellCount: specialCellCount.value,
    enabledBonusIds: enabledBonusIds.value,
    solutionKey: solvedConfigKey.value,
    solution: solution.value,
    strategyRows: strategyRows.value,
  }))
}

watch([plotCount, realm, productionType, specialCellCount, enabledBonusIds], () => {
  selectedPlanId.value = ''
  if (solvedConfigKey.value !== currentConfigKey()) {
    solution.value = null
    solvedConfigKey.value = ''
  }
  persistSettings()
}, { deep: true })

watch([solution, solvedConfigKey], persistSettings, { deep: true })
watch(strategyRows, persistSettings, { deep: true })

async function solve() {
  solving.value = true
  try {
    const result = await solveZaohuaPasture({
      plot_count: plotCount.value,
      production_mode: 'target_ratio',
      herb_count: productionType.value === 'herb' ? 1 : 0,
      pool_count: productionType.value === 'pool' ? 1 : 0,
      enabled_building_ids: enabledBonusIds.value.filter(id => relevantBonusIds.value.includes(id)),
      building_counts: {},
      exact_building_counts: false,
      special_cell_count: specialCellCount.value,
    })
    solution.value = result
    solvedConfigKey.value = currentConfigKey()
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


async function load() {
  loading.value = true
  try {
    const [pastureMeta, loadedHerbMeta, herbPage] = await Promise.all([
      fetchZaohuaPastureMeta(),
      fetchZaohuaHerbMeta(),
      fetchZaohuaHerbs({ page: 1, page_size: 200, sort_by: 'grade' }),
    ])
    meta.value = pastureMeta
    herbMeta.value = loadedHerbMeta
    herbs.value = herbPage.items
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '灵田逆向数据加载失败')
  } finally {
    loading.value = false
  }
}

function toggleBonus(buildingId: number, enabled: boolean) {
  enabledBonusIds.value = enabled
    ? [...new Set([...enabledBonusIds.value, buildingId])]
    : enabledBonusIds.value.filter(id => id !== buildingId)
}

function addStrategyRow() {
  const grade = herbMeta.value?.grades[0]?.name || ''
  strategyRows.value.push({ grade, herbId: '', demand: 1 })
}

function removeStrategyRow(index: number) {
  strategyRows.value.splice(index, 1)
}

function herbsForGrade(grade: string) {
  return herbs.value.filter(item => item.grade_name === grade)
}

function strategyRatio(herbId: number | '') {
  return herbId && strategyPlan.value ? (strategyPlan.value.ratios.get(herbId) || 0).toFixed(2) : ''
}

function plotVisualStyle(cell: ZaohuaPastureSolution['cells'][number]) {
  if (cell.kind !== 'plot' && !cell.productive) return {}
  const herb = strategyPlan.value?.byCell.get(cell.index)
  const hash = herb ? (Math.imul(herb.item_id, 2654435761) >>> 0) : 105
  const hue = cell.productive ? 198 : herb ? hash % 360 : 105
  const coefficient = Math.max(1, Number(cell.coefficient) || 1)
  const depth = maxPlotCoefficient.value <= 1 ? 0 : (coefficient - 1) / (maxPlotCoefficient.value - 1)
  const saturation = cell.productive ? 44 : herb ? 48 : 34
  const lightness = 96 - depth * 14
  return {
    backgroundColor: `hsl(${hue} ${saturation}% ${lightness}%)`,
    borderColor: `hsl(${hue} ${Math.min(68, saturation + 10)}% ${Math.max(48, lightness - 24)}%)`,
  }
}

async function saveCurrentPlan() {
  if (!solution.value) {
    ElMessage.warning('请先完成求解')
    return
  }
  const defaultName = `${realm.value} · ${productionType.value === 'herb' ? '灵田布局' : '灵池布局'}`
  try {
    const { value } = await ElMessageBox.prompt('为当前布局和策略命名', '保存方案', {
      inputValue: defaultName,
      inputPattern: /\S+/,
      inputErrorMessage: '请输入方案名称',
      confirmButtonText: '保存',
      cancelButtonText: '取消',
    })
    const plan: SavedPlan = {
      id: `${Date.now()}`,
      name: value.trim(),
      updatedAt: Date.now(),
      plotCount: plotCount.value,
      realm: realm.value,
      productionType: productionType.value,
      specialCellCount: specialCellCount.value,
      enabledBonusIds: [...enabledBonusIds.value],
      solution: solution.value,
      strategyRows: cloneData(strategyRows.value),
    }
    savedPlans.value.unshift(plan)
    selectedPlanId.value = plan.id
    persistSavedPlans()
    ElMessage.success('方案已保存')
  } catch {
    // 用户取消保存。
  }
}

async function deleteSavedPlan(planId: string) {
  const index = savedPlans.value.findIndex(item => item.id === planId)
  if (index < 0) return
  const plan = savedPlans.value[index]
  try {
    await ElMessageBox.confirm(`删除方案“${plan.name}”？`, '删除方案', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
    savedPlans.value.splice(index, 1)
    if (selectedPlanId.value === planId) selectedPlanId.value = ''
    persistSavedPlans()
    persistSettings()
    ElMessage.success('方案已删除')
  } catch {
    // 用户取消删除。
  }
}

async function loadSavedPlan(planId: string) {
  const plan = savedPlans.value.find(item => item.id === planId)
  if (!plan) return
  plotCount.value = plan.plotCount
  realm.value = plan.realm
  productionType.value = plan.productionType
  specialCellCount.value = plan.specialCellCount
  enabledBonusIds.value = [...plan.enabledBonusIds]
  await nextTick()
    strategyRows.value = cloneData(plan.strategyRows)
  solution.value = cloneData(plan.solution)
  solvedConfigKey.value = currentConfigKey()
  selectedPlanId.value = plan.id
  persistSettings()
}

onMounted(() => {
  restoreSettings()
  restoreSavedPlans()
  void load()
})
</script>

<template>
  <div class="pasture-page">
    <header class="page-header">
      <h1>洞天求解</h1>
      <div class="config-row">
        <el-select :model-value="realm" class="realm-select" @update:model-value="selectRealm">
          <el-option v-for="item in REALMS" :key="item.label" :label="`${item.label} · ${item.count}格`" :value="item.label" />
        </el-select>
        <label class="special-label">特殊格子</label>
        <el-input-number v-model="specialCellCount" class="special-count" :min="0" :max="plotCount" controls-position="right" aria-label="特殊格子数量" />
      </div>
      <div class="config-row">
        <el-select v-model="productionType" class="production-select" aria-label="生产类型">
          <el-option label="灵田布局" value="herb" />
          <el-option label="灵池布局" value="pool" />
        </el-select>
        <div v-for="building in bonusBuildings" :key="building.build_id" class="bonus-toggle">
          <span>{{ building.name }}</span>
          <el-switch :model-value="enabledBonusIds.includes(building.build_id)" :aria-label="`${building.name}允许使用`" @update:model-value="toggleBonus(building.build_id, Boolean($event))" />
        </div>
      </div>
      <div class="action-row">
        <el-button type="primary" :loading="solving" @click="solve">求解</el-button>
        <el-button :disabled="!solution" @click="saveCurrentPlan">保存方案</el-button>
      </div>
    </header>

    <main class="workspace" :class="{ 'has-saved-plans': savedPlans.length }">
      <section class="board-panel">
        <div class="section-title">
          <span>最优布局</span>
          <small v-if="solution">
            {{ Math.round(solution.total_value) }}倍
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
              ...plotVisualStyle(cell),
            }"
          >
            <template v-if="cell.kind === 'plot'">
              <img v-if="strategyPlan?.byCell.get(cell.index)?.icon_url" class="crop-icon" :src="strategyPlan?.byCell.get(cell.index)?.icon_url" alt="" />
              <div class="plot-title">
                <span class="plot-name">{{ strategyPlan?.byCell.get(cell.index)?.name || '灵田' }}</span>
                <small>×{{ cell.coefficient || 1 }}</small>
              </div>
              <small v-if="!strategyPlan && ((cell.speed_count || 0) || (cell.yield_count || 0))" class="factor-detail">
                泉×{{ 1 + (cell.speed_count || 0) }} · 枢×{{ 1 + (cell.yield_count || 0) }}
              </small>
            </template>
            <template v-else>
              <img v-if="!cell.productive && buildingById.get(cell.building_id || 0)?.image_url" :src="buildingById.get(cell.building_id || 0)?.image_url" alt="" />
              <div v-if="cell.productive" class="plot-title">
                <span>{{ buildingById.get(cell.building_id || 0)?.name }}</span>
                <small>×{{ cell.coefficient || 1 }}</small>
              </div>
              <span v-else>{{ cell.building_id === -1 ? '特殊格' : buildingById.get(cell.building_id || 0)?.name }}</span>
            </template>
          </div>
        </div>
        <section v-if="solution && productionType === 'herb'" class="strategy-panel">
          <div class="strategy-title">
            <span>种植策略</span>
            <el-button size="small" @click="addStrategyRow">+</el-button>
            <strong v-if="strategyPlan">每生产 1 份约需 {{ strategyPlan.daysPerUnit.toFixed(2) }} 天</strong>
          </div>
          <div v-if="strategyRows.length" class="strategy-columns">
            <span>品级</span>
            <span>药材</span>
            <span>需求数量</span>
            <span>产出比例</span>
            <span></span>
          </div>
          <div v-for="(row, index) in strategyRows" :key="index" class="strategy-row">
            <el-select v-model="row.grade" class="grade-select" placeholder="品级" @change="row.herbId = ''">
              <el-option v-for="grade in herbMeta?.grades || []" :key="grade.grade_id" :label="grade.name" :value="grade.name" />
            </el-select>
            <el-select v-model="row.herbId" class="herb-select" filterable placeholder="药材">
              <el-option v-for="herb in herbsForGrade(row.grade)" :key="herb.item_id" :label="herb.name" :value="herb.item_id">
                <span class="herb-option"><img :src="herb.icon_url" alt="" />{{ herb.name }}</span>
              </el-option>
            </el-select>
            <el-input-number v-model="row.demand" class="demand-input" :min="1" :max="999" controls-position="right" aria-label="需求数量" />
            <el-input class="ratio-input" :model-value="strategyRatio(row.herbId)" readonly aria-label="相对产出比例" />
            <el-button class="remove-row" text type="danger" :aria-label="`删除第${index + 1}行`" @click="removeStrategyRow(index)">−</el-button>
          </div>
          <small v-if="strategyRows.length && !strategyPlan" class="strategy-hint">选择药材后，会在布局中标注对应的种植位置。</small>
        </section>
      </section>
      <aside v-if="savedPlans.length" class="saved-plans">
        <div class="saved-plans-title">已保存方案</div>
        <button
          v-for="plan in savedPlans" :key="plan.id" type="button" class="saved-plan-row"
          :class="{ active: selectedPlanId === plan.id }" @click="loadSavedPlan(plan.id)"
        >
          <span class="saved-plan-info">
            <b>{{ plan.name }}</b>
            <small>{{ plan.realm }} · {{ plan.productionType === 'herb' ? '灵田布局' : '灵池布局' }} · {{ plan.solution.total_value.toFixed(0) }}倍</small>
          </span>
          <span class="saved-plan-delete" role="button" :aria-label="`删除方案${plan.name}`" @click.stop="deleteSavedPlan(plan.id)">−</span>
        </button>
      </aside>
    </main>
  </div>
</template>

<style scoped>
.pasture-page { box-sizing: border-box; height: 100%; min-height: 0; overflow: hidden; padding: 18px 22px; display: flex; flex-direction: column; gap: 16px; }
.page-header { flex: none; display: flex; flex-direction: column; align-items: flex-start; gap: 12px; }
.page-header h1 { margin: 0; font-size: 22px; }
.config-row, .action-row { display: flex; align-items: center; flex-wrap: wrap; gap: 10px 12px; min-height: 32px; }
.config-row label { color: var(--el-text-color-regular); white-space: nowrap; }
.config-row :deep(.el-input-number) { width: 112px; }
.config-row :deep(.special-count) { width: 72px; }
.special-label { font-size: 13px; }
.realm-select { width: 126px; }
.production-select { width: 118px; }
.bonus-toggle { display: flex; align-items: center; gap: 6px; color: var(--el-text-color-regular); }
.count-summary { color: var(--el-text-color-secondary); }
.count-summary.invalid { color: var(--el-color-danger); }
.section-title { display: flex; align-items: baseline; gap: 8px; font-weight: 600; margin-bottom: 8px; }
.section-title small { margin-left: 8px; color: var(--el-text-color-secondary); font-weight: 400; }
.workspace { min-height: 0; flex: 1; display: grid; grid-template-columns: minmax(420px, 1fr); gap: 20px; }
.workspace.has-saved-plans { grid-template-columns: minmax(420px, 1fr) 280px; }
.board-panel { min-height: 0; height: 100%; overflow: auto; }
.board { display: grid; gap: 8px; width: max-content; padding: 2px; }
.plot-cell { width: 88px; height: 88px; box-sizing: border-box; border: 1px solid var(--el-border-color); background: #eef5e8; color: var(--el-text-color-primary); display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 4px; }
.plot-cell.building { background: var(--el-fill-color-light); }
.plot-cell img { width: 56px; height: 48px; object-fit: contain; }
.plot-cell .crop-icon { width: 30px; height: 30px; }
.plot-title { display: flex; align-items: baseline; gap: 4px; }
.plot-cell small { color: var(--el-color-success); }
.strategy-panel { width: max-content; min-width: 520px; margin-top: 20px; padding-top: 14px; border-top: 1px solid var(--el-border-color-lighter); }
.strategy-title { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; font-weight: 600; }
.strategy-title strong { margin-left: 8px; color: var(--el-color-success); font-weight: 500; }
.strategy-columns { display: grid; grid-template-columns: 120px 170px 88px 68px 28px; gap: 8px; margin-bottom: 5px; color: var(--el-text-color-secondary); font-size: 12px; }
.strategy-columns span { padding-left: 10px; }
.strategy-row { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.grade-select { width: 120px; }
.herb-select { width: 170px; }
.strategy-row :deep(.demand-input) { width: 88px; }
.strategy-row :deep(.ratio-input) { width: 68px; }
.strategy-row :deep(.ratio-input input) { text-align: center; color: var(--el-text-color-regular); }
.remove-row { width: 28px; padding: 0; }
.strategy-hint { color: var(--el-text-color-secondary); }
.herb-option { display: inline-flex; align-items: center; gap: 6px; }
.herb-option img { width: 22px; height: 22px; object-fit: contain; }
.saved-plans { min-height: 0; overflow: auto; border-left: 1px solid var(--el-border-color-lighter); padding-left: 16px; }
.saved-plans-title { margin-bottom: 8px; font-weight: 600; }
.saved-plan-row { width: 100%; border: 0; border-bottom: 1px solid var(--el-border-color-lighter); background: transparent; display: flex; align-items: center; gap: 8px; padding: 10px 4px; text-align: left; color: inherit; cursor: pointer; }
.saved-plan-row:hover, .saved-plan-row.active { background: var(--el-fill-color-light); }
.saved-plan-info { min-width: 0; flex: 1; display: flex; flex-direction: column; gap: 3px; }
.saved-plan-info b, .saved-plan-info small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.saved-plan-info small { color: var(--el-text-color-secondary); }
.saved-plan-delete { flex: none; color: var(--el-color-danger); font-size: 18px; padding: 2px 6px; }
@media (max-width: 900px) { .workspace.has-saved-plans { grid-template-columns: 1fr; } .board-panel { height: auto; } .saved-plans { border-left: 0; border-top: 1px solid var(--el-border-color-lighter); padding: 14px 0 0; } }
</style>
