<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import axios from 'axios'
import { askReadingQuestion, type QaAnswer, type QaMessage, type QaSource, type ReadingQuestion } from '@/api/libraryQa'
import { useUserStore } from '@/store/userStore'
import { readerAssistantLogo } from './readerAssistantBrand'

const props = withDefaults(defineProps<{
  resourceType: ReadingQuestion['resource_type']; resourceId: string; bookTitle: string
  chapterId?: string; chapterTitle?: string; page?: number; bookshelfId?: string
}>(), { chapterId: '', chapterTitle: '', page: 1, bookshelfId: '' })
const emit = defineEmits<{ navigate: [source: QaSource] }>()
const user = useUserStore()
type Turn = QaMessage & { result?: QaAnswer; location: string }
const turns = ref<Turn[]>([])
const question = ref('')
const busy = ref(false)
const error = ref('')
const messages = ref<HTMLElement | null>(null)
const location = computed(() => props.resourceType === 'pdf' ? `第 ${props.page} 页` : props.chapterTitle || '当前章节')
let controller: AbortController | null = null
let generation = 0
let retryQuestion = ''
function cancel() {
  generation++
  controller?.abort()
  controller = null
  busy.value = false
}
function clear() {
  cancel(); turns.value = []; error.value = ''; retryQuestion = ''; question.value = ''
}
watch(() => [props.resourceType, props.resourceId, props.bookshelfId, user.user?.id, user.isAuthenticated], clear, { flush: 'sync' })
// 已完成的同书对话保留；旧位置的在途请求不能写入新位置。
watch(() => [props.chapterId, props.page], () => {
  if (busy.value) {
    cancel()
    const pending = turns.value[turns.value.length - 1]
    if (pending?.role === 'user') { question.value = pending.content; turns.value.pop() }
  }
  error.value = ''; retryQuestion = ''
}, { flush: 'sync' })
onBeforeUnmount(cancel)
async function scroll() {
  await nextTick()
  messages.value?.scrollTo({ top: messages.value.scrollHeight, behavior: 'smooth' })
}
async function send(retry = false) {
  const text = (retry ? retryQuestion : question.value).trim()
  if (!text || busy.value || !user.isAuthenticated || text.length > 2000) return
  const history = turns.value.slice(-12).map(({ role, content, location }) => ({ role, content: `[当时阅读位置：${location}]\n${content}`.slice(0, 6000) }))
  const context = location.value
  const request = ++generation
  const abort = new AbortController()
  controller = abort
  busy.value = true; error.value = ''; question.value = ''; retryQuestion = text
  turns.value.push({ role: 'user', content: text, location: context })
  void scroll()
  try {
    const result = await askReadingQuestion({ resource_type: props.resourceType, resource_id: props.resourceId,
      chapter_id: props.chapterId, page: props.page, bookshelf_id: props.bookshelfId, question: text, history }, abort.signal)
    if (request !== generation) return
    turns.value.push({ role: 'assistant', content: result.answer, result, location: result.context.location })
    retryQuestion = ''
    void scroll()
  } catch (cause) {
    if (request !== generation || axios.isCancel(cause)) return
    turns.value.pop()
    question.value = text
    error.value = axios.isAxiosError(cause) && typeof cause.response?.data?.detail === 'string'
      ? cause.response.data.detail : '问答请求失败，请重试'
  } finally {
    if (request === generation) { busy.value = false; controller = null }
  }
}
function keydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); void send() }
}
</script>

<template>
  <section class="reader-qa">
    <header><div class="qa-brand"><img :src="readerAssistantLogo" alt="" draggable="false" /><div><strong>阅读助手</strong><span>{{ bookTitle }}</span></div></div><button type="button" @click="clear">清空对话</button></header>
    <div class="qa-location">{{ location }} · 自动结合书籍上下文</div>
    <div ref="messages" class="qa-messages" aria-live="polite">
      <div v-if="!turns.length" class="qa-welcome"><img :src="readerAssistantLogo" alt="阅读助手" draggable="false" /><strong>一起读懂这本书</strong><p class="qa-hint">可以提问“这章讲了什么？”或询问书中的概念。回答会显示参考来源。</p></div>
      <article v-for="(turn, index) in turns" :key="index" :class="['qa-turn', turn.role]">
        <div class="qa-turn-heading"><img v-if="turn.role === 'assistant'" :src="readerAssistantLogo" alt="" draggable="false" /><small>{{ turn.role === 'user' ? '你' : '阅读助手' }} · {{ turn.location }}</small></div>
        <div class="qa-text">{{ turn.content }}</div>
        <template v-if="turn.result">
          <p v-for="warning in turn.result.context.warnings" :key="warning" class="qa-warning">{{ warning }}</p>
          <div class="qa-sources"><button v-for="source in turn.result.sources" :key="source.number" type="button"
            @click="emit('navigate', source)">[{{ source.number }}] {{ source.title }}</button></div>
          <small>{{ turn.result.model }}</small>
        </template>
      </article>
      <div v-if="busy" class="qa-thinking" role="status"><img :src="readerAssistantLogo" alt="" draggable="false" /><p class="qa-hint">正在结合书籍内容回答…</p></div>
    </div>
    <div class="qa-input">
      <p v-if="!user.isAuthenticated" class="qa-hint">登录后可使用阅读问答。</p>
      <div v-if="error" class="qa-warning" role="alert">{{ error }} <button type="button" @click="send(true)">重试</button></div>
      <textarea v-model="question" rows="3" maxlength="2000" aria-label="阅读问题" placeholder="输入问题，Enter 发送，Shift+Enter 换行"
        :disabled="busy || !user.isAuthenticated" @keydown="keydown" />
      <button type="button" class="qa-send" :disabled="busy || !question.trim() || !user.isAuthenticated" @click="send()">{{ busy ? '回答中…' : '发送' }}</button>
    </div>
  </section>
</template>

<style scoped>
.reader-qa { display: flex; flex: 1; flex-direction: column; min-height: 0; min-width: 0; background: var(--reader-panel); color: var(--reader-text); font-size: 13px; }
header { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 12px 12px 4px; }
header strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.qa-brand { display: flex; align-items: center; gap: 8px; min-width: 0; }
.qa-brand > img { width: 32px; height: 32px; object-fit: contain; flex: none; }
.qa-brand > div { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.qa-brand span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 11px; color: var(--reader-muted); }
header > button { flex: none; font-size: 11px; }
.qa-welcome { display: flex; flex-direction: column; align-items: center; padding: 24px 0 12px; text-align: center; }
.qa-welcome > img { width: 80px; height: 80px; object-fit: contain; margin-bottom: 12px; }
.qa-welcome > strong { font-size: 14px; }
.qa-welcome p { line-height: 1.8; max-width: 260px; }
.qa-turn-heading, .qa-thinking { display: flex; align-items: center; gap: 6px; }
.qa-turn-heading img, .qa-thinking img { width: 24px; height: 24px; object-fit: contain; flex: none; }
button { cursor: pointer; color: var(--reader-text); border: 1px solid var(--reader-border); background: var(--reader-surface); border-radius: 4px; padding: 4px 8px; }
button:disabled { opacity: .5; cursor: default; }
.qa-location, .qa-hint, small { color: var(--reader-muted); font-size: 12px; }
.qa-location { padding: 4px 12px 10px; border-bottom: 1px solid var(--reader-border); }
.qa-messages { flex: 1; min-height: 0; overflow: auto; padding: 12px; }
.qa-turn { margin-bottom: 16px; }
.qa-turn.user { padding: 8px; background: var(--reader-surface); border-radius: 4px; }
.qa-text { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.7; margin-top: 4px; }
.qa-sources { display: flex; flex-wrap: wrap; gap: 4px; margin: 8px 0; }
.qa-sources button { font-size: 12px; text-align: left; }
.qa-warning { color: var(--el-color-warning); font-size: 12px; margin: 8px 0; }
.qa-input { padding: 10px 12px; border-top: 1px solid var(--reader-border); }
textarea { box-sizing: border-box; width: 100%; resize: vertical; min-height: 64px; max-height: 180px; border: 1px solid var(--reader-border); border-radius: 4px; color: var(--reader-text); background: var(--reader-content); font: inherit; padding: 8px; }
.qa-send { margin-top: 6px; float: right; }
</style>
