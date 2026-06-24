<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { Search } from '@element-plus/icons-vue'
import {
  getMystiaCatalogEntries,
  type MystiaCatalogKind,
} from '@/api/mystia'
import StandardPagination from '@/components/StandardPagination.vue'
import { useResizablePane } from '@/utils/useResizablePane'

type Row = Record<string, any>

interface TabConfig {
  kind: MystiaCatalogKind
  label: string
  idLabel: string
  nameKey: string
  assetMode?: 'item' | 'image' | 'audio'
}

interface TableColumn {
  key: string
  label: string
  className?: string
  sortKey?: string
  text: (row: Row) => string
}

const tabs: TabConfig[] = [
  { kind: 'foods', label: '菜品', idLabel: '菜品ID', nameKey: 'name', assetMode: 'item' },
  { kind: 'ingredients', label: '食材', idLabel: '食材ID', nameKey: 'name', assetMode: 'item' },
  { kind: 'beverages', label: '饮品', idLabel: '饮品ID', nameKey: 'name', assetMode: 'item' },
  { kind: 'special_guests', label: '稀客', idLabel: '角色ID', nameKey: 'name' },
  { kind: 'guests', label: '普通客人', idLabel: '客人ID', nameKey: 'name' },
  { kind: 'locations', label: '地点', idLabel: '摊位ID', nameKey: 'name' },
  { kind: 'images', label: '图片素材', idLabel: '素材', nameKey: 'name', assetMode: 'image' },
  { kind: 'audio', label: '音频素材', idLabel: '音频', nameKey: 'name', assetMode: 'audio' },
]

const activeKind = ref<MystiaCatalogKind>('foods')
const query = ref('')
const loading = ref(false)
const rows = ref<Row[]>([])
const selectedId = ref<string | number | null>(null)
const page = ref(1)
const pageSize = ref(50)
const total = ref(0)
const sortBy = ref('')
const sortOrder = ref<'asc' | 'desc' | ''>('')
let requestToken = 0

const activeTab = computed(() => tabs.find((item) => item.kind === activeKind.value) ?? tabs[0])
const selectedRow = computed(() => rows.value.find((row) => rowKey(row) === selectedId.value) ?? rows.value[0] ?? null)
const {
  paneHeight: listHeight,
  isResizing,
  startResizing,
} = useResizablePane({
  initialHeight: 420,
  getAdaptiveHeight: () => {
    const availableHeight = Math.max(420, window.innerHeight - 210)
    return Math.floor(availableHeight * 0.55)
  },
  getResizeBounds: () => {
    const availableHeight = Math.max(420, window.innerHeight - 210)
    return {
      min: 180,
      max: Math.max(220, availableHeight - 180),
    }
  },
  storageKey: 'mystia:wiki:list-pane-height',
})
const listPaneStyle = computed(() => ({ height: `${listHeight.value}px` }))


function rowKey(row: Row): string | number {
  return row.id ?? row.path ?? row.name
}

function rowUniqueKey(row: Row, index: number): string {
  return `${activeKind.value}:${String(rowKey(row))}:${index}`
}

function rowTitle(row: Row): string {
  return String(row[activeTab.value.nameKey] ?? row.name ?? row.food_name ?? row.string_id ?? row.id ?? row.path)
}

function compactList(value: unknown): string {
  if (!Array.isArray(value)) return ''
  return value.map((item) => {
    if (item && typeof item === 'object' && 'name' in item) return item.name
    return String(item)
  }).join('、')
}

function normalizeDisplayText(value: unknown): string {
  if (value === null || value === undefined) return ''
  return String(value).replace(/\\r\\n|\\n|\\r/g, '\n')
}

function formatBytes(value: unknown): string {
  const bytes = Number(value)
  if (!Number.isFinite(bytes) || bytes < 0) return ''
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB']
  let size = bytes / 1024
  let unitIndex = 0
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024
    unitIndex += 1
  }
  const precision = size >= 10 ? 1 : 2
  return `${Number(size.toFixed(precision))} ${units[unitIndex]}`
}

function formatMultiplier(value: unknown): string {
  const numberValue = Number(value)
  if (!Number.isFinite(numberValue)) return ''
  return numberValue.toFixed(2)
}

function formatProbability(value: unknown): string {
  const numberValue = Number(value)
  if (!Number.isFinite(numberValue)) return ''
  return `${Math.round(numberValue * 100)}%`
}

function formatRange(value: unknown): string {
  if (!value || typeof value !== 'object') return ''
  const range = value as Row
  if (range.min === undefined && range.max === undefined) return ''
  return `${range.min ?? ''} - ${range.max ?? ''}`
}

function formatLocationList(value: unknown): string {
  if (!Array.isArray(value)) return ''
  return value.map((item) => {
    if (!item || typeof item !== 'object') return String(item)
    const row = item as Row
    const probability = row.probability !== undefined ? ` ${formatProbability(row.probability)}` : ''
    return `${row.name ?? row.map_label ?? row.id}${probability}`
  }).join('、')
}

function compactSpawnList(value: unknown): string {
  if (!Array.isArray(value)) return ''
  return value
    .slice(0, 8)
    .map((item) => {
      if (item && typeof item === 'object' && 'name' in item) {
        const probability = 'probability' in item ? ` ${formatProbability((item as Row).probability)}` : ''
        return `${String((item as Row).name)}${probability}`
      }
      return String(item)
    })
    .join('、') + (value.length > 8 ? ` 等${value.length}项` : '')
}

function compactSpells(value: unknown): string {
  if (!Array.isArray(value)) return ''
  return value
    .slice(0, 3)
    .map((item) => {
      if (!item || typeof item !== 'object') return String(item)
      const row = item as Row
      return `${row.type_label ?? ''}${row.name ? `：${row.name}` : ''}`
    })
    .join('、') + (value.length > 3 ? ` 等${value.length}项` : '')
}

function hasRecipe(row: Row): boolean {
  return row.has_recipe !== false && (row.cook_time !== undefined || Array.isArray(row.ingredients))
}

const tableColumns = computed<TableColumn[]>(() => {
  const baseColumns: TableColumn[] = [
    { key: 'id', label: activeTab.value.idLabel, className: 'mono', text: (row) => String(row.id ?? row.path ?? '') },
    { key: 'name', label: '名称', className: 'name-cell', text: rowTitle },
  ]
  if (activeKind.value === 'foods') {
    return [
      ...baseColumns,
      { key: 'level', label: '等级', className: 'mono', text: (row) => row.level ? `Lv${row.level}` : '' },
      { key: 'base_value', label: '基础售价', className: 'mono', text: (row) => String(row.base_value ?? '') },
      { key: 'tags', label: '标签', className: 'tag-cell', text: (row) => compactList(row.effective_tags ?? row.tags) },
      { key: 'ban_tags', label: '负面标签', className: 'tag-cell', text: (row) => compactList(row.ban_tags) },
      { key: 'cook_time', label: '烹饪时间(秒)', className: 'mono', text: (row) => hasRecipe(row) ? String(row.cook_time ?? '') : '无菜谱' },
      { key: 'ingredients', label: '食材', className: 'tag-cell', text: (row) => compactList(row.ingredients) },
      { key: 'cooker_name', label: '厨具', text: (row) => String(row.cooker_name ?? '') },
    ]
  }
  if (activeKind.value === 'ingredients') {
    return [
      ...baseColumns,
      { key: 'tags', label: '标签', className: 'tag-cell', text: (row) => compactList(row.tags) },
      { key: 'base_value', label: '基础价值', className: 'mono', text: (row) => String(row.base_value ?? '') },
    ]
  }
  if (activeKind.value === 'beverages') {
    return [
      ...baseColumns,
      { key: 'tags', label: '标签', className: 'tag-cell', text: (row) => compactList(row.tags) },
      { key: 'base_value', label: '基础售价', className: 'mono', text: (row) => String(row.base_value ?? '') },
    ]
  }
  if (activeKind.value === 'images') {
    return [
      ...baseColumns,
      { key: 'size', label: '尺寸', className: 'mono', text: (row) => `${row.width ?? ''}x${row.height ?? ''}` },
      { key: 'kind', label: '分类', text: (row) => String(row.kind ?? '') },
    ]
  }
  if (activeKind.value === 'audio') {
    return [
      ...baseColumns,
      { key: 'format', label: '格式', className: 'mono', text: (row) => String(row.format ?? '') },
      { key: 'bytes', label: '大小', className: 'mono', sortKey: 'bytes', text: (row) => formatBytes(row.bytes) },
      { key: 'group', label: '分组', text: (row) => String(row.group ?? '') },
    ]
  }
  if (activeKind.value === 'locations') {
    return [
      ...baseColumns,
      { key: 'map_label', label: '区域', text: (row) => String(row.map_label ?? '') },
      { key: 'shop_level', label: '等级', className: 'mono', text: (row) => row.shop_level ? `Lv${row.shop_level}` : '' },
      { key: 'variant', label: '配置', text: (row) => [row.variant_label, row.variant_summary].filter(Boolean).join('：') },
      { key: 'normal_guests', label: '普通客人', className: 'tag-cell', text: (row) => compactSpawnList(row.normal_guests) },
      { key: 'special_guests', label: '稀客', className: 'tag-cell', text: (row) => compactSpawnList(row.special_guests) },
    ]
  }
  if (activeKind.value === 'special_guests') {
    return [
      ...baseColumns,
      { key: 'tags', label: '偏好标签', className: 'tag-cell', text: (row) => compactList(row.like_food_tags) },
      { key: 'spells', label: '符卡', className: 'tag-cell', text: (row) => compactSpells(row.spells) },
    ]
  }
  return [
    ...baseColumns,
    { key: 'tags', label: '偏好标签', className: 'tag-cell', text: (row) => compactList(row.like_food_tags) },
    { key: 'value', label: '资金倍率', className: 'mono', text: (row) => formatMultiplier(row.fund_multiplier) },
  ]
})

function itemIcon(row: Row | null): Row | null {
  if (!row) return null
  return row.assets?.icon ?? null
}

function itemPlate(row: Row | null): Row | null {
  if (!row) return null
  return row.assets?.plate ?? null
}

function cookerIcon(row: Row | null): Row | null {
  if (!row) return null
  return row.assets?.cooker ?? row.cooker?.assets?.icon ?? null
}

function portraitImages(row: Row | null): Row[] {
  const portraits = row?.assets?.portraits
  return Array.isArray(portraits) ? portraits : []
}

function previewImage(row: Row | null): Row | null {
  if (!row) return null
  if (activeTab.value.assetMode === 'image') return row
  return itemIcon(row) ?? itemPlate(row)
}

function imagePreviewStyle(image: Row | null) {
  const width = Number(image?.width ?? 0)
  const height = Number(image?.height ?? 0)
  if (!width || !height) return {}
  const maxSmallSide = Math.max(width, height)
  const scale = maxSmallSide <= 32 ? 4 : maxSmallSide <= 96 ? 3 : maxSmallSide <= 160 ? 2 : 1
  return {
    width: `${Math.min(width * scale, 640)}px`,
    height: `${Math.min(height * scale, 520)}px`,
  }
}

function detailEntries(row: Row | null) {
  if (!row) return []
  const hidden = new Set(['id', 'name', 'food_name', 'description', 'description_parts', 'assets', 'url', 'cooker', 'normal_guests', 'special_guests', 'spells', 'conversations', 'evaluations', 'requests', 'food_tag_requests', 'beverage_tag_requests'])
  return Object.entries(row)
    .filter(([key, value]) => !hidden.has(key) && value !== null && value !== undefined && value !== '')
    .map(([key, value]) => [fieldLabel(key), formatDetailValue(key, value)] as const)
    .filter(([, value]) => value !== '')
}

function formatDetailValue(key: string, value: unknown): unknown {
  if (key === 'bytes') return formatBytes(value)
  if (key === 'fund_multiplier') return formatMultiplier(value)
  if (key === 'probability') return formatProbability(value)
  if (key.endsWith('_range') || key.endsWith('_interval')) return formatRange(value)
  if (key === 'locations') return formatLocationList(value)
  if (typeof value === 'string') return normalizeDisplayText(value)
  return Array.isArray(value) ? compactList(value) : value
}

function fieldLabel(key: string): string {
  const labels: Record<string, string> = {
    level: '等级',
    base_value: activeKind.value === 'ingredients' ? '基础价值' : '基础售价',
    food_base_value: '菜品基础售价',
    cook_time: '烹饪时间',
    fund_multiplier: '资金倍率',
    locations: '出现地点',
    conversations: '闲聊台词',
    evaluations: '评价台词',
    requests: '好感请求',
    food_tag_requests: '菜品点单请求',
    beverage_tag_requests: '饮品点单请求',
    tag_ids: '标签ID',
    tags: '标签',
    effective_tag_ids: '有效标签ID',
    effective_tags: '有效标签',
    ban_tag_ids: '排除标签ID',
    ban_tags: '负面标签',
    is_collab: '联动',
    ingredients: '食材',
    cooker_type: '厨具类型',
    cooker_name: '厨具',
    has_recipe: '有菜谱',
    recipe_id: '菜谱ID',
    food_tags: '菜品标签',
    evaluation: '评价',
    like_food_tag_ids: '偏好菜品标签ID',
    like_food_tags: '偏好菜品标签',
    like_beverage_tag_ids: '偏好饮品标签ID',
    like_beverage_tags: '偏好饮品标签',
    is_child: '儿童',
    kind: '分类',
    group: '分组',
    bytes: '大小',
    format: '格式',
    map_name: '地图标识',
    map_label: '区域',
    shop_level: '店铺等级',
    variant_index: '配置序号',
    variant_count: '同地点配置数',
    variant_label: '配置',
    variant_summary: '配置差异',
    base_fund_range: '基础资金范围',
    normal_guest_span_interval: '普通客人间隔',
    spawn_passerby_guest: '生成路人',
    passerby_guest_span_interval: '路人间隔',
    normal_guest_pool_weight: '普通客人权重总和',
    special_guest_gacha_interval: '稀客抽取间隔',
    guest_table_count: '客桌数量',
    cook_table_count: '厨台数量',
    music_package_index: '音乐包',
    music_package_override: '覆盖音乐包',
    width: '宽',
    height: '高',
    path: '路径',
    bundle: 'bundle',
    exbad: '很差',
    bad: '较差',
    norm: '普通',
    good: '满意',
    exgood: '极好',
    lackmoneyangry: '钱不够(生气)',
    lackmoneynormal: '钱不够',
    repell: '被驱赶',
    seenRepell: '目击驱赶',
  }
  return labels[key] ?? key
}

function requestWeightLabel(value: unknown): string {
  const weight = Number(value)
  if (!Number.isFinite(weight) || weight === 1) return ''
  return `x${weight}`
}

function requestRows(value: unknown, requireLine = false): Row[] {
  if (!Array.isArray(value)) return []
  return value.filter((item) => {
    if (!item || typeof item !== 'object') return false
    return !requireLine || Boolean(normalizeDisplayText(item.line).trim())
  }) as Row[]
}


async function loadRows() {
  const token = ++requestToken
  loading.value = true
  try {
    const result = await getMystiaCatalogEntries(activeKind.value, {
      query: query.value,
      page: page.value,
      pageSize: pageSize.value,
      sortBy: sortBy.value,
      sortOrder: sortOrder.value,
    })
    if (token !== requestToken) return
    rows.value = result.items
    page.value = result.page
    pageSize.value = result.page_size
    total.value = result.total
    if (!rows.value.some((row) => rowKey(row) === selectedId.value)) {
      selectedId.value = rows.value.length ? rowKey(rows.value[0]) : null
    }
  } finally {
    if (token === requestToken) loading.value = false
  }
}

function columnSortOrder(column: TableColumn): '' | 'asc' | 'desc' {
  return column.sortKey === sortBy.value ? sortOrder.value : ''
}

function sortMark(column: TableColumn): string {
  const order = columnSortOrder(column)
  if (order === 'asc') return '↑'
  if (order === 'desc') return '↓'
  return '↕'
}

function toggleSort(column: TableColumn) {
  if (!column.sortKey) return
  if (sortBy.value !== column.sortKey) {
    sortBy.value = column.sortKey
    sortOrder.value = 'asc'
  } else if (sortOrder.value === 'asc') {
    sortOrder.value = 'desc'
  } else {
    sortBy.value = ''
    sortOrder.value = ''
  }
  page.value = 1
  void loadRows()
}

function handlePageChange(value: number) {
  page.value = value
  void loadRows()
}

function handlePageSizeChange(value: number) {
  pageSize.value = value
  page.value = 1
  void loadRows()
}

watch(activeKind, () => {
  page.value = 1
  sortBy.value = ''
  sortOrder.value = ''
  void loadRows()
})

let queryTimer = 0
watch(query, () => {
  window.clearTimeout(queryTimer)
  queryTimer = window.setTimeout(() => {
    page.value = 1
    void loadRows()
  }, 200)
})

onMounted(() => {
  void loadRows()
})
</script>

<template>
  <main class="mystia-wiki-page">
    <header class="wiki-toolbar">
      <el-segmented v-model="activeKind" :options="tabs.map((item) => ({ label: item.label, value: item.kind }))" />
      <el-input
        v-model="query"
        class="wiki-search"
        clearable
        placeholder="搜索名称、描述、标签、素材"
        :prefix-icon="Search"
      />
    </header>


    <section class="wiki-body" :class="{ 'is-resizing': isResizing }" v-loading="loading">
      <div class="wiki-list" :style="listPaneStyle">
        <table>
          <colgroup>
            <col class="thumb-col" />
            <col v-for="column in tableColumns" :key="column.key" class="data-col" />
            <col class="fill-col" />
          </colgroup>
          <thead>
            <tr>
              <th class="thumb-head">图</th>
              <th v-for="column in tableColumns" :key="column.key">
                <button
                  v-if="column.sortKey"
                  type="button"
                  class="sort-button"
                  :class="{ active: Boolean(columnSortOrder(column)) }"
                  @click="toggleSort(column)"
                >
                  <span>{{ column.label }}</span>
                  <span class="sort-mark">{{ sortMark(column) }}</span>
                </button>
                <template v-else>{{ column.label }}</template>
              </th>
              <th class="fill-cell"></th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="(row, index) in rows"
              :key="rowUniqueKey(row, index)"
              :class="{ selected: rowKey(selectedRow ?? {}) === rowKey(row) }"
              @click="selectedId = rowKey(row)"
            >
              <td class="thumb-cell">
                <img v-if="previewImage(row)" :src="previewImage(row)?.url" alt="" />
                <span v-else-if="activeKind === 'audio'" class="audio-mark">♪</span>
              </td>
              <td v-for="column in tableColumns" :key="column.key" :class="column.className">
                <span v-if="column.key === 'cooker_name'" class="cooker-cell">
                  <img v-if="cookerIcon(row)" :src="cookerIcon(row)?.url" alt="" />
                  <span>{{ column.text(row) }}</span>
                </span>
                <template v-else>{{ column.text(row) }}</template>
              </td>
              <td class="fill-cell"></td>
            </tr>
          </tbody>
        </table>
      </div>

      <StandardPagination
        v-if="total > 0"
        class="wiki-pagination"
        :page="page"
        :page-size="pageSize"
        :total="total"
        :page-size-options="[20, 50, 100, 200]"
        align="left"
        @update:page="handlePageChange"
        @update:page-size="handlePageSizeChange"
      />

      <div
        class="wiki-resizer"
        role="separator"
        aria-orientation="horizontal"
        title="拖动调整列表和详情的比例"
        @mousedown="startResizing"
      >
        <span></span>
      </div>

      <aside class="wiki-detail" v-if="selectedRow">
        <div class="detail-title">
          <span class="mono">#{{ selectedRow.id ?? selectedRow.path }}</span>
          <h2>{{ rowTitle(selectedRow) }}</h2>
        </div>

        <div v-if="previewImage(selectedRow)" class="asset-preview">
          <img :src="previewImage(selectedRow)?.url" :style="imagePreviewStyle(previewImage(selectedRow))" alt="" />
        </div>
        <div v-if="itemIcon(selectedRow) && itemPlate(selectedRow)" class="asset-pair">
          <img :src="itemIcon(selectedRow)?.url" alt="" />
          <img :src="itemPlate(selectedRow)?.url" alt="" />
          <img v-if="cookerIcon(selectedRow)" :src="cookerIcon(selectedRow)?.url" alt="" />
        </div>
        <div v-if="activeKind === 'special_guests' && portraitImages(selectedRow).length" class="portrait-gallery">
          <figure v-for="portrait in portraitImages(selectedRow)" :key="portrait.path">
            <img :src="portrait.url" alt="" />
            <figcaption>{{ portrait.name }}</figcaption>
          </figure>
        </div>
        <audio v-if="activeKind === 'audio' && selectedRow.url" class="audio-player" :src="selectedRow.url" controls />

        <div v-if="activeKind === 'special_guests' && selectedRow.description_parts?.length" class="description-group">
          <p v-for="(part, index) in selectedRow.description_parts" :key="`desc:${index}`" class="description">
            {{ normalizeDisplayText(part) }}
          </p>
        </div>
        <p v-else-if="selectedRow.description" class="description">{{ normalizeDisplayText(selectedRow.description) }}</p>
        <section v-if="activeKind === 'special_guests' && selectedRow.spells?.length" class="spell-section">
          <h3>符卡</h3>
          <table class="spell-table">
            <thead>
              <tr>
                <th>类型</th>
                <th>名称</th>
                <th>效果</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="spell in selectedRow.spells" :key="`spell:${spell.slot}:${spell.name}`">
                <td>{{ spell.type_label }}</td>
                <td class="name-cell">{{ spell.name }}</td>
                <td class="spell-description">{{ normalizeDisplayText(spell.description) }}</td>
              </tr>
            </tbody>
          </table>
        </section>
        <section v-if="activeKind === 'special_guests' && requestRows(selectedRow.food_tag_requests, true).length" class="spell-section">
          <h3>下单请求</h3>
          <table class="spell-table">
            <thead>
              <tr>
                <th>标签</th>
                <th>权重</th>
                <th>台词</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="request in requestRows(selectedRow.food_tag_requests, true)" :key="`food-request:${request.tag_id}:${request.line}`">
                <td class="name-cell">{{ request.tag || '-' }}</td>
                <td class="mono">{{ requestWeightLabel(request.weight) }}</td>
                <td class="spell-description">{{ normalizeDisplayText(request.line) }}</td>
              </tr>
            </tbody>
          </table>
        </section>
        <section v-if="activeKind === 'special_guests' && requestRows(selectedRow.beverage_tag_requests, true).length" class="spell-section">
          <h3>饮品点单请求</h3>
          <table class="spell-table">
            <thead>
              <tr>
                <th>标签</th>
                <th>权重</th>
                <th>台词</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="request in requestRows(selectedRow.beverage_tag_requests, true)" :key="`beverage-request:${request.tag_id}:${request.line}`">
                <td class="name-cell">{{ request.tag || '-' }}</td>
                <td class="mono">{{ requestWeightLabel(request.weight) }}</td>
                <td class="spell-description">{{ normalizeDisplayText(request.line) }}</td>
              </tr>
            </tbody>
          </table>
        </section>
        <section v-if="activeKind === 'special_guests' && selectedRow.requests?.length" class="spell-section">
          <h3>好感请求</h3>
          <table class="spell-table">
            <thead>
              <tr>
                <th>等级</th>
                <th>类型</th>
                <th>内容</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="request in selectedRow.requests" :key="request.asset_name">
                <td class="mono">Lv{{ request.level }}</td>
                <td>{{ request.kind_label }}</td>
                <td class="spell-description">{{ request.lines.join('\n') }}</td>
              </tr>
            </tbody>
          </table>
        </section>
        <section v-if="activeKind === 'special_guests' && selectedRow.conversations?.length" class="spell-section">
          <h3>闲聊台词</h3>
          <ol class="line-list">
            <li v-for="(line, index) in selectedRow.conversations" :key="`conv:${index}`">{{ line }}</li>
          </ol>
        </section>
        <section v-if="activeKind === 'special_guests' && selectedRow.evaluations && Object.keys(selectedRow.evaluations).length" class="spell-section">
          <h3>评价台词</h3>
          <dl class="line-dl">
            <template v-for="[key, value] in Object.entries(selectedRow.evaluations)" :key="key">
              <dt>{{ fieldLabel(key) }}</dt>
              <dd>{{ normalizeDisplayText(value) }}</dd>
            </template>
          </dl>
        </section>
        <div v-if="activeKind === 'locations'" class="spawn-sections">
          <section v-if="selectedRow.normal_guests?.length" class="spawn-section">
            <h3>普通客人</h3>
            <table class="spawn-table">
              <thead>
                <tr>
                  <th>图</th>
                  <th>客人</th>
                  <th>权重</th>
                  <th>抽取占比</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="guest in selectedRow.normal_guests" :key="`normal:${guest.id}`">
                  <td class="spawn-thumb">
                    <img v-if="guest.assets?.icon" :src="guest.assets.icon.url" alt="" />
                  </td>
                  <td>{{ guest.name }}</td>
                  <td class="mono">{{ guest.weight }}</td>
                  <td class="mono">{{ formatProbability(guest.probability) }}</td>
                </tr>
              </tbody>
            </table>
          </section>
          <section v-if="selectedRow.special_guests?.length" class="spawn-section">
            <h3>稀客</h3>
            <table class="spawn-table">
              <thead>
                <tr>
                  <th>图</th>
                  <th>角色</th>
                  <th>每次抽取概率</th>
                  <th>条件</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="guest in selectedRow.special_guests" :key="`special:${guest.id}`">
                  <td class="spawn-thumb">
                    <img v-if="guest.assets?.icon" :src="guest.assets.icon.url" alt="" />
                  </td>
                  <td>{{ guest.name }}</td>
                  <td class="mono">{{ formatProbability(guest.probability) }}</td>
                  <td>{{ [guest.only_spawn_after_unlocking ? '解锁后' : '', guest.only_spawn_when_place_recorded ? '记录地点后' : ''].filter(Boolean).join('、') }}</td>
                </tr>
              </tbody>
            </table>
          </section>
        </div>
        <dl>
          <template v-for="[key, value] in detailEntries(selectedRow)" :key="key">
            <dt>{{ key }}</dt>
            <dd>{{ value }}</dd>
          </template>
        </dl>
      </aside>
    </section>
  </main>
</template>

<style scoped>
.mystia-wiki-page {
  height: 100%;
  min-height: 0;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  color: #202124;
}

.wiki-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.wiki-search {
  width: 300px;
}


.wiki-body {
  min-height: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 0;
}

.wiki-list {
  flex: none;
  max-width: 100%;
  overflow: auto;
  border: 1px solid #d8dde6;
  border-radius: 6px;
}

.wiki-pagination {
  flex: none;
  padding: 8px 0 0;
}

table {
  border-collapse: collapse;
  width: 100%;
  background: white;
}

col.thumb-col {
  width: 54px;
}

col.data-col {
  width: 1%;
}

col.fill-col {
  width: auto;
}

th,
td {
  padding: 7px 10px;
  border-bottom: 1px solid #edf0f4;
  text-align: left;
  font-size: 13px;
  vertical-align: middle;
}

th:not(.fill-cell),
td:not(.fill-cell) {
  white-space: nowrap;
}

th {
  position: sticky;
  top: 0;
  background: #f7f8fa;
  z-index: 1;
  font-weight: 600;
}

.sort-button {
  border: 0;
  padding: 0;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: transparent;
  color: inherit;
  font: inherit;
  cursor: pointer;
}

.sort-button.active {
  color: #2563eb;
}

.sort-mark {
  min-width: 14px;
  color: #64748b;
  font-size: 12px;
  text-align: center;
}

.sort-button.active .sort-mark {
  color: #2563eb;
}

tbody tr {
  cursor: pointer;
}

tbody tr:hover,
tbody tr.selected {
  background: #eef5ff;
}

.thumb-head,
.thumb-cell {
  width: 48px;
}

.thumb-cell img {
  display: block;
  width: 34px;
  height: 34px;
  object-fit: contain;
  image-rendering: pixelated;
}

.audio-mark {
  display: inline-grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border-radius: 4px;
  background: #eef2f7;
  color: #475569;
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  color: #4b5563;
  white-space: nowrap;
}

.name-cell {
  font-weight: 600;
}

.tag-cell {
  max-width: 420px;
  color: #4b5563;
  overflow: hidden;
  text-overflow: ellipsis;
}

.cooker-cell {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
}

.cooker-cell img {
  width: 30px;
  height: 30px;
  object-fit: contain;
  image-rendering: pixelated;
  flex: 0 0 auto;
}

.fill-cell {
  padding-left: 0;
  padding-right: 0;
}

.wiki-resizer {
  flex: none;
  height: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: ns-resize;
  touch-action: none;
}

.wiki-resizer span {
  width: 48px;
  height: 4px;
  border-top: 1px solid #d8dde6;
  border-bottom: 1px solid #d8dde6;
}

.wiki-resizer:hover,
.wiki-body.is-resizing .wiki-resizer {
  background: #f5f9ff;
}

.wiki-resizer:hover span,
.wiki-body.is-resizing .wiki-resizer span {
  border-color: #409eff;
}

.wiki-detail {
  border-top: 1px solid #d8dde6;
  padding-top: 14px;
  min-width: 0;
  min-height: 0;
  flex: 1;
  overflow: auto;
}

.detail-title {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.detail-title h2 {
  margin: 0;
  font-size: 20px;
  line-height: 1.35;
}

.asset-preview {
  margin-top: 12px;
  display: inline-flex;
  max-width: 100%;
  padding: 8px;
  align-items: center;
  justify-content: center;
  background:
    linear-gradient(45deg, #f1f5f9 25%, transparent 25%),
    linear-gradient(-45deg, #f1f5f9 25%, transparent 25%),
    linear-gradient(45deg, transparent 75%, #f1f5f9 75%),
    linear-gradient(-45deg, transparent 75%, #f1f5f9 75%);
  background-color: #fff;
  background-position: 0 0, 0 8px, 8px -8px, -8px 0;
  background-size: 16px 16px;
  border: 1px solid #d8dde6;
  border-radius: 4px;
  overflow: auto;
}

.asset-preview img {
  display: block;
  max-width: min(100%, 640px);
  max-height: 520px;
  object-fit: contain;
  image-rendering: pixelated;
}

.asset-pair {
  margin-top: 10px;
  display: flex;
  gap: 10px;
  align-items: center;
}

.asset-pair img {
  width: 48px;
  height: 48px;
  object-fit: contain;
  image-rendering: pixelated;
  border: 1px solid #e5e7eb;
  border-radius: 4px;
  background: white;
}

.portrait-gallery {
  margin-top: 12px;
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: flex-end;
}

.portrait-gallery figure {
  margin: 0;
  width: 118px;
}

.portrait-gallery img {
  display: block;
  width: 118px;
  height: 150px;
  object-fit: contain;
  object-position: center bottom;
  image-rendering: pixelated;
  border: 1px solid #d8dde6;
  border-radius: 4px;
  background:
    linear-gradient(45deg, #f1f5f9 25%, transparent 25%),
    linear-gradient(-45deg, #f1f5f9 25%, transparent 25%),
    linear-gradient(45deg, transparent 75%, #f1f5f9 75%),
    linear-gradient(-45deg, transparent 75%, #f1f5f9 75%);
  background-color: #fff;
  background-position: 0 0, 0 8px, 8px -8px, -8px 0;
  background-size: 16px 16px;
}

.portrait-gallery figcaption {
  margin-top: 4px;
  font-size: 12px;
  color: #64748b;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.audio-player {
  width: min(420px, 100%);
  margin-top: 12px;
}

.description-group {
  margin: 10px 0 14px;
}

.description {
  margin: 0 0 10px;
  line-height: 1.7;
  color: #374151;
  white-space: pre-line;
}

.spawn-sections {
  display: flex;
  flex-direction: column;
  gap: 14px;
  margin-top: 12px;
}

.spell-section {
  margin-top: 12px;
}

.spell-section h3,
.spawn-section h3 {
  margin: 0 0 8px;
  font-size: 15px;
  line-height: 1.4;
}

.spell-table,
.spawn-table {
  width: max-content;
  max-width: 100%;
  border: 1px solid #e5e7eb;
  border-radius: 4px;
  overflow: hidden;
}

.spell-table th,
.spell-table td,
.spawn-table th,
.spawn-table td {
  padding: 6px 10px;
}

.spell-description {
  min-width: 260px;
  max-width: 760px;
  white-space: pre-line;
  line-height: 1.6;
  color: #374151;
}

.line-list {
  margin: 0;
  padding-left: 24px;
  max-width: 900px;
  line-height: 1.7;
  color: #374151;
}

.line-dl {
  max-width: 980px;
}

.line-dl dd {
  line-height: 1.6;
}

.spawn-thumb {
  width: 42px;
}

.spawn-thumb img {
  display: block;
  width: 30px;
  height: 30px;
  object-fit: contain;
  image-rendering: pixelated;
}

dl {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  gap: 8px 14px;
  margin: 14px 0 0;
  font-size: 13px;
}

dt {
  color: #6b7280;
}

dd {
  margin: 0;
  color: #202124;
  word-break: break-word;
  white-space: pre-line;
}

@media (max-width: 900px) {
  .wiki-list {
    min-height: 160px;
  }
}
</style>


