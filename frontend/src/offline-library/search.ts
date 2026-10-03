import type { OfflineSection } from './store'
export interface OfflineSearchHit { section: number; title: string; snippet: string; offset: number }
/** Match across PDF line breaks, just as the online book search does. Offsets
 * remain positions in the original text for scrolling and progress restore. */
export function searchOfflineSections(sections: OfflineSection[], query: string, limit = 200): OfflineSearchHit[] {
  const term = query.toLowerCase().replace(/\s/g, '')
  if (!term) return []
  const hits: OfflineSearchHit[] = []
  for (const [section, item] of sections.entries()) {
    const positions: number[] = []
    let corpus = ''
    for (let i = 0; i < item.text.length; i++) {
      const value = item.text[i]!.toLowerCase()
      if (/\s/.test(value)) continue
      corpus += value
      for (let n = 0; n < value.length; n++) positions.push(i)
    }
    let at = 0
    while ((at = corpus.indexOf(term, at)) >= 0) {
      const offset = positions[at]!
      hits.push({ section, title: item.title, offset, snippet: item.text.slice(Math.max(0, offset - 35), offset + term.length + 65) })
      if (hits.length >= limit) return hits
      at += Math.max(1, term.length)
    }
  }
  return hits
}
