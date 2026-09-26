<template>
  <section class="section-reader">
    <div ref="scroll" class="reading-scroll" @scroll.passive="syncLocation">
      <article>
        <section v-for="section in visibleSections" :key="section.id" :data-outline-id="section.id" :data-start-page="section.start">
        <component :is="section.id === scopeId ? 'h1' : 'h2'" :data-outline-heading="section.id">{{ section.title }}</component>
        <template v-for="item in sectionFlow(section)" :key="item.key">
          <div v-if="!item.block" class="missing">
            <template v-if="item.unresolved">章节边界尚未定位 <button @click="$emit('source', item.page)">查看原文</button></template>
            <template v-else>第 {{ item.page }} 页尚未识别 <button :disabled="recognizing !== null" @click="recognize(item.page)">{{ recognizing === item.page ? '识别中…' : '识别此页' }}</button></template>
          </div>
          <component v-else :is="item.block.kind === 'heading' ? 'h2' : 'p'"
            :data-source-pages="item.sources.map(source => source.page).join(',')"
            :style="{fontSize: `${item.block.font_scale || 1}em`, textIndent: item.block.kind === 'paragraph' ? `${item.block.indent_em}em` : '0', textAlign: item.block.kind === 'heading' ? item.block.align : 'justify'}">{{ headingText(item.block) }}</component>
        </template>
        </section>
        <p v-if="error" role="alert">{{ error }} <button @click="loadMore">重试</button></p>
        <button v-if="nextPage <= range.end" ref="sentinel" :disabled="loading" @click="loadMore">{{ loading ? '加载中…' : '继续阅读' }}</button>

      </article>
    </div>
  </section>
</template>
<script setup lang="ts">
import {computed, nextTick, ref, watch, onBeforeUnmount} from 'vue'
import {ocrOutlineSections} from './ocrOutlineSections'
import {getPdfReadingPages, getPdfPageOcr, type PdfReadingPage, type PdfOutlineEntry} from '@/api/pdfDocuments'
import {readingFlow} from './ocrReadingFlow'
import {trimReadingPage} from './ocrSectionBoundary'
import {spatialRunText} from './ocrTypography'
function headingText(block:PdfReadingPage['blocks'][number]) {
  const runs = block.runs
  return block.kind === 'heading' && runs?.length && runs.map(r => r.text).join('').replace(/\s/g,'') === block.text.replace(/\s/g,'')
    ? spatialRunText(runs) : block.text
}
const props = defineProps<{pdfId:number; page:number; total:number; entries:PdfOutlineEntry[]; outlineIds:string[]; scopeId:string; active:boolean; revision:string}>()
const emit = defineEmits<{source:[page:number]; location:[id:string, page:number]}>()
const sections = computed(() => ocrOutlineSections(props.entries, props.outlineIds, props.scopeId, props.total, props.page))
const range = computed(() => ({start: Math.min(...sections.value.map(item => item.start)), end: Math.max(...sections.value.map(item => item.end))}))
const pages = ref<PdfReadingPage[]>([]), nextPage = ref(1), loading = ref(false), error = ref('')
const visibleSections = computed(() => sections.value.filter(section => section.start < nextPage.value))
function sectionFlow(section: ReturnType<typeof ocrOutlineSections>[number]) {
  return readingFlow(pages.value.filter(page => page.page >= section.start && page.page <= section.end).map(page => trimReadingPage(page,
    page.page === section.start && section.hasTitle ? section.title : undefined,
    page.page === section.nextStart ? section.nextTitle : undefined)))
}
const recognizing = ref<number|null>(null), scroll = ref<HTMLElement>()
let controller:AbortController|undefined, generation = 0
let pending: Promise<void> | undefined
function loadMore(): Promise<void> {
  if (pending) return pending
  const request = loadBatch()
  pending = request
  void request.finally(() => { if (pending === request) pending = undefined })
  return request
}
async function loadBatch() {
  if (loading.value || nextPage.value > range.value.end || !props.active) return
  const version = generation
  loading.value = true; error.value = ''
  const end = Math.min(nextPage.value + 7, range.value.end)
  try {
    const batch = await getPdfReadingPages(props.pdfId, nextPage.value, end, controller?.signal)
    if (version !== generation) return
    pages.value.push(...batch); nextPage.value = end + 1
    await nextTick(); syncLocation()
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
const sentinel = ref<HTMLElement>()
let observer: IntersectionObserver | undefined
let frame = 0
let navigating = false
let navigationVersion = 0
function syncLocation() {
  cancelAnimationFrame(frame)
  frame = requestAnimationFrame(() => {
    if (!props.active || navigating || !scroll.value) return
    const top = scroll.value.getBoundingClientRect().top + 80
    const elements = [...scroll.value.querySelectorAll<HTMLElement>('[data-outline-id]')]
    const element = elements.find(item => item.getBoundingClientRect().bottom > top)
    if (!element) return
    const block = [...element.querySelectorAll<HTMLElement>('[data-source-pages]')].find(item => item.getBoundingClientRect().bottom > top)
    emit('location', element.dataset.outlineId!, Number(block?.dataset.sourcePages?.split(',')[0] ?? element.dataset.startPage))
  })
}
async function scrollToSection(id: string) {
  const target = sections.value.find(section => section.id === id)
  if (!target || !props.active) return
  const version = generation
  const navigation = ++navigationVersion
  navigating = true
  try {
    while (nextPage.value <= target.start && version === generation && navigation === navigationVersion && !error.value) await loadMore()
    if (version !== generation || navigation !== navigationVersion) return
    await nextTick()
    const element = [...(scroll.value?.querySelectorAll<HTMLElement>('[data-outline-id]') ?? [])].find(item => item.dataset.outlineId === id)
    if (element && scroll.value) scroll.value.scrollTop += element.getBoundingClientRect().top - scroll.value.getBoundingClientRect().top - 12
    emit('location', id, target.start)
  } finally { if (navigation === navigationVersion) navigating = false }
}
async function scrollToPage(page: number) {
  const target = sections.value.find(section => section.id === props.scopeId && section.start === page)
    ?? [...sections.value].reverse().find(section => section.start <= page) ?? sections.value[0]
  if (target) await scrollToSection(target.id)
}
watch([() => props.pdfId, () => props.revision, () => props.active, () => sections.value.map(item => `${item.id}:${item.start}:${item.end}:${item.title}`).join('|')], async () => {
  generation++; controller?.abort(); controller = new AbortController(); pending = undefined
  pages.value = []; nextPage.value = range.value.start; loading.value = false; recognizing.value = null; error.value = ''
  const initialPage = props.page
  scroll.value?.scrollTo(0,0)
  if (props.active) await scrollToPage(initialPage)
}, {immediate:true})
watch(() => [sentinel.value, props.active], () => {
  observer?.disconnect()
  if (!props.active || !sentinel.value) return
  observer = new IntersectionObserver(entries => { if (entries.some(entry => entry.isIntersecting) && !error.value) void loadMore() }, {root: scroll.value, rootMargin:'400px'})
  observer.observe(sentinel.value)
}, {flush:'post'})
defineExpose({scrollToSection, scrollToPage})
onBeforeUnmount(() => {generation++; controller?.abort(); observer?.disconnect(); cancelAnimationFrame(frame)})
</script>
<style scoped>
.section-reader{display:flex;flex-direction:column;min-height:0;flex:1;background:var(--reader-content,#f3efe6)}
.reading-scroll{overflow:auto;flex:1;min-height:0}
article{max-width:780px;margin:0 auto;padding:12px 32px 48px;background:var(--reader-content,#fffdf9);color:var(--reader-text,#302b25);font:18px/1.9 "SimSun",serif}
h1{font-size:1.4em;margin:0 0 1.5em}h2{font-size:1.15em;margin:1.5em 0 1em}p{margin:0 0 .65em}
button{cursor:pointer;border:0;background:transparent;color:var(--reader-link,#59728e);font:inherit;font-size:13px}button:disabled{opacity:.5;cursor:default}
.source-link{display:block;margin-left:auto;font-size:11px;color:var(--reader-muted,#9a9388)}.missing{padding:20px;color:var(--reader-muted,#918779);font-size:14px}

@media(max-width:640px){article{margin:12px;padding:24px}}
</style>
