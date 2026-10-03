<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import type { PDFDocumentProxy, RenderTask } from 'pdfjs-dist'
import { openOfflinePdf } from './pdf'
import { ensureOfflineRuntime } from './runtime'
import { searchOfflineSections } from './search'
import {
  listOfflineBooks, readOfflineBook, deleteOfflineBook, readOfflinePosition, saveOfflinePosition,
  offlineOwner, formatOfflineBytes, type OfflineBook, type OfflineBookSummary,
} from './store'

const books = ref<OfflineBookSummary[]>([]), book = shallowRef<OfflineBook>(), error = ref(''), busy = ref(false)
const online = ref(navigator.onLine), section = ref(0), offset = ref(0), query = ref(''), fontSize = ref(18), zoom = ref(1)
const sidebar = ref(window.innerWidth > 700), panel = ref<'toc' | 'search' | 'annotations'>('toc'), textView = ref(false), noteOpen = ref(false)
const frame = ref<HTMLIFrameElement>(), canvas = ref<HTMLCanvasElement>(), pdfViewport = ref<HTMLElement>()
const current = computed(() => book.value?.sections[section.value])
const hits = computed(() => searchOfflineSections(book.value?.sections ?? [], query.value))
const annotationList = computed(() => book.value?.annotations ?? [])
const syncing = ref(false), syncMessage = ref(''), rendering = ref(false)
let pdf: PDFDocumentProxy | undefined, renderTask: RenderTask | undefined, version = 0
let progressTimer: ReturnType<typeof setTimeout> | undefined
let account = offlineOwner()

function safeDocument(html: string) {
  return `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data: blob:; media-src data: blob:; font-src data: blob:; style-src 'unsafe-inline'"><style>html{scroll-behavior:auto}body{margin:0;padding:24px;line-height:1.85;font-family:system-ui,sans-serif;font-size:${fontSize.value}px;color:#292d35;background:#fff}img,svg,video{max-width:100%;height:auto}table{max-width:100%;overflow:auto}pre{white-space:pre-wrap;overflow-wrap:anywhere}a{color:#356283}mark{background:#ffe78b}#offline-content{max-width:900px;margin:auto;overflow-wrap:anywhere}</style></head><body><div id="offline-content">${html}</div></body></html>`
}
function markedHtml(html: string) {
  if (!query.value.trim()) return html
  const doc = new DOMParser().parseFromString(html, 'text/html')
  const term = query.value.trim().replace(/[.*+?^${}()|[\]\\]/g, '\\$&').split('').join('\\s*')
  const expression = new RegExp(term, 'gi')
  const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT)
  const nodes: Text[] = []
  while (walker.nextNode()) if (!walker.currentNode.parentElement?.closest('style,script')) nodes.push(walker.currentNode as Text)
  for (const node of nodes) {
    const matches = [...node.data.matchAll(expression)]
    if (!matches.length) continue
    const fragment = doc.createDocumentFragment()
    let start = 0
    for (const match of matches) {
      fragment.append(node.data.slice(start, match.index))
      const mark = doc.createElement('mark'); mark.textContent = match[0]; fragment.append(mark)
      start = match.index! + match[0].length
    }
    fragment.append(node.data.slice(start)); node.replaceWith(fragment)
  }
  return doc.head.innerHTML + doc.body.innerHTML
}
const sourceDocument = computed(() => safeDocument(markedHtml(current.value?.html ?? '')))
const noteDocument = computed(() => safeDocument(book.value?.notes?.[String(section.value + 1)] ?? ''))
const textParts = computed(() => {
  const text = current.value?.text ?? '', term = query.value.trim()
  if (!term) return [{ text, match: false }]
  const expression = new RegExp(term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').split('').join('\\s*'), 'gi')
  const parts: Array<{ text: string; match: boolean }> = []
  let cursor = 0
  for (const hit of text.matchAll(expression)) {
    parts.push({ text: text.slice(cursor, hit.index), match: false }, { text: hit[0], match: true })
    cursor = hit.index! + hit[0].length
  }
  parts.push({ text: text.slice(cursor), match: false })
  return parts
})

async function refreshBooks() { books.value = await listOfflineBooks() }
async function remember() {
  if (!book.value || account !== offlineOwner()) return
  try { await saveOfflinePosition({ key: book.value.key, section: section.value, offset: offset.value, updatedAt: Date.now() }) }
  catch { error.value = '阅读进度保存失败，请检查设备存储空间' }
}
function textNodes(doc: Document): Text[] {
  const root = doc.getElementById('offline-content')
  if (!root) return []
  const walker = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  const nodes: Text[] = []
  while (walker.nextNode()) if (!walker.currentNode.parentElement?.closest('style,script')) nodes.push(walker.currentNode as Text)
  return nodes
}
function captureScroll() {
  const doc = frame.value?.contentDocument
  if (!doc) return
  let total = 0
  for (const node of textNodes(doc)) {
    const range = doc.createRange(); range.selectNodeContents(node)
    const rect = range.getBoundingClientRect()
    if (rect.height > 0 && rect.bottom > 24) {
      let low = 0, high = node.length
      while (low < high) {
        const middle = Math.floor((low + high) / 2)
        range.setStart(node, middle); range.setEnd(node, Math.min(node.length, middle + 1))
        if (range.getBoundingClientRect().bottom <= 24) low = middle + 1
        else high = middle
      }
      offset.value = total + low
      break
    }
    total += node.length
  }
  clearTimeout(progressTimer)
  progressTimer = setTimeout(() => { void remember() }, 300)
}
function restoreScroll() {
  const doc = frame.value?.contentDocument
  if (!doc || !offset.value) return
  let remaining = offset.value
  for (const node of textNodes(doc)) {
    if (remaining <= node.length) {
      const range = doc.createRange()
      range.setStart(node, remaining); range.setEnd(node, Math.min(node.length, remaining + 1))
      doc.defaultView?.scrollTo(0, range.getBoundingClientRect().top + (doc.defaultView.scrollY ?? 0) - 24)
      return
    }
    remaining -= node.length
  }
}
function frameLoaded() {
  const doc = frame.value?.contentDocument
  if (!doc) return
  // Images are embedded; restore after their layout settles too.
  restoreScroll()
  const loadingImages = Array.from(doc.images).filter(image => !image.complete)
  void Promise.all(loadingImages.map(image => new Promise<void>(resolve => {
    image.addEventListener('load', () => resolve(), { once: true }); image.addEventListener('error', () => resolve(), { once: true })
  }))).then(restoreScroll)
  doc.defaultView?.addEventListener('scroll', captureScroll, { passive: true })
  doc.addEventListener('click', event => {
    const anchor = (event.target as Element)?.closest('a')
    const href = anchor?.getAttribute('href')
    if (!href?.startsWith('#')) return
    event.preventDefault()
    const id = decodeURIComponent(href.slice(1))
    const local = doc.getElementById(id)
    if (local) { local.scrollIntoView(); return }
    const entry = book.value?.toc.find(item => item.id === id || item.anchor === id)
    const otherSection = entry?.section ?? book.value?.sections.findIndex(part =>
      new DOMParser().parseFromString(part.html, 'text/html').getElementById(id)) ?? -1
    if (otherSection >= 0) void navigate(otherSection, 0, id)
  })
  if (pendingAnchor) { doc.getElementById(pendingAnchor)?.scrollIntoView(); pendingAnchor = '' }
}
let pendingAnchor = ''
async function navigate(index: number, characterOffset = 0, anchor = '') {
  if (!book.value) return
  clearTimeout(progressTimer)
  section.value = Math.max(0, Math.min(book.value.sections.length - 1, index))
  offset.value = Math.max(0, characterOffset)
  noteOpen.value = false; pendingAnchor = anchor
  await remember()
  await nextTick()
  if (book.value.kind === 'pdf' && !textView.value) await renderPdf()
  else if (frame.value?.contentDocument && index === section.value) restoreScroll()
  if (window.innerWidth <= 700) sidebar.value = false
}
async function renderPdf() {
  if (!pdf || !canvas.value || textView.value) return
  const own = ++version
  const previous = renderTask
  previous?.cancel()
  await previous?.promise.catch(() => undefined)
  if (own !== version) return
  rendering.value = true
  try {
    const page = await pdf.getPage(section.value + 1)
    if (own !== version || !canvas.value) return
    const width = Math.max(200, (pdfViewport.value?.clientWidth ?? 850) - 32)
    const ratio = window.devicePixelRatio || 1
    const viewport = page.getViewport({ scale: width / page.getViewport({ scale: 1 }).width * zoom.value })
    const element = canvas.value
    element.width = Math.ceil(viewport.width * ratio); element.height = Math.ceil(viewport.height * ratio)
    element.style.width = `${viewport.width}px`; element.style.height = `${viewport.height}px`
    renderTask = page.render({ canvas: element, viewport, transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0] })
    await renderTask.promise
  } catch (cause) {
    if (own === version && !(cause instanceof Error && cause.name === 'RenderingCancelledException')) error.value = 'PDF 页面渲染失败，请重新下载该书'
  } finally { if (own === version) rendering.value = false }
}
async function closePdf() {
  version++; renderTask?.cancel(); renderTask = undefined
  const old = pdf; pdf = undefined; await old?.destroy()
}
async function open(key: string) {
  busy.value = true; error.value = ''; syncMessage.value = ''
  await remember(); await closePdf()
  try {
    const downloaded = await readOfflineBook(key)
    if (!downloaded) throw new Error('此书尚未下载，或属于其他账号')
    book.value = downloaded
    const saved = await readOfflinePosition(key)
    section.value = Math.max(0, Math.min(downloaded.sections.length - 1, saved?.section ?? downloaded.initialSection))
    offset.value = saved?.offset ?? downloaded.initialOffset
    history.replaceState(null, '', `?book=${encodeURIComponent(key)}`)
    document.title = `${downloaded.title} · 离线阅读`
    if (downloaded.pdf) pdf = await openOfflinePdf(downloaded.pdf)
    await nextTick(); await renderPdf()
  } catch (cause) { book.value = undefined; error.value = cause instanceof Error ? cause.message : '书籍加载失败' }
  finally { busy.value = false }
}
async function shelf() {
  await remember(); await closePdf(); book.value = undefined; query.value = ''; error.value = ''
  history.replaceState(null, '', location.pathname); document.title = 'CodeYun 离线书架'; await refreshBooks()
}
async function remove(key: string) {
  try { await deleteOfflineBook(key); await refreshBooks() }
  catch { error.value = '删除失败，请重试' }
}
async function syncPosition() {
  const currentBook = book.value
  if (!currentBook || !current.value || !online.value || currentBook.owner !== offlineOwner() || currentBook.owner === 'anonymous') return
  syncing.value = true; syncMessage.value = ''
  try {
    let path = '', payload: Record<string, unknown>
    if (currentBook.kind === 'pdf') {
      path = `/api/pdf-documents/${currentBook.id}/my-state`
      const detail = await fetch(`/api/pdf-documents/${currentBook.id}`, { headers: { Authorization: `Bearer ${localStorage.getItem('token')}` } })
      if (!detail.ok) throw new Error(detail.status === 401 ? '请联网登录后再同步' : '无法读取服务器进度，请稍后重试')
      const remote = (await detail.json()).my_state
      // Synchronize the position alone, preserving online reader preferences.
      payload = { zoom: 'page-width', sidebar_open: true, state_json: {}, ...remote, current_page: section.value + 1 }
    } else if (currentBook.kind === 'ebook') {
      path = `/api/linux-do-books/${encodeURIComponent(currentBook.id)}/reading-state`
      payload = { chapter_id: current.value.id, character_offset: offset.value, chapter_revision: currentBook.revision,
        current_page: section.value + 1, page_count: currentBook.sectionCount }
    } else {
      path = '/api/skill-books/local/my-state'
      payload = { chapter_id: current.value.id.replace(/:zh-CN$/, ''), character_offset: offset.value, chapter_revision: current.value.revision ?? '' }
    }
    const response = await fetch(path, { method: 'PUT', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('token')}` }, body: JSON.stringify(payload) })
    if (!response.ok) throw new Error(response.status === 401 ? '请联网登录后再同步' : '同步失败，请稍后重试')
    syncMessage.value = '进度已同步'
  } catch (cause) { syncMessage.value = cause instanceof Error ? cause.message : '同步失败，本地进度已保留' }
  finally { syncing.value = false }
}
function networkChanged() { online.value = navigator.onLine }
async function accountChanged() {
  if (account !== offlineOwner()) { account = offlineOwner(); book.value = undefined; await closePdf(); await refreshBooks() }
}
function resized() { void nextTick().then(renderPdf) }
watch([zoom, textView], async () => { await nextTick(); await renderPdf() })
onMounted(async () => {
  window.addEventListener('online', networkChanged); window.addEventListener('offline', networkChanged)
  window.addEventListener('storage', accountChanged); window.addEventListener('focus', accountChanged)
  window.addEventListener('resize', resized); window.addEventListener('pagehide', remember)
  try {
    await refreshBooks()
    const key = new URLSearchParams(location.search).get('book')
    if (key) await open(key)
    if (online.value) void ensureOfflineRuntime().catch(cause => { error.value = cause.message })
  } catch { error.value = '无法读取本地书架，请检查浏览器存储权限' }
})
onBeforeUnmount(() => {
  clearTimeout(progressTimer); void remember(); void closePdf()
  window.removeEventListener('online', networkChanged); window.removeEventListener('offline', networkChanged)
  window.removeEventListener('storage', accountChanged); window.removeEventListener('focus', accountChanged)
  window.removeEventListener('resize', resized); window.removeEventListener('pagehide', remember)
})
</script>
<template>
  <div class="offline-app">
    <header>
      <button v-if="book" @click="shelf">离线书架</button>
      <strong>{{ book?.title || 'CodeYun 离线书架' }}</strong>
      <span class="connection" role="status">{{ online ? '已联网' : '离线 · 飞行模式可用' }}</span>
      <a href="/notes/library" v-if="!book && online">在线图书馆</a>
    </header>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="busy" class="busy" role="status">正在打开本地书籍…</p>
    <main v-if="!book" class="shelf">
      <p>在图书馆右键或长按一本书，选择“下载供离线阅读”。下载完成后，将此页面加入书签，断网也可重新打开。</p>
      <p v-if="!books.length" class="empty">当前账号还没有下载完成的书籍。</p>
      <ul class="books">
        <li v-for="item in books" :key="item.key">
          <button class="book-title" @click="open(item.key)">{{ item.title }}</button>
          <span>{{ item.kind === 'pdf' ? 'PDF' : item.kind === 'skill' ? '技能书' : '电子书' }} · {{ item.sectionCount }} {{ item.kind === 'pdf' ? '页' : '章节' }} · {{ formatOfflineBytes(item.bytes) }}</span>
          <time>{{ new Date(item.downloadedAt).toLocaleString() }}</time>
          <button @click="remove(item.key)" :aria-label="`删除缓存 ${item.title}`">删除缓存</button>
        </li>
      </ul>
      <small>缓存保存在当前设备的当前浏览器中。主动下载的书籍没有自动到期时间；清除网站数据会删除这些副本。</small>
    </main>
    <template v-else>
      <nav class="toolbar" aria-label="阅读控制">
        <button @click="sidebar = !sidebar">目录 / 搜索</button>
        <button :disabled="section === 0" @click="navigate(section - 1)">上一{{ book.kind === 'pdf' ? '页' : '章' }}</button>
        <label>跳转 <input aria-label="跳转页或章节" type="number" min="1" :max="book.sectionCount" :value="section + 1" @change="navigate(Number(($event.target as HTMLInputElement).value) - 1)"></label>
        <span>/ {{ book.sectionCount }}</span>
        <button :disabled="section + 1 === book.sectionCount" @click="navigate(section + 1)">下一{{ book.kind === 'pdf' ? '页' : '章' }}</button>
        <template v-if="book.kind === 'pdf'">
          <button @click="textView = !textView">{{ textView ? '查看原文' : '查看文本' }}</button>
          <label v-if="!textView">缩放 <select v-model="zoom" aria-label="PDF 缩放"><option :value="1">适合宽度</option><option :value="1.5">150%</option><option :value="2">200%</option></select></label>
          <button v-if="book.notes?.[String(section + 1)]" @click="noteOpen = !noteOpen">已有笔记</button>
        </template>
        <label v-if="book.kind !== 'pdf' || textView">字号 <input type="range" aria-label="阅读字号" min="12" max="32" v-model="fontSize"></label>
        <button v-if="online && book.owner !== 'anonymous'" :disabled="syncing" @click="syncPosition">{{ syncing ? '同步中…' : '同步进度' }}</button>
        <span role="status">{{ syncMessage }}</span>
      </nav>
      <div class="reader" :class="{ 'with-sidebar': sidebar }">
        <aside v-if="sidebar" aria-label="离线导航">
          <div class="panel-tabs"><button @click="panel = 'toc'">目录</button><button @click="panel = 'search'">搜索全书</button><button @click="panel = 'annotations'">批注 {{ annotationList.length }}</button></div>
          <ul v-if="panel === 'toc'" class="toc">
            <li v-for="item in book.toc" :key="item.id"><button :style="{ paddingLeft: `${12 + Math.min(5, item.level) * 12}px` }" :class="{ active: item.section === section }" @click="navigate(item.section, 0, item.anchor)">{{ item.title }}</button></li>
            <li v-if="!book.toc.length">此书没有目录，请按页查阅。</li>
          </ul>
          <section v-else-if="panel === 'search'" class="search">
            <input v-model="query" type="search" placeholder="输入关键词" aria-label="搜索全书">
            <p v-if="query">{{ hits.length }} 条结果{{ hits.length === 200 ? '（最多显示 200 条）' : '' }}</p>
            <ul aria-label="全文搜索结果"><li v-for="(hit, index) in hits" :key="index"><button @click="navigate(hit.section, hit.offset)"><strong>{{ hit.title }}</strong><span>{{ hit.snippet }}</span></button></li></ul>
            <small v-if="book.kind === 'pdf'">搜索 PDF 文字与下载时已有的 OCR 文本。未识别的扫描页可查看完整原文。</small>
          </section>
          <ul v-else class="annotations"><li v-for="(annotation, index) in annotationList" :key="index"><blockquote :style="{ borderColor: annotation.color }">{{ annotation.quote_text }}</blockquote><p>{{ annotation.comment_text }}</p></li></ul>
        </aside>
        <section class="content" aria-label="书籍正文">
          <div v-if="book.kind === 'pdf' && !textView" ref="pdfViewport" class="pdf-viewport"><span v-if="rendering" role="status">正在渲染第 {{ section + 1 }} 页…</span><canvas ref="canvas" :aria-label="`PDF 第 ${section + 1} 页`" /></div>
          <div v-else-if="book.kind === 'pdf'" class="text-page" :style="{ fontSize: `${fontSize}px` }"><h2>{{ current?.title }}</h2><p v-if="!current?.text">此页暂无识别文本，请查看原文。</p><pre><template v-for="(part, index) in textParts" :key="index"><mark v-if="part.match">{{ part.text }}</mark><template v-else>{{ part.text }}</template></template></pre></div>
          <iframe v-else ref="frame" :key="`${book.key}:${section}`" :srcdoc="sourceDocument" title="离线书籍正文" sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox" @load="frameLoaded" />
          <iframe v-if="noteOpen" class="page-note" :srcdoc="noteDocument" title="已有页面笔记" sandbox="allow-same-origin" />
        </section>
      </div>
      <footer>本地完整副本 · {{ current?.title }} · 阅读进度自动保存在本机</footer>
    </template>
  </div>
</template>
<style>
html,body,#app{height:100%;margin:0}*{box-sizing:border-box}body{font-family:system-ui,sans-serif;color:#292d35;background:#f6f4ee}button,input,select{font:inherit}button,a{touch-action:manipulation}button{cursor:pointer;background:#fff;border:1px solid #dadcd9;border-radius:6px;padding:7px 11px;color:inherit}button:hover{background:#eff3ef}button:disabled{opacity:.4;cursor:default}a{color:#356283}.offline-app{height:100%;display:flex;flex-direction:column}header{display:flex;gap:16px;align-items:center;padding:12px 20px;border-bottom:1px solid #ddd;flex:none}header strong{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.connection{font-size:12px;color:#527058;flex:none}.error{color:#a22;background:#ffefed;padding:10px 20px;margin:0}.busy{padding:10px 20px}.shelf{width:min(100%,1000px);margin:0 auto;padding:24px;overflow:auto}.shelf p{line-height:1.8;color:#646a6e}.books{display:grid;grid-template-columns:repeat(auto-fill,minmax(245px,1fr));gap:16px;padding:0;list-style:none;margin:24px 0}.books li{background:#fff;border:1px solid #deded8;border-radius:10px;padding:20px;display:flex;gap:12px;flex-direction:column}.books span,.books time,small{font-size:12px;color:#747a7f}.book-title{font-size:18px;font-weight:600;text-align:left;border:0;padding:0}.toolbar{display:flex;align-items:center;gap:8px;padding:8px 16px;flex-wrap:wrap;border-bottom:1px solid #ddd;font-size:13px;flex:none}.toolbar label{display:flex;align-items:center;gap:5px}.toolbar input[type=number]{width:68px}.toolbar input[type=range]{width:85px}.toolbar input,.toolbar select,.search input{border:1px solid #ccc;border-radius:5px;background:#fff;padding:5px}.reader{flex:1;min-height:0;display:grid;grid-template-columns:minmax(0,1fr);position:relative}.reader.with-sidebar{grid-template-columns:270px minmax(0,1fr)}aside{overflow:auto;background:#f9faf7;border-right:1px solid #ddd}.panel-tabs{display:flex;gap:5px;padding:10px;position:sticky;top:0;background:#f9faf7}.panel-tabs button{padding:6px 8px;font-size:12px}.toc,.search ul,.annotations{list-style:none;margin:0;padding:10px}.toc button{width:100%;text-align:left;background:transparent;border:0;font-size:13px;line-height:1.6}.toc button.active{background:#e7ede6}.search{padding:12px}.search input{width:100%}.search ul{padding:0}.search li{margin-bottom:8px}.search li button{width:100%;text-align:left}.search strong,.search span{display:block}.search span{font-size:12px;line-height:1.65;margin-top:6px}.annotations blockquote{margin:8px 0;padding-left:10px;border-left:3px solid #dabd66;font-size:13px}.annotations p{font-size:13px;white-space:pre-wrap}.content{min-width:0;min-height:0;display:flex;flex-direction:column;position:relative;background:#fff}.content>iframe{border:0;flex:1;width:100%;min-height:0}.pdf-viewport{flex:1;min-height:0;overflow:auto;padding:16px;background:#dddeda;text-align:center}.pdf-viewport canvas{display:block;margin:0 auto;background:white;box-shadow:0 2px 8px #0002}.text-page{overflow:auto;padding:24px;flex:1;line-height:1.8}.text-page pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}.content>iframe.page-note{flex:none;height:220px;border-top:1px solid #ddd}footer{padding:7px 16px;font-size:12px;border-top:1px solid #ddd;color:#686f70;flex:none}mark{background:#ffe78b}@media(max-width:700px){header{padding:10px;gap:8px;font-size:14px}.connection{max-width:115px}aside{position:absolute;z-index:2;top:0;bottom:0;left:0;width:min(290px,85vw);box-shadow:4px 0 15px #0002}.reader.with-sidebar{grid-template-columns:minmax(0,1fr)}.toolbar{padding:8px;gap:6px}.toolbar button{padding:6px 8px}.shelf{padding:16px}.books{grid-template-columns:1fr}}
</style>
