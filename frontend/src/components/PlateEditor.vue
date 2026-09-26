<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { readPlateContent, writePlateContent } from './rich-text/plateDocument'
/** Editor-only bridge. The caller owns identity, permissions, drafts and persistence. */
const props = defineProps<{ modelValue: string; readOnly?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: string]; change: [value: string] }>()
const frame = ref<HTMLIFrameElement>(), error = ref('')
const session = crypto.randomUUID(), channel = 'codeyun.plate'
const src = `/plugins/project-graph/plate.html?session=${session}`
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
  if (message.type === 'ready') { clearTimeout(timer); ready = true; load() }
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
  timer = setTimeout(() => { error.value = 'Plate 编辑器尚未就绪，请刷新重试' }, 45000)
})
onBeforeUnmount(() => { clearTimeout(timer); window.removeEventListener('message', onMessage) })
</script>
<template>
  <div class="plate-body-editor">
    <div v-if="error" role="alert" class="plate-error">{{ error }}</div>
    <iframe ref="frame" :src="src" title="Plate 正文编辑器" :class="{ blocked: !!error }" allow="clipboard-read; clipboard-write" />
  </div>
</template>
<style scoped>
.plate-body-editor{display:flex;flex:1;flex-direction:column;min-height:320px;min-width:0;height:100%;position:relative}
iframe{border:0;width:100%;flex:1;min-height:320px;background:white}.blocked{visibility:hidden}.plate-error{padding:16px;color:#b33}
</style>
