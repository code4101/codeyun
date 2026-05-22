<template>
  <div class="doc-resource-page" v-loading="loading">
    <main class="doc-main" :class="{ 'has-outline': outlineItems.length > 0 }">
      <section v-if="currentNote" ref="editorShellRef" class="doc-editor-shell">
        <div class="doc-editor-frame">
          <SharedNoteEditor
            :model-value="currentNote"
            :readonly="readonly"
            :show-private-toggle="true"
            readonly-presentation
            editor-layout="fill"
            :on-save="handleDocSave"
            :on-save-keepalive="handleDocSaveKeepalive"
            @change="handleEditorChange"
          >
            <template #actions="{ note, readonly: editorReadonly }">
              <NoteTitleActions
                v-if="note"
                :readonly="editorReadonly"
                :doc-href="resolveDocHref(note)"
                :show-doc-link="false"
                :show-share="!editorReadonly && canManageAccess"
                :can-share="true"
                :show-copy="!editorReadonly"
                :can-copy="true"
                :show-delete="!editorReadonly"
                :can-delete="true"
                @share="openShareDialog"
                @copy="showCopyDialog = true"
                @delete="deleteCurrentNote"
              />
            </template>

            <template #meta-actions="{ readonly: editorReadonly }">
              <el-tooltip content="根据当前标题，并参考已有条目元数据自动识别分类、形态、阶段" placement="top">
                <el-button
                  size="small"
                  :icon="MagicStick"
                  :loading="aiCategorizing"
                  :disabled="editorReadonly || !currentNote"
                  @click="categorizeCurrentNote"
                >
                  AI分类
                </el-button>
              </el-tooltip>
              <el-tooltip content="全景图：展示该节点所在的完整关联网络" placement="top">
                <el-button
                  size="small"
                  :disabled="editorReadonly || !hasConnections"
                  @click="openGraph('planetary')"
                >
                  行星图
                </el-button>
              </el-tooltip>
              <el-tooltip content="衍生图：仅展示该节点向下延伸的发展网络（忽略来源）" placement="top">
                <el-button
                  size="small"
                  :disabled="editorReadonly || !hasOutConnections"
                  @click="openGraph('satellite')"
                >
                  卫星图
                </el-button>
              </el-tooltip>
            </template>
          </SharedNoteEditor>
        </div>
      </section>

      <section v-else-if="errorText" class="doc-empty">
        <el-empty :description="errorText" />
      </section>

      <aside v-if="outlineItems.length > 0" class="doc-outline-panel">
        <div class="outline-toolbar">
          <span class="outline-title">目录</span>
          <el-checkbox v-model="outlineNumbering" size="small">自动编号</el-checkbox>
        </div>
        <DocOutline
          :items="displayOutlineItems"
          :active-key="activeOutlineKey"
          @jump="jumpToOutlineItem"
        />
      </aside>
    </main>

    <NoteCopyDialog
      v-if="currentNote"
      v-model="showCopyDialog"
      :source-note="currentNote"
      @success="handleCopySuccess"
    />

    <NoteDocAccessDialog
      v-if="currentNote"
      v-model="showAccessDialog"
      :note-ref="getDocRouteRef(currentNote)"
      :title="currentNote.title"
      @update:access="handleAccessUpdate"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { MagicStick } from '@element-plus/icons-vue'

import NoteCopyDialog from '@/components/NoteCopyDialog.vue'
import NoteDocAccessDialog from '@/components/NoteDocAccessDialog.vue'
import NoteTitleActions from '@/components/NoteTitleActions.vue'
import SharedNoteEditor from '@/components/SharedNoteEditor.vue'
import {
  type NoteDocResourceAccess,
  type NoteDocUpdatePayload,
  type NoteNode,
  noteKey,
  useNoteStore,
} from '@/api/notes'
import type { EditableNotePatch } from '@/utils/noteAutoSave'
import { putJsonKeepalive } from '@/utils/keepaliveRequest'
import DocOutline from './DocOutline.vue'

interface DocOutlineItem {
  key: string
  text: string
  level: number
  number?: string
}

const OUTLINE_REFRESH_DELAY_MS = 120
const APP_TITLE = 'CodeYun'
const OUTLINE_NUMBERING_KEY = 'codeyun.note-doc.outline-numbering.v1'

const route = useRoute()
const router = useRouter()
const noteStore = useNoteStore()

const currentNote = ref<NoteNode | null>(null)
const loading = ref(false)
const errorText = ref('')
const editorShellRef = ref<HTMLElement | null>(null)
const outlineItems = ref<DocOutlineItem[]>([])
const activeOutlineKey = ref('')
const outlineNumbering = ref(readLocalBoolean(OUTLINE_NUMBERING_KEY, false))
const showCopyDialog = ref(false)
const showAccessDialog = ref(false)
const aiCategorizing = ref(false)

let loadToken = 0
let outlineRefreshTimer: ReturnType<typeof setTimeout> | null = null
let initialOutlineRefreshTimers: ReturnType<typeof setTimeout>[] = []
let outlineMutationObserver: MutationObserver | null = null
let currentScrollElement: HTMLElement | null = null
let headingElementMap = new Map<string, HTMLElement>()

const noteId = computed(() => {
  const raw = Array.isArray(route.params.noteId) ? route.params.noteId[0] : route.params.noteId
  return String(raw || '').trim()
})

const readonly = computed(() => currentNote.value?.can_edit === false)
const canManageAccess = computed(() => {
  if (!currentNote.value) return false
  const explicit = currentNote.value.access?.capabilities.can_manage_access
  if (typeof explicit === 'boolean') return explicit
  return currentNote.value.can_edit !== false
})
const hasConnections = computed(() => (currentNote.value?.edge_count ?? 0) > 0)
const hasOutConnections = computed(() => (currentNote.value?.out_degree ?? 0) > 0)
const pageTitle = computed(() => `${currentNote.value?.title?.trim() || '文档'} - ${APP_TITLE}`)
const displayOutlineItems = computed(() => {
  if (!outlineNumbering.value || outlineItems.value.length === 0) return outlineItems.value
  const rootLevel = Math.min(...outlineItems.value.map((item) => item.level))
  const counters: number[] = []
  return outlineItems.value.map((item) => {
    const depth = Math.max(1, item.level - rootLevel + 1)
    for (let index = 0; index < depth - 1; index += 1) {
      if (!counters[index]) counters[index] = 1
    }
    counters[depth - 1] = (counters[depth - 1] || 0) + 1
    counters.length = depth
    return {
      ...item,
      number: counters.join('.'),
    }
  })
})

function readLocalBoolean(key: string, fallback: boolean) {
  if (typeof window === 'undefined' || typeof window.localStorage === 'undefined') return fallback
  try {
    const value = window.localStorage.getItem(key)
    if (value === '1') return true
    if (value === '0') return false
  } catch {
    // Ignore local preference read errors.
  }
  return fallback
}

function writeLocalBoolean(key: string, value: boolean) {
  if (typeof window === 'undefined' || typeof window.localStorage === 'undefined') return
  try {
    window.localStorage.setItem(key, value ? '1' : '0')
  } catch {
    // Ignore local preference write errors.
  }
}

const getDocRouteRef = (note: Pick<NoteNode, 'id' | 'numeric_id'>) => (
  note.numeric_id && note.numeric_id > 0 ? String(note.numeric_id) : noteKey(note.id)
)

const resolveDocHref = (note: Pick<NoteNode, 'id' | 'numeric_id'>) => (
  router.resolve(`/doc/${encodeURIComponent(getDocRouteRef(note))}`).href
)

const toDocApiPatch = (patch: NoteDocUpdatePayload): NoteDocUpdatePayload => {
  const outgoing = { ...patch }
  if (typeof outgoing.start_at === 'number' && outgoing.start_at > 10000000000) {
    outgoing.start_at /= 1000
  }
  return outgoing
}

async function loadNote(id: string, options: { force?: boolean } = {}) {
  const requestToken = ++loadToken

  if (!id) {
    currentNote.value = null
    errorText.value = '文档地址无效'
    return
  }

  loading.value = true
  errorText.value = ''
  if (options.force) {
    currentNote.value = null
  }

  try {
    const detail = await noteStore.fetchNoteDocDetail(id)
    if (requestToken !== loadToken || noteId.value !== id) return
    if (!detail) {
      currentNote.value = null
      errorText.value = '文档不存在或不可访问'
      return
    }

    currentNote.value = detail
    outlineItems.value = buildOutlineItemsFromHtml(detail.content || '')
    document.title = pageTitle.value
    const canonicalRouteRef = getDocRouteRef(detail)
    if (canonicalRouteRef && id !== canonicalRouteRef) {
      await router.replace(`/doc/${encodeURIComponent(canonicalRouteRef)}`)
      document.title = pageTitle.value
    }
    void nextTick().then(scheduleInitialOutlineRefresh)
  } catch (error) {
    console.error('Failed to load doc resource:', error)
    if (requestToken !== loadToken || noteId.value !== id) return
    currentNote.value = null
    errorText.value = '文档加载失败'
  } finally {
    if (requestToken === loadToken) {
      loading.value = false
    }
  }
}

function handleEditorModelUpdate(note: NoteNode) {
  currentNote.value = {
    ...(currentNote.value ?? note),
    ...note,
  }
  outlineItems.value = buildOutlineItemsFromHtml(note.content || '')
}

function handleEditorChange(note: NoteNode) {
  handleEditorModelUpdate(note)
  scheduleOutlineRefresh()
}

async function handleDocSave(note: NoteNode, patch: EditableNotePatch = {}) {
  const payload = (Object.keys(patch).length ? patch : note) as NoteDocUpdatePayload
  const updatedNote = await noteStore.updateNoteDocDetail(note.id, payload)
  if (!updatedNote) throw new Error('保存文档失败')
  currentNote.value = {
    ...(currentNote.value ?? updatedNote),
    ...updatedNote,
  }
  scheduleOutlineRefresh()
  return updatedNote
}

function handleDocSaveKeepalive(note: NoteNode, patch: EditableNotePatch = {}) {
  const payload = (Object.keys(patch).length ? patch : note) as NoteDocUpdatePayload
  putJsonKeepalive(`/api/note-docs/${encodeURIComponent(noteKey(note.id))}`, toDocApiPatch(payload))
}

function openGraph(mode: 'planetary' | 'satellite') {
  if (!currentNote.value) return
  const suffix = mode === 'satellite' ? '卫星图' : '行星图'
  noteStore.addTab({
    id: `planet-${noteKey(currentNote.value.id)}-${mode}`,
    label: `${(currentNote.value.title || 'Untitled').slice(0, 8)} - ${suffix}`,
    type: 'planet',
    data: { noteId: noteKey(currentNote.value.id), mode },
    closable: true,
  })
  void router.push('/notes/center')
}

async function categorizeCurrentNote() {
  if (!currentNote.value || aiCategorizing.value || readonly.value) return

  aiCategorizing.value = true
  try {
    const result = await noteStore.aiCategorizeNote(currentNote.value.id)
    if (!result) return
    currentNote.value = {
      ...currentNote.value,
      ...result.note,
    }
    ElMessage.success(result.summary || '已完成 AI 分类')
  } finally {
    aiCategorizing.value = false
  }
}

function openShareDialog() {
  if (!currentNote.value) return
  if (!canManageAccess.value) {
    ElMessage.warning('没有权限管理该文档')
    return
  }
  showAccessDialog.value = true
}

async function deleteCurrentNote() {
  if (!currentNote.value) return

  try {
    await ElMessageBox.confirm('确定要将这个文档移入回收站吗？', '删除文档', {
      confirmButtonText: '移入回收站',
      cancelButtonText: '取消',
      type: 'warning',
    })

    const noteId = currentNote.value.id
    const deleted = await noteStore.deleteNote(noteId)
    if (!deleted) return
    currentNote.value = null
    showCopyDialog.value = false
    showAccessDialog.value = false
    void router.push('/notes/center')
  } catch {
    // Ignore cancel
  }
}

function handleCopySuccess(newNote: NoteNode) {
  showCopyDialog.value = false
  void router.push(`/doc/${encodeURIComponent(getDocRouteRef(newNote))}`)
}

function handleAccessUpdate(access: NoteDocResourceAccess) {
  if (!currentNote.value) return
  currentNote.value = {
    ...currentNote.value,
    access,
    can_edit: access.capabilities.can_edit_content,
  }
}

function getEditorScrollElement() {
  return editorShellRef.value?.querySelector<HTMLElement>('.w-e-text-container .w-e-scroll')
    ?? editorShellRef.value?.querySelector<HTMLElement>('.shared-note-editor')
    ?? null
}

function bindScrollElement(nextElement: HTMLElement | null) {
  if (currentScrollElement === nextElement) return
  currentScrollElement?.removeEventListener('scroll', updateActiveOutline)
  currentScrollElement = nextElement
  currentScrollElement?.addEventListener('scroll', updateActiveOutline, { passive: true })
}

function bindOutlineMutationObserver() {
  outlineMutationObserver?.disconnect()
  outlineMutationObserver = null
  const editorElement = editorShellRef.value?.querySelector<HTMLElement>(
    '.w-e-text-container [data-slate-editor], .source-html-preview',
  )
  if (!editorElement) return
  outlineMutationObserver = new MutationObserver(() => scheduleOutlineRefresh())
  outlineMutationObserver.observe(editorElement, {
    childList: true,
    subtree: true,
    characterData: true,
  })
}

function buildOutlineItemsFromHtml(html: string): DocOutlineItem[] {
  if (typeof DOMParser === 'undefined') return []
  const documentFragment = new DOMParser().parseFromString(html || '', 'text/html')
  return Array.from(documentFragment.querySelectorAll<HTMLElement>('h1, h2, h3, h4'))
    .filter((element) => element.textContent?.trim())
    .map((element, index) => ({
      key: `heading-${index}`,
      text: element.textContent?.replace(/\s+/g, ' ').trim() || `标题 ${index + 1}`,
      level: Math.min(Math.max(Number(element.tagName.slice(1)) || 1, 1), 4),
    }))
}

function refreshOutline() {
  const headingElements = Array.from(
    editorShellRef.value?.querySelectorAll<HTMLElement>(
      '.w-e-text-container [data-slate-editor] h1, .w-e-text-container [data-slate-editor] h2, .w-e-text-container [data-slate-editor] h3, .w-e-text-container [data-slate-editor] h4, .source-html-preview h1, .source-html-preview h2, .source-html-preview h3, .source-html-preview h4',
    ) ?? [],
  ).filter((element) => element.textContent?.trim())

  headingElementMap = new Map()
  outlineItems.value = headingElements.map((element, index) => {
    const level = Math.min(Math.max(Number(element.tagName.slice(1)) || 1, 1), 4)
    const key = `heading-${index}`
    headingElementMap.set(key, element)
    return {
      key,
      text: element.textContent?.replace(/\s+/g, ' ').trim() || `标题 ${index + 1}`,
      level,
    }
  })

  if (!outlineItems.value.some((item) => item.key === activeOutlineKey.value)) {
    activeOutlineKey.value = outlineItems.value[0]?.key ?? ''
  }

  bindScrollElement(getEditorScrollElement())
  bindOutlineMutationObserver()
  updateActiveOutline()
}

function scheduleOutlineRefresh(delayMs = OUTLINE_REFRESH_DELAY_MS) {
  if (outlineRefreshTimer) {
    clearTimeout(outlineRefreshTimer)
    outlineRefreshTimer = null
  }
  outlineRefreshTimer = setTimeout(() => {
    refreshOutline()
  }, delayMs)
}

function scheduleInitialOutlineRefresh() {
  scheduleOutlineRefresh(0)
  initialOutlineRefreshTimers.forEach((timer) => clearTimeout(timer))
  initialOutlineRefreshTimers = [400, 1200].map((delayMs) => (
    setTimeout(() => scheduleOutlineRefresh(0), delayMs)
  ))
}

function updateActiveOutline() {
  if (!currentScrollElement || outlineItems.value.length === 0) return
  const containerTop = currentScrollElement.getBoundingClientRect().top
  let activeKey = outlineItems.value[0]?.key ?? ''

  for (const item of outlineItems.value) {
    const element = headingElementMap.get(item.key)
    if (!element) continue
    if (element.getBoundingClientRect().top - containerTop <= 72) {
      activeKey = item.key
    }
  }
  activeOutlineKey.value = activeKey
}

function jumpToOutlineItem(key: string) {
  const element = headingElementMap.get(key)
  const scrollElement = getEditorScrollElement()
  if (!element || !scrollElement) return
  const containerRect = scrollElement.getBoundingClientRect()
  const targetRect = element.getBoundingClientRect()
  scrollElement.scrollTo({
    top: scrollElement.scrollTop + targetRect.top - containerRect.top - 20,
    behavior: 'smooth',
  })
  activeOutlineKey.value = key
}

watch(noteId, (id) => {
  void loadNote(id)
}, { immediate: true })

watch(pageTitle, (title) => {
  document.title = title
}, { immediate: true })

watch(outlineNumbering, (enabled) => {
  writeLocalBoolean(OUTLINE_NUMBERING_KEY, enabled)
})

onBeforeUnmount(() => {
  if (outlineRefreshTimer) {
    clearTimeout(outlineRefreshTimer)
  }
  initialOutlineRefreshTimers.forEach((timer) => clearTimeout(timer))
  outlineMutationObserver?.disconnect()
  currentScrollElement?.removeEventListener('scroll', updateActiveOutline)
})
</script>

<style scoped>
.doc-resource-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: #f7f8fa;
  overflow: hidden;
}

.doc-main {
  flex: 1 1 auto;
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 18px;
  min-height: 0;
  padding: 18px 20px;
  box-sizing: border-box;
  overflow: hidden;
}

.doc-main.has-outline {
  grid-template-columns: minmax(0, 1fr) 228px;
}

.doc-editor-shell {
  display: flex;
  justify-content: center;
  min-width: 0;
  min-height: 0;
  height: 100%;
}

.doc-editor-frame {
  flex: 1 1 auto;
  display: flex;
  flex-direction: column;
  width: min(100%, 980px);
  height: 100%;
  min-height: 0;
  padding: 16px 20px;
  background: #fff;
  border: 1px solid #e5e7eb;
  box-shadow: 0 8px 24px rgb(15 23 42 / 6%);
  box-sizing: border-box;
}

.doc-editor-frame :deep(.shared-note-editor) {
  min-height: 0;
}

.doc-editor-frame :deep(.editor-header) {
  margin-bottom: 18px;
}

.doc-editor-frame :deep(.editor-container) {
  min-height: 0;
}

.doc-outline-panel {
  align-self: stretch;
  min-width: 0;
  padding: 8px 0;
  overflow: auto;
}

.outline-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 4px 8px 8px;
}

.outline-title {
  flex: 0 0 auto;
  color: #303133;
  font-size: 14px;
  font-weight: 700;
}

.outline-toolbar :deep(.el-checkbox) {
  height: 22px;
  margin-right: 0;
}

.doc-empty {
  grid-column: 1 / -1;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 0;
}

@media (max-width: 1180px) {
  .doc-main,
  .doc-main.has-outline {
    grid-template-columns: minmax(0, 1fr);
  }

  .doc-outline-panel {
    display: none;
  }
}

@media (max-width: 760px) {
  .doc-main {
    padding: 10px;
  }

  .doc-editor-frame {
    padding: 12px;
  }
}
</style>
