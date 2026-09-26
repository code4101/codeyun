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

        <svg v-else-if="status === 'ready' && result" class="ocr-page"
          :viewBox="`0 0 ${result.geometry.width} ${result.geometry.height}`"
          :style="{ aspectRatio: `${result.geometry.width} / ${result.geometry.height}` }"
          xmlns="http://www.w3.org/2000/svg" aria-label="OCR 文字">
          <g v-for="block in positionedBlocks" :key="block.id" :data-paragraph-id="block.id">
            <text v-for="line in block.lines" :key="line.id" xml:space="preserve"><tspan
              v-for="(run, index) in line.runs" :key="index"
              :x="run.x" :y="run.y + run.h * .88" :font-size="run.h"
              :textLength="run.w" lengthAdjust="spacingAndGlyphs">{{ run.text }}</tspan></text>
          </g>
        </svg>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import { getPdfPageOcr, type PdfPageOcr } from '@/api/pdfDocuments'
import PdfBookOcrControl from './PdfBookOcrControl.vue'
import { ocrTextBlocks } from './ocrTextLayer'

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

// Share the source PDF's paragraph/run model. Position individual OCR runs so real
// gaps survive, without inserting synthetic spaces into continuous paragraph text.
// Incomplete tokenization falls back to the original line to preserve punctuation.
const positionedBlocks = computed(() => result.value ? ocrTextBlocks(result.value) : [])

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
  max-width: 1100px;
  margin: 24px auto;
  padding: 0;
  background: #fffdf9;
  border: 1px solid #e8e0d0;
  border-radius: 4px;
  box-shadow: 0 1px 3px rgba(74, 66, 56, 0.08), 0 8px 24px rgba(74, 66, 56, 0.06);
  color: #2f2a24;
  font-family: "Songti SC", "STSong", "SimSun", "Noto Serif SC", "Source Han Serif SC", "Songti TC", serif;
  font-size: 18px;
  line-height: 1.9;
}

.ocr-page {
  display: block;
  width: 100%;
  height: auto;
  fill: #222;
  user-select: text;
  cursor: text;
  font-weight: 400;
}

.ocr-page text::selection { background: #b9d2ff; }

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
    font-size: 17px;
  }
}
</style>
