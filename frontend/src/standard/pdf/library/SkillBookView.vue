<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import RichTextDocumentReader from '@/components/rich-text/RichTextDocumentReader.vue'
import RichTextOutlineNav from '@/components/rich-text/RichTextOutlineNav.vue'
import { formatNoteDateTime } from '@/utils/noteDate'
import {
  extractRichTextOutline,
  type RichTextDocument,
  type RichTextSelection,
} from '@/components/rich-text/document'
import {
  createLibraryAnnotation,
  deleteLibraryAnnotation,
  fetchLibraryAnnotations,
  updateLibraryAnnotation,
  type LibraryAnnotation,
} from '@/api/libraryAnnotations'
import ReaderSettingsPanel from './ReaderSettingsPanel.vue'
import ReaderContextMenu from './ReaderContextMenu.vue'
import BookReaderSurface from './BookReaderSurface.vue'
import ReaderLayout from './ReaderDockLayout.vue'
import ReaderStatus from './ReaderStatus.vue'
import ReaderTocTree from './ReaderTocTree.vue'
import ReaderNavigationPanel from './ReaderNavigationPanel.vue'
import ReaderTreeSplitMenu from './ReaderTreeSplitMenu.vue'
import { useReaderTreeSplit } from './useReaderTreeSplit'
import { appendReaderHeadings, splitReaderTree } from './readerTree'
import { libraryReaderThemeClass } from './readerTheme'
import { useReaderDock as useDockLayout, readerTabContext } from './readerWorkspaceContext'
import { inject } from 'vue'
const workspaceTab = inject(readerTabContext, null)
import { skillDockTools } from './readerDockTools'

const dock = useDockLayout('codeyun.reader.dock.skill.v1', skillDockTools)

import {
  fetchLocalSkillBookCatalog,
  fetchLocalSkillBookChapter,
  fetchLocalSkillBookReadingState,
  syncLocalSkillBookTranslations,
  updateLocalSkillBookReadingState,
  type SkillBookCatalog,
  type SkillBookChapter,
  type SkillBookChapterContent,
  type SkillBookReadingState,
} from '@/api/skillBooks'

const props = defineProps<{
  bookshelfId: string
}>()


const emit = defineEmits<{
  'catalog-updated': [catalog: SkillBookCatalog]
  'reading-state-updated': [state: SkillBookReadingState]
}>()

const LAST_CHAPTER_STORAGE_KEY = 'codeyun.skill-book.local.last-chapter'
const LANGUAGE_STORAGE_KEY = 'codeyun.skill-book.local.language.v1'
const LIVE_REFRESH_INTERVAL_MS = 5 * 60 * 1000
const TRANSLATION_POLL_INTERVAL_MS = 2_000

const contextMenu = ref<InstanceType<typeof ReaderContextMenu>>()
const catalog = ref<SkillBookCatalog | null>(null)
watch(() => catalog.value?.title, title => { if (title) workspaceTab?.title(title) })
const selectedChapterId = ref('')
const selectedChapterRevision = ref('')
const selectedSourceRevision = ref('')
const markdown = ref('')
const originalMarkdown = ref('')
const translatedMarkdown = ref('')
const translationRevision = ref('')
const translationStatus = ref<'not_needed' | 'missing' | 'pending' | 'done' | 'error'>('not_needed')
const translationError = ref('')
const preferredLanguage = ref<'zh' | 'original'>(
  localStorage.getItem(LANGUAGE_STORAGE_KEY) === 'original' ? 'original' : 'zh',
)
const searchText = ref('')
const catalogLoading = ref(false)
const chapterLoading = ref(false)
const errorMessage = ref('')
const documentViewportRef = ref<HTMLElement | null>(null)
const currentCharacterOffset = ref(0)
const activeHeadingId = ref('')
const savedReadingPosition = ref<SkillBookReadingState | null>(null)
const annotations = ref<LibraryAnnotation[]>([])
let refreshTimer: ReturnType<typeof setInterval> | null = null
let translationPollTimer: ReturnType<typeof setInterval> | null = null
let positionSaveTimer: ReturnType<typeof setTimeout> | null = null
let catalogLoadSequence = 0
let chapterLoadSequence = 0

const visible = computed(() => workspaceTab?.active.value ?? true)

const allChapters = computed(() => catalog.value?.skills.flatMap((skill) => skill.chapters) ?? [])
const articleNumberByChapterId = computed(() => {
  const numbers = new Map<string, string>()
  for (const [skillIndex, skill] of (catalog.value?.skills ?? []).entries()) {
    for (const chapter of skill.chapters) {
      if (chapter.kind === 'main') {
        numbers.set(chapter.id, `${skillIndex + 1}`)
      }
    }
  }
  return numbers
})
const selectedChapter = computed(() => (
  allChapters.value.find((chapter) => chapter.id === selectedChapterId.value) ?? null
))
const isEnglishChapter = computed(() => selectedChapter.value?.source_language === 'en')
const effectiveLanguage = computed<'zh' | 'original'>(() => (
  isEnglishChapter.value
  && preferredLanguage.value === 'zh'
  && translationStatus.value === 'done'
  && Boolean(translatedMarkdown.value)
    ? 'zh'
    : 'original'
))
const activeAnnotationChapterId = computed(() => (
  effectiveLanguage.value === 'zh'
    ? `${selectedChapterId.value}:zh-CN`
    : selectedChapterId.value
))
const selectedChapterIndex = computed(() => allChapters.value.findIndex(
  (chapter) => chapter.id === selectedChapterId.value,
))
const currentBookPage = computed(() => {
  const chapter = selectedChapter.value
  if (!chapter || chapter.character_count <= 0) return chapter?.page_start ?? 1
  const capacity = Math.max(1, catalog.value?.page_capacity_units ?? 1000)
  const boundedOffset = Math.min(chapter.character_count - 1, Math.max(0, currentCharacterOffset.value))
  return Math.floor((chapter.book_character_start + boundedOffset) / capacity) + 1
})
const READER_TOC_COLUMN_WIDTH = 260
const READER_OUTLINE_COLUMN_WIDTH = 220
const SKILL_PAGE_BASE_WIDTH = 760

/** 收起的栏把宽度还给正文，避免收栏只留下一片空白背景。 */
const readerPanelFreedWidth = computed(() => (
  (dock.regionOpen('left') ? 0 : READER_TOC_COLUMN_WIDTH)
  + (dock.regionOpen('right') ? 0 : READER_OUTLINE_COLUMN_WIDTH)
))
const documentPaperStyle = computed(() => {
  const widthMillimeters = catalog.value?.page_width_mm ?? 210
  const heightMillimeters = catalog.value?.page_height_mm ?? 297
  const pageWidth = Math.round(SKILL_PAGE_BASE_WIDTH * widthMillimeters / 210)
  return {
    '--skill-page-width': `${pageWidth}px`,
    '--skill-reading-max-width': `${pageWidth + readerPanelFreedWidth.value}px`,
    '--skill-page-aspect-ratio': `${widthMillimeters} / ${heightMillimeters}`,
  }
})
const filteredSkills = computed(() => {
  const query = searchText.value.trim().toLowerCase()
  if (!query) {
    return catalog.value?.skills ?? []
  }
  return (catalog.value?.skills ?? []).flatMap((skill) => {
    const skillMatches = `${skill.name}\n${skill.description}`.toLowerCase().includes(query)
    const chapters = skillMatches
      ? skill.chapters
      : skill.chapters.filter((chapter) => chapter.title.toLowerCase().includes(query))
    return chapters.length ? [{ ...skill, chapters }] : []
  })
})
const currentDocument = computed<RichTextDocument | null>(() => {
  const chapter = selectedChapter.value
  if (!chapter) return null
  return {
    id: activeAnnotationChapterId.value || chapter.id,
    title: chapter.title,
    content: markdown.value,
    format: 'markdown',
    revision: selectedChapterRevision.value,
    capabilities: {
      // Skill 文件目前以本地目录为事实源，图书馆只提供阅读能力。
      // 将来接入保存适配器后，同一文档模型即可开启编辑能力。
      canEdit: false,
      canAnnotate: true,
      canEditContent: false,
      editMode: null,
      sourcePolicy: 'external',
    },
  }
})
const documentOutline = computed(() => extractRichTextOutline(currentDocument.value))

function formatFileTime(timestamp: number) {
  return formatNoteDateTime(timestamp * 1000)
}

function stopTranslationPolling() {
  if (translationPollTimer) {
    clearInterval(translationPollTimer)
    translationPollTimer = null
  }
}

function applyPreferredLanguage() {
  if (effectiveLanguage.value === 'zh') {
    markdown.value = translatedMarkdown.value
    selectedChapterRevision.value = translationRevision.value
  } else {
    markdown.value = originalMarkdown.value
    selectedChapterRevision.value = selectedChapter.value?.revision ?? ''
  }
}

function updateTranslationState(content: SkillBookChapterContent) {
  selectedSourceRevision.value = content.chapter.revision
  originalMarkdown.value = content.markdown
  translatedMarkdown.value = content.translation.markdown
  translationRevision.value = content.translation.revision
  translationStatus.value = content.translation.status
  translationError.value = content.translation.error_message
  applyPreferredLanguage()
}

async function changePreferredLanguage(value: string | number | boolean | undefined) {
  const language = value === 'original' ? 'original' : 'zh'
  if (language === 'zh' && translationStatus.value !== 'done') return
  persistReadingPosition()
  preferredLanguage.value = language
  localStorage.setItem(LANGUAGE_STORAGE_KEY, language)
  applyPreferredLanguage()
  activeHeadingId.value = ''
  await loadAnnotations(activeAnnotationChapterId.value)
  await nextTick()
  if (documentViewportRef.value) documentViewportRef.value.scrollTop = 0
  updateActiveHeading()
}

async function refreshSelectedTranslation(chapterId: string) {
  try {
    const content = await fetchLocalSkillBookChapter(chapterId)
    if (selectedChapterId.value !== chapterId) return
    const wasReady = translationStatus.value === 'done'
    updateTranslationState(content)
    if (translationStatus.value === 'done' || translationStatus.value === 'error') {
      stopTranslationPolling()
    }
    if (!wasReady && translationStatus.value === 'done') {
      await loadAnnotations(activeAnnotationChapterId.value)
      await nextTick()
      updateActiveHeading()
    }
  } catch (error) {
    console.warn('Failed to refresh Skill translation:', error)
    stopTranslationPolling()
  }
}

function startTranslationPolling(chapterId: string) {
  stopTranslationPolling()
  if (!isEnglishChapter.value || translationStatus.value === 'done' || translationStatus.value === 'error') {
    return
  }
  translationPollTimer = setInterval(() => {
    void refreshSelectedTranslation(chapterId)
  }, TRANSLATION_POLL_INTERVAL_MS)
}

async function ensureSkillTranslations() {
  try {
    await syncLocalSkillBookTranslations()
  } catch (error) {
    console.warn('Failed to start Skill translation:', error)
    if (isEnglishChapter.value && translationStatus.value !== 'done') {
      translationStatus.value = 'error'
      translationError.value = '中文版生成服务暂不可用'
      stopTranslationPolling()
    }
  }
}

const { outlineLevel, setOutlineLevel } = useReaderTreeSplit(() => 'local-skill')
const splitTree = computed(() => {
  const items = appendReaderHeadings(treeItems.value, selectedChapterId.value, documentOutline.value.filter((item, index) => index !== 0 || item.level !== 1))
  const heading = `heading:${activeHeadingId.value}`
  return splitReaderTree(items, items.some(item => item.id === heading) ? heading : selectedChapterId.value, outlineLevel.value)
})
function selectTreeTarget(id: string) {
  if (id.startsWith('heading:')) void navigateToHeading(id.slice(8))
  else void selectChapter(id)
}
const treeItems = computed(() => filteredSkills.value.flatMap(skill => {
  const main = skill.chapters.find(chapter => chapter.kind === 'main')
  return skill.chapters.map(chapter => ({
    id: chapter.id,
    parentId: chapter.kind === 'main' ? null : main?.id,
    title: chapter.kind === 'main' ? skill.name : chapter.title,
    number: chapter.kind === 'main' ? String(articleNumberByChapterId.value.get(chapter.id) ?? '') : '',
  }))
}))

function chapterPathMap() {
  return new Map(allChapters.value.map((chapter) => [chapter.relative_path, chapter.id]))
}

function normalizeRelativeMarkdownPath(basePath: string, href: string) {
  const cleanHref = decodeURIComponent(href.split('#')[0] ?? '').replace(/\\/g, '/')
  const baseParts = basePath.split('/').slice(0, -1)
  const result = cleanHref.startsWith('/') ? [] : [...baseParts]
  for (const part of cleanHref.split('/')) {
    if (!part || part === '.') {
      continue
    }
    if (part === '..') {
      result.pop()
      continue
    }
    result.push(part)
  }
  return result.join('/')
}

function calculateCharacterOffset() {
  const viewport = documentViewportRef.value
  const chapter = selectedChapter.value
  if (!viewport || !chapter) return 0
  const maxScrollTop = Math.max(0, viewport.scrollHeight - viewport.clientHeight)
  if (maxScrollTop <= 0) return 0
  const progress = Math.min(1, Math.max(0, viewport.scrollTop / maxScrollTop))
  return Math.round(progress * chapter.character_count)
}

function persistReadingPosition() {
  if (workspaceTab && !workspaceTab.canPersist()) return
  const chapter = selectedChapter.value
  if (!chapter) return
  if (!workspaceTab || workspaceTab.active.value) currentCharacterOffset.value = calculateCharacterOffset()
  savedReadingPosition.value = {
    book_id: catalog.value?.id ?? 'local-skills',
    chapter_id: chapter.id,
    character_offset: currentCharacterOffset.value,
    chapter_revision: chapter.revision,
    current_page: currentBookPage.value,
    pagination_version: catalog.value?.pagination_version ?? 1,
    page_format: catalog.value?.page_format ?? 'A4',
    updated_at: Date.now() / 1000,
  }
  void updateLocalSkillBookReadingState({
    chapter_id: chapter.id,
    character_offset: currentCharacterOffset.value,
    chapter_revision: chapter.revision,
  }).then((state) => {
    savedReadingPosition.value = state
    emit('reading-state-updated', state)
  }).catch((error) => {
    console.warn('Failed to save Skill reading position:', error)
  })
  localStorage.setItem(LAST_CHAPTER_STORAGE_KEY, chapter.id)
}

function handleDocumentScroll() {
  if (workspaceTab && !workspaceTab.active.value) return
  if (!workspaceTab || workspaceTab.active.value) currentCharacterOffset.value = calculateCharacterOffset()
  updateActiveHeading()
  if (positionSaveTimer) clearTimeout(positionSaveTimer)
  positionSaveTimer = setTimeout(() => {
    positionSaveTimer = null
    persistReadingPosition()
  }, 180)
}

function updateActiveHeading() {
  const viewport = documentViewportRef.value
  if (!viewport || !documentOutline.value.length) {
    activeHeadingId.value = ''
    return
  }
  const viewportTop = viewport.getBoundingClientRect().top
  let activeId = documentOutline.value[0]?.id ?? ''
  for (const item of documentOutline.value) {
    const heading = viewport.querySelector<HTMLElement>(`#${CSS.escape(item.id)}`)
    if (!heading) continue
    if (heading.getBoundingClientRect().top - viewportTop <= 72) {
      activeId = item.id
    } else {
      break
    }
  }
  activeHeadingId.value = activeId
}

function navigateToHeading(headingId: string) {
  const viewport = documentViewportRef.value
  const heading = viewport?.querySelector<HTMLElement>(`#${CSS.escape(headingId)}`)
  if (!viewport || !heading) return
  const top = heading.getBoundingClientRect().top
    - viewport.getBoundingClientRect().top
    + viewport.scrollTop
    - 18
  viewport.scrollTo({ top: Math.max(0, top), behavior: 'smooth' })
  activeHeadingId.value = headingId
}

async function restoreReadingPosition(characterOffset: number) {
  await nextTick()
  const viewport = documentViewportRef.value
  const chapter = selectedChapter.value
  if (!viewport || !chapter) return
  currentCharacterOffset.value = Math.min(chapter.character_count, Math.max(0, characterOffset))
  const maxScrollTop = Math.max(0, viewport.scrollHeight - viewport.clientHeight)
  const progress = chapter.character_count > 0
    ? currentCharacterOffset.value / chapter.character_count
    : 0
  viewport.scrollTop = maxScrollTop * progress
  updateActiveHeading()
}

async function loadChapter(
  chapterId: string,
  options: { silent?: boolean; restoreOffset?: number } = {},
) {
  stopTranslationPolling()
  const sequence = ++chapterLoadSequence
  let loaded = false
  if (!options.silent) {
    chapterLoading.value = true
    errorMessage.value = ''
  }
  try {
    const content = await fetchLocalSkillBookChapter(chapterId)
    if (sequence !== chapterLoadSequence || selectedChapterId.value !== chapterId) {
      return
    }
    updateTranslationState(content)
    if (content.chapter.source_language === 'en' && content.translation.status !== 'done') {
      void ensureSkillTranslations()
    }
    activeHeadingId.value = ''
    loaded = true
    await loadAnnotations(activeAnnotationChapterId.value)
    startTranslationPolling(chapterId)
  } catch (error) {
    if (sequence !== chapterLoadSequence || options.silent) {
      return
    }
    console.warn('Failed to load live Skill chapter:', error)
    errorMessage.value = '章节读取失败'
  } finally {
    if (sequence === chapterLoadSequence && !options.silent) {
      chapterLoading.value = false
    }
  }
  if (loaded && sequence === chapterLoadSequence) {
    await restoreReadingPosition(options.restoreOffset ?? 0)
  }
}

async function loadAnnotations(chapterId = activeAnnotationChapterId.value) {
  if (!catalog.value?.asset_id || !chapterId) {
    annotations.value = []
    return
  }
  try {
    annotations.value = await fetchLibraryAnnotations(
      'skill-book',
      catalog.value.asset_id,
      chapterId,
    )
  } catch (error) {
    console.warn('Failed to load Skill annotations:', error)
    annotations.value = []
  }
}

function annotationErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : '批注保存失败'
}

async function handleAnnotationCreate(payload: {
  selection: RichTextSelection
  withComment: boolean
}) {
  if (!catalog.value?.asset_id || !activeAnnotationChapterId.value) return
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
    const created = await createLibraryAnnotation({
      resource_type: 'skill-book',
      resource_id: catalog.value.asset_id,
      chapter_id: activeAnnotationChapterId.value,
      kind: commentText ? 'comment' : 'highlight',
      color: 'yellow',
      quote_text: payload.selection.quoteText,
      prefix_text: payload.selection.prefixText,
      suffix_text: payload.selection.suffixText,
      start_offset: payload.selection.startOffset,
      end_offset: payload.selection.endOffset,
      source_revision: selectedChapterRevision.value,
      comment_text: commentText,
    })
    annotations.value = [...annotations.value, created]
  } catch (error) {
    ElMessage.error(annotationErrorMessage(error))
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

async function selectChapter(chapterId: string, options: { restoreSaved?: boolean } = {}) {
  if (!chapterId) {
    return
  }
  if (selectedChapterId.value && selectedChapterId.value !== chapterId) {
    persistReadingPosition()
  }
  selectedChapterId.value = chapterId
  localStorage.setItem(LAST_CHAPTER_STORAGE_KEY, chapterId)
  const savedPosition = options.restoreSaved ? savedReadingPosition.value : null
  await loadChapter(chapterId, {
    restoreOffset: savedPosition?.chapter_id === chapterId ? savedPosition.character_offset : 0,
  })
}

async function loadCatalog(options: { silent?: boolean } = {}) {
  const sequence = ++catalogLoadSequence
  if (!options.silent) {
    catalogLoading.value = true
    errorMessage.value = ''
  }
  try {
    const nextCatalog = await fetchLocalSkillBookCatalog(props.bookshelfId)
    if (sequence !== catalogLoadSequence) {
      return
    }
    const previousRevision = catalog.value?.revision
    catalog.value = nextCatalog
    emit('catalog-updated', nextCatalog)
    void ensureSkillTranslations()

    if (!options.silent) {
      try {
        savedReadingPosition.value = await fetchLocalSkillBookReadingState()
      } catch (error) {
        console.warn('Failed to load Skill reading position:', error)
      }
    }

    const savedPosition = savedReadingPosition.value
    const savedChapterId = selectedChapterId.value
      || savedPosition?.chapter_id
      || localStorage.getItem(LAST_CHAPTER_STORAGE_KEY)
      || ''
    const nextChapter = nextCatalog.skills
      .flatMap((skill) => skill.chapters)
      .find((chapter) => chapter.id === savedChapterId)
      ?? nextCatalog.skills[0]?.chapters[0]
      ?? null

    if (!nextChapter) {
      selectedChapterId.value = ''
      markdown.value = ''
      originalMarkdown.value = ''
      translatedMarkdown.value = ''
      return
    }
    const selectionChanged = nextChapter.id !== selectedChapterId.value
    selectedChapterId.value = nextChapter.id
    localStorage.setItem(LAST_CHAPTER_STORAGE_KEY, nextChapter.id)
    if (selectionChanged || !markdown.value) {
      await loadChapter(nextChapter.id, {
        ...options,
        restoreOffset: savedPosition?.chapter_id === nextChapter.id ? savedPosition.character_offset : 0,
      })
    } else if (previousRevision !== nextCatalog.revision && nextChapter.revision !== selectedSourceRevision.value) {
      const currentOffset = calculateCharacterOffset()
      await loadChapter(nextChapter.id, { silent: true, restoreOffset: currentOffset })
    }
  } catch (error) {
    if (sequence !== catalogLoadSequence || options.silent) {
      return
    }
    console.warn('Failed to load live Skill catalog:', error)
    errorMessage.value = 'Skill 目录读取失败'
  } finally {
    if (sequence === catalogLoadSequence && !options.silent) {
      catalogLoading.value = false
    }
  }
}

function turnChapter(offset: number) {
  const target = allChapters.value[selectedChapterIndex.value + offset]
  if (target) {
    void selectChapter(target.id)
  }
}

function handleDocumentLink(payload: { href: string; event: MouseEvent }) {
  const { href, event } = payload
  if (!href || href.startsWith('#') || /^[a-z][a-z0-9+.-]*:/i.test(href)) {
    return
  }
  const currentPath = selectedChapter.value?.relative_path
  if (!currentPath || !href.split('#')[0]?.toLowerCase().endsWith('.md')) {
    return
  }
  const targetId = chapterPathMap().get(normalizeRelativeMarkdownPath(currentPath, href))
  if (!targetId) {
    return
  }
  event.preventDefault()
  void selectChapter(targetId)
}

function handleReaderKeydown(event: KeyboardEvent) {
  if (workspaceTab && !workspaceTab.active.value) return
  if ((event.target as HTMLElement | null)?.closest('[role="tablist"], [role="tree"]')) return
  if (!visible.value || event.defaultPrevented) {
    return
  }
  const target = event.target as HTMLElement | null
  if (target?.closest('input, textarea, [contenteditable="true"]')) {
    return
  }
  if (event.key === 'ArrowLeft') {
    event.preventDefault()
    turnChapter(-1)
  } else if (event.key === 'ArrowRight') {
    event.preventDefault()
    turnChapter(1)
  }
}

function startLiveRefresh() {
  stopLiveRefresh()
  refreshTimer = setInterval(() => {
    if (visible.value && (!workspaceTab || workspaceTab.active.value)) {
      void loadCatalog({ silent: true })
    }
  }, LIVE_REFRESH_INTERVAL_MS)
}

function stopLiveRefresh() {
  if (refreshTimer) {
    clearInterval(refreshTimer)
    refreshTimer = null
  }
}

watch(() => props.bookshelfId, () => {
  window.addEventListener('keydown', handleReaderKeydown, true)
  startLiveRefresh()
  void loadCatalog()
}, { immediate: true })

watch(() => workspaceTab?.active.value, active => {
  if (!workspaceTab) return
  if (active) startLiveRefresh()
  else { persistReadingPosition(); stopLiveRefresh(); stopTranslationPolling() }
})

onBeforeUnmount(() => {
  persistReadingPosition()
  if (positionSaveTimer) clearTimeout(positionSaveTimer)
  stopTranslationPolling()
  window.removeEventListener('keydown', handleReaderKeydown, true)
  stopLiveRefresh()
})
</script>

<template>
  <BookReaderSurface>

    <ReaderLayout
      @context-menu="contextMenu?.open($event)"
      :dock="dock"
    >
      <template #toc>
      <ReaderNavigationPanel>
        <el-input v-model="searchText" clearable placeholder="搜索目录" />
        <ReaderStatus v-if="catalogLoading && !catalog" message="正在读取目录…" />
        <ReaderStatus v-else-if="errorMessage && !catalog" :message="errorMessage" error retry @retry="loadCatalog()" />
        <ReaderTreeSplitMenu v-else v-slot="{ open }" :model-value="outlineLevel" @update:model-value="setOutlineLevel">
          <ReaderTocTree v-context-menu="open" :items="outlineLevel ? splitTree.toc : treeItems" :active-id="outlineLevel ? splitTree.tocActiveId : selectedChapterId"
            storage-key="codeyun.reader.tree.local-skill" :expand-all="Boolean(searchText.trim())" @select="selectTreeTarget" />
        </ReaderTreeSplitMenu>
      </ReaderNavigationPanel>
      </template>

      <template #default>
        <header class="skill-book-content-toolbar">
          <div>
            <strong>{{ selectedChapter?.title ?? '未选择章节' }}</strong>
            <span v-if="catalog">
              页码：{{ currentBookPage }}/{{ catalog.estimated_page_count }}
            </span>
            <span v-if="selectedChapter" class="skill-book-file-times">
              创建：{{ formatFileTime(selectedChapter.created_at) }}
              · 修改：{{ formatFileTime(selectedChapter.modified_at) }}
            </span>
          </div>
          <div class="skill-book-content-actions">
            <el-radio-group
              v-if="isEnglishChapter && translationStatus === 'done'"
              :model-value="effectiveLanguage"
              size="small"
              aria-label="正文语言"
              @change="changePreferredLanguage"
            >
              <el-radio-button value="zh">中文</el-radio-button>
              <el-radio-button value="original">原文</el-radio-button>
            </el-radio-group>
            <span
              v-else-if="isEnglishChapter && translationStatus !== 'not_needed'"
              class="skill-book-translation-status"
              :class="{ 'is-error': translationStatus === 'error' }"
              :title="translationError"
            >
              {{ translationStatus === 'error' ? '中文版生成失败' : '中文版生成中' }}
            </span>
            <el-button
              :disabled="selectedChapterIndex <= 0"
              aria-keyshortcuts="ArrowLeft"
              @click="turnChapter(-1)"
            >上一篇</el-button>
            <el-button
              :disabled="selectedChapterIndex < 0 || selectedChapterIndex >= allChapters.length - 1"
              aria-keyshortcuts="ArrowRight"
              @click="turnChapter(1)"
            >下一篇</el-button>
          </div>
        </header>

        <ReaderStatus v-if="chapterLoading" message="正在读取最新内容…" />
        <ReaderStatus v-else-if="errorMessage" :message="errorMessage" error :retry="Boolean(selectedChapterId)" @retry="loadChapter(selectedChapterId)" />
        <div
          v-else
          ref="documentViewportRef"
          class="skill-book-document"
          :style="documentPaperStyle"
          @scroll.passive="handleDocumentScroll"
          v-context-menu="($event: MouseEvent) => (contextMenu?.open($event))"
        >
          <RichTextDocumentReader
            :document="currentDocument"
            :annotations="annotations"
            @link-activate="handleDocumentLink"
            @annotation-create="handleAnnotationCreate"
            @annotation-activate="handleAnnotationActivate"
            @annotation-delete="handleAnnotationDelete"
          />
        </div>

      </template>

      <template #settings><ReaderSettingsPanel /></template>
      <template #outline>
      <ReaderNavigationPanel>
      <template v-if="outlineLevel">
        <ReaderTocTree :items="splitTree.outline" :active-id="splitTree.outlineActiveId" :storage-key="`codeyun.reader.split-outline.${'local-skill'}`" @select="selectTreeTarget" />
      </template>
      <RichTextOutlineNav embedded v-else
        :items="documentOutline"
        :active-id="activeHeadingId"
        :document-title="currentDocument?.title"
        @select="navigateToHeading"
      />
      </ReaderNavigationPanel>
      </template>
    </ReaderLayout>
  </BookReaderSurface>
  <ReaderContextMenu ref="contextMenu" />
</template>

<style scoped>
.skill-book-window-actions { display: flex; align-items: center; gap: 12px; }
.skill-book-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding-right: 34px;
}

.skill-book-title {
  display: flex;
  align-items: baseline;
  min-width: 0;
  gap: 10px;
}

.skill-book-heading strong {
  overflow: hidden;
  color: var(--reader-heading);
  font-size: 16px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.skill-book-heading span {
  color: var(--reader-muted);
  font-size: 12px;
}

.skill-book-content-toolbar {
  box-sizing: border-box;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  min-height: 50px;
  padding: 8px 18px;
  border-bottom: 1px solid var(--reader-border);
}

.skill-book-content-toolbar > div:first-child {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
}

.skill-book-content-toolbar strong {
  overflow: hidden;
  color: var(--reader-heading);
  font-size: 15px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.skill-book-content-toolbar span {
  flex: 0 0 auto;
  color: var(--reader-muted);
  font-size: 12px;
}

.skill-book-content-actions {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 10px;
}

.skill-book-translation-status {
  color: var(--reader-muted);
  font-size: 12px;
  white-space: nowrap;
}

.skill-book-translation-status.is-error {
  color: #9b4d4d;
}

.skill-book-document {
  box-sizing: border-box;
  flex: 1;
  min-height: 0;

  margin: 0;
  padding: var(--reader-content-top-inset, 12px) clamp(22px, 5vw, 64px) 48px;
  overflow: auto;
  background: var(--reader-content);
}

.skill-book-document :deep(.rich-text-document-reader) {
  width: min(100%, var(--skill-reading-max-width, var(--skill-page-width)));
  min-height: 100%;
  aspect-ratio: var(--skill-page-aspect-ratio);
  margin: 0 auto;
}

@media (max-width: 980px) {
  .skill-book-heading {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
