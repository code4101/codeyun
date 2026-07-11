<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Search } from '@element-plus/icons-vue'
import { useRoute, useRouter } from 'vue-router'

import {
  fetchZaohuaAlchemyMeta,
  fetchZaohuaAlchemyRecipe,
  fetchZaohuaAlchemyRecipes,
  fetchZaohuaFurnaces,
  solveZaohuaAlchemy,
  type ZaohuaAlchemyMeta,
  type ZaohuaAlchemyRecipe,
  type ZaohuaAlchemySolution,
  type ZaohuaAlchemySolveResult,
  type ZaohuaAlchemyValueMetric,
  type ZaohuaFurnace,
} from '@/api/zaohua'
import SortableOrderHandle from '@/components/SortableOrderHandle.vue'
import StandardPagination from '@/components/StandardPagination.vue'
import { mixWeightedColors, toHex } from '@/utils/colorMath'
import { formatChineseCompactNumber } from '@/utils/numberFormat'
import { useResizablePane } from '@/utils/useResizablePane'
import { useSortableList } from '@/utils/useSortableList'
import GradeMeter from '../components/GradeMeter.vue'
import AlchemyFormulaDiagram from '../components/AlchemyFormulaDiagram.vue'
import '../catalog-inspector.css'

const GRADE_FILTER_STORAGE_KEY = 'zaohua:alchemy:grade-filter'

const loadStoredGradeFilter = () => {
  try {
    return window.localStorage.getItem(GRADE_FILTER_STORAGE_KEY) || ''
  } catch {
    return ''
  }
}

const route = useRoute()
const router = useRouter()
const loading = ref(false)
const meta = ref<ZaohuaAlchemyMeta | null>(null)
const recipes = ref<ZaohuaAlchemyRecipe[]>([])
const selected = ref<ZaohuaAlchemyRecipe | null>(null)
const query = ref('')
const grade = ref(loadStoredGradeFilter())
const page = ref(1)
const pageSize = ref(40)
const total = ref(0)
const sortBy = ref<'number' | 'grade'>('grade')
const sortOrder = ref<'asc' | 'desc'>('asc')
const furnaces = ref<ZaohuaFurnace[]>([])
const selectedFurnaceId = ref(0)
const solving = ref(false)
const solveResult = ref<ZaohuaAlchemySolveResult | null>(null)
const solveError = ref('')
type SolverHerbOption = {
  item_id: number
  name: string
  price: number
  icon_path: string
  icon_url: string
}
const solverHerbs = ref<SolverHerbOption[]>([])
const disabledSolverHerbIds = ref<number[]>([])
const valueSortListRef = ref<HTMLElement | null>(null)
const solverLimit = ref(5)
let searchTimer = 0
let requestSequence = 0
let solveRequestSequence = 0

const FURNACE_SELECTION_STORAGE_KEY = 'zaohua:alchemy:furnace-item-id'
const VALUE_SORT_STORAGE_KEY = 'zaohua:alchemy:value-sort-program'
const SOLVER_HERB_STATE_STORAGE_PREFIX = 'zaohua:alchemy:solver-herbs:'
const SOLVER_RESULT_CACHE_PREFIX = 'zaohua:alchemy:solver-result:'
const SOLVER_CACHE_SCHEMA_VERSION = 4
const DEFAULT_VALUE_SORT_METRICS: ZaohuaAlchemyValueMetric[] = [
  'output_input_ratio',
  'net_profit',
  'profit_rate',
]
const VALUE_METRIC_LABELS: Record<ZaohuaAlchemyValueMetric, string> = {
  output_input_ratio: '产出比',
  net_profit: '净利润',
  profit_rate: '日利润',
}

const normalizeValueSortMetrics = (value: unknown): ZaohuaAlchemyValueMetric[] => {
  if (!Array.isArray(value)) return [...DEFAULT_VALUE_SORT_METRICS]
  const normalized = value.filter(
    (item): item is ZaohuaAlchemyValueMetric => DEFAULT_VALUE_SORT_METRICS.includes(item as ZaohuaAlchemyValueMetric),
  )
  return normalized.length === DEFAULT_VALUE_SORT_METRICS.length
    && new Set(normalized).size === DEFAULT_VALUE_SORT_METRICS.length
    ? normalized
    : [...DEFAULT_VALUE_SORT_METRICS]
}

const loadValueSortMetrics = () => {
  try {
    return normalizeValueSortMetrics(JSON.parse(window.localStorage.getItem(VALUE_SORT_STORAGE_KEY) || 'null'))
  } catch {
    return [...DEFAULT_VALUE_SORT_METRICS]
  }
}

const valueSortMetrics = ref<ZaohuaAlchemyValueMetric[]>(loadValueSortMetrics())
const selectedFurnace = computed(() => furnaces.value.find(
  item => item.item_id === selectedFurnaceId.value,
) || null)

const formatCraftingTime = (days: number) => {
  const normalizedDays = Math.max(0, Math.trunc(Number(days) || 0))
  if (normalizedDays >= 360 && normalizedDays % 360 === 0) {
    return `${normalizedDays / 360} 年（${normalizedDays} 天）`
  }
  return `${normalizedDays} 天`
}

const loadFurnaceSelection = () => {
  try {
    return Number(window.localStorage.getItem(FURNACE_SELECTION_STORAGE_KEY) || 0)
  } catch {
    return 0
  }
}

const solverHerbStateStorageKey = (recipeId: number) => `${SOLVER_HERB_STATE_STORAGE_PREFIX}${recipeId}`

const loadSolverHerbState = (recipeId: number) => {
  try {
    const saved = JSON.parse(window.localStorage.getItem(solverHerbStateStorageKey(recipeId)) || '{}') as {
      herbs?: SolverHerbOption[]
      disabled_ids?: number[]
    }
    solverHerbs.value = Array.isArray(saved.herbs) ? saved.herbs : []
    disabledSolverHerbIds.value = Array.isArray(saved.disabled_ids)
      ? saved.disabled_ids.filter(Number.isInteger)
      : []
  } catch {
    solverHerbs.value = []
    disabledSolverHerbIds.value = []
  }
}

const saveSolverHerbState = (recipeId: number) => {
  try {
    window.localStorage.setItem(solverHerbStateStorageKey(recipeId), JSON.stringify({
      herbs: solverHerbs.value,
      disabled_ids: disabledSolverHerbIds.value,
    }))
  } catch {
    // The solver remains usable when browser storage is unavailable.
  }
}

const solverResultCacheKey = (recipe: ZaohuaAlchemyRecipe, limit: number) => {
  const excluded = [...disabledSolverHerbIds.value]
    .sort((a, b) => a - b)
    .join(',')
  return [
    SOLVER_RESULT_CACHE_PREFIX,
    `v${SOLVER_CACHE_SCHEMA_VERSION}`,
    recipe.source_build_id || 'unknown',
    recipe.recipe_id,
    `f${selectedFurnaceId.value || '-'}`,
    `e${excluded || '-'}`,
    `s${valueSortMetrics.value.join('.')}`,
    `l${limit}`,
  ].join(':')
}

const loadCachedSolverResult = (recipe: ZaohuaAlchemyRecipe, limit: number) => {
  try {
    const cached = JSON.parse(window.localStorage.getItem(solverResultCacheKey(recipe, limit)) || 'null') as {
      schema_version?: number
      result?: ZaohuaAlchemySolveResult
    } | null
    return cached?.schema_version === SOLVER_CACHE_SCHEMA_VERSION && cached.result
      ? cached.result
      : null
  } catch {
    return null
  }
}

const saveCachedSolverResult = (
  recipe: ZaohuaAlchemyRecipe,
  limit: number,
  result: ZaohuaAlchemySolveResult,
) => {
  try {
    window.localStorage.setItem(solverResultCacheKey(recipe, limit), JSON.stringify({
      schema_version: SOLVER_CACHE_SCHEMA_VERSION,
      cached_at: Date.now(),
      result,
    }))
  } catch {
    // Solver results are a disposable cache; quota failures can be ignored.
  }
}

const applySolverResult = (result: ZaohuaAlchemySolveResult) => {
  const herbsById = new Map(solverHerbs.value.map(item => [item.item_id, item]))
  for (const herb of result.available_herbs ?? []) herbsById.set(herb.item_id, herb)
  solverHerbs.value = [...herbsById.values()].sort(
    (left, right) => left.price - right.price || left.item_id - right.item_id,
  )
  solveResult.value = result
  if (selected.value) saveSolverHerbState(selected.value.recipe_id)
}

const restoreCachedSolverResult = () => {
  const cached = selected.value
    ? loadCachedSolverResult(selected.value, solverLimit.value)
    : null
  if (cached) applySolverResult(cached)
  else solveResult.value = null
}

const {
  paneHeight: listPaneHeight,
  isResizing,
  startResizing,
} = useResizablePane({
  initialHeight: 430,
  getAdaptiveHeight: () => Math.floor(Math.max(500, window.innerHeight - 220) * 0.56),
  getResizeBounds: () => ({
    min: 250,
    max: Math.max(340, window.innerHeight - 390),
  }),
  storageKey: 'zaohua:alchemy:list-pane-height',
})
const listPaneStyle = computed(() => ({ height: `${listPaneHeight.value}px` }))

const ingredientText = (recipe: ZaohuaAlchemyRecipe) => recipe.example_items
  .map(item => `${item.name} ${item.count ?? 0}`)
  .join(' ')

const hideBrokenImage = (event: Event) => {
  const image = event.currentTarget as HTMLImageElement | null
  if (image) image.style.visibility = 'hidden'
}

type GradeVisual = {
  grade_color_hex?: string
  color_hex?: string
}

const gradeStyle = (item: GradeVisual) => ({
  '--grade-color': item.grade_color_hex || item.color_hex || '#757575',
})

const gradeOptionByName = (name: string) => meta.value?.grades.find(item => item.name === name)

const ELEMENT_COLORS: Record<string, string> = {
  gold: '#9a6b00',
  water: '#1769aa',
  wood: '#2f7d32',
  fire: '#c43b2f',
  soil: '#8a5a2b',
  earth: '#8a5a2b',
  ice: '#2f8798',
  wind: '#527c72',
  thunder: '#7449a8',
}

const elementStyle = (element: string) => ({
  '--element-color': ELEMENT_COLORS[element.toLowerCase()] || '#4f5960',
})

const mixedElementColor = (limits: ZaohuaAlchemyRecipe['attr_limits']) => {
  const entries = limits
    .map(item => ({
      color: ELEMENT_COLORS[item.element.toLowerCase()] || '#4f5960',
      weight: Number(item.value) || 0,
    }))
    .filter(item => item.weight > 0)
  const totalWeight = entries.reduce((sum, item) => sum + item.weight, 0)
  const mixed = mixWeightedColors(entries, { fillToWeight: totalWeight })
  return mixed ? toHex(mixed) : '#2f3437'
}

const formatPrice = (value?: number) => Number.isFinite(value)
  ? formatChineseCompactNumber(value)
  : '—'

const formatRatio = (value: number | null) => value == null ? '—' : value.toFixed(2)

const formatMetricNumber = (value: number) => {
  if (!Number.isFinite(value)) return '—'
  const sign = value < 0 ? '-' : ''
  const magnitude = Math.abs(value)
  if (magnitude >= 10_000) return `${sign}${formatChineseCompactNumber(magnitude)}`
  return `${sign}${Number(magnitude.toFixed(2))}`
}

const formatValueMetric = (
  solution: ZaohuaAlchemySolution,
  metric: ZaohuaAlchemyValueMetric,
) => {
  if (metric === 'output_input_ratio') return formatRatio(solution.output_input_ratio)
  if (metric === 'profit_rate') {
    return solution.profit_rate == null ? '—' : `${formatMetricNumber(solution.profit_rate)}/天`
  }
  return formatMetricNumber(solution.net_profit)
}

const requestSolverResults = async () => {
  if (!selected.value || !selectedFurnace.value) return
  const recipe = selected.value
  const cachedResult = loadCachedSolverResult(recipe, solverLimit.value)
  if (cachedResult) {
    solveRequestSequence += 1
    solving.value = false
    solveError.value = ''
    applySolverResult(cachedResult)
    return
  }
  const sequence = ++solveRequestSequence
  solving.value = true
  solveError.value = ''
  try {
    const result = await solveZaohuaAlchemy(recipe.recipe_id, {
      furnace_item_id: selectedFurnace.value.item_id,
      limit: solverLimit.value,
      excluded_item_ids: disabledSolverHerbIds.value,
      sort_metrics: valueSortMetrics.value,
    })
    if (sequence === solveRequestSequence) {
      applySolverResult(result)
      saveCachedSolverResult(recipe, solverLimit.value, result)
    }
  } catch (error) {
    if (sequence === solveRequestSequence) {
      solveResult.value = null
      solveError.value = error instanceof Error ? error.message : '求解失败'
    }
  } finally {
    if (sequence === solveRequestSequence) solving.value = false
  }
}

const runSolver = () => {
  solverLimit.value = 5
  return requestSolverResults()
}

const loadMoreSolutions = () => {
  solverLimit.value += 5
  return requestSolverResults()
}

const toggleSolverHerb = (itemId: number) => {
  disabledSolverHerbIds.value = disabledSolverHerbIds.value.includes(itemId)
    ? disabledSolverHerbIds.value.filter(id => id !== itemId)
    : [...disabledSolverHerbIds.value, itemId]
  if (selected.value) saveSolverHerbState(selected.value.recipe_id)
  solverLimit.value = 5
  void requestSolverResults()
}

const reorderValueMetric = (oldIndex: number, newIndex: number) => {
  const next = [...valueSortMetrics.value]
  const [moved] = next.splice(oldIndex, 1)
  if (!moved) return
  next.splice(newIndex, 0, moved)
  valueSortMetrics.value = next
  try {
    window.localStorage.setItem(VALUE_SORT_STORAGE_KEY, JSON.stringify(next))
  } catch {
    // The current sort program remains usable when browser storage is unavailable.
  }
  solverLimit.value = 5
  void requestSolverResults()
}

const promoteValueMetric = (index: number) => {
  if (index > 0) reorderValueMetric(index, 0)
}

useSortableList({
  listRef: valueSortListRef,
  getDeps: () => [valueSortListRef.value, valueSortMetrics.value.join(',')],
  onReorder: reorderValueMetric,
})

const toggleSort = (field: 'number' | 'grade') => {
  if (sortBy.value === field) {
    sortOrder.value = sortOrder.value === 'asc' ? 'desc' : 'asc'
  } else {
    sortBy.value = field
    sortOrder.value = 'asc'
  }
  page.value = 1
  void loadRecipes()
}

const sortMark = (field: 'number' | 'grade') => sortBy.value === field
  ? (sortOrder.value === 'asc' ? '↑' : '↓')
  : '↕'

const selectRecipe = async (recipe: ZaohuaAlchemyRecipe, updateRoute = true) => {
  selected.value = recipe
  if (updateRoute) {
    await router.replace({
      query: {
        ...route.query,
        recipe_id: String(recipe.recipe_id),
      },
    })
  }
}

const loadMeta = async () => {
  meta.value = await fetchZaohuaAlchemyMeta()
}

const loadFurnaces = async () => {
  const response = await fetchZaohuaFurnaces({
    sort_by: 'number',
    sort_order: 'asc',
    page: 1,
    page_size: 100,
  })
  furnaces.value = response.items
  const savedId = loadFurnaceSelection()
  selectedFurnaceId.value = furnaces.value.some(item => item.item_id === savedId)
    ? savedId
    : (furnaces.value[0]?.item_id || 0)
}

const loadRecipes = async () => {
  const sequence = ++requestSequence
  loading.value = true
  try {
    const response = await fetchZaohuaAlchemyRecipes({
      q: query.value.trim(),
      grade: grade.value,
      sort_by: sortBy.value,
      sort_order: sortOrder.value,
      page: page.value,
      page_size: pageSize.value,
    })
    if (sequence !== requestSequence) return
    recipes.value = response.items
    total.value = response.total

    const routeId = Number(route.query.recipe_id || 0)
    const preferredId = routeId || selected.value?.recipe_id || 0
    const visibleSelected = recipes.value.find(item => item.recipe_id === preferredId)
    if (visibleSelected) {
      selected.value = visibleSelected
      return
    }
    if (routeId > 0) {
      try {
        selected.value = await fetchZaohuaAlchemyRecipe(routeId)
        return
      } catch {
        // The route may point to an old build; fall back to the current page.
      }
    }
    selected.value = recipes.value[0] || null
  } finally {
    if (sequence === requestSequence) loading.value = false
  }
}

watch(query, () => {
  window.clearTimeout(searchTimer)
  searchTimer = window.setTimeout(() => {
    page.value = 1
    void loadRecipes()
  }, 250)
})

watch(grade, () => {
  try {
    if (grade.value) window.localStorage.setItem(GRADE_FILTER_STORAGE_KEY, grade.value)
    else window.localStorage.removeItem(GRADE_FILTER_STORAGE_KEY)
  } catch {
    // Filtering still works when browser storage is unavailable.
  }
  page.value = 1
  void loadRecipes()
})

watch(pageSize, () => {
  page.value = 1
  void loadRecipes()
})

watch(page, () => {
  void loadRecipes()
})

watch(selectedFurnaceId, (itemId) => {
  try {
    if (itemId > 0) window.localStorage.setItem(FURNACE_SELECTION_STORAGE_KEY, String(itemId))
  } catch {
    // The current selection remains usable when browser storage is unavailable.
  }
  solveRequestSequence += 1
  solverLimit.value = 5
  restoreCachedSolverResult()
})

watch(() => selected.value?.recipe_id, () => {
  solveRequestSequence += 1
  if (selected.value) {
    loadSolverHerbState(selected.value.recipe_id)
  }
  else {
    solverHerbs.value = []
    disabledSolverHerbIds.value = []
  }
  solverLimit.value = 5
  restoreCachedSolverResult()
  solveError.value = ''
})

onMounted(async () => {
  await Promise.all([loadMeta(), loadFurnaces(), loadRecipes()])
})

onBeforeUnmount(() => {
  window.clearTimeout(searchTimer)
})
</script>

<template>
  <main class="alchemy-page zaohua-catalog-page" :class="{ resizing: isResizing }">
    <header class="page-head">
      <div>
        <h1>造化仙缘 · 丹药</h1>
        <p>丹药、五行需求与炉内规则的静态逆向数据。</p>
      </div>
    </header>

    <section class="toolbar">
      <el-input
        v-model="query"
        class="search-input"
        clearable
        :prefix-icon="Search"
        placeholder="搜索丹药、药材或规则"
      />
      <el-select v-model="grade" class="grade-select" placeholder="全部品阶" clearable>
        <template #label="{ value }">
          <span v-if="gradeOptionByName(String(value || ''))" class="grade-filter-selection">
            <span class="grade-rank">{{ gradeOptionByName(String(value || ''))?.order }}</span>
            <GradeMeter
              class="grade-filter-meter"
              :rank="gradeOptionByName(String(value || ''))?.order"
              :label="String(value)"
            />
          </span>
        </template>
        <el-option
          v-for="item in meta?.grades || []"
          :key="item.name"
          :label="item.name"
          :value="item.name"
        >
          <span class="grade-option">
            <span class="grade-filter-selection">
              <span class="grade-rank">{{ item.order }}</span>
              <GradeMeter class="grade-filter-meter" :rank="item.order" :label="item.name" />
            </span>
            <em>{{ item.count }}</em>
          </span>
        </el-option>
      </el-select>
    </section>

    <section class="list-pane" :style="listPaneStyle" v-loading="loading">
      <div class="table-scroll">
        <table class="recipe-table zaohua-catalog-table">
          <thead>
            <tr>
              <th class="number-column" :aria-sort="sortBy === 'number' ? (sortOrder === 'asc' ? 'ascending' : 'descending') : 'none'">
                <button type="button" class="sort-button" :class="{ active: sortBy === 'number' }" @click="toggleSort('number')">
                  <span>编号</span><span class="sort-mark">{{ sortMark('number') }}</span>
                </button>
              </th>
              <th class="icon-column">图标</th>
              <th>丹药</th>
              <th>成丹</th>
              <th :aria-sort="sortBy === 'grade' ? (sortOrder === 'asc' ? 'ascending' : 'descending') : 'none'">
                <button type="button" class="sort-button" :class="{ active: sortBy === 'grade' }" @click="toggleSort('grade')">
                  <span>品级</span><span class="sort-mark">{{ sortMark('grade') }}</span>
                </button>
              </th>
              <th>价格</th>
              <th>作用</th>
              <th>耐药</th>
              <th>五行需求</th>
              <th>炼丹药材</th>
              <th class="fill-column" aria-hidden="true"></th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="recipe in recipes"
              :key="recipe.recipe_id"
              :class="{ selected: selected?.recipe_id === recipe.recipe_id }"
              @click="selectRecipe(recipe)"
            >
              <td class="number-cell">{{ recipe.recipe_id }}</td>
              <td class="icon-cell">
                <img
                  v-if="recipe.output.icon_url"
                  :src="recipe.output.icon_url"
                  :alt="recipe.output.name"
                  loading="lazy"
                  @error="hideBrokenImage"
                />
              </td>
              <td>
                <GradeMeter
                  class="output-grade-meter"
                  :rank="recipe.output.grade_rank"
                  :label="recipe.output.name"
                  :text-color="mixedElementColor(recipe.attr_limits)"
                  :title="recipe.output.grade_name"
                />
              </td>
              <td class="number-cell">{{ recipe.output.count }}</td>
              <td class="number-cell">{{ recipe.output.grade_rank || '—' }}</td>
              <td class="number-cell">{{ formatPrice(recipe.output.price) }}</td>
              <td class="effect-cell" :title="recipe.output.effect_text">{{ recipe.output.effect_text || '—' }}</td>
              <td class="number-cell" :title="recipe.output.drug_max ? `每次服用增加 ${recipe.output.add_drug_tolerance || 0} 点，累计达到 ${recipe.output.drug_max} 后耐药` : ''">
                {{ recipe.output.drug_max || '—' }}
              </td>
              <td>
                <span v-if="recipe.attr_limits.length" class="element-list">
                  <span
                    v-for="item in recipe.attr_limits"
                    :key="item.element"
                    class="element-value"
                    :style="elementStyle(item.element)"
                  >{{ item.label }}{{ item.value }}</span>
                </span>
                <span v-else>—</span>
              </td>
              <td class="ingredients-cell" :title="ingredientText(recipe)">
                <span v-if="recipe.example_items.length" class="ingredient-chips">
                  <span v-for="item in recipe.example_items" :key="item.item_id" class="ingredient-chip">
                    <GradeMeter
                      class="ingredient-grade-meter"
                      :rank="item.grade_rank"
                      :label="item.name"
                      :text-color="mixedElementColor(item.crafting_attributes || [])"
                      :title="item.grade_name"
                    />
                    <em>{{ item.count }}</em>
                  </span>
                </span>
                <span v-else>—</span>
              </td>
              <td class="fill-column" aria-hidden="true"></td>
            </tr>
          </tbody>
        </table>
        <div v-if="!loading && !recipes.length" class="empty-state">没有匹配的丹药</div>
      </div>
      <StandardPagination
        class="pagination"
        :page="page"
        :page-size="pageSize"
        :total="total"
        :page-size-options="[20, 40, 80, 160]"
        align="right"
        @update:page="value => page = value"
        @update:page-size="value => pageSize = value"
      />
    </section>

    <div class="pane-resizer" @mousedown="startResizing">
      <span></span>
    </div>

    <section v-if="selected" class="detail-pane">
      <header class="detail-head">
        <div class="detail-title">
          <img
            v-if="selected.output.icon_url"
            :src="selected.output.icon_url"
            :alt="selected.output.name"
            @error="hideBrokenImage"
          />
          <div>
            <div class="detail-name-row">
              <h2 class="grade-text" :style="gradeStyle(selected.output)">{{ selected.output.name }}</h2>
              <span v-if="selected.output.grade_name" class="grade-label" :style="gradeStyle(selected.output)">
                <i></i>
                <span>{{ selected.output.grade_name }}</span>
              </span>
              <span v-else class="ungraded-label">未标品阶</span>
            </div>
            <p>成丹 {{ selected.output.count }}</p>
          </div>
        </div>
      </header>

      <dl class="detail-fields">
        <dt>作用</dt>
        <dd>{{ selected.output.effect_text || '—' }}</dd>

        <dt>说明</dt>
        <dd>{{ selected.output.description || '—' }}</dd>

        <dt>耐药性</dt>
        <dd v-if="selected.output.drug_max">
          每次服用增加 {{ selected.output.add_drug_tolerance || 0 }} 点，累计达到 {{ selected.output.drug_max }} 后耐药
        </dd>
        <dd v-else>无</dd>

        <dt>五行需求</dt>
        <dd>
          <ul class="detail-element-list">
            <li v-for="item in selected.attr_limits" :key="item.element">
              <span class="element-text" :style="elementStyle(item.element)">{{ item.label }}系</span>
              <span class="element-text element-number" :style="elementStyle(item.element)">{{ item.value }}</span>
            </li>
          </ul>
        </dd>

        <dt>炼丹药材</dt>
        <dd>
          <ul class="plain-list">
            <li v-for="item in selected.example_items" :key="item.item_id">
              <span class="ingredient-name">
                <img
                  v-if="item.icon_url"
                  :src="item.icon_url"
                  :alt="item.name"
                  loading="lazy"
                  @error="hideBrokenImage"
                />
                <span class="ingredient-text">
                  <GradeMeter
                    class="ingredient-grade-meter"
                    :rank="item.grade_rank"
                    :label="item.name"
                    :text-color="mixedElementColor(item.crafting_attributes || [])"
                    :title="item.grade_name"
                  />
                  <em>{{ item.count }}</em>
                </span>
              </span>
            </li>
          </ul>
        </dd>

        <dt>炉内规则</dt>
        <dd>
          <ul class="rule-list">
            <li v-for="rule in selected.state_rules" :key="rule.state_id">
              <span>{{ rule.name || '未命名规则' }}</span>
            </li>
          </ul>
        </dd>

        <dt>炼丹时间</dt>
        <dd>{{ formatCraftingTime(selected.cost_days) }}</dd>

        <dt>丹炉</dt>
        <dd>
          <el-select
            v-model="selectedFurnaceId"
            class="furnace-select"
            filterable
            placeholder="选择丹炉"
          >
            <template #label>
              <span v-if="selectedFurnace" class="furnace-selection">
                <img :src="selectedFurnace.icon_url" :alt="selectedFurnace.name" />
                <strong>{{ selectedFurnace.name }}</strong>
                <small>
                  阳 {{ selectedFurnace.yang_grid_size.width }}×{{ selectedFurnace.yang_grid_size.height }}
                  · 阴 {{ selectedFurnace.yin_grid_size.width }}×{{ selectedFurnace.yin_grid_size.height }}
                </small>
              </span>
            </template>
            <el-option
              v-for="furnace in furnaces"
              :key="furnace.item_id"
              :label="furnace.name"
              :value="furnace.item_id"
            >
              <span class="furnace-option">
                <img :src="furnace.icon_url" :alt="furnace.name" />
                <strong>{{ furnace.name }}</strong>
                <small>
                  阳 {{ furnace.yang_grid_size.width }}×{{ furnace.yang_grid_size.height }}
                  · 阴 {{ furnace.yin_grid_size.width }}×{{ furnace.yin_grid_size.height }}
                </small>
              </span>
            </el-option>
          </el-select>
        </dd>

        <dt>求解</dt>
        <dd>
          <div class="solver-module">
            <div class="solver-actions">
              <el-button type="primary" size="small" :loading="solving" @click="runSolver">
                求解
              </el-button>
              <div class="value-sort-control">
                <span>排序</span>
                <ol ref="valueSortListRef" aria-label="价值指标排序程序">
                  <li v-for="(metric, index) in valueSortMetrics" :key="metric">
                    <SortableOrderHandle
                      :index="index"
                      :total="valueSortMetrics.length"
                      size="xs"
                      :pad="false"
                      :title="`点击将${VALUE_METRIC_LABELS[metric]}设为第一优先级；也可拖拽调整顺序`"
                      :aria-label="`点击将${VALUE_METRIC_LABELS[metric]}设为第一优先级，也可拖拽调整顺序`"
                      @click="promoteValueMetric(index)"
                    />
                    <span>{{ VALUE_METRIC_LABELS[metric] }}</span>
                  </li>
                </ol>
              </div>
              <span v-if="solveResult" class="solver-scope">
                {{ solveResult.exhaustive ? '已穷尽当前模型' : '当前搜索范围' }}
                · {{ solveResult.search_nodes.toLocaleString() }} 节点
              </span>
            </div>
            <ul v-if="solverHerbs.length" class="solver-herb-pool" aria-label="可用药材池">
              <li v-for="herb in solverHerbs" :key="herb.item_id">
                <button
                  type="button"
                  :class="{ 'is-disabled': disabledSolverHerbIds.includes(herb.item_id) }"
                  :aria-label="`${disabledSolverHerbIds.includes(herb.item_id) ? '恢复' : '停用'}${herb.name}`"
                  :aria-pressed="disabledSolverHerbIds.includes(herb.item_id)"
                  :title="herb.name"
                  @click="toggleSolverHerb(herb.item_id)"
                >
                  <img
                    v-if="herb.icon_url"
                    :src="herb.icon_url"
                    :alt="herb.name"
                    @error="hideBrokenImage"
                  />
                  <span v-else>{{ herb.name.slice(0, 1) }}</span>
                </button>
              </li>
            </ul>
            <p v-if="solveError" class="solver-error">{{ solveError }}</p>
            <p v-else-if="solveResult && !solveResult.solutions.length" class="solver-empty">
              {{ solveResult.exhaustive
                ? '当前炉形与单调配平范围内没有可行解。'
                : '当前搜索上限内尚未找到可行解。' }}
            </p>
            <ol v-else-if="solveResult" class="solution-list">
              <li v-for="solution in solveResult.solutions" :key="solution.rank" class="solution-item">
                <div class="solution-summary">
                  <strong class="solution-rank">{{ solution.rank }}</strong>
                  <dl>
                    <div
                      v-for="(metric, metricIndex) in valueSortMetrics"
                      :key="metric"
                      :class="{ 'is-primary-metric': metricIndex === 0 }"
                    >
                      <dt>{{ VALUE_METRIC_LABELS[metric] }}</dt>
                      <dd>{{ formatValueMetric(solution, metric) }}</dd>
                    </div>
                    <div>
                      <dt>成丹</dt>
                      <dd>{{ solution.base_yield }}<em v-if="solution.rule_bonus">+{{ solution.rule_bonus }}</em></dd>
                    </div>
                  </dl>
                  <i v-if="!solution.rule_supported" class="rule-pending">规则待补</i>
                </div>
                <AlchemyFormulaDiagram
                  :solution="solution"
                  :yang-width="solveResult.furnace.yang_grid_size.width"
                  :yang-height="solveResult.furnace.yang_grid_size.height"
                  :yin-width="solveResult.furnace.yin_grid_size.width"
                  :yin-height="solveResult.furnace.yin_grid_size.height"
                />
              </li>
            </ol>
            <button
              v-if="solveResult?.has_more"
              type="button"
              class="load-more-solutions"
              :disabled="solving"
              @click="loadMoreSolutions"
            >{{ solving ? '加载中…' : '加载更多 5 个' }}</button>
            <p class="solver-note">阴炉药材按负向量参与配平；当前版本先排除需要中间抵消的组合。</p>
          </div>
        </dd>
      </dl>

      <details class="source-evidence">
        <summary>逆向来源</summary>
        <dl>
          <template v-for="(value, key) in selected.source_evidence" :key="key">
            <dt>{{ key }}</dt>
            <dd>{{ value }}</dd>
          </template>
          <dt>content_hash</dt>
          <dd>{{ selected.content_hash }}</dd>
        </dl>
      </details>
    </section>
    <section v-else class="detail-empty">选择一种丹药查看详情</section>
  </main>
</template>

<style scoped>
.alchemy-page {
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
  height: 100%;
  min-height: 0;
  padding: 18px 22px 28px;
  overflow: hidden;
  color: #272b2f;
  background: #f6f7f5;
}

.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 14px;
}

.page-head h1,
.detail-head h2 {
  margin: 0;
}

.page-head h1 {
  font-size: 22px;
}

.page-head p,
.detail-head p {
  margin: 5px 0 0;
  color: #6c7379;
}

.toolbar {
  display: flex;
  gap: 10px;
  align-items: center;
  margin-bottom: 10px;
}

.search-input {
  width: 330px;
}

.grade-select {
  width: 202px;
}

.grade-filter-selection {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  min-width: 0;
}

.grade-rank {
  min-width: 2ch;
  color: #687076;
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.grade-filter-meter {
  width: 108px;
  height: 25px;
}

.grade-label {
  display: inline-flex;
  gap: 7px;
  align-items: center;
  color: var(--grade-color);
  white-space: nowrap;
}

.grade-label i {
  display: inline-block;
  flex: none;
  width: 8px;
  height: 8px;
  border: 1px solid color-mix(in srgb, var(--grade-color) 78%, #000);
  border-radius: 50%;
  background: var(--grade-color);
}

.grade-option {
  display: flex;
  gap: 10px;
  align-items: center;
  justify-content: space-between;
  width: 100%;
}

.grade-option em {
  min-width: 2ch;
  color: #8a9094;
  font-style: normal;
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.grade-text {
  color: var(--grade-color);
}

.output-grade-meter {
  width: 119px;
  height: 23px;
}

.element-list {
  display: inline-flex;
  gap: 10px;
  align-items: baseline;
  white-space: nowrap;
}

.element-value,
.element-text {
  color: var(--element-color);
}

.element-value {
  font-weight: 600;
}

.element-text {
  font-weight: 600;
}

.element-number {
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.list-pane {
  display: flex;
  flex-direction: column;
  min-height: 250px;
  overflow: hidden;
  border: 1px solid #d9ddda;
  background: #fff;
}

.table-scroll {
  flex: 1;
  min-height: 0;
  overflow: auto;
}

.recipe-table {
  width: 100%;
  border-collapse: collapse;
  table-layout: auto;
  white-space: nowrap;
}

.recipe-table th,
.recipe-table td {
  padding: 4px 11px;
  border-bottom: 1px solid #eceeec;
  text-align: left;
  vertical-align: middle;
}

.recipe-table th {
  position: sticky;
  top: 0;
  z-index: 1;
  background: #f0f2ef;
  color: #555d61;
  font-size: 13px;
  font-weight: 600;
}

.sort-button {
  display: inline-flex;
  gap: 4px;
  align-items: center;
  padding: 0;
  border: 0;
  color: inherit;
  font: inherit;
  background: transparent;
  cursor: pointer;
}

.sort-button.active,
.sort-button:hover {
  color: #356a91;
}

.sort-mark {
  width: 12px;
  color: #899095;
  text-align: center;
}

.sort-button.active .sort-mark {
  color: #356a91;
}

.recipe-table tbody tr {
  cursor: pointer;
  font-size: 14px;
}

.recipe-table tbody tr:hover {
  background: #f6f8f4;
}

.recipe-table tbody tr.selected {
  background: #e9f1e7;
}

.recipe-table .number-cell {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.recipe-table .number-column {
  width: 48px;
  text-align: right;
}

.recipe-table .icon-column,
.recipe-table .icon-cell {
  width: 42px;
  padding: 2px 6px;
  text-align: center;
}

.icon-cell img {
  display: block;
  width: 32px;
  height: 32px;
  object-fit: contain;
}

.ingredients-cell {
  max-width: 460px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.effect-cell {
  max-width: 240px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.ingredient-chips {
  display: inline-flex;
  gap: 8px;
  align-items: center;
}

.ingredient-chip,
.ingredient-text {
  display: inline-flex;
  gap: 4px;
  align-items: center;
}

.ingredient-chip em,
.ingredient-text em {
  color: #687076;
  font-style: normal;
  font-variant-numeric: tabular-nums;
}

.ingredient-grade-meter {
  width: 97px;
  height: 23px;
}

.recipe-table .fill-column {
  width: 100%;
  padding: 0;
}

.pagination {
  flex: none;
  padding: 9px 10px;
  border-top: 1px solid #eceeec;
}

.empty-state,
.detail-empty {
  padding: 28px;
  color: #8a9094;
  text-align: center;
}

.pane-resizer {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: center;
  height: 18px;
  cursor: row-resize;
}

.pane-resizer span {
  width: 48px;
  height: 3px;
  border-radius: 2px;
  background: #cfd4d0;
}

.pane-resizer:hover span,
.alchemy-page.resizing .pane-resizer span {
  background: #698663;
}

.detail-pane {
  flex: 1;
  min-height: 0;
  overflow: auto;
  border: 1px solid #d9ddda;
  background: #fff;
  padding: 16px 18px;
}

.detail-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding-bottom: 12px;
  border-bottom: 1px solid #eceeec;
}

.detail-head h2 {
  font-size: 19px;
}

.detail-title {
  display: flex;
  gap: 12px;
  align-items: center;
}

.detail-name-row {
  display: flex;
  gap: 10px;
  align-items: center;
}

.ungraded-label {
  color: #7b8286;
}

.detail-title > img {
  flex: none;
  width: 56px;
  height: 56px;
  object-fit: contain;
}

.detail-fields {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  margin: 0;
  padding-top: 15px;
}

.detail-fields > dt,
.detail-fields > dd {
  margin: 0;
  padding: 10px 0;
  border-bottom: 1px solid #eceeec;
}

.detail-fields > dt {
  padding-right: 28px;
  color: #4b524f;
  font-size: 14px;
  font-weight: 600;
}

.source-evidence dl {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  gap: 6px 14px;
  margin: 0;
}

.source-evidence dt {
  color: #737a7e;
}

.source-evidence dd {
  margin: 0;
}

.detail-element-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 22px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.detail-element-list li {
  display: inline-flex;
  gap: 10px;
}

.furnace-select {
  width: 360px;
  max-width: 100%;
}

.furnace-selection,
.furnace-option {
  display: inline-flex;
  gap: 7px;
  align-items: center;
  min-width: 0;
}

.furnace-selection img,
.furnace-option img {
  flex: none;
  width: 26px;
  height: 26px;
  object-fit: contain;
}

.furnace-selection strong,
.furnace-option strong {
  font-size: 13px;
  font-weight: 600;
}

.furnace-selection small,
.furnace-option small {
  color: #7b8280;
  font-size: 12px;
  white-space: nowrap;
}

.solver-module {
  display: grid;
  gap: 9px;
  min-width: 0;
}

.solver-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
}

.value-sort-control,
.value-sort-control ol,
.value-sort-control li {
  display: flex;
  align-items: center;
}

.value-sort-control {
  gap: 5px;
  color: #747b78;
  font-size: 12px;
}

.value-sort-control ol {
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.value-sort-control li {
  gap: 4px;
  padding: 2px 6px 2px 2px;
  border: 1px solid #d4dad6;
  color: #4f5953;
  background: #fafbfa;
  white-space: nowrap;
}

.solver-herb-pool {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.solver-herb-pool button {
  display: grid;
  box-sizing: border-box;
  width: 34px;
  height: 34px;
  padding: 3px;
  border: 1px solid #b9c8be;
  color: #59615d;
  background: #edf4ef;
  cursor: pointer;
  font: inherit;
  place-items: center;
}

.solver-herb-pool button:hover {
  border-color: #7e9b88;
  background: #e4eee7;
}

.solver-herb-pool img {
  width: 27px;
  height: 27px;
  object-fit: contain;
}

.solver-herb-pool button.is-disabled {
  border-color: #cfd2d0;
  color: #9a9e9b;
  background: #e4e6e5;
}

.solver-herb-pool button.is-disabled img {
  filter: grayscale(1);
  opacity: 0.35;
}

.solver-scope,
.solver-note,
.solver-empty {
  color: #747b78;
  font-size: 12px;
}

.solver-note,
.solver-empty,
.solver-error {
  margin: 0;
}

.solver-error {
  color: #b5443c;
}

.solution-list {
  display: grid;
  gap: 0;
  margin: 0;
  padding: 0;
  list-style: none;
}

.load-more-solutions {
  justify-self: start;
  padding: 5px 10px;
  border: 1px solid #c7cfca;
  color: #4f6657;
  background: #f7f9f7;
  cursor: pointer;
  font: inherit;
  font-size: 12px;
}

.load-more-solutions:hover:not(:disabled) {
  border-color: #9eaea4;
  background: #f1f5f2;
}

.load-more-solutions:disabled {
  cursor: default;
  opacity: 0.62;
}

.solution-item {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  gap: 20px;
  align-items: start;
  padding: 12px 0;
  border-bottom: 1px solid #e7eae7;
}

.solution-summary {
  display: grid;
  grid-template-columns: 26px max-content;
  gap: 8px;
  align-items: start;
  font-size: 13px;
}

.solution-rank {
  display: grid;
  width: 22px;
  height: 22px;
  border: 1px solid #cbd1cd;
  color: #59615d;
  font-size: 12px;
  place-items: center;
}

.solution-summary dl {
  display: grid;
  gap: 4px;
  margin: 0;
}

.solution-summary dl > div {
  display: grid;
  grid-template-columns: 54px max-content;
  gap: 6px;
  align-items: baseline;
  font-variant-numeric: tabular-nums;
}

.solution-summary dt,
.solution-summary dd {
  margin: 0;
}

.solution-summary dt {
  color: #747b78;
}

.solution-summary dt::after {
  content: '：';
}

.solution-summary .is-primary-metric dd {
  color: #267044;
  font-weight: 700;
}

.solution-summary em {
  margin-left: 2px;
  color: #63806c;
  font-style: normal;
}

.rule-pending {
  grid-column: 2;
  color: #9a6a2f;
  font-size: 11px;
  font-style: normal;
}

@media (max-width: 1080px) {
  .solution-item {
    grid-template-columns: 1fr;
    gap: 9px;
  }
}

.plain-list,
.rule-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.plain-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 24px;
}

.plain-list li {
  display: flex;
}

.plain-list em {
  color: #7b8286;
  font-style: normal;
  white-space: nowrap;
}

.ingredient-name {
  display: inline-flex;
  gap: 8px;
  align-items: center;
}

.ingredient-text {
  display: inline-flex;
  gap: 5px;
  align-items: baseline;
  white-space: nowrap;
}

.ingredient-name img {
  flex: none;
  width: 32px;
  height: 32px;
  object-fit: contain;
}

.rule-list li {
  padding: 2px 0;
}

.source-evidence {
  margin-top: 16px;
  border-top: 1px solid #eceeec;
  padding-top: 11px;
  color: #68706c;
}

.source-evidence summary {
  cursor: pointer;
}

.source-evidence dl {
  margin-top: 10px;
  font-size: 12px;
}

.source-evidence dd {
  overflow-wrap: anywhere;
}

@media (max-width: 700px) {
  .detail-fields {
    grid-template-columns: 1fr;
  }

  .detail-fields > dt {
    padding-bottom: 4px;
    border-bottom: 0;
  }

  .detail-fields > dd {
    padding-top: 0;
  }
}
</style>
