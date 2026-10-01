<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { WorkspaceMenuItem } from '@/components/editor-workspace/workspaceMenu'
import type { GraphStorage } from './storage'
import { graphFileName } from './fileName'
import type { GallerySnapshot, GalleryCommand, GalleryCanvasDrag } from './gallery'

/** Reusable host. Mount a fresh instance (key=documentId) when switching documents.
 * The caller supplies storage and owns navigation; the editor owns document semantics. */
const props = defineProps<{ documentId: string; title: string; storage: GraphStorage; detailsActive?: boolean; galleryActive?: boolean; sharedToolbar?: boolean; viewStateKey?: string }>()
const emit = defineEmits<{ gallery: [value: GallerySnapshot]; galleryDrag: [value: GalleryCanvasDrag]; galleryDrop: [value: { documentId: string; itemId: string }]; status: [state: string]; error: [message: string]; saved: []; focus: []; hints: [value: { keys: string[]; items: { displayKey: string; title: string }[]; page: string }]; mode: [value: { mode: string; readOnly: boolean; color: number[] }]; auxiliary: [value: { tabs: { id: string; title: string }[]; active: string }]; menu: [items: WorkspaceMenuItem[]]; command: [command: string]; details: [value: { id: string; title: string; value: unknown[] } | null] }>()
const frame = ref<HTMLIFrameElement>(), presented = ref(false)
const session = crypto.randomUUID()
const channel = 'codeyun.project-graph'
const frameUrl = computed(() => `/plugins/project-graph/embed.html?session=${session}`)
function hostTheme() {
  const style = getComputedStyle(frame.value ?? document.documentElement)
  const color = (name: string, fallback: string) => style.getPropertyValue(name).trim() || fallback
  return {
    background: color('--reader-content', color('--el-bg-color', '#ffffff')),
    panel: color('--reader-panel', color('--el-fill-color-light', '#f5f7fa')),
    text: color('--reader-text', color('--el-text-color-primary', '#303133')),
    border: color('--reader-border', color('--el-border-color-light', '#e4e7ed')),
    accent: color('--reader-active-text', color('--el-color-primary', '#409eff')),
    dark: !!frame.value?.closest('.is-reader-theme-dark, .dark'),
  }
}
let themeObserver: MutationObserver | undefined
function syncTheme() {
  frame.value?.contentWindow?.postMessage({ channel, version: 1, session, type: 'theme', payload: hostTheme() }, location.origin)
}
let revision = 0
let readOnly = false
let ready = false
let initializing = false
let booted = false
const flushes = new Map<string, { resolve: () => void; reject: (error: Error) => void; timer: ReturnType<typeof setTimeout> }>()
async function flush() {
  const deadline = Date.now() + 45000
  while (!booted && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 50))
  if (!booted) throw new Error('编辑器尚未就绪')
  return new Promise<void>((resolve, reject) => {
    const id = crypto.randomUUID()
    const timer = setTimeout(() => { flushes.delete(id); reject(new Error('保存超时，请重试或下载文件')) }, 30000)
    flushes.set(id, { resolve, reject, timer })
    frame.value?.contentWindow?.postMessage({ channel, version: 1, session, type: 'flush', id }, location.origin)
  })
}
async function changeGallery(command: GalleryCommand) {
  if (!booted) throw new Error('编辑器尚未就绪')
  // Vue selection arrays are reactive proxies, which postMessage cannot clone.
  const payload: GalleryCommand = JSON.parse(JSON.stringify(command))
  await new Promise<void>((resolve, reject) => {
    const id = crypto.randomUUID()
    const timer = setTimeout(() => { flushes.delete(id); reject(new Error('图库保存超时，请重试保存或下载文件')) }, 45000)
    flushes.set(id, { resolve, reject, timer })
    frame.value?.contentWindow?.postMessage({ channel, version: 1, session, type: 'gallery-command', id, payload }, location.origin)
  })
}
let timer: ReturnType<typeof setTimeout>
function send(type: string, payload?: unknown) {
  frame.value?.contentWindow?.postMessage({ channel, version: 1, session, type, payload }, location.origin)
}
function download(bytes: Uint8Array) {
  const url = URL.createObjectURL(new Blob([new Uint8Array(bytes)], { type: 'application/vnd.project-graph' }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = graphFileName(props.title).replace(/[\\/:*?"<>|]/g, '_')
  anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
async function onMessage(event: MessageEvent) {
  const message = event.data
  if (event.source !== frame.value?.contentWindow || event.origin !== location.origin
    || message?.channel !== channel || message.version !== 1 || message.session !== session) return
  const respond = (payload: unknown, error?: string) => frame.value?.contentWindow?.postMessage({
    channel, version: 1, session, type: 'response', id: message.id, payload, error,
  }, location.origin)
  try {
    if (message.type === 'canvas-focus') emit('focus')
    if (message.type === 'keyboard-hints') emit('hints', message.payload)
    if (message.type === 'canvas-mode') emit('mode', message.payload)
    if (message.type === 'ready' && !ready && !initializing) {
      initializing = true
      clearTimeout(timer)
      const document = await props.storage.read(props.documentId)
      revision = document?.revision ?? 0
      readOnly = document?.role === 'viewer'
      ready = true
      respond({ title: graphFileName(document?.title ?? props.title), bytes: document?.bytes.length ? document.bytes : null, readOnly,
        collaboration: document?.collaborative ? await props.storage.collaborationCredentials?.(props.documentId) : null,
        theme: hostTheme(), detailsActive: !!props.detailsActive, sharedToolbar: !!props.sharedToolbar, viewStateKey: props.viewStateKey })
    } else if (message.type === 'collaboration-credentials' && ready) {
      respond(await props.storage.collaborationCredentials?.(props.documentId))
    } else if (message.type === 'write' && ready) {
      if (readOnly) throw new Error('此文件为只读')
      if (!(message.payload?.bytes instanceof Uint8Array)) throw new Error('编辑器文档格式错误')
      const document = await props.storage.write(props.documentId, props.title, message.payload.bytes, revision)
      revision = document.revision
      respond({ revision })
      emit('saved')
    } else if (message.type === 'gallery-state') { emit('gallery', message.payload)
    } else if (message.type === 'gallery-drag' && frame.value) {
      const value = message.payload as GalleryCanvasDrag, rect = frame.value.getBoundingClientRect()
      if (['move', 'end', 'cancel'].includes(value.phase) && Number.isFinite(value.x) && Number.isFinite(value.y) && Array.isArray(value.ids))
        emit('galleryDrag', { ...value, x: rect.left + value.x * rect.width / frame.value.clientWidth, y: rect.top + value.y * rect.height / frame.value.clientHeight })
    } else if (message.type === 'gallery-drop') { emit('galleryDrop', message.payload)
    } else if (message.type === 'gallery-result') {
      const pending = flushes.get(message.id)
      if (pending) { clearTimeout(pending.timer); flushes.delete(message.id); message.payload.error ? pending.reject(new Error(message.payload.error)) : pending.resolve() }
    } else if (message.type === 'flushed') {
      const pending = flushes.get(message.payload.id)
      if (pending) { clearTimeout(pending.timer); flushes.delete(message.payload.id); message.payload.error ? pending.reject(new Error(message.payload.error)) : pending.resolve() }
    } else if (message.type === 'status') { if (!booted) syncTheme(); booted = true; emit('status', message.payload.state) }
    else if (message.type === 'presented') { presented.value = true; send('gallery-visible', { active: !!props.galleryActive }) }
    else if (message.type === 'aux-tabs') emit('auxiliary', message.payload)
    else if (message.type === 'menu-model' && Array.isArray(message.payload)) emit('menu', message.payload)
    else if (message.type === 'host-command' && ready && typeof message.payload?.command === 'string') emit('command', message.payload.command)
    else if (message.type === 'selection-details') emit('details', message.payload)
    else if (message.type === 'error') emit('error', message.payload.message)
    else if (message.type === 'exported' && message.payload?.bytes instanceof Uint8Array) download(message.payload.bytes)
  } catch (error) {
    const text = error instanceof Error ? error.message : String(error)
    respond(null, text)
    emit('error', text)
  }
}
function detectFocus() { queueMicrotask(() => { if (document.activeElement === frame.value) emit('focus') }) }
onMounted(() => {
  window.addEventListener('blur', detectFocus)
  window.addEventListener('message', onMessage)
  themeObserver = new MutationObserver(syncTheme)
  for (let element: HTMLElement | null = frame.value ?? null; element; element = element.parentElement) {
    themeObserver.observe(element, { attributes: true, attributeFilter: ['class', 'style', 'data-theme'] })
  }
  timer = setTimeout(() => emit('error', '编辑器尚未就绪，请确认插件资源已构建，或刷新重试。'), 45000)
})
onBeforeUnmount(() => {
  window.removeEventListener('blur', detectFocus)
  themeObserver?.disconnect()
  clearTimeout(timer); window.removeEventListener('message', onMessage)
  for (const pending of flushes.values()) { clearTimeout(pending.timer); pending.reject(new Error('编辑器已关闭')) }
  flushes.clear()
})
watch(() => props.galleryActive, active => send('gallery-visible', { active: !!active }))
watch(() => props.sharedToolbar, active => send('shared-toolbar', { active: !!active }))
watch(() => props.detailsActive, active => send('details-visible', { active: !!active }))
defineExpose({ changeGallery, requestMode: () => send('canvas-mode-request'), setMode: (mode: string, color?: number[]) => send('canvas-mode-set', { mode, color }), focusAuxiliary: (id: string) => send('aux-focus', { id }), closeAuxiliary: (id: string) => send('aux-close', { id }), refreshMenu: () => { if (booted) send('menu-request') }, executeMenu: (id: string) => { frame.value?.contentWindow?.focus(); send('menu-execute', { id }) }, updateDetails: (id: string, value: unknown[]) => send('details-change', { id, value }), flush, save: () => send('save'), exportDocument: () => send('export') })
</script>

<template>
  <iframe ref="frame" @focus="emit('focus')" :src="frameUrl" title="ProjectGraph 编辑器" class="project-graph-frame" :class="{ pending: !presented }" :aria-busy="!presented"
    allow="clipboard-read; clipboard-write" />
</template>

<style scoped>
.project-graph-frame { width: 100%; height: 100%; border: 0; display: block; background: var(--reader-content, var(--el-bg-color, #fff)); }
.project-graph-frame.pending { visibility: hidden; }
</style>
