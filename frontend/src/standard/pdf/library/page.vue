<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Grid, List, Plus, Search } from '@element-plus/icons-vue'

import {
  fetchPdfDocuments,
  importPdfDocumentFromLocalPath,
  updatePdfBookshelfLayout,
  type PdfBookshelfOrientation,
  type PdfBookshelfPlacement,
  type PdfDocumentSummary,
  type PdfResourceRole,
} from '@/api/pdfDocuments'
import { useUserStore } from '@/store/userStore'

type PdfFilter = 'all' | 'mine' | 'other'
type PdfViewMode = 'bookshelf' | 'list'

interface BookContextMenuState {
  pdfId: number
  x: number
  y: number
}

const PDF_LIBRARY_VIEW_MODE_KEY = 'codeyun.pdf-library.view-mode'
const BOOK_PAGE_SCALE = 0.32
const MIN_BOOK_HEIGHT = 190
const MAX_BOOK_HEIGHT = 286
const MIN_SPINE_WIDTH = 24
const MAX_SPINE_WIDTH = 84
const BOOK_THICKNESS_SCALE = 2
const MIN_SPINE_FONT_SIZE = 12
const MAX_SPINE_FONT_SIZE = 24
const BOOK_ORIENTATION_CYCLE: PdfBookshelfOrientation[] = [
  'spine_vertical',
  'spine_horizontal',
  'cover_front',
]

const router = useRouter()
const userStore = useUserStore()

const loading = ref(false)
const documents = ref<PdfDocumentSummary[]>([])
const searchText = ref('')
const pdfFilter = ref<PdfFilter>('all')
const viewMode = ref<PdfViewMode>('bookshelf')
const draggingPdfId = ref<number | null>(null)
const dragOverPdfId = ref<number | null>(null)
const dragOverShelfIndex = ref<number | null>(null)
const bookContextMenu = ref<BookContextMenuState | null>(null)
let titleRefreshTimer: ReturnType<typeof setTimeout> | null = null
let layoutSaveQueue = Promise.resolve()
let pointerDragPdfId: number | null = null
let pointerStartX = 0
let pointerStartY = 0
let pointerMoved = false
let suppressNextBookClick = false

const currentUserId = computed(() => userStore.user?.id ?? null)
const normalizedSearchText = computed(() => searchText.value.trim().toLowerCase())

const filteredDocuments = computed(() => {
  const query = normalizedSearchText.value
  return documents.value.filter((document) => {
    const isMine = document.owner_user_id != null && document.owner_user_id === currentUserId.value
    if (pdfFilter.value === 'mine' && !isMine) {
      return false
    }
    if (pdfFilter.value === 'other' && isMine) {
      return false
    }
    if (!query) {
      return true
    }
    return document.display_title.toLowerCase().includes(query)
      || document.title.toLowerCase().includes(query)
      || String(document.id).includes(query)
      || document.source_device_id.toLowerCase().includes(query)
      || document.source_absolute_path.toLowerCase().includes(query)
      || accessRoleLabel(document.access?.role).toLowerCase().includes(query)
  })
})

const filteredDocumentIds = computed(() => new Set(filteredDocuments.value.map((document) => document.id)))

const bookshelfRows = computed(() => {
  return buildBookshelfRows().map((row) => row.filter((document) => filteredDocumentIds.value.has(document.id)))
})

const filterOptions: Array<{ value: PdfFilter; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'mine', label: '我创建的' },
  { value: 'other', label: '其他可访问' },
]

function accessRoleLabel(role?: PdfResourceRole | null) {
  switch (role) {
    case 'manager':
      return '可管理'
    case 'editor':
      return '可编辑'
    case 'viewer':
      return '只读'
    case 'deny':
      return '无权限'
    default:
      return '未知'
  }
}

function formatDateTime(timestamp?: number | null) {
  if (!timestamp) {
    return '-'
  }
  const date = new Date(timestamp * 1000)
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  const hour = String(date.getHours()).padStart(2, '0')
  const minute = String(date.getMinutes()).padStart(2, '0')
  return `${year}-${month}-${day} ${hour}:${minute}`
}

function formatFileSize(sizeBytes?: number | null) {
  if (!sizeBytes || sizeBytes <= 0) {
    return '-'
  }
  const units = ['B', 'KB', 'MB', 'GB']
  let value = sizeBytes
  let unitIndex = 0
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024
    unitIndex += 1
  }
  const precision = value >= 10 || unitIndex === 0 ? 0 : 1
  return `${value.toFixed(precision)} ${units[unitIndex]}`
}

function formatCurrentPage(document: PdfDocumentSummary) {
  const page = document.my_state?.current_page
  return page && page >= 1 ? `第 ${page} 页` : '-'
}

function resolvePdfHref(pdfId: number) {
  return router.resolve({ path: `/pdf/${pdfId}` }).href
}

function setViewMode(mode: PdfViewMode) {
  viewMode.value = mode
  localStorage.setItem(PDF_LIBRARY_VIEW_MODE_KEY, mode)
}

function restoreViewMode() {
  const savedMode = localStorage.getItem(PDF_LIBRARY_VIEW_MODE_KEY)
  if (savedMode === 'bookshelf' || savedMode === 'list') {
    viewMode.value = savedMode
  }
}

function spineTone(document: PdfDocumentSummary) {
  return `tone-${Math.abs(document.id) % 6}`
}

function bookOrientation(document: PdfDocumentSummary): PdfBookshelfOrientation {
  return document.bookshelf_placement?.orientation ?? 'spine_vertical'
}

function bookOrientationClass(document: PdfDocumentSummary) {
  return `orientation-${bookOrientation(document).replace('_', '-')}`
}

function bookSpineStyle(document: PdfDocumentSummary) {
  const metadata = document.metadata
  const pageHeight = metadata?.status === 'ready' && metadata.page_height_points
    ? metadata.page_height_points
    : 792
  const pageWidth = metadata?.status === 'ready' && metadata.page_width_points
    ? metadata.page_width_points
    : 612
  const pageCount = metadata?.status === 'ready' && metadata.page_count
    ? metadata.page_count
    : 400
  const bookHeight = Math.min(MAX_BOOK_HEIGHT, Math.max(MIN_BOOK_HEIGHT, Math.round(pageHeight * BOOK_PAGE_SCALE)))
  const screenScale = bookHeight / pageHeight
  const paperBlockMillimeters = 2 + pageCount * 0.05
  const paperBlockPoints = paperBlockMillimeters * 72 / 25.4
  const baseSpineWidth = Math.min(
    MAX_SPINE_WIDTH,
    Math.max(MIN_SPINE_WIDTH, Math.round(paperBlockPoints * screenScale)),
  )
  const spineWidth = baseSpineWidth * BOOK_THICKNESS_SCALE
  const widthRatio = (baseSpineWidth - MIN_SPINE_WIDTH) / (MAX_SPINE_WIDTH - MIN_SPINE_WIDTH)
  const heightRatio = (bookHeight - MIN_BOOK_HEIGHT) / (MAX_BOOK_HEIGHT - MIN_BOOK_HEIGHT)
  const sizeRatio = Math.min(1, Math.max(0, widthRatio * 0.75 + heightRatio * 0.25))
  const titleFontSize = Math.round(
    MIN_SPINE_FONT_SIZE + (MAX_SPINE_FONT_SIZE - MIN_SPINE_FONT_SIZE) * sizeRatio,
  )
  const pageDepth = Math.min(210, Math.max(120, Math.round(pageWidth * screenScale)))
  const orientation = bookOrientation(document)
  const itemWidth = orientation === 'spine_vertical' ? spineWidth : pageDepth
  const coverFontSize = Math.min(28, Math.max(16, Math.round(Math.min(pageDepth / 6, bookHeight / 10))))
  return {
    '--spine-width': `${spineWidth}px`,
    '--spine-height': `${bookHeight}px`,
    '--spine-font-size': `${titleFontSize}px`,
    '--page-depth': `${pageDepth}px`,
    '--book-item-width': `${itemWidth}px`,
    '--cover-font-size': `${coverFontSize}px`,
  }
}

function bookTooltip(document: PdfDocumentSummary) {
  const metadata = document.metadata
  const fields = [
    `书名：${document.display_title}`,
    `原文件：${document.title}`,
  ]
  if (metadata?.status === 'ready') {
    fields.push(`页数：${metadata.page_count} 页`)
    fields.push(`页面尺寸：${metadata.page_width_points} × ${metadata.page_height_points} pt`)
  } else {
    fields.push('页面信息：暂不可用')
  }
  fields.push(`阅读位置：${formatCurrentPage(document)}`)
  return fields.join('\n')
}

function buildBookshelfRows() {
  const originalOrder = new Map(documents.value.map((document, index) => [document.id, index]))
  const maxShelfIndex = documents.value.reduce(
    (maximum, document) => Math.max(maximum, document.bookshelf_placement?.shelf_index ?? 0),
    0,
  )
  const rowCount = Math.max(4, maxShelfIndex + 2)
  const rows = Array.from({ length: rowCount }, () => [] as PdfDocumentSummary[])
  const orderedDocuments = [...documents.value].sort((left, right) => {
    const leftShelf = left.bookshelf_placement?.shelf_index ?? 0
    const rightShelf = right.bookshelf_placement?.shelf_index ?? 0
    if (leftShelf !== rightShelf) {
      return leftShelf - rightShelf
    }
    const leftPosition = left.bookshelf_placement?.position_index ?? originalOrder.get(left.id) ?? 0
    const rightPosition = right.bookshelf_placement?.position_index ?? originalOrder.get(right.id) ?? 0
    return leftPosition - rightPosition
  })
  for (const document of orderedDocuments) {
    const shelfIndex = Math.max(0, document.bookshelf_placement?.shelf_index ?? 0)
    while (rows.length <= shelfIndex) {
      rows.push([])
    }
    rows[shelfIndex].push(document)
  }
  return rows
}

function clearBookDragState() {
  draggingPdfId.value = null
  dragOverPdfId.value = null
  dragOverShelfIndex.value = null
}

function closeBookContextMenu() {
  bookContextMenu.value = null
}

function openBookContextMenu(event: MouseEvent, pdfId: number) {
  event.preventDefault()
  event.stopPropagation()
  const menuWidth = 112
  const menuHeight = 42
  bookContextMenu.value = {
    pdfId,
    x: Math.max(8, Math.min(event.clientX, window.innerWidth - menuWidth - 8)),
    y: Math.max(8, Math.min(event.clientY, window.innerHeight - menuHeight - 8)),
  }
}

function handleContextMenuKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    closeBookContextMenu()
  }
}

function nextBookOrientation(orientation: PdfBookshelfOrientation): PdfBookshelfOrientation {
  const currentIndex = BOOK_ORIENTATION_CYCLE.indexOf(orientation)
  return BOOK_ORIENTATION_CYCLE[(currentIndex + 1) % BOOK_ORIENTATION_CYCLE.length] ?? 'spine_vertical'
}

function applyBookshelfRows(
  rows: PdfDocumentSummary[][],
  orientationOverrides: ReadonlyMap<number, PdfBookshelfOrientation> = new Map(),
) {
  const placements: PdfBookshelfPlacement[] = []
  for (const [rowIndex, row] of rows.entries()) {
    for (const [positionIndex, document] of row.entries()) {
      const placement: PdfBookshelfPlacement = {
        pdf_id: document.id,
        shelf_index: rowIndex,
        position_index: positionIndex,
        orientation: orientationOverrides.get(document.id) ?? bookOrientation(document),
      }
      document.bookshelf_placement = placement
      placements.push(placement)
    }
  }
  documents.value = [...documents.value]
  queueBookshelfLayoutSave(placements)
}

function rotateContextBook() {
  const pdfId = bookContextMenu.value?.pdfId
  if (pdfId == null) {
    return
  }
  const document = documents.value.find((item) => item.id === pdfId)
  if (!document) {
    closeBookContextMenu()
    return
  }
  const orientation = nextBookOrientation(bookOrientation(document))
  closeBookContextMenu()
  applyBookshelfRows(buildBookshelfRows(), new Map([[pdfId, orientation]]))
}

function handleBookDragOver(pdfId: number, shelfIndex: number) {
  if (draggingPdfId.value == null || draggingPdfId.value === pdfId) {
    return
  }
  dragOverPdfId.value = pdfId
  dragOverShelfIndex.value = shelfIndex
}

function handleBookPointerDown(event: PointerEvent, pdfId: number) {
  if (event.button !== 0) {
    return
  }
  pointerDragPdfId = pdfId
  pointerStartX = event.clientX
  pointerStartY = event.clientY
  pointerMoved = false
  window.addEventListener('pointermove', handleBookPointerMove, { passive: false })
  window.addEventListener('pointerup', handleBookPointerUp, { once: true })
  window.addEventListener('pointercancel', handleBookPointerCancel, { once: true })
}

function handleBookPointerMove(event: PointerEvent) {
  if (pointerDragPdfId == null) {
    return
  }
  if (!pointerMoved && Math.hypot(event.clientX - pointerStartX, event.clientY - pointerStartY) < 6) {
    return
  }
  pointerMoved = true
  draggingPdfId.value = pointerDragPdfId
  event.preventDefault()
  const target = document.elementFromPoint(event.clientX, event.clientY)
  const targetBook = target?.closest<HTMLElement>('.book-item')
  const targetShelf = target?.closest<HTMLElement>('.bookshelf-row')
  const shelfIndex = Number(targetShelf?.dataset.shelfIndex)
  if (!Number.isInteger(shelfIndex) || shelfIndex < 0) {
    dragOverPdfId.value = null
    dragOverShelfIndex.value = null
    return
  }
  const targetPdfId = Number(targetBook?.dataset.pdfId)
  if (Number.isInteger(targetPdfId) && targetPdfId !== pointerDragPdfId) {
    handleBookDragOver(targetPdfId, shelfIndex)
  } else {
    handleShelfDragOver(shelfIndex)
  }
}

function handleBookPointerUp(event: PointerEvent) {
  window.removeEventListener('pointermove', handleBookPointerMove)
  window.removeEventListener('pointercancel', handleBookPointerCancel)
  if (pointerMoved) {
    event.preventDefault()
    suppressNextBookClick = true
    if (dragOverShelfIndex.value != null) {
      const shelfIndex = dragOverShelfIndex.value
      const beforePdfId = dragOverPdfId.value
      moveBookToShelf(shelfIndex, beforePdfId)
    } else {
      clearBookDragState()
    }
    setTimeout(() => {
      suppressNextBookClick = false
    }, 0)
  } else {
    clearBookDragState()
  }
  pointerDragPdfId = null
  pointerMoved = false
}

function handleBookPointerCancel() {
  window.removeEventListener('pointermove', handleBookPointerMove)
  window.removeEventListener('pointerup', handleBookPointerUp)
  pointerDragPdfId = null
  pointerMoved = false
  clearBookDragState()
}

function handleBookClick(event: MouseEvent) {
  if (suppressNextBookClick) {
    event.preventDefault()
  }
}

function handleShelfDragOver(shelfIndex: number) {
  if (draggingPdfId.value == null) {
    return
  }
  dragOverPdfId.value = null
  dragOverShelfIndex.value = shelfIndex
}

function moveBookToShelf(shelfIndex: number, beforePdfId: number | null = null) {
  const pdfId = draggingPdfId.value
  if (pdfId == null) {
    return
  }
  const rows = buildBookshelfRows()
  let movingDocument: PdfDocumentSummary | null = null
  for (const row of rows) {
    const documentIndex = row.findIndex((document) => document.id === pdfId)
    if (documentIndex >= 0) {
      movingDocument = row.splice(documentIndex, 1)[0] ?? null
      break
    }
  }
  if (!movingDocument) {
    clearBookDragState()
    return
  }
  while (rows.length <= shelfIndex) {
    rows.push([])
  }
  const targetRow = rows[shelfIndex]
  const targetIndex = beforePdfId == null
    ? targetRow.length
    : Math.max(0, targetRow.findIndex((document) => document.id === beforePdfId))
  targetRow.splice(targetIndex, 0, movingDocument)

  clearBookDragState()
  applyBookshelfRows(rows)
}

function queueBookshelfLayoutSave(placements: PdfBookshelfPlacement[]) {
  layoutSaveQueue = layoutSaveQueue.then(async () => {
    try {
      await updatePdfBookshelfLayout(placements)
    } catch (error) {
      console.warn('Failed to save PDF bookshelf layout:', error)
      ElMessage.error('保存书柜位置失败，已恢复服务器布局')
      await reloadDocuments({ silent: true })
    }
  })
}

function scheduleTitleRefresh() {
  if (titleRefreshTimer) {
    clearTimeout(titleRefreshTimer)
    titleRefreshTimer = null
  }
  if (!documents.value.some((document) => document.display_title_status === 'pending')) {
    return
  }
  titleRefreshTimer = setTimeout(() => {
    titleRefreshTimer = null
    void reloadDocuments({ silent: true })
  }, 4000)
}

async function reloadDocuments(options: { silent?: boolean } = {}) {
  if (!options.silent) {
    loading.value = true
  }
  try {
    documents.value = await fetchPdfDocuments()
    scheduleTitleRefresh()
  } catch (error) {
    console.warn('Failed to load PDF documents:', error)
    if (!options.silent) {
      ElMessage.error('加载馆藏失败')
    }
  } finally {
    if (!options.silent) {
      loading.value = false
    }
  }
}

async function handleImportLocalPdf() {
  try {
    const { value } = await ElMessageBox.prompt('请输入本机 PDF 绝对路径', '导入本机 PDF', {
      inputValue: '',
      confirmButtonText: '导入',
      cancelButtonText: '取消',
      inputValidator: (inputValue) => inputValue.trim().toLowerCase().endsWith('.pdf') ? true : '请输入 PDF 文件路径',
    })
    await importPdfDocumentFromLocalPath({ absolute_path: value.trim() })
    await reloadDocuments()
    ElMessage.success('PDF 已导入')
  } catch (error) {
    if (error === 'cancel' || error === 'close') {
      return
    }
    console.warn('Failed to import PDF document:', error)
    ElMessage.error('导入 PDF 失败')
  }
}

async function initializeLibraryPage() {
  if (userStore.isAuthenticated && !userStore.user && !userStore.loading) {
    await userStore.fetchUserProfile()
  }
  await reloadDocuments()
}

onMounted(() => {
  restoreViewMode()
  window.addEventListener('pointerdown', closeBookContextMenu)
  window.addEventListener('keydown', handleContextMenuKeydown)
  void initializeLibraryPage()
})

onBeforeUnmount(() => {
  if (titleRefreshTimer) {
    clearTimeout(titleRefreshTimer)
  }
  window.removeEventListener('pointermove', handleBookPointerMove)
  window.removeEventListener('pointerup', handleBookPointerUp)
  window.removeEventListener('pointercancel', handleBookPointerCancel)
  window.removeEventListener('pointerdown', closeBookContextMenu)
  window.removeEventListener('keydown', handleContextMenuKeydown)
})
</script>

<template>
  <div class="pdf-library-page" v-loading="loading">
    <header class="library-header">
      <div class="library-heading">
        <h1>图书馆</h1>
      </div>

      <div class="library-actions">
        <el-input
          v-model="searchText"
          class="library-search"
          :prefix-icon="Search"
          clearable
          placeholder="搜索图书"
        />
        <button
          v-for="option in filterOptions"
          :key="option.value"
          type="button"
          class="filter-button"
          :class="{ active: pdfFilter === option.value }"
          @click="pdfFilter = option.value"
        >
          {{ option.label }}
        </button>
        <div class="view-switch" role="group" aria-label="视图方式">
          <button
            type="button"
            class="view-switch-button"
            :class="{ active: viewMode === 'bookshelf' }"
            title="书柜视图"
            aria-label="书柜视图"
            :aria-pressed="viewMode === 'bookshelf'"
            @click="setViewMode('bookshelf')"
          >
            <el-icon><Grid /></el-icon>
          </button>
          <button
            type="button"
            class="view-switch-button"
            :class="{ active: viewMode === 'list' }"
            title="清单视图"
            aria-label="清单视图"
            :aria-pressed="viewMode === 'list'"
            @click="setViewMode('list')"
          >
            <el-icon><List /></el-icon>
          </button>
        </div>
        <el-button type="primary" :icon="Plus" @click="handleImportLocalPdf">导入本机 PDF</el-button>
      </div>
    </header>

    <section class="pdf-library-content" aria-label="图书馆藏书">
      <div
        v-if="filteredDocuments.length && viewMode === 'bookshelf'"
        class="bookshelf-scroll"
        @scroll="closeBookContextMenu"
      >
        <div class="bookshelf-grid">
          <div
            v-for="(row, shelfIndex) in bookshelfRows"
            :key="shelfIndex"
            class="bookshelf-row"
            :data-shelf-index="shelfIndex"
            :class="{ 'drag-target': dragOverShelfIndex === shelfIndex && dragOverPdfId == null }"
          >
            <a
              v-for="document in row"
              :key="document.id"
              class="book-item"
              :class="[
                bookOrientationClass(document),
                {
                  'insert-before': dragOverPdfId === document.id,
                  dragging: draggingPdfId === document.id,
                },
              ]"
              :href="resolvePdfHref(document.id)"
              target="_blank"
              rel="noopener noreferrer"
              draggable="false"
              :data-pdf-id="document.id"
              :style="bookSpineStyle(document)"
              :title="bookTooltip(document)"
              @pointerdown="handleBookPointerDown($event, document.id)"
              @contextmenu="openBookContextMenu($event, document.id)"
              @click="handleBookClick"
            >
              <span class="book-spine" :class="spineTone(document)">
                <span class="book-spine-title">{{ document.display_title }}</span>
              </span>
            </a>
          </div>
        </div>
      </div>

      <div v-else-if="filteredDocuments.length" class="pdf-table-scroll">
        <table class="pdf-table-inner">
          <thead>
            <tr>
              <th scope="col">藏书</th>
              <th scope="col">权限</th>
              <th scope="col">阅读位置</th>
              <th scope="col">大小</th>
              <th scope="col">更新时间</th>
              <th scope="col" class="pdf-spacer-cell" aria-hidden="true"></th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="document in filteredDocuments"
              :key="document.id"
              class="pdf-row"
            >
              <td class="pdf-name-cell">
                <a
                  class="pdf-title-button"
                  :href="resolvePdfHref(document.id)"
                  target="_blank"
                  rel="noopener noreferrer"
                  :title="document.source_absolute_path || document.title"
                >
                  <span class="pdf-subtitle">#{{ document.id }}</span>
                  <span class="pdf-title">{{ document.display_title }}</span>
                </a>
              </td>
              <td class="pdf-role">{{ accessRoleLabel(document.access?.role) }}</td>
              <td class="pdf-current-page">{{ formatCurrentPage(document) }}</td>
              <td class="pdf-size">{{ formatFileSize(document.size_bytes) }}</td>
              <td class="pdf-updated">{{ formatDateTime(document.updated_at) }}</td>
              <td class="pdf-spacer-cell" aria-hidden="true"></td>
            </tr>
          </tbody>
        </table>
      </div>

      <el-empty
        v-else
        class="pdf-empty"
        :description="documents.length ? '没有匹配的图书' : '暂无藏书'"
      />

      <div
        v-if="bookContextMenu"
        class="book-context-menu"
        role="menu"
        :style="{ left: `${bookContextMenu.x}px`, top: `${bookContextMenu.y}px` }"
        @pointerdown.stop
        @contextmenu.prevent
      >
        <button type="button" role="menuitem" @click="rotateContextBook">旋转</button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.pdf-library-page {
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  padding: 16px 18px;
  background: #f8fafc;
  overflow: hidden;
  gap: 14px;
}

.library-header {
  flex: 0 0 auto;
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
}

.library-heading {
  display: grid;
  gap: 4px;
  min-width: 160px;
}

.library-heading h1 {
  margin: 0;
  color: #172033;
  font-size: 22px;
  font-weight: 700;
  line-height: 30px;
}

.library-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 8px;
  min-width: 0;
}

.library-search {
  width: 240px;
}

.filter-button {
  height: 32px;
  border: 1px solid #d8e0ea;
  border-radius: 6px;
  background: #fff;
  padding: 0 12px;
  color: #526071;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}

.filter-button:hover {
  border-color: #9ab9ee;
  color: #2f6fd6;
}

.filter-button.active {
  border-color: #2f6fd6;
  background: #edf4ff;
  color: #1f5fbe;
}

.pdf-library-content {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid #dfe7f0;
  border-radius: 8px;
  background: #fff;
  overflow: hidden;
}

.view-switch {
  display: inline-flex;
  height: 32px;
  border: 1px solid #d8e0ea;
  border-radius: 6px;
  background: #fff;
  overflow: hidden;
}

.view-switch-button {
  display: inline-grid;
  place-items: center;
  width: 34px;
  border: 0;
  background: transparent;
  color: #68768a;
  font-size: 16px;
  cursor: pointer;
}

.view-switch-button + .view-switch-button {
  border-left: 1px solid #e1e7ef;
}

.view-switch-button:hover {
  color: #2f6fd6;
}

.view-switch-button.active {
  background: #edf4ff;
  color: #1f5fbe;
}

.bookshelf-scroll {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
}

.bookshelf-grid {
  box-sizing: border-box;
  display: grid;
  width: max-content;
  min-width: 100%;
  min-height: 100%;
  padding: 0 24px 12px;
  background-color: #ece8e1;
  background-image: linear-gradient(90deg, rgb(83 69 53 / 8%), transparent 7%, transparent 93%, rgb(83 69 53 / 8%));
  box-shadow: inset 14px 0 20px rgb(72 58 44 / 5%), inset -14px 0 20px rgb(72 58 44 / 5%);
}

.bookshelf-row {
  box-sizing: border-box;
  display: flex;
  align-items: flex-end;
  gap: 3px;
  width: max-content;
  min-width: 100%;
  height: 312px;
  padding: 0 0 12px;
  background-image: linear-gradient(
    to bottom,
    transparent 0,
    transparent 294px,
    #d6cec2 294px,
    #c2b5a3 300px,
    #9e8c76 303px,
    transparent 306px
  );
  transition: background-color 120ms ease;
}

.bookshelf-row.drag-target {
  background-color: rgb(47 111 214 / 6%);
}

.book-item {
  box-sizing: border-box;
  display: flex;
  align-items: flex-end;
  width: var(--book-item-width, var(--spine-width));
  height: 300px;
  color: inherit;
  text-decoration: none;
  cursor: grab;
  touch-action: none;
  user-select: none;
}

.book-item.dragging {
  opacity: 0.72;
}

.book-item:active {
  cursor: grabbing;
}

.book-item.insert-before {
  border-left: 3px solid #2f6fd6;
}

.book-spine {
  position: relative;
  box-sizing: border-box;
  display: flex;
  align-items: center;
  width: 100%;
  height: var(--spine-height);
  padding: 10px 5px 8px;
  border: 1px solid rgb(26 31 37 / 15%);
  border-radius: 3px 3px 1px 1px;
  background: #3d6383;
  box-shadow:
    inset 3px 0 4px rgb(255 255 255 / 10%),
    inset -3px 0 4px rgb(15 24 32 / 18%),
    2px 2px 3px rgb(42 35 28 / 18%);
  color: #fff;
  transform-origin: bottom center;
  transition: filter 120ms ease, transform 120ms ease;
}

.book-item:hover .book-spine,
.book-item:focus-visible .book-spine {
  filter: brightness(1.08);
  transform: translateY(-5px);
}

.book-item:focus-visible {
  outline: none;
}

.book-item:focus-visible .book-spine {
  outline: 2px solid #2f6fd6;
  outline-offset: 2px;
}

.book-spine-title {
  max-height: 100%;
  color: rgb(255 255 255 / 92%);
  font-size: var(--spine-font-size, 12px);
  font-weight: 700;
  line-height: 1.35;
  overflow: hidden;
  text-overflow: clip;
  writing-mode: vertical-rl;
  text-orientation: upright;
}

.book-item.orientation-spine-horizontal .book-spine {
  width: var(--page-depth);
  height: var(--spine-width);
  padding: 7px 12px;
  border-radius: 3px 2px 2px 3px;
  box-shadow:
    inset 0 -5px 4px rgb(20 24 28 / 16%),
    inset 0 3px 3px rgb(255 255 255 / 10%),
    2px 2px 3px rgb(42 35 28 / 18%);
}

.book-item.orientation-spine-horizontal .book-spine-title {
  width: 100%;
  max-height: none;
  overflow: hidden;
  text-align: center;
  text-overflow: ellipsis;
  white-space: nowrap;
  writing-mode: horizontal-tb;
}

.book-item.orientation-cover-front .book-spine {
  justify-content: center;
  width: var(--page-depth);
  height: var(--spine-height);
  padding: 20px 16px;
  border-width: 2px 1px 2px 5px;
  border-radius: 5px 2px 2px 5px;
  box-shadow:
    inset 7px 0 8px rgb(18 22 27 / 15%),
    inset 0 0 0 2px rgb(255 255 255 / 10%),
    3px 3px 5px rgb(42 35 28 / 22%);
}

.book-item.orientation-cover-front .book-spine-title {
  width: 100%;
  max-height: 100%;
  color: rgb(255 255 255 / 94%);
  font-size: var(--cover-font-size, 18px);
  line-height: 1.45;
  overflow: hidden;
  text-align: center;
  writing-mode: horizontal-tb;
}

.book-context-menu {
  position: fixed;
  z-index: 3000;
  box-sizing: border-box;
  width: 112px;
  padding: 4px;
  border: 1px solid #d9e0e8;
  border-radius: 6px;
  background: #fff;
  box-shadow: 0 6px 18px rgb(31 42 55 / 18%);
}

.book-context-menu button {
  width: 100%;
  height: 32px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  padding: 0 10px;
  color: #273447;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
}

.book-context-menu button:hover,
.book-context-menu button:focus-visible {
  background: #edf4ff;
  color: #1f5fbe;
  outline: none;
}

.tone-0 { background: #315c78; }
.tone-1 { background: #705b46; }
.tone-2 { background: #41685b; }
.tone-3 { background: #665579; }
.tone-4 { background: #87554f; }
.tone-5 { background: #485f86; }

.pdf-table-scroll {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
}

.pdf-table-inner {
  width: 100%;
  border-collapse: collapse;
  table-layout: auto;
}

.pdf-table-inner th,
.pdf-table-inner td {
  box-sizing: border-box;
  padding: 0 14px;
  text-align: left;
  vertical-align: middle;
  white-space: nowrap;
}

.pdf-table-inner th {
  height: 38px;
  border-bottom: 1px solid #e5ebf2;
  background: #f3f6fa;
  color: #5a6677;
  font-size: 12px;
  font-weight: 700;
}

.pdf-table-inner td {
  height: 48px;
  border-bottom: 1px solid #eef2f6;
}

.pdf-table-inner th + th,
.pdf-table-inner td + td {
  padding-left: 24px;
}

.pdf-row:hover {
  background: #f8fbff;
}

.pdf-name-cell {
  max-width: min(52vw, 560px);
}

.pdf-title-button {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  max-width: min(52vw, 560px);
  color: inherit;
  text-align: left;
  text-decoration: none;
  cursor: pointer;
}

.pdf-title {
  flex: 1 1 auto;
  min-width: 0;
  color: #182235;
  font-size: 14px;
  font-weight: 700;
  line-height: 22px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pdf-title-button:hover .pdf-title {
  color: #2368d1;
}

.pdf-subtitle {
  flex: 0 0 auto;
  min-width: 24px;
  color: #8a96a8;
  font-size: 12px;
  line-height: 22px;
}

.pdf-role,
.pdf-current-page,
.pdf-size,
.pdf-updated {
  color: #4f5d70;
  font-size: 13px;
  line-height: 20px;
  white-space: nowrap;
}

.pdf-current-page {
  color: #172033;
  font-weight: 700;
}

.pdf-spacer-cell {
  width: 100%;
  padding: 0 !important;
}

.pdf-empty {
  flex: 1 1 auto;
  min-height: 220px;
}

@media (max-width: 1100px) {
  .library-header {
    align-items: flex-start;
    flex-direction: column;
  }

  .library-actions {
    justify-content: flex-start;
    width: 100%;
  }

  .pdf-table-inner th,
  .pdf-table-inner td {
    padding-right: 12px;
  }

  .pdf-table-inner th + th,
  .pdf-table-inner td + td {
    padding-left: 18px;
  }

  .pdf-name-cell,
  .pdf-title-button {
    max-width: min(48vw, 440px);
  }
}

@media (max-width: 760px) {
  .pdf-library-page {
    padding: 12px;
  }

  .library-search {
    width: 100%;
  }

  .bookshelf-scroll {
    padding: 0;
  }

  .bookshelf-grid {
    padding-right: 14px;
    padding-left: 14px;
  }

  .pdf-table-inner {
    min-width: 720px;
  }

  .pdf-name-cell,
  .pdf-title-button {
    max-width: 260px;
  }
}
</style>
