import {
  renderRichTextPageFootnotes,
  type RichTextFootnoteDefinition,
} from './richTextFootnotes'

export const DYNAMIC_BOOK_CHARACTERS_PER_PAGE = 1000
export const IMAGE_TILE_SIZE = 512
export const IMAGE_HIGH_DETAIL_SHORT_SIDE = 768
export const IMAGE_MAX_SIDE = 2048
export const IMAGE_BASE_TOKENS = 70
export const IMAGE_TILE_TOKENS = 140
export const IMAGE_TOKEN_TO_CHARACTER_RATIO = 0.65
export const SMALL_IMAGE_CHARACTER_SIDE = 16
export const INLINE_MARKER_EQUIVALENT_CHARACTERS = 1

export interface LogicalBookPage {
  html: string
  characterCount: number
  footnoteCharacterCount: number
  localStartOffset: number
  absoluteStartOffset: number
}

interface LogicalBookBlock {
  html: string
  characterCount: number
  isHeading: boolean
  footnoteIds: string[]
}

const ATOMIC_TAGS = new Set([
  'P',
  'H1',
  'H2',
  'H3',
  'H4',
  'H5',
  'H6',
  'PRE',
  'BLOCKQUOTE',
  'TABLE',
  'FIGURE',
  'PICTURE',
  'IMG',
  'SVG',
  'VIDEO',
  'AUDIO',
  'CANVAS',
  'UL',
  'OL',
  'DL',
  'HR',
  'DETAILS',
])

const SPLITTABLE_TEXT_CONTAINER_TAGS = new Set([
  'P',
  'BLOCKQUOTE',
  'UL',
  'OL',
  'DL',
])

const HEADING_TAGS = new Set(['H1', 'H2', 'H3', 'H4', 'H5', 'H6'])
const INLINE_MARKER_IMAGE_PATTERN = /(?:^|[\s_-])(footnote|icon|emoji|badge|avatar)(?:$|[\s_-])/i
const EXPLICIT_ATOMIC_ATTRIBUTE = 'data-book-page-atomic'

function positiveDimension(value: string | null | undefined) {
  const normalized = (value || '').trim()
  if (!/^[\d.]+(?:px)?$/i.test(normalized)) return undefined
  const parsed = Number.parseFloat(normalized)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : undefined
}

function cssPixelDimension(element: Element, property: 'width' | 'height') {
  const match = (element.getAttribute('style') || '').match(
    new RegExp(`(?:^|;)\\s*${property}\\s*:\\s*([\\d.]+)px`, 'i'),
  )
  return positiveDimension(match?.[1])
}

function imageDimensions(element: Element) {
  const width = positiveDimension(element.getAttribute('width'))
    || cssPixelDimension(element, 'width')
  const height = positiveDimension(element.getAttribute('height'))
    || cssPixelDimension(element, 'height')
  if (width && height) return { width, height }

  // Imported articles often omit intrinsic dimensions. A landscape default is
  // closer to their usual rendered size than treating the image as zero text.
  if (width) return { width, height: width * 2 / 3 }
  if (height) return { width: height * 3 / 2, height }
  return { width: 768, height: 512 }
}

function isInlineMarkerImage(element: Element) {
  if (element.closest('sup, sub')) return true
  const descriptor = [
    element.getAttribute('class'),
    element.parentElement?.getAttribute('class'),
    element.getAttribute('role'),
  ].filter(Boolean).join(' ')
  return INLINE_MARKER_IMAGE_PATTERN.test(descriptor)
}

function estimateImageElementEquivalentCharacters(
  image: Element,
  pageCharacters: number,
) {
  if (isInlineMarkerImage(image)) {
    return Math.min(INLINE_MARKER_EQUIVALENT_CHARACTERS, pageCharacters)
  }
  const { width, height } = imageDimensions(image)
  return estimateImageEquivalentCharacters(width, height, pageCharacters)
}

/**
 * Convert an image into an equivalent Chinese-character weight.
 *
 * This follows OpenAI's high-detail vision structure (2048px fit, 768px short
 * side, 512px tiles, base + per-tile cost), then converts tokens to a reading
 * weight. Images smaller than one tile are not enlarged, and one atomic image
 * is capped at one logical page.
 */
export function estimateImageEquivalentCharacters(
  sourceWidth: number,
  sourceHeight: number,
  pageCharacters = DYNAMIC_BOOK_CHARACTERS_PER_PAGE,
) {
  let width = Math.max(1, sourceWidth)
  let height = Math.max(1, sourceHeight)
  if (Math.max(width, height) <= 128) {
    return Math.min(
      Math.max(1, Math.round(
        width * height / SMALL_IMAGE_CHARACTER_SIDE ** 2,
      )),
      Math.max(1, Math.floor(pageCharacters)),
    )
  }
  const fitScale = Math.min(1, IMAGE_MAX_SIDE / Math.max(width, height))
  width *= fitScale
  height *= fitScale

  if (width * height > IMAGE_TILE_SIZE * IMAGE_TILE_SIZE) {
    const detailScale = IMAGE_HIGH_DETAIL_SHORT_SIDE / Math.min(width, height)
    width *= detailScale
    height *= detailScale
  }

  const tiles = Math.ceil(width / IMAGE_TILE_SIZE) * Math.ceil(height / IMAGE_TILE_SIZE)
  const equivalentCharacters = Math.round(
    (IMAGE_BASE_TOKENS + IMAGE_TILE_TOKENS * tiles) * IMAGE_TOKEN_TO_CHARACTER_RATIO,
  )
  return Math.min(
    Math.max(1, equivalentCharacters),
    Math.max(1, Math.floor(pageCharacters)),
  )
}

function normalizedTextCharacterCount(node: Node) {
  return (node.textContent || '').replace(/\s+/g, ' ').trim().length
}

function normalizedInformationCharacterCount(node: Node, targetCharacters: number) {
  const textCharacters = normalizedTextCharacterCount(node)
  if (!(node instanceof Element)) return textCharacters
  const images = node.matches('img') ? [node] : Array.from(node.querySelectorAll('img'))
  return images.reduce((count, image) => {
    return count + estimateImageElementEquivalentCharacters(image, targetCharacters)
  }, textCharacters)
}

/**
 * Estimate the information volume of a whole rich-text document.
 *
 * Unlike reading-page boundaries, this deliberately includes image weight and
 * is suitable for book thickness and total-page estimates.
 */
export function estimateRichTextInformationCharacters(
  html: string,
  targetCharacters = DYNAMIC_BOOK_CHARACTERS_PER_PAGE,
) {
  if (!html || typeof DOMParser === 'undefined') return html.length
  const parsed = new DOMParser().parseFromString(html, 'text/html')
  return normalizedInformationCharacterCount(
    parsed.body,
    Math.max(1, Math.floor(targetCharacters)),
  )
}

/**
 * Reading pages are text-density units. Illustrations stay with the surrounding
 * text, but do not consume that page's character quota.
 */
function normalizedCharacterCount(node: Node, _targetCharacters: number) {
  return normalizedTextCharacterCount(node)
}

function serializeNode(node: Node) {
  const container = node.ownerDocument.createElement('div')
  container.append(node.cloneNode(true))
  return container.innerHTML
}

function nodeFootnoteIds(node: Node) {
  if (!(node instanceof Element)) return []
  const references = node.matches('[data-footnote-id]')
    ? [node]
    : Array.from(node.querySelectorAll('[data-footnote-id]'))
  return Array.from(new Set(
    references
      .map(reference => reference.getAttribute('data-footnote-id') || '')
      .filter(Boolean),
  ))
}

function wrapWithContainer(container: Element, childHtml: string) {
  const clone = container.cloneNode(false) as Element
  clone.innerHTML = childHtml
  return clone.outerHTML
}

function isLayoutTable(element: Element) {
  if (element.tagName !== 'TABLE') return false
  const role = (element.getAttribute('role') || '').toLowerCase()
  if (role === 'presentation' || role === 'none') return true
  if (role === 'table' || element.querySelector('caption, th')) return false
  const rows = Array.from(element.querySelectorAll('tr'))
    .filter(row => row.closest('table') === element)
  const cells = rows.flatMap(row => (
    Array.from(row.children).filter(child => child.matches('td, th'))
  ))
  return rows.length === 1 && cells.length === 1 && cells[0]?.tagName === 'TD'
}

function isExplicitlyAtomic(element: Element) {
  if (!element.hasAttribute(EXPLICIT_ATOMIC_ATTRIBUTE)) return false
  return element.getAttribute(EXPLICIT_ATOMIC_ATTRIBUTE)?.trim().toLowerCase() !== 'false'
}

function splitTextNode(node: Node, targetCharacters: number): LogicalBookBlock[] {
  const source = node.textContent || ''
  if (normalizedCharacterCount(node, targetCharacters) <= targetCharacters) {
    return source.trim()
      ? [{
          html: serializeNode(node),
          characterCount: normalizedCharacterCount(node, targetCharacters),
          isHeading: false,
          footnoteIds: [],
        }]
      : []
  }

  const blocks: LogicalBookBlock[] = []
  let remaining = source
  const preferredBreak = /[。！？!?；;\n]\s*/g
  while (remaining.replace(/\s+/g, ' ').trim().length > targetCharacters) {
    const window = remaining.slice(0, targetCharacters + 1)
    const minimumCut = Math.max(1, Math.floor(targetCharacters * 0.6))
    let cut = targetCharacters
    for (const match of window.matchAll(preferredBreak)) {
      const candidate = (match.index || 0) + match[0].length
      if (candidate >= minimumCut) cut = candidate
    }
    if (cut === targetCharacters) {
      const whitespaceCut = Math.max(window.lastIndexOf(' '), window.lastIndexOf('\n'))
      if (whitespaceCut >= minimumCut) cut = whitespaceCut + 1
    }
    const part = remaining.slice(0, cut)
    const textNode = node.ownerDocument.createTextNode(part)
    blocks.push({
      html: serializeNode(textNode),
      characterCount: normalizedCharacterCount(textNode, targetCharacters),
      isHeading: false,
      footnoteIds: [],
    })
    remaining = remaining.slice(cut)
  }
  if (remaining.trim()) {
    const textNode = node.ownerDocument.createTextNode(remaining)
    blocks.push({
      html: serializeNode(textNode),
      characterCount: normalizedCharacterCount(textNode, targetCharacters),
      isHeading: false,
      footnoteIds: [],
    })
  }
  return blocks
}

function logicalBlocks(node: Node, targetCharacters: number): LogicalBookBlock[] {
  if (node.nodeType === Node.TEXT_NODE) {
    return splitTextNode(node, targetCharacters)
  }
  if (!(node instanceof Element)) return []

  const characterCount = normalizedCharacterCount(node, targetCharacters)
  const isHeading = HEADING_TAGS.has(node.tagName)
  const isSplittableContainer = (
    SPLITTABLE_TEXT_CONTAINER_TAGS.has(node.tagName)
    && characterCount > targetCharacters
  )
  const isAtomic = (
    (ATOMIC_TAGS.has(node.tagName) || isExplicitlyAtomic(node))
    && !isSplittableContainer
    && !isLayoutTable(node)
  )
  if (
    isAtomic
    || characterCount <= targetCharacters
    || !node.childNodes.length
  ) {
    return [{
      html: node.outerHTML,
      characterCount,
      isHeading,
      footnoteIds: nodeFootnoteIds(node),
    }]
  }

  const children = Array.from(node.childNodes)
    .flatMap(child => logicalBlocks(child, targetCharacters))
  if (!children.length) {
    return [{
      html: node.outerHTML,
      characterCount,
      isHeading,
      footnoteIds: nodeFootnoteIds(node),
    }]
  }
  return children.map(block => ({
    ...block,
    html: wrapWithContainer(node, block.html),
  }))
}

function keepHeadingsWithFollowingBlock(blocks: LogicalBookBlock[]) {
  const grouped: LogicalBookBlock[] = []
  for (let index = 0; index < blocks.length; index += 1) {
    const block = blocks[index]
    const next = blocks[index + 1]
    if (block?.isHeading && next) {
      grouped.push({
        html: block.html + next.html,
        characterCount: block.characterCount + next.characterCount,
        isHeading: true,
        footnoteIds: Array.from(new Set([...block.footnoteIds, ...next.footnoteIds])),
      })
      index += 1
    } else if (block) {
      grouped.push(block)
    }
  }
  return grouped
}

/**
 * Split rich-text HTML at semantic block boundaries.
 *
 * Paragraphs, headings, lists, images, figures, tables, and other media blocks
 * remain atomic. A single oversized block therefore occupies one oversized
 * logical page instead of being cut in the middle.
 */
export function paginateRichTextHtml(
  html: string,
  targetCharacters = DYNAMIC_BOOK_CHARACTERS_PER_PAGE,
  footnotes: Record<string, RichTextFootnoteDefinition> = {},
): LogicalBookPage[] {
  if (!html || typeof DOMParser === 'undefined') {
    return [{
      html,
      characterCount: html.length,
      footnoteCharacterCount: 0,
      localStartOffset: 0,
      absoluteStartOffset: 0,
    }]
  }

  const target = Math.max(1, Math.floor(targetCharacters))
  const parsed = new DOMParser().parseFromString(html, 'text/html')
  const blocks = keepHeadingsWithFollowingBlock(
    Array.from(parsed.body.childNodes).flatMap(node => logicalBlocks(node, target)),
  )
  if (!blocks.length) {
    return [{
      html,
      characterCount: 0,
      footnoteCharacterCount: 0,
      localStartOffset: 0,
      absoluteStartOffset: 0,
    }]
  }

  const pages: LogicalBookPage[] = []
  let pageBlocks: LogicalBookBlock[] = []
  let pageCharacterCount = 0
  let pageFootnoteCharacterCount = 0
  let pageFootnoteIds: string[] = []
  let pageStartOffset = 0

  const finishPage = () => {
    if (!pageBlocks.length) return
    const pageHtml = pageBlocks.map(block => block.html).join('')
    const pageDocument = new DOMParser().parseFromString(pageHtml, 'text/html')
    const firstReferenceIds: Record<string, string> = {}
    for (const reference of Array.from(pageDocument.querySelectorAll<HTMLElement>('[data-footnote-id]'))) {
      const footnoteId = reference.dataset.footnoteId || ''
      if (footnoteId && reference.id && !firstReferenceIds[footnoteId]) {
        firstReferenceIds[footnoteId] = reference.id
      }
    }
    const pageFootnotes = pageFootnoteIds
      .map(id => footnotes[id])
      .filter((footnote): footnote is RichTextFootnoteDefinition => Boolean(footnote))
    pages.push({
      html: pageHtml + renderRichTextPageFootnotes(pageFootnotes, firstReferenceIds),
      characterCount: pageCharacterCount,
      footnoteCharacterCount: pageFootnoteCharacterCount,
      localStartOffset: pageStartOffset,
      absoluteStartOffset: 0,
    })
    pageStartOffset += pageCharacterCount
    pageBlocks = []
    pageCharacterCount = 0
    pageFootnoteCharacterCount = 0
    pageFootnoteIds = []
  }

  for (const block of blocks) {
    const knownFootnoteIds = new Set(pageFootnoteIds)
    const newFootnoteIds = block.footnoteIds.filter(id => footnotes[id] && !knownFootnoteIds.has(id))
    const newFootnoteCharacterCount = newFootnoteIds.reduce(
      (sum, id) => sum + (footnotes[id]?.characterCount || 0),
      0,
    )
    const currentTotal = pageCharacterCount + pageFootnoteCharacterCount
    const combinedCount = currentTotal + block.characterCount + newFootnoteCharacterCount
    if (pageBlocks.length && combinedCount > target) {
      const distanceBefore = Math.abs(target - currentTotal)
      const distanceAfter = Math.abs(combinedCount - target)
      if (distanceBefore <= distanceAfter) finishPage()
    }
    const activeFootnoteIds = new Set(pageFootnoteIds)
    const blockNewFootnoteIds = block.footnoteIds.filter(
      id => footnotes[id] && !activeFootnoteIds.has(id),
    )
    pageBlocks.push(block)
    pageCharacterCount += block.characterCount
    pageFootnoteIds.push(...blockNewFootnoteIds)
    pageFootnoteCharacterCount += blockNewFootnoteIds.reduce(
      (sum, id) => sum + (footnotes[id]?.characterCount || 0),
      0,
    )
    if (pageCharacterCount + pageFootnoteCharacterCount >= target) finishPage()
  }
  finishPage()
  return pages
}
