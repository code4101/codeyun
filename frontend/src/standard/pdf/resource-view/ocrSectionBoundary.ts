import type {PdfReadingPage} from '@/api/pdfDocuments'

export function titleKey(text:string) {
  return text.normalize('NFKC').replace(/[\s\p{P}]/gu, '').toLowerCase()
}
/** Match a complete block, never an incidental mention inside prose. */
export function trimReadingPage(page:PdfReadingPage, startTitle?:string, endTitle?:string) {
  let blocks = page.blocks
  let unresolved = false
  if (page.available && startTitle) {
    const index = blocks.findIndex(b => b.kind !== 'marginal' && titleKey(b.text) === titleKey(startTitle))
    if (index < 0) { blocks = []; unresolved = true }
    else blocks = blocks.slice(index + 1) // The reader already displays the section heading.
  }
  if (page.available && endTitle) {
    const index = blocks.findIndex(b => b.kind !== 'marginal' && titleKey(b.text) === titleKey(endTitle))
    if (index < 0) { blocks = []; unresolved = true }
    else blocks = blocks.slice(0, index)
  }
  return {...page, blocks, unresolved}
}
