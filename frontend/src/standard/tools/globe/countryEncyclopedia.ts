export interface CountrySummary { text: string; url: string }
const cache = new Map<string, CountrySummary>()

export function encyclopediaLinks(country: string) {
  return {
    wikipedia: `https://zh.wikipedia.org/wiki/${encodeURIComponent(country)}`,
    baidu: `https://baike.baidu.com/item/${encodeURIComponent(country)}`,
  }
}

/** Plain-text extracts only. Redirects resolve common names; disambiguation pages are not introductions. */
export async function loadCountrySummary(country: string, signal: AbortSignal): Promise<CountrySummary | null> {
  const cached = cache.get(country)
  if (cached) return cached
  const query = new URLSearchParams({
    action: 'query', format: 'json', formatversion: '2', origin: '*', redirects: '1',
    prop: 'extracts|info|pageprops', inprop: 'url', ppprop: 'disambiguation',
    exintro: '1', explaintext: '1', exchars: '600', titles: country, variant: 'zh-cn',
  })
  const response = await fetch(`https://zh.wikipedia.org/w/api.php?${query}`, { signal })
  if (!response.ok) throw new Error(`百科请求失败：${response.status}`)
  const data = await response.json()
  if (data.error) throw new Error('百科服务暂不可用')
  const page = data.query?.pages?.[0]
  if (!page || page.missing || page.pageprops?.disambiguation !== undefined || typeof page.extract !== 'string' || !page.extract.trim()) return null
  const summary = { text: page.extract.trim(), url: encyclopediaLinks(country).wikipedia }
  cache.set(country, summary)
  return summary
}
