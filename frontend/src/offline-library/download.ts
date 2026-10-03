import api from '@/api'
import { fetchPdfContentUrl, type PdfDocumentDetail, type PdfOutlineResponse, type PdfReadingPage } from '@/api/pdfDocuments'
import { fetchLinuxDoBook } from '@/api/linuxDoBooks'
import { fetchLocalSkillBookCatalog, fetchLocalSkillBookChapter, fetchLocalSkillBookReadingState } from '@/api/skillBooks'
import { fetchLibraryAnnotations } from '@/api/libraryAnnotations'
import { marked } from 'marked'
import { createOfflineHtml } from './html'
import { ensureOfflineRuntime } from './runtime'
import { offlineBookKey, offlineOwner, saveOfflineBook, type OfflineBook, type OfflineBookKind } from './store'

export interface OfflineDownloadTarget { kind: OfflineBookKind; id: string; bookshelfId?: string }
interface PdfOfflineMetadata {
  document: PdfDocumentDetail; outline: PdfOutlineResponse
  notes: Array<{ page_number: number; content_html: string }>; reading_pages: PdfReadingPage[]
}
const pending = new Map<string, Promise<OfflineBook>>()
const plainText = (html: string) => {
  const parsed = new DOMParser().parseFromString(html, 'text/html')
  parsed.querySelectorAll('style,script').forEach(node => node.remove())
  return parsed.body.textContent ?? ''
}
async function fetchBlob(url: string, signal?: AbortSignal, progress?: (received: number, total: number) => void): Promise<Blob> {
  const resolved = new URL(url, window.location.href)
  const token = localStorage.getItem('token')
  const headers: Record<string, string> = {}
  if (resolved.origin === location.origin && token) headers.Authorization = `Bearer ${token}`
  const response = await fetch(resolved, { headers, signal })
  if (!response.ok || response.status === 206) throw new Error(`资源下载失败（${response.status}）：${resolved.pathname}`)
  if (response.headers.get('Content-Type')?.includes('text/html') && !/\.html?$/.test(resolved.pathname)) {
    throw new Error(`资源返回了网页而非文件：${resolved.pathname}`)
  }
  const total = Number(response.headers.get('Content-Length') ?? 0)
  if (!response.body) return response.blob()
  const reader = response.body.getReader()
  const chunks: Uint8Array<ArrayBuffer>[] = []
  let received = 0
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      chunks.push(new Uint8Array(value))
      received += value.byteLength
      progress?.(received, total)
    }
  } finally { reader.releaseLock() }
  // Content-Length describes the compressed wire body when Content-Encoding
  // is present. Only compare it with decoded bytes for uncompressed responses.
  if (total && !response.headers.has('Content-Encoding') && received !== total) throw new Error('文件下载不完整，请重试')
  return new Blob(chunks, { type: response.headers.get('Content-Type') ?? '' })
}

/** The only writer of download packages. UI receives completion after the
 * complete runtime, file, text index and every embedded asset have committed. */
export function downloadOfflineBook(target: OfflineDownloadTarget, progress: (message: string) => void, signal?: AbortSignal): Promise<OfflineBook> {
  const key = offlineBookKey(target.kind, target.id)
  const running = pending.get(key)
  if (running) return running
  const task = download(target, key, progress, signal).finally(() => pending.delete(key))
  pending.set(key, task)
  return task
}
async function download(target: OfflineDownloadTarget, key: string, progress: (message: string) => void, signal?: AbortSignal) {
  const owner = offlineOwner()
  let assets = 0
  const localize = createOfflineHtml(url => fetchBlob(url, signal), () => progress(`正在下载图片与附件 ${++assets}`))
  const book: OfflineBook = {
    schema: 1, key, owner, kind: target.kind, id: target.id, title: '', revision: '', downloadedAt: Date.now(), bytes: 0,
    sectionCount: 0, sections: [], toc: [], annotations: [], initialSection: 0, initialOffset: 0, bookshelfId: target.bookshelfId,
  }
  progress('正在准备离线阅读器')
  await ensureOfflineRuntime()
  signal?.throwIfAborted()
  if (target.kind === 'pdf') {
    progress('正在下载书籍信息、目录与已有识别文本')
    const metadata = (await api.get<PdfOfflineMetadata>(`/pdf-documents/${target.id}/offline-metadata`, { signal, timeout: 120000 })).data
    book.title = metadata.document.display_title || metadata.document.title
    book.revision = metadata.document.content_hash ?? String(metadata.document.updated_at)
    const { url } = await fetchPdfContentUrl(Number(target.id))
    book.pdf = await fetchBlob(url, signal, (received, total) => progress(`正在下载整本 PDF ${total ? Math.floor(received / total * 100) + '%' : Math.ceil(received / 1024 / 1024) + ' MB'}`))
    const { openOfflinePdf } = await import('./pdf')
    const pdf = await openOfflinePdf(book.pdf)
    try {
      const ocr = new Map(metadata.reading_pages.map(page => [page.page, page]))
      for (let number = 1; number <= pdf.numPages; number++) {
        signal?.throwIfAborted()
        progress(`正在验证全文与搜索索引 ${number}/${pdf.numPages}`)
        const page = await pdf.getPage(number)
        const content = await page.getTextContent()
        const text = content.items.map(item => 'str' in item ? item.str + (item.hasEOL ? '\n' : ' ') : '').join('')
        const recognized = ocr.get(number)?.blocks.map(block => block.text).join('\n') ?? ''
        const normalized = (value: string) => value.replace(/\s/g, '').toLowerCase()
        const searchable = !recognized || normalized(text).includes(normalized(recognized)) ? text
          : normalized(recognized).includes(normalized(text)) ? recognized : `${text}\n${recognized}`
        book.sections.push({ id: String(number), title: `第 ${number} 页`, html: '', text: searchable })
        page.cleanup()
      }
    } finally { await pdf.destroy() }
    book.toc = metadata.outline.entries.filter(item => item.page != null).map(item => ({
      id: item.id, title: item.title, level: item.level, section: Math.max(0, Math.min(book.sections.length - 1, item.page! - 1)),
    }))
    book.notes = {}
    for (const note of metadata.notes) book.notes[String(note.page_number)] = await localize(note.content_html)
    book.initialSection = Math.max(0, (metadata.document.my_state?.current_page ?? 1) - 1)
    if (owner !== 'anonymous') book.annotations = await fetchLibraryAnnotations('pdf', target.id)
    const latest = (await api.get<PdfOfflineMetadata>(`/pdf-documents/${target.id}/offline-metadata`, { signal, timeout: 120000 })).data
    if (latest.document.content_hash !== metadata.document.content_hash || latest.document.updated_at !== metadata.document.updated_at
      || latest.outline.revision !== metadata.outline.revision) throw new Error('下载期间书籍发生变化，请重新下载')
  } else if (target.kind === 'ebook') {
    progress('正在下载整本正文')
    const content = await fetchLinuxDoBook(target.id)
    book.title = content.title
    book.revision = content.revision
    const html = await localize(content.content_html)
    const parsed = new DOMParser().parseFromString(html, 'text/html')
    const styles = Array.from(parsed.querySelectorAll('style')).map(node => node.outerHTML).join('')
    const articles = Array.from(parsed.querySelectorAll<HTMLElement>('article[data-article-id]'))
    book.sections = articles.length ? articles.map((article, index) => ({
      id: article.dataset.articleId!, title: content.toc.find(item => item.anchor === article.dataset.articleId)?.title
        || article.querySelector('h1,h2,h3,h4,h5,h6')?.textContent || `章节 ${index + 1}`,
      html: styles + article.outerHTML, text: plainText(article.outerHTML), revision: content.revision,
    })) : [{ id: content.toc[0]?.anchor || 'body', title: content.title, html, text: plainText(html), revision: content.revision }]
    // Footnotes and appendices outside article wrappers remain part of the book.
    if (articles.length) {
      const remainder = parsed.body.cloneNode(true) as HTMLElement
      remainder.querySelectorAll('article[data-article-id],style').forEach(node => node.remove())
      if (remainder.textContent?.trim() || remainder.querySelector('img,svg,audio,video')) {
        book.sections.push({ id: 'offline-appendix', title: '附录与注释', html: styles + remainder.innerHTML, text: plainText(remainder.innerHTML), revision: content.revision })
      }
    }
    const anchorSections = new Map<string, number>()
    for (const [index, part] of book.sections.entries()) {
      anchorSections.set(part.id, index)
      for (const node of new DOMParser().parseFromString(part.html, 'text/html').querySelectorAll('[id]')) anchorSections.set(node.id, index)
    }
    book.toc = content.toc.map(item => ({ id: item.anchor, title: item.title, level: item.level,
      section: anchorSections.get(item.anchor) ?? 0, anchor: item.anchor }))
    const appendix = book.sections.findIndex(part => part.id === 'offline-appendix')
    if (appendix >= 0) book.toc.push({ id: 'offline-appendix', title: '附录与注释', level: 1, section: appendix })
    if (!book.toc.length) book.toc = book.sections.map((part, section) => ({ id: part.id, title: part.title, level: 1, section }))
    book.initialSection = Math.max(0, book.sections.findIndex(part => part.id === content.reading_state?.chapter_id))
    book.initialOffset = content.reading_state?.character_offset ?? 0
    if (owner !== 'anonymous') book.annotations = await fetchLibraryAnnotations('rich-text', target.id)
    if ((await fetchLinuxDoBook(target.id)).revision !== book.revision) throw new Error('下载期间书籍发生变化，请重新下载')
  } else {
    progress('正在下载技能书目录')
    const catalog = await fetchLocalSkillBookCatalog(target.bookshelfId ?? '')
    book.title = catalog.title
    book.revision = catalog.revision
    const chapters = catalog.skills.flatMap(skill => skill.chapters)
    for (const [index, chapter] of chapters.entries()) {
      signal?.throwIfAborted()
      progress(`正在下载全部章节 ${index + 1}/${chapters.length}`)
      const content = await fetchLocalSkillBookChapter(chapter.id)
      if (content.chapter.revision !== chapter.revision) throw new Error('下载期间章节发生变化，请重新下载')
      const add = async (id: string, title: string, source: string, revision: string) => {
        const html = await localize(await marked.parse(source))
        book.toc.push({ id, title, level: chapter.kind === 'main' ? 0 : 1, section: book.sections.length })
        book.sections.push({ id, title, html, text: plainText(html), revision })
      }
      await add(chapter.id, chapter.title, content.markdown, chapter.revision)
      if (content.translation.status === 'done' && content.translation.markdown) {
        await add(`${chapter.id}:zh-CN`, `${chapter.title}（中文译文）`, content.translation.markdown, content.translation.revision)
      }
    }
    const state = await fetchLocalSkillBookReadingState()
    book.initialSection = Math.max(0, book.sections.findIndex(part => part.id === state.chapter_id))
    book.initialOffset = state.character_offset
    if (owner !== 'anonymous') book.annotations = await fetchLibraryAnnotations('skill-book', catalog.asset_id)
    if ((await fetchLocalSkillBookCatalog(target.bookshelfId ?? '')).revision !== book.revision) throw new Error('下载期间书籍发生变化，请重新下载')
  }
  if (!book.sections.length) throw new Error('书籍没有可缓存的正文')
  signal?.throwIfAborted()
  book.sectionCount = book.sections.length
  book.downloadedAt = Date.now()
  book.bytes = new Blob([JSON.stringify({ ...book, pdf: undefined })]).size + (book.pdf?.size ?? 0)
  progress('正在保存完整离线副本')
  try { await saveOfflineBook(book) }
  catch (error) {
    if (error instanceof DOMException && error.name === 'QuotaExceededError') throw new Error('设备存储空间不足，请删除部分离线书籍后重试')
    throw error
  }
  // Persistent storage is a browser policy decision; failure does not turn an
  // otherwise committed whole-book package into an incomplete download.
  try { await navigator.storage?.persist?.() } catch { /* browser may decline */ }
  return book
}
