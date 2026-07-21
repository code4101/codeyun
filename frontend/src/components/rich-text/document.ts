import DOMPurify from 'dompurify'
import { marked } from 'marked'

export type RichTextDocumentFormat = 'html' | 'markdown' | 'plain-text'

export interface RichTextDocumentCapabilities {
  canEdit: boolean
}

export interface RichTextDocument {
  id: string
  title: string
  content: string
  format: RichTextDocumentFormat
  revision?: string
  capabilities: RichTextDocumentCapabilities
}

function escapeHtml(value: string) {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;')
}

/**
 * 图书馆与星图笔记共享 HTML 作为富文本展示语义。
 * 各内容源只负责把自己的原始格式适配成安全 HTML；保存仍由内容源自行实现。
 */
export function renderRichTextDocument(document: RichTextDocument | null) {
  if (!document) return ''

  let html = ''
  if (document.format === 'markdown') {
    html = marked.parse(document.content, {
      async: false,
      gfm: true,
      breaks: false,
    })
  } else if (document.format === 'plain-text') {
    html = `<pre>${escapeHtml(document.content)}</pre>`
  } else {
    html = document.content
  }
  return DOMPurify.sanitize(html)
}
