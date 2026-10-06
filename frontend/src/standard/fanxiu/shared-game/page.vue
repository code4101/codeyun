<template>
  <main class="shared-game">
    <header>
      <h2>凡修游戏共享</h2>
      <p>{{ message }}</p>
      <label v-if="permission.control">
        <input v-model="interactive" type="checkbox" :disabled="permission.busy" /> 手动操作（点击、拖拽）
      </label>
      <p v-if="permission.busy" class="hint">自动任务正在运行，当前可观看。</p>
    </header>
    <div v-if="src" class="screen">
      <img :src="src" alt="当前游戏画面" draggable="false"
           @pointerdown="startInput" @pointerup="finishInput" @pointercancel="start = null" />
    </div>
    <p v-else>登录后，请在微信群报 CodeYun 用户名，由群主授权加入白名单。</p>
  </main>
</template>

<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import api from '@/api'

const src = ref('')
const message = ref('正在连接游戏')
const permission = ref({ control: false, busy: true })
const interactive = ref(false)
let timer: number | undefined
let stopped = false
let sending = false
let start: { x: number; y: number; width: number; height: number } | null = null

async function refresh() {
  try {
    const status = (await api.get('/fanxiu/shared-game/status')).data
    permission.value = status
    message.value = status.message
    if (permission.value.busy) interactive.value = false
    const frame = await api.get('/fanxiu/shared-game/frame', { responseType: 'blob' })
    if (stopped) return
    const next = URL.createObjectURL(frame.data)
    if (src.value) URL.revokeObjectURL(src.value)
    src.value = next
  } catch (error: any) {
    message.value = error.response?.data?.detail || '画面连接暂时中断，正在重试'
  } finally {
    if (!stopped) timer = window.setTimeout(refresh, 1500)
  }
}

function point(event: PointerEvent) {
  const img = event.currentTarget as HTMLImageElement
  const rect = img.getBoundingClientRect()
  return { x: Math.min(.999999, Math.max(0, (event.clientX - rect.left) / rect.width)),
           y: Math.min(.999999, Math.max(0, (event.clientY - rect.top) / rect.height)),
           width: img.naturalWidth, height: img.naturalHeight }
}

function startInput(event: PointerEvent) {
  if (!interactive.value || permission.value.busy || sending) return
  start = point(event)
  ;(event.currentTarget as HTMLImageElement).setPointerCapture(event.pointerId)
}

async function finishInput(event: PointerEvent) {
  if (!start || sending) return
  const end = point(event)
  const payload: Record<string, number | string> = { ...start, request_id: crypto.randomUUID() }
  if (Math.hypot((end.x - start.x) * start.width, (end.y - start.y) * start.height) > 12) {
    payload.end_x = end.x
    payload.end_y = end.y
  }
  start = null
  sending = true
  try {
    await api.post('/fanxiu/shared-game/input', payload, { timeout: 0 })
    message.value = '操作已执行'
  } catch (error: any) {
    message.value = error.response?.data?.detail || '操作结果不明，请查看画面，勿重复点击'
  } finally {
    sending = false
  }
}

onMounted(refresh)
onBeforeUnmount(() => {
  stopped = true
  window.clearTimeout(timer)
  if (src.value) URL.revokeObjectURL(src.value)
})
</script>

<style scoped>
.shared-game { max-width: 900px; margin: auto; padding: 16px; }
header { margin-bottom: 12px; }
h2 { margin: 0 0 8px; }
p { margin: 8px 0; }
.hint { color: #946000; }
.screen { display: flex; justify-content: center; background: #102034; border-radius: 12px; overflow: hidden; }
.screen img { display: block; max-width: 100%; max-height: 80vh; object-fit: contain; touch-action: none; user-select: none; }
</style>
