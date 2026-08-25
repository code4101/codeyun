<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import StandardPagination from '@/components/StandardPagination.vue'
import { useResizablePane } from '@/utils/useResizablePane'
import {
  getFanxiuGuideVideos,
  syncFanxiuGuideVideos,
  type FanxiuGuideVideoCatalogResponse,
  type FanxiuGuideVideoItem,
} from '@/api/fanxiu'

const catalog = ref<FanxiuGuideVideoCatalogResponse | null>(null)
const keyword = ref('')
const sourceId = ref('')
const page = ref(1)
const pageSize = ref(20)
const selectedItemId = ref('')
const loading = ref(false)
const catalogRef = ref<HTMLElement | null>(null)
const researchVideoRef = ref<HTMLVideoElement | null>(null)
let pollTimer: number | null = null

function splitPaneBounds() {
  const containerHeight = catalogRef.value?.clientHeight || Math.max(620, window.innerHeight - 220)
  const availableHeight = Math.max(460, containerHeight - 54)
  return {
    adaptiveHeight: Math.max(250, Math.floor(availableHeight * 0.58)),
    maxHeight: Math.max(300, availableHeight - 190),
  }
}

const {
  paneHeight: listPaneHeight,
  isResizing,
  startResizing,
} = useResizablePane({
  initialHeight: 420,
  getAdaptiveHeight: () => splitPaneBounds().adaptiveHeight,
  getResizeBounds: () => ({ min: 220, max: splitPaneBounds().maxHeight }),
  storageKey: 'fanxiu:wiki:guide-video-list-pane-height',
})

const listPaneStyle = computed(() => ({ height: `${listPaneHeight.value}px` }))
const items = computed(() => catalog.value?.items || [])
const sources = computed(() => catalog.value?.sources || [])
const selectedSource = computed(() => (
  sources.value.find(source => source.source_id === sourceId.value) || null
))
const selected = computed(() => (
  items.value.find(item => item.item_id === selectedItemId.value) || items.value[0] || null
))
const selectedResearch = computed(() => selected.value?.research || null)
const syncing = computed(() => catalog.value?.status === 'running')
const downloading = computed(() => catalog.value?.download_status === 'running')
const updatedAt = computed(() => {
  const timestamp = Number(catalog.value?.updated_at || 0)
  return timestamp ? formatDateTime(timestamp) : '尚未同步'
})

function formatDateTime(timestamp: number) {
  if (!timestamp) return '—'
  return new Date(timestamp * 1000).toLocaleString('zh-CN', { hour12: false })
}

function selectFirst() {
  if (!items.value.some(item => item.item_id === selectedItemId.value)) {
    selectedItemId.value = items.value[0]?.item_id || ''
  }
}

async function load(options: { quiet?: boolean } = {}) {
  if (!options.quiet) loading.value = true
  try {
    catalog.value = await getFanxiuGuideVideos({
      query: keyword.value.trim(),
      source_id: sourceId.value,
      page: page.value,
      page_size: pageSize.value,
    })
    selectFirst()
    if (catalog.value.status === 'running' || catalog.value.download_status === 'running') startPolling()
    else stopPolling()
  } catch (error: any) {
    stopPolling()
    ElMessage.error(error?.response?.data?.detail || error?.message || '读取攻略视频失败')
  } finally {
    if (!options.quiet) loading.value = false
  }
}

function search() {
  page.value = 1
  void load()
}

function changeSource() {
  page.value = 1
  void load()
}

function changePage(nextPage: number) {
  page.value = nextPage
  void load()
}

function changePageSize(nextPageSize: number) {
  pageSize.value = nextPageSize
  page.value = 1
  void load()
}

async function syncCatalog() {
  try {
    catalog.value = await syncFanxiuGuideVideos()
    startPolling()
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '启动攻略视频同步失败')
  }
}

function startPolling() {
  if (pollTimer !== null) return
  pollTimer = window.setInterval(async () => {
    await load({ quiet: true })
    if (catalog.value?.status === 'done' && !downloading.value) {
      ElMessage.success(`已同步 ${catalog.value.done_count} 条攻略视频`)
    } else if (catalog.value?.status === 'error') {
      ElMessage.error(catalog.value.error || '攻略视频同步失败')
    }
  }, 2000)
}

function platformLabel(platform: string) {
  return platform === 'douyin' ? '抖音' : 'B站'
}

function roleLabel(role: string) {
  return ({ official: '官方', original: '原创攻略', guide: '攻略作者', clip: '授权切片' } as Record<string, string>)[role] || role
}

function seekResearchVideo(seconds: number) {
  if (!researchVideoRef.value) return
  researchVideoRef.value.currentTime = Math.max(Number(seconds || 0), 0)
  void researchVideoRef.value.play()
}

function stopPolling() {
  if (pollTimer === null) return
  window.clearInterval(pollTimer)
  pollTimer = null
}

onMounted(load)
onBeforeUnmount(stopPolling)
</script>

<template>
  <section ref="catalogRef" class="guide-catalog">
    <header class="guide-toolbar">
      <span class="source-status">
        {{ sources.length }} 个来源
        <template v-if="catalog?.done_count"> · {{ catalog.done_count }} 条</template>
        <template v-if="catalog?.research_count"> · 已研究 {{ catalog.research_count }} 条</template>
        <template v-if="catalog?.download_target_count">
          · 已下载 {{ catalog.download_done_count }}/{{ catalog.download_target_count }}
          <template v-if="catalog.download_failed_count"> · 失败 {{ catalog.download_failed_count }}</template>
          <template v-if="downloading"> · 串行下载中</template>
        </template>
        · {{ updatedAt }}
        <template v-if="syncing"> · 正在同步 {{ catalog?.done_count || 0 }}</template>
      </span>
      <div class="guide-actions">
        <el-select v-model="sourceId" size="small" placeholder="全部来源" clearable @change="changeSource">
          <el-option
            v-for="source in sources"
            :key="source.source_id"
            :label="`${source.uploader_name} · ${platformLabel(source.platform)}`"
            :value="source.source_id"
          />
        </el-select>
        <el-input
          v-model="keyword"
          clearable
          size="small"
          placeholder="搜索标题、作者或视频号"
          :prefix-icon="Search"
          @keyup.enter="search"
          @clear="search"
        />
        <el-button size="small" type="primary" :loading="syncing" @click="syncCatalog">同步来源</el-button>
      </div>
    </header>

    <nav v-if="selectedSource?.collections?.length" class="collection-links" aria-label="作者合集">
      <a
        v-for="collection in selectedSource.collections"
        :key="collection.collection_id"
        :href="collection.url"
        target="_blank"
        rel="noopener"
      >{{ collection.title }}<span v-if="collection.episode_count"> · {{ collection.episode_count }}集</span></a>
    </nav>

    <el-alert
      v-if="catalog?.status === 'error' && catalog.error"
      class="sync-error"
      type="error"
      :title="catalog.error"
      :closable="false"
      show-icon
    />

    <div class="guide-body" :class="{ 'is-resizing': isResizing }">
      <div class="list-pane" :style="listPaneStyle">
        <el-table
          v-if="items.length"
          v-loading="loading"
          class="video-table"
          :data="items"
          height="calc(100% - 42px)"
          size="small"
          row-key="item_id"
          table-layout="auto"
          :fit="false"
          highlight-current-row
          :current-row-key="selected?.item_id"
          @row-click="(row: FanxiuGuideVideoItem) => selectedItemId = row.item_id"
        >
          <el-table-column label="来源" width="180" show-overflow-tooltip>
            <template #default="{ row }">{{ row.uploader_name }}</template>
          </el-table-column>
          <el-table-column label="渠道" width="66">
            <template #default="{ row }">{{ platformLabel(row.platform) }}</template>
          </el-table-column>
          <el-table-column label="发布时间" width="168">
            <template #default="{ row }">{{ formatDateTime(row.published_at) }}</template>
          </el-table-column>
          <el-table-column label="标题" min-width="360" show-overflow-tooltip>
            <template #default="{ row }">
              <el-tag v-if="row.is_pinned" class="row-tag" size="small" type="warning" effect="plain">置顶</el-tag>
              <el-tag v-if="row.download?.status === 'done'" class="row-tag" size="small" type="info" effect="plain">已下载</el-tag>
              <el-tag v-if="row.research?.status === 'done'" class="row-tag" size="small" type="success" effect="plain">已研究</el-tag>
              <a :href="row.url" target="_blank" rel="noopener" @click.stop>{{ row.title }}</a>
            </template>
          </el-table-column>
          <el-table-column prop="duration_text" label="时长" width="72" />
          <el-table-column prop="play_text" label="播放" width="78" align="right" />
          <el-table-column prop="video_id" label="视频号" width="150" />
          <el-table-column width="24" class-name="table-tail-space" label-class-name="table-tail-space" />
        </el-table>
        <el-empty v-else v-loading="loading" description="尚无匹配攻略视频" :image-size="72" />
        <StandardPagination
          :page="page"
          :page-size="pageSize"
          :total="catalog?.total || 0"
          :disabled="loading"
          @page-change="changePage"
          @page-size-change="changePageSize"
        />
      </div>

      <div
        class="pane-resizer"
        role="separator"
        aria-orientation="horizontal"
        title="拖动调整列表和详情的比例"
        @mousedown="startResizing"
      ><span /></div>

      <main class="detail-pane">
        <template v-if="selected">
          <div class="detail-heading">
            <h2>{{ selected.title }}</h2>
            <span>{{ selected.uploader_name }} · {{ platformLabel(selected.platform) }} · {{ roleLabel(selected.source_role) }}</span>
            <span>{{ selected.duration_text || '—' }}</span>
            <span>{{ formatDateTime(selected.published_at) }}</span>
          </div>
          <a class="video-url" :href="selected.url" target="_blank" rel="noopener">{{ selected.url }}</a>
          <p v-if="selected.description" class="description">{{ selected.description }}</p>
          <section v-if="selectedResearch?.status === 'done'" class="research-card">
            <video
              ref="researchVideoRef"
              class="research-video"
              :src="selectedResearch.media_url"
              controls
              preload="metadata"
            />
            <div class="research-heading">
              <h3>攻略研究</h3>
              <a v-if="selectedResearch.transcript_url" :href="selectedResearch.transcript_url" target="_blank" rel="noopener">转录稿</a>
              <a v-if="selectedResearch.document_url" :href="selectedResearch.document_url" target="_blank" rel="noopener">完整文档</a>
            </div>
            <p class="research-summary">{{ selectedResearch.summary }}</p>
            <div v-if="selectedResearch.topics?.length" class="research-topics">
              <el-tag v-for="topic in selectedResearch.topics" :key="topic" size="small" effect="plain">{{ topic }}</el-tag>
            </div>
            <ul v-if="selectedResearch.conclusions?.length" class="research-conclusions">
              <li v-for="conclusion in selectedResearch.conclusions" :key="conclusion">{{ conclusion }}</li>
            </ul>
            <ol v-if="selectedResearch.timeline?.length" class="research-timeline">
              <li v-for="item in selectedResearch.timeline" :key="`${item.time}-${item.label}`">
                <button type="button" @click="seekResearchVideo(item.time)">{{ item.label }}</button>
                <span>{{ item.description }}</span>
              </li>
            </ol>
            <p v-if="selectedResearch.version_note" class="version-note">{{ selectedResearch.version_note }}</p>
          </section>
        </template>
        <el-empty v-else description="选择一条攻略视频查看详情" :image-size="64" />
      </main>
    </div>
  </section>
</template>

<style scoped>
.guide-catalog { box-sizing: border-box; height: 100%; min-height: 0; display: flex; flex-direction: column; overflow: hidden; }
.guide-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 16px; min-height: 44px; }
.source-status { color: var(--el-text-color-secondary); font-size: 13px; white-space: nowrap; }
.guide-actions { display: flex; align-items: center; gap: 8px; }
.guide-actions .el-select { width: 220px; }
.guide-actions .el-input { width: 280px; }
.collection-links { display: flex; flex-wrap: wrap; gap: 6px 16px; padding: 2px 0 8px; font-size: 13px; }
.collection-links a { color: var(--el-color-primary); text-decoration: none; }
.collection-links a:hover { text-decoration: underline; }
.collection-links span { color: var(--el-text-color-secondary); }
.sync-error { margin-bottom: 8px; }
.guide-body { min-height: 0; flex: 1; display: flex; flex-direction: column; }
.guide-body.is-resizing { user-select: none; }
.list-pane { min-height: 220px; overflow: hidden; }
.video-table { width: max-content; max-width: 100%; }
.video-table a, .video-url { color: var(--el-color-primary); text-decoration: none; }
.video-table a:hover, .video-url:hover { text-decoration: underline; }
.pane-resizer { height: 9px; flex: 0 0 9px; cursor: row-resize; display: grid; place-items: center; }
.pane-resizer span { width: 56px; height: 2px; border-radius: 2px; background: var(--el-border-color); }
.detail-pane { min-height: 0; flex: 1; overflow: auto; padding: 12px 2px 0; }
.detail-heading { display: flex; align-items: baseline; gap: 12px; }
.detail-heading h2 { margin: 0; font-size: 18px; }
.detail-heading span { color: var(--el-text-color-secondary); font-size: 12px; }
.video-url { display: inline-block; margin-top: 8px; font-size: 13px; }
.description { max-width: 900px; margin: 12px 0 0; white-space: pre-wrap; line-height: 1.7; }
.row-tag { margin-right: 5px; vertical-align: 1px; }
.research-card { max-width: 980px; margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--el-border-color-lighter); }
.research-video { display: block; width: min(100%, 430px); max-height: 62vh; margin: 0 auto 16px; border-radius: 12px; background: #0b0f15; }
.research-heading { display: flex; align-items: baseline; gap: 14px; }
.research-heading h3 { margin: 0; font-size: 17px; }
.research-heading a { color: var(--el-color-primary); font-size: 13px; text-decoration: none; }
.research-summary { margin: 10px 0; line-height: 1.7; }
.research-topics { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
.research-conclusions { margin: 8px 0; padding-left: 22px; line-height: 1.7; }
.research-timeline { padding: 0; list-style: none; }
.research-timeline li { display: flex; gap: 10px; align-items: baseline; margin: 7px 0; }
.research-timeline button { flex: 0 0 auto; border: 0; border-radius: 999px; padding: 4px 9px; color: #075985; background: #e0f2fe; cursor: pointer; }
.version-note { color: var(--el-text-color-secondary); font-size: 12px; }
:deep(.table-tail-space .cell) { padding: 0; }

@media (max-width: 1200px) {
  .guide-toolbar { align-items: stretch; flex-direction: column; gap: 6px; padding-bottom: 4px; }
  .source-status { overflow: hidden; text-overflow: ellipsis; }
  .guide-actions { width: 100%; }
  .guide-actions .el-select { width: 190px; }
  .guide-actions .el-input { min-width: 180px; width: auto; flex: 1; }
}
</style>
