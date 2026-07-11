<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Search } from '@element-plus/icons-vue'
import { useRoute, useRouter } from 'vue-router'

import {
  fetchZaohuaHerb,
  fetchZaohuaHerbMeta,
  fetchZaohuaHerbs,
  type ZaohuaHerb,
  type ZaohuaHerbCraftingAttribute,
  type ZaohuaHerbMeta,
} from '@/api/zaohua'
import StandardPagination from '@/components/StandardPagination.vue'
import { mixWeightedColors, toHex } from '@/utils/colorMath'
import { formatChineseCompactNumber } from '@/utils/numberFormat'
import { useResizablePane } from '@/utils/useResizablePane'
import GradeMeter from '../components/GradeMeter.vue'
import HerbShapePreview from '../components/HerbShapePreview.vue'
import '../catalog-inspector.css'

const route = useRoute()
const router = useRouter()
const loading = ref(false)
const meta = ref<ZaohuaHerbMeta | null>(null)
const herbs = ref<ZaohuaHerb[]>([])
const matrixHerbs = ref<ZaohuaHerb[]>([])
const matrixLoading = ref(false)
const selected = ref<ZaohuaHerb | null>(null)
const viewMode = ref<'matrix' | 'list'>('matrix')
const query = ref('')
const grade = ref('')
const element = ref('')
const page = ref(1)
const pageSize = ref(40)
const total = ref(0)
const sortBy = ref<'number' | 'grade'>('number')
const sortOrder = ref<'asc' | 'desc'>('asc')
let searchTimer = 0
let requestSequence = 0

type MatrixColumn = { key: string; label: string }
type MatrixGroup = 'cycle' | 'polarity' | 'variant' | 'shop'
const MATRIX_GROUP_STORAGE_KEY = 'zaohua:herbs:matrix-group'
const MATRIX_GROUPS: Array<{ value: MatrixGroup; code: string; label: string }> = [
  { value: 'cycle', code: 'A', label: '五行互生' },
  { value: 'polarity', code: 'B', label: '五行阴阳' },
  { value: 'variant', code: 'C', label: '异灵根' },
  { value: 'shop', code: '', label: '灵材铺' },
]
const loadMatrixGroup = (): MatrixGroup => {
  try {
    const saved = window.localStorage.getItem(MATRIX_GROUP_STORAGE_KEY)
    return saved === 'polarity' || saved === 'variant' || saved === 'shop' ? saved : 'cycle'
  } catch {
    return 'cycle'
  }
}
const matrixGroup = ref<MatrixGroup>(loadMatrixGroup())
const MATRIX_COLUMNS: MatrixColumn[] = [
  { key: 'mix_gold_water', label: '金水' },
  { key: 'mix_water_wood', label: '水木' },
  { key: 'mix_wood_fire', label: '木火' },
  { key: 'mix_fire_soil', label: '火土' },
  { key: 'mix_soil_gold', label: '土金' },
  { key: 'yang_gold', label: '阳金' }, { key: 'yin_gold', label: '阴金' },
  { key: 'yang_water', label: '阳水' }, { key: 'yin_water', label: '阴水' },
  { key: 'yang_wood', label: '阳木' }, { key: 'yin_wood', label: '阴木' },
  { key: 'yang_fire', label: '阳火' }, { key: 'yin_fire', label: '阴火' },
  { key: 'yang_soil', label: '阳土' }, { key: 'yin_soil', label: '阴土' },
  { key: 'yang_ice', label: '阳冰' }, { key: 'yin_ice_yang_water', label: '阴冰' },
  { key: 'yang_wind', label: '阳风' }, { key: 'yin_wind_yang_wood', label: '阴风' },
  { key: 'yang_thunder', label: '阳雷' }, { key: 'yin_thunder_yang_fire', label: '阴雷' },
]
const MATRIX_GROUP_RANGES: Record<MatrixGroup, [number, number]> = {
  cycle: [0, 5],
  polarity: [5, 15],
  variant: [15, 21],
  shop: [0, 0],
}
const visibleMatrixColumns = computed(() => {
  const [start, end] = MATRIX_GROUP_RANGES[matrixGroup.value]
  return MATRIX_COLUMNS.slice(start, end)
})
const matrixGroupLabel = computed(() => MATRIX_GROUPS.find(item => item.value === matrixGroup.value)?.label || '')
const matrixGroupCode = computed(() => MATRIX_GROUPS.find(item => item.value === matrixGroup.value)?.code || '')
const isShopMatrix = computed(() => matrixGroup.value === 'shop')
const PLANTING_DAYS_BY_RANK = [10, 20, 30, 360, 720, 1080, 3600, 7200, 10800, 36000, 72000, 108000]
const plantingTimeLabel = (rank: number) => {
  const days = PLANTING_DAYS_BY_RANK[rank - 1]
  if (!days) return '未配置'
  return days >= 360 ? `${formatNumber(days / 360)}年` : `${formatNumber(days)}日`
}

type ShopPool = {
  startRank: number
  endRank: number
  drawCount: number
  label: string
  perItem: 'one' | 'random'
  note?: string
}
type ShopStage = { key: string; label: string; pools: ShopPool[] }
const SHOP_STAGES: ShopStage[] = [
  {
    key: 'chapter-1',
    label: '第一章',
    pools: [
      { startRank: 1, endRank: 1, drawCount: 20, label: '一阶下品池', perItem: 'random' },
      { startRank: 2, endRank: 2, drawCount: 10, label: '一阶中品池', perItem: 'one' },
    ],
  },
  {
    key: 'chapter-2-6',
    label: '第二至六章',
    pools: [
      { startRank: 1, endRank: 1, drawCount: 20, label: '一阶下品池', perItem: 'random' },
      { startRank: 2, endRank: 2, drawCount: 10, label: '一阶中品池', perItem: 'one', note: '第三章仙缘城抽20株，单种数量随机' },
      { startRank: 3, endRank: 3, drawCount: 5, label: '一阶上品池', perItem: 'one' },
    ],
  },
  {
    key: 'chapter-7',
    label: '第七章',
    pools: [
      { startRank: 1, endRank: 3, drawCount: 20, label: '一阶合池', perItem: 'one' },
      { startRank: 4, endRank: 4, drawCount: 10, label: '二阶下品池', perItem: 'one' },
    ],
  },
  {
    key: 'map-1005',
    label: '赤霄秘境',
    pools: [
      { startRank: 1, endRank: 6, drawCount: 20, label: '一至二阶合池', perItem: 'one' },
      { startRank: 7, endRank: 9, drawCount: 10, label: '三阶合池', perItem: 'one' },
    ],
  },
]
const shopPoolForRank = (stage: ShopStage, rank: number) => (
  stage.pools.find(pool => rank >= pool.startRank && rank <= pool.endRank)
)
type MatrixPriceColumn = { key: string; label: string; slotKeys: string[] }
const visiblePriceColumns = computed<MatrixPriceColumn[]>(() => {
  if (matrixGroup.value === 'shop') return []
  if (matrixGroup.value === 'cycle') {
    return [{ key: 'cycle', label: '互生价', slotKeys: MATRIX_COLUMNS.slice(0, 5).map(item => item.key) }]
  }
  if (matrixGroup.value === 'polarity') {
    return [
      { key: 'yang', label: '阳价', slotKeys: MATRIX_COLUMNS.slice(5, 15).filter(item => item.key.startsWith('yang_')).map(item => item.key) },
      { key: 'yin', label: '阴价', slotKeys: MATRIX_COLUMNS.slice(5, 15).filter(item => item.key.startsWith('yin_')).map(item => item.key) },
    ]
  }
  return [
    { key: 'yang', label: '阳价', slotKeys: MATRIX_COLUMNS.slice(15).filter(item => item.key.startsWith('yang_')).map(item => item.key) },
    { key: 'yin', label: '阴价', slotKeys: MATRIX_COLUMNS.slice(15).filter(item => item.key.startsWith('yin_')).map(item => item.key) },
  ]
})

const matrixPriceEntries = (row: { cells: Map<string, ZaohuaHerb[]> }, column: MatrixPriceColumn) => {
  const herbs = column.slotKeys.flatMap(key => row.cells.get(key) || [])
  const byPrice = new Map<number, string[]>()
  for (const herb of herbs) byPrice.set(herb.price, [...(byPrice.get(herb.price) || []), herb.name])
  return [...byPrice.entries()]
    .sort(([left], [right]) => left - right)
    .map(([price, names]) => ({ price, names }))
}

const matrixSlotKey = (herb: ZaohuaHerb) => {
  const values = new Map(herb.crafting_attributes.map(item => [item.element, item.value]))
  const positive = [...values].filter(([, value]) => value > 0).map(([key]) => key)
  const negative = [...values].filter(([, value]) => value < 0).map(([key]) => key)
  const pair = (left: string, right: string) => positive.includes(left) && positive.includes(right) && values.size === 2
  if (pair('gold', 'water')) return 'mix_gold_water'
  if (pair('water', 'wood')) return 'mix_water_wood'
  if (pair('wood', 'fire')) return 'mix_wood_fire'
  if (pair('fire', 'soil')) return 'mix_fire_soil'
  if (pair('soil', 'gold')) return 'mix_soil_gold'
  if (negative.includes('ice') && positive.includes('water')) return 'yin_ice_yang_water'
  if (negative.includes('wind') && positive.includes('wood')) return 'yin_wind_yang_wood'
  if (negative.includes('thunder') && positive.includes('fire')) return 'yin_thunder_yang_fire'
  if (values.size === 1) {
    const [elementKey, value] = [...values][0] || []
    if (elementKey && value) return `${value < 0 ? 'yin' : 'yang'}_${elementKey}`
  }
  return 'other'
}

const matrixRows = computed(() => {
  const byGrade = new Map<number, ZaohuaHerb[]>()
  for (const herb of matrixHerbs.value) byGrade.set(herb.grade_rank, [...(byGrade.get(herb.grade_rank) || []), herb])
  const rows = [...byGrade.entries()].sort(([left], [right]) => left - right).map(([rank, gradeHerbs]) => {
    const cells = new Map<string, ZaohuaHerb[]>()
    for (const herb of gradeHerbs) {
      const key = matrixSlotKey(herb)
      cells.set(key, [...(cells.get(key) || []), herb])
    }
    return {
      rank,
      gradeName: gradeHerbs[0]?.grade_name || '',
      prices: [...new Set(gradeHerbs.map(item => item.price))].sort((a, b) => a - b),
      cells,
    }
  })
  for (const [rank, gradeName] of [[13, '五阶下品'], [14, '五阶中品'], [15, '五阶上品']] as const) {
    if (!byGrade.has(rank)) rows.push({ rank, gradeName, prices: [], cells: new Map<string, ZaohuaHerb[]>() })
  }
  return rows
})

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
  storageKey: 'zaohua:herbs:list-pane-height',
})
const listPaneStyle = computed(() => ({ height: `${listPaneHeight.value}px` }))

type GradeVisual = { grade_color_hex?: string; color_hex?: string }
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
  ice: '#2f8798',
  wind: '#527c72',
  thunder: '#7449a8',
  none: '#697176',
}
const elementColor = (key: string) => ELEMENT_COLORS[key.toLowerCase()] || '#697176'
const elementStyle = (key: string) => ({
  '--element-color': elementColor(key),
})
const craftingAttributeColor = (attributes: ZaohuaHerbCraftingAttribute[], fallbackKey = 'none') => {
  const entries = (attributes || [])
    .filter(item => Number.isFinite(item.value) && item.value !== 0)
    .map(item => ({
      color: elementColor(item.element),
      weight: Math.abs(item.value),
    }))
  const totalWeight = entries.reduce((sum, item) => sum + item.weight, 0)
  return totalWeight > 0
    ? toHex(mixWeightedColors(entries, { fillToWeight: totalWeight }))
    : elementColor(fallbackKey)
}
const attributeMagnitude = (value: number) => Math.abs(value)
const herbGridEfficiency = (herb: ZaohuaHerb) => {
  const cellCount = herb.shape?.cells.length || 0
  if (!cellCount) return null
  const attributeTotal = herb.crafting_attributes.reduce((total, attribute) => total + Math.abs(attribute.value), 0)
  if (!attributeTotal) return null
  return { value: attributeTotal / cellCount, attributeTotal, cellCount }
}
const formatGridEfficiency = (herb: ZaohuaHerb) => {
  const efficiency = herbGridEfficiency(herb)
  if (!efficiency) return '—'
  return Math.trunc(efficiency.value).toString()
}
const gridEfficiencyTitle = (herb: ZaohuaHerb) => {
  const efficiency = herbGridEfficiency(herb)
  if (!efficiency) return '格效：缺少炼丹属性或占格数据'
  return `格效 ${formatGridEfficiency(herb)} = 属性绝对值 ${efficiency.attributeTotal} ÷ ${efficiency.cellCount} 格；越高表示单位丹炉空间提供的属性越多`
}
const herbCultivationEfficiency = (herb: ZaohuaHerb) => {
  const plantingDays = PLANTING_DAYS_BY_RANK[herb.grade_rank - 1]
  if (!plantingDays) return null
  const attributeTotal = herb.crafting_attributes.reduce((total, attribute) => total + Math.abs(attribute.value), 0)
  if (!attributeTotal) return null
  return { value: attributeTotal / plantingDays * 30, attributeTotal, plantingDays }
}
const formatCultivationEfficiency = (herb: ZaohuaHerb) => {
  const efficiency = herbCultivationEfficiency(herb)
  if (!efficiency) return '—'
  return Number(efficiency.value.toFixed(2)).toString()
}
const cultivationEfficiencyTitle = (herb: ZaohuaHerb) => {
  const efficiency = herbCultivationEfficiency(herb)
  if (!efficiency) return '种效：缺少炼丹属性或种植时间'
  return `种效 ${formatCultivationEfficiency(herb)} = 属性绝对值 ${efficiency.attributeTotal} ÷ ${efficiency.plantingDays} 日 × 30；表示每30日可产出的炼丹属性值`
}
const matrixAttributes = (herb: ZaohuaHerb, columnKey: string) => {
  if (!columnKey.startsWith('yin_')) return herb.crafting_attributes
  const variantElements = new Set(['ice', 'wind', 'thunder'])
  return [...herb.crafting_attributes].sort((left, right) => (
    Number(variantElements.has(right.element)) - Number(variantElements.has(left.element))
  ))
}
const elementOptionByKey = (key: string) => meta.value?.elements.find(item => item.key === key)
const elementLabel = (key: string, name: string) => key === 'none' ? '无属性' : `${name}系`

const formatNumber = (value?: number) => Number.isFinite(value)
  ? new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 }).format(Number(value))
  : '—'
const formatPrice = (value?: number) => Number.isFinite(value)
  ? formatChineseCompactNumber(value)
  : '—'

const toggleSort = (field: 'number' | 'grade') => {
  if (sortBy.value === field) {
    sortOrder.value = sortOrder.value === 'asc' ? 'desc' : 'asc'
  } else {
    sortBy.value = field
    sortOrder.value = 'asc'
  }
  page.value = 1
  void loadHerbs()
}

const sortMark = (field: 'number' | 'grade') => sortBy.value === field
  ? (sortOrder.value === 'asc' ? '↑' : '↓')
  : '↕'

const hideBrokenImage = (event: Event) => {
  const image = event.currentTarget as HTMLImageElement | null
  if (image) image.style.visibility = 'hidden'
}

const selectHerb = async (herb: ZaohuaHerb, updateRoute = true) => {
  selected.value = herb
  if (updateRoute) {
    await router.replace({ query: { ...route.query, item_id: String(herb.item_id) } })
  }
}

const loadMeta = async () => {
  meta.value = await fetchZaohuaHerbMeta()
}

const loadHerbs = async () => {
  const sequence = ++requestSequence
  loading.value = true
  try {
    const response = await fetchZaohuaHerbs({
      q: query.value.trim(),
      grade: grade.value,
      element: element.value,
      sort_by: sortBy.value,
      sort_order: sortOrder.value,
      page: page.value,
      page_size: pageSize.value,
    })
    if (sequence !== requestSequence) return
    herbs.value = response.items
    total.value = response.total

    const routeId = Number(route.query.item_id || 0)
    const preferredId = routeId || selected.value?.item_id || 0
    const visibleSelected = herbs.value.find(item => item.item_id === preferredId)
    if (visibleSelected) {
      selected.value = visibleSelected
      return
    }
    if (routeId > 0) {
      try {
        selected.value = await fetchZaohuaHerb(routeId)
        return
      } catch {
        // An old route may reference an item absent from the current build.
      }
    }
    selected.value = herbs.value[0] || null
  } finally {
    if (sequence === requestSequence) loading.value = false
  }
}

const loadMatrixHerbs = async () => {
  matrixLoading.value = true
  try {
    const response = await fetchZaohuaHerbs({
      sort_by: 'grade',
      sort_order: 'asc',
      page: 1,
      page_size: 200,
    })
    matrixHerbs.value = response.items
  } finally {
    matrixLoading.value = false
  }
}

watch(query, () => {
  window.clearTimeout(searchTimer)
  searchTimer = window.setTimeout(() => {
    page.value = 1
    void loadHerbs()
  }, 250)
})

watch([grade, element, pageSize], () => {
  page.value = 1
  void loadHerbs()
})

watch(page, () => void loadHerbs())

watch(matrixGroup, (value) => {
  try {
    window.localStorage.setItem(MATRIX_GROUP_STORAGE_KEY, value)
  } catch {
    // The matrix remains usable when browser storage is unavailable.
  }
})

onMounted(async () => {
  await Promise.all([loadMeta(), loadHerbs(), loadMatrixHerbs()])
})

onBeforeUnmount(() => window.clearTimeout(searchTimer))
</script>

<template>
  <main class="herb-page zaohua-catalog-page" :class="{ resizing: isResizing }">
    <header class="page-head">
      <div>
        <h1>造化仙缘 · 药材</h1>
        <p>灵草的品阶、五行、灵气与炼丹用途。</p>
      </div>
    </header>

    <section class="toolbar">
      <el-select v-model="viewMode" class="view-mode-select" aria-label="药材视图">
        <el-option label="归纳表" value="matrix" />
        <el-option label="明细" value="list" />
      </el-select>
      <el-select
        v-if="viewMode === 'matrix'"
        v-model="matrixGroup"
        class="matrix-group-select"
        aria-label="规律类别"
      >
        <el-option
          v-for="item in MATRIX_GROUPS"
          :key="item.value"
          :label="item.code ? `${item.code} · ${item.label}` : item.label"
          :value="item.value"
        />
      </el-select>
      <span
        v-if="viewMode === 'matrix' && !isShopMatrix"
        class="efficiency-legend"
        title="种效 = 炼丹属性绝对值总和 ÷ 种植日数 × 30；格效 = 炼丹属性绝对值总和 ÷ 占格数。单元格按“种效 / 格效”显示。"
      ><b>单元格第3行：种效 / 格效</b><small>每30日属性 / 每格属性</small></span>
      <el-input
        v-if="viewMode === 'list'"
        v-model="query"
        class="search-input"
        clearable
        :prefix-icon="Search"
        placeholder="搜索药材、描述或丹药"
      />
      <el-select v-if="viewMode === 'list'" v-model="grade" class="grade-select" aria-label="品阶筛选" placeholder="全部品阶" clearable>
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
        <el-option v-for="item in meta?.grades || []" :key="item.name" :label="item.name" :value="item.name">
          <span class="filter-option grade-filter-option">
            <span class="grade-filter-selection">
              <span class="grade-rank">{{ item.order }}</span>
              <GradeMeter class="grade-filter-meter" :rank="item.order" :label="item.name" />
            </span>
            <em>{{ item.count }}</em>
          </span>
        </el-option>
      </el-select>
      <el-select v-if="viewMode === 'list'" v-model="element" class="element-select" placeholder="全部所属五行" clearable>
        <template #label="{ value }">
          <span v-if="elementOptionByKey(String(value || ''))" class="element-text" :style="elementStyle(String(value || ''))">
            {{ elementLabel(String(value || ''), elementOptionByKey(String(value || ''))?.name || '') }}
          </span>
        </template>
        <el-option v-for="item in meta?.elements || []" :key="item.key" :label="elementLabel(item.key, item.name)" :value="item.key">
          <span class="filter-option">
            <span class="element-text" :style="elementStyle(item.key)">{{ elementLabel(item.key, item.name) }}</span>
            <em>{{ item.count }}</em>
          </span>
        </el-option>
      </el-select>
    </section>

    <section class="list-pane" :style="listPaneStyle" v-loading="viewMode !== 'list' ? matrixLoading : loading">
      <div v-if="viewMode !== 'list'" class="matrix-scroll">
        <table class="herb-matrix">
          <thead>
            <tr>
              <th rowspan="2" class="sticky-grade-rank">品级</th>
              <th rowspan="2" class="sticky-grade-name">品阶</th>
              <th rowspan="2" class="planting-time-column"><span>种植<br>时间</span></th>
              <th v-for="column in isShopMatrix ? [] : visiblePriceColumns" :key="column.key" rowspan="2" class="matrix-price">
                {{ column.label }}
              </th>
              <th :colspan="isShopMatrix ? SHOP_STAGES.length : visibleMatrixColumns.length">
                <template v-if="isShopMatrix">灵材铺 · 每30日刷新</template>
                <template v-else>{{ matrixGroupCode }} · {{ matrixGroupLabel }}</template>
              </th>
            </tr>
            <tr>
              <th v-for="stage in isShopMatrix ? SHOP_STAGES : []" :key="stage.key">
                {{ stage.label }}
              </th>
              <th v-for="(column, index) in isShopMatrix ? [] : visibleMatrixColumns" :key="column.key">
                {{ matrixGroupCode }}{{ index + 1 }} · {{ column.label }}
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in matrixRows" :key="row.rank">
              <td class="sticky-grade-rank number-cell">{{ row.rank }}</td>
              <td class="sticky-grade-name">
                <GradeMeter
                  class="matrix-grade-meter"
                  :rank="row.rank"
                  :label="row.gradeName"
                />
              </td>
              <td class="planting-time-cell number-cell">
                <strong :class="{ 'not-configured': row.rank >= 13 }">{{ plantingTimeLabel(row.rank) }}</strong>
              </td>
              <td v-for="column in isShopMatrix ? [] : visiblePriceColumns" :key="column.key" class="matrix-price number-cell">
                <span
                  v-for="entry in matrixPriceEntries(row, column)"
                  :key="entry.price"
                  class="matrix-price-entry"
                  :title="entry.names.join('、')"
                >
                  {{ formatPrice(entry.price) }}
                  <small v-if="matrixPriceEntries(row, column).length > 1">
                    {{ entry.names.length === 1 ? entry.names[0] : `${entry.names.length}种` }}
                  </small>
                </span>
                <span v-if="!matrixPriceEntries(row, column).length">—</span>
              </td>
              <template v-if="row.rank >= 13">
                <td :colspan="isShopMatrix ? SHOP_STAGES.length : visibleMatrixColumns.length" class="unimplemented-cell">
                  药材未实装
                </td>
              </template>
              <template v-else-if="isShopMatrix">
                <template v-for="stage in SHOP_STAGES" :key="stage.key">
                  <td
                    v-if="shopPoolForRank(stage, row.rank)?.startRank === row.rank"
                    :rowspan="(shopPoolForRank(stage, row.rank)?.endRank || row.rank) - row.rank + 1"
                    class="shop-pool-cell"
                  >
                    <strong>{{ shopPoolForRank(stage, row.rank)?.perItem === 'one' ? '每种1株' : '单种随机' }}</strong>
                    <span>
                      {{ shopPoolForRank(stage, row.rank)?.label }} · 共抽{{ shopPoolForRank(stage, row.rank)?.drawCount }}株
                    </span>
                    <small v-if="shopPoolForRank(stage, row.rank)?.note">
                      {{ shopPoolForRank(stage, row.rank)?.note }}
                    </small>
                  </td>
                  <td v-else-if="!shopPoolForRank(stage, row.rank)" class="shop-pool-cell empty">—</td>
                </template>
              </template>
              <td v-for="column in isShopMatrix || row.rank >= 13 ? [] : visibleMatrixColumns" :key="column.key" class="matrix-cell">
                <button
                  v-for="herb in row.cells.get(column.key) || []"
                  :key="herb.item_id"
                  type="button"
                  class="matrix-herb"
                  :class="{ selected: selected?.item_id === herb.item_id }"
                  @click="selectHerb(herb)"
                >
                  <span class="matrix-herb-heading">
                    <b>{{ herb.name }}</b>
                  </span>
                  <small>
                    <i
                      v-for="attribute in matrixAttributes(herb, column.key)"
                      :key="attribute.element"
                      :class="{ negative: attribute.value < 0 }"
                      :style="elementStyle(attribute.element)"
                    >{{ attribute.label }}{{ attributeMagnitude(attribute.value) }}</i>
                  </small>
                  <span class="matrix-efficiency-line">
                    <em :title="cultivationEfficiencyTitle(herb)">{{ formatCultivationEfficiency(herb) }}</em>
                    <i>/</i>
                    <em :title="gridEfficiencyTitle(herb)">{{ formatGridEfficiency(herb) }}</em>
                  </span>
                </button>
              </td>
            </tr>
          </tbody>
        </table>
        <div v-if="!matrixLoading && !matrixRows.length" class="empty-state">没有匹配的药材</div>
      </div>
      <div v-else class="table-scroll">
        <table class="herb-table zaohua-catalog-table">
          <thead>
            <tr>
              <th class="number-column" :aria-sort="sortBy === 'number' ? (sortOrder === 'asc' ? 'ascending' : 'descending') : 'none'">
                <button type="button" class="sort-button" :class="{ active: sortBy === 'number' }" @click="toggleSort('number')">
                  <span>编号</span><span class="sort-mark">{{ sortMark('number') }}</span>
                </button>
              </th>
              <th class="icon-column">图标</th>
              <th>药材</th>
              <th :aria-sort="sortBy === 'grade' ? (sortOrder === 'asc' ? 'ascending' : 'descending') : 'none'">
                <button type="button" class="sort-button" :class="{ active: sortBy === 'grade' }" @click="toggleSort('grade')">
                  <span>品级</span><span class="sort-mark">{{ sortMark('grade') }}</span>
                </button>
              </th>
              <th>炼丹属性</th>
              <th>灵气</th>
              <th>价格</th>
              <th>用于丹药</th>
              <th class="fill-column" aria-hidden="true"></th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="herb in herbs"
              :key="herb.item_id"
              :class="{ selected: selected?.item_id === herb.item_id }"
              @click="selectHerb(herb)"
            >
              <td class="number-cell">{{ herb.display_order }}</td>
              <td class="icon-cell">
                <img v-if="herb.icon_url" :src="herb.icon_url" :alt="herb.name" loading="lazy" @error="hideBrokenImage" />
              </td>
              <td>
                <GradeMeter
                  class="herb-grade-meter"
                  :rank="herb.grade_rank"
                  :label="herb.name"
                  :text-color="craftingAttributeColor(herb.crafting_attributes, herb.element_key)"
                  :title="herb.grade_name"
                />
              </td>
              <td class="number-cell">{{ herb.grade_rank || '—' }}</td>
              <td>
                <span v-if="herb.crafting_attributes.length" class="crafting-attributes">
                  <span
                    v-for="item in herb.crafting_attributes"
                    :key="item.element"
                    class="crafting-attribute"
                    :class="{ negative: item.value < 0 }"
                    :style="elementStyle(item.element)"
                    :title="item.value < 0 ? '负值' : ''"
                  >{{ item.label }}{{ attributeMagnitude(item.value) }}</span>
                </span>
                <span v-else>—</span>
              </td>
              <td class="number-cell">{{ formatNumber(herb.lingqi) }}</td>
              <td class="number-cell">{{ formatPrice(herb.price) }}</td>
              <td class="number-cell">{{ herb.recipe_count || '' }}</td>
              <td class="fill-column" aria-hidden="true"></td>
            </tr>
          </tbody>
        </table>
        <div v-if="!loading && !herbs.length" class="empty-state">没有匹配的药材</div>
      </div>
      <StandardPagination
        v-if="viewMode === 'list'"
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

    <div class="pane-resizer" @mousedown="startResizing"><span></span></div>

    <section v-if="selected" class="detail-pane">
      <header class="detail-head">
        <div class="detail-title">
          <img v-if="selected.icon_url" :src="selected.icon_url" :alt="selected.name" @error="hideBrokenImage" />
          <div>
            <h2 class="element-text" :style="{ '--element-color': craftingAttributeColor(selected.crafting_attributes, selected.element_key) }">{{ selected.name }}</h2>
            <p>
              <span class="element-text" :style="elementStyle(selected.element_key)">{{ elementLabel(selected.element_key, selected.element_name) }}</span>
              · <span class="grade-text" :style="gradeStyle(selected)">{{ selected.grade_name || '未标品阶' }}</span>
            </p>
          </div>
        </div>
      </header>

      <dl class="detail-fields">
        <dt>炼丹属性</dt>
        <dd>
          <span v-if="selected.crafting_attributes.length" class="crafting-attributes">
            <span
              v-for="item in selected.crafting_attributes"
              :key="item.element"
              class="crafting-attribute"
              :class="{ negative: item.value < 0 }"
              :style="elementStyle(item.element)"
              :title="item.value < 0 ? '负值' : ''"
            >{{ item.label }}{{ attributeMagnitude(item.value) }}</span>
          </span>
          <span v-else>—</span>
        </dd>
        <dt>种效</dt>
        <dd class="efficiency-detail">
          <strong>{{ formatCultivationEfficiency(selected) }}</strong>
          <span v-if="herbCultivationEfficiency(selected)">
            每30日属性：{{ herbCultivationEfficiency(selected)?.attributeTotal }} ÷ {{ herbCultivationEfficiency(selected)?.plantingDays }}日 × 30
          </span>
          <span v-else>缺少炼丹属性或种植时间</span>
        </dd>
        <dt>格效</dt>
        <dd class="efficiency-detail">
          <strong>{{ formatGridEfficiency(selected) }}</strong>
          <span v-if="herbGridEfficiency(selected)">
            每格属性：{{ herbGridEfficiency(selected)?.attributeTotal }} ÷ {{ herbGridEfficiency(selected)?.cellCount }}格
          </span>
          <span v-else>缺少炼丹属性或占格数据</span>
        </dd>
        <dt>灵气</dt><dd>{{ formatNumber(selected.lingqi) }}</dd>
        <dt>价格</dt><dd>{{ formatPrice(selected.price) }}</dd>
        <dt>描述</dt><dd>{{ selected.description || '—' }}</dd>
        <template v-if="selected.effect_description">
          <dt>附加说明</dt><dd>{{ selected.effect_description }}</dd>
        </template>
        <dt>用于丹药</dt>
        <dd>
          <div v-if="selected.recipes.length" class="recipe-links">
            <router-link
              v-for="recipe in selected.recipes"
              :key="recipe.recipe_id"
              :to="{ path: '/zaohua/alchemy', query: { recipe_id: recipe.recipe_id } }"
            >{{ recipe.output_name }} ×{{ recipe.required_count }}</router-link>
          </div>
          <span v-else>当前丹方示例中未使用</span>
        </dd>
        <dt class="shape-heading">
          <span>占格形状</span>
          <span v-if="selected.shape?.name" class="shape-name">{{ selected.shape.name }}</span>
        </dt>
        <dd class="shape-field">
          <HerbShapePreview
            :shape="selected.shape"
            :image-url="selected.shape?.image_url"
            :label="selected.name"
            :color="elementColor(selected.element_key)"
          />
        </dd>
      </dl>

      <details class="source-evidence">
        <summary>逆向来源</summary>
        <dl>
          <template v-for="(value, key) in selected.source_evidence" :key="key">
            <dt>{{ key }}</dt><dd>{{ value }}</dd>
          </template>
          <dt>content_hash</dt><dd>{{ selected.content_hash }}</dd>
        </dl>
      </details>
    </section>
    <section v-else class="detail-empty">选择一种药材查看详情</section>
  </main>
</template>

<style scoped>
.herb-page { display: flex; flex-direction: column; box-sizing: border-box; height: 100%; min-height: 0; padding: 18px 22px 28px; overflow: hidden; color: #272b2f; background: #f6f7f5; }
.page-head { margin-bottom: 14px; }
.page-head h1, .detail-head h2 { margin: 0; }
.page-head h1 { font-size: 22px; }
.page-head p, .detail-head p { margin: 5px 0 0; color: #6c7379; }
.toolbar { display: flex; gap: 10px; align-items: center; margin-bottom: 10px; }
.search-input { width: 330px; }
.grade-select { width: 202px; }
.element-select { width: 142px; }
.view-mode-select { width: 104px; }
.matrix-group-select { width: 148px; }
.efficiency-legend { display: inline-flex; gap: 7px; align-items: baseline; color: #747c80; font-size: 12px; white-space: nowrap; cursor: help; }
.efficiency-legend b { color: #4e575b; font-weight: 600; }
.efficiency-legend small { color: #929895; font-size: 11px; }
.grade-text { color: var(--grade-color); }
.grade-label { display: inline-flex; gap: 7px; align-items: center; color: var(--grade-color); white-space: nowrap; }
.grade-rank { min-width: 2ch; color: #687076; font-variant-numeric: tabular-nums; text-align: right; }
.grade-label i { display: inline-block; flex: none; width: 8px; height: 8px; border: 1px solid color-mix(in srgb, var(--grade-color) 78%, #000); border-radius: 50%; background: var(--grade-color); }
.grade-filter-selection { display: inline-flex; gap: 6px; align-items: center; min-width: 0; }
.grade-filter-meter { width: 108px; height: 25px; }
.grade-filter-option { gap: 10px; }
.element-text { color: var(--element-color); font-weight: 600; white-space: nowrap; }
.herb-grade-meter { width: 128px; height: 23px; }
.crafting-attributes { display: inline-flex; gap: 10px; white-space: nowrap; }
.crafting-attribute { color: var(--element-color); font-weight: 600; }
.crafting-attribute.negative { text-decoration: underline; text-decoration-thickness: 1px; text-underline-offset: 3px; }
.filter-option { display: flex; align-items: center; justify-content: space-between; width: 100%; }
.filter-option em { min-width: 2ch; color: #8a9094; font-style: normal; font-variant-numeric: tabular-nums; text-align: right; }
.list-pane { display: flex; flex-direction: column; min-height: 250px; overflow: hidden; border: 1px solid #d9ddda; background: #fff; }
.table-scroll { flex: 1; min-height: 0; overflow: auto; }
.matrix-scroll { flex: 1; min-height: 0; overflow: auto; }
.herb-matrix { width: max-content; min-width: 0; max-width: none; border-collapse: separate; border-spacing: 0; table-layout: auto; font-size: 12px; }
.herb-matrix th, .herb-matrix td { box-sizing: border-box; min-width: 0; padding: 5px 7px; overflow: hidden; border-right: 1px solid #e4e7e4; border-bottom: 1px solid #e4e7e4; text-align: left; vertical-align: top; }
.herb-matrix th { position: sticky; top: 0; z-index: 3; background: #f0f2ef; color: #555d61; font-weight: 600; text-align: center; vertical-align: middle; }
.herb-matrix thead tr:nth-child(2) th { top: 29px; }
.herb-matrix .sticky-grade-rank { left: 0; min-width: 48px; width: 48px; z-index: 5; text-align: right; }
.herb-matrix .sticky-grade-name { left: 48px; min-width: 92px; width: 92px; padding-right: 3px; padding-left: 3px; z-index: 5; background: #f7f8f6; white-space: nowrap; }
.matrix-grade-meter { width: 100%; height: 24px; }
.herb-matrix .matrix-price { width: 64px; padding-right: 5px; padding-left: 5px; white-space: nowrap; }
.herb-matrix thead .sticky-grade-rank, .herb-matrix thead .sticky-grade-name { background: #f0f2ef; }
.matrix-price-entry { display: block; }
.matrix-price-entry small { display: block; overflow: hidden; color: #788086; font-size: 10px; text-overflow: ellipsis; }
.shop-pool-cell { padding: 8px 10px !important; text-align: center !important; vertical-align: middle !important; background: #fff; }
.shop-pool-cell strong, .shop-pool-cell span, .shop-pool-cell small { display: block; }
.shop-pool-cell strong { color: #324d62; font-size: 14px; font-variant-numeric: tabular-nums; }
.shop-pool-cell span { margin-top: 2px; color: #626b70; }
.shop-pool-cell small { margin-top: 4px; color: #8a6a32; font-size: 10px; }
.shop-pool-cell.empty { color: #a3a9a5; background: #fafbfa; }
.herb-matrix .planting-time-column { width: 64px; line-height: 1.25; white-space: normal; }
.planting-time-cell { vertical-align: top !important; white-space: nowrap; }
.planting-time-cell strong { color: #324d62; font-size: 12px; font-weight: 600; }
.planting-time-cell .not-configured { color: #8a9094; font-size: 12px; font-weight: 500; }
.unimplemented-cell { color: #8a9094; text-align: center !important; vertical-align: middle !important; background: #fafbfa; }
.matrix-cell { white-space: nowrap; }
.matrix-cell:empty { background: #fafbfa; }
.matrix-herb { display: block; width: auto; min-width: 100%; padding: 0 4px 3px; border: 0; color: #333a3d; text-align: left; background: transparent; cursor: pointer; }
.matrix-herb + .matrix-herb { margin-top: 4px; border-top: 1px dashed #d8dcda; }
.matrix-herb:hover, .matrix-herb.selected { background: #e9f1e7; }
.matrix-herb-heading { display: block !important; white-space: nowrap; }
.matrix-herb-heading b { font-weight: 600; }
.matrix-herb small { display: flex; gap: 5px; margin-top: 2px; white-space: nowrap; }
.matrix-herb small i { color: var(--element-color); font-style: normal; font-weight: 600; }
.matrix-herb small i.negative { text-decoration: underline; text-decoration-thickness: 1px; text-underline-offset: 3px; }
.matrix-efficiency-line { display: flex; gap: 5px; align-items: baseline; margin-top: 2px; color: #536873; font-size: 12px; font-variant-numeric: tabular-nums; font-weight: 600; white-space: nowrap; }
.matrix-efficiency-line em { font-style: normal; }
.matrix-efficiency-line i { color: #9aa09d; font-size: 10px; font-style: normal; font-weight: 400; }
.herb-table { width: 100%; border-collapse: collapse; table-layout: auto; white-space: nowrap; }
.herb-table th, .herb-table td { padding: 4px 11px; border-bottom: 1px solid #eceeec; text-align: left; vertical-align: middle; }
.herb-table th { position: sticky; top: 0; z-index: 1; background: #f0f2ef; color: #555d61; font-size: 13px; font-weight: 600; }
.sort-button { display: inline-flex; gap: 4px; align-items: center; padding: 0; border: 0; color: inherit; font: inherit; background: transparent; cursor: pointer; }
.sort-button.active, .sort-button:hover { color: #356a91; }
.sort-mark { width: 12px; color: #899095; text-align: center; }
.sort-button.active .sort-mark { color: #356a91; }
.herb-table tbody tr { cursor: pointer; font-size: 14px; }
.herb-table tbody tr:hover { background: #f6f8f4; }
.herb-table tbody tr.selected { background: #e9f1e7; }
.herb-table .number-cell { text-align: right; font-variant-numeric: tabular-nums; }
.number-column { width: 48px; text-align: right !important; }
.icon-column, .icon-cell { width: 42px; padding: 2px 6px !important; text-align: center !important; }
.icon-cell img { display: block; width: 32px; height: 32px; object-fit: contain; }
.fill-column { width: 100%; padding: 0 !important; }
.pagination { flex: none; padding: 9px 10px; border-top: 1px solid #eceeec; }
.empty-state, .detail-empty { padding: 28px; color: #8a9094; text-align: center; }
.pane-resizer { display: flex; flex: none; align-items: center; justify-content: center; height: 18px; cursor: row-resize; }
.pane-resizer span { width: 48px; height: 3px; border-radius: 2px; background: #cfd4d0; }
.pane-resizer:hover span, .herb-page.resizing .pane-resizer span { background: #698663; }
.detail-pane { flex: 1; min-height: 0; padding: 16px 18px; overflow: auto; border: 1px solid #d9ddda; background: #fff; }
.detail-head { padding-bottom: 12px; border-bottom: 1px solid #eceeec; }
.detail-head h2 { font-size: 19px; }
.detail-title { display: flex; gap: 12px; align-items: center; }
.detail-title > img { flex: none; width: 56px; height: 56px; object-fit: contain; }
.detail-fields { display: grid; grid-template-columns: max-content minmax(0, 1fr); margin: 0; padding-top: 15px; }
.detail-fields > dt, .detail-fields > dd { margin: 0; padding: 9px 0; border-bottom: 1px solid #eceeec; }
.detail-fields > dt { padding-right: 28px; color: #4b524f; font-size: 14px; font-weight: 600; }
.efficiency-detail { display: flex; gap: 14px; align-items: baseline; }
.efficiency-detail strong { min-width: 3ch; color: #324d62; font-variant-numeric: tabular-nums; }
.efficiency-detail span { color: #747c80; font-size: 12px; }
.recipe-links { display: flex; flex-wrap: wrap; gap: 6px 20px; }
.detail-fields > .shape-heading { display: flex; grid-column: 1 / -1; gap: 18px; align-items: baseline; padding-bottom: 3px; border-bottom: 0; }
.shape-field { display: block; grid-column: 1 / -1; padding-top: 3px !important; }
.shape-name { color: #687076; }
.recipe-links a { color: #356a91; text-decoration: none; }
.recipe-links a:hover { text-decoration: underline; }
.source-evidence { margin-top: 16px; padding-top: 11px; border-top: 1px solid #eceeec; color: #68706c; }
.source-evidence summary { cursor: pointer; }
.source-evidence dl { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 6px 14px; margin: 10px 0 0; font-size: 12px; }
.source-evidence dt { color: #737a7e; }
.source-evidence dd { margin: 0; overflow-wrap: anywhere; }
@media (max-width: 760px) {
  .toolbar { flex-wrap: wrap; }
  .detail-fields { grid-template-columns: 1fr; }
  .detail-fields > dt { padding-bottom: 4px; border-bottom: 0; }
  .detail-fields > dd { padding-top: 0; }
}
</style>
