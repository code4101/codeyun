<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { Search, VideoPause, VideoPlay } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'

import {
  getVolcanoPrincessAudioEntries,
  getVolcanoPrincessAudioEntry,
  getVolcanoPrincessAudioMeta,
  type VolcanoPrincessAudioCategory,
  type VolcanoPrincessAudioEntry,
  type VolcanoPrincessAudioMeta,
  type VolcanoPrincessAudioSort,
} from '@/api/volcanoPrincess'
import StandardPagination from '@/components/StandardPagination.vue'
import { useResizablePane } from '@/utils/useResizablePane'


const route = useRoute()
const router = useRouter()

const CATEGORY_LABELS: Record<VolcanoPrincessAudioCategory, string> = {
  music_or_ambience: '长音乐 / 环境',
  voice_or_effect: '语音 / 音效',
  short_clip: '短片段',
}
const CATEGORY_OPTIONS = Object.entries(CATEGORY_LABELS).map(([value, label]) => ({
  value: value as VolcanoPrincessAudioCategory,
  label,
}))
const CATEGORY_VALUES = new Set(CATEGORY_OPTIONS.map((item) => item.value))
const SORT_VALUES = new Set<VolcanoPrincessAudioSort>(['path_id', 'name', 'duration'])

function routeText(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function routePositiveInt(value: unknown, fallback: number): number {
  const parsed = Number.parseInt(routeText(value), 10)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback
}

const query = ref(routeText(route.query.q))
const initialCategory = routeText(route.query.category) as VolcanoPrincessAudioCategory
const category = ref<VolcanoPrincessAudioCategory | ''>(
  CATEGORY_VALUES.has(initialCategory) ? initialCategory : '',
)
const initialSort = routeText(route.query.sort) as VolcanoPrincessAudioSort
const sortBy = ref<VolcanoPrincessAudioSort>(SORT_VALUES.has(initialSort) ? initialSort : 'path_id')
const sortOrder = ref<'asc' | 'desc'>(routeText(route.query.order) === 'desc' ? 'desc' : 'asc')
const page = ref(routePositiveInt(route.query.page, 1))
const pageSize = ref(routePositiveInt(route.query.page_size, 50))
const selectedPathId = ref(routePositiveInt(route.query.id, 0))

const loading = ref(false)
const meta = ref<VolcanoPrincessAudioMeta | null>(null)
const rows = ref<VolcanoPrincessAudioEntry[]>([])
const total = ref(0)
const selectedEntry = ref<VolcanoPrincessAudioEntry | null>(null)
const detailAudioRef = ref<HTMLAudioElement | null>(null)
const playingPathId = ref<number | null>(null)
const playbackLoadingPathId = ref<number | null>(null)
let requestSequence = 0
let queryTimer = 0

const { paneHeight, isResizing, startResizing } = useResizablePane({
  storageKey: 'volcano-princess:audio:list-pane-height',
  getAdaptiveHeight: () => Math.round(window.innerHeight * 0.42),
  getResizeBounds: () => ({ min: 190, max: Math.max(190, window.innerHeight - 390) }),
})
const listPaneStyle = computed(() => ({ height: `${paneHeight.value}px` }))

function syncRouteQuery() {
  void router.replace({
    path: route.path,
    query: {
      ...(query.value.trim() ? { q: query.value.trim() } : {}),
      ...(category.value ? { category: category.value } : {}),
      ...(sortBy.value !== 'path_id' ? { sort: sortBy.value } : {}),
      ...(sortOrder.value !== 'asc' ? { order: sortOrder.value } : {}),
      ...(page.value > 1 ? { page: String(page.value) } : {}),
      ...(pageSize.value !== 50 ? { page_size: String(pageSize.value) } : {}),
      ...(selectedPathId.value > 0 ? { id: String(selectedPathId.value) } : {}),
    },
  })
}

async function resolveSelection(items: VolcanoPrincessAudioEntry[]) {
  const current = items.find((item) => item.path_id === selectedPathId.value)
  if (current) {
    selectedEntry.value = current
    return
  }
  if (selectedPathId.value > 0) {
    try {
      selectedEntry.value = await getVolcanoPrincessAudioEntry(selectedPathId.value)
      return
    } catch {
      selectedPathId.value = 0
    }
  }
  selectedEntry.value = items[0] ?? null
  selectedPathId.value = selectedEntry.value?.path_id ?? 0
}

async function loadRows() {
  const sequence = ++requestSequence
  loading.value = true
  try {
    const response = await getVolcanoPrincessAudioEntries({
      q: query.value.trim(),
      category: category.value,
      sortBy: sortBy.value,
      sortOrder: sortOrder.value,
      page: page.value,
      pageSize: pageSize.value,
    })
    if (sequence !== requestSequence) return
    rows.value = response.items
    total.value = response.total
    if (page.value !== response.page) page.value = response.page
    await resolveSelection(response.items)
    syncRouteQuery()
  } catch (error) {
    if (sequence !== requestSequence) return
    rows.value = []
    total.value = 0
    selectedEntry.value = null
    ElMessage.error(error instanceof Error ? error.message : '音频图鉴加载失败')
  } finally {
    if (sequence === requestSequence) loading.value = false
  }
}

async function loadMeta() {
  try {
    meta.value = await getVolcanoPrincessAudioMeta()
  } catch {
    meta.value = null
  }
}

function stopPlayback() {
  detailAudioRef.value?.pause()
  playingPathId.value = null
  playbackLoadingPathId.value = null
}

function selectEntry(entry: VolcanoPrincessAudioEntry) {
  if (selectedEntry.value?.path_id !== entry.path_id) stopPlayback()
  selectedEntry.value = entry
  selectedPathId.value = entry.path_id
  syncRouteQuery()
}

async function togglePlayback(entry: VolcanoPrincessAudioEntry) {
  if (selectedEntry.value?.path_id === entry.path_id && detailAudioRef.value) {
    if (!detailAudioRef.value.paused) {
      detailAudioRef.value.pause()
      return
    }
  } else {
    stopPlayback()
    selectedEntry.value = entry
    selectedPathId.value = entry.path_id
    syncRouteQuery()
    await nextTick()
  }

  const audio = detailAudioRef.value
  if (!audio) return

  playbackLoadingPathId.value = entry.path_id
  try {
    await audio.play()
  } catch {
    ElMessage.error('音频播放失败')
  } finally {
    playbackLoadingPathId.value = null
  }
}

function handleAudioPlay() {
  playingPathId.value = selectedEntry.value?.path_id ?? null
}

function handleAudioPause() {
  playingPathId.value = null
}

function changePage(value: number) {
  page.value = value
  syncRouteQuery()
  void loadRows()
}

function changePageSize(value: number) {
  pageSize.value = value
  page.value = 1
  syncRouteQuery()
  void loadRows()
}

function changeCategory() {
  stopPlayback()
  page.value = 1
  selectedPathId.value = 0
  syncRouteQuery()
  void loadRows()
}

function toggleSort(nextSort: VolcanoPrincessAudioSort) {
  if (sortBy.value === nextSort) {
    sortOrder.value = sortOrder.value === 'asc' ? 'desc' : 'asc'
  } else {
    sortBy.value = nextSort
    sortOrder.value = 'asc'
  }
  page.value = 1
  syncRouteQuery()
  void loadRows()
}

function sortMark(key: VolcanoPrincessAudioSort): string {
  if (sortBy.value !== key) return ''
  return sortOrder.value === 'asc' ? '↑' : '↓'
}

function formatDuration(seconds: number): string {
  const totalSeconds = Math.max(0, Math.round(seconds))
  const minutes = Math.floor(totalSeconds / 60)
  const remainingSeconds = totalSeconds % 60
  return `${String(minutes).padStart(2, '0')}:${String(remainingSeconds).padStart(2, '0')}`
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

watch(query, () => {
  window.clearTimeout(queryTimer)
  queryTimer = window.setTimeout(() => {
    stopPlayback()
    page.value = 1
    selectedPathId.value = 0
    syncRouteQuery()
    void loadRows()
  }, 250)
})

onMounted(() => {
  void loadMeta()
  void loadRows()
})
</script>

<template>
  <main class="audio-catalog-page">
    <header class="page-head">
      <div>
        <h1>火山的女儿 · 音频图鉴</h1>
        <p v-if="meta?.source.build_id">
          Steam Build {{ meta.source.build_id }} · {{ meta.source.engine }}
        </p>
      </div>
    </header>

    <section class="catalog-toolbar">
      <el-input
        v-model="query"
        class="search-input"
        clearable
        :prefix-icon="Search"
        placeholder="搜索名称或对象 ID"
      />
      <el-select v-model="category" class="category-select" @change="changeCategory">
        <el-option value="" label="全部分类" />
        <el-option
          v-for="option in CATEGORY_OPTIONS"
          :key="option.value"
          :value="option.value"
          :label="option.label"
        />
      </el-select>
    </section>

    <section class="catalog-body" :class="{ 'is-resizing': isResizing }" v-loading="loading">
      <div class="audio-list" :style="listPaneStyle">
        <table>
          <colgroup>
            <col class="id-col">
            <col class="name-col">
            <col class="category-col">
            <col class="duration-col">
            <col class="playback-col">
            <col class="fill-col">
          </colgroup>
          <thead>
            <tr>
              <th><button type="button" @click="toggleSort('path_id')">对象 ID <span>{{ sortMark('path_id') }}</span></button></th>
              <th><button type="button" @click="toggleSort('name')">名称 <span>{{ sortMark('name') }}</span></button></th>
              <th>分组</th>
              <th><button type="button" @click="toggleSort('duration')">时长 <span>{{ sortMark('duration') }}</span></button></th>
              <th class="playback-head">播放</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="entry in rows"
              :key="entry.path_id"
              :class="{ selected: selectedEntry?.path_id === entry.path_id }"
              @click="selectEntry(entry)"
            >
              <td class="mono">{{ entry.path_id }}</td>
              <td class="entry-name">{{ entry.name }}</td>
              <td><span class="category-label">{{ CATEGORY_LABELS[entry.category] }}</span></td>
              <td class="mono">{{ formatDuration(entry.duration_seconds) }}</td>
              <td class="playback-cell">
                <button
                  type="button"
                  class="inline-play-button"
                  :class="{ active: playingPathId === entry.path_id }"
                  :disabled="playbackLoadingPathId === entry.path_id"
                  :title="playingPathId === entry.path_id ? '暂停' : '播放'"
                  :aria-label="`${playingPathId === entry.path_id ? '暂停' : '播放'} ${entry.name}`"
                  @click.stop="togglePlayback(entry)"
                >
                  <VideoPause v-if="playingPathId === entry.path_id" />
                  <VideoPlay v-else />
                </button>
              </td>
              <td></td>
            </tr>
            <tr v-if="!rows.length && !loading">
              <td class="empty-row" colspan="6">没有符合条件的音频</td>
            </tr>
          </tbody>
        </table>
      </div>

      <StandardPagination
        v-if="total > 0"
        class="catalog-pagination"
        :page="page"
        :page-size="pageSize"
        :total="total"
        :page-size-options="[20, 50, 100, 200]"
        align="right"
        @update:page="changePage"
        @update:page-size="changePageSize"
      />

      <div
        class="catalog-resizer"
        role="separator"
        aria-orientation="horizontal"
        title="拖动调整列表和详情的比例"
        @mousedown="startResizing"
      ><span></span></div>

      <aside v-if="selectedEntry" class="audio-detail">
        <div class="detail-heading">
          <div>
            <span class="detail-id">AudioClip #{{ selectedEntry.path_id }}</span>
            <h2>{{ selectedEntry.name }}</h2>
          </div>
          <span class="category-label">{{ CATEGORY_LABELS[selectedEntry.category] }}</span>
        </div>

        <audio
          :key="selectedEntry.path_id"
          ref="detailAudioRef"
          class="audio-player"
          :src="selectedEntry.media_url"
          controls
          preload="metadata"
          @play="handleAudioPlay"
          @pause="handleAudioPause"
          @ended="handleAudioPause"
        />

        <dl class="detail-fields">
          <div><dt>时长</dt><dd>{{ formatDuration(selectedEntry.duration_seconds) }}</dd></div>
          <div><dt>声道</dt><dd>{{ selectedEntry.channels }}</dd></div>
          <div><dt>采样率</dt><dd>{{ selectedEntry.frequency_hz.toLocaleString() }} Hz</dd></div>
          <div><dt>预览大小</dt><dd>{{ formatBytes(selectedEntry.media_bytes) }}</dd></div>
        </dl>

        <details class="source-evidence">
          <summary>逆向来源</summary>
          <dl>
            <div><dt>源资产</dt><dd>{{ selectedEntry.source_asset }}</dd></div>
            <div><dt>对象键</dt><dd>{{ selectedEntry.id }}</dd></div>
            <div><dt>媒体 SHA-256</dt><dd class="hash">{{ selectedEntry.media_sha256 }}</dd></div>
          </dl>
        </details>
      </aside>
      <div v-else class="detail-empty">选择一条音频查看并试听</div>
    </section>
  </main>
</template>

<style scoped>
.audio-catalog-page {
  box-sizing: border-box;
  height: 100%;
  min-height: 0;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  overflow: hidden;
  color: #202124;
}

.page-head {
  flex: none;
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
}

.page-head h1 {
  margin: 0;
  font-size: 22px;
  line-height: 1.35;
}

.page-head p {
  margin: 3px 0 0;
  color: #6b7280;
  font-size: 13px;
}

.catalog-toolbar {
  flex: none;
  display: flex;
  align-items: center;
  gap: 10px;
}

.search-input {
  width: 310px;
}

.category-select {
  width: 170px;
}

.catalog-body {
  min-height: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
}

.catalog-body.is-resizing {
  cursor: row-resize;
}

.audio-list {
  flex: none;
  max-width: 100%;
  overflow: auto;
  border: 1px solid #d8dde6;
  border-radius: 6px;
  background: #fff;
}

table {
  width: 100%;
  border-collapse: collapse;
  background: #fff;
}

col.id-col,
col.category-col,
col.duration-col {
  width: 1%;
}

col.playback-col {
  width: 54px;
}

col.name-col {
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
  white-space: nowrap;
}

th {
  position: sticky;
  top: 0;
  z-index: 1;
  background: #f7f8fa;
  font-weight: 600;
}

th button {
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  cursor: pointer;
}

th button span {
  display: inline-block;
  min-width: 12px;
  color: #2563eb;
}

tbody tr:not(:last-child) {
  cursor: pointer;
}

tbody tr:hover,
tbody tr.selected {
  background: #eef5ff;
}

.entry-name {
  max-width: 340px;
  overflow: hidden;
  text-overflow: ellipsis;
  font-weight: 500;
}

.mono {
  font-variant-numeric: tabular-nums;
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
}

.playback-head,
.playback-cell {
  padding-right: 8px;
  padding-left: 8px;
  text-align: center;
}

.inline-play-button {
  width: 28px;
  height: 28px;
  padding: 6px;
  display: inline-grid;
  place-items: center;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: #5d6673;
  cursor: pointer;
}

.inline-play-button:hover {
  border-color: #b8cff7;
  background: #eaf2ff;
  color: #2563eb;
}

.inline-play-button.active {
  border-color: #93b4ee;
  background: #dceaff;
  color: #1d4ed8;
}

.inline-play-button:disabled {
  cursor: wait;
  opacity: 0.55;
}

.inline-play-button svg {
  width: 15px;
  height: 15px;
}

.category-label {
  display: inline-block;
  padding: 2px 7px;
  border-radius: 999px;
  background: #f1f3f5;
  color: #4b5563;
  font-size: 12px;
  white-space: nowrap;
}

.empty-row {
  height: 80px;
  color: #8a919e;
  text-align: center;
}

.catalog-pagination {
  flex: none;
  padding: 8px 0;
}

.catalog-resizer {
  flex: none;
  height: 11px;
  display: grid;
  place-items: center;
  cursor: row-resize;
}

.catalog-resizer span {
  width: 48px;
  height: 3px;
  border-radius: 999px;
  background: #d7dce3;
}

.catalog-resizer:hover span {
  background: #9ca8b8;
}

.audio-detail {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 10px 2px 18px;
}

.detail-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
  max-width: 760px;
}

.detail-id {
  color: #7b8491;
  font: 12px/1.4 ui-monospace, SFMono-Regular, Consolas, monospace;
}

.detail-heading h2 {
  margin: 2px 0 0;
  font-size: 19px;
  line-height: 1.4;
}

.audio-player {
  display: block;
  width: min(620px, 100%);
  margin: 16px 0;
}

.detail-fields,
.source-evidence dl {
  display: flex;
  flex-wrap: wrap;
  gap: 12px 28px;
  margin: 0;
}

.detail-fields div,
.source-evidence dl div {
  display: grid;
  gap: 2px;
}

dt {
  color: #7b8491;
  font-size: 12px;
}

dd {
  margin: 0;
  font-size: 13px;
}

.source-evidence {
  margin-top: 18px;
  max-width: 900px;
  color: #4b5563;
}

.source-evidence summary {
  width: max-content;
  cursor: pointer;
  font-size: 13px;
}

.source-evidence dl {
  margin-top: 10px;
}

.hash {
  overflow-wrap: anywhere;
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
}

.detail-empty {
  flex: 1;
  display: grid;
  place-items: center;
  color: #8a919e;
}

@media (max-width: 720px) {
  .catalog-toolbar {
    align-items: stretch;
    flex-direction: column;
  }

  .search-input,
  .category-select {
    width: 100%;
  }

  .entry-name {
    max-width: 220px;
  }
}
</style>
