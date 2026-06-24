<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { Search } from '@element-plus/icons-vue'
import {
  getMystiaCatalogEntries,
  getMystiaCatalogSummary,
  type MystiaCatalogKind,
  type MystiaCatalogSummary,
} from '@/api/mystia'
import { useResizablePane } from '@/utils/useResizablePane'

type Row = Record<string, any>

interface TabConfig {
  kind: MystiaCatalogKind
  label: string
  idLabel: string
  nameKey: string
  assetMode?: 'item' | 'image' | 'audio'
}

const tabs: TabConfig[] = [
  { kind: 'foods', label: '菜品', idLabel: '菜品ID', nameKey: 'name', assetMode: 'item' },
  { kind: 'recipes', label: '菜谱', idLabel: '菜谱ID', nameKey: 'food_name' },
  { kind: 'ingredients', label: '食材', idLabel: '食材ID', nameKey: 'name', assetMode: 'item' },
  { kind: 'beverages', label: '饮品', idLabel: '饮品ID', nameKey: 'name', assetMode: 'item' },
  { kind: 'special_guests', label: '稀客', idLabel: '角色ID', nameKey: 'name' },
  { kind: 'guests', label: '普通客人', idLabel: '客人ID', nameKey: 'name' },
  { kind: 'images', label: '图片素材', idLabel: '素材', nameKey: 'name', assetMode: 'image' },
  { kind: 'audio', label: '音频素材', idLabel: '音频', nameKey: 'name', assetMode: 'audio' },
]

const activeKind = ref<MystiaCatalogKind>('foods')
const query = ref('')
const loading = ref(false)
const summary = ref<MystiaCatalogSummary | null>(null)
const rows = ref<Row[]>([])
const selectedId = ref<string | number | null>(null)
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

const countText = computed(() => {
  const stats = summary.value?.stats ?? {}
  return [
    `菜品 ${stats.foods ?? 0}`,
    `菜谱 ${stats.recipes ?? 0}`,
    `食材 ${stats.ingredients ?? 0}`,
    `饮品 ${stats.beverages ?? 0}`,
    `稀客 ${stats.special_guests ?? 0}`,
    `图片 ${stats.image_count ?? 0}`,
    `音频 ${stats.audio_count ?? 0}`,
  ].join(' / ')
})

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

function itemIcon(row: Row | null): Row | null {
  if (!row) return null
  return row.assets?.icon ?? null
}

function itemPlate(row: Row | null): Row | null {
  if (!row) return null
  return row.assets?.plate ?? null
}

function previewImage(row: Row | null): Row | null {
  if (!row) return null
  if (activeTab.value.assetMode === 'image') return row
  return itemIcon(row) ?? itemPlate(row)
}

function detailEntries(row: Row | null) {
  if (!row) return []
  const hidden = new Set(['id', 'name', 'food_name', 'description', 'assets', 'url'])
  return Object.entries(row)
    .filter(([key, value]) => !hidden.has(key) && value !== null && value !== undefined && value !== '')
    .map(([key, value]) => [fieldLabel(key), Array.isArray(value) ? compactList(value) : value] as const)
    .filter(([, value]) => value !== '')
}

function fieldLabel(key: string): string {
  const labels: Record<string, string> = {
    level: '等级',
    base_value: activeKind.value === 'ingredients' ? '基础价值' : '基础售价',
    food_base_value: '菜品基础售价',
    cook_time: '烹饪时间',
    fund_multiplier: '资金倍率',
    tag_ids: '标签ID',
    tags: '标签',
    ban_tag_ids: '排除标签ID',
    ban_tags: '排除标签',
    is_collab: '联动',
    ingredients: '食材',
    cooker_type: '厨具类型',
    cooker_name: '厨具',
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
    width: '宽',
    height: '高',
    path: '路径',
    bundle: 'bundle',
  }
  return labels[key] ?? key
}

function valueText(row: Row): string {
  if (activeKind.value === 'audio') return row.group ?? ''
  if (activeKind.value === 'images') return row.kind ?? ''
  return String(row.base_value ?? row.food_base_value ?? row.cook_time ?? row.fund_multiplier ?? '')
}

function valueHeaderText(): string {
  if (activeKind.value === 'foods' || activeKind.value === 'beverages') return '基础售价'
  if (activeKind.value === 'ingredients') return '基础价值'
  if (activeKind.value === 'recipes') return '烹饪时间'
  if (activeKind.value === 'guests') return '资金倍率'
  if (activeKind.value === 'images') return '分类'
  if (activeKind.value === 'audio') return '分组'
  return ''
}

function tagText(row: Row): string {
  if (activeKind.value === 'recipes') return compactList(row.ingredients)
  if (activeKind.value === 'audio') return `${row.format ?? ''} ${Math.round(Number(row.bytes ?? 0) / 1024)}KB`
  if (activeKind.value === 'images') return `${row.width ?? ''}x${row.height ?? ''}`
  return compactList(row.tags ?? row.like_food_tags)
}

async function loadSummary() {
  summary.value = await getMystiaCatalogSummary()
}

async function loadRows() {
  const token = ++requestToken
  loading.value = true
  try {
    const result = await getMystiaCatalogEntries(activeKind.value, query.value)
    if (token !== requestToken) return
    rows.value = result.items
    selectedId.value = rowKey(rows.value[0] ?? {})
  } finally {
    if (token === requestToken) loading.value = false
  }
}

watch(activeKind, () => {
  void loadRows()
})

let queryTimer = 0
watch(query, () => {
  window.clearTimeout(queryTimer)
  queryTimer = window.setTimeout(() => {
    void loadRows()
  }, 200)
})

onMounted(() => {
  void loadSummary()
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

    <div class="wiki-meta">
      <span>{{ countText }}</span>
      <span v-if="summary?.source?.bundle_xor_key !== undefined">bundle XOR {{ summary.source.bundle_xor_key }}</span>
    </div>

    <section class="wiki-body" :class="{ 'is-resizing': isResizing }" v-loading="loading">
      <div class="wiki-list" :style="listPaneStyle">
        <table>
          <colgroup>
            <col class="thumb-col" />
            <col class="id-col" />
            <col class="name-col" />
            <col class="tag-col" />
            <col class="value-col" />
            <col class="fill-col" />
          </colgroup>
          <thead>
            <tr>
              <th class="thumb-head">图</th>
              <th>{{ activeTab.idLabel }}</th>
              <th>名称</th>
              <th>{{ activeKind === 'recipes' ? '食材' : activeKind === 'audio' ? '格式' : '标签' }}</th>
              <th>{{ valueHeaderText() }}</th>
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
              <td class="mono">{{ row.id ?? row.path }}</td>
              <td class="name-cell">{{ rowTitle(row) }}</td>
              <td class="tag-cell">{{ tagText(row) }}</td>
              <td class="mono">{{ valueText(row) }}</td>
              <td class="fill-cell"></td>
            </tr>
          </tbody>
        </table>
      </div>

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
          <img :src="previewImage(selectedRow)?.url" alt="" />
        </div>
        <div v-if="itemIcon(selectedRow) && itemPlate(selectedRow)" class="asset-pair">
          <img :src="itemIcon(selectedRow)?.url" alt="" />
          <img :src="itemPlate(selectedRow)?.url" alt="" />
        </div>
        <audio v-if="activeKind === 'audio' && selectedRow.url" class="audio-player" :src="selectedRow.url" controls />

        <p v-if="selectedRow.description" class="description">{{ selectedRow.description }}</p>
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

.wiki-meta {
  display: flex;
  gap: 14px;
  color: #6b7280;
  font-size: 13px;
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

table {
  border-collapse: collapse;
  width: 100%;
  background: white;
}

col.thumb-col {
  width: 54px;
}

col.id-col,
col.name-col,
col.tag-col,
col.value-col {
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
  width: min(520px, 100%);
  height: 240px;
  display: flex;
  align-items: center;
  justify-content: flex-start;
  background: #f8fafc;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  overflow: hidden;
}

.asset-preview img {
  max-width: 100%;
  max-height: 100%;
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

.audio-player {
  width: min(420px, 100%);
  margin-top: 12px;
}

.description {
  margin: 10px 0 14px;
  line-height: 1.7;
  color: #374151;
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
}

@media (max-width: 900px) {
  .wiki-list {
    min-height: 160px;
  }
}
</style>
