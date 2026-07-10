<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Search } from '@element-plus/icons-vue'
import { useRoute, useRouter } from 'vue-router'

import {
  fetchZaohuaAlchemyMeta,
  fetchZaohuaAlchemyRecipe,
  fetchZaohuaAlchemyRecipes,
  type ZaohuaAlchemyMeta,
  type ZaohuaAlchemyRecipe,
} from '@/api/zaohua'
import StandardPagination from '@/components/StandardPagination.vue'
import { useResizablePane } from '@/utils/useResizablePane'

const route = useRoute()
const router = useRouter()
const loading = ref(false)
const meta = ref<ZaohuaAlchemyMeta | null>(null)
const recipes = ref<ZaohuaAlchemyRecipe[]>([])
const selected = ref<ZaohuaAlchemyRecipe | null>(null)
const query = ref('')
const grade = ref('')
const page = ref(1)
const pageSize = ref(40)
const total = ref(0)
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
  storageKey: 'zaohua:alchemy:list-pane-height',
})
const listPaneStyle = computed(() => ({ height: `${listPaneHeight.value}px` }))

const ingredientText = (recipe: ZaohuaAlchemyRecipe) => recipe.example_items
  .map(item => `${item.name}×${item.count ?? 0}`)
  .join('、')

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

const formatPrice = (value?: number) => Number.isFinite(value)
  ? new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 }).format(Number(value))
  : '—'

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

const loadRecipes = async () => {
  const sequence = ++requestSequence
  loading.value = true
  try {
    const response = await fetchZaohuaAlchemyRecipes({
      q: query.value.trim(),
      grade: grade.value,
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

onMounted(async () => {
  await Promise.all([loadMeta(), loadRecipes()])
})

onBeforeUnmount(() => {
  window.clearTimeout(searchTimer)
})
</script>

<template>
  <main class="alchemy-page" :class="{ resizing: isResizing }">
    <header class="page-head">
      <div>
        <h1>造化仙缘 · 炼丹</h1>
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
          <span v-if="gradeOptionByName(String(value || ''))" class="grade-label" :style="gradeStyle(gradeOptionByName(String(value || ''))!)">
            <i></i>
            <span>{{ value }}</span>
          </span>
        </template>
        <el-option
          v-for="item in meta?.grades || []"
          :key="item.name"
          :label="item.name"
          :value="item.name"
        >
          <span class="grade-option" :style="gradeStyle(item)">
            <span class="grade-label"><i></i><span>{{ item.name }}</span></span>
            <em>{{ item.count }}</em>
          </span>
        </el-option>
      </el-select>
    </section>

    <section class="list-pane" :style="listPaneStyle" v-loading="loading">
      <div class="table-scroll">
        <table class="recipe-table">
          <thead>
            <tr>
              <th class="number-column">编号</th>
              <th class="icon-column">图标</th>
              <th>产出</th>
              <th>成丹</th>
              <th>品阶</th>
              <th>价格</th>
              <th>五行需求</th>
              <th>示例药材</th>
              <th class="fill-column" aria-hidden="true"></th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="(recipe, recipeIndex) in recipes"
              :key="recipe.recipe_id"
              :class="{ selected: selected?.recipe_id === recipe.recipe_id }"
              @click="selectRecipe(recipe)"
            >
              <td class="number-cell">{{ (page - 1) * pageSize + recipeIndex + 1 }}</td>
              <td class="icon-cell">
                <img
                  v-if="recipe.output.icon_url"
                  :src="recipe.output.icon_url"
                  :alt="recipe.output.name"
                  loading="lazy"
                  @error="hideBrokenImage"
                />
              </td>
              <td><span class="grade-text" :style="gradeStyle(recipe.output)">{{ recipe.output.name }}</span></td>
              <td class="number-cell">{{ recipe.output.count }}</td>
              <td>
                <span v-if="recipe.output.grade_name" class="grade-label" :style="gradeStyle(recipe.output)">
                  <i></i>
                  <span>{{ recipe.output.grade_name }}</span>
                </span>
                <span v-else>—</span>
              </td>
              <td class="number-cell">{{ formatPrice(recipe.output.price) }}</td>
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
              <td class="ingredients-cell" :title="ingredientText(recipe)">{{ ingredientText(recipe) || '—' }}</td>
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
            <h2 class="grade-text" :style="gradeStyle(selected.output)">{{ selected.output.name }}</h2>
            <p>
              成丹 {{ selected.output.count }} ·
              <span v-if="selected.output.grade_name" class="grade-label" :style="gradeStyle(selected.output)">
                <i></i>
                <span>{{ selected.output.grade_name }}</span>
              </span>
              <span v-else>未标品阶</span>
            </p>
          </div>
        </div>
      </header>

      <dl class="detail-fields">
        <dt>五行需求</dt>
        <dd>
          <ul class="detail-element-list">
            <li v-for="item in selected.attr_limits" :key="item.element">
              <span class="element-text" :style="elementStyle(item.element)">{{ item.label }}系</span>
              <span class="element-text element-number" :style="elementStyle(item.element)">{{ item.value }}</span>
            </li>
          </ul>
        </dd>

        <dt>示例药材</dt>
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
                  <span class="grade-text" :style="gradeStyle(item)">{{ item.name }}</span>
                  <em>×{{ item.count }}</em>
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
  min-height: calc(100vh - 92px);
  padding: 18px 22px 28px;
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
  width: 164px;
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

.element-list {
  display: inline-flex;
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

.element-value:not(:last-child)::after {
  margin: 0 5px;
  color: #a1a7aa;
  content: '·';
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
  padding: 8px 11px;
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

.recipe-table tbody tr {
  cursor: pointer;
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
  width: 46px;
  padding: 4px 6px;
  text-align: center;
}

.icon-cell img {
  display: block;
  width: 36px;
  height: 36px;
  object-fit: contain;
}

.ingredients-cell {
  max-width: 440px;
  overflow: hidden;
  text-overflow: ellipsis;
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
