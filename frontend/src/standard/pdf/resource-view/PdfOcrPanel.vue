<template>
  <div class="ocr-panel">
    <header class="ocr-header">
      <PdfBookOcrControl :pdf-id="pdfId" :revision="revision" :active="active" :can-control="canControl" />
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

        <div v-else-if="status === 'ready' && result" class="reading-text">
          <template v-for="block in readingBlocks" :key="block.id">
            <div v-if="block.kind === 'marginal'" class="marginal-text">
              <span v-for="line in block.lines" :key="line.id">{{ spatialRunText(line.runs) }}</span>
            </div>
            <component v-else :is="block.kind === 'heading' ? 'h2' : 'p'"
              class="reading-block" :class="{ 'reading-heading': block.kind === 'heading' }"
              :style="{ textIndent: `${block.indent}em`, fontSize: `${block.scale}em`,
                textAlign: block.kind === 'heading' ? block.align : 'justify' }">{{ block.text }}</component>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import { getPdfPageOcr, type PdfPageOcr } from '@/api/pdfDocuments'
import PdfBookOcrControl from './PdfBookOcrControl.vue'
import { ocrTextBlocks } from './ocrTextLayer'
import { spatialRunText } from './ocrTypography'

type OcrResult = PdfPageOcr

const props = defineProps<{
  pdfId: number
  page: number
  active: boolean
  revision: string
  canControl: boolean
}>()

type Status = 'idle' | 'loading' | 'ready' | 'error'

const CACHE_LIMIT = 20

const status = ref<Status>('idle')
const result = ref<OcrResult | null>(null)

const cache = new Map<string, OcrResult>()
let generation = 0
let controller: AbortController | null = null

function cacheKey(): string {
  return `${props.pdfId}/${props.revision}/${props.page}`
}

const hasText = computed<boolean>(() => {
  const r = result.value
  if (!r) return false
  if (r.text && r.text.trim()) return true
  return r.lines.some((line) => line.text && line.text.trim())
})

// Reflow prose; use geometry to recover meaningful gaps in headings and marginalia.
// Geometry supplies spacing evidence, never glyph stretching or prose positioning.
const readingBlocks = computed(() => {
  const r = result.value
  if (!r) return []
  const source = new Map(r.layout?.blocks.map(block => [block.id, block]) || [])
  const blocks = ocrTextBlocks(r).map(block => {
    const semantic = source.get(block.id)
    return { ...block,
      text: block.kind === 'heading' ? block.lines.map(line => spatialRunText(line.runs)).join(' ') : semantic?.text ?? block.lines.map(line => line.runs.map(run => run.text).join('')).join('\n'),
      indent: block.kind === 'heading' ? 0 : semantic?.indent_em ?? 0,
      scale: block.kind === 'heading' ? Math.min(1.5, Math.max(1, semantic?.font_scale ?? 1)) : 1,
      align: semantic?.align ?? 'center',
    }
  })
  // Consecutive marginal blocks share a compact row; retain their text and order.
  const grouped: typeof blocks = []
  for (const block of blocks) {
    const previous = grouped[grouped.length - 1]
    if (block.kind === 'marginal' && previous?.kind === 'marginal') {
      previous.lines.push(...block.lines)
    } else grouped.push(block)
  }
  return grouped
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

watch(
  () => [props.active, props.pdfId, props.page, props.revision] as const,
  () => {
    void load()
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  cancel()
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
  justify-content: flex-end;
  gap: 12px;
  padding: 12px 20px;
  border-bottom: 1px solid #e2dbcc;
  background: #faf7f0;
}

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

.retry-btn:hover {
  background: #f1e9da;
  border-color: #c9bda5;
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
  width: calc(100% - 48px);
  max-width: 840px;
  margin: 24px auto;
  padding: 28px 40px;
  background: #fffdf9;
  border: 1px solid #e8e0d0;
  border-radius: 4px;
  box-shadow: 0 1px 3px rgba(74, 66, 56, 0.08), 0 8px 24px rgba(74, 66, 56, 0.06);
  color: #2f2a24;
  font-family: "Songti SC", "STSong", "SimSun", "Noto Serif SC", "Source Han Serif SC", "Songti TC", serif;
  font-size: 18px;
  line-height: 1.9;
}

.reading-block {
  margin: 0 0 .65em;
  line-height: 1.9;
  overflow-wrap: break-word;
  letter-spacing: normal;
  white-space: pre-line;
}

.reading-heading {
  margin: 1.5em 0 1em;
  font-weight: 600;
  line-height: 1.5;
}

.marginal-text {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: space-between;
  gap: 4px 20px;
  margin: 0 0 20px;
  padding-bottom: 10px;
  border-bottom: 1px solid #ece6da;
  font-size: 13px;
  line-height: 1.6;
  color: #82796c;
  overflow-wrap: anywhere;
  user-select: text;
}

.reading-heading:first-child { margin-top: 0; }
.reading-block:last-child { margin-bottom: 0; }

.state {
  margin: 0;
  padding: 32px 20px;
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
    width: calc(100% - 24px);
    padding: 24px 22px;
    font-size: 17px;
  }
}
</style>
