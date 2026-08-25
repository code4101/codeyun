import DOMPurify from 'dompurify'
import katex from 'katex'
import 'katex/dist/katex.min.css'
import { marked } from 'marked'

export type RichTextDocumentFormat = 'html' | 'markdown' | 'plain-text'

export interface RichTextDocumentCapabilities {
  canEdit: boolean
  canAnnotate?: boolean
  canEditContent?: boolean
  editMode?: RichTextDocumentFormat | 'source' | null
  sourcePolicy?: 'owned' | 'derived' | 'external'
}

export interface RichTextDocument {
  id: string
  title: string
  content: string
  format: RichTextDocumentFormat
  revision?: string
  capabilities: RichTextDocumentCapabilities
}

export interface RichTextOutlineItem {
  id: string
  title: string
  level: number
}

export interface RichTextSelection {
  quoteText: string
  prefixText: string
  suffixText: string
  startOffset: number
  endOffset: number
}

function escapeHtml(value: string) {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;')
}

interface MathToken {
  placeholder: string
  expression: string
  displayMode: boolean
}

function decodeHtmlText(value: string) {
  const element = globalThis.document?.createElement('textarea')
  if (!element) return value
  element.innerHTML = value.replace(/<[^>]+>/g, '')
  return element.value
}

function normalizeLatex(value: string) {
  return decodeHtmlText(value).trim().replace(/\\([_*])/g, '$1')
}

function renderFormula(expression: string, displayMode: boolean) {
  const normalized = normalizeLatex(expression)
  if (!normalized) return ''
  return katex.renderToString(normalized, {
    displayMode,
    throwOnError: false,
    strict: 'ignore',
    trust: false,
    output: 'htmlAndMathml',
  })
}

function renderHtmlMath(source: string) {
  return source.replace(
    /<(div|span)\b([^>]*)>([\s\S]*?)<\/\1>/gi,
    (full, tag: string, attributes: string, expression: string) => {
      if (!/\bclass\s*=\s*["'][^"']*\bmath\b[^"']*["']/i.test(attributes)) return full
      const rendered = renderFormula(expression, tag.toLowerCase() === 'div')
      return rendered || full
    },
  )
}

function extractMarkdownMath(source: string) {
  const tokens: MathToken[] = []
  const stash = (expression: string, displayMode: boolean) => {
    const placeholder = `CODEYUNMATH${tokens.length}PLACEHOLDER`
    tokens.push({ placeholder, expression, displayMode })
    return placeholder
  }
  let markdown = source.replace(/\$\$([\s\S]*?)\$\$|\\\[([\s\S]*?)\\\]/g, (_full, dollars, brackets) => (
    `\n${stash(dollars ?? brackets ?? '', true)}\n`
  ))
  markdown = markdown.replace(/\$([^$\n]+?)\$|\\\(([^\n]+?)\\\)/g, (_full, dollars, parentheses) => (
    stash(dollars ?? parentheses ?? '', false)
  ))
  return { markdown, tokens }
}

function renderMarkdown(source: string) {
  const { markdown, tokens } = extractMarkdownMath(source)
  let html = marked.parse(markdown, { async: false, gfm: true, breaks: false })
  for (const token of tokens) {
    const rendered = renderFormula(token.expression, token.displayMode)
    if (token.displayMode) {
      html = html.replace(`<p>${token.placeholder}</p>`, rendered)
    }
    html = html.replaceAll(token.placeholder, rendered)
  }
  return html
}

/**
 * 图书馆与星图笔记共享 HTML 作为富文本展示语义。
 * 各内容源只负责把自己的原始格式适配成安全 HTML；保存仍由内容源自行实现。
 */
export function renderRichTextDocument(document: RichTextDocument | null) {
  if (!document) return ''

  let html = ''
  if (document.format === 'markdown') {
    html = renderMarkdown(document.content)
  } else if (document.format === 'plain-text') {
    html = `<pre>${escapeHtml(document.content)}</pre>`
  } else {
    html = renderHtmlMath(document.content)
  }
  return addHeadingAnchors(DOMPurify.sanitize(html))
}

function headingSlug(title: string, index: number) {
  const slug = title
    .normalize('NFKC')
    .trim()
    .toLocaleLowerCase()
    .replace(/[^\p{Letter}\p{Number}]+/gu, '-')
    .replace(/^-+|-+$/g, '')
  return slug || `section-${index + 1}`
}

function addHeadingAnchors(html: string) {
  const template = globalThis.document?.createElement('template')
  if (!template) return html
  template.innerHTML = html
  const usedIds = new Set<string>()
  for (const [index, heading] of Array.from(template.content.querySelectorAll<HTMLElement>('h1, h2, h3, h4, h5, h6')).entries()) {
    const baseId = heading.id.trim() || headingSlug(heading.textContent ?? '', index)
    let nextId = baseId
    let suffix = 2
    while (usedIds.has(nextId)) {
      nextId = `${baseId}-${suffix}`
      suffix += 1
    }
    heading.id = nextId
    usedIds.add(nextId)
  }
  return template.innerHTML
}

export function extractRichTextOutline(document: RichTextDocument | null): RichTextOutlineItem[] {
  const template = globalThis.document?.createElement('template')
  if (!template || !document) return []
  template.innerHTML = renderRichTextDocument(document)
  return Array.from(template.content.querySelectorAll<HTMLElement>('h1, h2, h3, h4, h5, h6'))
    .map((heading) => ({
      id: heading.id,
      title: (heading.textContent ?? '').trim(),
      level: Number(heading.tagName.slice(1)),
    }))
    .filter((item) => item.id && item.title)
}
