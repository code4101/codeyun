<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Grid, List, Plus, Search } from '@element-plus/icons-vue'

import {
  createPdfBookshelf,
  createLibraryFolder,
  copyPdfToOwnLibrary,
  deletePdfDocument,
  deletePdfBookshelf,
  deleteLibraryFolder,
  fetchLibraryFolders,
  fetchPdfBookshelfAccess,
  fetchPdfBookshelves,
  fetchPdfDocuments,
  fetchPdfPagePreview,
  leaveSharedPdfBookshelf,
  movePdfToBookshelf,
  movePdfToLibraryFolder,
  removePdfDocumentFromMyLibrary,
  updatePdfBookshelf,
  uploadPdfDocument,
  updatePdfBookshelfLayout,
  updateLibraryBookshelfLayout,
  updatePdfBookshelfAccess,
  updatePdfDocumentMetadata,
  updateLibraryFolder,
  updatePdfUserState,
  type PdfBookshelfOrientation,
  type PdfAccessGrantItem,
  type PdfAccessGrantUpdate,
  type PdfBookshelfPlacement,
  type PdfDocumentSummary,
  type PdfLibraryBookshelf,
  type LibraryFolder,
  type LibraryBookshelfLayoutItem,
  type PdfResourceRole,
} from '@/api/pdfDocuments'
import { useUserStore } from '@/store/userStore'
import type { AccountUserOption } from '@/api/accountUsers'
import AccountUserSelect from '@/components/AccountUserSelect.vue'
import {
  deleteLocalSkillBook,
  fetchLocalSkillBookCatalog,
  fetchLocalSkillBookReadingState,
  updateLocalSkillBookMetadata,
  updateLocalSkillBookPlacement,
  type SkillBookCatalog,
  type SkillBookReadingState,
} from '@/api/skillBooks'
import { getCachedPreviewPageUrl, loadPreviewPageBlock } from './previewPageCache'
import SkillBookReaderDialog from './SkillBookReaderDialog.vue'
import LinuxDoBookReaderDialog from './LinuxDoBookReaderDialog.vue'
import ReaderThemeControl from './ReaderThemeControl.vue'
import { libraryReaderThemeClass } from './readerTheme'
import {
  deleteLinuxDoBook,
  fetchLinuxDoBooks,
  uploadElectronicBook,
  updateLinuxDoBookMetadata,
  updateLinuxDoBookPlacement,
  type LinuxDoBookReadingState,
  type LinuxDoBookSummary,
} from '@/api/linuxDoBooks'
import {
  loadCommonSiteLogoBlob,
  loadCommonSites,
  saveCommonSites,
  type CommonSite,
} from './commonSites'

type PdfViewMode = 'bookshelf' | 'list'
type ArticleReadingMode = 'scroll' | 'paginated'
type ArticleReadingModeOverride = ArticleReadingMode | 'inherit'

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

interface ShelfContextMenuState {
  shelfIndex: number
  x: number
  y: number
  xRatio: number
  yRatio: number
}

interface SkillBookContextMenuState {
  x: number
  y: number
}

interface LinuxDoBookContextMenuState {
  bookId: string
  x: number
  y: number
}

interface BookshelfBookGroup {
  key: string
  kind: 'single' | 'horizontal-stack' | 'skill-book' | 'linux-do-book' | 'folder'
  documents: PdfDocumentSummary[]
  folder?: LibraryFolder
  linuxDoBook?: LinuxDoBookSummary
}

interface BookshelfPlacementItem extends LibraryBookshelfLayoutItem {
  key: string
  document?: PdfDocumentSummary
  linuxDoBook?: LinuxDoBookSummary
  folder?: LibraryFolder
  skillBook?: SkillBookCatalog
}

interface BookTitleSegment {
  text: string
  combined: boolean
}

interface ExternalPdfDropTarget {
  shelfIndex: number
  beforePdfId: number | null
}

interface WallSitePosition {
  shelfIndex: number
  xRatio: number
  yRatio: number
}

interface LegacyWallSitePosition {
  x: number
  y: number
}

interface WallSiteMarquee {
  shelfIndex: number
  startX: number
  startY: number
  currentX: number
  currentY: number
  active: boolean
}

const PDF_LIBRARY_VIEW_MODE_KEY = 'codeyun.pdf-library.view-mode'
const PDF_LIBRARY_BOOKSHELF_KEY_PREFIX = 'codeyun.pdf-library.bookshelf'
const BOOK_PAGE_SCALE = 0.32
const MIN_BOOK_HEIGHT = 190
const MAX_BOOK_HEIGHT = 286
const SYSTEM_MIN_SPINE_WIDTH = 16
const SYSTEM_REFERENCE_SPINE_WIDTH = SYSTEM_MIN_SPINE_WIDTH * 4
const MIN_BOOK_INTERACTION_WIDTH = 12
const MIN_SPINE_WIDTH_FOR_TITLE = 22
const MIN_SPINE_WIDTH_FOR_COMPACT_TITLE = 8
const MIN_COMPACT_SPINE_FONT_SIZE = 6
const MIN_SPINE_WIDTH_FOR_BOOKMARK = 14
const MIN_SPINE_FONT_SIZE = 12
const MAX_SPINE_FONT_SIZE = 36
const SPINE_VERTICAL_TEXT_HEIGHT_INSET = 20
const MAX_COMBINED_VERTICAL_TOKEN_LENGTH = 4
const COMPACT_VERTICAL_TOKEN_CELL_RATIO = 0.78
const MIN_TITLE_LENGTH_FOR_SEPARATE_QUALIFIER = 11
const DEFAULT_BOOKSHELF_ROW_COUNT = 1
const TRAILING_EMPTY_BOOKSHELF_ROW_COUNT = 1
const BOOKSHELF_ROW_GROWTH_COUNT = 3
const BOOKSHELF_HORIZONTAL_GROWTH_MIN = 480
const BOOKSHELF_EDGE_THRESHOLD = 72
const BOOKSHELF_DRAG_SCROLL_STEP = 24
const BOOKSHELF_EXPANSION_COOLDOWN_MS = 1200
const BOOK_ORIENTATION_CYCLE: PdfBookshelfOrientation[] = [
  'spine_vertical',
  'spine_horizontal',
  'cover_front',
]

const router = useRouter()
const userStore = useUserStore()

const loading = ref(true)
const bookshelves = ref<PdfLibraryBookshelf[]>([])
const ownedBookshelves = computed(() => bookshelves.value.filter((bookshelf) => bookshelf.is_owned))
const sharedBookshelves = computed(() => bookshelves.value.filter((bookshelf) => !bookshelf.is_owned))
const selectedBookshelfId = ref('')
const bookshelfScrollRef = ref<HTMLElement | null>(null)
const bookshelfCanvasMinWidth = ref(0)
const bookshelfCanvasMinRowCount = ref(DEFAULT_BOOKSHELF_ROW_COUNT)
const documents = ref<PdfDocumentSummary[]>([])
const searchText = ref('')
const viewMode = ref<PdfViewMode>('bookshelf')
const draggingPdfId = ref<number | null>(null)
const draggingSkillBook = ref(false)
const draggingLinuxDoBookId = ref<string | null>(null)
const dragOverPdfId = ref<number | null>(null)
const dragOverShelfIndex = ref<number | null>(null)
const dragOverPlacementKey = ref<string | null>(null)
const bookDragOffsetX = ref(0)
const bookDragOffsetY = ref(0)
const externalFileDragActive = ref(false)
const externalPdfDropTarget = ref<ExternalPdfDropTarget | null>(null)
const commonSites = ref<CommonSite[]>([])
const commonSiteIconUrls = ref<Record<string, string>>({})
const wallSitePositions = ref<Record<string, WallSitePosition>>({})
const legacyWallSitePositions = ref<Record<string, LegacyWallSitePosition>>({})
const draggingWallSiteId = ref<string | null>(null)
const selectedWallSiteIds = ref<string[]>([])
const wallSiteMarquee = ref<WallSiteMarquee | null>(null)
const wallSiteEditorVisible = ref(false)
const wallSiteEditorId = ref<string | null>(null)
const wallSiteEditorTitle = ref('')
const wallSiteEditorUrl = ref('')
const wallSiteEditorDescription = ref('')
const wallSiteEditorLogoSize = ref(46)
const wallSiteEditorPlacement = ref<WallSitePosition | null>(null)
const wallSiteLogoRefreshing = ref(false)
const wallSiteEditorLogoPreviewUrl = ref('')
let wallSiteEditorLogoPreviewOwned = false
let wallSiteEditorRefreshedLogoBlob: Blob | null = null
const importingDroppedPdfs = ref(false)
let wallSitePressTimer: ReturnType<typeof setTimeout> | null = null
let pendingWallSiteId: string | null = null
let wallSitePointerStartX = 0
let wallSitePointerStartY = 0
let suppressWallSiteClickId: string | null = null
let bookshelfHorizontalExpansionPending = false
let bookshelfHorizontalExpansionAllowedAt = 0
let bookshelfVerticalExpansionAllowedAt = 0
const bookContextMenu = ref<BookContextMenuState | null>(null)
const skillBookContextMenu = ref<SkillBookContextMenuState | null>(null)
const linuxDoBookContextMenu = ref<LinuxDoBookContextMenuState | null>(null)
const bookshelfContextMenu = ref<BookshelfContextMenuState | null>(null)
const shelfContextMenu = ref<ShelfContextMenuState | null>(null)
const metadataEditorVisible = ref(false)
const metadataEditorSaving = ref(false)
const metadataEditorPdfId = ref<number | null>(null)
const metadataEditorTitle = ref('')
const metadataEditorAuthor = ref('')
const metadataEditorStartDate = ref('')
const metadataEditorSubtitle = ref('')
const metadataEditorTranslator = ref('')
const metadataEditorEdition = ref('')
const metadataEditorVolume = ref('')
const metadataEditorSourceName = ref('')
const metadataEditorImportedFilename = ref('')
const metadataEditorDescription = ref('')
const metadataEditorTags = ref('')
const metadataEditorCoverColor = ref('')
const deleteBookDialogVisible = ref(false)
const deleteBookDialogDocument = ref<PdfDocumentSummary | null>(null)
const deleteBookDialogBookshelfName = ref('')
const deleteBookDialogSaving = ref(false)
const libraryFolders = ref<LibraryFolder[]>([])
const folderEditorVisible = ref(false)
const folderEditorSaving = ref(false)
const editingFolder = ref<LibraryFolder | null>(null)
const folderEditorName = ref('')
const folderEditorColor = ref('')
const folderEditorMinThickness = ref<number | undefined>()
const folderEditorFixedThickness = ref<number | undefined>()
const folderContentsVisible = ref(false)
const openedFolder = ref<LibraryFolder | null>(null)
const copyBookVisible = ref(false)
const copyBookSaving = ref(false)
const copyBookPdfId = ref<number | null>(null)
const copyTargetBookshelfId = ref('')
const copyIncludeNotes = ref(true)
const previewVisible = ref(false)
const previewDocument = ref<PdfDocumentSummary | null>(null)
const previewPage = ref(1)
const previewImageUrl = ref('')
const previewLoading = ref(false)
const previewError = ref('')
const skillBookCatalog = ref<SkillBookCatalog | null>(null)
const skillBookReadingState = ref<SkillBookReadingState | null>(null)
const skillBookReaderVisible = ref(false)
const skillBookMetadataVisible = ref(false)
const skillBookMetadataSaving = ref(false)
const skillBookPageFormat = ref('A4')
const skillBookStartDate = ref('')
const linuxDoBooks = ref<LinuxDoBookSummary[]>([])
const linuxDoBookReaderVisible = ref(false)
const selectedLinuxDoBookId = ref('')
const linuxDoBookMetadataVisible = ref(false)
const linuxDoBookMetadataSaving = ref(false)
const linuxDoBookMetadataId = ref('')
const linuxDoBookMetadataTitle = ref('')
const linuxDoBookMetadataAuthor = ref('')
const linuxDoBookMetadataStartDate = ref('')
const linuxDoBookMetadataCoverColor = ref('')
const bookshelfShareVisible = ref(false)
const bookshelfShareLoading = ref(false)
const bookshelfShareBookshelf = ref<PdfLibraryBookshelf | null>(null)
const bookshelfShareGrants = ref<PdfAccessGrantItem[]>([])
const bookshelfShareUsername = ref('')
const bookshelfShareSelectedUser = ref<AccountUserOption | null>(null)
const bookshelfSettingsVisible = ref(false)
const bookshelfSettingsSaving = ref(false)
const bookshelfSettingsId = ref('')
const bookshelfSettingsName = ref('')
const bookshelfSettingsPageTarget = ref(1600)
const bookshelfSettingsReadingMode = ref<ArticleReadingMode>('scroll')
const bookReadingSettingsVisible = ref(false)
const bookReadingSettingsSaving = ref(false)
const bookReadingSettingsBookId = ref('')
const bookReadingSettingsTitle = ref('')
const bookReadingSettingsMode = ref<ArticleReadingModeOverride>('inherit')
const bookCoverImageUrls = ref(new Map<number, string>())
let titleRefreshTimer: ReturnType<typeof setTimeout> | null = null
let layoutSaveQueue = Promise.resolve()
let pointerDragPdfId: number | null = null
let pointerDragSkillBook = false
let pointerDragLinuxDoBookId: string | null = null
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
const selectedBookshelfIsOwned = computed(() => selectedBookshelf.value?.is_owned !== false)
const contextBookshelf = computed(() => bookshelves.value.find(
  (bookshelf) => bookshelf.id === bookshelfContextMenu.value?.bookshelfId,
) ?? null)
const normalizedSearchText = computed(() => searchText.value.trim().toLowerCase())

const filteredDocuments = computed(() => {
  const query = normalizedSearchText.value
  return documents.value.filter((document) => {
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
  if (!catalog) {
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
const filteredLinuxDoBooks = computed(() => {
  const query = normalizedSearchText.value
  return linuxDoBooks.value.filter((book) => !query
    || `${book.title}\n${book.author}`.toLowerCase().includes(query))
})
const hasVisibleLibraryItems = computed(() => (
  showLocalSkillBook.value || filteredDocuments.value.length > 0 || filteredLinuxDoBooks.value.length > 0
))
const selectedReaderBook = computed(() => linuxDoBooks.value.find(
  book => book.id === selectedLinuxDoBookId.value,
))
const selectedReaderMode = computed<ArticleReadingMode>(() => (
  selectedReaderBook.value?.bookshelf_placement.article_reading_mode
  ?? selectedBookshelf.value?.article_reading_mode
  ?? 'scroll'
))
const skillBookSpineStyle = computed(() => {
  const catalog = skillBookCatalog.value
  const pages = catalog?.estimated_page_count ?? 1
  const currentPage = Math.min(pages, Math.max(1, skillBookReadingState.value?.current_page ?? 1))
  const readingProgress = pages > 1 ? (currentPage - 1) / (pages - 1) : 0
  return dynamicBookSpineStyle({
    title: '本地Skill手册',
    author: bookAuthorWithYear(catalog?.author, catalog?.start_date),
    startDate: catalog?.start_date ?? '',
    pageCount: pages,
    pageWidthMm: catalog?.page_width_mm ?? 210,
    pageHeightMm: catalog?.page_height_mm ?? 297,
    orientation: catalog?.bookshelf_placement.orientation ?? 'spine_vertical',
    coverColor: catalog?.cover_color ?? '#315f53',
    dragX: draggingSkillBook.value ? bookDragOffsetX.value : 0,
    dragY: draggingSkillBook.value ? bookDragOffsetY.value : 0,
    readingProgress,
  })
})

const filteredDocumentIds = computed(() => new Set(filteredDocuments.value
  .filter((document) => !document.bookshelf_placement?.folder_id)
  .map((document) => document.id)))
const foldersByShelf = computed(() => {
  const result = new Map<number, LibraryFolder[]>()
  for (const folder of libraryFolders.value) {
    const row = result.get(folder.shelf_index) ?? []
    row.push(folder)
    result.set(folder.shelf_index, row)
  }
  for (const row of result.values()) row.sort((left, right) => left.position_index - right.position_index)
  return result
})
const openedFolderDocuments = computed(() => openedFolder.value
  ? filteredDocuments.value.filter((document) => document.bookshelf_placement?.folder_id === openedFolder.value?.id)
  : [])
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
const bookshelfContentRowCount = computed(() => contentDrivenBookshelfRowCount())
const bookshelfGridStyle = computed(() => ({
  '--bookshelf-canvas-min-width': `${bookshelfCanvasMinWidth.value}px`,
}))

watch(bookshelfContentRowCount, (rowCount) => {
  bookshelfCanvasMinRowCount.value = Math.min(bookshelfCanvasMinRowCount.value, rowCount)
})

function bookshelfGroupPosition(group: BookshelfBookGroup) {
  if (group.kind === 'folder') return group.folder?.position_index ?? Number.MAX_SAFE_INTEGER
  if (group.kind === 'linux-do-book') {
    return group.linuxDoBook?.bookshelf_placement.position_index ?? Number.MAX_SAFE_INTEGER
  }
  if (group.kind === 'skill-book') {
    return skillBookCatalog.value?.bookshelf_placement.position_index ?? Number.MAX_SAFE_INTEGER
  }
  return group.documents[0]?.bookshelf_placement?.position_index ?? Number.MAX_SAFE_INTEGER
}

function bookshelfGroupContainsPlacementKey(group: BookshelfBookGroup, placementKey: string | null) {
  if (!placementKey) {
    return false
  }
  return group.key === placementKey
    || group.documents.some((document) => `book-${document.id}` === placementKey)
}

const bookshelfDisplayRows = computed(() => bookshelfRows.value.map((row, shelfIndex) => {
  const groups = buildBookshelfBookGroups(row)
  for (const folder of foldersByShelf.value.get(shelfIndex) ?? []) {
    const folderInsertionIndex = groups.findIndex((group) => bookshelfGroupPosition(group) >= folder.position_index)
    groups.splice(folderInsertionIndex < 0 ? groups.length : folderInsertionIndex, 0, {
      key: `folder-${folder.id}`,
      kind: 'folder',
      documents: [],
      folder,
    })
  }
  for (const book of filteredLinuxDoBooks.value.filter((item) => (
    !item.bookshelf_placement.folder_id && item.bookshelf_placement.shelf_index === shelfIndex
  ))) {
    const bookInsertionIndex = groups.findIndex(
      (group) => bookshelfGroupPosition(group) >= book.bookshelf_placement.position_index,
    )
    groups.splice(bookInsertionIndex < 0 ? groups.length : bookInsertionIndex, 0, {
      key: `linux-do-book-${book.id}`,
      kind: 'linux-do-book',
      documents: [],
      linuxDoBook: book,
    })
  }
  const placement = skillBookCatalog.value?.bookshelf_placement
  if (!showLocalSkillBook.value || !placement || placement.folder_id || placement.shelf_index !== shelfIndex) {
    return groups
  }
  const insertionIndex = groups.findIndex((group) => bookshelfGroupPosition(group) >= placement.position_index)
  groups.splice(insertionIndex < 0 ? groups.length : insertionIndex, 0, {
    key: `skill-book-${skillBookCatalog.value?.id ?? 'local'}`,
    kind: 'skill-book',
    documents: [],
  })
  return groups
}))

function placementItemsForGroup(group: BookshelfBookGroup): BookshelfPlacementItem[] {
  if (group.kind === 'linux-do-book' && group.linuxDoBook) {
    return [{
      key: group.key,
      resource_type: 'book_asset',
      resource_id: group.linuxDoBook.id,
      shelf_index: group.linuxDoBook.bookshelf_placement.shelf_index,
      position_index: group.linuxDoBook.bookshelf_placement.position_index,
      linuxDoBook: group.linuxDoBook,
    }]
  }
  if (group.kind === 'skill-book' && skillBookCatalog.value) {
    return [{
      key: group.key,
      resource_type: 'book_asset',
      resource_id: skillBookCatalog.value.asset_id,
      shelf_index: skillBookCatalog.value.bookshelf_placement.shelf_index,
      position_index: skillBookCatalog.value.bookshelf_placement.position_index,
      skillBook: skillBookCatalog.value,
    }]
  }
  if (group.kind === 'folder' && group.folder) {
    return [{
      key: group.key,
      resource_type: 'folder',
      resource_id: group.folder.id,
      shelf_index: group.folder.shelf_index,
      position_index: group.folder.position_index,
      folder: group.folder,
    }]
  }
  return group.documents.map((document) => ({
    key: `book-${document.id}`,
    resource_type: 'pdf' as const,
    resource_id: `${document.id}`,
    shelf_index: document.bookshelf_placement?.shelf_index ?? 0,
    position_index: document.bookshelf_placement?.position_index ?? 0,
    document,
  }))
}

function buildUnifiedLibraryLayoutRows() {
  return bookshelfDisplayRows.value.map((groups) => groups.flatMap(placementItemsForGroup))
}

function applyUnifiedLibraryLayoutRows(rows: BookshelfPlacementItem[][]) {
  const items: LibraryBookshelfLayoutItem[] = []
  for (const [shelfIndex, row] of rows.entries()) {
    for (const [positionIndex, item] of row.entries()) {
      item.shelf_index = shelfIndex
      item.position_index = positionIndex
      items.push({
        resource_type: item.resource_type,
        resource_id: item.resource_id,
        shelf_index: shelfIndex,
        position_index: positionIndex,
      })
      if (item.document?.bookshelf_placement) {
        item.document.bookshelf_placement = {
          ...item.document.bookshelf_placement,
          shelf_index: shelfIndex,
          position_index: positionIndex,
          folder_id: null,
        }
      } else if (item.linuxDoBook) {
        item.linuxDoBook.bookshelf_placement = {
          ...item.linuxDoBook.bookshelf_placement,
          shelf_index: shelfIndex,
          position_index: positionIndex,
          folder_id: null,
        }
      } else if (item.skillBook) {
        item.skillBook.bookshelf_placement = {
          ...item.skillBook.bookshelf_placement,
          shelf_index: shelfIndex,
          position_index: positionIndex,
          folder_id: null,
        }
      } else if (item.folder) {
        item.folder.shelf_index = shelfIndex
        item.folder.position_index = positionIndex
      }
    }
  }
  documents.value = [...documents.value]
  linuxDoBooks.value = [...linuxDoBooks.value]
  if (skillBookCatalog.value) skillBookCatalog.value = { ...skillBookCatalog.value }
  libraryFolders.value = [...libraryFolders.value]
  queueUnifiedLibraryLayoutSave(items)
}
const externalDropShelfLabel = computed(() => externalPdfDropTarget.value == null
  ? `书柜“${selectedBookshelf.value?.name ?? ''}”`
  : `书柜“${selectedBookshelf.value?.name ?? ''}”第 ${externalPdfDropTarget.value.shelfIndex + 1} 栏`)

function loadBookshelfWallSites() {
  commonSites.value = loadCommonSites()
  syncRuanyfWeeklyCommonSiteUrl()
  void loadCommonSiteIcons()
}

function syncRuanyfWeeklyCommonSiteUrl(books: LinuxDoBookSummary[] = linuxDoBooks.value) {
  const weeklyBook = books.find((book) => book.id.startsWith('ruanyf-weekly:'))
  const latestIssue = Number(weeklyBook?.latest_issue)
  if (!Number.isInteger(latestIssue) || latestIssue < 1) return
  const latestUrl = `https://github.com/ruanyf/weekly/blob/master/docs/issue-${latestIssue}.md`
  let site = commonSites.value.find((item) => item.id === 'ruanyf-weekly-latest')
  let siteChanged = false
  if (!site) {
    site = {
      id: 'ruanyf-weekly-latest',
      title: 'Weekly',
      url: latestUrl,
      description: '科技爱好者周刊最新一期',
      logo_size: 46,
    }
    commonSites.value = [...commonSites.value, site]
    siteChanged = true
  } else if (site.url !== latestUrl) {
    site.url = latestUrl
    commonSites.value = [...commonSites.value]
    siteChanged = true
  }
  if (siteChanged) {
    saveCommonSites(commonSites.value)
    void loadCommonSiteIcon(site).catch((error) => {
      console.warn(`Failed to load weekly logo for ${site.url}:`, error)
    })
  }
  const existingPosition = wallSitePositions.value[site.id]
  const autoPlacementKey = weeklyCommonSiteAutoPlacementStorageKey()
  if (
    selectedBookshelfIsOwned.value
    && (!existingPosition || localStorage.getItem(autoPlacementKey) !== '1')
  ) {
    wallSitePositions.value = {
      ...wallSitePositions.value,
      [site.id]: {
        shelfIndex: Math.max(0, weeklyBook.bookshelf_placement.shelf_index),
        xRatio: existingPosition?.xRatio ?? 0.5,
        yRatio: existingPosition?.yRatio ?? 0.28,
      },
    }
    saveWallSitePositions()
    localStorage.setItem(autoPlacementKey, '1')
  }
}

function setCommonSiteIconBlob(siteId: string, blob: Blob) {
  const previousUrl = commonSiteIconUrls.value[siteId]
  const nextUrl = URL.createObjectURL(blob)
  commonSiteIconUrls.value = { ...commonSiteIconUrls.value, [siteId]: nextUrl }
  if (previousUrl) URL.revokeObjectURL(previousUrl)
}

function removeCommonSiteIcon(siteId: string) {
  const previousUrl = commonSiteIconUrls.value[siteId]
  if (previousUrl) URL.revokeObjectURL(previousUrl)
  const nextUrls = { ...commonSiteIconUrls.value }
  delete nextUrls[siteId]
  commonSiteIconUrls.value = nextUrls
}

async function loadCommonSiteIcon(site: CommonSite, refresh = false) {
  const requestedUrl = site.url
  const blob = await loadCommonSiteLogoBlob(requestedUrl, { refresh })
  const currentSite = commonSites.value.find((item) => item.id === site.id)
  if (!currentSite || currentSite.url !== requestedUrl) return
  setCommonSiteIconBlob(site.id, blob)
}

async function loadCommonSiteIcons() {
  await Promise.all(commonSites.value.map(async (site) => {
    try {
      await loadCommonSiteIcon(site)
    } catch (error) {
      console.warn(`Failed to load cached logo for ${site.url}:`, error)
    }
  }))
}

function releaseCommonSiteIconUrls() {
  Object.values(commonSiteIconUrls.value).forEach((url) => URL.revokeObjectURL(url))
  commonSiteIconUrls.value = {}
}

function wallSitePositionStorageKey() {
  return `codeyun.pdf-library.wall-sites.${currentUserId.value ?? 'anonymous'}.${selectedBookshelfId.value || 'none'}`
}

function weeklyCommonSiteAutoPlacementStorageKey() {
  return `${wallSitePositionStorageKey()}.ruanyf-weekly-auto-placement.v1`
}

function bookshelfCanvasExtentStorageKey() {
  return `${PDF_LIBRARY_BOOKSHELF_KEY_PREFIX}.canvas.v2.${currentUserId.value ?? 'anonymous'}.${selectedBookshelfId.value || 'none'}`
}

function loadBookshelfCanvasExtent() {
  bookshelfHorizontalExpansionAllowedAt = 0
  bookshelfVerticalExpansionAllowedAt = 0
  bookshelfCanvasMinWidth.value = 0
  bookshelfCanvasMinRowCount.value = DEFAULT_BOOKSHELF_ROW_COUNT
  try {
    const parsed = JSON.parse(localStorage.getItem(bookshelfCanvasExtentStorageKey()) ?? '{}') as Record<string, unknown>
    const minWidth = Number(parsed.minWidth)
    if (Number.isFinite(minWidth) && minWidth > 0) {
      bookshelfCanvasMinWidth.value = Math.floor(minWidth)
    }
  } catch {
    // Invalid UI extent preferences can safely fall back to the content-driven canvas.
  }
}

function saveBookshelfCanvasExtent() {
  localStorage.setItem(bookshelfCanvasExtentStorageKey(), JSON.stringify({
    minWidth: bookshelfCanvasMinWidth.value,
  }))
}

function extendBookshelfDownward() {
  const now = Date.now()
  if (now < bookshelfVerticalExpansionAllowedAt) return
  bookshelfVerticalExpansionAllowedAt = now + BOOKSHELF_EXPANSION_COOLDOWN_MS
  bookshelfCanvasMinRowCount.value = Math.max(
    bookshelfCanvasMinRowCount.value + BOOKSHELF_ROW_GROWTH_COUNT,
    bookshelfDisplayRows.value.length + BOOKSHELF_ROW_GROWTH_COUNT,
  )
}

function extendBookshelfRightward(scroller: HTMLElement) {
  const now = Date.now()
  if (bookshelfHorizontalExpansionPending || now < bookshelfHorizontalExpansionAllowedAt) return
  bookshelfHorizontalExpansionAllowedAt = now + BOOKSHELF_EXPANSION_COOLDOWN_MS
  bookshelfHorizontalExpansionPending = true
  const previousLayerWidth = document.querySelector<HTMLElement>('.bookshelf-wall-sites')?.offsetWidth ?? 0
  const growth = Math.max(BOOKSHELF_HORIZONTAL_GROWTH_MIN, Math.round(scroller.clientWidth * 0.75))
  bookshelfCanvasMinWidth.value = Math.max(bookshelfCanvasMinWidth.value, scroller.scrollWidth) + growth
  saveBookshelfCanvasExtent()
  void nextTick(() => {
    const nextLayerWidth = document.querySelector<HTMLElement>('.bookshelf-wall-sites')?.offsetWidth ?? 0
    if (previousLayerWidth > 0 && nextLayerWidth > previousLayerWidth) {
      const ratio = previousLayerWidth / nextLayerWidth
      const positions = { ...wallSitePositions.value }
      Object.entries(positions).forEach(([id, position]) => {
        positions[id] = { ...position, xRatio: position.xRatio * ratio }
      })
      wallSitePositions.value = positions
      saveWallSitePositions()
    }
    bookshelfHorizontalExpansionPending = false
  })
}

function handleBookshelfScroll(event: Event) {
  closeBookContextMenu()
  const scroller = event.currentTarget
  if (!(scroller instanceof HTMLElement)) return
  const remainingBottom = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight
  if (scroller.scrollHeight > scroller.clientHeight && remainingBottom <= BOOKSHELF_EDGE_THRESHOLD) {
    extendBookshelfDownward()
  } else if (
    scroller.scrollTop <= BOOKSHELF_EDGE_THRESHOLD
    && draggingPdfId.value == null
    && draggingLinuxDoBookId.value == null
    && !draggingSkillBook.value
    && !externalFileDragActive.value
  ) {
    bookshelfCanvasMinRowCount.value = bookshelfContentRowCount.value
  }
  const remainingRight = scroller.scrollWidth - scroller.scrollLeft - scroller.clientWidth
  if (scroller.scrollWidth > scroller.clientWidth && remainingRight <= BOOKSHELF_EDGE_THRESHOLD) {
    extendBookshelfRightward(scroller)
  }
}

function growBookshelfNearPointer(event: Pick<PointerEvent | DragEvent, 'clientX' | 'clientY'>) {
  const scroller = bookshelfScrollRef.value
  if (!scroller) return
  const bounds = scroller.getBoundingClientRect()
  const nearRight = event.clientX >= bounds.right - BOOKSHELF_EDGE_THRESHOLD
  const nearBottom = event.clientY >= bounds.bottom - BOOKSHELF_EDGE_THRESHOLD
  if (nearRight) {
    const remainingRight = scroller.scrollWidth - scroller.scrollLeft - scroller.clientWidth
    if (remainingRight <= BOOKSHELF_EDGE_THRESHOLD) extendBookshelfRightward(scroller)
    scroller.scrollLeft += BOOKSHELF_DRAG_SCROLL_STEP
  }
  if (nearBottom) {
    const remainingBottom = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight
    if (remainingBottom <= BOOKSHELF_EDGE_THRESHOLD) extendBookshelfDownward()
    scroller.scrollTop += BOOKSHELF_DRAG_SCROLL_STEP
  }
  if (event.clientX <= bounds.left + BOOKSHELF_EDGE_THRESHOLD) {
    scroller.scrollLeft -= BOOKSHELF_DRAG_SCROLL_STEP
  }
  if (event.clientY <= bounds.top + BOOKSHELF_EDGE_THRESHOLD) {
    scroller.scrollTop -= BOOKSHELF_DRAG_SCROLL_STEP
  }
}

function loadWallSitePositions() {
  const positions: Record<string, WallSitePosition> = {}
  const legacyPositions: Record<string, LegacyWallSitePosition> = {}
  try {
    const parsed = JSON.parse(localStorage.getItem(wallSitePositionStorageKey()) ?? '{}') as Record<string, unknown>
    if (parsed && typeof parsed === 'object') {
      Object.entries(parsed).forEach(([id, rawPosition]) => {
        if (!rawPosition || typeof rawPosition !== 'object') return
        const position = rawPosition as Record<string, unknown>
        const shelfIndex = Number(position.shelfIndex)
        const xRatio = Number(position.xRatio)
        const yRatio = Number(position.yRatio)
        if (Number.isFinite(shelfIndex) && Number.isFinite(xRatio) && Number.isFinite(yRatio)) {
          positions[id] = {
            shelfIndex: Math.max(0, Math.floor(shelfIndex)),
            xRatio: Math.min(1, Math.max(0, xRatio)),
            yRatio: Math.min(1, Math.max(0, yRatio)),
          }
          return
        }
        const x = Number(position.x)
        const y = Number(position.y)
        if (Number.isFinite(x) && Number.isFinite(y)) legacyPositions[id] = { x, y }
      })
    }
  } catch {
    // Ignore invalid historical layout data and fall back to the default wall arrangement.
  }
  wallSitePositions.value = positions
  legacyWallSitePositions.value = legacyPositions
  selectedWallSiteIds.value = []
  void nextTick(migrateLegacyWallSitePositions)
}

function saveWallSitePositions() {
  localStorage.setItem(wallSitePositionStorageKey(), JSON.stringify(wallSitePositions.value))
}

function wallSiteStyle(site: CommonSite) {
  const logoSize = Math.min(96, Math.max(24, Number(site.logo_size) || 46))
  const position = wallSitePositions.value[site.id]
  const style: Record<string, string> = {
    '--wall-site-logo-size': `${logoSize}px`,
  }
  if (!position) return style
  return {
    ...style,
    position: 'absolute',
    left: `${position.xRatio * 100}%`,
    top: `${position.yRatio * 100}%`,
    transform: 'translate(-50%, -50%)',
  }
}

function wallSitesForShelf(shelfIndex: number) {
  return commonSites.value.filter((site) => (wallSitePositions.value[site.id]?.shelfIndex ?? 0) === shelfIndex)
}

function selectedWallSitesForShelf(shelfIndex: number) {
  const selectedIds = new Set(selectedWallSiteIds.value)
  return wallSitesForShelf(shelfIndex).filter((site) => selectedIds.has(site.id))
}

function openWallSiteEditor(event: MouseEvent, site: CommonSite) {
  event.preventDefault()
  event.stopPropagation()
  selectedWallSiteIds.value = []
  wallSiteEditorId.value = site.id
  wallSiteEditorTitle.value = site.title
  wallSiteEditorUrl.value = site.url
  wallSiteEditorDescription.value = site.description ?? ''
  wallSiteEditorLogoSize.value = Math.min(96, Math.max(24, Number(site.logo_size) || 46))
  wallSiteEditorPlacement.value = null
  wallSiteEditorLogoPreviewUrl.value = commonSiteIconUrls.value[site.id] ?? ''
  wallSiteEditorLogoPreviewOwned = false
  wallSiteEditorRefreshedLogoBlob = null
  wallSiteEditorVisible.value = true
}

function openNewWallSiteEditor() {
  const menu = shelfContextMenu.value
  if (!menu || !selectedBookshelfIsOwned.value) return
  wallSiteEditorId.value = null
  wallSiteEditorTitle.value = ''
  wallSiteEditorUrl.value = ''
  wallSiteEditorDescription.value = ''
  wallSiteEditorLogoSize.value = 46
  wallSiteEditorPlacement.value = {
    shelfIndex: menu.shelfIndex,
    xRatio: menu.xRatio,
    yRatio: menu.yRatio,
  }
  releaseWallSiteEditorLogoPreview()
  closeContextMenus()
  wallSiteEditorVisible.value = true
}

function releaseWallSiteEditorLogoPreview() {
  if (wallSiteEditorLogoPreviewOwned && wallSiteEditorLogoPreviewUrl.value) {
    URL.revokeObjectURL(wallSiteEditorLogoPreviewUrl.value)
  }
  wallSiteEditorLogoPreviewUrl.value = ''
  wallSiteEditorLogoPreviewOwned = false
  wallSiteEditorRefreshedLogoBlob = null
}

function saveWallSiteEditor() {
  let url = wallSiteEditorUrl.value.trim()
  if (!/^https?:\/\//i.test(url)) url = `https://${url}`
  try {
    new URL(url)
  } catch {
    ElMessage.warning('网址格式不正确')
    return
  }
  const existingSite = commonSites.value.find((item) => item.id === wallSiteEditorId.value)
  const site: CommonSite = existingSite ?? {
    id: typeof crypto.randomUUID === 'function'
      ? `site-${crypto.randomUUID()}`
      : `site-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    title: '',
    url,
  }
  const previousUrl = existingSite?.url ?? ''
  site.title = wallSiteEditorTitle.value.trim()
  site.url = url
  site.description = wallSiteEditorDescription.value.trim()
  site.logo_size = Math.min(96, Math.max(24, Number(wallSiteEditorLogoSize.value) || 46))
  if (existingSite) {
    commonSites.value = [...commonSites.value]
  } else {
    commonSites.value = [...commonSites.value, site]
    wallSitePositions.value = {
      ...wallSitePositions.value,
      [site.id]: wallSiteEditorPlacement.value ?? {
        shelfIndex: 0,
        xRatio: 0.5,
        yRatio: 0.25,
      },
    }
    saveWallSitePositions()
  }
  saveCommonSites(commonSites.value)
  wallSiteEditorVisible.value = false
  if (wallSiteEditorRefreshedLogoBlob) {
    setCommonSiteIconBlob(site.id, wallSiteEditorRefreshedLogoBlob)
  } else {
    try {
      if (new URL(previousUrl).origin !== new URL(site.url).origin) removeCommonSiteIcon(site.id)
    } catch {
      removeCommonSiteIcon(site.id)
    }
    void loadCommonSiteIcon(site).catch((error) => {
      console.warn(`Failed to load cached logo for ${site.url}:`, error)
    })
  }
}

async function refreshWallSiteLogo() {
  let url = wallSiteEditorUrl.value.trim()
  if (!/^https?:\/\//i.test(url)) url = `https://${url}`
  try {
    new URL(url)
  } catch {
    ElMessage.warning('网址格式不正确')
    return
  }
  wallSiteLogoRefreshing.value = true
  try {
    const blob = await loadCommonSiteLogoBlob(url, { refresh: true })
    if (wallSiteEditorLogoPreviewOwned && wallSiteEditorLogoPreviewUrl.value) {
      URL.revokeObjectURL(wallSiteEditorLogoPreviewUrl.value)
    }
    wallSiteEditorLogoPreviewUrl.value = URL.createObjectURL(blob)
    wallSiteEditorLogoPreviewOwned = true
    wallSiteEditorRefreshedLogoBlob = blob
    wallSiteEditorUrl.value = url
    ElMessage.success('Logo 已重新获取')
  } catch (error) {
    console.warn('Failed to refresh common site logo:', error)
    ElMessage.error('Logo 获取失败')
  } finally {
    wallSiteLogoRefreshing.value = false
  }
}

function clampWallSiteRatio(pointerCoordinate: number, layerSize: number, itemSize: number) {
  if (layerSize <= 0) return 0.5
  const halfRatio = Math.min(0.5, itemSize / (2 * layerSize))
  return Math.min(1 - halfRatio, Math.max(halfRatio, pointerCoordinate / layerSize))
}

function materializeWallSitePositions() {
  const positions: Record<string, WallSitePosition> = { ...wallSitePositions.value }
  document.querySelectorAll<HTMLElement>('[data-wall-site-id]').forEach((element) => {
    const id = element.dataset.wallSiteId
    if (!id || positions[id]) return
    const layer = element.closest<HTMLElement>('.bookshelf-wall-sites')
    if (!layer) return
    const shelfIndex = Number(layer.dataset.wallShelfIndex)
    const layerRect = layer.getBoundingClientRect()
    const rect = element.getBoundingClientRect()
    positions[id] = {
      shelfIndex: Number.isFinite(shelfIndex) ? Math.max(0, Math.floor(shelfIndex)) : 0,
      xRatio: clampWallSiteRatio(rect.left + rect.width / 2 - layerRect.left, layerRect.width, rect.width),
      yRatio: clampWallSiteRatio(rect.top + rect.height / 2 - layerRect.top, layerRect.height, rect.height),
    }
  })
  wallSitePositions.value = positions
}

function migrateLegacyWallSitePositions() {
  if (!Object.keys(legacyWallSitePositions.value).length) return
  const layer = document.querySelector<HTMLElement>('[data-wall-shelf-index="0"]')
  if (!layer) return
  const layerRect = layer.getBoundingClientRect()
  const positions = { ...wallSitePositions.value }
  Object.entries(legacyWallSitePositions.value).forEach(([id, legacyPosition]) => {
    const element = document.querySelector<HTMLElement>(`[data-wall-site-id="${CSS.escape(id)}"]`)
    if (!element) return
    positions[id] = {
      shelfIndex: 0,
      xRatio: clampWallSiteRatio(legacyPosition.x + element.offsetWidth / 2, layerRect.width, element.offsetWidth),
      yRatio: clampWallSiteRatio(legacyPosition.y + element.offsetHeight / 2, layerRect.height, element.offsetHeight),
    }
    delete legacyWallSitePositions.value[id]
  })
  wallSitePositions.value = positions
  if (!Object.keys(legacyWallSitePositions.value).length) saveWallSitePositions()
}

function activateWallSiteDrag(siteId: string) {
  const element = document.querySelector<HTMLElement>(`[data-wall-site-id="${CSS.escape(siteId)}"]`)
  if (!element) return
  materializeWallSitePositions()
  selectedWallSiteIds.value = []
  draggingWallSiteId.value = siteId
}

function wallSiteMarqueeStyle(shelfIndex: number) {
  const marquee = wallSiteMarquee.value
  if (!marquee?.active || marquee.shelfIndex !== shelfIndex) return undefined
  const left = Math.min(marquee.startX, marquee.currentX)
  const top = Math.min(marquee.startY, marquee.currentY)
  return {
    left: `${left}px`,
    top: `${top}px`,
    width: `${Math.abs(marquee.currentX - marquee.startX)}px`,
    height: `${Math.abs(marquee.currentY - marquee.startY)}px`,
  }
}

function handleWallSelectionPointerDown(event: PointerEvent, shelfIndex: number) {
  if (event.button !== 0 || !selectedBookshelfIsOwned.value || !commonSites.value.length) return
  const target = event.target as Element | null
  if (target?.closest('.book-group, .bookshelf-wall-site, .wall-site-selection-toolbar')) return
  const row = event.currentTarget as HTMLElement
  const layer = row.querySelector<HTMLElement>('.bookshelf-wall-sites')
  if (!layer) return
  materializeWallSitePositions()
  const layerRect = layer.getBoundingClientRect()
  const x = Math.min(layerRect.width, Math.max(0, event.clientX - layerRect.left))
  const y = Math.min(layerRect.height, Math.max(0, event.clientY - layerRect.top))
  wallSiteMarquee.value = { shelfIndex, startX: x, startY: y, currentX: x, currentY: y, active: false }
  selectedWallSiteIds.value = []
  event.preventDefault()
  window.addEventListener('pointermove', handleWallSelectionPointerMove, { passive: false })
  window.addEventListener('pointerup', finishWallSelection, { once: true })
  window.addEventListener('pointercancel', finishWallSelection, { once: true })
}

function handleWallSelectionPointerMove(event: PointerEvent) {
  const marquee = wallSiteMarquee.value
  if (!marquee) return
  const layer = document.querySelector<HTMLElement>(`[data-wall-shelf-index="${marquee.shelfIndex}"]`)
  if (!layer) return
  const layerRect = layer.getBoundingClientRect()
  const currentX = Math.min(layerRect.width, Math.max(0, event.clientX - layerRect.left))
  const currentY = Math.min(layerRect.height, Math.max(0, event.clientY - layerRect.top))
  const active = marquee.active || Math.hypot(currentX - marquee.startX, currentY - marquee.startY) > 4
  wallSiteMarquee.value = { ...marquee, currentX, currentY, active }
  if (!active) return
  event.preventDefault()
  const selectionRect = {
    left: layerRect.left + Math.min(marquee.startX, currentX),
    right: layerRect.left + Math.max(marquee.startX, currentX),
    top: layerRect.top + Math.min(marquee.startY, currentY),
    bottom: layerRect.top + Math.max(marquee.startY, currentY),
  }
  selectedWallSiteIds.value = Array.from(layer.querySelectorAll<HTMLElement>('[data-wall-site-id]'))
    .filter((element) => {
      const rect = element.getBoundingClientRect()
      return rect.right >= selectionRect.left
        && rect.left <= selectionRect.right
        && rect.bottom >= selectionRect.top
        && rect.top <= selectionRect.bottom
    })
    .map((element) => element.dataset.wallSiteId)
    .filter((id): id is string => Boolean(id))
}

function finishWallSelection() {
  wallSiteMarquee.value = null
  window.removeEventListener('pointermove', handleWallSelectionPointerMove)
  window.removeEventListener('pointerup', finishWallSelection)
  window.removeEventListener('pointercancel', finishWallSelection)
}

function wallSiteSelectionToolbarStyle(shelfIndex: number) {
  const sites = selectedWallSitesForShelf(shelfIndex)
  if (sites.length < 2) return undefined
  const positions = sites.map((site) => wallSitePositions.value[site.id]).filter(Boolean)
  const xRatio = positions.reduce((total, position) => total + position.xRatio, 0) / positions.length
  const lowestRatio = Math.max(...positions.map((position) => position.yRatio))
  return {
    left: `${xRatio * 100}%`,
    top: `${Math.min(88, lowestRatio * 100 + 24)}%`,
  }
}

function standardizeSelectedWallSites(shelfIndex: number) {
  materializeWallSitePositions()
  const sites = selectedWallSitesForShelf(shelfIndex)
  const layer = document.querySelector<HTMLElement>(`[data-wall-shelf-index="${shelfIndex}"]`)
  if (!layer || sites.length < 2) return
  const layerRect = layer.getBoundingClientRect()
  const items = sites.map((site) => {
    const element = layer.querySelector<HTMLElement>(`[data-wall-site-id="${CSS.escape(site.id)}"]`)
    const position = wallSitePositions.value[site.id]
    if (!element || !position) return null
    return {
      id: site.id,
      width: element.offsetWidth,
      height: element.offsetHeight,
      x: position.xRatio * layerRect.width,
      y: position.yRatio * layerRect.height,
    }
  }).filter((item): item is NonNullable<typeof item> => Boolean(item))
  if (items.length < 2) return
  const xRange = Math.max(...items.map((item) => item.x)) - Math.min(...items.map((item) => item.x))
  const yRange = Math.max(...items.map((item) => item.y)) - Math.min(...items.map((item) => item.y))
  const nextPositions = { ...wallSitePositions.value }
  if (xRange >= yRange) {
    items.sort((left, right) => left.x - right.x)
    const gap = 12
    const totalWidth = items.reduce((total, item) => total + item.width, 0) + gap * (items.length - 1)
    const currentCenter = items.reduce((total, item) => total + item.x, 0) / items.length
    let cursor = Math.min(Math.max(0, currentCenter - totalWidth / 2), Math.max(0, layerRect.width - totalWidth))
    const targetY = items.reduce((total, item) => total + item.y, 0) / items.length
    items.forEach((item) => {
      const x = cursor + item.width / 2
      nextPositions[item.id] = {
        shelfIndex,
        xRatio: clampWallSiteRatio(x, layerRect.width, item.width),
        yRatio: clampWallSiteRatio(targetY, layerRect.height, item.height),
      }
      cursor += item.width + gap
    })
  } else {
    items.sort((left, right) => left.y - right.y)
    const gap = 12
    const totalHeight = items.reduce((total, item) => total + item.height, 0) + gap * (items.length - 1)
    const currentCenter = items.reduce((total, item) => total + item.y, 0) / items.length
    let cursor = Math.min(Math.max(0, currentCenter - totalHeight / 2), Math.max(0, layerRect.height - totalHeight))
    const targetX = items.reduce((total, item) => total + item.x, 0) / items.length
    items.forEach((item) => {
      const y = cursor + item.height / 2
      nextPositions[item.id] = {
        shelfIndex,
        xRatio: clampWallSiteRatio(targetX, layerRect.width, item.width),
        yRatio: clampWallSiteRatio(y, layerRect.height, item.height),
      }
      cursor += item.height + gap
    })
  }
  wallSitePositions.value = nextPositions
  saveWallSitePositions()
}

function handleWallSitePointerDown(event: PointerEvent, siteId: string) {
  if (event.button !== 0) return
  pendingWallSiteId = siteId
  wallSitePointerStartX = event.clientX
  wallSitePointerStartY = event.clientY
  if (wallSitePressTimer) clearTimeout(wallSitePressTimer)
  wallSitePressTimer = setTimeout(() => activateWallSiteDrag(siteId), 350)
  window.addEventListener('pointermove', handleWallSitePointerMove, { passive: false })
  window.addEventListener('pointerup', handleWallSitePointerUp, { once: true })
  window.addEventListener('pointercancel', handleWallSitePointerCancel, { once: true })
}

function handleWallSitePointerMove(event: PointerEvent) {
  if (!pendingWallSiteId) return
  const deltaX = event.clientX - wallSitePointerStartX
  const deltaY = event.clientY - wallSitePointerStartY
  if (!draggingWallSiteId.value) {
    if (Math.hypot(deltaX, deltaY) <= 8) return
    if (wallSitePressTimer) clearTimeout(wallSitePressTimer)
    wallSitePressTimer = null
    activateWallSiteDrag(pendingWallSiteId)
    if (!draggingWallSiteId.value) return
  }
  event.preventDefault()
  growBookshelfNearPointer(event)
  const element = document.querySelector<HTMLElement>(
    `[data-wall-site-id="${CSS.escape(draggingWallSiteId.value)}"]`,
  )
  if (!element) return
  const targetRow = document.elementsFromPoint(event.clientX, event.clientY)
    .map((target) => target.closest<HTMLElement>('.bookshelf-row'))
    .find((row): row is HTMLElement => Boolean(row))
  const layer = targetRow?.querySelector<HTMLElement>('.bookshelf-wall-sites')
  if (!targetRow || !layer) return
  const shelfIndex = Number(targetRow.dataset.shelfIndex)
  if (!Number.isFinite(shelfIndex)) return
  const layerRect = layer.getBoundingClientRect()
  wallSitePositions.value = {
    ...wallSitePositions.value,
    [draggingWallSiteId.value]: {
      shelfIndex: Math.max(0, Math.floor(shelfIndex)),
      xRatio: clampWallSiteRatio(event.clientX - layerRect.left, layerRect.width, element.offsetWidth),
      yRatio: clampWallSiteRatio(event.clientY - layerRect.top, layerRect.height, element.offsetHeight),
    },
  }
}

function finishWallSitePointerInteraction() {
  if (wallSitePressTimer) clearTimeout(wallSitePressTimer)
  wallSitePressTimer = null
  pendingWallSiteId = null
  draggingWallSiteId.value = null
  window.removeEventListener('pointermove', handleWallSitePointerMove)
  window.removeEventListener('pointerup', handleWallSitePointerUp)
  window.removeEventListener('pointercancel', handleWallSitePointerCancel)
}

function handleWallSitePointerUp() {
  const draggedSiteId = draggingWallSiteId.value
  if (draggedSiteId) {
    suppressWallSiteClickId = draggedSiteId
    saveWallSitePositions()
    setTimeout(() => {
      if (suppressWallSiteClickId === draggedSiteId) suppressWallSiteClickId = null
    }, 0)
  }
  finishWallSitePointerInteraction()
}

function handleWallSitePointerCancel() {
  finishWallSitePointerInteraction()
}

function handleWallSiteClick(event: MouseEvent, siteId: string) {
  if (suppressWallSiteClickId !== siteId) return
  event.preventDefault()
  event.stopPropagation()
}

function handleCommonSitesStorage(event: StorageEvent) {
  if (event.key === 'codeyun.notes.commonSites.v1') {
    loadBookshelfWallSites()
  }
}

function commonSiteIconUrl(site: CommonSite) {
  return commonSiteIconUrls.value[site.id] ?? ''
}

function handleCommonSiteIconError(site: CommonSite) {
  removeCommonSiteIcon(site.id)
}

function commonSiteFallbackLabel(site: CommonSite) {
  return site.title.trim().slice(0, 2).toUpperCase() || '站'
}

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
    ...(catalog.start_date ? [tooltipField('起始时间', catalog.start_date)] : []),
    tooltipField('开本', `${catalog.page_format}（${catalog.page_width_mm} × ${catalog.page_height_mm} mm）`),
    tooltipField('目录', `${catalog.skill_count} 个 Skill / ${catalog.chapter_count} 篇`),
    tooltipField('页数', `${catalog.estimated_page_count} 页`),
    ...(skillBookReadingState.value?.updated_at
      ? [tooltipField('页码', `${skillBookReadingState.value.current_page}/${catalog.estimated_page_count}`)]
      : []),
    tooltipField('内容', '按需读取本地最新文件'),
  ].join('\n')
}

function openSkillBookReader() {
  if (suppressNextBookClick) {
    return
  }
  skillBookReaderVisible.value = true
}

function openLinuxDoBookReader(bookId: string) {
  selectedLinuxDoBookId.value = bookId
  linuxDoBookReaderVisible.value = true
}

function linuxDoBookTooltip(book: LinuxDoBookSummary) {
  return [
    tooltipField('书名', book.title),
    ...(book.author ? [tooltipField('作者', book.author)] : []),
    ...(book.start_date ? [tooltipField('起始时间', book.start_date)] : []),
    tooltipField('页数', `${book.estimated_page_count} 页`),
  ].join('\n')
}

function bookAuthorWithYear(author: string | null | undefined, startDate: string | null | undefined) {
  const normalizedAuthor = author?.trim() ?? ''
  const year = bookStartYear(startDate)
  if (normalizedAuthor && year) return `${normalizedAuthor} · ${year}`
  return normalizedAuthor || year
}

function bookStartYear(startDate: string | null | undefined) {
  return startDate?.trim().match(/^\d{4}/)?.[0] ?? ''
}

function handleLinuxDoBookClick(event: MouseEvent, bookId: string) {
  if (suppressNextBookClick) {
    event.preventDefault()
    return
  }
  openLinuxDoBookReader(bookId)
}

function openLinuxDoBookContextMenu(event: MouseEvent, bookId: string) {
  event.preventDefault()
  event.stopPropagation()
  closeContextMenus()
  const menuWidth = 128
  const menuHeight = 92
  linuxDoBookContextMenu.value = {
    bookId,
    x: Math.max(8, Math.min(event.clientX, window.innerWidth - menuWidth - 8)),
    y: Math.max(8, Math.min(event.clientY, window.innerHeight - menuHeight - 8)),
  }
}

async function deleteContextLinuxDoBook() {
  const book = linuxDoBooks.value.find((item) => item.id === linuxDoBookContextMenu.value?.bookId)
  linuxDoBookContextMenu.value = null
  if (!book) return
  try {
    await ElMessageBox.confirm(
      `确定删除《${book.title}》？阅读进度及已导入的图书内容也会删除。`,
      '删除图书',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
    await deleteLinuxDoBook(book.id)
    await Promise.all([reloadLinuxDoBooks(), reloadFolders()])
    ElMessage.success('图书已删除')
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    console.warn('Failed to delete dynamic book:', error)
    ElMessage.error('删除图书失败')
  }
}

function openLinuxDoBookMetadataEditor() {
  const book = linuxDoBooks.value.find((item) => item.id === linuxDoBookContextMenu.value?.bookId)
  linuxDoBookContextMenu.value = null
  if (!book) return
  linuxDoBookMetadataId.value = book.id
  linuxDoBookMetadataTitle.value = book.title
  linuxDoBookMetadataAuthor.value = book.author
  linuxDoBookMetadataStartDate.value = book.start_date
  linuxDoBookMetadataCoverColor.value = book.cover_color
  linuxDoBookMetadataVisible.value = true
}

async function saveLinuxDoBookMetadata() {
  const title = linuxDoBookMetadataTitle.value.trim()
  if (!title) {
    ElMessage.warning('书名不能为空')
    return
  }
  linuxDoBookMetadataSaving.value = true
  try {
    const updated = await updateLinuxDoBookMetadata(linuxDoBookMetadataId.value, {
      title,
      author: linuxDoBookMetadataAuthor.value.trim(),
      start_date: linuxDoBookMetadataStartDate.value.trim(),
      cover_color: linuxDoBookMetadataCoverColor.value || '#294f6d',
    })
    linuxDoBooks.value = linuxDoBooks.value.map((book) => book.id === updated.id ? updated : book)
    linuxDoBookMetadataVisible.value = false
    ElMessage.success('动态书本元数据已保存')
  } catch (error) {
    console.warn('Failed to update dynamic book metadata:', error)
    ElMessage.error('保存动态书本元数据失败')
  } finally {
    linuxDoBookMetadataSaving.value = false
  }
}

function linuxDoBookSpineStyle(book: LinuxDoBookSummary) {
  const readingState = book.reading_state
  const readingProgress = readingState?.updated_at
    ? (Math.max(1, readingState.current_page) - 1)
      / Math.max(1, readingState.page_count - 1)
    : 0
  return dynamicBookSpineStyle({
    title: book.title,
    author: bookAuthorWithYear(book.author, book.start_date),
    startDate: book.start_date,
    pageCount: book.estimated_page_count,
    pageWidthMm: 210,
    pageHeightMm: 297,
    orientation: book.bookshelf_placement.orientation,
    coverColor: book.cover_color,
    dragX: draggingLinuxDoBookId.value === book.id ? bookDragOffsetX.value : 0,
    dragY: draggingLinuxDoBookId.value === book.id ? bookDragOffsetY.value : 0,
    readingProgress,
  })
}

function handleLinuxDoBookReadingStateUpdated(state: LinuxDoBookReadingState) {
  const book = linuxDoBooks.value.find(item => item.id === state.book_id)
  if (book) book.reading_state = state
}

async function reloadLinuxDoBooks() {
  if (!selectedBookshelfId.value) {
    linuxDoBooks.value = []
    return
  }
  try {
    const books = await fetchLinuxDoBooks(selectedBookshelfId.value)
    linuxDoBooks.value = books
    syncRuanyfWeeklyCommonSiteUrl(books)
  } catch (error) {
    console.warn('Failed to load imported LINUX DO books:', error)
    linuxDoBooks.value = []
  }
}

function openSkillBookContextMenu(event: MouseEvent) {
  event.preventDefault()
  event.stopPropagation()
  if (!skillBookCatalog.value?.is_owned) {
    return
  }
  closeContextMenus()
  const menuWidth = 128
  const menuHeight = 92
  skillBookContextMenu.value = {
    x: Math.max(8, Math.min(event.clientX, window.innerWidth - menuWidth - 8)),
    y: Math.max(8, Math.min(event.clientY, window.innerHeight - menuHeight - 8)),
  }
}

async function deleteContextSkillBook() {
  const catalog = skillBookCatalog.value
  skillBookContextMenu.value = null
  if (!catalog?.is_owned) return
  try {
    await ElMessageBox.confirm(
      `确定从图书馆删除《${catalog.title}》？本地 Skill 文件不会被删除。`,
      '删除图书',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
    await deleteLocalSkillBook()
    skillBookCatalog.value = null
    skillBookReadingState.value = null
    await reloadFolders()
    ElMessage.success('图书已删除')
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    console.warn('Failed to delete local Skill book:', error)
    ElMessage.error('删除图书失败')
  }
}

function handleSkillBookPointerDown(event: PointerEvent) {
  if (event.button !== 0 || !skillBookCatalog.value?.is_owned) {
    return
  }
  pointerDragSkillBook = true
  pointerStartX = event.clientX
  pointerStartY = event.clientY
  pointerMoved = false
  window.addEventListener('pointermove', handleSkillBookPointerMove, { passive: false })
  window.addEventListener('pointerup', handleSkillBookPointerUp)
  window.addEventListener('pointercancel', handleSkillBookPointerCancel, { once: true })
}

function handleSkillBookPointerMove(event: PointerEvent) {
  if (!pointerDragSkillBook) {
    return
  }
  if (!pointerMoved && Math.hypot(event.clientX - pointerStartX, event.clientY - pointerStartY) < 6) {
    return
  }
  pointerMoved = true
  draggingSkillBook.value = true
  bookDragOffsetX.value = event.clientX - pointerStartX
  bookDragOffsetY.value = event.clientY - pointerStartY
  event.preventDefault()
  growBookshelfNearPointer(event)
  const skillBookGroupKey = `skill-book-${skillBookCatalog.value?.id ?? 'local'}`
  const dropTarget = resolveDynamicBookDropTarget(event, skillBookGroupKey)
  dragOverShelfIndex.value = dropTarget?.shelfIndex ?? null
  dragOverPlacementKey.value = dropTarget?.beforePlacementKey ?? null
  const targetPdfId = Number(document.elementFromPoint(event.clientX, event.clientY)
    ?.closest<HTMLElement>('[data-pdf-id]')?.dataset.pdfId)
  dragOverPdfId.value = Number.isInteger(targetPdfId) ? targetPdfId : null
}

async function handleSkillBookPointerUp(event: PointerEvent) {
  window.removeEventListener('pointermove', handleSkillBookPointerMove)
  window.removeEventListener('pointerup', handleSkillBookPointerUp)
  window.removeEventListener('pointercancel', handleSkillBookPointerCancel)
  const catalog = skillBookCatalog.value
  const skillBookGroupKey = `skill-book-${catalog?.id ?? 'local'}`
  const dropTarget = pointerDragSkillBook
    ? resolveDynamicBookDropTarget(event, skillBookGroupKey)
    : null
  if (pointerMoved && catalog && dropTarget) {
    event.preventDefault()
    suppressNextBookClick = true
    if (dropTarget.folderId) {
      try {
        const placement = await updateLocalSkillBookPlacement({
          bookshelf_id: selectedBookshelfId.value,
          shelf_index: dropTarget.shelfIndex,
          position_index: dropTarget.positionIndex,
          orientation: catalog.bookshelf_placement.orientation,
          folder_id: dropTarget.folderId,
        })
        catalog.bookshelf_placement = placement
        skillBookCatalog.value = { ...catalog }
        await reloadFolders()
      } catch (error) {
        console.warn('Failed to move local Skill book:', error)
        ElMessage.error('保存动态书本位置失败')
      }
    } else {
      movePlacementToShelf(skillBookGroupKey, dropTarget.shelfIndex, dropTarget.beforePlacementKey)
    }
  }
  handleSkillBookPointerCancel()
  setTimeout(() => {
    suppressNextBookClick = false
  }, 0)
}

function handleSkillBookPointerCancel() {
  window.removeEventListener('pointermove', handleSkillBookPointerMove)
  window.removeEventListener('pointerup', handleSkillBookPointerUp)
  pointerDragSkillBook = false
  pointerMoved = false
  draggingSkillBook.value = false
  dragOverShelfIndex.value = null
  dragOverPdfId.value = null
  dragOverPlacementKey.value = null
  bookDragOffsetX.value = 0
  bookDragOffsetY.value = 0
}

function resolveDynamicBookDropTarget(event: PointerEvent, ignoredGroupKey: string) {
  const target = document.elementFromPoint(event.clientX, event.clientY)
  const targetShelf = target?.closest<HTMLElement>('.bookshelf-row')
  const shelfIndex = Number(targetShelf?.dataset.shelfIndex)
  if (!targetShelf || !Number.isInteger(shelfIndex) || shelfIndex < 0) {
    return null
  }
  const groups = Array.from(targetShelf.querySelectorAll<HTMLElement>('.book-group'))
    .filter((group) => group.dataset.groupKey !== ignoredGroupKey)
  const nextGroup = groups.find((group) => (
    event.clientX < group.getBoundingClientRect().left + group.getBoundingClientRect().width / 2
  ))
  const positions = groups
    .map((group) => Number(group.dataset.positionIndex))
    .filter((position) => Number.isInteger(position) && position >= 0)
  const nextPosition = Number(nextGroup?.dataset.positionIndex)
  return {
    shelfIndex,
    beforePlacementKey: nextGroup?.dataset.groupKey ?? null,
    positionIndex: Number.isInteger(nextPosition)
      ? nextPosition
      : Math.max(-1, ...positions) + 1,
    folderId: target?.closest<HTMLElement>('.library-folder')?.dataset.folderId ?? null,
  }
}

function handleLinuxDoBookPointerDown(event: PointerEvent, bookId: string) {
  if (event.button !== 0 || !selectedBookshelfIsOwned.value) {
    return
  }
  pointerDragLinuxDoBookId = bookId
  pointerStartX = event.clientX
  pointerStartY = event.clientY
  pointerMoved = false
  window.addEventListener('pointermove', handleLinuxDoBookPointerMove, { passive: false })
  window.addEventListener('pointerup', handleLinuxDoBookPointerUp)
  window.addEventListener('pointercancel', handleLinuxDoBookPointerCancel, { once: true })
}

function handleLinuxDoBookPointerMove(event: PointerEvent) {
  if (!pointerDragLinuxDoBookId) {
    return
  }
  if (!pointerMoved && Math.hypot(event.clientX - pointerStartX, event.clientY - pointerStartY) < 6) {
    return
  }
  pointerMoved = true
  draggingLinuxDoBookId.value = pointerDragLinuxDoBookId
  bookDragOffsetX.value = event.clientX - pointerStartX
  bookDragOffsetY.value = event.clientY - pointerStartY
  event.preventDefault()
  growBookshelfNearPointer(event)
  const dropTarget = resolveDynamicBookDropTarget(event, `linux-do-book-${pointerDragLinuxDoBookId}`)
  dragOverShelfIndex.value = dropTarget?.shelfIndex ?? null
  dragOverPlacementKey.value = dropTarget?.beforePlacementKey ?? null
}

async function handleLinuxDoBookPointerUp(event: PointerEvent) {
  window.removeEventListener('pointermove', handleLinuxDoBookPointerMove)
  window.removeEventListener('pointerup', handleLinuxDoBookPointerUp)
  window.removeEventListener('pointercancel', handleLinuxDoBookPointerCancel)
  const bookId = pointerDragLinuxDoBookId
  const book = linuxDoBooks.value.find((item) => item.id === bookId)
  const dropTarget = pointerDragLinuxDoBookId
    ? resolveDynamicBookDropTarget(event, `linux-do-book-${pointerDragLinuxDoBookId}`)
    : null
  if (pointerMoved && book && dropTarget) {
    event.preventDefault()
    suppressNextBookClick = true
    if (dropTarget.folderId) {
      try {
        book.bookshelf_placement = await updateLinuxDoBookPlacement(book.id, {
          bookshelf_id: selectedBookshelfId.value,
          shelf_index: dropTarget.shelfIndex,
          position_index: dropTarget.positionIndex,
          orientation: book.bookshelf_placement.orientation,
          folder_id: dropTarget.folderId,
        })
        linuxDoBooks.value = [...linuxDoBooks.value]
        await reloadFolders()
      } catch (error) {
        console.warn('Failed to move LINUX DO book:', error)
        ElMessage.error('保存动态书本位置失败')
      }
    } else {
      movePlacementToShelf(
        `linux-do-book-${book.id}`,
        dropTarget.shelfIndex,
        dropTarget.beforePlacementKey,
      )
    }
  }
  handleLinuxDoBookPointerCancel()
  setTimeout(() => {
    suppressNextBookClick = false
  }, 0)
}

function handleLinuxDoBookPointerCancel() {
  window.removeEventListener('pointermove', handleLinuxDoBookPointerMove)
  window.removeEventListener('pointerup', handleLinuxDoBookPointerUp)
  pointerDragLinuxDoBookId = null
  pointerMoved = false
  draggingLinuxDoBookId.value = null
  dragOverShelfIndex.value = null
  dragOverPlacementKey.value = null
  bookDragOffsetX.value = 0
  bookDragOffsetY.value = 0
}

function openSkillBookMetadataEditor() {
  skillBookContextMenu.value = null
  skillBookPageFormat.value = skillBookCatalog.value?.page_format ?? 'A4'
  skillBookStartDate.value = skillBookCatalog.value?.start_date ?? ''
  skillBookMetadataVisible.value = true
}

async function saveSkillBookMetadata() {
  skillBookMetadataSaving.value = true
  try {
    skillBookCatalog.value = await updateLocalSkillBookMetadata({
      page_format: skillBookPageFormat.value,
      start_date: skillBookStartDate.value.trim(),
    })
    skillBookMetadataVisible.value = false
    skillBookReadingState.value = await fetchLocalSkillBookReadingState()
    ElMessage.success('动态书本元数据已保存')
  } catch (error) {
    console.warn('Failed to update Skill book metadata:', error)
    ElMessage.error('保存动态书本元数据失败')
  } finally {
    skillBookMetadataSaving.value = false
  }
}

function handleSkillBookCatalogUpdated(catalog: SkillBookCatalog) {
  skillBookCatalog.value = catalog
}

function handleSkillBookReadingStateUpdated(state: SkillBookReadingState) {
  skillBookReadingState.value = state
}

async function reloadSkillBookCatalog() {
  if (!selectedBookshelfId.value) {
    skillBookCatalog.value = null
    skillBookReadingState.value = null
    return
  }
  try {
    skillBookCatalog.value = await fetchLocalSkillBookCatalog(selectedBookshelfId.value)
  } catch (error) {
    if ((error as { response?: { status?: number } }).response?.status !== 404) {
      console.warn('Failed to load local Skill book:', error)
    }
    skillBookCatalog.value = null
    skillBookReadingState.value = null
    return
  }
  try {
    skillBookReadingState.value = await fetchLocalSkillBookReadingState()
  } catch (error) {
    console.warn('Failed to load local Skill reading state:', error)
    skillBookReadingState.value = null
  }
}

async function fetchSkillBookBundle(bookshelfId: string) {
  const [catalogResult, stateResult] = await Promise.allSettled([
    fetchLocalSkillBookCatalog(bookshelfId),
    fetchLocalSkillBookReadingState(),
  ])
  if (catalogResult.status === 'rejected') {
    if ((catalogResult.reason as { response?: { status?: number } }).response?.status !== 404) {
      throw catalogResult.reason
    }
    return { catalog: null, readingState: null }
  }
  if (stateResult.status === 'rejected') {
    console.warn('Failed to load local Skill reading state:', stateResult.reason)
  }
  return {
    catalog: catalogResult.value,
    readingState: stateResult.status === 'fulfilled' ? stateResult.value : null,
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

function parseHexColor(color?: string | null): [number, number, number] | null {
  const match = /^#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/i.exec(color?.trim() ?? '')
  if (!match) return null
  const hex = match[1]
  const expanded = hex.length <= 4
    ? Array.from(hex.slice(0, 3), channel => channel + channel).join('')
    : hex.slice(0, 6)
  const value = Number.parseInt(expanded, 16)
  return [
    (value >> 16) & 255,
    (value >> 8) & 255,
    value & 255,
  ]
}

function relativeLuminance([red, green, blue]: [number, number, number]) {
  const linearChannel = (channel: number) => {
    const srgb = channel / 255
    return srgb <= 0.04045
      ? srgb / 12.92
      : ((srgb + 0.055) / 1.055) ** 2.4
  }
  return linearChannel(red) * 0.2126
    + linearChannel(green) * 0.7152
    + linearChannel(blue) * 0.0722
}

function contrastRatio(left: number, right: number) {
  const lighter = Math.max(left, right)
  const darker = Math.min(left, right)
  return (lighter + 0.05) / (darker + 0.05)
}

function coverInkColor(coverColor?: string | null) {
  const background = parseHexColor(coverColor)
  if (!background) return '#ffffff'
  const darkInk = '#000000'
  const lightInk = '#ffffff'
  const backgroundLuminance = relativeLuminance(background)
  const darkLuminance = relativeLuminance(parseHexColor(darkInk)!)
  const lightLuminance = relativeLuminance(parseHexColor(lightInk)!)
  return contrastRatio(backgroundLuminance, darkLuminance)
    >= contrastRatio(backgroundLuminance, lightLuminance)
    ? darkInk
    : lightInk
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

function verticalTitleSegments(title: string) {
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

function bookSpineTitleSegments(document: PdfDocumentSummary) {
  const title = bookSpineTitle(document)
  if (bookOrientation(document) !== 'spine_vertical') {
    return [{ text: title, combined: false }] satisfies BookTitleSegment[]
  }
  return verticalTitleSegments(title)
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

function verticalTitleCellCount(title: string) {
  return verticalTitleSegments(title).reduce(
    (count, segment) => count + bookTitleSegmentCellCount(segment),
    0,
  )
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

function millimetersToPoints(value: number) {
  return value * 72 / 25.4
}

function physicalBookHeight(pageHeightPoints: number) {
  return Math.min(
    MAX_BOOK_HEIGHT,
    Math.max(MIN_BOOK_HEIGHT, Math.round(pageHeightPoints * BOOK_PAGE_SCALE)),
  )
}

function systemBookSpineGeometry(pageCount: number) {
  const safePageCount = Math.max(1, pageCount)
  const spineWidth = Math.round(
    SYSTEM_MIN_SPINE_WIDTH * (1 + Math.log10(safePageCount)),
  )
  const baseSpineWidth = spineWidth
  return { baseSpineWidth, spineWidth }
}

function dynamicBookOrientationClass(orientation: PdfBookshelfOrientation | undefined) {
  return `orientation-${(orientation ?? 'spine_vertical').replace('_', '-')}`
}

function dynamicBookSpineStyle(options: {
  title: string
  author: string
  startDate?: string
  pageCount: number
  pageWidthMm: number
  pageHeightMm: number
  orientation: PdfBookshelfOrientation
  coverColor: string
  dragX: number
  dragY: number
  readingProgress: number
}) {
  const pageCount = Math.max(1, options.pageCount)
  const pageHeightPoints = millimetersToPoints(options.pageHeightMm)
  const pageWidthPoints = millimetersToPoints(options.pageWidthMm)
  const bookHeight = physicalBookHeight(pageHeightPoints)
  const { baseSpineWidth, spineWidth } = systemBookSpineGeometry(pageCount)
  const screenScale = bookHeight / pageHeightPoints
  const pageDepth = Math.min(210, Math.max(120, Math.round(pageWidthPoints * screenScale)))
  const titleGlyphCount = Math.max(4, verticalTitleCellCount(options.title))
  let titleFontSize = physicalSpineTitleFontSize(
    baseSpineWidth,
    spineWidth,
    bookHeight,
    titleGlyphCount,
  )
  if (options.orientation === 'spine_vertical') {
    titleFontSize = verticalSpineTitleFontSize(spineWidth, titleFontSize)
  } else if (options.orientation === 'spine_horizontal') {
    titleFontSize = Math.min(
      titleFontSize,
      Math.max(MIN_SPINE_FONT_SIZE, Math.floor((bookHeight - 30) / titleGlyphCount * 0.95)),
    )
  }
  const itemWidth = options.orientation === 'spine_vertical'
    ? Math.max(spineWidth, MIN_BOOK_INTERACTION_WIDTH)
    : options.orientation === 'spine_horizontal'
      ? bookHeight
      : pageDepth
  const showTitle = options.orientation === 'cover_front'
    || spineWidth >= (options.orientation === 'spine_vertical'
      ? MIN_SPINE_WIDTH_FOR_COMPACT_TITLE
      : MIN_SPINE_WIDTH_FOR_TITLE)
  const compactVerticalTitle = options.orientation === 'spine_vertical'
    && spineWidth < MIN_SPINE_WIDTH_FOR_TITLE
  const author = options.author.trim()
  const authorFontSize = Math.min(15, Math.max(10, Math.round(titleFontSize * 0.48)))
  const coverFontSize = Math.min(28, Math.max(16, Math.round(Math.min(pageDepth / 6, bookHeight / 8))))
  const showAuthor = showTitle && Boolean(author) && (
    options.orientation !== 'spine_vertical'
    || spineWidth >= titleFontSize + authorFontSize + 8
  )
  const readingProgress = Math.max(0, Math.min(1, options.readingProgress))
  return {
    '--spine-width': `${spineWidth}px`,
    '--spine-height': `${bookHeight}px`,
    '--page-depth': `${pageDepth}px`,
    '--cover-flat-height': `${bookHeight}px`,
    '--book-item-width': `${itemWidth}px`,
    '--spine-font-size': `${titleFontSize}px`,
    '--cover-font-size': `${coverFontSize}px`,
    '--book-title-display': showTitle ? 'block' : 'none',
    '--book-spine-inline-padding': showTitle && !compactVerticalTitle ? '5px' : '0px',
    '--book-spine-title-line-height': compactVerticalTitle ? '1' : '1.35',
    '--book-spine-title-scale-x': '1',
    '--book-spine-justify-content': 'center',
    '--book-author-display': showAuthor ? 'block' : 'none',
    '--book-start-year-display': options.startDate && !showAuthor ? 'block' : 'none',
    '--book-author-font-size': `${authorFontSize}px`,
    '--book-cover-color': options.coverColor,
    '--book-cover-ink': coverInkColor(options.coverColor),
    '--book-physical-border-width': '1px',
    '--book-lean': '0deg',
    '--book-drag-x': `${options.dragX}px`,
    '--book-drag-y': `${options.dragY}px`,
    '--book-reading-progress': `${((1 - readingProgress) * 100).toFixed(2)}%`,
    '--book-cover-bookmark-progress': `${(readingProgress * 100).toFixed(2)}%`,
  }
}

function physicalSpineTitleFontSize(
  baseSpineWidth: number,
  spineWidth: number,
  bookHeight: number,
  titleCellCount: number,
) {
  const widthRatio = (baseSpineWidth - SYSTEM_MIN_SPINE_WIDTH)
    / (SYSTEM_REFERENCE_SPINE_WIDTH - SYSTEM_MIN_SPINE_WIDTH)
  const heightRatio = (bookHeight - MIN_BOOK_HEIGHT) / (MAX_BOOK_HEIGHT - MIN_BOOK_HEIGHT)
  const sizeRatio = Math.min(1, Math.max(0, widthRatio * 0.75 + heightRatio * 0.25))
  const glyphCount = Math.max(4, titleCellCount)
  const widthFontLimit = spineWidth * 0.46
  const heightFontLimit = (bookHeight - SPINE_VERTICAL_TEXT_HEIGHT_INSET) / glyphCount
  const geometryFontLimit = MIN_SPINE_FONT_SIZE
    + (MAX_SPINE_FONT_SIZE - MIN_SPINE_FONT_SIZE) * (0.45 + sizeRatio * 0.55)
  return Math.floor(Math.min(
    MAX_SPINE_FONT_SIZE,
    Math.max(
      MIN_SPINE_FONT_SIZE,
      Math.min(widthFontLimit, heightFontLimit, geometryFontLimit),
    ),
  ))
}

function verticalSpineTitleFontSize(spineWidth: number, preferredFontSize: number) {
  if (spineWidth >= MIN_SPINE_WIDTH_FOR_TITLE) {
    return preferredFontSize
  }
  return Math.min(
    preferredFontSize,
    Math.max(MIN_COMPACT_SPINE_FONT_SIZE, Math.floor(spineWidth - 2)),
  )
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
  const bookHeight = physicalBookHeight(pageHeight)
  // Every resource type shares the same system-owned logarithmic page-to-thickness
  // mapping. Interaction width remains separate from the visual spine width.
  const { baseSpineWidth, spineWidth } = systemBookSpineGeometry(
    pageCount,
  )
  const screenScale = bookHeight / pageHeight
  const pageDepth = Math.min(210, Math.max(120, Math.round(pageWidth * screenScale)))
  return { pageCount, bookHeight, baseSpineWidth, spineWidth, pageDepth }
}

function bookTitleFontSize(document: PdfDocumentSummary) {
  const { bookHeight, baseSpineWidth, spineWidth } = bookPhysicalGeometry(document)
  const titleGlyphCount = Math.max(4, bookTitleCellCount(document))
  let titleFontSize = physicalSpineTitleFontSize(
    baseSpineWidth,
    spineWidth,
    bookHeight,
    bookTitleCellCount(document),
  )
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
  const titleGlyphCount = Math.max(4, bookTitleCellCount(document))
  const orientation = bookOrientation(document)
  const preferredTitleFontSize = bookTitleFontSize(document)
  const titleFontSize = orientation === 'spine_vertical'
    ? verticalSpineTitleFontSize(spineWidth, preferredTitleFontSize)
    : preferredTitleFontSize
  const itemWidth = orientation === 'spine_vertical'
    ? Math.max(spineWidth, MIN_BOOK_INTERACTION_WIDTH)
    : orientation === 'spine_horizontal'
      ? bookHeight
      : pageDepth
  const showTitle = orientation === 'cover_front'
    || spineWidth >= (orientation === 'spine_vertical'
      ? MIN_SPINE_WIDTH_FOR_COMPACT_TITLE
      : MIN_SPINE_WIDTH_FOR_TITLE)
  const compactVerticalTitle = orientation === 'spine_vertical'
    && spineWidth < MIN_SPINE_WIDTH_FOR_TITLE
  const physicalBorderWidth = orientation === 'cover_front' || spineWidth >= 2 ? 1 : 0
  const coverFlatHeight = bookHeight
  const coverFontSize = Math.min(28, Math.max(16, Math.round(Math.min(pageDepth / 6, coverFlatHeight / 8))))
  const author = bookAuthorWithYear(document.display_author, document.start_date)
  const authorGlyphCount = Array.from(author.replace(/\s+/g, '')).length
  const authorFontSize = Math.min(15, Math.max(10, Math.round(titleFontSize * 0.48)))
  const qualifier = bookSpineQualifier(document)
  const qualifierFontSize = Math.min(14, Math.max(10, Math.round(titleFontSize * 0.58)))
  const horizontalAuthorWidth = authorGlyphCount * authorFontSize * 0.9
  const horizontalTwoLineHeight = titleFontSize * 1.35 + authorFontSize * 1.25 + 18
  const verticalColumnCount = qualifier ? 3 : 2
  const verticalAuthorRequiredWidth = titleFontSize
    + authorFontSize
    + (qualifier ? qualifierFontSize : 0)
    + (verticalColumnCount - 1) * 2
    + 8
    + physicalBorderWidth * 2
  const showAuthor = showTitle && authorGlyphCount > 0 && (
    orientation === 'spine_vertical'
      ? spineWidth >= verticalAuthorRequiredWidth
      : orientation === 'spine_horizontal'
        ? spineWidth >= horizontalTwoLineHeight
          && bookHeight - 24 >= horizontalAuthorWidth
        : bookHeight - 40 >= Math.ceil(
          titleGlyphCount * coverFontSize * 0.95 / Math.max(pageDepth - 32, 1),
        ) * coverFontSize * 1.45 + authorFontSize * 1.35 + 12
  )
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
    '--book-qualifier-display': showTitle && !compactVerticalTitle ? 'block' : 'none',
    '--book-spine-inline-padding': showAuthor && orientation === 'spine_vertical'
      ? '4px'
      : showTitle && !compactVerticalTitle ? '5px' : '0px',
    '--book-spine-title-line-height': compactVerticalTitle || (showAuthor && orientation === 'spine_vertical')
      ? '1'
      : '1.35',
    '--book-spine-block-padding': showTitle ? '7px' : '0px',
    '--book-spine-justify-content': 'center',
    '--book-physical-border-width': `${physicalBorderWidth}px`,
    '--book-drag-x': document.id === draggingPdfId.value ? `${bookDragOffsetX.value}px` : '0px',
    '--book-drag-y': document.id === draggingPdfId.value ? `${bookDragOffsetY.value}px` : '0px',
    '--cover-font-size': `${coverFontSize}px`,
    '--book-author-font-size': `${authorFontSize}px`,
    '--book-author-display': showAuthor ? 'block' : 'none',
    '--book-author-line-height': showAuthor && orientation === 'spine_vertical' ? '1' : '1.2',
    '--book-start-year-display': document.start_date && !showAuthor ? 'block' : 'none',
    '--book-qualifier-font-size': `${qualifierFontSize}px`,
    '--book-qualifier-line-height': showAuthor && orientation === 'spine_vertical' ? '1' : '1.2',
    '--book-lean': '0deg',
    '--book-reading-progress': `${(bookmarkPagePosition * 100).toFixed(2)}%`,
    '--book-cover-bookmark-progress': `${(readingProgress * 100).toFixed(2)}%`,
    '--book-bookmark-tilt': `${bookmarkTilt}deg`,
    '--book-cover-color': document.appearance.cover_color_override ?? metadata.cover_average_color ?? undefined,
    '--book-cover-ink': coverInkColor(document.appearance.cover_color_override ?? metadata.cover_average_color),
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
  if (!document.my_state || document.my_state.current_page < 1) {
    return false
  }
  return bookOrientation(document) === 'cover_front'
    || bookPhysicalGeometry(document).spineWidth >= MIN_SPINE_WIDTH_FOR_BOOKMARK
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
    ...(document.start_date ? [tooltipField('起始时间', document.start_date)] : []),
    tooltipField('原文件', document.title, true),
    tooltipField('页码', `${currentPageLabel}/${pageCountLabel}`),
  ]
  return fields.join('\n')
}

function contentDrivenBookshelfRowCount() {
  const occupiedShelfIndices = [
    ...documents.value.map((document) => document.bookshelf_placement?.shelf_index ?? 0),
    ...libraryFolders.value.map((folder) => folder.shelf_index),
    ...Object.values(wallSitePositions.value).map((position) => position.shelfIndex),
    ...linuxDoBooks.value.map((book) => book.bookshelf_placement.shelf_index),
    ...(showLocalSkillBook.value && skillBookCatalog.value
      ? [skillBookCatalog.value.bookshelf_placement.shelf_index]
      : []),
  ]
  const maxShelfIndex = occupiedShelfIndices.length
    ? Math.max(...occupiedShelfIndices)
    : -1
  return Math.max(
    DEFAULT_BOOKSHELF_ROW_COUNT,
    maxShelfIndex + 1 + TRAILING_EMPTY_BOOKSHELF_ROW_COUNT,
  )
}

function buildBookshelfRows() {
  const originalOrder = new Map(documents.value.map((document, index) => [document.id, index]))
  const rowCount = Math.max(
    bookshelfCanvasMinRowCount.value,
    contentDrivenBookshelfRowCount(),
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

  for (const document of row) {
    if (bookOrientation(document) !== 'spine_horizontal') {
      currentStack = null
      groups.push({
        key: `book-${document.id}`,
        kind: 'single',
        documents: [document],
      })
      continue
    }

    if (!currentStack) {
      currentStack = {
        key: `stack-${document.id}`,
        kind: 'horizontal-stack',
        documents: [],
      }
      groups.push(currentStack)
    }
    currentStack.documents.push(document)
  }

  return groups
}

function clearBookDragState() {
  draggingPdfId.value = null
  dragOverPdfId.value = null
  dragOverShelfIndex.value = null
  dragOverPlacementKey.value = null
  bookDragOffsetX.value = 0
  bookDragOffsetY.value = 0
}

function closeBookContextMenu() {
  bookContextMenu.value = null
  skillBookContextMenu.value = null
  linuxDoBookContextMenu.value = null
}

function closeBookshelfContextMenu() {
  bookshelfContextMenu.value = null
}

function closeShelfContextMenu() {
  shelfContextMenu.value = null
}

function closeContextMenus() {
  closeBookContextMenu()
  closeBookshelfContextMenu()
  closeShelfContextMenu()
}

function openBookContextMenu(event: MouseEvent, pdfId: number) {
  event.preventDefault()
  event.stopPropagation()
  closeBookshelfContextMenu()
  closeShelfContextMenu()
  const menuWidth = 176
  const document = documents.value.find((item) => item.id === pdfId)
  const canDelete = document ? canDeleteBook(document) : false
  const actionCount = (selectedBookshelfIsOwned.value ? 1 : 0)
    + (document && canEditBookMetadata(document) ? 1 : 0)
    + (!selectedBookshelfIsOwned.value ? 1 : 0)
    + (canDelete ? 1 : 0)
  const menuHeight = 8 + actionCount * 32 + (canDelete ? 9 : 0)
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
  closeShelfContextMenu()
  const menuWidth = 128
  const bookshelf = bookshelves.value.find((item) => item.id === bookshelfId)
  const menuHeight = bookshelf?.is_owned
    ? (bookshelf.book_count === 0 && bookshelf.folder_count === 0 ? 116 : 76)
    : 42
  bookshelfContextMenu.value = {
    bookshelfId,
    x: Math.max(8, Math.min(event.clientX, window.innerWidth - menuWidth - 8)),
    y: Math.max(8, Math.min(event.clientY, window.innerHeight - menuHeight - 8)),
  }
}

function handleContextMenuKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    closeContextMenus()
    selectedWallSiteIds.value = []
    finishWallSelection()
  }
}

function nextBookOrientation(orientation: PdfBookshelfOrientation): PdfBookshelfOrientation {
  const currentIndex = BOOK_ORIENTATION_CYCLE.indexOf(orientation)
  return BOOK_ORIENTATION_CYCLE[(currentIndex + 1) % BOOK_ORIENTATION_CYCLE.length] ?? 'spine_vertical'
}

function rotateBook(pdfId: number) {
  if (!selectedBookshelfIsOwned.value) {
    return
  }
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

async function rotateContextSkillBook() {
  const catalog = skillBookCatalog.value
  closeBookContextMenu()
  if (!catalog || !catalog.is_owned) {
    return
  }
  try {
    catalog.bookshelf_placement = await updateLocalSkillBookPlacement({
      bookshelf_id: catalog.bookshelf_placement.bookshelf_id,
      shelf_index: catalog.bookshelf_placement.shelf_index,
      position_index: catalog.bookshelf_placement.position_index,
      orientation: nextBookOrientation(catalog.bookshelf_placement.orientation),
      folder_id: catalog.bookshelf_placement.folder_id ?? null,
    })
    skillBookCatalog.value = { ...catalog }
  } catch (error) {
    console.warn('Failed to rotate local Skill book:', error)
    ElMessage.error('保存图书旋转状态失败')
  }
}

async function rotateContextLinuxDoBook() {
  const bookId = linuxDoBookContextMenu.value?.bookId
  const book = linuxDoBooks.value.find((item) => item.id === bookId)
  closeBookContextMenu()
  if (!book || !selectedBookshelfIsOwned.value) {
    return
  }
  try {
    book.bookshelf_placement = await updateLinuxDoBookPlacement(book.id, {
      ...book.bookshelf_placement,
      orientation: nextBookOrientation(book.bookshelf_placement.orientation),
    })
    linuxDoBooks.value = [...linuxDoBooks.value]
  } catch (error) {
    console.warn('Failed to rotate dynamic book:', error)
    ElMessage.error('保存图书旋转状态失败')
  }
}

function openCopyBookDialog() {
  copyBookPdfId.value = bookContextMenu.value?.pdfId ?? null
  closeBookContextMenu()
  copyTargetBookshelfId.value = ownedBookshelves.value[0]?.id ?? ''
  copyIncludeNotes.value = true
  copyBookVisible.value = copyBookPdfId.value != null && Boolean(copyTargetBookshelfId.value)
}

async function saveCopyBook() {
  if (copyBookPdfId.value == null || !copyTargetBookshelfId.value) return
  copyBookSaving.value = true
  try {
    await copyPdfToOwnLibrary(copyBookPdfId.value, {
      target_bookshelf_id: copyTargetBookshelfId.value,
      shelf_index: 0,
      include_notes: copyIncludeNotes.value,
      include_reading_progress: false,
    })
    copyBookVisible.value = false
    await reloadBookshelves()
    ElMessage.success('已复制到自己的书柜，笔记与原书互不影响')
  } catch (error) {
    console.warn('Failed to copy shared PDF:', error)
    ElMessage.error('复制图书失败')
  } finally {
    copyBookSaving.value = false
  }
}

function canEditBookMetadata(document: PdfDocumentSummary) {
  return document.access.role === 'editor' || document.access.role === 'manager'
}

function canDeleteBook(document: PdfDocumentSummary) {
  return selectedBookshelfIsOwned.value && document.bookshelf_placement != null
}

const canDeleteBookOriginalFile = computed(() => {
  const document = deleteBookDialogDocument.value
  return Boolean(document && (
    document.owner_user_id === currentUserId.value
    || userStore.user?.is_superuser
  ))
})

function releaseBookCoverImage(pdfId: number) {
  const imageUrl = bookCoverImageUrls.value.get(pdfId)
  if (!imageUrl) return
  URL.revokeObjectURL(imageUrl)
  const nextUrls = new Map(bookCoverImageUrls.value)
  nextUrls.delete(pdfId)
  bookCoverImageUrls.value = nextUrls
}

function deleteContextBook() {
  const pdfId = bookContextMenu.value?.pdfId
  const document = documents.value.find((item) => item.id === pdfId)
  closeBookContextMenu()
  if (!document || !canDeleteBook(document)) return
  deleteBookDialogDocument.value = document
  deleteBookDialogBookshelfName.value = selectedBookshelf.value?.name || '当前书柜'
  deleteBookDialogVisible.value = true
}

async function deleteBookReference() {
  const document = deleteBookDialogDocument.value
  if (!document || deleteBookDialogSaving.value) return
  deleteBookDialogSaving.value = true
  try {
    await removePdfDocumentFromMyLibrary(document.id)
    releaseBookCoverImage(document.id)
    deleteBookDialogVisible.value = false
    await Promise.all([reloadBookshelves(), reloadDocuments({ silent: true }), reloadFolders()])
    ElMessage.success('图书引用已删除')
  } catch (error) {
    console.warn('Failed to remove PDF reference:', error)
    ElMessage.error('删除图书引用失败')
  } finally {
    deleteBookDialogSaving.value = false
  }
}

async function deleteBookOriginalFile() {
  const document = deleteBookDialogDocument.value
  if (!document || !canDeleteBookOriginalFile.value || deleteBookDialogSaving.value) return
  deleteBookDialogSaving.value = true
  try {
    await deletePdfDocument(document.id)
    releaseBookCoverImage(document.id)
    deleteBookDialogVisible.value = false
    await Promise.all([reloadBookshelves(), reloadDocuments({ silent: true }), reloadFolders()])
    ElMessage.success('图书原文件已删除')
  } catch (error) {
    console.warn('Failed to delete PDF source resource:', error)
    ElMessage.error('删除图书原文件失败')
  } finally {
    deleteBookDialogSaving.value = false
  }
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
  metadataEditorStartDate.value = document.start_date
  metadataEditorSubtitle.value = document.display_subtitle
  metadataEditorTranslator.value = document.display_translator
  metadataEditorEdition.value = document.display_edition
  metadataEditorVolume.value = document.display_volume
  metadataEditorSourceName.value = document.title
  metadataEditorImportedFilename.value = document.imported_filename
  metadataEditorDescription.value = document.description
  metadataEditorTags.value = document.tags.join('，')
  metadataEditorCoverColor.value = document.appearance.cover_color_override ?? ''
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
      start_date: metadataEditorStartDate.value.trim(),
      display_subtitle: metadataEditorSubtitle.value.trim(),
      display_translator: metadataEditorTranslator.value.trim(),
      display_edition: metadataEditorEdition.value.trim(),
      display_volume: metadataEditorVolume.value.trim(),
      source_display_name: metadataEditorSourceName.value.trim() || null,
      description: metadataEditorDescription.value.trim(),
      tags: metadataEditorTags.value.split(/[，,]/).map((tag) => tag.trim()).filter(Boolean),
      cover_color_override: metadataEditorCoverColor.value.trim() || null,
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

function folderStyle(folder: LibraryFolder) {
  const automaticThickness = Math.max(folder.min_thickness_mm ?? 4, 4 + folder.member_count * 0.8)
  const thickness = folder.fixed_thickness_mm ?? automaticThickness
  return {
    '--folder-width': `${Math.max(28, Math.min(100, Math.round(thickness * 3)))}px`,
    '--folder-color': folder.color_override || '#58718a',
  }
}

async function createFolderAtShelf(shelfIndex: number) {
  if (!selectedBookshelfId.value || !selectedBookshelfIsOwned.value) return
  try {
    const { value } = await ElMessageBox.prompt('资料夹用于收纳较薄的一批文件', '新建资料夹', {
      inputValue: '资料夹',
      confirmButtonText: '新建',
      cancelButtonText: '取消',
      inputValidator: (name) => name.trim() ? true : '请输入资料夹名称',
    })
    await createLibraryFolder(selectedBookshelfId.value, value.trim(), shelfIndex)
    await reloadFolders()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error('新建资料夹失败')
  }
}

function handleShelfContextMenu(event: MouseEvent, shelfIndex: number) {
  event.preventDefault()
  const clickedOnBookObject = event.composedPath().some((target) => (
    target instanceof Element
      && target.matches('.book-item, .book-group, .library-folder, .bookshelf-wall-site, .book-context-menu')
  ))
  if (clickedOnBookObject || !selectedBookshelfIsOwned.value) return
  event.stopPropagation()
  closeContextMenus()
  const row = event.currentTarget instanceof HTMLElement ? event.currentTarget : null
  const wall = row?.querySelector<HTMLElement>('.bookshelf-wall-sites')
  const bounds = wall?.getBoundingClientRect() ?? row?.getBoundingClientRect()
  const width = bounds?.width ?? 1
  const height = bounds?.height ?? 1
  const xRatio = clampWallSiteRatio(event.clientX - (bounds?.left ?? 0), width, 64)
  const yRatio = clampWallSiteRatio(event.clientY - (bounds?.top ?? 0), height, 72)
  const menuWidth = 152
  const menuHeight = 72
  shelfContextMenu.value = {
    shelfIndex,
    x: Math.max(8, Math.min(event.clientX, window.innerWidth - menuWidth - 8)),
    y: Math.max(8, Math.min(event.clientY, window.innerHeight - menuHeight - 8)),
    xRatio,
    yRatio,
  }
}

function createFolderFromShelfMenu() {
  const shelfIndex = shelfContextMenu.value?.shelfIndex
  closeContextMenus()
  if (shelfIndex == null) return
  void createFolderAtShelf(shelfIndex)
}

function openFolder(folder: LibraryFolder) {
  openedFolder.value = folder
  folderContentsVisible.value = true
}

function openFolderEditor(folder: LibraryFolder) {
  if (!selectedBookshelfIsOwned.value) {
    openFolder(folder)
    return
  }
  editingFolder.value = folder
  folderEditorName.value = folder.name
  folderEditorColor.value = folder.color_override ?? ''
  folderEditorMinThickness.value = folder.min_thickness_mm ?? undefined
  folderEditorFixedThickness.value = folder.fixed_thickness_mm ?? undefined
  folderEditorVisible.value = true
}

async function saveFolderEditor() {
  if (!editingFolder.value || !folderEditorName.value.trim()) return
  folderEditorSaving.value = true
  try {
    await updateLibraryFolder(editingFolder.value.id, {
      name: folderEditorName.value.trim(),
      color_override: folderEditorColor.value.trim() || null,
      min_thickness_mm: folderEditorMinThickness.value ?? null,
      fixed_thickness_mm: folderEditorFixedThickness.value ?? null,
    })
    folderEditorVisible.value = false
    await reloadFolders()
  } catch (error) {
    ElMessage.error('保存资料夹失败')
  } finally {
    folderEditorSaving.value = false
  }
}

async function removeOpenedFolder() {
  const folder = openedFolder.value
  if (!folder || folder.member_count > 0) return
  try {
    await ElMessageBox.confirm('只允许删除空资料夹。确定删除？', '删除资料夹', { type: 'warning' })
    await deleteLibraryFolder(folder.id)
    folderContentsVisible.value = false
    await reloadFolders()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error('删除资料夹失败')
  }
}

async function takeBookOutOfFolder(document: PdfDocumentSummary) {
  const shelfIndex = openedFolder.value?.shelf_index ?? 0
  await movePdfToLibraryFolder(document.id, null, shelfIndex)
  await Promise.all([reloadDocuments({ silent: true }), reloadFolders()])
}

async function takeSkillBookOutOfFolder() {
  const catalog = skillBookCatalog.value
  if (!catalog || !openedFolder.value) return
  catalog.bookshelf_placement = await updateLocalSkillBookPlacement({
    bookshelf_id: selectedBookshelfId.value,
    shelf_index: openedFolder.value.shelf_index,
    position_index: openedFolder.value.position_index + 1,
    orientation: catalog.bookshelf_placement.orientation,
    folder_id: null,
  })
  skillBookCatalog.value = { ...catalog }
  await reloadFolders()
}

async function reloadFolders() {
  if (!selectedBookshelfId.value) {
    libraryFolders.value = []
    return
  }
  try {
    libraryFolders.value = await fetchLibraryFolders(selectedBookshelfId.value)
  } catch (error) {
    console.warn('Failed to load library folders:', error)
    libraryFolders.value = []
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
  if (event.button !== 0 || !selectedBookshelfIsOwned.value) {
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
  growBookshelfNearPointer(event)
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
    dragOverPlacementKey.value = `book-${targetPdfId}`
  } else {
    handleShelfDragOver(shelfIndex)
    const targetGroupKey = target?.closest<HTMLElement>('.book-group')?.dataset.groupKey ?? null
    dragOverPlacementKey.value = targetGroupKey === `book-${pointerDragPdfId}` ? null : targetGroupKey
  }
}

function handleBookPointerUp(event: PointerEvent) {
  if (event.button !== 0) {
    return
  }
  window.removeEventListener('pointermove', handleBookPointerMove)
  window.removeEventListener('pointerup', handleBookPointerUp)
  window.removeEventListener('pointercancel', handleBookPointerCancel)
  window.removeEventListener('pointermove', handleLinuxDoBookPointerMove)
  window.removeEventListener('pointerup', handleLinuxDoBookPointerUp)
  window.removeEventListener('pointercancel', handleLinuxDoBookPointerCancel)
  window.removeEventListener('contextmenu', handleDragRotateContextMenu, { capture: true })
  if (pointerMoved) {
    event.preventDefault()
    suppressNextBookClick = true
    const hitFolder = document.elementFromPoint(event.clientX, event.clientY)
      ?.closest<HTMLElement>('.library-folder')
    const folderId = hitFolder?.dataset.folderId
    if (folderId && pointerDragPdfId != null) {
      const pdfId = pointerDragPdfId
      clearBookDragState()
      void movePdfToLibraryFolder(pdfId, folderId).then(async () => {
        await Promise.all([reloadDocuments({ silent: true }), reloadFolders()])
      }).catch(() => ElMessage.error('放入资料夹失败'))
    } else if (dragOverShelfIndex.value != null) {
      const shelfIndex = dragOverShelfIndex.value
      const beforePlacementKey = dragOverPlacementKey.value
      moveBookToShelf(shelfIndex, beforePlacementKey)
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
  dragOverPlacementKey.value = null
}

function moveBookToShelf(shelfIndex: number, beforePlacementKey: string | null = null) {
  const pdfId = draggingPdfId.value
  if (pdfId == null) {
    return
  }
  movePlacementToShelf(`book-${pdfId}`, shelfIndex, beforePlacementKey)
}

function movePlacementToShelf(
  movingPlacementKey: string,
  shelfIndex: number,
  beforePlacementKey: string | null = null,
) {
  const rows = buildUnifiedLibraryLayoutRows()
  let movingItem: BookshelfPlacementItem | null = null
  for (const row of rows) {
    const itemIndex = row.findIndex((item) => item.key === movingPlacementKey)
    if (itemIndex >= 0) {
      movingItem = row.splice(itemIndex, 1)[0] ?? null
      break
    }
  }
  if (!movingItem) {
    clearBookDragState()
    return
  }
  while (rows.length <= shelfIndex) {
    rows.push([])
  }
  const targetRow = rows[shelfIndex]
  const targetIndex = beforePlacementKey == null
    ? targetRow.length
    : targetRow.findIndex((item) => item.key === beforePlacementKey)
  targetRow.splice(targetIndex >= 0 ? targetIndex : targetRow.length, 0, movingItem)

  clearBookDragState()
  applyUnifiedLibraryLayoutRows(rows)
}

function queueBookshelfLayoutSave(placements: PdfBookshelfPlacement[]) {
  if (!selectedBookshelfIsOwned.value) {
    return
  }
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

function queueUnifiedLibraryLayoutSave(items: LibraryBookshelfLayoutItem[]) {
  if (!selectedBookshelfIsOwned.value || !selectedBookshelfId.value) {
    return
  }
  const bookshelfId = selectedBookshelfId.value
  layoutSaveQueue = layoutSaveQueue.then(async () => {
    try {
      await updateLibraryBookshelfLayout(bookshelfId, items)
    } catch (error) {
      console.warn(
        'Failed to save unified library layout:',
        error,
        (error as { response?: { data?: unknown } })?.response?.data,
      )
      ElMessage.error('保存书柜位置失败，已恢复服务器布局')
      await Promise.all([
        reloadDocuments({ silent: true }),
        reloadSkillBookCatalog(),
        reloadLinuxDoBooks(),
        reloadFolders(),
      ])
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
  const [mineResult, sharedResult] = await Promise.allSettled([
    fetchPdfBookshelves('mine'),
    fetchPdfBookshelves('shared'),
  ])
  if (mineResult.status === 'rejected') {
    throw mineResult.reason
  }
  const mine = mineResult.value
  const shared = sharedResult.status === 'fulfilled'
    ? sharedResult.value
    : sharedBookshelves.value
  if (sharedResult.status === 'rejected') {
    console.warn('Failed to refresh shared PDF bookshelves:', sharedResult.reason)
  }
  const loadedBookshelves = [...mine, ...shared]
  bookshelves.value = loadedBookshelves
  const savedBookshelfId = localStorage.getItem(bookshelfSelectionStorageKey()) ?? ''
  const preferredBookshelfId = selectedBookshelfId.value || savedBookshelfId
  selectedBookshelfId.value = loadedBookshelves.some(
    (bookshelf) => bookshelf.id === preferredBookshelfId,
  )
    ? preferredBookshelfId
    : mine[0]?.id ?? shared[0]?.id ?? ''
  if (selectedBookshelfId.value) {
    localStorage.setItem(bookshelfSelectionStorageKey(), selectedBookshelfId.value)
  }
  loadBookshelfCanvasExtent()
  loadWallSitePositions()
}

async function selectBookshelf(bookshelfId: string) {
  if (bookshelfId === selectedBookshelfId.value) {
    return
  }
  closeBookContextMenu()
  releaseBookCoverImages()
  selectedBookshelfId.value = bookshelfId
  localStorage.setItem(bookshelfSelectionStorageKey(), bookshelfId)
  loadBookshelfCanvasExtent()
  loadWallSitePositions()
  void nextTick(() => bookshelfScrollRef.value?.scrollTo({ left: 0, top: 0 }))
  await reloadSelectedBookshelfContents()
}

async function reloadSelectedBookshelfContents() {
  const bookshelfId = selectedBookshelfId.value
  if (!bookshelfId) {
    documents.value = []
    skillBookCatalog.value = null
    skillBookReadingState.value = null
    linuxDoBooks.value = []
    libraryFolders.value = []
    return
  }
  const reloadSequence = ++documentReloadSequence
  loading.value = true
  try {
    const [loadedDocuments, skillBookBundle, loadedLinuxDoBooks, loadedFolders] = await Promise.all([
      fetchPdfDocuments(bookshelfId),
      fetchSkillBookBundle(bookshelfId),
      fetchLinuxDoBooks(bookshelfId),
      fetchLibraryFolders(bookshelfId),
    ])
    if (reloadSequence !== documentReloadSequence || bookshelfId !== selectedBookshelfId.value) {
      return
    }
    documents.value = loadedDocuments
    skillBookCatalog.value = skillBookBundle.catalog
    skillBookReadingState.value = skillBookBundle.readingState
    linuxDoBooks.value = loadedLinuxDoBooks
    libraryFolders.value = loadedFolders
    ensureFacingCoverImages(loadedDocuments)
    scheduleTitleRefresh()
    syncRuanyfWeeklyCommonSiteUrl(loadedLinuxDoBooks)
  } catch (error) {
    if (reloadSequence === documentReloadSequence) {
      console.warn('Failed to load bookshelf metadata:', error)
      ElMessage.error('加载馆藏失败')
    }
  } finally {
    if (reloadSequence === documentReloadSequence) {
      loading.value = false
    }
  }
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
    bookshelves.value = [...ownedBookshelves.value, bookshelf, ...sharedBookshelves.value]
    ElMessage.success(`已新建书柜“${bookshelf.name}”`)
  } catch (error) {
    if (error === 'cancel' || error === 'close') {
      return
    }
    console.warn('Failed to create PDF bookshelf:', error)
    ElMessage.error('新建书柜失败，请检查名称是否重复')
  }
}

function openBookshelfSettings(bookshelf: PdfLibraryBookshelf) {
  bookshelfSettingsId.value = bookshelf.id
  bookshelfSettingsName.value = bookshelf.name
  bookshelfSettingsPageTarget.value = bookshelf.logical_page_target_characters || 1600
  bookshelfSettingsReadingMode.value = bookshelf.article_reading_mode || 'scroll'
  bookshelfSettingsVisible.value = true
}

async function saveBookshelfSettings() {
  const name = bookshelfSettingsName.value.trim()
  if (!name) {
    ElMessage.warning('请输入书柜名称')
    return
  }
  bookshelfSettingsSaving.value = true
  try {
    const updated = await updatePdfBookshelf(bookshelfSettingsId.value, {
      name,
      logical_page_target_characters: bookshelfSettingsPageTarget.value,
      article_reading_mode: bookshelfSettingsReadingMode.value,
    })
    const index = bookshelves.value.findIndex((item) => item.id === updated.id)
    if (index >= 0) {
      bookshelves.value[index] = updated
      bookshelves.value = [...bookshelves.value]
    }
    bookshelfSettingsVisible.value = false
    ElMessage.success('书柜设置已保存')
  } catch (error) {
    console.warn('Failed to update PDF bookshelf:', error)
    ElMessage.error('保存失败，请检查书柜名称是否重复')
  } finally {
    bookshelfSettingsSaving.value = false
  }
}

function openContextBookReadingSettings() {
  const bookId = linuxDoBookContextMenu.value?.bookId
  const book = linuxDoBooks.value.find(item => item.id === bookId)
  closeBookContextMenu()
  if (!book || !selectedBookshelfIsOwned.value) return
  bookReadingSettingsBookId.value = book.id
  bookReadingSettingsTitle.value = book.title
  bookReadingSettingsMode.value = book.bookshelf_placement.article_reading_mode ?? 'inherit'
  bookReadingSettingsVisible.value = true
}

async function saveBookReadingSettings() {
  const book = linuxDoBooks.value.find(item => item.id === bookReadingSettingsBookId.value)
  if (!book || !selectedBookshelfIsOwned.value) return
  bookReadingSettingsSaving.value = true
  try {
    book.bookshelf_placement = await updateLinuxDoBookPlacement(book.id, {
      ...book.bookshelf_placement,
      article_reading_mode: bookReadingSettingsMode.value === 'inherit'
        ? null
        : bookReadingSettingsMode.value,
    })
    linuxDoBooks.value = [...linuxDoBooks.value]
    bookReadingSettingsVisible.value = false
    ElMessage.success('图书阅读方式已保存')
  } catch (error) {
    console.warn('Failed to update book reading mode:', error)
    ElMessage.error('保存图书阅读方式失败')
  } finally {
    bookReadingSettingsSaving.value = false
  }
}

function settingsContextBookshelf() {
  const bookshelfId = bookshelfContextMenu.value?.bookshelfId
  closeBookshelfContextMenu()
  const bookshelf = bookshelves.value.find((item) => item.id === bookshelfId)
  if (bookshelf) {
    openBookshelfSettings(bookshelf)
  }
}

async function shareContextBookshelf() {
  const bookshelf = contextBookshelf.value
  closeBookshelfContextMenu()
  if (!bookshelf?.is_owned) {
    return
  }
  bookshelfShareBookshelf.value = bookshelf
  bookshelfShareVisible.value = true
  bookshelfShareUsername.value = ''
  bookshelfShareSelectedUser.value = null
  bookshelfShareLoading.value = true
  try {
    const access = await fetchPdfBookshelfAccess(bookshelf.id)
    bookshelfShareGrants.value = access.grants.filter((grant) => grant.subject_type === 'user')
  } catch (error) {
    console.warn('Failed to load bookshelf access:', error)
    ElMessage.error('读取书柜分享设置失败')
    bookshelfShareVisible.value = false
  } finally {
    bookshelfShareLoading.value = false
  }
}

function addBookshelfShareUser() {
  const username = bookshelfShareUsername.value.trim()
  const selectedUser = bookshelfShareSelectedUser.value
  if (!username || !selectedUser || selectedUser.username !== username) {
    ElMessage.warning('请从下拉清单中选择账号')
    return
  }
  if (bookshelfShareGrants.value.some((grant) => grant.username.toLowerCase() === username.toLowerCase())) {
    ElMessage.warning('该账号已在分享清单中')
    return
  }
  bookshelfShareGrants.value.push({
    subject_type: 'user',
    subject_key: `draft:${username}`,
    subject_user_id: selectedUser.id,
    username,
    nickname: selectedUser.nickname,
    role: 'viewer',
  })
  bookshelfShareUsername.value = ''
  bookshelfShareSelectedUser.value = null
}

function removeBookshelfShareUser(subjectKey: string) {
  bookshelfShareGrants.value = bookshelfShareGrants.value.filter(
    (grant) => grant.subject_key !== subjectKey,
  )
}

async function saveBookshelfShare() {
  const bookshelf = bookshelfShareBookshelf.value
  if (!bookshelf) {
    return
  }
  bookshelfShareLoading.value = true
  try {
    const grants: PdfAccessGrantUpdate[] = bookshelfShareGrants.value.map((grant) => ({
      subject_type: 'user',
      subject_user_id: grant.subject_user_id ?? null,
      username: grant.username,
      role: 'viewer',
    }))
    const access = await updatePdfBookshelfAccess(bookshelf.id, grants)
    bookshelfShareGrants.value = access.grants.filter((grant) => grant.subject_type === 'user')
    bookshelfShareVisible.value = false
    ElMessage.success('书柜分享已更新')
  } catch (error) {
    console.warn('Failed to update bookshelf access:', error)
    ElMessage.error('更新分享失败，请确认账号名称')
  } finally {
    bookshelfShareLoading.value = false
  }
}

async function leaveContextBookshelf() {
  const bookshelf = contextBookshelf.value
  closeBookshelfContextMenu()
  if (!bookshelf || bookshelf.is_owned) {
    return
  }
  try {
    await ElMessageBox.confirm(`不再查看 ${bookshelf.owner_username} 分享的“${bookshelf.name}”？`, '退出共享', {
      confirmButtonText: '退出',
      cancelButtonText: '取消',
    })
    await leaveSharedPdfBookshelf(bookshelf.id)
    const wasSelected = selectedBookshelfId.value === bookshelf.id
    await reloadBookshelves()
    if (wasSelected) {
      documents.value = []
      await reloadDocuments()
    }
    ElMessage.success('已退出共享书柜')
  } catch (error) {
    if (error === 'cancel' || error === 'close') {
      return
    }
    console.warn('Failed to leave shared bookshelf:', error)
    ElMessage.error('退出共享书柜失败')
  }
}

async function deleteContextBookshelf() {
  const bookshelf = contextBookshelf.value
  closeBookshelfContextMenu()
  if (!bookshelf || bookshelf.book_count !== 0 || bookshelf.folder_count !== 0) {
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
  if (!selectedBookshelfIsOwned.value || !hasExternalFiles(event) || importingDroppedPdfs.value) {
    return
  }
  event.preventDefault()
  externalFileDragDepth += 1
  externalFileDragActive.value = true
  externalPdfDropTarget.value = resolveExternalPdfDropTarget(event)
}

function handleExternalFileDragOver(event: DragEvent) {
  if (!selectedBookshelfIsOwned.value || !hasExternalFiles(event) || importingDroppedPdfs.value) {
    return
  }
  event.preventDefault()
  growBookshelfNearPointer(event)
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

async function placeImportedLibraryItems(
  importedPlacementKeys: string[],
  target: ExternalPdfDropTarget,
) {
  const importedKeySet = new Set(importedPlacementKeys)
  const importedByKey = new Map<string, BookshelfPlacementItem>()
  const rows = buildUnifiedLibraryLayoutRows().map((row) => row.filter((item) => {
    if (!importedKeySet.has(item.key)) return true
    importedByKey.set(item.key, item)
    return false
  }))
  const importedItems = importedPlacementKeys
    .map((key) => importedByKey.get(key))
    .filter((item): item is BookshelfPlacementItem => Boolean(item))
  if (!importedItems.length) {
    return
  }

  while (rows.length <= target.shelfIndex) {
    rows.push([])
  }
  const targetRow = rows[target.shelfIndex]
  const beforeKey = target.beforePdfId == null ? null : `book-${target.beforePdfId}`
  const beforeIndex = beforeKey == null ? -1 : targetRow.findIndex((item) => item.key === beforeKey)
  targetRow.splice(beforeIndex >= 0 ? beforeIndex : targetRow.length, 0, ...importedItems)
  applyUnifiedLibraryLayoutRows(rows)
  await layoutSaveQueue
}

function isPdfImportFile(file: File) {
  return file.type.toLowerCase() === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf')
}

function isElectronicBookImportFile(file: File) {
  return /\.(?:epub|html?|md|markdown|txt)$/i.test(file.name)
}

function isLibraryImportFile(file: File) {
  return isPdfImportFile(file) || isElectronicBookImportFile(file)
}

async function importBookFiles(
  bookFiles: File[],
  rejectedCount = 0,
  dropTarget: ExternalPdfDropTarget | null = null,
) {
  const bookshelfId = selectedBookshelfId.value
  if (!selectedBookshelfIsOwned.value) {
    ElMessage.warning('共享书柜为只读')
    return
  }
  if (!bookshelfId) {
    ElMessage.error('请先选择书柜')
    return
  }
  if (!bookFiles.length) {
    ElMessage.warning('请选择电子书文件')
    return
  }

  importingDroppedPdfs.value = true
  let uploadedCount = 0
  let placedCount = 0
  let failedCount = rejectedCount
  const importedPlacementKeys: string[] = []
  try {
    for (const file of bookFiles) {
      try {
        if (isPdfImportFile(file)) {
          const importedDocument = await uploadPdfDocument(file)
          try {
            await movePdfToBookshelf(importedDocument.id, bookshelfId)
            placedCount += 1
          } catch (error) {
            console.warn(`Uploaded PDF but failed to place it: ${file.name}`, error)
          }
          const placementKey = `book-${importedDocument.id}`
          if (!importedPlacementKeys.includes(placementKey)) importedPlacementKeys.push(placementKey)
        } else {
          const importedBook = await uploadElectronicBook(file, bookshelfId, dropTarget?.shelfIndex ?? 0)
          placedCount += 1
          const placementKey = `linux-do-book-${importedBook.id}`
          if (!importedPlacementKeys.includes(placementKey)) importedPlacementKeys.push(placementKey)
        }
        uploadedCount += 1
      } catch (error) {
        failedCount += 1
        console.warn(`Failed to import electronic book: ${file.name}`, error)
      }
    }
    if (uploadedCount === 0) {
      ElMessage.error(failedCount > 0 ? '电子书均导入失败' : '没有可导入的电子书')
      return
    }
    await reloadBookshelves()
    await Promise.all([reloadDocuments(), reloadLinuxDoBooks()])
    if (dropTarget && importedPlacementKeys.length) {
      try {
        await placeImportedLibraryItems(importedPlacementKeys, dropTarget)
      } catch (error) {
        console.warn('Failed to place imported books at drop target:', error)
        await Promise.all([reloadDocuments({ silent: true }), reloadLinuxDoBooks()])
        ElMessage.error('图书已导入，但保存落点失败')
        return
      }
    }
    const placementFailedCount = uploadedCount - placedCount
    if (failedCount === 0 && placementFailedCount === 0) {
      ElMessage.success(`已导入 ${uploadedCount} 本图书`)
    } else if (placementFailedCount > 0) {
      ElMessage.warning(`已上传 ${uploadedCount} 本，其中 ${placementFailedCount} 本暂未放入目标书柜`)
    } else {
      ElMessage.warning(`已导入 ${uploadedCount} 本，${failedCount} 个文件上传失败`)
    }
  } catch (error) {
    console.warn('Failed to refresh library after dropped PDF import:', error)
    ElMessage.error(`已上传 ${uploadedCount} 本，但刷新本人书柜失败，请重新打开页面`)
  } finally {
    importingDroppedPdfs.value = false
  }
}

async function handleExternalFileDrop(event: DragEvent) {
  if (!selectedBookshelfIsOwned.value || !hasExternalFiles(event) || importingDroppedPdfs.value) {
    return
  }
  event.preventDefault()
  const dropTarget = resolveExternalPdfDropTarget(event)
  externalFileDragDepth = 0
  externalFileDragActive.value = false
  externalPdfDropTarget.value = null

  const droppedFiles = Array.from(event.dataTransfer?.files ?? [])
  const bookFiles = droppedFiles.filter(isLibraryImportFile)
  if (!bookFiles.length) {
    ElMessage.warning('支持 PDF、EPUB、HTML、Markdown 和 TXT')
    return
  }
  await importBookFiles(bookFiles, droppedFiles.length - bookFiles.length, dropTarget)
}

async function initializeLibraryPage() {
  if (userStore.isAuthenticated && !userStore.user && !userStore.loading) {
    await userStore.fetchUserProfile()
  }
  try {
    await reloadBookshelves()
    await reloadSelectedBookshelfContents()
  } catch (error) {
    console.warn('Failed to initialize PDF library:', error)
    ElMessage.error('加载图书馆失败')
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  restoreViewMode()
  loadBookshelfWallSites()
  window.addEventListener('storage', handleCommonSitesStorage)
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
  window.removeEventListener('storage', handleCommonSitesStorage)
  finishWallSitePointerInteraction()
  finishWallSelection()
  closeBookPreview()
  releaseBookCoverImages()
  releaseCommonSiteIconUrls()
  releaseWallSiteEditorLogoPreview()
})
</script>

<template>
  <div class="pdf-library-page" v-loading="loading || importingDroppedPdfs">
    <header class="library-header">
      <nav class="library-bookshelves" aria-label="书柜">
        <div
          v-for="bookshelf in ownedBookshelves"
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
        <span v-if="sharedBookshelves.length" class="library-bookshelf-group-label">与我共享</span>
        <div
          v-for="bookshelf in sharedBookshelves"
          :key="bookshelf.id"
          class="library-bookshelf-tab is-shared"
          :class="{ active: bookshelf.id === selectedBookshelfId }"
        >
          <button
            type="button"
            class="library-bookshelf-select"
            :aria-current="bookshelf.id === selectedBookshelfId ? 'page' : undefined"
            :title="`${bookshelf.owner_username} 分享的“${bookshelf.name}”（${bookshelf.book_count} 本）`"
            @click="selectBookshelf(bookshelf.id)"
            @contextmenu="openBookshelfContextMenu($event, bookshelf.id)"
          >
            {{ bookshelf.owner_username }}/{{ bookshelf.name }}
          </button>
        </div>
      </nav>

      <div class="library-actions">
        <el-input
          v-model="searchText"
          class="library-search"
          :prefix-icon="Search"
          clearable
          placeholder="搜索图书"
        />
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
      <template v-if="contextBookshelf?.is_owned">
        <button type="button" role="menuitem" @click="settingsContextBookshelf">书柜设置</button>
        <button type="button" role="menuitem" @click="shareContextBookshelf">分享</button>
      </template>
      <button v-if="!contextBookshelf?.is_owned" type="button" role="menuitem" @click="leaveContextBookshelf">退出共享</button>
      <template v-if="contextBookshelf?.is_owned && contextBookshelf.book_count === 0 && contextBookshelf.folder_count === 0">
        <div class="book-context-menu-separator" role="separator"></div>
        <button class="danger" type="button" role="menuitem" @click="deleteContextBookshelf">删除书柜</button>
      </template>
    </div>

    <div
      v-if="shelfContextMenu"
      class="book-context-menu shelf-context-menu"
      role="menu"
      :style="{ left: `${shelfContextMenu.x}px`, top: `${shelfContextMenu.y}px` }"
      @pointerdown.stop
      @contextmenu.prevent
    >
      <button type="button" role="menuitem" @click="createFolderFromShelfMenu">新建文件夹</button>
      <button type="button" role="menuitem" @click="openNewWallSiteEditor">新建网站链接</button>
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
        v-if="viewMode === 'bookshelf' && selectedBookshelfId"
        ref="bookshelfScrollRef"
        class="bookshelf-scroll"
        @scroll="handleBookshelfScroll"
      >
        <div class="bookshelf-grid" :style="bookshelfGridStyle">
          <div
            v-for="(row, shelfIndex) in bookshelfDisplayRows"
            :key="shelfIndex"
            class="bookshelf-row"
            :data-shelf-index="shelfIndex"
            :class="{
              'drag-target': (dragOverShelfIndex === shelfIndex && dragOverPdfId == null)
                || (externalFileDragActive && externalPdfDropTarget?.shelfIndex === shelfIndex),
            }"
            @pointerdown="handleWallSelectionPointerDown($event, shelfIndex)"
            @contextmenu="handleShelfContextMenu($event, shelfIndex)"
          >
            <div
              v-for="group in row"
              :key="group.key"
              class="book-group"
              :class="{
                'horizontal-book-stack': group.kind === 'horizontal-stack',
                'insert-before': bookshelfGroupContainsPlacementKey(group, dragOverPlacementKey),
              }"
              :data-group-key="group.key"
              :data-position-index="bookshelfGroupPosition(group)"
            >
              <button
                v-if="group.kind === 'folder' && group.folder"
                type="button"
                class="library-folder"
                :data-folder-id="group.folder.id"
                :style="folderStyle(group.folder)"
                :title="`${group.folder.name}\n${group.folder.member_count} 个文件`"
                @click="openFolder(group.folder)"
                @contextmenu.prevent.stop="openFolderEditor(group.folder)"
              >
                <span>{{ group.folder.name }}</span>
                <small>{{ group.folder.member_count }}</small>
              </button>
              <button
                v-if="group.kind === 'skill-book'"
                type="button"
                class="book-item skill-book-item"
                :class="[
                  dynamicBookOrientationClass(skillBookCatalog?.bookshelf_placement.orientation),
                  { dragging: draggingSkillBook },
                ]"
                :style="skillBookSpineStyle"
                :title="skillBookTooltip()"
                @pointerdown="handleSkillBookPointerDown"
                @click="openSkillBookReader"
                @contextmenu.prevent.stop="openSkillBookContextMenu"
              >
                <span
                  v-if="skillBookReadingState?.updated_at"
                  class="book-progress-bookmark"
                  aria-hidden="true"
                ></span>
                <span class="book-spine">
                  <span class="book-spine-title">
                    <span class="book-spine-title-token">本地</span>
                    <span class="book-spine-title-token is-compact-vertical-token">Skill</span>
                    <span class="book-spine-title-token">手册</span>
                  </span>
                  <span class="book-spine-author">
                    {{ bookAuthorWithYear(skillBookCatalog?.author, skillBookCatalog?.start_date) }}
                  </span>
                  <span
                    v-if="bookStartYear(skillBookCatalog?.start_date)"
                    class="book-spine-start-year"
                  >{{ bookStartYear(skillBookCatalog?.start_date) }}</span>
                </span>
              </button>
              <button
                v-if="group.kind === 'linux-do-book' && group.linuxDoBook"
                type="button"
                class="book-item skill-book-item linux-do-book-item"
                :class="[
                  dynamicBookOrientationClass(group.linuxDoBook.bookshelf_placement.orientation),
                  { dragging: draggingLinuxDoBookId === group.linuxDoBook.id },
                ]"
                :style="linuxDoBookSpineStyle(group.linuxDoBook)"
                :title="linuxDoBookTooltip(group.linuxDoBook)"
                @pointerdown="handleLinuxDoBookPointerDown($event, group.linuxDoBook.id)"
                @click="handleLinuxDoBookClick($event, group.linuxDoBook.id)"
                @contextmenu.prevent.stop="openLinuxDoBookContextMenu($event, group.linuxDoBook.id)"
              >
                <span
                  v-if="group.linuxDoBook.reading_state?.updated_at"
                  class="book-progress-bookmark"
                  aria-hidden="true"
                ></span>
                <span class="book-spine">
                  <span class="book-spine-title">
                    <span
                      v-for="(segment, segmentIndex) in verticalTitleSegments(group.linuxDoBook.title.replace(/\s+/g, ''))"
                      :key="segmentIndex"
                      class="book-spine-title-token"
                      :class="{
                        'is-combined': segment.combined,
                        'is-compact-vertical-token': isCompactVerticalToken(segment),
                      }"
                    >{{ segment.text }}</span>
                  </span>
                  <span class="book-spine-author">
                    {{ bookAuthorWithYear(group.linuxDoBook.author, group.linuxDoBook.start_date) }}
                  </span>
                  <span
                    v-if="bookStartYear(group.linuxDoBook.start_date)"
                    class="book-spine-start-year"
                  >{{ bookStartYear(group.linuxDoBook.start_date) }}</span>
                </span>
              </button>
              <button
                v-for="document in ['skill-book', 'linux-do-book', 'folder'].includes(group.kind) ? [] : group.documents"
                :key="document.id"
                class="book-item"
                :class="[
                  bookOrientationClass(document),
                  {
                    'insert-before': externalFileDragActive && externalPdfDropTarget?.beforePdfId === document.id,
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
                @contextmenu.prevent.stop="openBookContextMenu($event, document.id)"
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
                  <span
                    v-if="bookAuthorWithYear(document.display_author, document.start_date)"
                    class="book-spine-author"
                  >
                    {{ bookAuthorWithYear(document.display_author, document.start_date) }}
                  </span>
                  <span
                    v-if="bookStartYear(document.start_date)"
                    class="book-spine-start-year"
                  >{{ bookStartYear(document.start_date) }}</span>
                </span>
              </button>
            </div>
            <span
              v-if="dragOverShelfIndex === shelfIndex
                && dragOverPlacementKey == null
                && (draggingPdfId != null || draggingLinuxDoBookId != null || draggingSkillBook)"
              class="book-drop-end-marker"
              aria-hidden="true"
            ></span>
            <nav
              v-if="selectedBookshelfIsOwned && wallSitesForShelf(shelfIndex).length"
              class="bookshelf-wall-sites"
              aria-label="常用网站"
              :data-wall-shelf-index="shelfIndex"
              @contextmenu.stop
            >
              <span
                v-if="wallSiteMarqueeStyle(shelfIndex)"
                class="wall-site-selection-marquee"
                :style="wallSiteMarqueeStyle(shelfIndex)"
                aria-hidden="true"
              ></span>
              <a
                v-for="site in wallSitesForShelf(shelfIndex)"
                :key="site.id"
                class="bookshelf-wall-site"
                :class="{
                  dragging: draggingWallSiteId === site.id,
                  selected: selectedWallSiteIds.includes(site.id),
                }"
                :href="site.url"
                target="_blank"
                rel="noopener noreferrer"
                :title="site.description?.trim() || undefined"
                :data-wall-site-id="site.id"
                :style="wallSiteStyle(site)"
                draggable="false"
                @dragstart.prevent
                @pointerdown="handleWallSitePointerDown($event, site.id)"
                @click="handleWallSiteClick($event, site.id)"
                @contextmenu="openWallSiteEditor($event, site)"
              >
                <span class="bookshelf-wall-site-icon">
                  <img
                    v-if="commonSiteIconUrl(site)"
                    :src="commonSiteIconUrl(site)"
                    alt=""
                    draggable="false"
                    loading="lazy"
                    @dragstart.prevent
                    @error="handleCommonSiteIconError(site)"
                  />
                  <span v-else>{{ commonSiteFallbackLabel(site) }}</span>
                </span>
                <span v-if="site.title.trim()" class="bookshelf-wall-site-title">{{ site.title }}</span>
              </a>
              <div
                v-if="selectedWallSitesForShelf(shelfIndex).length >= 2"
                class="wall-site-selection-toolbar"
                :style="wallSiteSelectionToolbarStyle(shelfIndex)"
                role="toolbar"
                aria-label="Logo 排版"
              >
                <button
                  type="button"
                  title="自动对齐并等距排列"
                  @click.stop="standardizeSelectedWallSites(shelfIndex)"
                >整理</button>
              </div>
            </nav>
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
            <tr v-for="book in filteredLinuxDoBooks" :key="book.id" class="pdf-row skill-book-table-row">
              <td class="pdf-name-cell">
                <button class="pdf-title-button skill-book-title-button" type="button" @click="openLinuxDoBookReader(book.id)">
                  <span class="pdf-title">{{ book.title }}</span>
                </button>
              </td>
              <td class="pdf-role">{{ selectedBookshelfIsOwned ? '可管理' : '只读' }}</td>
              <td class="pdf-current-page">-</td>
              <td class="pdf-size">-</td>
              <td class="pdf-updated">{{ formatDateTime(book.updated_at) }}</td>
              <td class="pdf-spacer-cell" aria-hidden="true"></td>
            </tr>
            <tr v-if="showLocalSkillBook && skillBookCatalog" class="pdf-row skill-book-table-row">
              <td class="pdf-name-cell">
                <button class="pdf-title-button skill-book-title-button" type="button" @click="openSkillBookReader">
                  <span class="pdf-title">{{ skillBookCatalog.title }}</span>
                </button>
              </td>
              <td class="pdf-role">{{ accessRoleLabel(skillBookCatalog.access_role) }}</td>
              <td class="pdf-current-page">
                {{ skillBookReadingState?.updated_at
                  ? `第 ${skillBookReadingState.current_page} 页`
                  : '-' }}
              </td>
              <td class="pdf-size">-</td>
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
        v-if="skillBookContextMenu && skillBookCatalog?.is_owned"
        class="book-context-menu"
        role="menu"
        :style="{ left: `${skillBookContextMenu.x}px`, top: `${skillBookContextMenu.y}px` }"
        @pointerdown.stop
        @contextmenu.prevent
      >
        <button type="button" role="menuitem" @click="openSkillBookMetadataEditor">编辑元数据</button>
        <button type="button" role="menuitem" @click="rotateContextSkillBook">旋转</button>
        <div class="book-context-menu-separator" role="separator"></div>
        <button class="danger" type="button" role="menuitem" @click="deleteContextSkillBook">删除图书</button>
      </div>

      <div
        v-if="linuxDoBookContextMenu"
        class="book-context-menu"
        role="menu"
        :style="{ left: `${linuxDoBookContextMenu.x}px`, top: `${linuxDoBookContextMenu.y}px` }"
        @pointerdown.stop
        @contextmenu.prevent
      >
        <button type="button" role="menuitem" @click="openLinuxDoBookMetadataEditor">编辑元数据</button>
        <button
          v-if="selectedBookshelfIsOwned"
          type="button"
          role="menuitem"
          @click="openContextBookReadingSettings"
        >阅读方式</button>
        <button
          v-if="selectedBookshelfIsOwned"
          type="button"
          role="menuitem"
          @click="rotateContextLinuxDoBook"
        >旋转</button>
        <div class="book-context-menu-separator" role="separator"></div>
        <button class="danger" type="button" role="menuitem" @click="deleteContextLinuxDoBook">删除图书</button>
      </div>

      <div
        v-if="bookContextMenu"
        class="book-context-menu"
        role="menu"
        :style="{ left: `${bookContextMenu.x}px`, top: `${bookContextMenu.y}px` }"
        @pointerdown.stop
        @contextmenu.prevent
      >
        <button
          v-if="!selectedBookshelfIsOwned"
          type="button"
          role="menuitem"
          @click="openCopyBookDialog"
        >复制到我的书柜</button>
        <button
          v-if="documents.find((document) => document.id === bookContextMenu?.pdfId && canEditBookMetadata(document))"
          type="button"
          role="menuitem"
          @click="openMetadataEditorFromContext"
        >编辑元数据</button>
        <button v-if="selectedBookshelfIsOwned" type="button" role="menuitem" @click="rotateContextBook">旋转</button>
        <template v-if="documents.find((document) => document.id === bookContextMenu?.pdfId && canDeleteBook(document))">
          <div class="book-context-menu-separator" role="separator"></div>
          <button class="danger" type="button" role="menuitem" @click="deleteContextBook">删除图书</button>
        </template>
      </div>
    </section>

    <el-dialog
      v-model="deleteBookDialogVisible"
      title="删除图书"
      width="min(520px, calc(100vw - 32px))"
      append-to-body
      destroy-on-close
      :close-on-click-modal="!deleteBookDialogSaving"
      :close-on-press-escape="!deleteBookDialogSaving"
    >
      <div v-if="deleteBookDialogDocument" class="delete-book-dialog-content">
        <div class="delete-book-dialog-title">《{{ deleteBookDialogDocument.display_title }}》</div>
        <div class="delete-book-dialog-option">
          <strong>删除引用</strong>
          <span>只从书柜“{{ deleteBookDialogBookshelfName }}”移除这本书；图书原文件、原拥有者的数据和笔记均保留。</span>
        </div>
        <div
          class="delete-book-dialog-option"
          :class="{ disabled: !canDeleteBookOriginalFile }"
        >
          <strong>删除原文件</strong>
          <span v-if="canDeleteBookOriginalFile">永久删除图书资源，同时删除所有书柜引用、阅读进度、笔记和分享权限。</span>
          <span v-else>当前账号不是图书资源所有者，无权删除原文件。</span>
        </div>
      </div>
      <template #footer>
        <el-button :disabled="deleteBookDialogSaving" @click="deleteBookDialogVisible = false">取消</el-button>
        <el-button :loading="deleteBookDialogSaving" @click="deleteBookReference">删除引用</el-button>
        <el-button
          type="danger"
          :loading="deleteBookDialogSaving"
          :disabled="!canDeleteBookOriginalFile"
          @click="deleteBookOriginalFile"
        >删除原文件</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="bookshelfShareVisible"
      :title="`分享书柜“${bookshelfShareBookshelf?.name ?? ''}”`"
      width="min(460px, calc(100vw - 32px))"
      append-to-body
      destroy-on-close
    >
      <div v-loading="bookshelfShareLoading" class="bookshelf-share-editor">
        <div class="bookshelf-share-picker">
          <AccountUserSelect
            v-model="bookshelfShareUsername"
            class="bookshelf-share-select"
            placeholder="搜索账号或昵称"
            :exclude-usernames="bookshelfShareGrants.map(grant => grant.username)"
            @selected="user => bookshelfShareSelectedUser = user"
          />
          <el-button
            :disabled="!bookshelfShareSelectedUser || bookshelfShareSelectedUser.username !== bookshelfShareUsername"
            @click="addBookshelfShareUser"
          >添加</el-button>
        </div>
        <div v-if="bookshelfShareGrants.length" class="bookshelf-share-list">
          <div
            v-for="grant in bookshelfShareGrants"
            :key="grant.subject_key"
            class="bookshelf-share-user"
          >
            <span>{{ grant.nickname || grant.username }}</span>
            <span v-if="grant.nickname && grant.username" class="bookshelf-share-username">{{ grant.username }}</span>
            <span class="bookshelf-share-role">只读</span>
            <el-button text type="danger" @click="removeBookshelfShareUser(grant.subject_key)">移除</el-button>
          </div>
        </div>
        <div v-else class="bookshelf-share-empty">尚未分享给其他账号</div>
      </div>
      <template #footer>
        <el-button @click="bookshelfShareVisible = false">取消</el-button>
        <el-button type="primary" :loading="bookshelfShareLoading" @click="saveBookshelfShare">
          保存
        </el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="wallSiteEditorVisible"
      :title="wallSiteEditorId ? '编辑网站链接' : '新建网站链接'"
      width="min(460px, calc(100vw - 32px))"
      append-to-body
      @closed="releaseWallSiteEditorLogoPreview"
    >
      <el-form label-width="88px" @submit.prevent>
        <el-form-item label="Logo">
          <span class="wall-site-editor-logo-preview">
            <img
              v-if="wallSiteEditorLogoPreviewUrl"
              :src="wallSiteEditorLogoPreviewUrl"
              alt=""
            />
            <span v-else>站</span>
          </span>
          <el-button text :loading="wallSiteLogoRefreshing" @click="refreshWallSiteLogo">
            重新获取
          </el-button>
        </el-form-item>
        <el-form-item label="标题">
          <el-input v-model="wallSiteEditorTitle" placeholder="可留空" />
        </el-form-item>
        <el-form-item label="链接">
          <el-input v-model="wallSiteEditorUrl" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="wallSiteEditorDescription" />
        </el-form-item>
        <el-form-item label="Logo 大小">
          <el-slider
            v-model="wallSiteEditorLogoSize"
            :min="24"
            :max="96"
            :step="2"
            show-input
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="wallSiteEditorVisible = false">取消</el-button>
        <el-button type="primary" @click="saveWallSiteEditor">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="metadataEditorVisible"
      title="编辑图书元数据"
      width="min(560px, calc(100vw - 32px))"
      append-to-body
      align-center
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
        <div class="metadata-editor-field">
          <label for="pdf-metadata-start-date">起始时间</label>
          <el-input
            id="pdf-metadata-start-date"
            v-model="metadataEditorStartDate"
            maxlength="10"
            placeholder="YYYY / YYYY-MM / YYYY-MM-DD"
            autocomplete="off"
          />
        </div>
        <div class="metadata-editor-field">
          <label>副标题</label>
          <el-input v-model="metadataEditorSubtitle" maxlength="120" />
        </div>
        <div class="metadata-editor-field">
          <label>译者</label>
          <el-input v-model="metadataEditorTranslator" maxlength="60" />
        </div>
        <div class="metadata-editor-field">
          <label>版本</label>
          <el-input v-model="metadataEditorEdition" maxlength="60" placeholder="例如：第 3 版" />
        </div>
        <div class="metadata-editor-field">
          <label>卷册</label>
          <el-input v-model="metadataEditorVolume" maxlength="60" placeholder="例如：上册" />
        </div>
        <div class="metadata-editor-field">
          <label>显示文件名</label>
          <el-input v-model="metadataEditorSourceName" maxlength="512" />
        </div>
        <div class="metadata-editor-field">
          <label>原始导入名</label>
          <el-input
            :model-value="metadataEditorImportedFilename"
            disabled
            title="仅用于追溯导入来源"
          />
        </div>
        <div class="metadata-editor-field">
          <label>标签</label>
          <el-input v-model="metadataEditorTags" placeholder="使用逗号分隔" />
        </div>
        <div class="metadata-editor-field">
          <label>简介</label>
          <el-input v-model="metadataEditorDescription" type="textarea" :rows="2" maxlength="4000" />
        </div>
        <div class="metadata-editor-field metadata-appearance-field">
          <label>书脊颜色</label>
          <el-color-picker v-model="metadataEditorCoverColor" />
          <el-button text @click="metadataEditorCoverColor = ''">自动取色</el-button>
        </div>
      </form>
      <template #footer>
        <el-button @click="metadataEditorVisible = false">取消</el-button>
        <el-button type="primary" :loading="metadataEditorSaving" @click="saveMetadataEditor">
          保存
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="copyBookVisible" title="复制到我的书柜" width="min(420px, calc(100vw - 32px))" append-to-body>
      <div class="metadata-editor-form">
        <div class="metadata-editor-field">
          <label>目标书柜</label>
          <el-select v-model="copyTargetBookshelfId">
            <el-option v-for="bookshelf in ownedBookshelves" :key="bookshelf.id" :label="bookshelf.name" :value="bookshelf.id" />
          </el-select>
        </div>
        <el-checkbox v-model="copyIncludeNotes">同时复制原书主人的笔记快照</el-checkbox>
        <small>复制后成为你的独立藏书；原分享撤销不影响阅读，笔记也不会继续同步。</small>
      </div>
      <template #footer>
        <el-button @click="copyBookVisible = false">取消</el-button>
        <el-button type="primary" :loading="copyBookSaving" @click="saveCopyBook">复制</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="folderContentsVisible" :title="openedFolder?.name ?? '资料夹'" width="min(620px, calc(100vw - 32px))" append-to-body>
      <div v-if="openedFolderDocuments.length || skillBookCatalog?.bookshelf_placement.folder_id === openedFolder?.id" class="folder-content-list">
        <div v-if="skillBookCatalog?.bookshelf_placement.folder_id === openedFolder?.id" class="folder-content-item">
          <button type="button" @click="openSkillBookReader">{{ skillBookCatalog.title }}</button>
          <el-button v-if="selectedBookshelfIsOwned" text @click="takeSkillBookOutOfFolder">取出到当前书层</el-button>
        </div>
        <div v-for="document in openedFolderDocuments" :key="document.id" class="folder-content-item">
          <button type="button" @click="handleBookClick($event, document)">{{ document.display_title }}</button>
          <el-button v-if="selectedBookshelfIsOwned" text @click="takeBookOutOfFolder(document)">取出到当前书层</el-button>
        </div>
      </div>
      <el-empty v-else description="空资料夹" />
      <template #footer>
        <el-button v-if="selectedBookshelfIsOwned && openedFolder?.member_count === 0" type="danger" plain @click="removeOpenedFolder">删除空资料夹</el-button>
        <el-button @click="folderContentsVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="folderEditorVisible" title="资料夹设置" width="min(440px, calc(100vw - 32px))" append-to-body>
      <div class="metadata-editor-form">
        <div class="metadata-editor-field"><label>名称</label><el-input v-model="folderEditorName" /></div>
        <div class="metadata-editor-field metadata-appearance-field"><label>颜色</label><el-color-picker v-model="folderEditorColor" /><el-button text @click="folderEditorColor = ''">恢复默认</el-button></div>
        <div class="metadata-editor-field"><label>最小厚度（毫米）</label><el-input-number v-model="folderEditorMinThickness" :min="0.1" :max="200" :step="0.1" /></div>
        <div class="metadata-editor-field"><label>固定厚度（毫米）</label><el-input-number v-model="folderEditorFixedThickness" :min="0.1" :max="200" :step="0.1" /><el-button text @click="folderEditorFixedThickness = undefined">按内容自动</el-button></div>
      </div>
      <template #footer><el-button @click="folderEditorVisible = false">取消</el-button><el-button type="primary" :loading="folderEditorSaving" @click="saveFolderEditor">保存</el-button></template>
    </el-dialog>

    <el-dialog
      v-model="previewVisible"
      :class="['book-preview-dialog', 'library-reader-theme-dialog', libraryReaderThemeClass]"
      width="min(920px, calc(100vw - 32px))"
      :style="previewDialogStyle"
      append-to-body
      destroy-on-close
      :show-close="true"
      @closed="closeBookPreview"
    >
      <template #header>
        <div class="book-preview-heading">
          <div class="book-preview-title">
            <strong>{{ previewDocument?.display_title }}</strong>
            <span>快速预览</span>
          </div>
          <ReaderThemeControl class="book-preview-theme" />
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

    <el-dialog
      v-model="bookshelfSettingsVisible"
      title="书柜设置"
      width="min(420px, calc(100vw - 32px))"
      append-to-body
      destroy-on-close
    >
      <form class="metadata-editor-form" @submit.prevent="saveBookshelfSettings">
        <div class="metadata-editor-field">
          <label for="bookshelf-settings-name">名称</label>
          <el-input id="bookshelf-settings-name" v-model="bookshelfSettingsName" maxlength="80" />
        </div>
        <div class="metadata-editor-field">
          <label>默认阅读方式</label>
          <el-radio-group v-model="bookshelfSettingsReadingMode">
            <el-radio-button value="scroll">连续滚动</el-radio-button>
            <el-radio-button value="paginated">翻页</el-radio-button>
          </el-radio-group>
        </div>
        <div v-if="bookshelfSettingsReadingMode === 'paginated'" class="metadata-editor-field">
          <label for="bookshelf-page-target">每页目标字数</label>
          <el-input-number
            id="bookshelf-page-target"
            v-model="bookshelfSettingsPageTarget"
            :min="500"
            :max="5000"
            :step="100"
            controls-position="right"
          />
        </div>
      </form>
      <template #footer>
        <el-button @click="bookshelfSettingsVisible = false">取消</el-button>
        <el-button type="primary" :loading="bookshelfSettingsSaving" @click="saveBookshelfSettings">
          保存
        </el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="bookReadingSettingsVisible"
      :title="`阅读方式 · ${bookReadingSettingsTitle}`"
      width="min(440px, calc(100vw - 32px))"
      append-to-body
      destroy-on-close
    >
      <form class="metadata-editor-form" @submit.prevent="saveBookReadingSettings">
        <div class="metadata-editor-field">
          <label>阅读方式</label>
          <el-radio-group v-model="bookReadingSettingsMode">
            <el-radio-button value="inherit">跟随书柜</el-radio-button>
            <el-radio-button value="scroll">连续滚动</el-radio-button>
            <el-radio-button value="paginated">翻页</el-radio-button>
          </el-radio-group>
        </div>
      </form>
      <template #footer>
        <el-button @click="bookReadingSettingsVisible = false">取消</el-button>
        <el-button type="primary" :loading="bookReadingSettingsSaving" @click="saveBookReadingSettings">
          保存
        </el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="skillBookMetadataVisible"
      title="编辑动态书本元数据"
      width="min(440px, calc(100vw - 32px))"
      append-to-body
      destroy-on-close
    >
      <form class="metadata-editor-form" @submit.prevent="saveSkillBookMetadata">
        <div class="metadata-editor-field">
          <label for="skill-book-page-format">纸张规格</label>
          <el-select id="skill-book-page-format" v-model="skillBookPageFormat">
            <el-option
              v-for="option in skillBookCatalog?.page_format_options ?? []"
              :key="option.value"
              :label="`${option.label}（${option.width_mm} × ${option.height_mm} mm）`"
              :value="option.value"
            />
          </el-select>
        </div>
        <div class="metadata-editor-field">
          <label for="skill-book-start-date">起始时间</label>
          <el-input
            id="skill-book-start-date"
            v-model="skillBookStartDate"
            maxlength="10"
            placeholder="YYYY / YYYY-MM / YYYY-MM-DD"
          />
        </div>
      </form>
      <template #footer>
        <el-button @click="skillBookMetadataVisible = false">取消</el-button>
        <el-button type="primary" :loading="skillBookMetadataSaving" @click="saveSkillBookMetadata">
          保存
        </el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="linuxDoBookMetadataVisible"
      title="编辑动态书本元数据"
      width="min(440px, calc(100vw - 32px))"
      append-to-body
      destroy-on-close
    >
      <form class="metadata-editor-form" @submit.prevent="saveLinuxDoBookMetadata">
        <div class="metadata-editor-field">
          <label for="dynamic-book-title">书名</label>
          <el-input id="dynamic-book-title" v-model="linuxDoBookMetadataTitle" maxlength="240" />
        </div>
        <div class="metadata-editor-field">
          <label for="dynamic-book-author">作者</label>
          <el-input id="dynamic-book-author" v-model="linuxDoBookMetadataAuthor" maxlength="160" />
        </div>
        <div class="metadata-editor-field">
          <label for="dynamic-book-start-date">起始时间</label>
          <el-input
            id="dynamic-book-start-date"
            v-model="linuxDoBookMetadataStartDate"
            maxlength="10"
            placeholder="YYYY / YYYY-MM / YYYY-MM-DD"
          />
        </div>
        <div class="metadata-editor-field metadata-appearance-field">
          <label>书脊颜色</label>
          <el-color-picker v-model="linuxDoBookMetadataCoverColor" />
          <el-button text @click="linuxDoBookMetadataCoverColor = '#294f6d'">恢复默认</el-button>
        </div>
      </form>
      <template #footer>
        <el-button @click="linuxDoBookMetadataVisible = false">取消</el-button>
        <el-button type="primary" :loading="linuxDoBookMetadataSaving" @click="saveLinuxDoBookMetadata">
          保存
        </el-button>
      </template>
    </el-dialog>

    <SkillBookReaderDialog
      v-model="skillBookReaderVisible"
      :bookshelf-id="selectedBookshelfId"
      @catalog-updated="handleSkillBookCatalogUpdated"
      @reading-state-updated="handleSkillBookReadingStateUpdated"
    />
    <LinuxDoBookReaderDialog
      v-model="linuxDoBookReaderVisible"
      :book-id="selectedLinuxDoBookId"
      :logical-page-target-characters="selectedBookshelf?.logical_page_target_characters ?? 1600"
      :reading-mode="selectedReaderMode"
      @reading-state-updated="handleLinuxDoBookReadingStateUpdated"
    />
  </div>
</template>

<style scoped>
.metadata-editor-form {
  display: grid;
  gap: 10px;
}

.metadata-editor-field {
  display: grid;
  grid-template-columns: 112px minmax(0, 1fr);
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.metadata-editor-field label {
  color: #4b5563;
  line-height: 32px;
  text-align: right;
  white-space: nowrap;
}

.metadata-editor-field > small {
  grid-column: 2 / -1;
  margin-top: -5px;
  color: #8a94a3;
  font-size: 12px;
  line-height: 1.45;
}

.metadata-editor-field > :deep(.el-input),
.metadata-editor-field > :deep(.el-select),
.metadata-editor-field > :deep(.el-textarea) {
  min-width: 0;
  width: 100%;
}

.metadata-appearance-field {
  grid-template-columns: 112px max-content minmax(0, 1fr);
}

.metadata-appearance-field > :deep(.el-button) {
  justify-self: start;
  margin-left: 0;
  padding-right: 4px;
  padding-left: 4px;
}

@media (max-width: 520px) {
  .metadata-editor-field {
    grid-template-columns: 96px minmax(0, 1fr);
  }

  .metadata-appearance-field {
    grid-template-columns: 96px max-content minmax(0, 1fr);
  }
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

.library-bookshelf-group-label {
  flex: 0 0 auto;
  margin-left: 8px;
  padding-left: 12px;
  border-left: 1px solid #d8dee8;
  color: #8793a3;
  font-size: 12px;
  white-space: nowrap;
}

.library-bookshelf-tab.is-shared {
  color: #65758a;
}

.bookshelf-share-editor {
  display: grid;
  gap: 12px;
  min-height: 96px;
}

.bookshelf-share-picker {
  display: flex;
  align-items: center;
  gap: 8px;
}

.bookshelf-share-select {
  flex: 1;
  min-width: 0;
}

.bookshelf-share-list {
  display: grid;
  border-top: 1px solid #edf0f4;
}

.bookshelf-share-user {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto auto;
  align-items: center;
  gap: 8px;
  min-height: 42px;
  border-bottom: 1px solid #edf0f4;
}

.bookshelf-share-username,
.bookshelf-share-role,
.bookshelf-share-empty {
  color: #8793a3;
  font-size: 12px;
}

.bookshelf-share-empty {
  padding: 14px 0;
  text-align: center;
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
  grid-auto-rows: auto;
  row-gap: 0;
  width: max-content;
  min-width: max(100%, var(--bookshelf-canvas-min-width, 0px));
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
  min-height: 220px;
  padding: 24px 0 12px;
  transition: background-color 120ms ease;
}

.bookshelf-wall-sites {
  position: absolute;
  z-index: 2;
  display: flex;
  inset: 16px 0 20px;
  align-items: flex-start;
  justify-content: flex-end;
  gap: 12px;
  padding: 4px 18px 0 48px;
  pointer-events: none;
}

.bookshelf-wall-site {
  display: grid;
  justify-items: center;
  gap: 5px;
  width: max(58px, calc(var(--wall-site-logo-size) + 12px));
  color: #394353;
  text-decoration: none;
  cursor: grab;
  touch-action: none;
  user-select: none;
  pointer-events: auto;
}

.bookshelf-wall-site.dragging {
  z-index: 4;
  cursor: grabbing;
  pointer-events: none;
}

.bookshelf-wall-site.selected .bookshelf-wall-site-icon {
  border-color: #2f6fd6;
  outline: 2px solid rgb(47 111 214 / 30%);
  outline-offset: 2px;
}

.wall-site-selection-marquee {
  position: absolute;
  z-index: 5;
  box-sizing: border-box;
  border: 1px dashed #2f6fd6;
  background: rgb(47 111 214 / 8%);
  pointer-events: none;
}

.wall-site-selection-toolbar {
  position: absolute;
  z-index: 6;
  transform: translate(-50%, -50%);
  pointer-events: auto;
}

.wall-site-selection-toolbar button {
  min-width: 44px;
  height: 28px;
  padding: 0 10px;
  border: 1px solid #b9c7db;
  border-radius: 4px;
  background: #fff;
  color: #2d405d;
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}

.wall-site-selection-toolbar button:hover,
.wall-site-selection-toolbar button:focus-visible {
  border-color: #2f6fd6;
  color: #2368d1;
}

.bookshelf-wall-site-icon {
  box-sizing: border-box;
  display: grid;
  place-items: center;
  width: var(--wall-site-logo-size);
  height: var(--wall-site-logo-size);
  overflow: hidden;
  border: 1px solid rgb(74 64 53 / 24%);
  border-radius: 5px;
  background: rgb(255 255 255 / 72%);
  color: #42536a;
  font-size: 15px;
  font-weight: 700;
}

.bookshelf-wall-site-icon img {
  width: 70%;
  height: 70%;
  object-fit: contain;
}

.bookshelf-wall-site-title {
  display: block;
  width: 100%;
  overflow: hidden;
  font-size: 11px;
  line-height: 1.25;
  text-align: center;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.bookshelf-wall-site:hover .bookshelf-wall-site-icon,
.bookshelf-wall-site:focus-visible .bookshelf-wall-site-icon {
  border-color: #2f6fd6;
}

.bookshelf-wall-site:focus-visible {
  border-radius: 5px;
  outline: 2px solid rgb(47 111 214 / 32%);
  outline-offset: 3px;
}

.wall-site-editor-logo-preview {
  box-sizing: border-box;
  display: inline-grid;
  place-items: center;
  width: 36px;
  height: 36px;
  margin-right: 8px;
  overflow: hidden;
  border: 1px solid var(--el-border-color);
  border-radius: 4px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.wall-site-editor-logo-preview img {
  width: 72%;
  height: 72%;
  object-fit: contain;
}

.library-folder {
  width: var(--folder-width);
  min-width: 28px;
  height: 224px;
  align-self: flex-end;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 10px 4px;
  border: 1px solid color-mix(in srgb, var(--folder-color), #000 18%);
  border-bottom: 5px solid color-mix(in srgb, var(--folder-color), #000 22%);
  border-radius: 4px 4px 1px 1px;
  background: var(--folder-color);
  color: #fff;
  cursor: pointer;
  writing-mode: vertical-rl;
  text-orientation: upright;
  overflow: hidden;
}

.library-folder small {
  opacity: .72;
  font-size: 10px;
}

.folder-content-list {
  display: grid;
  gap: 8px;
}

.folder-content-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  min-height: 40px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.folder-content-item > button {
  min-width: 0;
  border: 0;
  background: transparent;
  color: var(--el-text-color-primary);
  text-align: left;
  cursor: pointer;
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
  position: relative;
  display: flex;
  flex: 0 0 auto;
  align-items: flex-end;
  height: auto;
}

.book-group.insert-before::before {
  position: absolute;
  z-index: 30;
  top: 0;
  bottom: 0;
  left: -3px;
  width: 3px;
  border-radius: 2px;
  background: #2f6fd6;
  content: '';
  pointer-events: none;
}

.book-drop-end-marker {
  z-index: 30;
  align-self: flex-end;
  width: 3px;
  height: 88px;
  border-radius: 2px;
  background: #2f6fd6;
  pointer-events: none;
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
  justify-content: center;
  width: var(--book-item-width, var(--spine-width));
  height: var(--spine-height);
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
  justify-content: var(--book-spine-justify-content, center);
  gap: var(--book-spine-column-gap, 2px);
  width: var(--spine-width);
  height: var(--spine-height);
  padding: 10px var(--book-spine-inline-padding, 5px) 8px;
  border: var(--book-physical-border-width, 1px) solid rgb(26 31 37 / 15%);
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
  color: var(--book-cover-ink, #fff);
  font-size: var(--spine-font-size, 12px);
  font-weight: 700;
  line-height: var(--book-spine-title-line-height, 1.35);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  writing-mode: vertical-lr;
  text-orientation: upright;
  transform: scaleX(var(--book-spine-title-scale-x, 1));
  transform-origin: center;
}

.book-item.orientation-spine-vertical .book-spine-title {
  text-overflow: clip;
  transform: translateX(-0.13em) scaleX(var(--book-spine-title-scale-x, 1));
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
  display: var(--book-qualifier-display, block);
  flex: none;
  margin: 0;
  color: var(--book-cover-ink, #fff);
  font-size: var(--book-qualifier-font-size, 11px);
  font-weight: 600;
  line-height: var(--book-qualifier-line-height, 1.2);
  white-space: nowrap;
  writing-mode: vertical-lr;
  text-orientation: upright;
}

.book-spine-author {
  position: relative;
  z-index: 3;
  display: var(--book-author-display, none);
  flex: none;
  margin: 0;
  color: var(--book-cover-ink, #fff);
  font-size: var(--book-author-font-size, 11px);
  font-weight: 500;
  line-height: var(--book-author-line-height, 1.2);
  writing-mode: vertical-lr;
  text-orientation: upright;
}

.book-item.orientation-spine-vertical .book-spine-qualifier,
.book-item.orientation-spine-vertical .book-spine-author {
  transform: translateX(-0.13em);
}

.book-item.orientation-spine-horizontal .book-spine {
  flex-direction: column;
  justify-content: center;
  gap: 3px;
  width: var(--spine-height);
  height: var(--spine-width);
  padding: var(--book-spine-block-padding, 7px) 12px;
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
  color: var(--book-cover-ink, #fff);
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

.shelf-context-menu {
  width: 152px;
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

.delete-book-dialog-content {
  display: grid;
  gap: 14px;
}

.delete-book-dialog-title {
  color: #1f2937;
  font-weight: 600;
}

.delete-book-dialog-option {
  display: grid;
  grid-template-columns: 76px minmax(0, 1fr);
  align-items: start;
  gap: 12px;
  color: #4b5563;
  line-height: 1.6;
}

.delete-book-dialog-option strong {
  color: #1f2937;
  font-weight: 600;
}

.delete-book-dialog-option.disabled,
.delete-book-dialog-option.disabled strong {
  color: #a8abb2;
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
  background: var(--preview-surface);
  border-radius: 10px;
  color: var(--preview-text);
  overflow: hidden;
  transition: background-color 180ms ease, color 180ms ease;
}

.book-spine-start-year {
  position: absolute;
  z-index: 4;
  bottom: 5px;
  left: 50%;
  display: var(--book-start-year-display, none);
  padding: 2px 0;
  border-radius: 2px;
  background: var(--book-cover-color, var(--book-fallback-color, #3d6383));
  color: var(--book-cover-ink, #fff);
  font-size: 9px;
  font-weight: 650;
  line-height: 1;
  letter-spacing: -0.08em;
  writing-mode: vertical-lr;
  text-orientation: upright;
  transform: translateX(-50%);
}

.book-item.orientation-spine-horizontal .book-spine-start-year,
.book-item.orientation-cover-front .book-spine-start-year {
  padding: 2px 4px;
  letter-spacing: 0;
  writing-mode: horizontal-tb;
}

:global(.book-preview-dialog .el-dialog__header) {
  margin: 0;
  padding: 14px 18px;
  border-bottom: 1px solid var(--preview-border);
}

:global(.book-preview-dialog .el-dialog__body) {
  padding: 0;
}

:global(.book-preview-dialog .el-dialog__footer) {
  padding: 12px 18px;
  border-top: 1px solid var(--preview-border);
}

:global(.book-preview-dialog .el-dialog__close) {
  color: var(--preview-muted);
}

.book-preview-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  min-width: 0;
  padding-right: 32px;
}

.book-preview-title {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
}

.book-preview-title strong {
  overflow: hidden;
  color: var(--preview-text);
  font-size: 16px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.book-preview-title span {
  flex: 0 0 auto;
  color: var(--preview-muted);
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
  background: var(--preview-stage);
  overflow: hidden;
  transition: background-color 180ms ease;
}

.book-preview-image {
  display: block;
  min-width: 0;
  min-height: 0;
  max-width: 100%;
  max-height: 100%;
  background: #fff;
  filter: var(--preview-page-filter);
  object-fit: contain;
  transition: filter 180ms ease;
}

.book-preview-status {
  display: flex;
  align-items: center;
  gap: 4px;
  color: var(--preview-muted);
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
  color: var(--preview-muted);
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

  .book-preview-heading {
    align-items: flex-start;
    flex-direction: column;
    gap: 10px;
  }

  .book-preview-theme {
    width: 100%;
  }
}
</style>
