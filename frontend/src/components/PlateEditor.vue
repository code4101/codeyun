<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { readPlateContent, writePlateContent } from './rich-text/plateDocument'
/** Editor-only bridge. The caller owns identity, permissions, drafts and persistence. */
const props = defineProps<{ modelValue: string; readOnly?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: string]; change: [value: string] }>()
const frame = ref<HTMLIFrameElement>(), error = ref(''), presented = ref(false)
const session = crypto.randomUUID(), channel = 'codeyun.plate'
const src = `/plugins/project-graph/plate.html?session=${session}`
const flushes = new Map<string, { resolve: () => void; reject: (error: Error) => void; timer: ReturnType<typeof setTimeout> }>()
let themeObserver: MutationObserver | undefined
function syncTheme() {
  const style = getComputedStyle(frame.value ?? document.documentElement)
  const read = (name: string, fallback: string) => style.getPropertyValue(name).trim() || fallback
  frame.value?.contentWindow?.postMessage({ channel, version: 1, session, type: 'theme', payload: {
    background: read('--reader-content', read('--el-bg-color', '#fff')), panel: read('--reader-panel', read('--el-fill-color-light', '#f5f7fa')),
    text: read('--reader-text', read('--el-text-color-primary', '#303133')), border: read('--reader-border', read('--el-border-color', '#dcdfe6')), dark: !!frame.value?.closest('.is-reader-theme-dark, .dark')
  } }, location.origin)
}
async function flush() {
  if (!ready) return
  return new Promise<void>((resolve, reject) => {
    const id = crypto.randomUUID()
    const timer = setTimeout(() => { flushes.delete(id); reject(new Error('正文保存确认超时')) }, 10000)
    flushes.set(id, { resolve, reject, timer })
    frame.value?.contentWindow?.postMessage({ channel, version: 1, session, type: 'flush', id }, location.origin)
  })
}
defineExpose({ flush })
let ready = false, lastContent = '', timer: ReturnType<typeof setTimeout>
function load() {
  if (!ready) return
  try {
    const value = readPlateContent(props.modelValue)
    lastContent = props.modelValue
    frame.value?.contentWindow?.postMessage({ channel, version: 1, session, type: 'load', payload: { value, readOnly: !!props.readOnly } }, location.origin)
    error.value = ''
  } catch (reason) { error.value = String(reason) }
}
function onMessage(event: MessageEvent) {
  const message = event.data
  if (event.source !== frame.value?.contentWindow || event.origin !== location.origin || message?.channel !== channel || message.version !== 1 || message.session !== session) return
  if (message.type === 'ready') { clearTimeout(timer); ready = true; syncTheme(); load() }
  else if (message.type === 'presented') presented.value = true
  else if (message.type === 'flushed') {
    const pending = flushes.get(message.id)
    if (pending) { clearTimeout(pending.timer); flushes.delete(message.id); pending.resolve() }
  }
  else if (message.type === 'error') error.value = String(message.payload?.message)
  else if (message.type === 'change' && !props.readOnly && !error.value) {
    if (!Array.isArray(message.payload?.value)) return
    lastContent = writePlateContent(message.payload.value)
    emit('update:modelValue', lastContent)
    emit('change', lastContent)
  }
}
watch(() => props.modelValue, value => { if (value !== lastContent) load() })
watch(() => props.readOnly, load)
onMounted(() => {
  window.addEventListener('message', onMessage)
  themeObserver = new MutationObserver(syncTheme)
  for (let element: HTMLElement | null = frame.value ?? null; element; element = element.parentElement) themeObserver.observe(element, { attributes: true, attributeFilter: ['class', 'style', 'data-theme'] })
  timer = setTimeout(() => { error.value = 'Plate 编辑器尚未就绪，请刷新重试' }, 45000)
})
onBeforeUnmount(() => { themeObserver?.disconnect(); for (const pending of flushes.values()) { clearTimeout(pending.timer); pending.reject(new Error('正文编辑器已关闭')) } flushes.clear(); clearTimeout(timer); window.removeEventListener('message', onMessage) })
</script>
<template>
  <div class="plate-body-editor">
    <div v-if="error" role="alert" class="plate-error">{{ error }}</div>
    <iframe ref="frame" :src="src" title="Plate 正文编辑器" :class="{ blocked: !!error || !presented }" :aria-busy="!presented" allow="clipboard-read; clipboard-write" />
  </div>
</template>
<style scoped>
.plate-body-editor{display:flex;flex:1;flex-direction:column;min-height:320px;min-width:0;height:100%;position:relative;background:var(--reader-content,var(--el-bg-color,#fff))}
iframe{border:0;width:100%;flex:1;min-height:320px;background:transparent}.blocked{visibility:hidden}.plate-error{padding:16px;color:#b33}
</style>
