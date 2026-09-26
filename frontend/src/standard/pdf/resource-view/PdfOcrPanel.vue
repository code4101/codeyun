<template>
  <div class="ocr-panel">
    <header class="ocr-header">
      <span class="ocr-title">第 {{ page }} 页 · OCR</span>
      <button
        class="copy-btn"
        type="button"
        :disabled="!hasText"
        @click="copyText"
      >
        {{ copied ? '已复制' : '复制文字' }}
      </button>
    </header>

    <div class="ocr-scroll">
      <div class="paper">
        <p v-if="status === 'loading'" class="state state--loading">
          首次识别中，完成后会自动保存
        </p>

        <div v-else-if="status === 'error'" class="state state--error">
          <p class="state-text">本页识别失败</p>
          <button class="retry-btn" type="button" @click="retry">重试</button>
        </div>

        <p v-else-if="status === 'ready' && !hasText" class="state state--blank">
          本页未识别到文字
        </p>

        <div v-else-if="status === 'ready'" class="text">
          <component :is="block.kind === 'heading' ? 'h2' : 'p'"
            v-for="block in displayBlocks" :key="block.id" class="text-block"
            :class="`text-block--${block.kind}`"
            :style="{ fontSize: `${block.font_scale}em`, textAlign: block.align,
              textIndent: `${block.indent_em}em`, marginTop: `${block.space_before_em}em` }">
            {{ block.text }}
          </component>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import { getPdfPageOcr, type PdfPageOcr, type PdfOcrBlock } from '@/api/pdfDocuments'

type OcrResult = PdfPageOcr

const props = defineProps<{
  pdfId: number
  page: number
  active: boolean
  revision: string
}>()

type Status = 'idle' | 'loading' | 'ready' | 'error'

const CACHE_LIMIT = 20

const status = ref<Status>('idle')
const result = ref<OcrResult | null>(null)
const copied = ref(false)

const cache = new Map<string, OcrResult>()
let generation = 0
let controller: AbortController | null = null
let copyTimer: ReturnType<typeof setTimeout> | null = null

function cacheKey(): string {
  return `${props.pdfId}/${props.revision}/${props.page}`
}

const hasText = computed<boolean>(() => {
  const r = result.value
  if (!r) return false
  if (r.text && r.text.trim()) return true
  return r.lines.some((line) => line.text && line.text.trim())
})

const displayBlocks = computed<PdfOcrBlock[]>(() => {
  const r = result.value
  if (!r) return []
  if (r.layout) return r.layout.blocks
  return r.lines.map(line => ({ id: line.line_id, text: line.text, kind: 'paragraph',
    font_scale: 1, font_size_estimate_pt: 0, align: 'justify', indent_em: 0,
    space_before_em: .65, line_ids: [line.line_id], box: [] }))
})

function cacheSet(key: string, value: OcrResult): void {
  if (cache.has(key)) cache.delete(key)
  cache.set(key, value)
  while (cache.size > CACHE_LIMIT) {
    const oldest = cache.keys().next().value
    if (oldest === undefined) break
    cache.delete(oldest)
  }
}

function cancel(): void {
  generation += 1
  if (controller) {
    controller.abort()
    controller = null
  }
}

async function load(): Promise<void> {
  if (!props.active || !props.pdfId || !props.page) {
    cancel()
    status.value = 'idle'
    result.value = null
    return
  }

  const key = cacheKey()
  const cached = cache.get(key)
  if (cached) {
    cancel()
    result.value = cached
    status.value = 'ready'
    return
  }

  cancel()
  const gen = generation
  const ctrl = new AbortController()
  controller = ctrl

  status.value = 'loading'
  result.value = null

  try {
    const data = await getPdfPageOcr(props.pdfId, props.page, ctrl.signal)
    if (gen !== generation) return
    cacheSet(key, data)
    result.value = data
    status.value = 'ready'
  } catch (err) {
    if (gen !== generation) return
    if (ctrl.signal.aborted) return
    status.value = 'error'
  } finally {
    if (controller === ctrl) controller = null
  }
}

function retry(): void {
  cache.delete(cacheKey())
  void load()
}

async function copyText(): Promise<void> {
  const r = result.value
  if (!r || !hasText.value) return
  const text = r.layout?.text || r.text || displayBlocks.value.map(block => block.text).join('\n\n')

  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text)
    } else {
      const area = document.createElement('textarea')
      area.value = text
      area.style.position = 'fixed'
      area.style.opacity = '0'
      document.body.appendChild(area)
      area.select()
      document.execCommand('copy')
      document.body.removeChild(area)
    }
    copied.value = true
    if (copyTimer) clearTimeout(copyTimer)
    copyTimer = setTimeout(() => {
      copied.value = false
    }, 1500)
  } catch {
    copied.value = false
  }
}

watch(
  () => [props.active, props.pdfId, props.page, props.revision] as const,
  () => {
    void load()
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  cancel()
  if (copyTimer) clearTimeout(copyTimer)
  copyTimer = null
})
</script>

<style scoped>
.ocr-panel {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  background: #f3efe6;
}

.ocr-header {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 20px;
  border-bottom: 1px solid #e2dbcc;
  background: #faf7f0;
}

.ocr-title {
  font-size: 14px;
  font-weight: 600;
  letter-spacing: 0.02em;
  color: #4a4238;
}

.copy-btn,
.retry-btn {
  font: inherit;
  font-size: 13px;
  line-height: 1;
  padding: 7px 14px;
  border-radius: 6px;
  border: 1px solid #d8cfbd;
  background: #fffdf8;
  color: #4a4238;
  cursor: pointer;
  transition: background 0.15s ease, border-color 0.15s ease;
}

.copy-btn:hover:not(:disabled),
.retry-btn:hover {
  background: #f1e9da;
  border-color: #c9bda5;
}

.copy-btn:disabled {
  opacity: 0.45;
  cursor: default;
}

.ocr-scroll {
  flex: 1 1 auto;
  height: 100%;
  min-height: 0;
  overflow: auto;
  -webkit-overflow-scrolling: touch;
}

.paper {
  box-sizing: border-box;
  max-width: 780px;
  margin: 24px auto;
  padding: 48px 56px;
  background: #fffdf9;
  border: 1px solid #e8e0d0;
  border-radius: 4px;
  box-shadow: 0 1px 3px rgba(74, 66, 56, 0.08), 0 8px 24px rgba(74, 66, 56, 0.06);
  color: #2f2a24;
  font-family: "Songti SC", "STSong", "SimSun", "Noto Serif SC", "Source Han Serif SC", "Songti TC", serif;
  font-size: 18px;
  line-height: 1.9;
}

.text {
  margin: 0;
}

.text-block {
  margin: 0;
  white-space: normal;
  overflow-wrap: break-word;
  word-break: break-word;
  text-align: justify;
}

.text-block--heading { font-weight: 600; line-height: 1.5; margin-bottom: 1em; }
.text-block--marginal { color: #8a8176; line-height: 1.5; }

.state {
  margin: 0;
  text-align: center;
  color: #7a7060;
  font-size: 16px;
}

.state--error {
  padding: 8px 0;
}

.state-text {
  margin: 0 0 16px;
}

.retry-btn {
  display: inline-block;
}

@media (max-width: 640px) {
  .paper {
    margin: 16px 12px;
    padding: 28px 22px;
    font-size: 17px;
  }
}
</style>
