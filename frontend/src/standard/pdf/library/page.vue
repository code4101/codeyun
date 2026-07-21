<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Grid, List, Plus, Search } from '@element-plus/icons-vue'

import {
  createPdfBookshelf,
  deletePdfBookshelf,
  fetchPdfBookshelves,
  fetchPdfDocuments,
  fetchPdfPagePreview,
  movePdfToBookshelf,
  renamePdfBookshelf,
  uploadPdfDocument,
  updatePdfBookshelfLayout,
  updatePdfDocumentMetadata,
  updatePdfUserState,
  type PdfBookshelfOrientation,
  type PdfBookshelfPlacement,
  type PdfDocumentSummary,
  type PdfLibraryBookshelf,
  type PdfResourceRole,
} from '@/api/pdfDocuments'
import { useUserStore } from '@/store/userStore'
import {
  fetchLocalSkillBookCatalog,
  type SkillBookCatalog,
} from '@/api/skillBooks'
import { getCachedPreviewPageUrl, loadPreviewPageBlock } from './previewPageCache'
import SkillBookReaderDialog from './SkillBookReaderDialog.vue'

type PdfFilter = 'all' | 'mine' | 'other'
type PdfViewMode = 'bookshelf' | 'list'

interface BookContextMenuState {
  pdfId: number
  x: number
  y: number
}

interface BookshelfContextMenuState {
  bookshelfId: string
  x: number
  y: number
}

interface BookshelfBookGroup {
  key: string
  kind: 'single' | 'horizontal-stack'
  documents: PdfDocumentSummary[]
}

interface BookTitleSegment {
  text: string
  combined: boolean
}

interface ExternalPdfDropTarget {
  shelfIndex: number
  beforePdfId: number | null
}

const PDF_LIBRARY_VIEW_MODE_KEY = 'codeyun.pdf-library.view-mode'
const PDF_LIBRARY_BOOKSHELF_KEY_PREFIX = 'codeyun.pdf-library.bookshelf'
const BOOK_PAGE_SCALE = 0.32
const MIN_BOOK_HEIGHT = 190
const MAX_BOOK_HEIGHT = 286
const MIN_SPINE_WIDTH = 24
const MAX_SPINE_WIDTH = 84
const BOOK_THICKNESS_SCALE = 2
const MIN_SPINE_FONT_SIZE = 12
const MAX_SPINE_FONT_SIZE = 36
const SPINE_VERTICAL_TEXT_HEIGHT_INSET = 20
const MAX_COMBINED_VERTICAL_TOKEN_LENGTH = 4
const COMPACT_VERTICAL_TOKEN_CELL_RATIO = 0.78
const MIN_TITLE_LENGTH_FOR_SEPARATE_QUALIFIER = 11
const MAX_HORIZONTAL_STACK_HEIGHT = 286
const DEFAULT_BOOKSHELF_ROW_COUNT = 12
const TRAILING_EMPTY_BOOKSHELF_ROW_COUNT = 4
const BOOK_ORIENTATION_CYCLE: PdfBookshelfOrientation[] = [
  'spine_vertical',
  'spine_horizontal',
  'cover_front',
]

const router = useRouter()
const userStore = useUserStore()

const loading = ref(false)
const bookshelves = ref<PdfLibraryBookshelf[]>([])
const selectedBookshelfId = ref('')
const documents = ref<PdfDocumentSummary[]>([])
const searchText = ref('')
const pdfFilter = ref<PdfFilter>('all')
const viewMode = ref<PdfViewMode>('bookshelf')
const draggingPdfId = ref<number | null>(null)
const dragOverPdfId = ref<number | null>(null)
const dragOverShelfIndex = ref<number | null>(null)
const bookDragOffsetX = ref(0)
const bookDragOffsetY = ref(0)
const externalFileDragActive = ref(false)
const externalPdfDropTarget = ref<ExternalPdfDropTarget | null>(null)
const importingDroppedPdfs = ref(false)
const localPdfInput = ref<HTMLInputElement | null>(null)
const bookContextMenu = ref<BookContextMenuState | null>(null)
const bookshelfContextMenu = ref<BookshelfContextMenuState | null>(null)
const metadataEditorVisible = ref(false)
const metadataEditorSaving = ref(false)
const metadataEditorPdfId = ref<number | null>(null)
const metadataEditorTitle = ref('')
const metadataEditorAuthor = ref('')
const previewVisible = ref(false)
const previewDocument = ref<PdfDocumentSummary | null>(null)
const previewPage = ref(1)
const previewImageUrl = ref('')
const previewLoading = ref(false)
const previewError = ref('')
const skillBookCatalog = ref<SkillBookCatalog | null>(null)
const skillBookReaderVisible = ref(false)
const bookCoverImageUrls = ref(new Map<number, string>())
let titleRefreshTimer: ReturnType<typeof setTimeout> | null = null
let layoutSaveQueue = Promise.resolve()
let pointerDragPdfId: number | null = null
let pointerStartX = 0
let pointerStartY = 0
let pointerMoved = false
let suppressNextBookClick = false
let externalFileDragDepth = 0
let previewLoadSequence = 0
let documentReloadSequence = 0
const loadingBookCoverIds = new Set<number>()

const currentUserId = computed(() => userStore.user?.id ?? null)
const selectedBookshelf = computed(() => bookshelves.value.find(
  (bookshelf) => bookshelf.id === selectedBookshelfId.value,
) ?? null)
const contextBookshelf = computed(() => bookshelves.value.find(
  (bookshelf) => bookshelf.id === bookshelfContextMenu.value?.bookshelfId,
) ?? null)
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

const showLocalSkillBook = computed(() => {
  const catalog = skillBookCatalog.value
  if (!catalog || pdfFilter.value === 'other' || selectedBookshelfId.value !== bookshelves.value[0]?.id) {
    return false
  }
  const query = normalizedSearchText.value
  if (!query) {
    return true
  }
  return [
    catalog.title,
    catalog.author,
    ...catalog.skills.flatMap((skill) => [
      skill.name,
      skill.description,
      ...skill.chapters.map((chapter) => chapter.title),
    ]),
  ].some((value) => value.toLowerCase().includes(query))
})
const hasVisibleLibraryItems = computed(() => showLocalSkillBook.value || filteredDocuments.value.length > 0)
const skillBookSpineStyle = computed(() => {
  const pages = skillBookCatalog.value?.estimated_page_count ?? 1
  const spineWidth = Math.min(104, Math.max(64, Math.round(52 + Math.sqrt(pages) * 2.4)))
  return {
    '--spine-width': `${spineWidth}px`,
    '--spine-height': '286px',
    '--book-item-width': `${spineWidth}px`,
    '--spine-font-size': '23px',
    '--book-title-display': 'block',
    '--book-author-display': spineWidth >= 76 ? 'block' : 'none',
    '--book-author-font-size': '10px',
    '--book-cover-color': skillBookCatalog.value?.cover_color ?? '#315f53',
    '--book-cover-ink': '#ffffff',
    '--book-lean': '0deg',
  }
})

const filteredDocumentIds = computed(() => new Set(filteredDocuments.value.map((document) => document.id)))
const previewPageCount = computed(() => Math.max(1, previewDocument.value?.metadata.page_count ?? 1))
const previewStandaloneHref = computed(() => previewDocument.value
  ? resolvePdfHref(previewDocument.value.id)
  : '#')
const previewDialogStyle = computed(() => {
  const metadata = previewDocument.value?.metadata
  const pageWidth = metadata?.status === 'ready' && metadata.page_width_points
    ? metadata.page_width_points
    : 612
  const pageHeight = metadata?.status === 'ready' && metadata.page_height_points
    ? metadata.page_height_points
    : 792
  return {
    '--preview-page-aspect-ratio': `${pageWidth} / ${pageHeight}`,
  }
})

const bookshelfRows = computed(() => {
  return buildBookshelfRows().map((row) => row.filter((document) => filteredDocumentIds.value.has(document.id)))
})
const bookshelfDisplayRows = computed(() => bookshelfRows.value.map(buildBookshelfBookGroups))
const externalDropShelfLabel = computed(() => externalPdfDropTarget.value == null
  ? `书柜“${selectedBookshelf.value?.name ?? ''}”`
  : `书柜“${selectedBookshelf.value?.name ?? ''}”第 ${externalPdfDropTarget.value.shelfIndex + 1} 栏`)

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

function skillBookTooltip() {
  const catalog = skillBookCatalog.value
  if (!catalog) {
    return '本地 Skill 手册'
  }
  return [
    tooltipField('书名', catalog.title),
    tooltipField('作者', catalog.author),
    tooltipField('目录', `${catalog.skill_count} 个 Skill / ${catalog.chapter_count} 篇`),
    tooltipField('内容', '按需读取本地最新文件'),
  ].join('\n')
}

function openSkillBookReader() {
  skillBookReaderVisible.value = true
}

function handleSkillBookCatalogUpdated(catalog: SkillBookCatalog) {
  skillBookCatalog.value = catalog
}

async function reloadSkillBookCatalog() {
  try {
    skillBookCatalog.value = await fetchLocalSkillBookCatalog()
  } catch (error) {
    console.warn('Failed to load local Skill book:', error)
    skillBookCatalog.value = null
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

function coverInkColor(coverColor?: string | null) {
  const match = /^#([0-9a-f]{6})$/i.exec(coverColor ?? '')
  if (!match) {
    return '#ffffff'
  }
  const value = Number.parseInt(match[1], 16)
  const red = (value >> 16) & 255
  const green = (value >> 8) & 255
  const blue = value & 255
  const luminance = (red * 0.2126 + green * 0.7152 + blue * 0.0722) / 255
  return luminance > 0.62 ? '#26313d' : '#ffffff'
}

function bookOrientation(document: PdfDocumentSummary): PdfBookshelfOrientation {
  return document.bookshelf_placement?.orientation ?? 'spine_vertical'
}

function bookOrientationClass(document: PdfDocumentSummary) {
  return `orientation-${bookOrientation(document).replace('_', '-')}`
}

function isSeparateVolumeQualifier(qualifier: string) {
  const content = qualifier.slice(1, -1)
  return /^(?:第[一二三四五六七八九十百千万\d]+[卷册]|[上中下](?:卷|册)?|全[一二三四五六七八九十百千万\d]+卷)$/.test(content)
}

function bookSpineTitleParts(document: PdfDocumentSummary) {
  const title = document.display_title.trim()
  if (bookOrientation(document) !== 'spine_vertical') {
    return { title, qualifier: '' }
  }

  const verticalTitle = title
    .replace(/\s+/g, '')
    .replace(/\(/g, '（')
    .replace(/\)/g, '）')
  const qualifierMatch = /^(.*?)(（[^（）]+）)$/.exec(verticalTitle)
  if (
    qualifierMatch
    && Array.from(verticalTitle).length >= MIN_TITLE_LENGTH_FOR_SEPARATE_QUALIFIER
    && qualifierMatch[1]
    && isSeparateVolumeQualifier(qualifierMatch[2])
  ) {
    return { title: qualifierMatch[1], qualifier: qualifierMatch[2] }
  }
  return { title: verticalTitle, qualifier: '' }
}

function bookSpineTitle(document: PdfDocumentSummary) {
  return bookSpineTitleParts(document).title
}

function bookSpineQualifier(document: PdfDocumentSummary) {
  return bookSpineTitleParts(document).qualifier
}

function bookSpineTitleSegments(document: PdfDocumentSummary) {
  const title = bookSpineTitle(document)
  if (bookOrientation(document) !== 'spine_vertical') {
    return [{ text: title, combined: false }] satisfies BookTitleSegment[]
  }

  const segments: BookTitleSegment[] = []
  const tokenPattern = /[A-Za-z0-9]+(?:[.+_-][A-Za-z0-9]+)*/g
  let cursor = 0
  for (const match of title.matchAll(tokenPattern)) {
    const index = match.index ?? 0
    if (index > cursor) {
      segments.push({ text: title.slice(cursor, index), combined: false })
    }
    const token = match[0]
    segments.push({
      text: token,
      combined: Array.from(token).length <= MAX_COMBINED_VERTICAL_TOKEN_LENGTH,
    })
    cursor = index + token.length
  }
  if (cursor < title.length) {
    segments.push({ text: title.slice(cursor), combined: false })
  }
  return segments.length ? segments : [{ text: title, combined: false }]
}

function isCompactVerticalToken(segment: BookTitleSegment) {
  return !segment.combined && /^[A-Za-z0-9]+(?:[.+_-][A-Za-z0-9]+)*$/.test(segment.text)
}

function bookTitleSegmentCellCount(segment: BookTitleSegment) {
  if (segment.combined) {
    return 1
  }
  const glyphCount = Array.from(segment.text).length
  if (!isCompactVerticalToken(segment) || glyphCount <= 1) {
    return glyphCount
  }
  return 1 + (glyphCount - 1) * COMPACT_VERTICAL_TOKEN_CELL_RATIO
}

function bookTitleCellCount(document: PdfDocumentSummary) {
  if (bookOrientation(document) !== 'spine_vertical') {
    return Array.from(document.display_title.replace(/\s+/g, '')).length
  }
  return bookSpineTitleSegments(document).reduce(
    (count, segment) => count + bookTitleSegmentCellCount(segment),
    0,
  )
}

function bookSpineDisplayTitleSegments(document: PdfDocumentSummary) {
  const segments = bookSpineTitleSegments(document)
  if (bookOrientation(document) !== 'spine_vertical') {
    return segments
  }

  const { bookHeight } = bookPhysicalGeometry(document)
  const titleFontSize = bookTitleFontSize(document)
  const maximumCellCount = Math.max(
    2,
    Math.floor((bookHeight - SPINE_VERTICAL_TEXT_HEIGHT_INSET) / titleFontSize),
  )
  if (bookTitleCellCount(document) <= maximumCellCount) {
    return segments
  }

  const visibleCellCount = maximumCellCount - 1
  const visibleSegments: BookTitleSegment[] = []
  let usedCellCount = 0
  for (const segment of segments) {
    if (usedCellCount >= visibleCellCount) {
      break
    }
    if (segment.combined) {
      visibleSegments.push(segment)
      usedCellCount += 1
      continue
    }
    const availableCellCount = visibleCellCount - usedCellCount
    const segmentGlyphs = Array.from(segment.text)
    const visibleGlyphCount = isCompactVerticalToken(segment)
      ? Math.min(
          segmentGlyphs.length,
          availableCellCount < 1
            ? 0
            : 1 + Math.floor((availableCellCount - 1) / COMPACT_VERTICAL_TOKEN_CELL_RATIO),
        )
      : Math.min(segmentGlyphs.length, Math.floor(availableCellCount))
    const visibleText = segmentGlyphs.slice(0, visibleGlyphCount).join('')
    if (visibleText) {
      visibleSegments.push({ text: visibleText, combined: false })
      usedCellCount += bookTitleSegmentCellCount({ text: visibleText, combined: false })
    }
  }
  visibleSegments.push({ text: '⋮', combined: false })
  return visibleSegments
}

function bookPhysicalGeometry(document: PdfDocumentSummary) {
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
  const pageDepth = Math.min(210, Math.max(120, Math.round(pageWidth * screenScale)))
  return { pageCount, bookHeight, baseSpineWidth, spineWidth, pageDepth }
}

function bookTitleFontSize(document: PdfDocumentSummary) {
  const { bookHeight, baseSpineWidth, spineWidth } = bookPhysicalGeometry(document)
  const widthRatio = (baseSpineWidth - MIN_SPINE_WIDTH) / (MAX_SPINE_WIDTH - MIN_SPINE_WIDTH)
  const heightRatio = (bookHeight - MIN_BOOK_HEIGHT) / (MAX_BOOK_HEIGHT - MIN_BOOK_HEIGHT)
  const sizeRatio = Math.min(1, Math.max(0, widthRatio * 0.75 + heightRatio * 0.25))
  const titleGlyphCount = Math.max(4, bookTitleCellCount(document))
  const widthFontLimit = spineWidth * 0.46
  const heightFontLimit = (bookHeight - SPINE_VERTICAL_TEXT_HEIGHT_INSET) / titleGlyphCount
  const geometryFontLimit = MIN_SPINE_FONT_SIZE
    + (MAX_SPINE_FONT_SIZE - MIN_SPINE_FONT_SIZE) * (0.45 + sizeRatio * 0.55)
  let titleFontSize = Math.floor(Math.min(
    MAX_SPINE_FONT_SIZE,
    Math.max(
      MIN_SPINE_FONT_SIZE,
      Math.min(widthFontLimit, heightFontLimit, geometryFontLimit),
    ),
  ))
  const orientation = bookOrientation(document)
  if (orientation === 'spine_vertical' && bookSpineQualifier(document)) {
    const availableWidth = Math.max(0, spineWidth - 13)
    while (titleFontSize > MIN_SPINE_FONT_SIZE) {
      const qualifierFontSize = Math.min(14, Math.max(10, Math.round(titleFontSize * 0.58)))
      if (titleFontSize * 1.35 + qualifierFontSize * 1.2 <= availableWidth) {
        break
      }
      titleFontSize -= 1
    }
  }
  if (orientation === 'spine_horizontal') {
    titleFontSize = Math.min(
      titleFontSize,
      Math.max(MIN_SPINE_FONT_SIZE, Math.floor((bookHeight - 30) / titleGlyphCount * 0.95)),
    )
  }
  return titleFontSize
}

function bookSpineStyle(document: PdfDocumentSummary) {
  const {
    pageCount,
    bookHeight,
    spineWidth,
    pageDepth,
  } = bookPhysicalGeometry(document)
  const metadata = document.metadata
  const titleFontSize = bookTitleFontSize(document)
  const titleGlyphCount = Math.max(4, bookTitleCellCount(document))
  const orientation = bookOrientation(document)
  const itemWidth = orientation === 'spine_vertical'
    ? spineWidth
    : orientation === 'spine_horizontal'
      ? bookHeight
      : pageDepth
  const showTitle = orientation === 'cover_front' || pageCount > 1
  const coverFlatHeight = bookHeight
  const coverFontSize = Math.min(28, Math.max(16, Math.round(Math.min(pageDepth / 6, coverFlatHeight / 8))))
  const author = document.display_author?.trim() ?? ''
  const authorGlyphCount = Array.from(author.replace(/\s+/g, '')).length
  const authorFontSize = Math.min(15, Math.max(10, Math.round(titleFontSize * 0.48)))
  const qualifier = bookSpineQualifier(document)
  const qualifierFontSize = Math.min(14, Math.max(10, Math.round(titleFontSize * 0.58)))
  const horizontalAuthorWidth = authorGlyphCount * authorFontSize * 0.9
  const horizontalTwoLineHeight = titleFontSize * 1.35 + authorFontSize * 1.25 + 18
  const showAuthor = showTitle && !qualifier && authorGlyphCount > 0 && (
    orientation === 'spine_vertical'
      ? spineWidth >= titleFontSize * 1.35 + authorFontSize * 1.2 + 14
      : orientation === 'spine_horizontal'
        ? spineWidth >= horizontalTwoLineHeight
          && bookHeight - 24 >= horizontalAuthorWidth
        : bookHeight - 40 >= Math.ceil(
          titleGlyphCount * coverFontSize * 0.95 / Math.max(pageDepth - 32, 1),
        ) * coverFontSize * 1.45 + authorFontSize * 1.35 + 12
  )
  const verticalLeanDegrees = [-0.55, -0.4, -0.25, -0.1, 0][Math.abs(document.id) % 5]
  const leanDegrees = orientation === 'spine_vertical' ? verticalLeanDegrees : 0
  const currentPage = Math.max(1, Math.min(document.my_state?.current_page ?? 1, pageCount))
  const readingProgress = pageCount <= 1 ? 1 : (currentPage - 1) / (pageCount - 1)
  // When the spine faces the reader, early pages sit by the right cover;
  // the bookmark therefore travels from right to left as reading advances.
  const bookmarkPagePosition = 1 - readingProgress
  const bookmarkTilt = [-1.1, -0.45, 0.25, 0.85][Math.abs(document.id) % 4]
  const coverImageUrl = bookCoverImageUrls.value.get(document.id)
  return {
    '--spine-width': `${spineWidth}px`,
    '--spine-height': `${bookHeight}px`,
    '--spine-font-size': `${titleFontSize}px`,
    '--page-depth': `${pageDepth}px`,
    '--cover-flat-height': `${coverFlatHeight}px`,
    '--book-item-width': `${itemWidth}px`,
    '--book-title-display': showTitle ? 'block' : 'none',
    '--book-drag-x': document.id === draggingPdfId.value ? `${bookDragOffsetX.value}px` : '0px',
    '--book-drag-y': document.id === draggingPdfId.value ? `${bookDragOffsetY.value}px` : '0px',
    '--cover-font-size': `${coverFontSize}px`,
    '--book-author-font-size': `${authorFontSize}px`,
    '--book-author-display': showAuthor ? 'block' : 'none',
    '--book-qualifier-font-size': `${qualifierFontSize}px`,
    '--book-lean': `${leanDegrees}deg`,
    '--book-reading-progress': `${(bookmarkPagePosition * 100).toFixed(2)}%`,
    '--book-cover-bookmark-progress': `${(readingProgress * 100).toFixed(2)}%`,
    '--book-bookmark-tilt': `${bookmarkTilt}deg`,
    '--book-cover-color': metadata.cover_average_color ?? undefined,
    '--book-cover-ink': coverInkColor(metadata.cover_average_color),
    '--book-cover-image': coverImageUrl ? `url("${coverImageUrl}")` : undefined,
  }
}

async function ensureBookCoverImage(document: PdfDocumentSummary) {
  if (bookCoverImageUrls.value.has(document.id) || loadingBookCoverIds.has(document.id)) {
    return
  }
  loadingBookCoverIds.add(document.id)
  try {
    const blob = await fetchPdfPagePreview(document.id, 1)
    const nextUrls = new Map(bookCoverImageUrls.value)
    nextUrls.set(document.id, URL.createObjectURL(blob))
    bookCoverImageUrls.value = nextUrls
  } catch (error) {
    console.warn('Failed to load PDF cover image:', error)
  } finally {
    loadingBookCoverIds.delete(document.id)
  }
}

function ensureFacingCoverImages(sourceDocuments: PdfDocumentSummary[]) {
  for (const document of sourceDocuments) {
    if (bookOrientation(document) === 'cover_front') {
      void ensureBookCoverImage(document)
    }
  }
}

function releaseBookCoverImages() {
  for (const imageUrl of bookCoverImageUrls.value.values()) {
    URL.revokeObjectURL(imageUrl)
  }
  bookCoverImageUrls.value = new Map()
}

function wrapTooltipValue(value: string, maxDisplayWidth = 22) {
  const lines: string[] = []
  let line = ''
  let lineWidth = 0
  for (const character of Array.from(value)) {
    const characterWidth = /^[\x00-\xff]$/.test(character) ? 1 : 2
    if (line && lineWidth + characterWidth > maxDisplayWidth) {
      lines.push(line)
      line = ''
      lineWidth = 0
    }
    line += character
    lineWidth += characterWidth
  }
  if (line || lines.length === 0) {
    lines.push(line)
  }
  return lines
}

function tooltipField(label: string, value: string, wrapValue = false) {
  const key = `${label}：`
  const valueIndent = '　'.repeat(4)
  const alignedKey = `${key}${'　'.repeat(Math.max(0, 4 - Array.from(key).length))}`
  const valueLines = wrapValue ? wrapTooltipValue(value) : [value]
  return valueLines
    .map((line, index) => `${index === 0 ? alignedKey : valueIndent}${line}`)
    .join('\n')
}

function hasReadingBookmark(document: PdfDocumentSummary) {
  return Boolean(document.my_state && document.my_state.current_page >= 1)
}

function bookTooltip(document: PdfDocumentSummary) {
  const metadata = document.metadata
  const currentPage = document.my_state?.current_page
  const currentPageLabel = currentPage && currentPage >= 1 ? currentPage : '-'
  const pageCountLabel = metadata?.status === 'ready' && metadata.page_count >= 1
    ? metadata.page_count
    : '-'
  const fields = [
    tooltipField('书名', document.display_title),
    ...(document.display_author ? [tooltipField('作者', document.display_author)] : []),
    tooltipField('原文件', document.title, true),
    tooltipField('页码', `${currentPageLabel}/${pageCountLabel}`),
  ]
  return fields.join('\n')
}

function buildBookshelfRows() {
  const originalOrder = new Map(documents.value.map((document, index) => [document.id, index]))
  const maxShelfIndex = documents.value.reduce(
    (maximum, document) => Math.max(maximum, document.bookshelf_placement?.shelf_index ?? 0),
    0,
  )
  const rowCount = Math.max(
    DEFAULT_BOOKSHELF_ROW_COUNT,
    maxShelfIndex + 1 + TRAILING_EMPTY_BOOKSHELF_ROW_COUNT,
  )
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

function buildBookshelfBookGroups(row: PdfDocumentSummary[]) {
  const groups: BookshelfBookGroup[] = []
  let currentStack: BookshelfBookGroup | null = null
  let currentStackHeight = 0

  for (const document of row) {
    if (bookOrientation(document) !== 'spine_horizontal') {
      currentStack = null
      currentStackHeight = 0
      groups.push({
        key: `book-${document.id}`,
        kind: 'single',
        documents: [document],
      })
      continue
    }

    const thickness = bookPhysicalGeometry(document).spineWidth
    if (!currentStack || currentStackHeight + thickness > MAX_HORIZONTAL_STACK_HEIGHT) {
      currentStack = {
        key: `stack-${document.id}`,
        kind: 'horizontal-stack',
        documents: [],
      }
      groups.push(currentStack)
      currentStackHeight = 0
    }
    currentStack.documents.push(document)
    currentStackHeight += thickness
  }

  return groups
}

function clearBookDragState() {
  draggingPdfId.value = null
  dragOverPdfId.value = null
  dragOverShelfIndex.value = null
  bookDragOffsetX.value = 0
  bookDragOffsetY.value = 0
}

function closeBookContextMenu() {
  bookContextMenu.value = null
}

function closeBookshelfContextMenu() {
  bookshelfContextMenu.value = null
}

function closeContextMenus() {
  closeBookContextMenu()
  closeBookshelfContextMenu()
}

function openBookContextMenu(event: MouseEvent, pdfId: number) {
  event.preventDefault()
  event.stopPropagation()
  closeBookshelfContextMenu()
  const menuWidth = 176
  const document = documents.value.find((item) => item.id === pdfId)
  const menuHeight = document && canEditBookMetadata(document) ? 76 : 42
  bookContextMenu.value = {
    pdfId,
    x: Math.max(8, Math.min(event.clientX, window.innerWidth - menuWidth - 8)),
    y: Math.max(8, Math.min(event.clientY, window.innerHeight - menuHeight - 8)),
  }
}

function openBookshelfContextMenu(event: MouseEvent, bookshelfId: string) {
  event.preventDefault()
  event.stopPropagation()
  closeBookContextMenu()
  const menuWidth = 128
  const bookshelf = bookshelves.value.find((item) => item.id === bookshelfId)
  const menuHeight = bookshelf?.book_count === 0 ? 79 : 40
  bookshelfContextMenu.value = {
    bookshelfId,
    x: Math.max(8, Math.min(event.clientX, window.innerWidth - menuWidth - 8)),
    y: Math.max(8, Math.min(event.clientY, window.innerHeight - menuHeight - 8)),
  }
}

function handleContextMenuKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    closeContextMenus()
  }
}

function nextBookOrientation(orientation: PdfBookshelfOrientation): PdfBookshelfOrientation {
  const currentIndex = BOOK_ORIENTATION_CYCLE.indexOf(orientation)
  return BOOK_ORIENTATION_CYCLE[(currentIndex + 1) % BOOK_ORIENTATION_CYCLE.length] ?? 'spine_vertical'
}

function rotateBook(pdfId: number) {
  const document = documents.value.find((item) => item.id === pdfId)
  if (!document) {
    return
  }
  const orientation = nextBookOrientation(bookOrientation(document))
  applyBookshelfRows(buildBookshelfRows(), new Map([[pdfId, orientation]]))
  if (orientation === 'cover_front') {
    void ensureBookCoverImage(document)
  }
}

function applyBookshelfRows(
  rows: PdfDocumentSummary[][],
  orientationOverrides: ReadonlyMap<number, PdfBookshelfOrientation> = new Map(),
  persist = true,
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
  if (persist) {
    queueBookshelfLayoutSave(placements)
  }
  return placements
}

function rotateContextBook() {
  const pdfId = bookContextMenu.value?.pdfId
  if (pdfId == null) {
    return
  }
  closeBookContextMenu()
  rotateBook(pdfId)
}

function canEditBookMetadata(document: PdfDocumentSummary) {
  return document.access.role === 'editor' || document.access.role === 'manager'
}

function openMetadataEditorFromContext() {
  const pdfId = bookContextMenu.value?.pdfId
  const document = documents.value.find((item) => item.id === pdfId)
  closeBookContextMenu()
  if (!document || !canEditBookMetadata(document)) {
    return
  }
  metadataEditorPdfId.value = document.id
  metadataEditorTitle.value = document.display_title
  metadataEditorAuthor.value = document.display_author
  metadataEditorVisible.value = true
}

async function saveMetadataEditor() {
  const pdfId = metadataEditorPdfId.value
  const displayTitle = metadataEditorTitle.value.trim()
  if (pdfId == null || !displayTitle) {
    ElMessage.warning('书名不能为空')
    return
  }
  metadataEditorSaving.value = true
  try {
    const updatedDocument = await updatePdfDocumentMetadata(pdfId, {
      display_title: displayTitle,
      display_author: metadataEditorAuthor.value.trim(),
    })
    documents.value = documents.value.map((document) => (
      document.id === updatedDocument.id ? updatedDocument : document
    ))
    metadataEditorVisible.value = false
    ElMessage.success('图书元数据已保存')
  } catch (error) {
    console.warn('Failed to update PDF metadata:', error)
    ElMessage.error('保存图书元数据失败')
  } finally {
    metadataEditorSaving.value = false
  }
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
  window.addEventListener('pointerup', handleBookPointerUp)
  window.addEventListener('pointercancel', handleBookPointerCancel, { once: true })
  window.addEventListener('contextmenu', handleDragRotateContextMenu, { capture: true })
}

function handleDragRotateContextMenu(event: MouseEvent) {
  if (pointerDragPdfId == null || !pointerMoved) {
    return
  }
  event.preventDefault()
  event.stopPropagation()
  rotateBook(pointerDragPdfId)
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
  bookDragOffsetX.value = event.clientX - pointerStartX
  bookDragOffsetY.value = event.clientY - pointerStartY
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
  if (event.button !== 0) {
    return
  }
  window.removeEventListener('pointermove', handleBookPointerMove)
  window.removeEventListener('pointerup', handleBookPointerUp)
  window.removeEventListener('pointercancel', handleBookPointerCancel)
  window.removeEventListener('contextmenu', handleDragRotateContextMenu, { capture: true })
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
  window.removeEventListener('contextmenu', handleDragRotateContextMenu, { capture: true })
  pointerDragPdfId = null
  pointerMoved = false
  clearBookDragState()
}

function handleBookClick(event: MouseEvent, document: PdfDocumentSummary) {
  event.preventDefault()
  if (suppressNextBookClick) {
    return
  }
  openBookPreview(document)
}

async function persistPreviewPage(document: PdfDocumentSummary, pageNumber: number) {
  if (!document.access.capabilities.can_update_state) {
    return
  }
  const previousState = document.my_state
  try {
    document.my_state = await updatePdfUserState(document.id, {
      current_page: pageNumber,
      zoom: previousState?.zoom ?? 'auto',
      sidebar_open: previousState?.sidebar_open ?? true,
      state_json: previousState?.state_json ?? {},
    })
  } catch (error) {
    console.warn('Failed to save PDF preview position:', error)
  }
}

async function loadPreviewPage(document: PdfDocumentSummary, pageNumber: number) {
  const loadSequence = ++previewLoadSequence
  const cachedUrl = getCachedPreviewPageUrl(document.id, pageNumber)
  previewImageUrl.value = cachedUrl
  previewLoading.value = !cachedUrl
  previewError.value = ''
  try {
    const pageCount = Math.max(1, document.metadata.page_count ?? 1)
    const imageUrl = await loadPreviewPageBlock(document.id, pageNumber, pageCount)
    if (loadSequence !== previewLoadSequence || previewDocument.value?.id !== document.id || previewPage.value !== pageNumber) {
      return
    }
    previewImageUrl.value = imageUrl
    void persistPreviewPage(document, pageNumber)
  } catch (error) {
    if (loadSequence === previewLoadSequence) {
      console.warn('Failed to load PDF page preview:', error)
      previewError.value = '这一页暂时无法预览'
    }
  } finally {
    if (loadSequence === previewLoadSequence) {
      previewLoading.value = false
    }
  }
}

function openBookPreview(document: PdfDocumentSummary) {
  const pageCount = Math.max(1, document.metadata.page_count ?? 1)
  const currentPage = Math.min(pageCount, Math.max(1, document.my_state?.current_page ?? 1))
  previewDocument.value = document
  previewPage.value = currentPage
  previewVisible.value = true
  void loadPreviewPage(document, currentPage)
}

function turnPreviewPage(offset: number) {
  const document = previewDocument.value
  if (!document) {
    return
  }
  const nextPage = Math.min(previewPageCount.value, Math.max(1, previewPage.value + offset))
  if (nextPage === previewPage.value) {
    return
  }
  previewPage.value = nextPage
  void loadPreviewPage(document, nextPage)
}

function closeBookPreview() {
  previewLoadSequence += 1
  previewImageUrl.value = ''
  previewLoading.value = false
  previewError.value = ''
  previewDocument.value = null
}

function handlePreviewKeydown(event: KeyboardEvent) {
  if (!previewVisible.value) {
    return
  }
  if (event.key === 'ArrowLeft') {
    event.preventDefault()
    event.stopPropagation()
    turnPreviewPage(-1)
  } else if (event.key === 'ArrowRight') {
    event.preventDefault()
    event.stopPropagation()
    turnPreviewPage(1)
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
  const bookshelfId = selectedBookshelfId.value
  if (!bookshelfId) {
    documents.value = []
    return
  }
  const reloadSequence = ++documentReloadSequence
  if (!options.silent) {
    loading.value = true
  }
  try {
    const loadedDocuments = await fetchPdfDocuments(bookshelfId)
    if (reloadSequence !== documentReloadSequence || bookshelfId !== selectedBookshelfId.value) {
      return
    }
    documents.value = loadedDocuments
    ensureFacingCoverImages(documents.value)
    scheduleTitleRefresh()
  } catch (error) {
    console.warn('Failed to load PDF documents:', error)
    if (!options.silent) {
      ElMessage.error('加载馆藏失败')
    }
  } finally {
    if (!options.silent && reloadSequence === documentReloadSequence) {
      loading.value = false
    }
  }
}

function bookshelfSelectionStorageKey() {
  return `${PDF_LIBRARY_BOOKSHELF_KEY_PREFIX}.${currentUserId.value ?? 'anonymous'}`
}

async function reloadBookshelves() {
  const loadedBookshelves = await fetchPdfBookshelves()
  bookshelves.value = loadedBookshelves
  const savedBookshelfId = localStorage.getItem(bookshelfSelectionStorageKey()) ?? ''
  const preferredBookshelfId = selectedBookshelfId.value || savedBookshelfId
  selectedBookshelfId.value = loadedBookshelves.some(
    (bookshelf) => bookshelf.id === preferredBookshelfId,
  )
    ? preferredBookshelfId
    : loadedBookshelves[0]?.id ?? ''
  if (selectedBookshelfId.value) {
    localStorage.setItem(bookshelfSelectionStorageKey(), selectedBookshelfId.value)
  }
}

async function selectBookshelf(bookshelfId: string) {
  if (bookshelfId === selectedBookshelfId.value) {
    return
  }
  closeBookContextMenu()
  releaseBookCoverImages()
  selectedBookshelfId.value = bookshelfId
  localStorage.setItem(bookshelfSelectionStorageKey(), bookshelfId)
  documents.value = []
  await reloadDocuments()
}

async function handleCreateBookshelf() {
  try {
    const { value } = await ElMessageBox.prompt('为新书柜命名', '新建书柜', {
      inputValue: '',
      confirmButtonText: '新建',
      cancelButtonText: '取消',
      inputValidator: (inputValue) => inputValue.trim() ? true : '请输入书柜名称',
    })
    const bookshelf = await createPdfBookshelf(value.trim())
    bookshelves.value.push(bookshelf)
    ElMessage.success(`已新建书柜“${bookshelf.name}”`)
  } catch (error) {
    if (error === 'cancel' || error === 'close') {
      return
    }
    console.warn('Failed to create PDF bookshelf:', error)
    ElMessage.error('新建书柜失败，请检查名称是否重复')
  }
}

async function handleRenameBookshelf(bookshelf: PdfLibraryBookshelf) {
  try {
    const { value } = await ElMessageBox.prompt('修改书柜名称', '重命名书柜', {
      inputValue: bookshelf.name,
      confirmButtonText: '保存',
      cancelButtonText: '取消',
      inputValidator: (inputValue) => inputValue.trim() ? true : '请输入书柜名称',
    })
    const updated = await renamePdfBookshelf(bookshelf.id, value.trim())
    const index = bookshelves.value.findIndex((item) => item.id === bookshelf.id)
    if (index >= 0) {
      bookshelves.value[index] = updated
      bookshelves.value = [...bookshelves.value]
    }
    ElMessage.success('书柜已重命名')
  } catch (error) {
    if (error === 'cancel' || error === 'close') {
      return
    }
    console.warn('Failed to rename PDF bookshelf:', error)
    ElMessage.error('重命名失败，请检查名称是否重复')
  }
}

function renameContextBookshelf() {
  const bookshelfId = bookshelfContextMenu.value?.bookshelfId
  closeBookshelfContextMenu()
  const bookshelf = bookshelves.value.find((item) => item.id === bookshelfId)
  if (bookshelf) {
    void handleRenameBookshelf(bookshelf)
  }
}

async function deleteContextBookshelf() {
  const bookshelf = contextBookshelf.value
  closeBookshelfContextMenu()
  if (!bookshelf || bookshelf.book_count !== 0) {
    return
  }
  try {
    await ElMessageBox.confirm(`确定删除空书柜“${bookshelf.name}”？`, '删除书柜', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
    await deletePdfBookshelf(bookshelf.id)
    const deletedSelectedBookshelf = bookshelf.id === selectedBookshelfId.value
    if (deletedSelectedBookshelf) {
      releaseBookCoverImages()
      documents.value = []
    }
    await reloadBookshelves()
    if (deletedSelectedBookshelf) {
      await reloadDocuments()
    }
    ElMessage.success('书柜已删除')
  } catch (error) {
    if (error === 'cancel' || error === 'close') {
      return
    }
    console.warn('Failed to delete PDF bookshelf:', error)
    ElMessage.error('删除失败，只能删除空书柜')
  }
}

function handleImportLocalPdf() {
  if (!importingDroppedPdfs.value) {
    localPdfInput.value?.click()
  }
}

function hasExternalFiles(event: DragEvent) {
  return Array.from(event.dataTransfer?.types ?? []).includes('Files')
}

function resolveExternalPdfDropTarget(event: DragEvent): ExternalPdfDropTarget | null {
  if (viewMode.value !== 'bookshelf') {
    return null
  }
  const hitElement = document.elementFromPoint(event.clientX, event.clientY)
  const targetShelf = hitElement?.closest<HTMLElement>('.bookshelf-row')
  const shelfIndex = Number(targetShelf?.dataset.shelfIndex)
  if (!targetShelf || !Number.isInteger(shelfIndex) || shelfIndex < 0) {
    return null
  }

  const groups = Array.from(targetShelf.querySelectorAll<HTMLElement>('.book-group'))
  // The horizontal coordinate selects the gap between physical book groups. This keeps
  // a horizontal stack intact instead of accidentally inserting into the middle of it.
  const nextGroup = groups.find((group) => event.clientX < group.getBoundingClientRect().left + group.getBoundingClientRect().width / 2)
  const nextBook = nextGroup?.querySelector<HTMLElement>('.book-item')
  const nextPdfId = Number(nextBook?.dataset.pdfId)
  return {
    shelfIndex,
    beforePdfId: Number.isInteger(nextPdfId) ? nextPdfId : null,
  }
}

function handleExternalFileDragEnter(event: DragEvent) {
  if (!hasExternalFiles(event) || importingDroppedPdfs.value) {
    return
  }
  event.preventDefault()
  externalFileDragDepth += 1
  externalFileDragActive.value = true
  externalPdfDropTarget.value = resolveExternalPdfDropTarget(event)
}

function handleExternalFileDragOver(event: DragEvent) {
  if (!hasExternalFiles(event) || importingDroppedPdfs.value) {
    return
  }
  event.preventDefault()
  if (event.dataTransfer) {
    event.dataTransfer.dropEffect = 'copy'
  }
  externalFileDragActive.value = true
  externalPdfDropTarget.value = resolveExternalPdfDropTarget(event)
}

function handleExternalFileDragLeave() {
  if (!externalFileDragActive.value) {
    return
  }
  externalFileDragDepth = Math.max(0, externalFileDragDepth - 1)
  if (externalFileDragDepth === 0) {
    externalFileDragActive.value = false
    externalPdfDropTarget.value = null
  }
}

async function placeImportedDocuments(
  importedPdfIds: number[],
  target: ExternalPdfDropTarget,
) {
  const importedIdSet = new Set(importedPdfIds)
  const importedById = new Map<number, PdfDocumentSummary>()
  const rows = buildBookshelfRows().map((row) => row.filter((document) => {
    if (!importedIdSet.has(document.id)) {
      return true
    }
    importedById.set(document.id, document)
    return false
  }))
  const importedDocuments = importedPdfIds
    .map((pdfId) => importedById.get(pdfId))
    .filter((document): document is PdfDocumentSummary => Boolean(document))
  if (!importedDocuments.length) {
    return
  }

  while (rows.length <= target.shelfIndex) {
    rows.push([])
  }
  const targetRow = rows[target.shelfIndex]
  const beforeIndex = target.beforePdfId == null
    ? -1
    : targetRow.findIndex((document) => document.id === target.beforePdfId)
  targetRow.splice(beforeIndex >= 0 ? beforeIndex : targetRow.length, 0, ...importedDocuments)
  const placements = applyBookshelfRows(rows, new Map(), false)
  await updatePdfBookshelfLayout(placements)
}

async function importPdfFiles(
  pdfFiles: File[],
  rejectedCount = 0,
  dropTarget: ExternalPdfDropTarget | null = null,
) {
  const bookshelfId = selectedBookshelfId.value
  if (!bookshelfId) {
    ElMessage.error('请先选择书柜')
    return
  }
  if (!pdfFiles.length) {
    ElMessage.warning('请选择 PDF 文件')
    return
  }

  importingDroppedPdfs.value = true
  let importedCount = 0
  let failedCount = rejectedCount
  const importedPdfIds: number[] = []
  try {
    for (const file of pdfFiles) {
      try {
        const importedDocument = await uploadPdfDocument(file)
        await movePdfToBookshelf(importedDocument.id, bookshelfId)
        if (!importedPdfIds.includes(importedDocument.id)) {
          importedPdfIds.push(importedDocument.id)
        }
        importedCount += 1
      } catch (error) {
        failedCount += 1
        console.warn(`Failed to import PDF: ${file.name}`, error)
      }
    }
    await reloadBookshelves()
    await reloadDocuments()
    if (dropTarget && importedPdfIds.length) {
      try {
        await placeImportedDocuments(importedPdfIds, dropTarget)
      } catch (error) {
        console.warn('Failed to place imported PDFs at drop target:', error)
        await reloadDocuments({ silent: true })
        ElMessage.error('图书已导入，但保存落点失败')
        return
      }
    }
    if (failedCount === 0) {
      ElMessage.success(`已导入 ${importedCount} 本图书`)
    } else if (importedCount > 0) {
      ElMessage.warning(`已导入 ${importedCount} 本，${failedCount} 个文件失败`)
    } else {
      ElMessage.error('PDF 均导入失败')
    }
  } catch (error) {
    console.warn('Failed to refresh library after dropped PDF import:', error)
    ElMessage.error('PDF 已上传，但刷新书柜失败')
  } finally {
    importingDroppedPdfs.value = false
  }
}

async function handleLocalPdfInputChange(event: Event) {
  const input = event.target as HTMLInputElement
  const selectedFiles = Array.from(input.files ?? [])
  input.value = ''
  const pdfFiles = selectedFiles.filter((file) => (
    file.type.toLowerCase() === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf')
  ))
  await importPdfFiles(pdfFiles, selectedFiles.length - pdfFiles.length)
}

async function handleExternalFileDrop(event: DragEvent) {
  if (!hasExternalFiles(event) || importingDroppedPdfs.value) {
    return
  }
  event.preventDefault()
  const dropTarget = resolveExternalPdfDropTarget(event)
  externalFileDragDepth = 0
  externalFileDragActive.value = false
  externalPdfDropTarget.value = null

  const droppedFiles = Array.from(event.dataTransfer?.files ?? [])
  const pdfFiles = droppedFiles.filter((file) => (
    file.type.toLowerCase() === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf')
  ))
  if (!pdfFiles.length) {
    ElMessage.warning('只能拖入 PDF 文件')
    return
  }
  await importPdfFiles(pdfFiles, droppedFiles.length - pdfFiles.length, dropTarget)
}

async function initializeLibraryPage() {
  if (userStore.isAuthenticated && !userStore.user && !userStore.loading) {
    await userStore.fetchUserProfile()
  }
  try {
    await reloadBookshelves()
    await Promise.all([
      reloadDocuments(),
      reloadSkillBookCatalog(),
    ])
  } catch (error) {
    console.warn('Failed to initialize PDF library:', error)
    ElMessage.error('加载图书馆失败')
  }
}

onMounted(() => {
  restoreViewMode()
  window.addEventListener('pointerdown', closeContextMenus)
  window.addEventListener('keydown', handleContextMenuKeydown)
  window.addEventListener('keydown', handlePreviewKeydown, true)
  void initializeLibraryPage()
})

onBeforeUnmount(() => {
  if (titleRefreshTimer) {
    clearTimeout(titleRefreshTimer)
  }
  window.removeEventListener('pointermove', handleBookPointerMove)
  window.removeEventListener('pointerup', handleBookPointerUp)
  window.removeEventListener('pointercancel', handleBookPointerCancel)
  window.removeEventListener('contextmenu', handleDragRotateContextMenu, { capture: true })
  window.removeEventListener('pointerdown', closeContextMenus)
  window.removeEventListener('keydown', handleContextMenuKeydown)
  window.removeEventListener('keydown', handlePreviewKeydown, true)
  closeBookPreview()
  releaseBookCoverImages()
})
</script>

<template>
  <div class="pdf-library-page" v-loading="loading || importingDroppedPdfs">
    <header class="library-header">
      <nav class="library-bookshelves" aria-label="书柜">
        <div
          v-for="bookshelf in bookshelves"
          :key="bookshelf.id"
          class="library-bookshelf-tab"
          :class="{ active: bookshelf.id === selectedBookshelfId }"
        >
          <button
            type="button"
            class="library-bookshelf-select"
            :aria-current="bookshelf.id === selectedBookshelfId ? 'page' : undefined"
            :title="`${bookshelf.name}（${bookshelf.book_count} 本）`"
            @click="selectBookshelf(bookshelf.id)"
            @contextmenu="openBookshelfContextMenu($event, bookshelf.id)"
          >
            {{ bookshelf.name }}
          </button>
        </div>
        <button
          type="button"
          class="library-bookshelf-add"
          title="新建书柜"
          aria-label="新建书柜"
          @click="handleCreateBookshelf"
        >
          <el-icon><Plus /></el-icon>
        </button>
      </nav>

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
        <input
          ref="localPdfInput"
          class="pdf-file-input"
          type="file"
          accept="application/pdf,.pdf"
          multiple
          @change="handleLocalPdfInputChange"
        />
        <el-button
          type="primary"
          :icon="Plus"
          :loading="importingDroppedPdfs"
          @click="handleImportLocalPdf"
        >导入本机 PDF</el-button>
      </div>
    </header>

    <div
      v-if="bookshelfContextMenu"
      class="book-context-menu bookshelf-context-menu"
      role="menu"
      :style="{ left: `${bookshelfContextMenu.x}px`, top: `${bookshelfContextMenu.y}px` }"
      @pointerdown.stop
      @contextmenu.prevent
    >
      <button type="button" role="menuitem" @click="renameContextBookshelf">重命名</button>
      <template v-if="contextBookshelf?.book_count === 0">
        <div class="book-context-menu-separator" role="separator"></div>
        <button class="danger" type="button" role="menuitem" @click="deleteContextBookshelf">删除书柜</button>
      </template>
    </div>

    <section
      class="pdf-library-content"
      aria-label="图书馆藏书"
      @dragenter="handleExternalFileDragEnter"
      @dragover="handleExternalFileDragOver"
      @dragleave="handleExternalFileDragLeave"
      @drop="handleExternalFileDrop"
    >
      <div v-if="externalFileDragActive" class="pdf-drop-indicator" role="status">
        松开导入到{{ externalDropShelfLabel }}
      </div>
      <div
        v-if="hasVisibleLibraryItems && viewMode === 'bookshelf'"
        class="bookshelf-scroll"
        @scroll="closeBookContextMenu"
      >
        <div class="bookshelf-grid">
          <div
            v-for="(row, shelfIndex) in bookshelfDisplayRows"
            :key="shelfIndex"
            class="bookshelf-row"
            :data-shelf-index="shelfIndex"
            :class="{
              'drag-target': (dragOverShelfIndex === shelfIndex && dragOverPdfId == null)
                || (externalFileDragActive && externalPdfDropTarget?.shelfIndex === shelfIndex),
            }"
          >
            <button
              v-if="shelfIndex === 0 && showLocalSkillBook"
              type="button"
              class="book-item orientation-spine-vertical skill-book-item"
              :style="skillBookSpineStyle"
              :title="skillBookTooltip()"
              @click="openSkillBookReader"
            >
              <span class="book-spine">
                <span class="book-spine-title">
                  <span class="book-spine-title-token">本地</span>
                  <span class="book-spine-title-token is-compact-vertical-token">Skill</span>
                  <span class="book-spine-title-token">手册</span>
                </span>
                <span class="book-spine-author">{{ skillBookCatalog?.author }}</span>
              </span>
            </button>
            <div
              v-for="group in row"
              :key="group.key"
              class="book-group"
              :class="{ 'horizontal-book-stack': group.kind === 'horizontal-stack' }"
            >
              <button
                v-for="document in group.documents"
                :key="document.id"
                class="book-item"
                :class="[
                  bookOrientationClass(document),
                  {
                    'insert-before': dragOverPdfId === document.id
                      || (externalFileDragActive && externalPdfDropTarget?.beforePdfId === document.id),
                    dragging: draggingPdfId === document.id,
                    'has-cover-image': bookCoverImageUrls.has(document.id),
                  },
                ]"
                type="button"
                draggable="false"
                :data-pdf-id="document.id"
                :style="bookSpineStyle(document)"
                :title="bookTooltip(document)"
                @pointerdown="handleBookPointerDown($event, document.id)"
                @contextmenu="openBookContextMenu($event, document.id)"
                @click="handleBookClick($event, document)"
              >
                <span
                  v-if="hasReadingBookmark(document)"
                  class="book-progress-bookmark"
                  aria-hidden="true"
                ></span>
                <span class="book-spine" :class="spineTone(document)">
                  <span class="book-spine-title">
                    <span
                      v-for="(segment, segmentIndex) in bookSpineDisplayTitleSegments(document)"
                      :key="segmentIndex"
                      class="book-spine-title-token"
                      :class="{
                        'is-combined': segment.combined,
                        'is-compact-vertical-token': isCompactVerticalToken(segment),
                      }"
                    >{{ segment.text }}</span>
                  </span>
                  <span v-if="bookSpineQualifier(document)" class="book-spine-qualifier">
                    {{ bookSpineQualifier(document) }}
                  </span>
                  <span v-if="document.display_author" class="book-spine-author">
                    {{ document.display_author }}
                  </span>
                </span>
              </button>
            </div>
          </div>
        </div>
      </div>

      <div v-else-if="hasVisibleLibraryItems" class="pdf-table-scroll">
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
            <tr v-if="showLocalSkillBook && skillBookCatalog" class="pdf-row skill-book-table-row">
              <td class="pdf-name-cell">
                <button class="pdf-title-button skill-book-title-button" type="button" @click="openSkillBookReader">
                  <span class="pdf-title">{{ skillBookCatalog.title }}</span>
                </button>
              </td>
              <td class="pdf-role">本机动态</td>
              <td class="pdf-current-page">{{ skillBookCatalog.skill_count }} 个 Skill</td>
              <td class="pdf-size">{{ skillBookCatalog.chapter_count }} 篇</td>
              <td class="pdf-updated">{{ formatDateTime(skillBookCatalog.updated_at) }}</td>
              <td class="pdf-spacer-cell" aria-hidden="true"></td>
            </tr>
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
        :description="documents.length || skillBookCatalog ? '没有匹配的图书' : '暂无藏书'"
      />

      <div
        v-if="bookContextMenu"
        class="book-context-menu"
        role="menu"
        :style="{ left: `${bookContextMenu.x}px`, top: `${bookContextMenu.y}px` }"
        @pointerdown.stop
        @contextmenu.prevent
      >
        <button
          v-if="documents.find((document) => document.id === bookContextMenu?.pdfId && canEditBookMetadata(document))"
          type="button"
          role="menuitem"
          @click="openMetadataEditorFromContext"
        >编辑元数据</button>
        <button type="button" role="menuitem" @click="rotateContextBook">旋转</button>
      </div>
    </section>

    <el-dialog
      v-model="metadataEditorVisible"
      title="编辑图书元数据"
      width="min(480px, calc(100vw - 32px))"
      append-to-body
      destroy-on-close
    >
      <form class="metadata-editor-form" @submit.prevent="saveMetadataEditor">
        <div class="metadata-editor-field">
          <label for="pdf-metadata-title">书名</label>
          <el-input
            id="pdf-metadata-title"
            v-model="metadataEditorTitle"
            maxlength="80"
            autocomplete="off"
          />
        </div>
        <div class="metadata-editor-field">
          <label for="pdf-metadata-author">作者</label>
          <el-input
            id="pdf-metadata-author"
            v-model="metadataEditorAuthor"
            maxlength="60"
            autocomplete="off"
          />
        </div>
      </form>
      <template #footer>
        <el-button @click="metadataEditorVisible = false">取消</el-button>
        <el-button type="primary" :loading="metadataEditorSaving" @click="saveMetadataEditor">
          保存
        </el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="previewVisible"
      class="book-preview-dialog"
      width="min(920px, calc(100vw - 32px))"
      :style="previewDialogStyle"
      append-to-body
      destroy-on-close
      :show-close="true"
      @closed="closeBookPreview"
    >
      <template #header>
        <div class="book-preview-heading">
          <strong>{{ previewDocument?.display_title }}</strong>
          <span>快速预览</span>
        </div>
      </template>

      <div class="book-preview-stage">
        <div v-if="previewLoading" class="book-preview-status">正在取出第 {{ previewPage }} 页…</div>
        <div v-else-if="previewError" class="book-preview-status is-error">
          <span>{{ previewError }}</span>
          <el-button text type="primary" @click="previewDocument && loadPreviewPage(previewDocument, previewPage)">
            重试
          </el-button>
        </div>
        <img
          v-else-if="previewImageUrl"
          class="book-preview-image"
          :src="previewImageUrl"
          :alt="`${previewDocument?.display_title ?? '图书'}第 ${previewPage} 页`"
        >
      </div>

      <template #footer>
        <div class="book-preview-footer">
          <div class="book-preview-pager">
            <el-button
              :disabled="previewLoading || previewPage <= 1"
              aria-keyshortcuts="ArrowLeft"
              @click="turnPreviewPage(-1)"
            >上一页</el-button>
            <span>第 {{ previewPage }} / {{ previewPageCount }} 页</span>
            <el-button
              :disabled="previewLoading || previewPage >= previewPageCount"
              aria-keyshortcuts="ArrowRight"
              @click="turnPreviewPage(1)"
            >
              下一页
            </el-button>
          </div>
          <el-button
            tag="a"
            type="primary"
            :href="previewStandaloneHref"
            target="_blank"
            rel="noopener noreferrer"
          >
            单独打开
          </el-button>
        </div>
      </template>
    </el-dialog>

    <SkillBookReaderDialog
      v-model="skillBookReaderVisible"
      @catalog-updated="handleSkillBookCatalogUpdated"
    />
  </div>
</template>

<style scoped>
.metadata-editor-form {
  display: grid;
  gap: 14px;
}

.metadata-editor-field {
  display: grid;
  grid-template-columns: 48px minmax(0, 1fr);
  align-items: center;
  gap: 10px;
}

.metadata-editor-field label {
  color: #4b5563;
  text-align: right;
  white-space: nowrap;
}

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

.library-bookshelves {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
  min-height: 32px;
  overflow-x: auto;
}

.library-bookshelf-tab {
  display: inline-flex;
  align-items: center;
  flex: 0 0 auto;
  height: 30px;
  border-radius: 6px;
  color: #596778;
}

.library-bookshelf-tab:hover {
  background: #f0f3f7;
  color: #25364d;
}

.library-bookshelf-tab.active {
  background: #e9f2ff;
  color: #1f5fbe;
}

.library-bookshelf-select,
.library-bookshelf-add {
  display: inline-grid;
  place-items: center;
  height: 30px;
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
}

.library-bookshelf-select {
  min-width: 34px;
  max-width: 180px;
  padding: 0 10px;
  font-size: 14px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.library-bookshelf-add {
  flex: 0 0 auto;
  width: 30px;
  border: 1px dashed #b9c4d1;
  border-radius: 6px;
  color: #617085;
  font-size: 15px;
}

.library-bookshelf-add:hover {
  border-color: #6e9ee9;
  background: #edf4ff;
  color: #1f5fbe;
}

.library-header {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
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

.pdf-file-input {
  display: none;
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
  position: relative;
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid #dfe7f0;
  border-radius: 8px;
  background: #fff;
  overflow: hidden;
}

.pdf-drop-indicator {
  position: absolute;
  z-index: 100;
  inset: 8px;
  display: grid;
  place-items: start center;
  padding-top: 18px;
  border: 2px dashed #2f6fd6;
  border-radius: 6px;
  background: rgb(237 244 255 / 20%);
  color: #1f5fbe;
  font-size: 16px;
  font-weight: 700;
  pointer-events: none;
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
  align-content: start;
  grid-auto-rows: 312px;
  row-gap: 0;
  width: max-content;
  min-width: 100%;
  min-height: 100%;
  padding: 0 24px 12px;
  background: #e9e4dc;
}

.bookshelf-row {
  position: relative;
  box-sizing: border-box;
  display: flex;
  align-items: flex-end;
  gap: 3px;
  width: max-content;
  min-width: 100%;
  height: 312px;
  padding: 0 0 12px;
  transition: background-color 120ms ease;
}

.bookshelf-row::after {
  position: absolute;
  z-index: 0;
  right: 0;
  bottom: 8px;
  left: 0;
  height: 6px;
  background: #b9aa96;
  content: '';
  pointer-events: none;
}

.bookshelf-row.drag-target {
  background-color: rgb(47 111 214 / 6%);
}

.book-group {
  display: flex;
  flex: 0 0 auto;
  align-items: flex-end;
  height: 300px;
}

.book-group.horizontal-book-stack {
  flex-direction: column-reverse;
  align-items: center;
  align-self: flex-end;
  height: auto;
}

.horizontal-book-stack .book-item.orientation-spine-horizontal {
  height: var(--spine-width);
}

.book-item {
  position: relative;
  z-index: 1;
  box-sizing: border-box;
  display: flex;
  align-items: flex-end;
  width: var(--book-item-width, var(--spine-width));
  height: 300px;
  color: inherit;
  border: 0;
  background: transparent;
  padding: 0;
  text-decoration: none;
  cursor: grab;
  touch-action: none;
  user-select: none;
}

.book-item.dragging {
  z-index: 20;
  opacity: 0.9;
  transform: translate3d(var(--book-drag-x, 0), var(--book-drag-y, 0), 0);
  pointer-events: none;
  will-change: transform;
}

.book-item:active {
  cursor: grabbing;
}

.skill-book-item {
  cursor: pointer;
}

.skill-book-item .book-spine {
  border-color: rgb(20 42 36 / 28%);
}

.skill-book-item .book-spine-title {
  flex: none;
}

.skill-book-title-button {
  border: 0;
  background: transparent;
  padding: 0;
}

.book-item.insert-before {
  border-left: 3px solid #2f6fd6;
}

.book-spine {
  position: relative;
  z-index: 2;
  box-sizing: border-box;
  display: flex;
  align-items: center;
  width: 100%;
  height: var(--spine-height);
  padding: 10px 5px 8px;
  border: 1px solid rgb(26 31 37 / 15%);
  border-radius: 3px 3px 1px 1px;
  background: var(--book-cover-color, var(--book-fallback-color, #3d6383));
  color: var(--book-cover-ink, #fff);
  transform-origin: bottom center;
  transform: rotateZ(var(--book-lean, 0deg));
  transition: transform 140ms ease;
}

.book-progress-bookmark {
  position: absolute;
  z-index: 1;
  bottom: calc(var(--spine-height) - 7px);
  left: clamp(6px, var(--book-reading-progress, 0%), calc(100% - 10px));
  width: 2px;
  height: 13px;
  border: 0;
  border-radius: 1px 1px 0 0;
  background: #c43b35;
  transform: rotateZ(var(--book-bookmark-tilt, 0deg));
  transform-origin: center bottom;
  transition: transform 140ms ease;
  pointer-events: none;
}

.book-item.orientation-spine-horizontal .book-progress-bookmark {
  right: auto;
  bottom: clamp(6px, var(--book-reading-progress, 0%), calc(100% - 8px));
  left: -6px;
  width: 13px;
  height: 2px;
  border-radius: 1px 0 0 1px;
  transform-origin: right center;
}

.book-item.orientation-cover-front .book-progress-bookmark {
  bottom: calc(var(--cover-flat-height) - 6px);
  left: clamp(
    calc(3% + 9px),
    var(--book-cover-bookmark-progress, 0%),
    calc(97% - 9px)
  );
  width: 18px;
  height: 12px;
  border-radius: 2px 2px 0 0;
  transform: translateX(-50%);
}

.book-item:hover .book-progress-bookmark,
.book-item:focus-visible .book-progress-bookmark {
  transform: translateY(-5px) rotateZ(var(--book-bookmark-tilt, 0deg));
}

.book-item.orientation-cover-front:hover .book-progress-bookmark,
.book-item.orientation-cover-front:focus-visible .book-progress-bookmark {
  transform: translateX(-50%);
}

.book-item:hover .book-spine,
.book-item:focus-visible .book-spine {
  transform: translateY(-5px) rotateZ(var(--book-lean, 0deg));
}

.book-item:focus-visible {
  outline: none;
}

.book-item:focus-visible .book-spine {
  outline: 2px solid #2f6fd6;
  outline-offset: 2px;
}

.book-spine-title {
  position: relative;
  z-index: 3;
  display: var(--book-title-display, block);
  flex: none;
  max-height: 100%;
  color: color-mix(in srgb, var(--book-cover-ink, #fff) 92%, transparent);
  font-size: var(--spine-font-size, 12px);
  font-weight: 700;
  line-height: 1.35;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  writing-mode: vertical-lr;
  text-orientation: upright;
}

.book-item.orientation-spine-vertical .book-spine-title {
  text-overflow: clip;
}

.book-spine-title-token.is-combined {
  text-combine-upright: all;
}

.book-item.orientation-spine-vertical .book-spine-title-token.is-compact-vertical-token {
  margin-inline-end: 0.22em;
  letter-spacing: -0.22em;
}

.book-spine-qualifier {
  position: relative;
  z-index: 3;
  flex: none;
  margin-left: 3px;
  color: color-mix(in srgb, var(--book-cover-ink, #fff) 82%, transparent);
  font-size: var(--book-qualifier-font-size, 11px);
  font-weight: 600;
  line-height: 1.2;
  white-space: nowrap;
  writing-mode: vertical-lr;
  text-orientation: upright;
}

.book-spine-author {
  position: relative;
  z-index: 3;
  display: var(--book-author-display, none);
  flex: none;
  margin-left: 2px;
  color: color-mix(in srgb, var(--book-cover-ink, #fff) 78%, transparent);
  font-size: var(--book-author-font-size, 11px);
  font-weight: 500;
  line-height: 1.2;
  writing-mode: vertical-lr;
  text-orientation: upright;
}

.book-item.orientation-spine-horizontal .book-spine {
  flex-direction: column;
  justify-content: center;
  gap: 3px;
  width: var(--spine-height);
  height: var(--spine-width);
  padding: 7px 12px;
  border-radius: 3px 2px 2px 3px;
}

.book-item.orientation-spine-horizontal .book-spine-title {
  width: auto;
  max-height: none;
  overflow: hidden;
  text-align: center;
  text-overflow: ellipsis;
  white-space: nowrap;
  writing-mode: horizontal-tb;
}

.book-item.orientation-spine-horizontal .book-spine-author {
  max-width: 100%;
  margin: 0;
  overflow: hidden;
  text-align: center;
  text-overflow: ellipsis;
  white-space: nowrap;
  writing-mode: horizontal-tb;
}

.book-item.orientation-spine-horizontal .book-spine-qualifier,
.book-item.orientation-cover-front .book-spine-qualifier {
  display: none;
}

.book-item.orientation-cover-front .book-spine {
  flex-direction: column;
  justify-content: center;
  width: var(--page-depth);
  height: var(--cover-flat-height);
  padding: 20px 16px;
  border-width: 1px;
  border-radius: 2px;
  background-image: var(--book-cover-image, none);
  background-position: center;
  background-size: cover;
  clip-path: polygon(3% 0, 97% 0, 100% 100%, 0 100%);
  transform: none;
}

@media (prefers-reduced-motion: reduce) {
  .book-spine {
    transition: none;
  }
}

.book-item.orientation-cover-front .book-spine-title {
  width: 100%;
  max-height: 100%;
  color: color-mix(in srgb, var(--book-cover-ink, #fff) 94%, transparent);
  font-size: var(--cover-font-size, 18px);
  line-height: 1.45;
  overflow: hidden;
  text-align: center;
  writing-mode: horizontal-tb;
}

.book-item.orientation-cover-front .book-spine-author {
  margin: 8px 0 0;
  writing-mode: horizontal-tb;
}

.book-item.orientation-cover-front.has-cover-image .book-spine-title,
.book-item.orientation-cover-front.has-cover-image .book-spine-author {
  visibility: hidden;
}

.book-context-menu {
  position: fixed;
  z-index: 3000;
  box-sizing: border-box;
  width: 176px;
  padding: 4px;
  border: 1px solid #d9e0e8;
  border-radius: 6px;
  background: #fff;
}

.bookshelf-context-menu {
  width: 128px;
}

.book-context-menu-separator {
  height: 1px;
  margin: 4px 6px;
  background: #e4e9ef;
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

.book-context-menu button.danger {
  color: #c43c3c;
}

.book-context-menu button.danger:hover,
.book-context-menu button.danger:focus-visible {
  background: #fff1f0;
  color: #b42318;
}

.tone-0 { --book-fallback-color: #315c78; }
.tone-1 { --book-fallback-color: #705b46; }
.tone-2 { --book-fallback-color: #41685b; }
.tone-3 { --book-fallback-color: #665579; }
.tone-4 { --book-fallback-color: #87554f; }
.tone-5 { --book-fallback-color: #485f86; }

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

:global(.book-preview-dialog) {
  margin: max(16px, 2vh) auto;
  border-radius: 10px;
  overflow: hidden;
}

:global(.book-preview-dialog .el-dialog__header) {
  margin: 0;
  padding: 14px 18px;
  border-bottom: 1px solid #e5e9ef;
}

:global(.book-preview-dialog .el-dialog__body) {
  padding: 0;
}

:global(.book-preview-dialog .el-dialog__footer) {
  padding: 12px 18px;
  border-top: 1px solid #e5e9ef;
}

.book-preview-heading {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
  padding-right: 32px;
}

.book-preview-heading strong {
  overflow: hidden;
  color: #172033;
  font-size: 16px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.book-preview-heading span {
  flex: 0 0 auto;
  color: #8a96a8;
  font-size: 12px;
}

.book-preview-stage {
  box-sizing: border-box;
  display: grid;
  place-items: center;
  width: 100%;
  height: auto;
  min-height: min(360px, calc(100vh - 190px));
  max-height: calc(100dvh - 180px);
  aspect-ratio: var(--preview-page-aspect-ratio, 612 / 792);
  padding: 18px;
  background: #eef1f4;
  overflow: hidden;
}

.book-preview-image {
  display: block;
  min-width: 0;
  min-height: 0;
  max-width: 100%;
  max-height: 100%;
  background: #fff;
  object-fit: contain;
}

.book-preview-status {
  display: flex;
  align-items: center;
  gap: 4px;
  color: #657286;
  font-size: 14px;
}

.book-preview-status.is-error {
  color: #9b4d4d;
}

.book-preview-footer,
.book-preview-pager {
  display: flex;
  align-items: center;
}

.book-preview-footer {
  justify-content: space-between;
  gap: 16px;
}

.book-preview-pager {
  gap: 12px;
}

.book-preview-pager span {
  min-width: 96px;
  color: #526071;
  font-size: 13px;
  text-align: center;
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
