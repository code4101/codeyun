<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import RichTextDocumentReader from '@/components/rich-text/RichTextDocumentReader.vue'
import type { RichTextDocument } from '@/components/rich-text/document'

import {
  fetchLocalSkillBookCatalog,
  fetchLocalSkillBookChapter,
  type SkillBookCatalog,
  type SkillBookChapter,
} from '@/api/skillBooks'

const props = defineProps<{
  modelValue: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  'catalog-updated': [catalog: SkillBookCatalog]
}>()

const LAST_CHAPTER_STORAGE_KEY = 'codeyun.skill-book.local.last-chapter'
const LIVE_REFRESH_INTERVAL_MS = 3000

const catalog = ref<SkillBookCatalog | null>(null)
const selectedChapterId = ref('')
const selectedChapterRevision = ref('')
const markdown = ref('')
const searchText = ref('')
const catalogLoading = ref(false)
const chapterLoading = ref(false)
const errorMessage = ref('')
let refreshTimer: ReturnType<typeof setInterval> | null = null
let catalogLoadSequence = 0
let chapterLoadSequence = 0

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

const allChapters = computed(() => catalog.value?.skills.flatMap((skill) => skill.chapters) ?? [])
const selectedChapter = computed(() => (
  allChapters.value.find((chapter) => chapter.id === selectedChapterId.value) ?? null
))
const selectedChapterIndex = computed(() => allChapters.value.findIndex(
  (chapter) => chapter.id === selectedChapterId.value,
))
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
    id: chapter.id,
    title: chapter.title,
    content: markdown.value,
    format: 'markdown',
    revision: selectedChapterRevision.value,
    capabilities: {
      // Skill 文件目前以本地目录为事实源，图书馆只提供阅读能力。
      // 将来接入保存适配器后，同一文档模型即可开启编辑能力。
      canEdit: false,
    },
  }
})

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

async function loadChapter(chapterId: string, options: { silent?: boolean } = {}) {
  const sequence = ++chapterLoadSequence
  if (!options.silent) {
    chapterLoading.value = true
    errorMessage.value = ''
  }
  try {
    const content = await fetchLocalSkillBookChapter(chapterId)
    if (sequence !== chapterLoadSequence || selectedChapterId.value !== chapterId) {
      return
    }
    markdown.value = content.markdown
    selectedChapterRevision.value = content.chapter.revision
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
}

async function selectChapter(chapterId: string) {
  if (!chapterId) {
    return
  }
  selectedChapterId.value = chapterId
  localStorage.setItem(LAST_CHAPTER_STORAGE_KEY, chapterId)
  await loadChapter(chapterId)
}

async function loadCatalog(options: { silent?: boolean } = {}) {
  const sequence = ++catalogLoadSequence
  if (!options.silent) {
    catalogLoading.value = true
    errorMessage.value = ''
  }
  try {
    const nextCatalog = await fetchLocalSkillBookCatalog()
    if (sequence !== catalogLoadSequence || !visible.value) {
      return
    }
    const previousRevision = catalog.value?.revision
    catalog.value = nextCatalog
    emit('catalog-updated', nextCatalog)

    const savedChapterId = selectedChapterId.value
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
      return
    }
    const selectionChanged = nextChapter.id !== selectedChapterId.value
    selectedChapterId.value = nextChapter.id
    localStorage.setItem(LAST_CHAPTER_STORAGE_KEY, nextChapter.id)
    if (selectionChanged || !markdown.value) {
      await loadChapter(nextChapter.id, options)
    } else if (previousRevision !== nextCatalog.revision && nextChapter.revision !== selectedChapterRevision.value) {
      await loadChapter(nextChapter.id, { silent: true })
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
    if (visible.value) {
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

watch(() => props.modelValue, (isVisible) => {
  if (isVisible) {
    window.addEventListener('keydown', handleReaderKeydown, true)
    startLiveRefresh()
    void loadCatalog()
  } else {
    window.removeEventListener('keydown', handleReaderKeydown, true)
    stopLiveRefresh()
  }
}, { immediate: true })

onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleReaderKeydown, true)
  stopLiveRefresh()
})
</script>

<template>
  <el-dialog
    v-model="visible"
    class="skill-book-dialog"
    width="min(1120px, calc(100vw - 32px))"
    append-to-body
    align-center
    destroy-on-close
  >
    <template #header>
      <div class="skill-book-heading">
        <strong>{{ catalog?.title ?? '本地 Skill 手册' }}</strong>
        <span>动态阅读</span>
      </div>
    </template>

    <div class="skill-book-reader">
      <aside class="skill-book-toc" aria-label="Skill 目录">
        <el-input v-model="searchText" clearable placeholder="搜索目录" />
        <div v-if="catalogLoading && !catalog" class="skill-book-status">正在读取目录…</div>
        <div v-else-if="errorMessage && !catalog" class="skill-book-status is-error">
          <span>{{ errorMessage }}</span>
          <el-button text type="primary" @click="loadCatalog()">重试</el-button>
        </div>
        <nav v-else class="skill-book-toc-list">
          <section v-for="skill in filteredSkills" :key="skill.id" class="skill-book-toc-group">
            <button
              v-for="chapter in skill.chapters"
              :key="chapter.id"
              type="button"
              class="skill-book-toc-item"
              :class="{
                active: chapter.id === selectedChapterId,
                reference: chapter.kind === 'reference',
              }"
              :title="chapter.relative_path"
              @click="selectChapter(chapter.id)"
            >
              <span>{{ chapter.kind === 'main' ? skill.name : chapter.title }}</span>
            </button>
          </section>
        </nav>
      </aside>

      <main class="skill-book-content">
        <header class="skill-book-content-toolbar">
          <div>
            <strong>{{ selectedChapter?.title ?? '未选择章节' }}</strong>
            <span v-if="catalog">{{ selectedChapterIndex + 1 }} / {{ allChapters.length }}</span>
          </div>
          <div class="skill-book-content-actions">
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

        <div v-if="chapterLoading" class="skill-book-status">正在读取最新内容…</div>
        <div v-else-if="errorMessage" class="skill-book-status is-error">
          <span>{{ errorMessage }}</span>
          <el-button v-if="selectedChapterId" text type="primary" @click="loadChapter(selectedChapterId)">
            重试
          </el-button>
        </div>
        <RichTextDocumentReader
          v-else
          class="skill-book-document"
          :document="currentDocument"
          @link-activate="handleDocumentLink"
        />
      </main>
    </div>
  </el-dialog>
</template>

<style scoped>
.skill-book-heading {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.skill-book-heading strong {
  color: #172033;
  font-size: 16px;
}

.skill-book-heading span {
  color: #8a96a8;
  font-size: 12px;
}

.skill-book-reader {
  display: grid;
  grid-template-columns: 260px minmax(0, 1fr);
  height: calc(100dvh - 128px);
  min-height: 420px;
  border: 1px solid #dfe5ec;
  background: #fff;
  overflow: hidden;
}

.skill-book-toc {
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  min-height: 0;
  padding: 12px;
  border-right: 1px solid #e4e9ef;
  background: #f7f9fb;
}

.skill-book-toc-list {
  flex: 1;
  min-height: 0;
  margin-top: 10px;
  overflow: auto;
}

.skill-book-toc-group + .skill-book-toc-group {
  margin-top: 3px;
}

.skill-book-toc-item {
  display: block;
  width: 100%;
  min-height: 30px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  padding: 5px 8px;
  color: #344256;
  font-size: 13px;
  line-height: 20px;
  text-align: left;
  cursor: pointer;
}

.skill-book-toc-item.reference {
  padding-left: 22px;
  color: #657286;
  font-size: 12px;
}

.skill-book-toc-item:hover {
  background: #edf2f7;
}

.skill-book-toc-item.active {
  background: #e4eefc;
  color: #1f5fbe;
  font-weight: 700;
}

.skill-book-content {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}

.skill-book-content-toolbar {
  box-sizing: border-box;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  min-height: 50px;
  padding: 8px 18px;
  border-bottom: 1px solid #e8ecf1;
}

.skill-book-content-toolbar > div:first-child {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
}

.skill-book-content-toolbar strong {
  overflow: hidden;
  color: #1d2939;
  font-size: 15px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.skill-book-content-toolbar span {
  flex: 0 0 auto;
  color: #8a96a8;
  font-size: 12px;
}

.skill-book-content-actions {
  display: flex;
  flex: 0 0 auto;
}

.skill-book-document {
  box-sizing: border-box;
  flex: 1;
  min-height: 0;
  margin: 0;
  padding: 22px clamp(22px, 5vw, 64px) 48px;
  overflow: auto;
}

.skill-book-status {
  display: grid;
  place-items: center;
  flex: 1;
  min-height: 120px;
  color: #657286;
  font-size: 13px;
}

.skill-book-status.is-error {
  color: #9b4d4d;
}

@media (max-width: 760px) {
  .skill-book-reader {
    grid-template-columns: 1fr;
    grid-template-rows: minmax(120px, 32%) minmax(0, 1fr);
  }

  .skill-book-toc {
    border-right: 0;
    border-bottom: 1px solid #e4e9ef;
  }
}
</style>
