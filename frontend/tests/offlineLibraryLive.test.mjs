/** Opt-in smoke test against real readable books. Uses the public local-owner
 * session provider, never accesses internal DB files or logs the session token.
 * Browser storage is isolated and removed when this test closes Chromium. */
import test from 'node:test'
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { spawnSync } from 'node:child_process'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import os from 'node:os'
import { fileURLToPath } from 'node:url'
const require = createRequire(import.meta.url)
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
const base = process.env.CODEYUN_TEST_BASE_URL || 'http://localhost:5173'
const output = path.join(os.tmpdir(), 'codeyun', 'offline-library-tests')
async function disableHttpCache(context, page, offline = false) {
  const session = await context.newCDPSession(page)
  await session.send('Network.enable')
  await session.send('Network.setCacheDisabled', { cacheDisabled: true })
  await session.send('Network.emulateNetworkConditions', { offline, latency: 0, downloadThroughput: -1, uploadThroughput: -1 })
}

test('real library menu download, full PDF and EPUB survive offline reopened tabs', { skip: process.env.CODEYUN_OFFLINE_LIVE !== '1', timeout: 600000 }, async () => {
  const { chromium } = require(process.env.CODEYUN_PLAYWRIGHT_MODULE || 'playwright')
  const owner = process.env.CODEYUN_LOCAL_OWNER || 'code4101'
  const python = `import sys,json;sys.path.insert(0,${JSON.stringify(path.resolve(root, '../xlproject/src')).replaceAll('\\', '/')});import xlproject.loadenv;from backend.core.access.auth import create_local_owner_session;print(json.dumps(create_local_owner_session(username=${JSON.stringify(owner)},expires_minutes=120)))`
  const credentials = spawnSync('uv', ['run', 'python', '-c', python], { cwd: root, encoding: 'utf8' })
  assert.equal(credentials.status, 0, 'Local-owner session provider failed')
  const session = JSON.parse(credentials.stdout)
  const headers = { Authorization: `Bearer ${session.access_token}` }
  async function get(path) { const response = await fetch(base + path, { headers }); assert.equal(response.status, 200); return response.json() }
  const pdfs = await get('/api/pdf-documents')
  const ebooks = await get('/api/linux-do-books')
  const pdfBook = pdfs.sort((a, b) => (a.size_bytes || Infinity) - (b.size_bytes || Infinity))[0]
  const ebook = ebooks.find(book => book.format === 'epub')
  assert.ok(pdfBook && ebook, 'This smoke test requires a real PDF and EPUB')
  const browser = await chromium.launch({ executablePath: process.env.CODEYUN_CHROME_EXECUTABLE, headless: true })
  const context = await browser.newContext({ viewport: { width: 1200, height: 850 }, serviceWorkers: 'allow' })
  let page = await context.newPage()
  const errors = []
  context.on('page', p => p.on('pageerror', error => errors.push(error.message)))
  page.on('pageerror', error => errors.push(error.message))
  try {
    await mkdir(output, { recursive: true })
    await disableHttpCache(context, page)
    await page.goto(`${base}/offline-library/`)
    await page.evaluate(token => localStorage.setItem('token', token), session.access_token)
    // Validate the actual download entry, with the actual library UI/API.
    await page.goto(`${base}/reader?view=bookshelf&ui=1`)
    await page.getByRole('link', { name: '离线书架', exact: true }).waitFor({ timeout: 60000 })
    const visibleBook = page.locator('.linux-do-book-item, tr.skill-book-table-row').first()
    await visibleBook.waitFor({ timeout: 60000 })
    await page.locator('.pdf-library-page > .el-loading-mask').waitFor({ state: 'hidden' })
    await visibleBook.click({ button: 'right' })
    await page.getByRole('menuitem', { name: '下载供离线阅读', exact: true }).click()
    await page.getByRole('dialog', { name: '下载供离线阅读' }).waitFor()
    await page.screenshot({ path: path.join(output, 'live-download-dialog.png') })
    await page.getByRole('dialog', { name: '下载供离线阅读' }).getByRole('button', { name: '下载整本书', exact: true }).click()
    await page.getByRole('status').filter({ hasText: '整本书已保存' }).waitFor({ timeout: 300000 })
    // Explicitly download the real EPUB/PDF too, without creating/changing any
    // server-side books or synchronizing the smoke test's reading positions.
    await page.goto(`${base}/offline-library/`)
    if (process.env.CODEYUN_TEST_PUBLIC === '1') {
      // The deployed build has no Vite /src modules. Exercise its real UI from
      // download through service-worker reopening instead of injecting a test
      // helper into production assets.
      await page.locator('.book-title').first().click()
      const jump = page.getByRole('spinbutton', { name: '跳转页或章节' })
      await jump.waitFor()
      const total = Number(await jump.getAttribute('max'))
      await jump.fill(String(total)); await jump.press('Tab')
      const body = page.frameLocator('iframe[title="离线书籍正文"]').locator('#offline-content')
      await body.waitFor()
      const text = (await body.innerText()).trim()
      assert.ok(text)
      const address = page.url()
      await context.setOffline(true); await page.close(); page = await context.newPage()
      await disableHttpCache(context, page, true)
      await page.goto(address)
      await page.frameLocator('iframe[title="离线书籍正文"]').locator('#offline-content').waitFor()
      assert.equal(await page.getByRole('spinbutton', { name: '跳转页或章节' }).inputValue(), String(total))
      await page.reload()
      await page.frameLocator('iframe[title="离线书籍正文"]').locator('#offline-content').waitFor()
      assert.equal((await page.frameLocator('iframe[title="离线书籍正文"]').locator('#offline-content').innerText()).trim(), text)
      await page.getByRole('button', { name: '搜索全书', exact: true }).click()
      await page.getByRole('searchbox', { name: '搜索全书' }).fill(text.replace(/\s/g, '').slice(0, 6))
      await page.getByRole('list', { name: '全文搜索结果' }).getByRole('button').first().waitFor()
      await page.screenshot({ path: path.join(output, 'public-offline.png') })
      await writeFile(path.join(output, 'public-result.json'), JSON.stringify({ base, total, errors }, null, 2))
      assert.deepEqual(errors, [])
      return
    }
    const downloaded = []
    for (const target of [{ kind: 'ebook', id: ebook.id }, { kind: 'pdf', id: String(pdfBook.id) }]) {
      const result = await page.evaluate(async target => {
        const { downloadOfflineBook } = await import('/src/offline-library/download.ts')
        const book = await downloadOfflineBook(target, status => { window.liveDownloadStatus = status })
        const index = Math.max(0, book.sections.length - 1)
        const textSection = book.sections.findIndex(part => part.text.trim().length > 20)
        const text = book.sections[Math.max(0, textSection)].text.replace(/\s/g, '')
        const keyword = text.slice(Math.min(20, Math.max(0, text.length - 6)), Math.min(26, text.length))
        return { key: book.key, title: book.title, bytes: book.bytes, sectionCount: book.sections.length, lastIndex: index,
          searchableSection: Math.max(0, textSection), keyword, hasText: text.length > 0 }
      }, target)
      downloaded.push({ ...result, kind: target.kind })
      console.log(`Downloaded ${target.kind}: ${result.sectionCount} sections, ${result.bytes} bytes`)
    }
    assert.ok(downloaded[1].sectionCount > 100)
    await context.setOffline(true)
    await page.close(); page = await context.newPage()
    await disableHttpCache(context, page, true)
    for (const item of downloaded) {
      await page.goto(`${base}/offline-library/?book=${encodeURIComponent(item.key)}`)
      await page.getByRole('spinbutton', { name: '跳转页或章节' }).fill(String(item.lastIndex + 1))
      await page.getByRole('spinbutton', { name: '跳转页或章节' }).press('Tab')
      if (item.kind === 'pdf') {
        await page.waitForFunction(() => {
          const canvas = document.querySelector('canvas')
          return canvas?.width > 0 && canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data.some(value => value > 0)
        }, null, { timeout: 60000 })
      } else {
        const frame = page.frameLocator('iframe[title="离线书籍正文"]')
        await frame.locator('#offline-content').waitFor()
        assert.ok((await frame.locator('#offline-content').innerText()).trim())
      }
      await page.screenshot({ path: path.join(output, `live-${item.kind}-offline.png`) })
      await page.reload()
      await page.getByRole('spinbutton', { name: '跳转页或章节' }).waitFor()
      assert.equal(await page.getByRole('spinbutton', { name: '跳转页或章节' }).inputValue(), String(item.lastIndex + 1))
      if (item.hasText && item.keyword) {
        await page.getByRole('button', { name: '搜索全书', exact: true }).click()
        await page.getByRole('searchbox', { name: '搜索全书' }).fill(item.keyword)
        await page.getByRole('list', { name: '全文搜索结果' }).getByRole('button').first().waitFor()
      }
    }
    await writeFile(path.join(output, 'live-result.json'), JSON.stringify({ base, downloaded, errors }, null, 2))
    assert.deepEqual(errors, [])
  } catch (cause) {
    await page.screenshot({ path: path.join(output, 'live-failure.png') }).catch(() => undefined)
    console.log('Live page state:', await page.evaluate(() => ({ url: location.href,
      library: document.querySelector('.pdf-library-page')?.textContent?.slice(0, 700),
      errors: Array.from(document.querySelectorAll('[role="alert"]')).map(element => element.textContent?.slice(0, 150)) })).catch(() => ({})))
    throw cause
  } finally { await context.close(); await browser.close() }
})
