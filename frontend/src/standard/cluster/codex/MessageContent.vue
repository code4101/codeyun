<script setup lang="ts">
import { computed, ref } from 'vue'
import DOMPurify from 'dompurify'
import { marked } from 'marked'
interface Attachment { name: string; path?: string; imageUrl?: string | null }
const props = defineProps<{ item: { type: string; text?: string; phase?: string; displayText?: string; attachments?: Attachment[]; content?: { type: string; text?: string }[] } }>()
const raw = computed(() => props.item.text || props.item.content?.filter(part => part.type === 'text').map(part => part.text).join('\n') || '')
// The exact desktop envelope is presentation metadata, not user-authored Markdown.
// Keep a fallback while an older backend is still running during hot reload.
const body = computed(() => props.item.displayText ?? raw.value.match(/^\s*# Files mentioned by the user:[\s\S]*?^## My request:\s*\n([\s\S]*)$/m)?.[1] ?? raw.value)
const html = computed(() => DOMPurify.sanitize(marked.parse(body.value, { async: false }) as string))
const user = computed(() => props.item.type === 'userMessage')
const lightbox = ref<HTMLDialogElement>(), preview = ref<Attachment>(), copied = ref(false)
function view(attachment: Attachment) { if (!attachment.imageUrl) return; preview.value = attachment; lightbox.value?.showModal() }
async function copy() { try { await navigator.clipboard.writeText(body.value); copied.value = true } catch { copied.value = false } }
</script>
<template>
  <article class="message" :class="{ user, assistant: !user, commentary: item.phase === 'commentary' }">
    <div v-if="item.attachments?.length" class="attachments">
      <button v-for="(attachment, index) in item.attachments" :key="index" class="attachment" :class="{ 'file-card': !attachment.imageUrl }" :title="attachment.name" :aria-label="`查看附件 ${attachment.name}`" :disabled="!attachment.imageUrl" @click="view(attachment)">
        <img v-if="attachment.imageUrl" :src="attachment.imageUrl" :alt="attachment.name" loading="lazy" /><span v-else>▧ {{ attachment.name }}</span>
      </button>
    </div>
    <div v-if="body" class="message-body"><div v-if="user" class="user-text">{{ body }}</div><div v-else class="prose" v-html="html" /></div>
    <div class="message-actions"><button v-if="body" :aria-label="copied ? '已复制消息' : '复制消息'" @click="copy">{{ copied ? '✓ 已复制' : '复制' }}</button><details v-if="user && raw !== body" class="raw-source"><summary>原始消息</summary><pre>{{ raw }}</pre></details></div>
    <dialog ref="lightbox" class="lightbox" @click="event => { if (event.target === event.currentTarget) lightbox?.close() }"><header><span>{{ preview?.name }}</span><button aria-label="关闭图片预览" @click="lightbox?.close()">×</button></header><img v-if="preview?.imageUrl" :src="preview.imageUrl" :alt="preview.name" /></dialog>
  </article>
</template>
<style scoped>
.message { margin:20px 0 28px; min-width:0; font-size:14px; line-height:1.65; overflow-wrap:anywhere; }
.user { display:flex; flex-direction:column; align-items:flex-end; margin:28px 0 34px; }.user .message-body { max-width:85%; background:#1e4479; color:#f1f5fc; padding:11px 16px; border-radius:17px 17px 5px 17px; }.user-text { white-space:pre-wrap; }.assistant .message-body { color:#e0e0e0; }.commentary { color:#b1b1b1; font-size:13px; }.commentary .message-body { color:inherit; }
.attachments { display:flex; flex-wrap:wrap; justify-content:flex-end; gap:8px; margin-bottom:9px; max-width:100%; }.attachment { width:90px; height:90px; padding:0; border:1px solid #383838; border-radius:12px; overflow:hidden; background:#222; cursor:zoom-in; }.attachment img { width:100%; height:100%; object-fit:cover; }.file-card { width:auto; height:auto; max-width:240px; padding:10px 12px; color:#aaa; font-size:12px; overflow-wrap:anywhere; cursor:default; }
.message-actions { display:flex; align-items:start; gap:10px; margin-top:7px; color:#888; font-size:11px; min-height:16px; }.message-actions>button { border:0; background:none; padding:0; color:inherit; cursor:pointer; opacity:0; }.message:hover .message-actions>button,.message:focus-within .message-actions>button { opacity:1; }.raw-source { max-width:100%; }.raw-source summary { cursor:pointer; opacity:.65; }.raw-source pre { max-width:560px; max-height:240px; overflow:auto; white-space:pre-wrap; padding:12px; background:#222; font-size:11px; border-radius:8px; }
.prose :deep(p) { margin:0 0 12px; }.prose :deep(p:last-child) { margin-bottom:0; }.prose :deep(h1) { font-size:21px; }.prose :deep(h2) { font-size:18px; }.prose :deep(h3),.prose :deep(h4) { font-size:15px; }.prose :deep(h1),.prose :deep(h2),.prose :deep(h3),.prose :deep(h4) { line-height:1.4; margin:22px 0 10px; font-weight:600; }.prose :deep(:first-child) { margin-top:0; }.prose :deep(ul),.prose :deep(ol) { padding-left:24px; margin:10px 0 14px; }.prose :deep(li) { margin:5px 0; }.prose :deep(pre) { background:#232323; border:1px solid #303030; border-radius:10px; padding:14px 16px; overflow:auto; font-size:12px; line-height:1.6; }.prose :deep(code) { font-family:Consolas,monospace; font-size:.9em; background:#292929; padding:2px 5px; border-radius:4px; }.prose :deep(pre code) { background:none; padding:0; }.prose :deep(a) { color:#8ab4eb; text-underline-offset:3px; }.prose :deep(blockquote) { margin:12px 0; border-left:3px solid #444; padding-left:16px; color:#aaa; }.prose :deep(table) { display:block; overflow:auto; border-collapse:collapse; font-size:13px; margin:14px 0; }.prose :deep(th),.prose :deep(td) { padding:8px 12px; border:1px solid #363636; }.prose :deep(th) { background:#242424; }.prose :deep(img) { max-width:100%; max-height:420px; border-radius:10px; object-fit:contain; }.prose :deep(hr) { border:0; border-top:1px solid #333; margin:20px 0; }
.lightbox { padding:16px; background:#202020; color:#ddd; border:1px solid #444; border-radius:14px; max-width:90vw; }.lightbox::backdrop { background:#000b; }.lightbox header { display:flex; align-items:center; justify-content:space-between; gap:20px; margin-bottom:14px; font-size:13px; }.lightbox header button { border:0; background:transparent; color:inherit; font-size:24px; cursor:pointer; }.lightbox>img { display:block; max-width:85vw; max-height:75vh; object-fit:contain; }
@media(max-width:760px) { .user .message-body { max-width:92%; }.raw-source pre { max-width:75vw; }.message-actions>button { opacity:1; } }
</style>
