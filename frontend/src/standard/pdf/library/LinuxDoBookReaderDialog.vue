<script setup lang="ts">
import axios from 'axios'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import RichTextDocumentReader from '@/components/rich-text/RichTextDocumentReader.vue'
import RichTextOutlineNav from '@/components/rich-text/RichTextOutlineNav.vue'
import {
  extractRichTextOutline,
  type RichTextDocument,
  type RichTextSelection,
} from '@/components/rich-text/document'
import {
  fetchEditableEbookSource,
  fetchLinuxDoBook,
  fetchElectronicBookResource,
  fetchLinuxDoBookReadingState,
  updateHtmlBookArticle,
  updateEditableEbookSource,
  updateLinuxDoBookReadingState,
  type LinuxDoBookContent,
  type LinuxDoBookReadingState,
  type LinuxDoBookTocItem,
} from '@/api/linuxDoBooks'
import {
  DYNAMIC_BOOK_CHARACTERS_PER_PAGE,
  paginateRichTextHtml,
  type LogicalBookPage,
} from './logicalBookPagination'
import {
  normalizeRichTextFootnotes,
  renderRichTextPageFootnotes,
  type RichTextFootnoteDefinition,
} from './richTextFootnotes'
import ReaderThemeControl from './ReaderThemeControl.vue'
import { libraryReaderThemeClass } from './readerTheme'
import {
  createLibraryAnnotation,
  deleteLibraryAnnotation,
  fetchLibraryAnnotations,
  updateLibraryAnnotation,
  type LibraryAnnotation,
} from '@/api/libraryAnnotations'

const props = defineProps<{
  modelValue: boolean
  bookId: string
  logicalPageTargetCharacters?: number
  readingMode?: 'scroll' | 'paginated'
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  'reading-state-updated': [state: LinuxDoBookReadingState]
}>()
const book = ref<LinuxDoBookContent | null>(null)
const loading = ref(false)
const errorMessage = ref('')
const searchText = ref('')
const activeAnchor = ref('')
const activeHeadingId = ref('')
const viewportRef = ref<HTMLElement | null>(null)
const imagePreviewVisible = ref(false)
const imagePreviewUrl = ref('')
const editingArticleId = ref('')
const articleDraftHtml = ref('')
const articleSaving = ref(false)
const sourceEditing = ref(false)
const sourceDraft = ref('')
const sourceRevision = ref('')
const sourceFilename = ref('')
const sourceLoading = ref(false)
const renderedArticleHtml = ref('')
const articleHtmlCache = ref<Record<string, string>>({})
// Estimates remain metadata for book thickness and backward-compatible reading
// offsets. The reader itself always renders one article as continuous HTML.
const articleEstimateCache = ref<Record<string, LogicalBookPage[]>>({})
const articleToc = ref<LinuxDoBookTocItem[]>([])
const searchCorpus = ref<SearchArticle[]>([])
const searchQuery = ref('')
const searchHighlightQuery = ref('')
const annotations = ref<LibraryAnnotation[]>([])
const annotationsLoading = ref(false)
const standardReaderLayerRef = ref<HTMLElement | null>(null)
const activePageIndex = ref(0)
const activePageCount = ref(1)
const readerPageWidth = ref(820)
const readerPageHeight = ref(640)
const readerPageInset = ref(24)
const readerPageStep = ref(860)
const ebookResourceUrls = new Map<string, string>()
let articleRenderSequence = 0
let saveTimer: ReturnType<typeof setTimeout> | null = null
let searchTimer: ReturnType<typeof setTimeout> | null = null
let dialogSizePersistTimer: ReturnType<typeof setTimeout> | null = null
let dialogResizeObserver: ResizeObserver | null = null
let documentResizeObserver: ResizeObserver | null = null
let pendingRestoreCharacterOffset: number | null = null

interface SearchArticle {
  anchor: string
  title: string
  segments: string[]
}

interface FullTextSearchResult {
  anchor: string
  title: string
  matchOrdinal: number
  before: string
  match: string
  after: string
}

const SEARCH_RESULT_LIMIT = 200
const READER_DIALOG_SIZE_STORAGE_KEY = 'codeyun.library.reader-dialog-size'
const READER_DIALOG_VIEWPORT_GAP = 32
const MIN_READER_DIALOG_WIDTH = 720
const MIN_READER_DIALOG_HEIGHT = 520
const DEFAULT_READER_DIALOG_WIDTH = 1440
const DEFAULT_READER_DIALOG_HEIGHT = 1390
const READER_FONT_SIZE_STORAGE_KEY = 'codeyun.library.reader-font-size'
const DEFAULT_READER_FONT_SIZE = 15
const MIN_READER_FONT_SIZE = 12
const MAX_READER_FONT_SIZE = 24
const READER_PAGE_MAX_WIDTH = 820
const READER_PAGE_SIDE_PADDING = 24
const READER_PAGE_COLUMN_GAP = 40

interface ReaderDialogSize {
  width: number
  height: number
}

function clampReaderDialogSize(size: ReaderDialogSize): ReaderDialogSize {
  if (typeof window === 'undefined') return size
  const maximumWidth = Math.max(320, window.innerWidth - READER_DIALOG_VIEWPORT_GAP)
  const maximumHeight = Math.max(360, window.innerHeight - READER_DIALOG_VIEWPORT_GAP)
  return {
    width: Math.round(Math.min(
      maximumWidth,
      Math.max(Math.min(MIN_READER_DIALOG_WIDTH, maximumWidth), size.width),
    )),
    height: Math.round(Math.min(
      maximumHeight,
      Math.max(Math.min(MIN_READER_DIALOG_HEIGHT, maximumHeight), size.height),
    )),
  }
}

function defaultReaderDialogSize() {
  return clampReaderDialogSize({
    width: DEFAULT_READER_DIALOG_WIDTH,
    height: DEFAULT_READER_DIALOG_HEIGHT,
  })
}

function loadReaderDialogSize() {
  if (typeof window === 'undefined') return defaultReaderDialogSize()
  try {
    const stored = JSON.parse(
      window.localStorage.getItem(READER_DIALOG_SIZE_STORAGE_KEY) || 'null',
    ) as Partial<ReaderDialogSize> | null
    if (
      stored
      && Number.isFinite(stored.width)
      && Number.isFinite(stored.height)
    ) {
      return clampReaderDialogSize({
        width: Number(stored.width),
        height: Number(stored.height),
      })
    }
  } catch {
    window.localStorage.removeItem(READER_DIALOG_SIZE_STORAGE_KEY)
  }
  return defaultReaderDialogSize()
}

const readerDialogSize = ref(loadReaderDialogSize())

function persistReaderDialogSize() {
  window.localStorage.setItem(
    READER_DIALOG_SIZE_STORAGE_KEY,
    JSON.stringify(readerDialogSize.value),
  )
}

function scheduleReaderDialogSizePersist() {
  if (dialogSizePersistTimer) clearTimeout(dialogSizePersistTimer)
  dialogSizePersistTimer = setTimeout(() => {
    dialogSizePersistTimer = null
    persistReaderDialogSize()
  }, 120)
}

function disconnectDialogResizeObserver() {
  dialogResizeObserver?.disconnect()
  dialogResizeObserver = null
  documentResizeObserver?.disconnect()
  documentResizeObserver = null
}

function textOffsetForDomPosition(root: HTMLElement, node: Node, offset: number) {
  if (!root.contains(node)) return 0
  const range = root.ownerDocument.createRange()
  range.selectNodeContents(root)
  try {
    range.setEnd(node, offset)
  } catch {
    return 0
  }
  return range.toString().length
}

function attachDialogResizeObserver() {
  disconnectDialogResizeObserver()
  const dialog = document.querySelector<HTMLElement>('.linux-do-book-dialog')
  if (!dialog || typeof ResizeObserver === 'undefined') return
  dialogResizeObserver = new ResizeObserver(() => {
    const bounds = dialog.getBoundingClientRect()
    const nextSize = clampReaderDialogSize({
      width: bounds.width,
      height: bounds.height,
    })
    if (
      nextSize.width === readerDialogSize.value.width
      && nextSize.height === readerDialogSize.value.height
    ) return
    readerDialogSize.value = nextSize
    scheduleReaderDialogSizePersist()
    if (isPaginated.value) void refreshPagination()
  })
  dialogResizeObserver.observe(dialog)
  if (viewportRef.value) {
    documentResizeObserver = new ResizeObserver(() => {
      if (isPaginated.value) void refreshPagination()
    })
    documentResizeObserver.observe(viewportRef.value)
  }
}

function handleReaderViewportResize() {
  const nextSize = clampReaderDialogSize(readerDialogSize.value)
  if (
    nextSize.width === readerDialogSize.value.width
    && nextSize.height === readerDialogSize.value.height
  ) return
  readerDialogSize.value = nextSize
  persistReaderDialogSize()
  if (isPaginated.value) void refreshPagination()
}

function loadReaderFontSize() {
  if (typeof window === 'undefined') return DEFAULT_READER_FONT_SIZE
  const stored = Number.parseInt(window.localStorage.getItem(READER_FONT_SIZE_STORAGE_KEY) || '', 10)
  return Number.isFinite(stored)
    ? Math.min(MAX_READER_FONT_SIZE, Math.max(MIN_READER_FONT_SIZE, stored))
    : DEFAULT_READER_FONT_SIZE
}

const readerFontSize = ref(loadReaderFontSize())
const canDecreaseReaderFont = computed(() => readerFontSize.value > MIN_READER_FONT_SIZE)
const canIncreaseReaderFont = computed(() => readerFontSize.value < MAX_READER_FONT_SIZE)

async function adjustReaderFontSize(delta: number) {
  const nextSize = Math.min(
    MAX_READER_FONT_SIZE,
    Math.max(MIN_READER_FONT_SIZE, readerFontSize.value + delta),
  )
  if (nextSize === readerFontSize.value) return
  const visibleOffset = visibleContentOffset()
  readerFontSize.value = nextSize
  window.localStorage.setItem(READER_FONT_SIZE_STORAGE_KEY, String(nextSize))
  await nextTick()
  if (isPaginated.value) await refreshPagination(visibleOffset)
  else scrollToLocalCharacterOffset(visibleOffset)
}

function blobAsDataUrl(blob: Blob) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.addEventListener('load', () => resolve(String(reader.result || '')), { once: true })
    reader.addEventListener('error', () => reject(reader.error || new Error('电子书资源读取失败')), { once: true })
    reader.readAsDataURL(blob)
  })
}

const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})
const isHtmlBook = computed(() => book.value?.capabilities.edit_mode === 'html')
const isSourceEditableBook = computed(() => book.value?.capabilities.edit_mode === 'source')
const isArticleBook = computed(() => Boolean(book.value))
const isEditingArticle = computed(() => Boolean(editingArticleId.value))
const isEditingContent = computed(() => isEditingArticle.value || sourceEditing.value)
const isPaginated = computed(() => props.readingMode === 'paginated' && !isEditingContent.value)
const hasPreviousPage = computed(() => activePageIndex.value > 0)
const hasNextPage = computed(() => activePageIndex.value < activePageCount.value - 1)

function orderWeeklyToc(toc: LinuxDoBookTocItem[]) {
  const anchors = new Set(toc.map(item => item.anchor))
  const childrenByParent = new Map<string, LinuxDoBookTocItem[]>()
  const roots: Array<{ item: LinuxDoBookTocItem; index: number; issueNumber: number }> = []
  toc.forEach((item, index) => {
    if (item.parent_anchor && anchors.has(item.parent_anchor)) {
      const children = childrenByParent.get(item.parent_anchor) ?? []
      children.push(item)
      childrenByParent.set(item.parent_anchor, children)
      return
    }
    roots.push({
      item,
      index,
      issueNumber: Number(item.title.match(/^\s*(\d+)\b/)?.[1] ?? 0),
    })
  })
  roots.sort((left, right) => right.issueNumber - left.issueNumber || left.index - right.index)

  const ordered: LinuxDoBookTocItem[] = []
  const visited = new Set<string>()
  const appendBranch = (item: LinuxDoBookTocItem) => {
    if (visited.has(item.anchor)) return
    visited.add(item.anchor)
    ordered.push(item)
    for (const child of childrenByParent.get(item.anchor) ?? []) appendBranch(child)
  }
  roots.forEach(({ item }) => appendBranch(item))
  toc.forEach(item => appendBranch(item))
  return ordered
}

const displayedToc = computed(() => {
  const toc = articleToc.value
  const title = book.value?.title ?? ''
  if (!/科技爱好者周刊|科技周刊摘抄/.test(title)) return toc
  return orderWeeklyToc(toc)
})
const tocItemByAnchor = computed(() => new Map(articleToc.value.map(item => [item.anchor, item])))
const hasExplicitTocHierarchy = computed(() => articleToc.value.some(item => Boolean(item.parent_anchor)))

function tocDepth(item: LinuxDoBookTocItem) {
  if (!hasExplicitTocHierarchy.value) return Math.max(0, item.level - 2)
  let depth = 0
  let parentAnchor = item.parent_anchor
  const visited = new Set<string>()
  while (parentAnchor && !visited.has(parentAnchor)) {
    visited.add(parentAnchor)
    depth += 1
    parentAnchor = tocItemByAnchor.value.get(parentAnchor)?.parent_anchor ?? null
  }
  return depth
}

const fullTextSearch = computed(() => {
  const query = searchQuery.value.toLocaleLowerCase()
  const results: FullTextSearchResult[] = []
  let total = 0
  let chapterCount = 0
  if (!query) return { results, total, chapterCount }
  for (const article of searchCorpus.value) {
    let articleMatched = false
    let matchOrdinal = 0
    for (const segment of article.segments) {
      const normalized = segment.toLocaleLowerCase()
      let offset = 0
      while (offset <= normalized.length - query.length) {
        const index = normalized.indexOf(query, offset)
        if (index < 0) break
        articleMatched = true
        total += 1
        if (results.length < SEARCH_RESULT_LIMIT) {
          const contextStart = Math.max(0, index - 32)
          const contextEnd = Math.min(segment.length, index + query.length + 48)
          results.push({
            anchor: article.anchor,
            title: article.title,
            matchOrdinal,
            before: `${contextStart > 0 ? '…' : ''}${segment.slice(contextStart, index)}`.replace(/\s+/g, ' '),
            match: segment.slice(index, index + query.length),
            after: `${segment.slice(index + query.length, contextEnd)}${contextEnd < segment.length ? '…' : ''}`.replace(/\s+/g, ' '),
          })
        }
        matchOrdinal += 1
        offset = index + Math.max(1, query.length)
      }
    }
    if (articleMatched) chapterCount += 1
  }
  return { results, total, chapterCount }
})
const activeArticleHtml = computed(() => {
  if (!book.value) return ''
  const articleId = activeAnchor.value || displayedToc.value[0]?.anchor || ''
  return articleHtmlCache.value[articleId] ?? ''
})
const activeArticleText = computed(() => {
  if (!activeArticleHtml.value || typeof DOMParser === 'undefined') return ''
  return new DOMParser().parseFromString(activeArticleHtml.value, 'text/html').body.textContent || ''
})
const activeArticleIndex = computed(() => displayedToc.value.findIndex(
  item => item.anchor === activeAnchor.value,
))
const canonicalDocument = computed<RichTextDocument | null>(() => book.value ? ({
  id: book.value.id,
  title: book.value.title,
  content: renderedArticleHtml.value,
  format: 'html',
  revision: book.value.revision,
  capabilities: {
    canEdit: Boolean(book.value.capabilities.can_edit_content),
    canAnnotate: book.value.capabilities.can_annotate,
    canEditContent: Boolean(book.value.capabilities.can_edit_content),
    editMode: book.value.capabilities.edit_mode,
    sourcePolicy: book.value.capabilities.source_policy,
  },
}) : null)
const editingDocument = computed<RichTextDocument | null>(() => (
  book.value && isEditingArticle.value
    ? {
        id: `${book.value.id}:${editingArticleId.value}:editing`,
        title: activeArticleTitle.value,
        content: articleDraftHtml.value,
        format: 'html',
        revision: book.value.revision,
        capabilities: {
          canEdit: true,
          canAnnotate: false,
          canEditContent: true,
          editMode: 'html',
          sourcePolicy: book.value.capabilities.source_policy,
        },
      }
    : null
))
const outlineDocument = computed<RichTextDocument | null>(() => book.value ? ({
  id: `${book.value.id}:${activeAnchor.value}:outline`,
  title: activeArticleTitle.value,
  content: isEditingArticle.value ? articleDraftHtml.value : activeArticleHtml.value,
  format: 'html',
  revision: book.value.revision,
  capabilities: {
    canEdit: false,
    canAnnotate: false,
    canEditContent: false,
    editMode: null,
    sourcePolicy: book.value.capabilities.source_policy,
  },
}) : null)
const documentOutline = computed(() => extractRichTextOutline(outlineDocument.value))
const activeArticleTitle = computed(() => (
  displayedToc.value.find((item) => item.anchor === activeAnchor.value)?.title ?? ''
))

function calculateOffset() {
  const estimatedPages = articleEstimateCache.value[activeAnchor.value] ?? []
  const articleStart = estimatedPages[0]?.absoluteStartOffset ?? 0
  if (isPaginated.value) {
    const articleLength = estimatedPages.reduce((sum, page) => sum + page.characterCount, 0)
      || activeArticleText.value.length
    return articleStart + Math.round(
      articleLength * activePageIndex.value / Math.max(1, activePageCount.value),
    )
  }
  return articleStart + visibleContentOffset()
}

function persistPosition() {
  if (!book.value) return
  const currentArticle = Math.max(1, activeArticleIndex.value + 1)
  const articleCount = Math.max(1, displayedToc.value.length)
  void updateLinuxDoBookReadingState(book.value.id, {
    chapter_id: activeAnchor.value,
    character_offset: calculateOffset(),
    chapter_revision: book.value.revision,
    // These legacy fields now describe article position. Reading within an
    // article is represented only by character_offset, never visual pages.
    current_page: currentArticle,
    page_count: articleCount,
  })
    .then(state => emit('reading-state-updated', state))
    .catch((error) => console.warn('Failed to save imported book position:', error))
}

function handleScroll() {
  updateActiveHeading()
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(() => {
    saveTimer = null
    persistPosition()
  }, 220)
}

function standardReaderRoot() {
  return standardReaderLayerRef.value
    ?.querySelector<HTMLElement>('.rich-text-document-reader') ?? null
}

function readerRoot() {
  return standardReaderRoot()
}

function applyPageGeometry() {
  const viewport = viewportRef.value
  if (!viewport) return
  const width = Math.max(
    280,
    Math.min(READER_PAGE_MAX_WIDTH, viewport.clientWidth - READER_PAGE_SIDE_PADDING * 2),
  )
  readerPageWidth.value = width
  readerPageHeight.value = Math.max(240, viewport.clientHeight - 56)
  readerPageInset.value = Math.max(READER_PAGE_SIDE_PADDING, (viewport.clientWidth - width) / 2)
  readerPageStep.value = width + READER_PAGE_COLUMN_GAP
}

function pageIndexAtLocalOffset(offset: number) {
  const root = readerRoot()
  if (!root) return 0
  const targetOffset = Math.max(0, Math.min(activeArticleText.value.length, offset))
  const walker = root.ownerDocument.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  let consumed = 0
  while (walker.nextNode()) {
    const node = walker.currentNode as Text
    const nextConsumed = consumed + node.data.length
    if (targetOffset <= nextConsumed) {
      const range = root.ownerDocument.createRange()
      const characterOffset = Math.max(0, Math.min(node.data.length - 1, targetOffset - consumed))
      range.setStart(node, characterOffset)
      range.setEnd(node, Math.min(node.data.length, characterOffset + 1))
      const bounds = range.getClientRects()[0] ?? range.getBoundingClientRect()
      return Math.max(0, Math.floor(
        (bounds.left - root.getBoundingClientRect().left + 1) / Math.max(1, readerPageStep.value),
      ))
    }
    consumed = nextConsumed
  }
  return Math.max(0, activePageCount.value - 1)
}

async function refreshPagination(localOffset?: number) {
  if (!isPaginated.value) return
  applyPageGeometry()
  await nextTick()
  await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
  const root = readerRoot()
  const viewport = viewportRef.value
  if (!root || !viewport) return
  activePageCount.value = Math.max(
    1,
    Math.ceil((root.scrollWidth + READER_PAGE_COLUMN_GAP - 1) / readerPageStep.value),
  )
  if (localOffset != null) activePageIndex.value = pageIndexAtLocalOffset(localOffset)
  activePageIndex.value = Math.min(activePageIndex.value, activePageCount.value - 1)
  viewport.scrollTo({ left: activePageIndex.value * readerPageStep.value, top: 0, behavior: 'auto' })
  updateActiveHeading()
}

function elementPageIndex(element: HTMLElement) {
  const root = readerRoot()
  if (!root) return 0
  const left = element.getClientRects()[0]?.left ?? element.getBoundingClientRect().left
  return Math.max(0, Math.floor(
    (left - root.getBoundingClientRect().left + 1) / Math.max(1, readerPageStep.value),
  ))
}

function navigatePage(delta: number) {
  if (!isPaginated.value) return
  activePageIndex.value = Math.max(
    0,
    Math.min(activePageCount.value - 1, activePageIndex.value + Math.sign(delta)),
  )
  viewportRef.value?.scrollTo({
    left: activePageIndex.value * readerPageStep.value,
    top: 0,
    behavior: 'smooth',
  })
  updateActiveHeading()
  persistPosition()
}

function visibleContentOffset() {
  const viewport = viewportRef.value
  const root = readerRoot()
  if (!viewport || !root || typeof document === 'undefined') return 0
  const bounds = viewport.getBoundingClientRect()
  const documentWithCaret = document as Document & {
    caretRangeFromPoint?: (x: number, y: number) => Range | null
  }
  const rootBounds = root.getBoundingClientRect()
  const xPositions = isPaginated.value
    ? [bounds.left + readerPageInset.value + 8, bounds.left + readerPageInset.value + readerPageWidth.value * 0.35]
    : [rootBounds.left + 8, rootBounds.left + rootBounds.width * 0.35]
  for (let y = bounds.top + 8; y < bounds.bottom - 6; y += 10) {
    for (const x of xPositions) {
      const caretPosition = document.caretPositionFromPoint?.(x, y)
      const caretRange = caretPosition ? null : documentWithCaret.caretRangeFromPoint?.(x, y)
      const node = caretPosition?.offsetNode ?? caretRange?.startContainer
      const offset = caretPosition?.offset ?? caretRange?.startOffset ?? 0
      if (node && root.contains(node)) {
        return textOffsetForDomPosition(root, node, offset)
      }
    }
  }
  const maxScrollTop = Math.max(1, viewport.scrollHeight - viewport.clientHeight)
  return Math.round(activeArticleText.value.length * viewport.scrollTop / maxScrollTop)
}

function scrollToLocalCharacterOffset(offset: number) {
  const viewport = viewportRef.value
  const root = readerRoot()
  if (!viewport || !root) return
  const targetOffset = Math.max(0, Math.min(activeArticleText.value.length, offset))
  const walker = root.ownerDocument.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  let consumed = 0
  while (walker.nextNode()) {
    const node = walker.currentNode as Text
    const nextConsumed = consumed + node.data.length
    if (targetOffset <= nextConsumed) {
      const range = root.ownerDocument.createRange()
      range.setStart(node, Math.max(0, targetOffset - consumed))
      range.collapse(true)
      const targetRect = range.getBoundingClientRect()
      const viewportRect = viewport.getBoundingClientRect()
      viewport.scrollTo({
        top: Math.max(0, viewport.scrollTop + targetRect.top - viewportRect.top - 8),
        behavior: 'auto',
      })
      return
    }
    consumed = nextConsumed
  }
  viewport.scrollTo({ top: viewport.scrollHeight, behavior: 'auto' })
}

function restorePendingPosition() {
  if (pendingRestoreCharacterOffset == null) return
  const estimatedPages = articleEstimateCache.value[activeAnchor.value] ?? []
  const articleStart = estimatedPages[0]?.absoluteStartOffset ?? 0
  const absoluteOffset = pendingRestoreCharacterOffset < articleStart
    ? articleStart + pendingRestoreCharacterOffset
    : pendingRestoreCharacterOffset
  pendingRestoreCharacterOffset = null
  const localOffset = Math.max(0, absoluteOffset - articleStart)
  if (isPaginated.value) void refreshPagination(localOffset)
  else scrollToLocalCharacterOffset(localOffset)
}

function updateActiveHeading() {
  const viewport = viewportRef.value
  if (!viewport || !documentOutline.value.length) {
    activeHeadingId.value = ''
    return
  }
  let activeId = documentOutline.value[0]?.id ?? ''
  const viewportTop = viewport.getBoundingClientRect().top
  for (const item of documentOutline.value) {
    const heading = viewport.querySelector<HTMLElement>(`#${CSS.escape(item.id)}`)
    if (!heading) continue
    if (
      isPaginated.value
        ? elementPageIndex(heading) <= activePageIndex.value
        : heading.getBoundingClientRect().top <= viewportTop + 20
    ) {
      activeId = item.id
    } else {
      break
    }
  }
  activeHeadingId.value = activeId
}

async function navigateToHeading(headingId: string) {
  const viewport = viewportRef.value
  const heading = viewport?.querySelector<HTMLElement>(`#${CSS.escape(headingId)}`)
  if (!viewport || !heading) return
  if (isPaginated.value) {
    activePageIndex.value = Math.min(elementPageIndex(heading), activePageCount.value - 1)
    viewport.scrollTo({
      left: activePageIndex.value * readerPageStep.value,
      top: 0,
      behavior: 'smooth',
    })
    activeHeadingId.value = headingId
    persistPosition()
    return
  }
  const viewportRect = viewport.getBoundingClientRect()
  viewport.scrollTo({
    top: Math.max(0, viewport.scrollTop + heading.getBoundingClientRect().top - viewportRect.top - 12),
    behavior: 'smooth',
  })
  activeHeadingId.value = headingId
  persistPosition()
}

async function navigateTo(anchor: string) {
  if (isEditingContent.value && anchor !== editingArticleId.value) {
    ElMessage.warning('请先保存或取消当前编辑')
    return
  }
  if (saveTimer) {
    clearTimeout(saveTimer)
    saveTimer = null
  }
  // 目录切换是一次新的阅读起点。先同步归零，避免新内容沿用旧文章的
  // scrollTop，或从旧位置平滑滚动时把中间位置误存成新文章进度。
  viewportRef.value?.scrollTo({ left: 0, top: 0, behavior: 'auto' })
  searchHighlightQuery.value = ''
  activeAnchor.value = anchor
  activePageIndex.value = 0
  activeHeadingId.value = ''
  await renderActiveArticle()
  await nextTick()
  const viewport = viewportRef.value
  if (!viewport) return
  viewport.scrollTo({ left: 0, top: 0, behavior: 'auto' })
  updateActiveHeading()
  persistPosition()
}

function applySearchHighlights(root: HTMLElement, query: string) {
  if (!query || typeof NodeFilter === 'undefined') return
  const textNodes: Text[] = []
  const walker = root.ownerDocument.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  while (walker.nextNode()) {
    const node = walker.currentNode
    if (node instanceof Text && node.data) textNodes.push(node)
  }
  let matchOrdinal = 0
  for (const node of textNodes) {
    const source = node.data
    const normalized = source.toLocaleLowerCase()
    const loweredQuery = query.toLocaleLowerCase()
    const matches: number[] = []
    let offset = 0
    while (offset <= normalized.length - loweredQuery.length) {
      const index = normalized.indexOf(loweredQuery, offset)
      if (index < 0) break
      matches.push(index)
      offset = index + Math.max(1, loweredQuery.length)
    }
    if (!matches.length) continue
    const fragment = root.ownerDocument.createDocumentFragment()
    let cursor = 0
    for (const index of matches) {
      if (index > cursor) fragment.append(source.slice(cursor, index))
      const mark = root.ownerDocument.createElement('mark')
      mark.className = 'book-search-hit'
      mark.dataset.searchOrdinal = String(matchOrdinal)
      mark.tabIndex = -1
      mark.textContent = source.slice(index, index + query.length)
      fragment.append(mark)
      cursor = index + query.length
      matchOrdinal += 1
    }
    if (cursor < source.length) fragment.append(source.slice(cursor))
    node.replaceWith(fragment)
  }
}

async function renderActiveArticle() {
  const sourceHtml = activeArticleHtml.value
  const sequence = ++articleRenderSequence
  if (!book.value || typeof DOMParser === 'undefined') {
    renderedArticleHtml.value = sourceHtml
    return
  }
  const parsed = new DOMParser().parseFromString(sourceHtml, 'text/html')
  if (book.value.book_kind === 'ebook') {
    const nodes = Array.from(parsed.querySelectorAll<HTMLElement>('[src], [href], image'))
    await Promise.all(nodes.map(async (node) => {
      const attribute = node.hasAttribute('src')
        ? 'src'
        : node.hasAttribute('href')
          ? 'href'
          : 'xlink:href'
      const sourceUrl = node.getAttribute(attribute) || ''
      if (!sourceUrl.startsWith('/api/linux-do-books/')) return
      let objectUrl = ebookResourceUrls.get(sourceUrl)
      if (!objectUrl) {
        try {
          const blob = await fetchElectronicBookResource(sourceUrl)
          objectUrl = await blobAsDataUrl(blob)
          ebookResourceUrls.set(sourceUrl, objectUrl)
        } catch (error) {
          console.warn('Failed to load EPUB resource:', sourceUrl, error)
          return
        }
      }
      node.setAttribute(attribute, objectUrl)
    }))
  }
  if (searchHighlightQuery.value) {
    applySearchHighlights(parsed.body, searchHighlightQuery.value)
  }
  if (sequence === articleRenderSequence) {
    renderedArticleHtml.value = parsed.body.innerHTML
    await nextTick()
    const hadPendingPosition = pendingRestoreCharacterOffset != null
    restorePendingPosition()
    if (isPaginated.value && !hadPendingPosition) await refreshPagination()
    updateActiveHeading()
  }
}

function ensureBookHeadingIds(html: string, scope: string) {
  const parsed = new DOMParser().parseFromString(html, 'text/html')
  const usedIds = new Set(
    Array.from(parsed.querySelectorAll<HTMLElement>('[id]'))
      .map(element => element.id)
      .filter(Boolean),
  )
  const safeScope = scope.replace(/[^a-zA-Z0-9_-]+/g, '-') || 'book'
  Array.from(parsed.querySelectorAll<HTMLElement>('h1, h2, h3, h4, h5, h6'))
    .forEach((heading, index) => {
      if (heading.id) return
      const baseId = `${safeScope}-heading-${index + 1}`
      let headingId = baseId
      let suffix = 2
      while (usedIds.has(headingId)) {
        headingId = `${baseId}-${suffix}`
        suffix += 1
      }
      heading.id = headingId
      usedIds.add(headingId)
    })
  return parsed.body.innerHTML
}

function appendDocumentFootnotes(
  html: string,
  footnotes: Record<string, RichTextFootnoteDefinition>,
) {
  const parsed = new DOMParser().parseFromString(html, 'text/html')
  const orderedFootnoteIds: string[] = []
  const firstReferenceIds: Record<string, string> = {}
  for (const reference of Array.from(parsed.querySelectorAll<HTMLElement>('[data-footnote-id]'))) {
    const footnoteId = reference.dataset.footnoteId || ''
    if (!footnoteId || !footnotes[footnoteId]) continue
    if (!orderedFootnoteIds.includes(footnoteId)) orderedFootnoteIds.push(footnoteId)
    if (reference.id && !firstReferenceIds[footnoteId]) {
      firstReferenceIds[footnoteId] = reference.id
    }
  }
  return html + renderRichTextPageFootnotes(
    orderedFootnoteIds.map(id => footnotes[id]).filter(Boolean),
    firstReferenceIds,
  )
}

function buildBookIndex(nextBook: LinuxDoBookContent) {
  if (typeof DOMParser === 'undefined') {
    articleHtmlCache.value = {}
    articleEstimateCache.value = {}
    articleToc.value = []
    searchCorpus.value = []
    return
  }
  const titles = new Map(nextBook.toc.map(item => [item.anchor, item.title]))
  const htmlCache: Record<string, string> = {}
  const pageCache: Record<string, LogicalBookPage[]> = {}
  const nextArticleToc: LinuxDoBookTocItem[] = []
  const corpus: SearchArticle[] = []
  const parsed = new DOMParser().parseFromString(nextBook.content_html, 'text/html')
  const articles = Array.from(parsed.querySelectorAll<HTMLElement>('article[data-article-id]'))
  let absoluteOffset = 0
  if (articles.length) {
    for (const [articleIndex, article] of articles.entries()) {
      const anchor = article.dataset.articleId || ''
      if (!anchor) continue
      const articleSourceHtml = article.innerHTML.trim()
      const normalizedArticle = normalizeRichTextFootnotes(articleSourceHtml, anchor)
      const indexedArticleHtml = ensureBookHeadingIds(normalizedArticle.bodyHtml, anchor)
      const articleHeading = article.querySelector<HTMLElement>('h1, h2, h3, h4, h5, h6')
      const title = titles.get(anchor) || articleHeading?.textContent?.trim() || `文章 ${articleIndex + 1}`
      const sourceTocItem = nextBook.toc.find(item => item.anchor === anchor)
      nextArticleToc.push(sourceTocItem ?? {
        title,
        number: '',
        level: 1,
        anchor,
        parent_anchor: null,
        source_post_number: articleIndex + 1,
        inferred: false,
      })
      const pages = paginateRichTextHtml(
        indexedArticleHtml,
        props.logicalPageTargetCharacters ?? DYNAMIC_BOOK_CHARACTERS_PER_PAGE,
        normalizedArticle.footnotes,
      ).map(page => ({
        ...page,
        absoluteStartOffset: absoluteOffset + page.localStartOffset,
      }))
      pageCache[anchor] = pages
      htmlCache[anchor] = appendDocumentFootnotes(indexedArticleHtml, normalizedArticle.footnotes)
      absoluteOffset += pages.reduce((sum, page) => sum + page.characterCount, 0)

      const segments: SearchArticle['segments'] = []
      const searchDocument = new DOMParser().parseFromString(htmlCache[anchor], 'text/html')
      const walker = searchDocument.createTreeWalker(searchDocument.body, NodeFilter.SHOW_TEXT)
      while (walker.nextNode()) {
        const current = walker.currentNode
        if (current instanceof Text && current.data.trim()) {
          segments.push(current.data)
        }
      }
      corpus.push({ anchor, title, segments })
    }
  } else {
    const anchor = `article-${nextBook.topic_id || 'document'}`
    const sourceHtml = parsed.body.innerHTML.trim()
    const normalizedArticle = normalizeRichTextFootnotes(sourceHtml, anchor)
    const indexedArticleHtml = ensureBookHeadingIds(normalizedArticle.bodyHtml, anchor)
    nextArticleToc.push({
      title: nextBook.title,
      number: '',
      level: 1,
      anchor,
      parent_anchor: null,
      source_post_number: 1,
      inferred: false,
    })
    const pages = paginateRichTextHtml(
      indexedArticleHtml,
      props.logicalPageTargetCharacters ?? DYNAMIC_BOOK_CHARACTERS_PER_PAGE,
      normalizedArticle.footnotes,
    )
      .map(page => ({ ...page, absoluteStartOffset: page.localStartOffset }))
    pageCache[anchor] = pages
    htmlCache[anchor] = appendDocumentFootnotes(indexedArticleHtml, normalizedArticle.footnotes)
    const segments: SearchArticle['segments'] = []
    const searchDocument = new DOMParser().parseFromString(htmlCache[anchor], 'text/html')
    const walker = searchDocument.createTreeWalker(searchDocument.body, NodeFilter.SHOW_TEXT)
    while (walker.nextNode()) {
      const current = walker.currentNode
      if (current instanceof Text && current.data.trim()) {
        segments.push(current.data)
      }
    }
    corpus.push({ anchor, title: nextBook.title, segments })
  }
  articleHtmlCache.value = htmlCache
  articleEstimateCache.value = pageCache
  articleToc.value = nextArticleToc
  searchCorpus.value = corpus
}

function articleHtml(documentHtml: string, articleId: string) {
  if (!documentHtml || !articleId || typeof DOMParser === 'undefined') return ''
  const parsed = new DOMParser().parseFromString(documentHtml, 'text/html')
  const article = Array.from(parsed.querySelectorAll<HTMLElement>('article[data-article-id]'))
    .find(item => item.dataset.articleId === articleId)
  return article?.innerHTML.trim() || ''
}

async function navigateToSearchResult(result: FullTextSearchResult) {
  if (isEditingContent.value && result.anchor !== editingArticleId.value) {
    ElMessage.warning('请先保存或取消当前编辑')
    return
  }
  activeAnchor.value = result.anchor
  activeHeadingId.value = ''
  searchHighlightQuery.value = searchQuery.value
  await renderActiveArticle()
  await nextTick()
  const viewport = viewportRef.value
  const target = viewport?.querySelector<HTMLElement>(
    `mark.book-search-hit[data-search-ordinal="${result.matchOrdinal}"]`,
  )
  if (!viewport || !target) return
  if (isPaginated.value) {
    activePageIndex.value = Math.min(elementPageIndex(target), activePageCount.value - 1)
    viewport.scrollTo({
      left: activePageIndex.value * readerPageStep.value,
      top: 0,
      behavior: 'auto',
    })
    target.focus({ preventScroll: true })
    persistPosition()
    return
  }
  const viewportRect = viewport.getBoundingClientRect()
  viewport.scrollTo({
    top: Math.max(0, viewport.scrollTop + target.getBoundingClientRect().top - viewportRect.top - 20),
    behavior: 'auto',
  })
  target.focus({ preventScroll: true })
  persistPosition()
}

function openFirstSearchResult() {
  const result = fullTextSearch.value.results[0]
  if (result) void navigateToSearchResult(result)
}

function startArticleEditing() {
  if (!book.value || !isHtmlBook.value) return
  const articleId = activeAnchor.value || displayedToc.value[0]?.anchor || ''
  const html = articleHtml(book.value.content_html, articleId)
  if (!articleId || !html) {
    ElMessage.error('没有找到可编辑的文章')
    return
  }
  editingArticleId.value = articleId
  articleDraftHtml.value = html
}

function cancelArticleEditing() {
  editingArticleId.value = ''
  articleDraftHtml.value = ''
}

async function startSourceEditing() {
  if (!book.value || !isSourceEditableBook.value || sourceLoading.value) return
  sourceLoading.value = true
  try {
    const source = await fetchEditableEbookSource(book.value.id)
    sourceDraft.value = source.content
    sourceRevision.value = source.revision
    sourceFilename.value = source.filename
    sourceEditing.value = true
  } catch (error) {
    ElMessage.error(saveErrorMessage(error))
  } finally {
    sourceLoading.value = false
  }
}

function cancelSourceEditing() {
  sourceEditing.value = false
  sourceDraft.value = ''
  sourceRevision.value = ''
  sourceFilename.value = ''
}

function cancelContentEditing() {
  cancelArticleEditing()
  cancelSourceEditing()
}

function saveErrorMessage(error: unknown) {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail
    if (typeof detail === 'string' && detail) return detail
  }
  return error instanceof Error ? error.message : '保存失败'
}

async function loadAnnotations() {
  if (!book.value || !activeAnchor.value) {
    annotations.value = []
    return
  }
  annotationsLoading.value = true
  try {
    annotations.value = await fetchLibraryAnnotations(
      'rich-text',
      book.value.id,
      activeAnchor.value,
    )
  } catch (error) {
    console.warn('Failed to load library annotations:', error)
    annotations.value = []
  } finally {
    annotationsLoading.value = false
  }
}

async function handleAnnotationCreate(payload: {
  selection: RichTextSelection
  withComment: boolean
}) {
  if (!book.value || !activeAnchor.value || annotationsLoading.value) return
  let commentText = ''
  if (payload.withComment) {
    try {
      const result = await ElMessageBox.prompt('写下批注', '添加批注', {
        confirmButtonText: '保存',
        cancelButtonText: '取消',
        inputType: 'textarea',
      })
      commentText = result.value.trim()
    } catch {
      return
    }
  }
  try {
    const annotation = await createLibraryAnnotation({
      resource_type: 'rich-text',
      resource_id: book.value.id,
      chapter_id: activeAnchor.value,
      kind: commentText ? 'comment' : 'highlight',
      color: 'yellow',
      quote_text: payload.selection.quoteText,
      prefix_text: payload.selection.prefixText,
      suffix_text: payload.selection.suffixText,
      start_offset: payload.selection.startOffset,
      end_offset: payload.selection.endOffset,
      source_revision: book.value.revision,
      comment_text: commentText,
    })
    annotations.value = [...annotations.value, annotation]
  } catch (error) {
    ElMessage.error(saveErrorMessage(error))
  }
}

async function handleAnnotationActivate(annotation: LibraryAnnotation) {
  try {
    const result = await ElMessageBox.prompt('批注内容（留空则保留为纯高亮）', '编辑批注', {
      confirmButtonText: '保存',
      cancelButtonText: '取消',
      inputType: 'textarea',
      inputValue: annotation.comment_text,
    })
    const updated = await updateLibraryAnnotation(annotation.id, {
      comment_text: result.value,
    })
    annotations.value = annotations.value.map(item => item.id === updated.id ? updated : item)
  } catch {
    // 用户取消编辑时保持原批注。
  }
}

async function handleAnnotationDelete(annotation: LibraryAnnotation) {
  try {
    await ElMessageBox.confirm(
      annotation.comment_text ? '删除这条高亮和批注？' : '删除这处高亮？',
      '删除批注',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
    await deleteLibraryAnnotation(annotation.id)
    annotations.value = annotations.value.filter(item => item.id !== annotation.id)
  } catch {
    // 用户取消删除时保持原批注。
  }
}

async function saveArticle() {
  if (!book.value || !editingArticleId.value || articleSaving.value) return
  articleSaving.value = true
  try {
    const updated = await updateHtmlBookArticle(book.value.id, editingArticleId.value, {
      content_html: articleDraftHtml.value,
      revision: book.value.revision,
    })
    const savedArticleId = editingArticleId.value
    book.value = updated
    buildBookIndex(updated)
    activeAnchor.value = savedArticleId
    cancelArticleEditing()
    await renderActiveArticle()
    ElMessage.success('文章已保存')
  } catch (error) {
    ElMessage.error(saveErrorMessage(error))
  } finally {
    articleSaving.value = false
  }
}

async function saveSource() {
  if (!book.value || !sourceEditing.value || articleSaving.value) return
  articleSaving.value = true
  try {
    const updated = await updateEditableEbookSource(book.value.id, {
      content: sourceDraft.value,
      revision: sourceRevision.value,
    })
    book.value = updated
    buildBookIndex(updated)
    activeAnchor.value = displayedToc.value[0]?.anchor || ''
    cancelSourceEditing()
    await renderActiveArticle()
    await loadAnnotations()
    ElMessage.success('正文已保存并重新排版')
  } catch (error) {
    ElMessage.error(saveErrorMessage(error))
  } finally {
    articleSaving.value = false
  }
}

function handleDocumentLink(payload: { href: string; event: MouseEvent }) {
  const { href, event } = payload
  event.preventDefault()
  if (href.startsWith('#')) {
    let anchor = href.slice(1)
    try {
      anchor = decodeURIComponent(anchor)
    } catch {
      // Keep the literal fragment when it is not valid URI encoding.
    }
    const viewport = viewportRef.value
    const localTarget = viewport?.querySelector<HTMLElement>(`#${CSS.escape(anchor)}`)
    if (viewport && localTarget) {
      void navigateToHeading(anchor)
      return
    }
    void navigateTo(anchor)
    return
  }
  const target = event.target as HTMLElement | null
  if (target?.closest('.imported-book-image-link')) {
    event.stopPropagation()
    imagePreviewUrl.value = href
    imagePreviewVisible.value = true
    return
  }
  window.open(href, '_blank', 'noopener,noreferrer')
}

function openPreviewExternally() {
  if (!imagePreviewUrl.value) return
  window.open(imagePreviewUrl.value, '_blank', 'noopener,noreferrer')
}

function handleReaderKeydown(event: KeyboardEvent) {
  if (
    visible.value
    && isEditingArticle.value
    && (event.ctrlKey || event.metaKey)
    && event.key.toLocaleLowerCase() === 's'
  ) {
    event.preventDefault()
    void saveArticle()
    return
  }
  if (
    !visible.value
    || !isPaginated.value
    || imagePreviewVisible.value
    || event.ctrlKey
    || event.metaKey
    || event.altKey
  ) return
  const target = event.target as HTMLElement | null
  if (target?.closest('input, textarea, [contenteditable="true"]')) return
  if (event.key === 'ArrowLeft') {
    event.preventDefault()
    navigatePage(-1)
  } else if (event.key === 'ArrowRight') {
    event.preventDefault()
    navigatePage(1)
  }
}

async function loadBook() {
  if (!props.bookId) return
  loading.value = true
  errorMessage.value = ''
  try {
    const [nextBook, state] = await Promise.all([
      fetchLinuxDoBook(props.bookId),
      fetchLinuxDoBookReadingState(props.bookId),
    ])
    searchText.value = ''
    searchQuery.value = ''
    searchHighlightQuery.value = ''
    buildBookIndex(nextBook)
    book.value = nextBook
    const restoredAnchor = displayedToc.value.some(item => item.anchor === state.chapter_id)
      ? state.chapter_id
      : displayedToc.value[0]?.anchor || ''
    activeAnchor.value = restoredAnchor
    activePageIndex.value = 0
    pendingRestoreCharacterOffset = state.character_offset
    activeHeadingId.value = ''
    cancelContentEditing()
    await renderActiveArticle()
    await loadAnnotations()
  } catch (error) {
    console.warn('Failed to load imported LINUX DO book:', error)
    errorMessage.value = '电子书读取失败'
  } finally {
    loading.value = false
  }
}

watch(searchText, (value) => {
  if (searchTimer) clearTimeout(searchTimer)
  const nextQuery = value.trim()
  if (!nextQuery) {
    searchQuery.value = ''
    if (searchHighlightQuery.value) {
      searchHighlightQuery.value = ''
      void renderActiveArticle()
    }
    return
  }
  searchTimer = setTimeout(() => {
    searchTimer = null
    searchQuery.value = nextQuery
  }, 180)
})

watch(activeAnchor, () => {
  if (book.value) void loadAnnotations()
})

watch(() => props.readingMode, async () => {
  activePageIndex.value = 0
  activePageCount.value = 1
  await nextTick()
  viewportRef.value?.scrollTo({ left: 0, top: 0, behavior: 'auto' })
  if (isPaginated.value) await refreshPagination()
})

watch(() => [
  props.modelValue,
  props.bookId,
  props.logicalPageTargetCharacters,
] as const, ([isVisible]) => {
  if (isVisible) void loadBook()
  else {
    cancelContentEditing()
    persistPosition()
  }
}, { immediate: true })

onMounted(() => {
  window.addEventListener('keydown', handleReaderKeydown)
  window.addEventListener('resize', handleReaderViewportResize)
})

onBeforeUnmount(() => {
  persistPosition()
  disconnectDialogResizeObserver()
  if (saveTimer) clearTimeout(saveTimer)
  if (searchTimer) clearTimeout(searchTimer)
  if (dialogSizePersistTimer) clearTimeout(dialogSizePersistTimer)
  window.removeEventListener('keydown', handleReaderKeydown)
  window.removeEventListener('resize', handleReaderViewportResize)
  ebookResourceUrls.clear()
})
</script>

<template>
  <el-dialog
    v-model="visible"
    :class="['linux-do-book-dialog', 'library-reader-theme-dialog', libraryReaderThemeClass]"
    :width="`${readerDialogSize.width}px`"
    :style="{ height: `${readerDialogSize.height}px` }"
    append-to-body
    align-center
    destroy-on-close
    @opened="attachDialogResizeObserver"
    @closed="disconnectDialogResizeObserver"
  >
    <template #header>
      <div class="book-dialog-heading">
        <div class="book-dialog-title">
          <strong>{{ book?.title ?? (isArticleBook ? '电子书' : 'LINUX DO 电子书') }}</strong>
          <span v-if="book">
            {{ [book.author, book.start_date?.slice(0, 4)].filter(Boolean).join(' · ') }}
          </span>
        </div>
        <div class="book-dialog-actions">
          <div class="book-font-actions" role="group" aria-label="正文字体大小">
            <button
              type="button"
              class="book-font-button"
              :disabled="!canDecreaseReaderFont"
              :title="`减小正文字体（当前 ${readerFontSize}px）`"
              aria-label="减小正文字体"
              @click="adjustReaderFontSize(-1)"
            >
              A−
            </button>
            <button
              type="button"
              class="book-font-button"
              :disabled="!canIncreaseReaderFont"
              :title="`增大正文字体（当前 ${readerFontSize}px）`"
              aria-label="增大正文字体"
              @click="adjustReaderFontSize(1)"
            >
              A+
            </button>
          </div>
          <ReaderThemeControl />
        </div>
      </div>
    </template>

    <div
      class="book-reader"
      :class="{
        'has-page-outline': isArticleBook,
      }"
    >
      <aside class="book-toc" aria-label="目录">
        <el-input
          v-model="searchText"
          clearable
          placeholder="搜索全文"
          aria-label="搜索全文"
          @keyup.enter="openFirstSearchResult"
        />
        <nav v-if="!searchQuery" class="book-toc-list" :role="isArticleBook ? 'tree' : undefined">
          <button
            v-for="item in displayedToc"
            :key="item.anchor"
            type="button"
            class="book-toc-item"
            :class="{ active: activeAnchor === item.anchor, inferred: item.inferred, unnumbered: !item.number }"
            :style="{ '--toc-depth': String(tocDepth(item)) }"
            :role="isArticleBook ? 'treeitem' : undefined"
            :aria-level="isArticleBook ? tocDepth(item) + 1 : undefined"
            :title="item.title"
            @click="navigateTo(item.anchor)"
          >
            <span v-if="item.number" class="book-toc-number">{{ item.number }}</span>
            <span class="book-toc-title library-reader-single-line-title">{{ item.title }}</span>
          </button>
        </nav>
        <div v-else class="book-search-panel" aria-live="polite">
          <div class="book-search-summary">
            <span>{{ fullTextSearch.total }} 处 · {{ fullTextSearch.chapterCount }} 章</span>
            <span v-if="fullTextSearch.total > SEARCH_RESULT_LIMIT">
              显示前 {{ SEARCH_RESULT_LIMIT }} 处
            </span>
          </div>
          <div v-if="fullTextSearch.results.length" class="book-search-results">
            <button
              v-for="(result, index) in fullTextSearch.results"
              :key="`${result.anchor}-${result.matchOrdinal}-${index}`"
              type="button"
              class="book-search-result"
              @click="navigateToSearchResult(result)"
            >
              <strong>{{ result.title }}</strong>
              <span class="book-search-snippet">
                {{ result.before }}<mark>{{ result.match }}</mark>{{ result.after }}
              </span>
            </button>
          </div>
          <div v-else class="book-search-empty">没有找到相关正文</div>
        </div>
      </aside>

      <main class="book-content">
        <header v-if="isHtmlBook" class="book-toolbar html-book-toolbar">
          <el-button v-if="!isEditingArticle" type="primary" @click="startArticleEditing">编辑</el-button>
          <template v-else>
            <el-button :disabled="articleSaving" @click="cancelArticleEditing">取消</el-button>
            <el-button type="primary" :loading="articleSaving" @click="saveArticle">完成</el-button>
          </template>
        </header>
        <header v-if="isSourceEditableBook" class="book-toolbar html-book-toolbar">
          <span v-if="sourceEditing">{{ sourceFilename }} · 源格式编辑</span>
          <el-button
            v-if="!sourceEditing"
            type="primary"
            :loading="sourceLoading"
            @click="startSourceEditing"
          >
            编辑正文
          </el-button>
          <div v-else>
            <el-button :disabled="articleSaving" @click="cancelSourceEditing">取消</el-button>
            <el-button type="primary" :loading="articleSaving" @click="saveSource">保存</el-button>
          </div>
        </header>
        <div v-if="loading" class="reader-status">正在读取电子书…</div>
        <div v-else-if="errorMessage" class="reader-status is-error">
          <span>{{ errorMessage }}</span>
          <el-button text type="primary" @click="loadBook">重试</el-button>
        </div>
        <div
          v-else
          ref="viewportRef"
          class="book-document"
          :class="{ 'is-editing': isEditingContent, 'is-paginated': isPaginated }"
          :style="{
            '--reader-font-size': `${readerFontSize}px`,
            '--reader-page-width': `${readerPageWidth}px`,
            '--reader-page-height': `${readerPageHeight}px`,
            '--reader-page-inset': `${readerPageInset}px`,
            '--reader-page-column-gap': `${READER_PAGE_COLUMN_GAP}px`,
          }"
          @scroll.passive="handleScroll"
        >
          <div
            v-if="isEditingArticle"
            class="reader-standard-layer inline-editing-layer"
          >
            <RichTextDocumentReader
              :document="editingDocument"
              editable
              @content-change="articleDraftHtml = $event"
            />
          </div>
          <textarea
            v-else-if="sourceEditing"
            v-model="sourceDraft"
            class="ebook-source-editor"
            :aria-label="`编辑 ${sourceFilename} 正文`"
            spellcheck="false"
          />
          <template v-else>
            <div
              ref="standardReaderLayerRef"
              class="reader-standard-layer"
            >
              <RichTextDocumentReader
                :document="canonicalDocument"
                :annotations="annotations"
                :anchor-text="activeArticleText"
                @link-activate="handleDocumentLink"
                @annotation-create="handleAnnotationCreate"
                @annotation-activate="handleAnnotationActivate"
                @annotation-delete="handleAnnotationDelete"
              />
            </div>
          </template>
        </div>
        <footer
          v-if="!loading && !errorMessage && isPaginated"
          class="book-page-controls"
          aria-label="翻页控制"
        >
          <button type="button" :disabled="!hasPreviousPage" @click="navigatePage(-1)">上一页</button>
          <span class="book-page-status" aria-live="polite">
            <strong>{{ activePageIndex + 1 }}</strong>
            <span>/ {{ activePageCount }}</span>
          </span>
          <button type="button" :disabled="!hasNextPage" @click="navigatePage(1)">下一页</button>
        </footer>
      </main>

      <RichTextOutlineNav
        v-if="isArticleBook"
        :items="documentOutline"
        :active-id="activeHeadingId"
        :document-title="activeArticleTitle"
        heading="本章大纲"
        empty-text="本章没有下级标题"
        @select="navigateToHeading"
      />
    </div>
  </el-dialog>

  <el-dialog
    v-model="imagePreviewVisible"
    class="book-image-preview-dialog"
    width="min(94vw, 1280px)"
    append-to-body
    align-center
    destroy-on-close
  >
    <template #header>
      <strong>图片预览</strong>
    </template>
    <div class="book-image-preview">
      <img v-if="imagePreviewUrl" :src="imagePreviewUrl" alt="正文图片预览" />
    </div>
    <template #footer>
      <el-button @click="imagePreviewVisible = false">关闭</el-button>
      <el-button @click="openPreviewExternally">
        新窗口打开原图
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.book-dialog-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding-right: 34px; }
.book-dialog-title { display: flex; min-width: 0; align-items: baseline; gap: 10px; }
.book-dialog-actions { display: flex; flex: 0 0 auto; align-items: center; gap: 12px; }
.book-font-actions { display: inline-flex; gap: 2px; }
.book-font-button { min-width: 34px; border: 0; background: transparent; padding: 5px; color: var(--reader-text); font: inherit; font-size: 13px; font-weight: 600; cursor: pointer; }
.book-font-button:hover:not(:disabled) { color: var(--reader-active-text); }
.book-font-button:disabled { color: #bdc5cf; cursor: default; }
.book-dialog-heading strong { overflow: hidden; color: var(--reader-heading); font-size: 16px; text-overflow: ellipsis; white-space: nowrap; }
.book-dialog-heading span { flex: 0 0 auto; color: var(--reader-muted); font-size: 12px; }
:global(.linux-do-book-dialog) { position: relative; display: flex; min-width: min(720px, calc(100vw - 32px)); max-width: calc(100vw - 32px); min-height: min(520px, calc(100dvh - 32px)); max-height: calc(100dvh - 32px); flex-direction: column; resize: both; overflow: hidden; }
:global(.linux-do-book-dialog::after) { position: absolute; right: 3px; bottom: 3px; width: 12px; height: 12px; background: repeating-linear-gradient(135deg, transparent 0 3px, #aeb8c4 3px 4px); content: ''; pointer-events: none; }
:global(.linux-do-book-dialog .el-dialog__header) { flex: 0 0 auto; }
:global(.linux-do-book-dialog .el-dialog__body) { flex: 1; min-height: 0; overflow: hidden; }
.book-reader { display: grid; grid-template-columns: 290px minmax(0, 1fr); height: 100%; min-height: 0; border: 1px solid var(--reader-border); background: var(--reader-content); color: var(--reader-text); overflow: hidden; }
.book-reader.has-page-outline { grid-template-columns: 290px minmax(520px, 1fr) 220px; }
.book-toc { display: flex; flex-direction: column; min-height: 0; padding: 12px; border-right: 1px solid var(--reader-border); background: var(--reader-panel); overflow: hidden; }
.book-toc-list { flex: 1; min-height: 0; margin-top: 10px; overflow: auto; }
.book-toc-item { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 0.35em; width: 100%; min-height: 32px; align-items: baseline; border: 0; border-radius: 4px; background: transparent; padding: 6px 8px 6px calc(8px + var(--toc-depth) * 13px); color: var(--reader-text); font-size: 13px; line-height: 20px; text-align: left; cursor: pointer; }
.book-toc-item.unnumbered { grid-template-columns: minmax(0, 1fr); gap: 0; }
.book-toc-item:hover { background: var(--reader-hover); }
.book-toc-item.active { background: var(--reader-active); color: var(--reader-active-text); font-weight: 700; }
.book-toc-item.inferred { font-weight: 700; }
.book-toc-number { color: var(--reader-muted); font-variant-numeric: tabular-nums; white-space: nowrap; }
.book-toc-title { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.book-search-panel { display: flex; flex: 1; min-height: 0; flex-direction: column; margin-top: 10px; }
.book-search-summary { display: flex; flex: 0 0 auto; justify-content: space-between; gap: 8px; padding: 0 8px 8px; color: var(--reader-muted); font-size: 12px; }
.book-search-results { flex: 1; min-height: 0; overflow: auto; }
.book-search-result { display: block; width: 100%; border: 0; border-radius: 4px; background: transparent; padding: 7px 8px 8px; color: var(--reader-text); text-align: left; cursor: pointer; }
.book-search-result:hover { background: var(--reader-hover); }
.book-search-result strong { display: block; overflow: hidden; color: var(--reader-link); font-size: 12px; line-height: 18px; text-overflow: ellipsis; white-space: nowrap; }
.book-search-snippet { display: -webkit-box; margin-top: 2px; overflow: hidden; color: var(--reader-muted); font-size: 12px; line-height: 18px; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
.book-search-snippet mark,
.book-document :deep(mark.book-search-hit) { border-radius: 2px; background: var(--reader-mark); color: inherit; }
.book-document :deep(mark.book-search-hit:focus) { outline: 2px solid #e6a23c; outline-offset: 2px; }
.book-search-empty { padding: 24px 8px; color: var(--reader-muted); font-size: 13px; text-align: center; }
.book-content { display: flex; flex-direction: column; min-width: 0; min-height: 0; }
.book-toolbar { display: flex; min-height: 50px; align-items: center; justify-content: space-between; gap: 12px; padding: 0 18px; border-bottom: 1px solid var(--reader-border); color: var(--reader-muted); font-size: 12px; }
.html-book-toolbar { justify-content: flex-end; }
.book-document { position: relative; flex: 1; min-height: 0; padding: 28px 24px 64px; background: var(--reader-content); overflow: auto; }
.book-document.is-paginated { padding: 28px 0; overflow: hidden; }
.reader-standard-layer { display: contents; }
.book-page-controls { display: flex; flex: 0 0 48px; align-items: center; justify-content: center; gap: 18px; border-top: 1px solid var(--reader-border); background: var(--reader-surface); }
.book-page-controls button { min-width: 56px; border: 0; background: transparent; padding: 7px 8px; color: var(--reader-text); font: inherit; cursor: pointer; }
.book-page-controls button:hover:not(:disabled) { color: var(--reader-active-text); }
.book-page-controls button:disabled { color: #bdc5cf; cursor: default; }
.book-page-status { display: inline-flex; min-width: 72px; align-items: baseline; justify-content: center; gap: 5px; color: var(--reader-muted); font-size: 12px; font-variant-numeric: tabular-nums; }
.book-page-status strong { color: var(--reader-text); font-size: 13px; }
.inline-editing-layer { display: contents; }
.ebook-source-editor { box-sizing: border-box; width: 100%; height: 100%; resize: none; border: 0; outline: 0; background: var(--reader-content); padding: 24px 28px; color: var(--reader-text); font: 14px/1.7 ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace; tab-size: 2; }
.book-document :deep(.rich-text-document-reader) {
  width: min(100%, 820px);
  min-height: 100%;
  margin: 0 auto;
  padding-bottom: 36px;
  font-size: var(--reader-font-size, 15px);
}
.book-document.is-paginated :deep(.rich-text-document-reader) {
  width: var(--reader-page-width);
  height: var(--reader-page-height);
  min-height: 0;
  margin: 0 0 0 var(--reader-page-inset);
  padding-bottom: 0;
  column-fill: auto;
  column-gap: var(--reader-page-column-gap);
  column-width: var(--reader-page-width);
}
.book-document.is-editing :deep(.rich-text-document-reader) {
  min-height: calc(100% - 28px);
}
.book-document :deep(.rich-text-document-reader img:not(.duokan-footnote):not([alt="注"])) {
  display: block;
  max-width: 100%;
  max-height: min(70dvh, 720px);
  width: auto;
  height: auto;
  margin-right: auto;
  margin-left: auto;
  object-fit: contain;
}
.book-document :deep(.imported-book-title) { margin-bottom: 32px; text-align: center; }
.book-document :deep(.imported-book-byline), .book-document :deep(.imported-book-source) { color: var(--reader-muted); font-size: 12px; }
.book-document :deep(.selected-reply) { margin-top: 28px; padding-top: 4px; }
.book-document :deep(.x-entry) { border-bottom: 2px solid color-mix(in srgb, var(--reader-text) 28%, transparent) !important; }
.book-document :deep(.x-translation) { border-top-color: color-mix(in srgb, var(--reader-text) 12%, transparent) !important; }
.book-document :deep(.discussion-turn) { margin: 12px 0 18px; }
.book-document :deep(.discussion-speaker) { margin-bottom: 6px; color: var(--reader-text); font-size: 13px; }
.book-document :deep(.discussion-turn.is-question .discussion-speaker) { color: var(--reader-muted); }
.book-document :deep(.discussion-turn.is-answer) { padding-left: 14px; border-left: 3px solid #8fb4e3; }
.book-document :deep(.discussion-turn.is-answer > :last-child) { margin-bottom: 0; }
.book-document :deep(a.duokan-footnote img),
.book-document :deep(sup img[alt="注"]) { display: inline-block; width: auto; height: 1em; margin: 0 0.08em; vertical-align: 0.25em; }
.book-document :deep(video) { max-width: 100%; }
.reader-status { display: grid; place-items: center; flex: 1; color: var(--reader-muted); }
.reader-status.is-error { color: #9b4d4d; }
.book-image-preview { display: grid; place-items: center; max-height: calc(100dvh - 190px); overflow: auto; background: #f4f6f8; }
.book-image-preview img { display: block; max-width: 100%; height: auto; }
@media (max-width: 980px) {
  .book-dialog-heading { align-items: flex-start; flex-direction: column; gap: 10px; }
  .book-reader,
  .book-reader.has-page-outline { grid-template-columns: 1fr; grid-template-rows: minmax(140px, 28%) minmax(0, 1fr) minmax(100px, 20%); }
  .book-reader:not(.has-page-outline) { grid-template-rows: minmax(140px, 34%) minmax(0, 1fr); }
  .book-toc { border-right: 0; border-bottom: 1px solid #e4e9ef; }
  .book-reader :deep(.rich-text-outline) { border-top: 1px solid #e4e9ef; border-left: 0; }
}
</style>
