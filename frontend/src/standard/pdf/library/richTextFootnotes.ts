export interface RichTextFootnoteDefinition {
  id: string
  sourceId: string
  ordinal: number
  label: string
  html: string
  characterCount: number
}

export interface RichTextFootnoteDiagnostic {
  kind: 'missing-definition' | 'duplicate-definition'
  sourceId: string
}

export interface NormalizedRichTextFootnotes {
  bodyHtml: string
  footnotes: Record<string, RichTextFootnoteDefinition>
  diagnostics: RichTextFootnoteDiagnostic[]
}

const FOOTNOTE_CLASS_PATTERN = /(?:^|[\s_-])footnote(?:$|[\s_-])/i
const FOOTNOTE_CONTAINER_TAGS = new Set(['ASIDE', 'OL', 'UL', 'SECTION'])

function semanticTokens(element: Element, attribute: string) {
  return (element.getAttribute(attribute) || '')
    .toLocaleLowerCase()
    .split(/\s+/)
    .filter(Boolean)
}

function hasSemanticToken(element: Element, attribute: string, token: string) {
  return semanticTokens(element, attribute).includes(token)
}

function hasFootnoteClass(element: Element) {
  return FOOTNOTE_CLASS_PATTERN.test(element.getAttribute('class') || '')
}

function isExplicitFootnoteReference(anchor: HTMLAnchorElement) {
  return hasSemanticToken(anchor, 'epub:type', 'noteref')
    || hasSemanticToken(anchor, 'role', 'doc-noteref')
    || anchor.classList.contains('duokan-footnote')
}

function isExplicitFootnoteDefinition(element: Element) {
  return hasSemanticToken(element, 'epub:type', 'footnote')
    || hasSemanticToken(element, 'role', 'doc-footnote')
    || element.classList.contains('duokan-footnote-item')
}

function sourceTargetId(anchor: HTMLAnchorElement) {
  const href = anchor.getAttribute('href') || ''
  if (!href.startsWith('#') || href.length < 2) return ''
  try {
    return decodeURIComponent(href.slice(1))
  } catch {
    return href.slice(1)
  }
}

function looksLikeFootnoteImage(anchor: HTMLAnchorElement) {
  const images = Array.from(anchor.querySelectorAll('img'))
  if (images.length !== 1 || anchor.textContent?.trim()) return false
  const image = images[0]
  const descriptor = [
    image?.getAttribute('alt'),
    image?.getAttribute('class'),
    anchor.getAttribute('class'),
  ].filter(Boolean).join(' ')
  return /(?:注|footnote|note)/i.test(descriptor)
}

function scopedId(scope: string, suffix: string) {
  const normalizedScope = scope
    .normalize('NFKC')
    .replace(/[^\p{Letter}\p{Number}_-]+/gu, '-')
    .replace(/^-+|-+$/g, '')
  return `footnote-${normalizedScope || 'document'}-${suffix}`
}

function normalizedTextLength(html: string) {
  const parsed = new DOMParser().parseFromString(html, 'text/html')
  return (parsed.body.textContent || '').replace(/\s+/g, ' ').trim().length
}

function unwrapSourceBacklinks(element: Element, referenceSourceIds: Set<string>) {
  for (const anchor of Array.from(element.querySelectorAll<HTMLAnchorElement>('a[href^="#"]'))) {
    if (!referenceSourceIds.has(sourceTargetId(anchor))) continue
    anchor.replaceWith(...Array.from(anchor.childNodes))
  }
}

function removableFootnoteContainers(element: Element) {
  const containers: Element[] = []
  let current = element.parentElement
  while (current && current.tagName !== 'BODY') {
    if (
      FOOTNOTE_CONTAINER_TAGS.has(current.tagName)
      && (
        hasFootnoteClass(current)
        || hasSemanticToken(current, 'epub:type', 'footnote')
        || hasSemanticToken(current, 'epub:type', 'footnotes')
        || current.tagName === 'ASIDE'
      )
    ) {
      containers.push(current)
    }
    current = current.parentElement
  }
  return containers
}

function isVisuallyEmpty(element: Element) {
  return !(element.textContent || '').trim()
    && !element.querySelector('img, picture, svg, video, audio, table, hr')
}

export function normalizeRichTextFootnotes(
  html: string,
  scope = 'document',
): NormalizedRichTextFootnotes {
  if (!html || typeof DOMParser === 'undefined') {
    return { bodyHtml: html, footnotes: {}, diagnostics: [] }
  }

  const parsed = new DOMParser().parseFromString(html, 'text/html')
  const elementsById = new Map<string, Element>()
  const duplicateIds = new Set<string>()
  for (const element of Array.from(parsed.body.querySelectorAll<HTMLElement>('[id]'))) {
    const id = element.id.trim()
    if (!id) continue
    if (elementsById.has(id)) duplicateIds.add(id)
    else elementsById.set(id, element)
  }

  const anchors = Array.from(parsed.body.querySelectorAll<HTMLAnchorElement>('a[href^="#"]'))
  const referenceCandidates = anchors
    .map(anchor => ({
      anchor,
      sourceId: sourceTargetId(anchor),
    }))
    .filter(({ anchor, sourceId }) => {
      if (!sourceId) return false
      const target = elementsById.get(sourceId)
      if (isExplicitFootnoteReference(anchor)) return true
      if (!anchor.closest('sup') || !target) return false
      return isExplicitFootnoteDefinition(target)
        || Boolean(target.closest('aside'))
        || looksLikeFootnoteImage(anchor)
    })

  const referencedSourceIds = new Set(referenceCandidates.map(reference => reference.sourceId))
  const referenceSourceIds = new Set(
    referenceCandidates
      .map(({ anchor }) => anchor.id.trim())
      .filter(Boolean),
  )
  const definitions = new Map<string, RichTextFootnoteDefinition>()
  const diagnostics: RichTextFootnoteDiagnostic[] = Array.from(duplicateIds)
    .filter(id => referencedSourceIds.has(id))
    .map(sourceId => ({ kind: 'duplicate-definition', sourceId }))
  let nextOrdinal = 1
  let nextReferenceIndex = 1

  for (const { anchor, sourceId } of referenceCandidates) {
    let definition = definitions.get(sourceId)
    if (!definition) {
      const sourceDefinition = elementsById.get(sourceId)
      if (sourceDefinition) {
        const clone = sourceDefinition.cloneNode(true) as Element
        clone.removeAttribute('id')
        unwrapSourceBacklinks(clone, referenceSourceIds)
        const ordinal = nextOrdinal
        nextOrdinal += 1
        definition = {
          id: scopedId(scope, String(ordinal)),
          sourceId,
          ordinal,
          label: `[${ordinal}]`,
          html: clone.innerHTML.trim(),
          characterCount: Math.max(1, normalizedTextLength(clone.innerHTML)),
        }
        definitions.set(sourceId, definition)
      } else {
        const ordinal = nextOrdinal
        nextOrdinal += 1
        const marker = parsed.createElement('span')
        marker.className = 'rich-text-footnote-ref is-unresolved'
        marker.textContent = `[${ordinal}]`
        marker.setAttribute('aria-label', `脚注 ${ordinal} 的正文缺失`)
        anchor.replaceWith(marker)
        diagnostics.push({ kind: 'missing-definition', sourceId })
        continue
      }
    }

    const referenceId = scopedId(scope, `ref-${nextReferenceIndex}`)
    nextReferenceIndex += 1
    const normalizedAnchor = parsed.createElement('a')
    normalizedAnchor.id = referenceId
    normalizedAnchor.className = 'rich-text-footnote-ref'
    normalizedAnchor.href = `#${definition.id}`
    normalizedAnchor.dataset.footnoteId = definition.id
    normalizedAnchor.textContent = definition.label
    normalizedAnchor.setAttribute('aria-label', `查看脚注 ${definition.ordinal}`)
    anchor.replaceWith(normalizedAnchor)
  }

  const removableContainers = new Set<Element>()
  for (const sourceId of definitions.keys()) {
    const sourceDefinition = elementsById.get(sourceId)
    if (!sourceDefinition) continue
    removableFootnoteContainers(sourceDefinition).forEach(element => removableContainers.add(element))
    sourceDefinition.remove()
  }
  for (const container of Array.from(removableContainers).reverse()) {
    if (container.isConnected && isVisuallyEmpty(container)) container.remove()
  }

  return {
    bodyHtml: parsed.body.innerHTML,
    footnotes: Object.fromEntries(
      Array.from(definitions.values()).map(definition => [definition.id, definition]),
    ),
    diagnostics,
  }
}

function escapeHtmlAttribute(value: string) {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('"', '&quot;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
}

export function renderRichTextPageFootnotes(
  footnotes: RichTextFootnoteDefinition[],
  firstReferenceIds: Record<string, string>,
) {
  if (!footnotes.length) return ''
  const items = footnotes.map((footnote) => {
    const referenceId = firstReferenceIds[footnote.id] || ''
    const label = referenceId
      ? `<a class="rich-text-footnote-backref" href="#${escapeHtmlAttribute(referenceId)}" aria-label="返回脚注 ${footnote.ordinal} 的引用">${footnote.label}</a>`
      : `<span class="rich-text-footnote-label">${footnote.label}</span>`
    return [
      `<li id="${escapeHtmlAttribute(footnote.id)}" tabindex="-1">`,
      label,
      `<div class="rich-text-footnote-content">${footnote.html}</div>`,
      '</li>',
    ].join('')
  }).join('')
  return [
    '<section class="rich-text-page-footnotes" aria-label="本页脚注">',
    `<ol>${items}</ol>`,
    '</section>',
  ].join('')
}
