/** Real Chromium + IndexedDB + service worker. No request stubs remain during
 * the airplane-mode/reopened-tab checks. Run against CodeYun's Vite frontend. */
import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequire } from 'node:module'
import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import os from 'node:os'
const require = createRequire(import.meta.url)
const { chromium } = require(process.env.CODEYUN_PLAYWRIGHT_MODULE || 'playwright')
const base = process.env.CODEYUN_TEST_BASE_URL || 'http://localhost:5173'
const output = path.join(os.tmpdir(), 'codeyun', 'offline-library-tests')
const fakeToken = user => `e30.${Buffer.from(JSON.stringify({ sub: String(user), scope: 'user-session' })).toString('base64url')}.fixture`
const image = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+afo4AAAAASUVORK5CYII=', 'base64')
function fixturePdf() {
  const objects = [null, '<< /Type /Catalog /Pages 2 0 R >>', '<< /Type /Pages /Kids [3 0 R 5 0 R 7 0 R] /Count 3 >>']
  for (let page = 1; page <= 3; page++) {
    objects.push(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 500] /Resources << /Font << /F1 9 0 R >> >> /Contents ${page * 2 + 2} 0 R >>`)
    const content = `BT /F1 20 Tf 30 450 Td (Page ${page}: ${page === 3 ? 'hidden pineapple chapter' : 'complete offline reading'}) Tj ET`
    objects.push(`<< /Length ${Buffer.byteLength(content)} >>\nstream\n${content}\nendstream`)
  }
  objects.push('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>')
  let body = '%PDF-1.4\n', offsets = [0]
  for (let number = 1; number < objects.length; number++) { offsets.push(Buffer.byteLength(body)); body += `${number} 0 obj\n${objects[number]}\nendobj\n` }
  const start = Buffer.byteLength(body)
  body += `xref\n0 ${objects.length}\n0000000000 65535 f \n${offsets.slice(1).map(offset => String(offset).padStart(10, '0') + ' 00000 n ').join('\n')}\ntrailer\n<< /Size ${objects.length} /Root 1 0 R >>\nstartxref\n${start}\n%%EOF`
  return Buffer.from(body)
}
const pdfData = fixturePdf()
const ebook = {
  id: 'fixture-ebook', title: '离线电子书验证', revision: 'ebook-v1', reading_state: null,
  content_html: `<link rel="stylesheet" href="/api/fixture.css"><article data-article-id="chapter-1" id="chapter-1"><h1>第一章</h1><p>开篇内容。</p><img src="/api/image.png"></article><article data-article-id="chapter-2" id="chapter-2"><h1>第二章</h1>${'<p>长章节阅读进度验证。每一段都需要完整缓存。</p>'.repeat(100)}<img src="/api/image.png"></article><article data-article-id="chapter-3" id="chapter-3"><h1>未读章节</h1><p>最后一章藏着独角鲸搜索验证。</p><svg width="20" height="20"><image href="/api/image.png" width="20" height="20" /></svg><a href="#appendix">附录跳转</a></article><section id="appendix">全书附录与脚注也不能遗漏。</section>`,
  toc: [1, 2, 3].map(number => ({ anchor: `chapter-${number}`, title: number === 3 ? '未读章节' : `第${number}章`, level: 1 })),
}
async function fixtures(page, state) {
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const url = new URL(route.request().url()), pathname = url.pathname
    const json = data => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(data) })
    if (pathname === '/api/image.png') return route.fulfill({ status: state.failImage ? 503 : 200, contentType: 'image/png', body: image })
    if (pathname === '/api/fixture.css') return route.fulfill({ status: 200, contentType: 'text/css', body: 'h1{color:rgb(10,70,30)}p{background-image:url("/api/image.png");background-repeat:no-repeat}' })
    if (route.request().method() === 'PUT') { state.syncs.push({ pathname, body: route.request().postDataJSON() }); return json({}) }
    if (pathname === '/api/pdf-documents/42') return json({ my_state: { current_page: 1, zoom: '175%', sidebar_open: false, state_json: { preserve: 'online preferences' } } })
    if (pathname.endsWith('/offline-metadata')) return json({ document: { title: '离线 PDF 验证', content_hash: 'pdf-v1', updated_at: 1, my_state: null }, outline: { entries: [{ id: 'last', title: '未读 PDF 章节', page: 3, level: 0 }], revision: 'v1' }, notes: [{ page_number: 3, content_html: '<p>完整保存的页面笔记</p><img src="/api/image.png">' }], reading_pages: [{ page: 2, available: true, blocks: [{ text: '扫描页已有OCR星河验证' }] }] })
    if (pathname.endsWith('/content-url')) return json({ url: '/api/fixture.pdf' })
    if (pathname === '/api/fixture.pdf') return route.fulfill({ status: 200, contentType: 'application/pdf', body: pdfData })
    if (pathname === '/api/linux-do-books/fixture-ebook') return json({ ...ebook, ...(state.failImage ? { revision: 'ebook-v2', title: '不应替换完整副本' } : {}) })
    if (pathname === '/api/linux-do-books/broken-ebook') return json({ ...ebook, id: 'broken-ebook', title: '不应出现在离线书架' })
    if (pathname === '/api/library-annotations') return json([{ chapter_id: 'chapter-3', quote_text: '离线保留的划线', comment_text: '离线保留的批注', color: 'yellow' }])
    if (pathname === '/api/skill-books/local/catalog') return json({ title: '离线技能书验证', asset_id: 'fixture-skill', revision: 'skill-v1', skills: [{ chapters: [{ id: 'main', title: '技能正文', revision: 'main-v1', kind: 'main' }, { id: 'reference', title: '技能参考', revision: 'ref-v1', kind: 'reference' }] }] })
    if (pathname.includes('/api/skill-books/local/chapters/')) {
      const id = pathname.split('/').at(-1)
      return json({ chapter: { revision: id === 'main' ? 'main-v1' : 'ref-v1' }, markdown: `# ${id}\n\n${id === 'main' ? 'Original skill chapter' : 'Reference abyssal lantern'}\n\n![完整图片](/api/image.png)`, translation: { status: id === 'main' ? 'done' : 'not_needed', markdown: id === 'main' ? '# 已有中文译文\n\n技能翻译也完整缓存' : '', revision: 'zh-v1' } })
    }
    if (pathname === '/api/skill-books/local/my-state') return json({ chapter_id: 'main', character_offset: 0 })
    return route.fulfill({ status: 404, body: 'Unexpected fixture endpoint: ' + pathname })
  })
}
async function download(page, target) {
  return page.evaluate(async target => {
    const { downloadOfflineBook } = await import('/src/offline-library/download.ts')
    const saved = await downloadOfflineBook(target, message => { window.lastDownloadStatus = message })
    return { key: saved.key, bytes: saved.bytes, sections: saved.sections.length }
  }, target)
}
async function disableHttpCache(context, page, offline = false) {
  const session = await context.newCDPSession(page)
  await session.send('Network.enable')
  await session.send('Network.setCacheDisabled', { cacheDisabled: true })
  await session.send('Network.emulateNetworkConditions', { offline, latency: 0, downloadThroughput: -1, uploadThroughput: -1 })
  return session
}

test('whole-book downloads survive airplane mode, new tabs, searches, images, notes, account changes and failed replacements', { timeout: 240000 }, async () => {
  await mkdir(output, { recursive: true })
  const browser = await chromium.launch({ executablePath: process.env.CODEYUN_CHROME_EXECUTABLE, headless: true })
  const context = await browser.newContext({ viewport: { width: 1200, height: 850 }, serviceWorkers: 'allow' })
  let page = await context.newPage()
  const errors = []
  context.on('page', p => p.on('pageerror', error => errors.push(error.message)))
  page.on('pageerror', error => errors.push(error.message))
  const state = { failImage: false, syncs: [] }
  try {
    await disableHttpCache(context, page)
    await page.goto(`${base}/offline-library/`)
    await page.evaluate(token => localStorage.setItem('token', token), fakeToken(9091))
    await fixtures(page, state)
    const ebookResult = await download(page, { kind: 'ebook', id: 'fixture-ebook' })
    assert.equal(ebookResult.sections, 4) // including outside-article footnotes
    const pdfResult = await download(page, { kind: 'pdf', id: '42' })
    assert.equal(pdfResult.sections, 3)
    const skillResult = await download(page, { kind: 'skill', id: 'fixture-skill', bookshelfId: 'shelf' })
    assert.equal(skillResult.sections, 3) // original, translation, reference
    const runtime = await page.evaluate(async () => ({ manifest: await (await fetch('/offline-library/runtime-manifest.json')).json(), keys: (await Promise.all((await caches.keys()).map(async name => (await (await caches.open(name)).keys()).map(r => new URL(r.url).pathname)))).flat() }))
    assert.ok(runtime.manifest.assets.length > 170)
    for (const url of runtime.manifest.assets) assert.ok(runtime.keys.includes(url), `Missing cached runtime ${url}`)
    const quotaResult = await page.evaluate(async key => {
      const store = await import('/src/offline-library/store.ts')
      const original = await store.readOfflineBook(key)
      const put = IDBObjectStore.prototype.put
      IDBObjectStore.prototype.put = function (...args) {
        if (this.name === 'catalogue') throw new DOMException('Storage quota exceeded', 'QuotaExceededError')
        return put.apply(this, args)
      }
      let failed = false
      try { await store.saveOfflineBook({ ...original, title: 'Quota failure must not replace the book' }) }
      catch (error) { failed = error.name === 'QuotaExceededError' }
      finally { IDBObjectStore.prototype.put = put }
      return { failed, title: (await store.readOfflineBook(key)).title, catalogue: (await store.listOfflineBooks()).find(item => item.key === key).title }
    }, ebookResult.key)
    assert.deepEqual(quotaResult, { failed: true, title: '离线电子书验证', catalogue: '离线电子书验证' })
    await assert.rejects(page.evaluate(async () => {
      const { downloadOfflineBook } = await import('/src/offline-library/download.ts')
      const controller = new AbortController(); controller.abort()
      await downloadOfflineBook({ kind: 'ebook', id: 'cancelled-ebook' }, () => {}, controller.signal)
    }), /aborted|AbortError/i)

    state.failImage = true
    await assert.rejects(download(page, { kind: 'ebook', id: 'fixture-ebook' }), /503/)
    await assert.rejects(download(page, { kind: 'ebook', id: 'broken-ebook' }), /503/)
    assert.deepEqual(await page.evaluate(async () => (await (await import('/src/offline-library/store.ts')).listOfflineBooks()).map(b => b.title).sort()), ['离线 PDF 验证', '离线技能书验证', '离线电子书验证'].sort())
    state.failImage = false
    // IMPORTANT: offline validation has no fulfilled API mocks and HTTP cache
    // is disabled. Only the installed runtime and IndexedDB can satisfy reads.
    await page.unrouteAll({ behavior: 'wait' })
    await context.setOffline(true)
    await page.close()
    page = await context.newPage()
    const offlineNetwork = await disableHttpCache(context, page, true)
    await page.goto(`${base}/offline-library/?book=${encodeURIComponent(ebookResult.key)}`)
    await page.getByRole('status').filter({ hasText: '离线' }).waitFor()
    await page.getByRole('button', { name: '未读章节', exact: true }).click()
    const chapter = page.frameLocator('iframe[title="离线书籍正文"]')
    await chapter.getByText('最后一章藏着独角鲸搜索验证。').waitFor()
    await chapter.locator('svg image').waitFor()
    assert.match(await chapter.locator('svg image').getAttribute('href'), /^data:image\/png/)
    await page.getByRole('button', { name: '搜索全书', exact: true }).click()
    await page.getByRole('searchbox', { name: '搜索全书' }).fill('独角鲸')
    await page.getByRole('list', { name: '全文搜索结果' }).getByRole('button').click()
    await chapter.locator('mark').filter({ hasText: '独角鲸' }).waitFor()
    await chapter.getByRole('link', { name: '附录跳转' }).click()
    await chapter.getByText('全书附录与脚注也不能遗漏。').waitFor()
    await page.getByRole('button', { name: '目录', exact: true }).click()
    await page.getByRole('button', { name: '未读章节', exact: true }).click()
    await page.getByRole('button', { name: /批注 1/ }).click()
    await page.getByText('离线保留的批注', { exact: true }).waitFor()
    await page.screenshot({ path: path.join(output, 'ebook-offline.png') })
    await page.getByRole('button', { name: '目录', exact: true }).click()
    await page.getByRole('button', { name: '第2章', exact: true }).click()
    await chapter.locator('img').waitFor()
    assert.ok(await chapter.locator('img').evaluate(img => img.complete && img.naturalWidth > 0))
    await chapter.locator('body').evaluate(body => body.ownerDocument.defaultView.scrollTo(0, 1500))
    await page.waitForTimeout(500)
    await page.reload()
    await chapter.locator('img').waitFor()
    await page.waitForTimeout(200)
    assert.ok(await chapter.locator('body').evaluate(body => body.ownerDocument.defaultView.scrollY) > 900)
    assert.equal(await page.getByRole('spinbutton', { name: '跳转页或章节' }).inputValue(), '2')

    await page.goto(`${base}/offline-library/?book=${encodeURIComponent(pdfResult.key)}`)
    await page.getByRole('button', { name: '未读 PDF 章节', exact: true }).click()
    const renderedCanvas = page.locator('canvas')
    await page.waitForFunction(() => { const canvas = document.querySelector('canvas'); return canvas?.width > 0 && !document.body.textContent.includes('正在渲染') })
    await page.waitForFunction(() => { const canvas = document.querySelector('canvas'); return canvas?.width > 0 && canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data.some(value => value > 0) }, null, { timeout: 10000 })
    await page.getByRole('button', { name: '已有笔记', exact: true }).click()
    await page.frameLocator('iframe[title="已有页面笔记"]').getByText('完整保存的页面笔记').waitFor()
    await page.getByRole('button', { name: '搜索全书', exact: true }).click()
    await page.getByRole('searchbox', { name: '搜索全书' }).fill('pineapple')
    await page.getByRole('list', { name: '全文搜索结果' }).getByRole('button').click()
    await page.getByRole('button', { name: '查看文本', exact: true }).click()
    await page.locator('mark').filter({ hasText: 'pineapple' }).waitFor()
    await page.getByRole('searchbox', { name: '搜索全书' }).fill('OCR星河')
    await page.getByRole('list', { name: '全文搜索结果' }).getByRole('button').click()
    await page.getByRole('region', { name: '书籍正文' }).getByText('扫描页已有', { exact: false }).waitFor()
    await page.screenshot({ path: path.join(output, 'pdf-offline.png') })

    await page.goto(`${base}/offline-library/?book=${encodeURIComponent(skillResult.key)}`)
    await page.getByRole('button', { name: '技能参考', exact: true }).click()
    await page.frameLocator('iframe[title="离线书籍正文"]').getByText('Reference abyssal lantern').waitFor()
    await page.getByRole('button', { name: '技能正文（中文译文）', exact: true }).click()
    await page.frameLocator('iframe[title="离线书籍正文"]').getByText('技能翻译也完整缓存').waitFor()
    await page.setViewportSize({ width: 390, height: 844 })
    await page.getByRole('button', { name: '目录 / 搜索', exact: true }).click()
    await page.frameLocator('iframe[title="离线书籍正文"]').getByText('技能翻译也完整缓存').waitFor()
    await page.screenshot({ path: path.join(output, 'mobile-offline.png') })
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    await page.getByRole('button', { name: '离线书架', exact: true }).click()
    await page.getByRole('button', { name: '离线电子书验证', exact: true }).waitFor()
    assert.equal(await page.locator('.books li').count(), 3)
    await page.evaluate(token => localStorage.setItem('token', token), fakeToken(9092))
    await page.reload()
    await page.getByText('当前账号还没有下载完成的书籍。').waitFor()
    assert.equal(await page.locator('.books li').count(), 0)
    await page.goto(`${base}/offline-library/?book=${encodeURIComponent(ebookResult.key)}`)
    await page.getByRole('alert').filter({ hasText: '属于其他账号' }).waitFor()
    await page.evaluate(token => localStorage.setItem('token', token), fakeToken(9091))
    await page.goto(`${base}/offline-library/`)
    await page.getByRole('button', { name: '离线电子书验证', exact: true }).waitFor()
    await page.getByRole('button', { name: '删除缓存 离线电子书验证', exact: true }).click()
    await page.getByRole('button', { name: '离线电子书验证', exact: true }).waitFor({ state: 'detached' })
    await page.reload()
    await page.getByRole('button', { name: '离线 PDF 验证', exact: true }).waitFor()
    assert.equal(await page.locator('.books li').count(), 2)
    await context.setOffline(false)
    await offlineNetwork.send('Network.emulateNetworkConditions', { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 })
    await fixtures(page, state)
    await page.goto(`${base}/offline-library/?book=${encodeURIComponent(pdfResult.key)}`)
    await page.getByRole('spinbutton', { name: '跳转页或章节' }).waitFor()
    await page.getByRole('spinbutton', { name: '跳转页或章节' }).fill('3')
    await page.getByRole('spinbutton', { name: '跳转页或章节' }).press('Tab')
    await page.getByRole('button', { name: '同步进度', exact: true }).click()
    await page.getByRole('status').filter({ hasText: '进度已同步' }).waitFor()
    assert.deepEqual(state.syncs, [{ pathname: '/api/pdf-documents/42/my-state', body: { current_page: 3, zoom: '175%', sidebar_open: false, state_json: { preserve: 'online preferences' } } }])
    assert.deepEqual(errors, [])
  } finally { await context.close(); await browser.close() }
})
