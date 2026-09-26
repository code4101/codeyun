import type {PdfReadingPage} from '@/api/pdfDocuments'
type Block = PdfReadingPage['blocks'][number]
type Source = {page:number; block:Block}
export type ReadingItem = {key:string; page:number; block:Block|null; sources:Source[]; unresolved?:boolean}

/** Join only adjacent-page continuations; preserve original blocks as source anchors. */
export function readingFlow(pages:Array<PdfReadingPage & {unresolved?:boolean}>):ReadingItem[] {
  const result:ReadingItem[] = []
  for (const page of pages) {
    if (!page.available || page.unresolved) {
      result.push({key:`missing-${page.page}`,page:page.page,block:null,sources:[],unresolved:page.unresolved})
      continue
    }
    const blocks = page.blocks.filter(b => b.kind !== 'marginal')
    blocks.forEach((block,index) => {
      const previous = result[result.length - 1]
      const last = previous?.block
      const tail = previous?.sources[previous.sources.length - 1]
      if (index === 0 && last?.kind === 'paragraph' && block.kind === 'paragraph'
        && tail?.page === page.page - 1 && block.indent_em === 0
        && !/[。！？.!?：:][”’」』）)]?\s*$/.test(last.text)
        && Math.abs(last.font_scale - block.font_scale) <= .2) {
        const separator = /[A-Za-z0-9]$/.test(last.text) && /^[A-Za-z0-9]/.test(block.text) ? ' ' : ''
        previous.block = {...last,text:last.text + separator + block.text}
        previous.sources.push({page:page.page,block})
      } else result.push({key:`${page.page}-${block.id}`,page:page.page,block:{...block},sources:[{page:page.page,block}]})
    })
  }
  return result
}
