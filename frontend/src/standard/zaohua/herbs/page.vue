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
const selected = ref<ZaohuaHerb | null>(null)
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

onMounted(async () => {
  await Promise.all([loadMeta(), loadHerbs()])
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
      <el-input
        v-model="query"
        class="search-input"
        clearable
        :prefix-icon="Search"
        placeholder="搜索药材、描述或丹药"
      />
      <el-select v-model="grade" class="grade-select" aria-label="品阶筛选" placeholder="全部品阶" clearable>
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
      <el-select v-model="element" class="element-select" placeholder="全部所属五行" clearable>
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

    <section class="list-pane" :style="listPaneStyle" v-loading="loading">
      <div class="table-scroll">
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
        <dt>占格形状</dt>
        <dd class="shape-field">
          <HerbShapePreview
            :shape="selected.shape"
            :image-url="selected.shape?.image_url"
            :label="selected.name"
            :color="elementColor(selected.element_key)"
          />
          <span v-if="selected.shape?.name" class="shape-name">{{ selected.shape.name }}</span>
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
.recipe-links { display: flex; flex-wrap: wrap; gap: 6px 20px; }
.shape-field { display: flex; gap: 14px; align-items: center; }
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
