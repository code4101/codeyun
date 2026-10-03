import DOMPurify from 'dompurify'

function dataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(blob)
  })
}
/** Each book owns a deduplicated resource downloader. Rewritten HTML contains
 * only embedded media; any required resource failure aborts the entire package.
 * Rendering happens in a script-disabled iframe, so book CSS stays in the book. */
export function createOfflineHtml(fetchBlob: (url: string) => Promise<Blob>, onResource: (url: string) => void) {
  const resources = new Map<string, Promise<string>>()
  async function embed(raw: string, base: string): Promise<string> {
    if (!raw || raw.startsWith('#') || raw.startsWith('data:')) return raw
    const url = new URL(raw, base).href
    if (!/^https?:|^blob:/.test(url)) throw new Error(`无法离线保存资源：${raw.slice(0, 80)}`)
    let pending = resources.get(url)
    if (!pending) {
      onResource(url)
      pending = fetchBlob(url).then(dataUrl)
      resources.set(url, pending)
    }
    return pending
  }
  async function css(source: string, base: string, ancestors: string[] = []): Promise<string> {
    // Imported stylesheets can contain relative images and fonts of their own.
    const imports = [...source.matchAll(/@import\s+(?:url\(\s*)?["']?([^\s"'();]+)["']?\s*\)?([^;]*);/gi)]
    for (const match of imports) {
      const url = new URL(match[1]!, base).href
      if (ancestors.includes(url) || ancestors.length >= 8) throw new Error('书籍样式存在循环引用，无法完整缓存')
      const nested = await css(await (await fetchBlob(url)).text(), url, [...ancestors, url])
      const media = match[2]!.trim()
      source = source.replace(match[0], media ? `@media ${media}{${nested}}` : nested)
    }
    const matches = [...source.matchAll(/url\(\s*(["']?)(.*?)\1\s*\)/gi)]
    for (const match of matches) source = source.replace(match[0], `url("${await embed(match[2]!, base)}")`)
    return source
  }
  return async function localize(html: string, base = window.location.href): Promise<string> {
    const original = new DOMParser().parseFromString(html, 'text/html')
    if (original.querySelector('iframe[src], object[data], embed[src]')) {
      throw new Error('书中包含嵌入式外部页面，无法保证完整离线，请先将其转换为正文')
    }
    const clean = DOMPurify.sanitize(html, {
      WHOLE_DOCUMENT: true, ADD_TAGS: ['style', 'link'], ADD_ATTR: ['rel'],
      FORBID_TAGS: ['script', 'iframe', 'object', 'embed', 'form', 'input', 'button', 'base', 'meta'],
    })
    const parsed = new DOMParser().parseFromString(clean, 'text/html')
    for (const link of Array.from(parsed.querySelectorAll<HTMLLinkElement>('link'))) {
      if (link.rel !== 'stylesheet' || !link.getAttribute('href')) { link.remove(); continue }
      const url = new URL(link.getAttribute('href')!, base).href
      const style = parsed.createElement('style')
      style.textContent = await css(await (await fetchBlob(url)).text(), url, [url])
      link.replaceWith(style)
    }
    for (const style of Array.from(parsed.querySelectorAll('style'))) style.textContent = await css(style.textContent ?? '', base)
    // Resource variants are alternate encodings of the same image: select one
    // complete source and remove network-dependent browser source selection.
    for (const node of Array.from(parsed.querySelectorAll<HTMLElement>('[srcset]'))) {
      if (!node.getAttribute('src')) {
        const first = (node.getAttribute('srcset') ?? '').trim().split(/\s+/)[0]?.replace(/,$/, '')
        if (first) node.setAttribute('src', first)
      }
      node.removeAttribute('srcset')
    }
    for (const node of Array.from(parsed.querySelectorAll<HTMLElement>('*'))) {
      for (const attribute of ['src', 'poster', 'background', ...(node.localName === 'image' ? ['href', 'xlink:href'] : [])]) {
        const raw = node.getAttribute(attribute)
        if (raw) node.setAttribute(attribute, await embed(raw, base))
      }
      const inline = node.getAttribute('style')
      if (inline) node.setAttribute('style', await css(inline, base))
      if (node.localName === 'a') { node.setAttribute('target', '_blank'); node.setAttribute('rel', 'noopener noreferrer') }
    }
    return `${parsed.head.innerHTML}${parsed.body.innerHTML}`
  }
}
