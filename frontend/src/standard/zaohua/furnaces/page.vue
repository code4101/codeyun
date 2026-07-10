<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Search } from '@element-plus/icons-vue'
import { useRoute, useRouter } from 'vue-router'

import { fetchZaohuaFurnace, fetchZaohuaFurnaceMeta, fetchZaohuaFurnaces, type ZaohuaFurnace, type ZaohuaFurnaceMeta } from '@/api/zaohua'
import StandardPagination from '@/components/StandardPagination.vue'
import { formatChineseCompactNumber } from '@/utils/numberFormat'
import { useResizablePane } from '@/utils/useResizablePane'
import GradeMeter from '../components/GradeMeter.vue'
import '../catalog-inspector.css'

const route = useRoute()
const router = useRouter()
const loading = ref(false)
const meta = ref<ZaohuaFurnaceMeta | null>(null)
const furnaces = ref<ZaohuaFurnace[]>([])
const selected = ref<ZaohuaFurnace | null>(null)
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

const { paneHeight, isResizing, startResizing } = useResizablePane({
  initialHeight: 430,
  getAdaptiveHeight: () => Math.floor(Math.max(500, window.innerHeight - 220) * 0.56),
  getResizeBounds: () => ({ min: 250, max: Math.max(340, window.innerHeight - 390) }),
  storageKey: 'zaohua:furnaces:list-pane-height',
})
const listPaneStyle = computed(() => ({ height: `${paneHeight.value}px` }))
const formatPrice = (value: number) => Number.isFinite(value) ? formatChineseCompactNumber(value) : '—'
const elementLabel = (item: ZaohuaFurnace) => item.element_key === 'none' ? '无属性' : `${item.element_name}系`
const gridSizeText = (item: ZaohuaFurnace) => {
  const yang = `${item.yang_grid_size.width}×${item.yang_grid_size.height}`
  const yin = `${item.yin_grid_size.width}×${item.yin_grid_size.height}`
  return yang === yin ? yang : `阳 ${yang} · 阴 ${yin}`
}
const hideBrokenImage = (event: Event) => { (event.currentTarget as HTMLImageElement).style.visibility = 'hidden' }

const toggleSort = (field: 'number' | 'grade') => {
  if (sortBy.value === field) sortOrder.value = sortOrder.value === 'asc' ? 'desc' : 'asc'
  else { sortBy.value = field; sortOrder.value = 'asc' }
  page.value = 1
  void loadFurnaces()
}
const sortMark = (field: 'number' | 'grade') => sortBy.value === field ? (sortOrder.value === 'asc' ? '↑' : '↓') : '↕'
const selectFurnace = async (item: ZaohuaFurnace, updateRoute = true) => {
  selected.value = item
  if (updateRoute) await router.replace({ query: { ...route.query, item_id: String(item.item_id) } })
}
const loadFurnaces = async () => {
  const sequence = ++requestSequence
  loading.value = true
  try {
    const response = await fetchZaohuaFurnaces({ q: query.value.trim(), grade: grade.value, element: element.value, sort_by: sortBy.value, sort_order: sortOrder.value, page: page.value, page_size: pageSize.value })
    if (sequence !== requestSequence) return
    furnaces.value = response.items
    total.value = response.total
    const routeId = Number(route.query.item_id || 0)
    const visible = furnaces.value.find(item => item.item_id === (routeId || selected.value?.item_id || 0))
    if (visible) selected.value = visible
    else if (routeId > 0) {
      try { selected.value = await fetchZaohuaFurnace(routeId) } catch { selected.value = furnaces.value[0] || null }
    } else selected.value = furnaces.value[0] || null
  } finally { if (sequence === requestSequence) loading.value = false }
}

watch(query, () => { window.clearTimeout(searchTimer); searchTimer = window.setTimeout(() => { page.value = 1; void loadFurnaces() }, 250) })
watch([grade, element, pageSize], () => { page.value = 1; void loadFurnaces() })
watch(page, () => void loadFurnaces())
onMounted(async () => { [meta.value] = await Promise.all([fetchZaohuaFurnaceMeta(), loadFurnaces()]) })
onBeforeUnmount(() => window.clearTimeout(searchTimer))
</script>

<template>
  <main class="furnace-page zaohua-catalog-page" :class="{ resizing: isResizing }">
    <header class="page-head"><h1>造化仙缘 · 丹炉</h1><p>丹炉的品阶、属性与炼丹加成。</p></header>
    <section class="toolbar">
      <el-input v-model="query" class="search-input" clearable :prefix-icon="Search" placeholder="搜索丹炉、描述或效果" />
      <el-select v-model="grade" class="filter-select" placeholder="全部品阶" clearable>
        <el-option v-for="item in meta?.grades || []" :key="item.name" :label="`${item.name} · ${item.count}`" :value="item.name" />
      </el-select>
      <el-select v-model="element" class="filter-select" placeholder="全部五行" clearable>
        <el-option v-for="item in meta?.elements || []" :key="item.key" :label="`${item.key === 'none' ? '无属性' : `${item.name}系`} · ${item.count}`" :value="item.key" />
      </el-select>
    </section>
    <section class="list-pane" :style="listPaneStyle" v-loading="loading">
      <div class="table-scroll"><table class="zaohua-catalog-table"><thead><tr>
        <th><button @click="toggleSort('number')">编号 {{ sortMark('number') }}</button></th><th class="zaohua-icon-column">图标</th><th>丹炉</th>
        <th><button @click="toggleSort('grade')">品级 {{ sortMark('grade') }}</button></th><th>五行</th><th>尺寸</th><th>加成</th><th>价格</th><th class="zaohua-fill-column"></th>
      </tr></thead><tbody>
        <tr v-for="item in furnaces" :key="item.item_id" :class="{ selected: selected?.item_id === item.item_id }" @click="selectFurnace(item)">
          <td class="zaohua-number-cell">{{ item.display_order }}</td><td class="zaohua-icon-cell"><img v-if="item.icon_url" :src="item.icon_url" :alt="item.name" @error="hideBrokenImage" /></td>
          <td><GradeMeter class="name-meter" :rank="item.grade_rank" :label="item.name" :title="item.grade_name" /></td><td class="zaohua-number-cell">{{ item.grade_rank }}</td>
          <td>{{ elementLabel(item) }}</td><td>{{ gridSizeText(item) }}</td><td>{{ item.effect_description || '—' }}</td><td class="zaohua-number-cell">{{ formatPrice(item.price) }}</td><td class="zaohua-fill-column"></td>
        </tr></tbody></table><div v-if="!loading && !furnaces.length" class="empty">没有匹配的丹炉</div></div>
      <StandardPagination :page="page" :page-size="pageSize" :total="total" :page-size-options="[20, 40, 80]" align="right" @update:page="value => page = value" @update:page-size="value => pageSize = value" />
    </section>
    <div class="pane-resizer" @mousedown="startResizing"><span></span></div>
    <section v-if="selected" class="detail-pane">
      <header class="detail-head"><img v-if="selected.icon_url" :src="selected.icon_url" :alt="selected.name" @error="hideBrokenImage" /><div><h2>{{ selected.name }}</h2><p>{{ selected.grade_name }} · {{ elementLabel(selected) }}</p></div></header>
      <dl><dt>丹炉尺寸</dt><dd>{{ gridSizeText(selected) }}</dd><dt>加成</dt><dd>{{ selected.effect_description || '—' }}</dd><dt>价格</dt><dd>{{ formatPrice(selected.price) }}</dd><dt>描述</dt><dd>{{ selected.description || '—' }}</dd></dl>
      <details><summary>逆向来源</summary><dl><template v-for="(value, key) in selected.source_evidence" :key="key"><dt>{{ key }}</dt><dd>{{ value }}</dd></template><dt>content_hash</dt><dd>{{ selected.content_hash }}</dd></dl></details>
    </section>
    <section v-else class="detail-empty">选择一种丹炉查看详情</section>
  </main>
</template>

<style scoped>
.furnace-page{display:flex;flex-direction:column;box-sizing:border-box;height:100%;min-height:0;padding:18px 22px 28px;overflow:hidden;color:#272b2f;background:#f6f7f5}.page-head{margin-bottom:14px}.page-head h1,.detail-head h2{margin:0;font-size:22px}.page-head p,.detail-head p{margin:5px 0 0;color:#6c7379}.toolbar{display:flex;gap:10px;margin-bottom:10px}.search-input{width:330px}.filter-select{width:170px}.list-pane{display:flex;flex-direction:column;min-height:250px;overflow:hidden;border:1px solid #d9ddda;background:#fff}.table-scroll{flex:1;min-height:0;overflow:auto}th button{padding:0;border:0;color:inherit;font:inherit;background:none;cursor:pointer}tbody tr{cursor:pointer}tbody tr:hover{background:#f6f8f4}tbody tr.selected{background:#e9f1e7}.detail-head img{width:36px;height:36px;object-fit:contain}.name-meter{width:150px;height:24px}.empty,.detail-empty{padding:28px;color:#8a9094;text-align:center}.pane-resizer{display:flex;height:12px;cursor:row-resize;align-items:center;justify-content:center}.pane-resizer span{width:52px;height:3px;border-radius:2px;background:#c8cdca}.detail-pane{flex:1;min-height:0;padding:16px 18px;overflow:auto;border:1px solid #d9ddda;background:#fff}.detail-head{display:flex;gap:12px;align-items:center}.detail-head h2{font-size:20px}.detail-pane dl{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:9px 18px}.detail-pane dt{color:#737a7e}.detail-pane dd{margin:0}.detail-pane details{margin-top:18px;color:#687076}.detail-pane details dl{font-size:12px;word-break:break-all}@media(max-width:760px){.toolbar{flex-wrap:wrap}.search-input{width:100%}.furnace-page{padding:14px}}
</style>
