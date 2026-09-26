<template>
  <section class="section-reader">
    <header><PdfBookOcrControl :pdf-id="pdfId" :revision="revision" :active="active" :can-control="canControl" /></header>
    <div ref="scroll" class="reading-scroll">
      <article>
        <h1>{{ scope.title }}</h1>
        <template v-for="item in flow" :key="item.key">
          <div v-if="!item.block" class="missing">
            <template v-if="item.unresolved">章节边界尚未定位 <button @click="$emit('source', item.page)">查看原文</button></template>
            <template v-else>第 {{ item.page }} 页尚未识别 <button :disabled="recognizing !== null" @click="recognize(item.page)">{{ recognizing === item.page ? '识别中…' : '识别此页' }}</button></template>
          </div>
          <component v-else :is="item.block.kind === 'heading' ? 'h2' : 'p'"
            :data-source-pages="item.sources.map(source => source.page).join(',')"
            :style="{fontSize: `${item.block.font_scale || 1}em`, textIndent: item.block.kind === 'paragraph' ? `${item.block.indent_em}em` : '0', textAlign: item.block.kind === 'heading' ? item.block.align : 'justify'}">{{ headingText(item.block) }}</component>
        </template>
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
import {readingFlow} from './ocrReadingFlow'
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
  return visible
})
const flow = computed(() => readingFlow(visiblePages.value))
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
