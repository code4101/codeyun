<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useResizablePane } from '@/utils/useResizablePane'
import {
  collectFanxiuGongfaAtlas,
  getFanxiuGongfaAtlas,
  getFanxiuGongfaAtlasBookDetail,
  type FanxiuGongfaAtlasBookDetail,
  type FanxiuGongfaAtlasSnapshot,
} from '@/api/fanxiu'
import FanxiuActivityUpdateButton from '../components/FanxiuActivityUpdateButton.vue'
import FanxiuRenderedText from '../FanxiuRenderedText.vue'

const snapshot = ref<FanxiuGongfaAtlasSnapshot | null>(null)
const loading = ref(false)
const collecting = ref(false)
const keyword = ref('')
const selectedId = ref(0)
const bookDetail = ref<FanxiuGongfaAtlasBookDetail | null>(null)
const detailLoading = ref(false)
let detailRequestId = 0
const atlasRef = ref<HTMLElement | null>(null)

function splitPaneBounds() {
  const containerHeight = atlasRef.value?.clientHeight || Math.max(620, window.innerHeight - 220)
  const availableHeight = Math.max(480, containerHeight - 54)
  return {
    adaptiveHeight: Math.max(220, Math.floor(availableHeight * 0.48)),
    maxHeight: Math.max(260, availableHeight - 260),
  }
}

const {
  paneHeight: listPaneHeight,
  isResizing,
  startResizing,
} = useResizablePane({
  initialHeight: 360,
  getAdaptiveHeight: () => splitPaneBounds().adaptiveHeight,
  getResizeBounds: () => ({
    min: 180,
    max: splitPaneBounds().maxHeight,
  }),
  storageKey: 'fanxiu:wiki:gongfa-list-pane-height',
})

const listPaneStyle = computed(() => ({ height: `${listPaneHeight.value}px` }))

const books = computed(() => snapshot.value?.books || [])
const filteredBooks = computed(() => {
  const query = keyword.value.trim().toLocaleLowerCase()
  return books.value.filter(book => (
    !query || [book.name, book.filter_category, book.quality_type_name, ...book.sub_type_names]
      .some(value => String(value).toLocaleLowerCase().includes(query)))
  )
})
const selected = computed(() => books.value.find(book => book.book_id === selectedId.value) || filteredBooks.value[0] || null)
const channelGroups = computed(() => {
  const groups = new Map<string, FanxiuGongfaAtlasBookDetail['acquisition_channels']>()
  for (const channel of bookDetail.value?.acquisition_channels || []) {
    const rows = groups.get(channel.kind) || []
    rows.push(channel)
    groups.set(channel.kind, rows)
  }
  return [...groups.entries()].map(([kind, channels]) => ({ kind, channels }))
})
const updatedAt = computed(() => {
  const value = Number(snapshot.value?.runtime_updated_at || 0)
  return value ? new Date(value * 1000).toLocaleString('zh-CN', { hour12: false }) : '尚未更新'
})

function selectFirst() {
  if (!filteredBooks.value.some(book => book.book_id === selectedId.value)) {
    selectedId.value = filteredBooks.value[0]?.book_id || 0
  }
}

function gongfaRowStyle({ row }: { row: FanxiuGongfaAtlasSnapshot['books'][number] }) {
  return row.quality_grade_color
    ? { '--gongfa-grade-color': row.quality_grade_color }
    : undefined
}

async function load() {
  loading.value = true
  try {
    snapshot.value = await getFanxiuGongfaAtlas()
    selectFirst()
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '读取个人功法失败')
  } finally {
    loading.value = false
  }
}

async function collect() {
  collecting.value = true
  try {
    snapshot.value = await collectFanxiuGongfaAtlas()
    selectFirst()
    await loadBookDetail(selectedId.value)
    ElMessage.success(`已按当前搭配更新 ${snapshot.value.runtime_item_count} 本功法顺序`)
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '更新个人功法失败')
  } finally {
    collecting.value = false
  }
}

async function loadBookDetail(bookId: number) {
  const requestId = ++detailRequestId
  if (!bookId) {
    bookDetail.value = null
    return
  }
  bookDetail.value = null
  detailLoading.value = true
  try {
    const result = await getFanxiuGongfaAtlasBookDetail(bookId)
    if (requestId === detailRequestId) bookDetail.value = result
  } catch {
    if (requestId === detailRequestId) bookDetail.value = null
  } finally {
    if (requestId === detailRequestId) detailLoading.value = false
  }
}

watch(selectedId, loadBookDetail)

onMounted(load)
</script>

<template>
  <section ref="atlasRef" class="atlas-page" v-loading="loading">
    <header class="toolbar">
      <div class="heading">
        <h1>个人功法</h1>
        <span>{{ snapshot?.summary?.learned_count || 0 }} 本</span>
        <span>未满 {{ snapshot?.summary?.upgradeable_count || 0 }}</span>
        <span>已悟境 {{ snapshot?.summary?.wujing_count || 0 }}</span>
        <span>已通玄 {{ snapshot?.summary?.tongxuan_count || 0 }}</span>
        <span>{{ updatedAt }}</span>
      </div>
      <div class="actions">
        <el-input v-model="keyword" clearable size="small" placeholder="搜索功法" @input="selectFirst" />
        <FanxiuActivityUpdateButton :visible="true" :loading="collecting" :disabled="loading" @collect="collect" />
      </div>
    </header>

    <div class="body" :class="{ 'is-resizing': isResizing }">
      <div class="list-pane" :style="listPaneStyle">
        <el-table
          v-if="filteredBooks.length"
          class="books-table"
          :data="filteredBooks"
          height="100%"
          size="small"
          row-key="book_id"
          table-layout="auto"
          :fit="false"
          :row-style="gongfaRowStyle"
          highlight-current-row
          @row-click="selectedId = $event.book_id"
        >
          <el-table-column prop="upgrade_index" label="编号" width="58" align="right" />
          <el-table-column prop="name" label="功法" min-width="160" />
          <el-table-column prop="quality_grade_name" label="品级" width="68" />
          <el-table-column prop="filter_category" label="体系" width="78" />
          <el-table-column
            label="层数"
            width="96"
            align="right"
            class-name="layer-column"
            label-class-name="layer-column"
          >
            <template #default="{ row }">{{ row.max_grade ? `${row.grade} / ${row.max_grade}` : row.grade }}</template>
          </el-table-column>
          <el-table-column label="融合" width="92" align="right">
            <template #default="{ row }">{{ row.jie }} / {{ row.max_jie || '?' }}</template>
          </el-table-column>
          <el-table-column label="悟境" width="72" align="right">
            <template #default="{ row }">{{ row.max_wujing ? `${row.wujing}/${row.max_wujing}` : '—' }}</template>
          </el-table-column>
          <el-table-column label="通玄" width="72" align="right">
            <template #default="{ row }">{{ row.max_tongxuan ? `${row.tongxuan}/${row.max_tongxuan}` : '—' }}</template>
          </el-table-column>
          <el-table-column
            width="24"
            :resizable="false"
            class-name="table-end-gutter"
            label-class-name="table-end-gutter"
          />
        </el-table>
        <el-empty v-else description="没有符合条件的功法" :image-size="72" />
      </div>

      <div
        class="pane-resizer"
        role="separator"
        aria-orientation="horizontal"
        title="拖动调整功法清单和详情的比例"
        @mousedown="startResizing"
      >
        <span></span>
      </div>

      <main class="detail-pane" v-loading="detailLoading">
        <template v-if="selected">
          <div class="detail-heading">
            <h2>{{ selected.name }}</h2>
            <strong class="quality-grade" :style="{ color: selected.quality_grade_color }">{{ selected.quality_grade_name }}</strong>
            <span>{{ selected.filter_category }}</span>
            <a :href="selected.catalog_href" target="_blank" rel="noopener">完整静态图鉴</a>
          </div>
          <section v-if="bookDetail?.usages.length" class="detail-section">
            <h3>搭配使用</h3>
            <div class="usage-list">
              <article v-for="(usage, index) in bookDetail.usages" :key="`${usage.category}-${usage.slot}-${usage.role}-${index}`">
                <div class="usage-heading">
                  <strong>{{ usage.location_name }}</strong>
                  <span>{{ usage.category_name }}第 {{ usage.slot }} 栏 · {{ usage.role_name }}</span>
                </div>
                <FanxiuRenderedText
                  v-if="usage.effect_rich_text || usage.effect_text"
                  class="usage-effect"
                  :value="usage.effect_rich_text || usage.effect_text"
                  tone="light"
                  compact
                  preserve-colors
                  :enable-links="false"
                />
              </article>
            </div>
          </section>
          <section v-if="channelGroups.length" class="detail-section">
            <h3>获取渠道</h3>
            <div class="channel-groups">
              <div v-for="group in channelGroups" :key="group.kind" class="channel-group">
                <h4>{{ group.kind }}</h4>
                <ul>
                  <li v-for="(channel, index) in group.channels" :key="`${channel.source}-${channel.item_id}-${channel.level}-${index}`">
                    <strong>{{ channel.source }} · {{ channel.title }}</strong>
                    <span>{{ channel.detail }}</span>
                  </li>
                </ul>
              </div>
            </div>
          </section>
        </template>
        <el-empty v-else description="选择一本功法查看详情" :image-size="72" />
      </main>
    </div>
  </section>
</template>

<style scoped>
.atlas-page { box-sizing: border-box; height: 100%; min-height: 0; display: flex; flex-direction: column; overflow: hidden; }
.toolbar { min-height: 54px; display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 8px 14px; border-bottom: 1px solid var(--el-border-color-light); }
.heading, .actions, .detail-heading, .usage-heading { display: flex; align-items: center; gap: 10px; }
.heading h1, .detail-heading h2, h3, p { margin: 0; }
.heading h1 { font-size: 18px; white-space: nowrap; }
.heading span, .detail-heading span, .usage-heading span { color: var(--el-text-color-secondary); font-size: 12px; white-space: nowrap; }
.actions :deep(.el-input) { width: 170px; }
.body { flex: 1; min-height: 0; display: flex; flex-direction: column; overflow: hidden; }
.list-pane, .detail-pane { min-height: 0; padding: 10px 14px; }
.list-pane { flex: none; overflow: hidden; }
.books-table { width: max-content; max-width: 100%; }
.books-table :deep(.el-table__body tr > td.el-table__cell) { color: var(--gongfa-grade-color, var(--el-text-color-regular)); }
.books-table :deep(.layer-column .cell) { white-space: nowrap; }
.books-table :deep(.table-end-gutter .cell) { padding: 0; }
.quality-grade { font-size: 12px; font-weight: 600; white-space: nowrap; }
.pane-resizer { flex: none; height: 12px; display: flex; align-items: center; justify-content: center; cursor: ns-resize; touch-action: none; border-top: 1px solid var(--el-border-color-light); border-bottom: 1px solid var(--el-border-color-light); }
.pane-resizer span { width: 48px; height: 4px; border-top: 1px solid var(--el-border-color); border-bottom: 1px solid var(--el-border-color); }
.pane-resizer:hover, .body.is-resizing .pane-resizer { background: var(--el-color-primary-light-9); }
.pane-resizer:hover span, .body.is-resizing .pane-resizer span { border-color: var(--el-color-primary); }
.detail-pane { flex: 1; overflow: auto; }
.detail-heading { align-items: baseline; margin-bottom: 14px; }
.detail-heading h2 { font-size: 20px; }
.detail-heading a { color: var(--el-color-primary); font-size: 12px; text-decoration: none; }
section { padding: 16px 0; border-bottom: 1px solid var(--el-border-color-lighter); }
section h3 { color: #6f4b16; font-size: 14px; }
section p { color: var(--el-text-color-regular); line-height: 1.7; }
.detail-section { padding-top: 10px; }
.detail-section h3 { margin-bottom: 8px; }
.usage-list article { padding: 9px 0; border-top: 1px solid var(--el-border-color-extra-light); }
.usage-list article:first-child { border-top: 0; }
.usage-heading strong { font-size: 14px; }
.usage-effect { margin-top: 5px; font-size: 13px; line-height: 1.6; }
.channel-groups { display: grid; gap: 12px; }
.channel-group { display: grid; grid-template-columns: 48px minmax(0, 1fr); gap: 12px; }
.channel-group h4 { margin: 2px 0 0; color: var(--el-text-color-regular); font-size: 13px; }
.channel-group ul { margin: 0; padding: 0; list-style: none; }
.channel-group li { display: flex; align-items: baseline; gap: 10px; padding: 6px 0; border-top: 1px solid var(--el-border-color-extra-light); }
.channel-group li:first-child { border-top: 0; padding-top: 0; }
.channel-group li strong { flex: none; font-size: 13px; font-weight: 600; }
.channel-group li span { min-width: 0; color: var(--el-text-color-secondary); font-size: 12px; line-height: 1.5; }
@media (max-width: 980px) { .toolbar { align-items: stretch; flex-direction: column; } }
</style>
