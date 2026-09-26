<template>
  <section class="section-reader">
    <header><PdfBookOcrControl :pdf-id="pdfId" :revision="revision" :active="active" :can-control="canControl" /></header>
    <div ref="scroll" class="reading-scroll">
      <article>
        <h1>{{ scope.title }}</h1>
        <section v-for="item in visiblePages" :key="item.page" class="source-page">
          <button class="source-link" @click="$emit('source', item.page)">第 {{ item.page }} 页 ↗</button>
          <div v-if="!item.available" class="missing">
            此页尚未识别 <button :disabled="recognizing !== null" @click="recognize(item.page)">{{ recognizing === item.page ? '识别中…' : '识别此页' }}</button>
          </div>
          <p v-if="item.unresolved" class="missing">此页章节边界尚未定位，可点击页码查看原文。</p>
          <template v-for="block in item.blocks" :key="block.id">
            <component v-if="block.kind !== 'marginal'" :is="block.kind === 'heading' ? 'h2' : 'p'"
              :style="{fontSize: `${block.font_scale || 1}em`, textIndent: block.kind === 'paragraph' ? `${block.indent_em}em` : '0', textAlign: block.kind === 'heading' ? block.align : 'justify'}">{{ headingText(block) }}</component>
          </template>
        </section>
        <p v-if="error" role="alert">{{ error }} <button @click="loadMore">重试</button></p>
        <button v-if="nextPage <= scope.end" :disabled="loading" @click="loadMore">{{ loading ? '加载中…' : '继续阅读' }}</button>
        <footer><button :disabled="scope.start <= 1" @click="$emit('navigate', scope.start - 1)">上一节</button><button :disabled="!scope.nextStart" @click="$emit('navigate', scope.nextStart!)">下一节</button></footer>
      </article>
    </div>
  </section>
</template>
<script setup lang="ts">
import {computed, ref, watch, onBeforeUnmount} from 'vue'
import {getPdfReadingPages, getPdfPageOcr, type PdfReadingPage, type PdfOutlineEntry} from '@/api/pdfDocuments'
import PdfBookOcrControl from './PdfBookOcrControl.vue'
import {trimReadingPage} from './ocrSectionBoundary'
import {spatialRunText} from './ocrTypography'
function headingText(block:PdfReadingPage['blocks'][number]) {
  const runs = block.runs
  return block.kind === 'heading' && runs?.length && runs.map(r => r.text).join('').replace(/\s/g,'') === block.text.replace(/\s/g,'')
    ? spatialRunText(runs) : block.text
}
const props = defineProps<{pdfId:number; page:number; total:number; entries:PdfOutlineEntry[]; active:boolean; revision:string; canControl:boolean}>()
defineEmits<{source:[page:number]; navigate:[page:number]}>()
// The deepest last bookmark at or before the page defines the reading section.
// Include the next heading page, then trim both boundary pages against OCR blocks.
const scope = computed(() => {
  const entries = props.entries.filter(e => e.page && e.page <= props.total)
  let index = -1
  entries.forEach((e, i) => { if (e.page! <= props.page && (index < 0 || e.page! >= entries[index].page!)) index = i })
  if (index < 0) return {title:'正文', start:1, end:entries[0]?.page ?? props.total, nextTitle:entries[0]?.title, nextStart:entries[0]?.page, hasTitle:false}
  const entry = entries[index]
  const next = entries.slice(index + 1).find(e => e.page! > entry.page!)
  return {title:entry.title, start:entry.page!, end:next ? next.page! : props.total, nextTitle:next?.title, nextStart:next?.page, hasTitle:true}
})
const pages = ref<PdfReadingPage[]>([]), nextPage = ref(1), loading = ref(false), error = ref('')
const visiblePages = computed(() => {
  const visible = pages.value.map(page => trimReadingPage(page,
  page.page === scope.value.start && scope.value.hasTitle ? scope.value.title : undefined,
  page.page === scope.value.nextStart ? scope.value.nextTitle : undefined))
  // Carry typography over a probable continuation without mutating cached OCR.
  for (let i = 1; i < visible.length; i++) {
    const before = visible[i - 1], after = visible[i]
    if (!before.available || !after.available || before.unresolved || after.unresolved || after.page !== before.page + 1) continue
    const last = before.blocks.filter(b => b.kind !== 'marginal').at(-1)
    const first = after.blocks.find(b => b.kind !== 'marginal')
    if (last?.kind === 'paragraph' && first?.kind === 'paragraph' && first.indent_em === 0
      && !/[。！？.!?：:]\s*$/.test(last.text)
      && Math.abs(last.font_scale - first.font_scale) <= .2) {
      after.blocks = after.blocks.map(b => b === first ? {...b, font_scale:last.font_scale} : b)
    }
  }
  return visible
})
const recognizing = ref<number|null>(null), scroll = ref<HTMLElement>()
let controller:AbortController|undefined, generation = 0
async function loadMore() {
  if (loading.value || nextPage.value > scope.value.end || !props.active) return
  const version = generation
  loading.value = true; error.value = ''
  const end = Math.min(nextPage.value + 7, scope.value.end)
  try {
    const batch = await getPdfReadingPages(props.pdfId, nextPage.value, end, controller?.signal)
    if (version !== generation) return
    pages.value.push(...batch); nextPage.value = end + 1
  } catch { if (version === generation && !controller?.signal.aborted) error.value = '读取失败' }
  finally { if (version === generation) loading.value = false }
}
async function recognize(page:number) {
  const version = generation
  recognizing.value = page
  try {
    await getPdfPageOcr(props.pdfId, page, controller?.signal)
    const [result] = await getPdfReadingPages(props.pdfId, page, page, controller?.signal)
    if (version === generation) pages.value = pages.value.map(p => p.page === page ? result : p)
  } catch { if (version === generation) error.value = '识别失败，请稍后重试' }
  finally { if (version === generation) recognizing.value = null }
}
watch(() => [props.pdfId, props.revision, props.active, scope.value.start, scope.value.end], () => {
  generation++; controller?.abort(); controller = new AbortController()
  pages.value = []; nextPage.value = scope.value.start; loading.value = false; recognizing.value = null; error.value = ''
  scroll.value?.scrollTo(0,0)
  if (props.active) void loadMore()
}, {immediate:true})
onBeforeUnmount(() => {generation++; controller?.abort()})
</script>
<style scoped>
.section-reader{display:flex;flex-direction:column;min-height:0;flex:1;background:#f3efe6}
header{display:flex;justify-content:flex-end;padding:12px 20px}
.reading-scroll{overflow:auto;flex:1;min-height:0}
article{max-width:780px;margin:20px auto;padding:40px 48px;background:#fffdf9;color:#302b25;font:18px/1.9 "SimSun",serif}
h1{font-size:1.4em;margin:0 0 1.5em}h2{font-size:1.15em;margin:1.5em 0 1em}p{margin:0 0 .65em}
button{cursor:pointer;border:0;background:transparent;color:#59728e;font:inherit;font-size:13px}button:disabled{opacity:.5;cursor:default}
.source-link{display:block;margin-left:auto;font-size:11px;color:#9a9388}.missing{padding:20px;color:#918779;font-size:14px}
footer{display:flex;justify-content:space-between;margin-top:30px}
@media(max-width:640px){article{margin:12px;padding:24px}}
</style>
