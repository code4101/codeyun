<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import axios from 'axios'
import { askReadingQuestion, type QaSource, type ReadingQuestion } from '@/api/libraryQa'
import { useReaderQaHistory } from './useReaderQaHistory'
import { useUserStore } from '@/store/userStore'
import { readerAssistantLogo } from './readerAssistantBrand'
import { renderReaderAnswer } from './readerAnswerMarkdown'

const props = withDefaults(defineProps<{
  resourceType: ReadingQuestion['resource_type']; resourceId: string; bookTitle: string
  chapterId?: string; chapterTitle?: string; page?: number; bookshelfId?: string
}>(), { chapterId: '', chapterTitle: '', page: 1, bookshelfId: '' })
const emit = defineEmits<{ navigate: [source: QaSource] }>()
const user = useUserStore()
const historyStore = useReaderQaHistory(() => ({ resource_type: props.resourceType, resource_id: props.resourceId }),
  () => user.isAuthenticated ? user.user?.id : undefined)
const { turns, question, loading: historyLoading, error: historyError } = historyStore
const busy = ref(false)
const error = ref('')
const messages = ref<HTMLElement | null>(null)
const input = ref<HTMLTextAreaElement | null>(null)
const suggestions = ['概括这一章', '解释关键概念', '这段话怎么理解？']
function suggest(text: string) { question.value = text; input.value?.focus() }
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
  void historyStore.save()
}
watch(() => [props.resourceType, props.resourceId, props.bookshelfId, user.user?.id, user.isAuthenticated], () => {
  cancel(); error.value = ''; retryQuestion = ''
}, { flush: 'sync' })
// 已完成的同书对话保留；旧位置的在途请求不能写入新位置。
watch(() => [props.chapterId, props.page], () => {
  if (busy.value) {
    cancel()
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
  if (!text || busy.value || historyLoading.value || historyError.value || !user.isAuthenticated || text.length > 2000) return
  const history = turns.value.slice(-12).map(({ role, content, location }) => ({ role, content: `[当时阅读位置：${location}]\n${content}`.slice(0, 6000) }))
  const context = location.value
  const request = ++generation
  const abort = new AbortController()
  controller = abort
  busy.value = true; error.value = ''; question.value = ''; retryQuestion = text
  // 在途问题作为输入草稿保留；只有完整问答写入服务器对话。
  question.value = text
  void scroll()
  try {
    const result = await askReadingQuestion({ resource_type: props.resourceType, resource_id: props.resourceId,
      chapter_id: props.chapterId, page: props.page, bookshelf_id: props.bookshelfId, question: text, history }, abort.signal)
    if (request !== generation) return
    turns.value.push({ role: 'user', content: text, location: context }, { role: 'assistant', content: result.answer, result, location: result.context.location })
    question.value = ''
    void historyStore.save()
    retryQuestion = ''
    void scroll()
  } catch (cause) {
    if (request !== generation || axios.isCancel(cause)) return
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
  <section class="reader-qa" aria-label="阅读助手">
    <header class="qa-header">
      <div class="qa-brand"><img :src="readerAssistantLogo" alt="" draggable="false" /><strong>阅读助手</strong></div>
      <button type="button" class="qa-new" title="清空已保存的对话" aria-label="清空已保存的对话" :disabled="historyLoading || !!historyError" @click="clear">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
      </button>
    </header>
    <div class="qa-context" :title="`${bookTitle} · ${location}`">{{ bookTitle }}<span> · {{ location }}</span></div>
    <div ref="messages" class="qa-messages" :class="{ 'is-empty': !turns.length }" aria-live="polite">
      <div v-if="!turns.length" class="qa-welcome">
        <div class="qa-welcome-mark"><img :src="readerAssistantLogo" alt="" draggable="false" /></div>
        <h3>想聊聊这本书？</h3>
        <div class="qa-suggestions"><button v-for="text in suggestions" :key="text" type="button" :disabled="!user.isAuthenticated" @click="suggest(text)">{{ text }}</button></div>
      </div>
      <article v-for="(turn, index) in turns" :key="index" :class="['qa-turn', turn.role]" :aria-label="turn.role === 'user' ? '你的问题' : '助手回答'">
        <div v-if="turn.role === 'assistant'" class="qa-text qa-markdown" v-html="renderReaderAnswer(turn.content)" />
        <div v-else class="qa-text">{{ turn.content }}</div>
        <template v-if="turn.result">
          <p v-for="warning in turn.result.context.warnings" :key="warning" class="qa-warning">{{ warning }}</p>
          <details v-if="turn.result.sources.length" class="qa-sources">
            <summary>参考来源 · {{ turn.result.sources.length }}</summary>
            <div class="qa-source-list"><span class="qa-source-context">{{ turn.location }}</span><button v-for="source in turn.result.sources" :key="source.number" type="button" @click="emit('navigate', source)">[{{ source.number }}] {{ source.title }}</button></div>
          </details>
          <details v-if="turn.result.web_sources?.length" class="qa-sources">
            <summary>网页来源 · {{ turn.result.web_sources.length }}</summary>
            <div class="qa-source-list"><a v-for="source in turn.result.web_sources" :key="source.url" :href="source.url" target="_blank" rel="noopener noreferrer">{{ source.title }}</a></div>
          </details>
        </template>
      </article>
      <article v-if="busy" class="qa-turn user" aria-label="你的问题"><div class="qa-text">{{ question }}</div></article>
      <div v-if="busy" class="qa-thinking" role="status" aria-label="正在回答"><i /><i /><i /></div>
    </div>
    <div class="qa-input">
      <div v-if="historyError" class="qa-warning" role="alert">{{ historyError }} <button type="button" @click="historyStore.retry()">重试</button> <button type="button" @click="historyStore.reload()">读取最新</button></div>
      <div v-if="error" class="qa-warning" role="alert">{{ error }} <button type="button" @click="send(true)">重试</button></div>
      <div class="qa-composer">
        <textarea ref="input" v-model="question" rows="2" maxlength="2000" aria-label="阅读问题" placeholder="问问这本书…"
          :disabled="busy || historyLoading || !user.isAuthenticated" @keydown="keydown" />
        <button type="button" class="qa-send" :disabled="busy || historyLoading || !!historyError || !question.trim() || !user.isAuthenticated" :aria-label="busy ? '正在回答' : '发送问题'" :title="busy ? '正在回答' : '发送问题'" @click="send()">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 19V5m-6 6 6-6 6 6" /></svg>
        </button>
      </div>
      <p class="qa-input-hint">{{ user.isAuthenticated ? 'Enter 发送' : '登录后可提问' }}</p>
    </div>
  </section>
</template>

<style scoped>
/* 对齐 docs/designs/reader-qa-concept-v2.png：单行上下文、轻量对话、折叠来源与一体化输入框。 */
.reader-qa { --qa-accent: #17646a; display: flex; flex: 1; flex-direction: column; min-height: 0; min-width: 0; background: var(--reader-surface); color: var(--reader-text); font-size: 13px; }
.qa-header { display: flex; align-items: center; justify-content: space-between; gap: 8px; min-height: 48px; padding: 0 14px; border-bottom: 1px solid var(--reader-border); flex: none; }
.qa-brand { display: flex; align-items: center; gap: 8px; min-width: 0; }
.qa-brand img { width: 26px; height: 26px; object-fit: contain; }
.qa-brand strong { font-size: 14px; font-weight: 600; }
button { color: inherit; font: inherit; cursor: pointer; }
button:disabled { opacity: .4; cursor: default; }
button:focus-visible, summary:focus-visible { outline: 2px solid var(--reader-link); outline-offset: 2px; }
.qa-new { display: grid; place-items: center; width: 28px; height: 28px; padding: 0; border: 1px solid var(--reader-border); border-radius: 8px; background: transparent; flex: none; }
.qa-new:hover { background: var(--reader-hover); }
svg { width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
.qa-context { flex: none; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; padding: 10px 14px; color: var(--reader-muted); border-bottom: 1px solid var(--reader-border); font-size: 11px; }
.qa-messages { flex: 1; min-height: 0; overflow: auto; padding: 20px 14px; overscroll-behavior: contain; }
.qa-messages.is-empty { display: flex; }
.qa-welcome { display: flex; flex: none; flex-direction: column; align-items: center; width: 100%; margin: auto; padding: 12px 0; }
.qa-welcome-mark { display: grid; place-items: center; width: 64px; height: 64px; border-radius: 50%; background: color-mix(in srgb, var(--qa-accent) 8%, var(--reader-surface)); }
.qa-welcome-mark img { width: 48px; height: 48px; object-fit: contain; }
.qa-welcome h3 { margin: 14px 0 18px; font-size: 16px; font-weight: 600; }
.qa-suggestions { display: flex; flex-direction: column; gap: 8px; width: min(100%, 190px); }
.qa-suggestions button { padding: 8px 12px; border: 1px solid var(--reader-border); border-radius: 22px; background: transparent; font-size: 12px; }
.qa-suggestions button:hover:enabled { border-color: var(--qa-accent); background: color-mix(in srgb, var(--qa-accent) 5%, var(--reader-surface)); }
.qa-turn { margin-bottom: 24px; }
.qa-turn.user { width: fit-content; max-width: 88%; box-sizing: border-box; margin-left: auto; padding: 10px 12px; background: color-mix(in srgb, var(--qa-accent) 12%, var(--reader-surface)); border-radius: 14px 14px 4px 14px; }
.qa-text { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.8; }
.qa-markdown { white-space: normal; }
.qa-markdown :deep(> :first-child) { margin-top: 0; }
.qa-markdown :deep(> :last-child) { margin-bottom: 0; }
.qa-markdown :deep(p) { margin: 0 0 10px; }
.qa-markdown :deep(h1), .qa-markdown :deep(h2), .qa-markdown :deep(h3), .qa-markdown :deep(h4) { font-size: 14px; line-height: 1.5; margin: 16px 0 8px; }
.qa-markdown :deep(ul), .qa-markdown :deep(ol) { margin: 8px 0; padding-left: 20px; }
.qa-markdown :deep(li + li) { margin-top: 4px; }
.qa-markdown :deep(blockquote) { margin: 10px 0; padding-left: 10px; border-left: 2px solid var(--reader-border); color: var(--reader-muted); }
.qa-markdown :deep(code) { padding: 1px 4px; background: var(--reader-code); color: var(--reader-code-text); border-radius: 3px; font-size: 12px; }
.qa-markdown :deep(pre) { max-width: 100%; overflow-x: auto; padding: 10px; border-radius: 6px; background: var(--reader-code); }
.qa-markdown :deep(pre code) { padding: 0; white-space: pre; }
.qa-markdown :deep(table) { display: block; max-width: 100%; overflow-x: auto; border-collapse: collapse; font-size: 12px; }
.qa-markdown :deep(th), .qa-markdown :deep(td) { border: 1px solid var(--reader-border); padding: 5px 8px; }
.qa-markdown :deep(a), .qa-source-list a { color: var(--reader-link); overflow-wrap: anywhere; }
.qa-markdown :deep(hr) { border: 0; border-top: 1px solid var(--reader-border); margin: 12px 0; }
.qa-sources { margin-top: 14px; border: 1px solid var(--reader-border); border-radius: 8px; color: var(--reader-muted); font-size: 11px; }
.qa-sources summary { padding: 9px 10px; cursor: pointer; }
.qa-source-list { display: flex; flex-direction: column; gap: 6px; padding: 0 10px 10px; }
.qa-source-context { font-size: 10px; }
.qa-source-list button { text-align: left; overflow-wrap: anywhere; border: 0; border-radius: 4px; padding: 5px 6px; background: var(--reader-panel); color: var(--reader-link); font-size: 11px; }
.qa-source-list button:hover { background: var(--reader-hover); }
.qa-warning { color: var(--el-color-warning, #b87818); font-size: 12px; margin: 8px 0; overflow-wrap: anywhere; line-height: 1.6; }
.qa-warning button { border: 0; background: transparent; color: var(--reader-link); text-decoration: underline; }
.qa-thinking { display: flex; gap: 5px; padding: 8px 0; }
.qa-thinking i { width: 5px; height: 5px; border-radius: 50%; background: var(--reader-muted); animation: qa-pulse 1.2s ease-in-out infinite; }
.qa-thinking i:nth-child(2) { animation-delay: .15s; }
.qa-thinking i:nth-child(3) { animation-delay: .3s; }
@keyframes qa-pulse { 0%, 80%, 100% { opacity: .3; } 40% { opacity: 1; } }
@media (prefers-reduced-motion: reduce) { .qa-thinking i { animation: none; } }
.qa-input { flex: none; padding: 0 12px 10px; }
.qa-composer { position: relative; border: 1px solid var(--reader-border); border-radius: 12px; background: var(--reader-content); transition: border-color .15s; }
.qa-composer:focus-within { border-color: var(--qa-accent); }
textarea { display: block; box-sizing: border-box; width: 100%; resize: none; min-height: 80px; border: 0; outline: none; border-radius: inherit; color: var(--reader-text); background: transparent; font: inherit; line-height: 1.6; padding: 12px 46px 12px 12px; }
textarea::placeholder { color: var(--reader-muted); }
.qa-send { position: absolute; right: 8px; bottom: 8px; display: grid; place-items: center; width: 30px; height: 30px; padding: 0; border: 0; border-radius: 50%; background: var(--qa-accent); color: #fff; }
.qa-send:hover:enabled { filter: brightness(1.12); }
.qa-input-hint { margin: 7px 3px 0; color: var(--reader-muted); font-size: 10px; }
</style>
