/** Isolated Plate link acceptance against the built plugin; no user documents.
 * node plate-links-smoke.cjs <playwright-module>
 */
const { chromium } = require(process.argv[2] || 'playwright');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs/promises');
const path = require('node:path');
const publicRoot = path.resolve(__dirname, '../../frontend/public');
const session = 'plate-links-check';
const fixture = `<!doctype html><iframe style="width:100%;height:800px" src="/plugins/project-graph/plate.html?session=${session}"></iframe>
<script>
window.loadBody = (value, readOnly = true) => document.querySelector('iframe').contentWindow.postMessage({
  channel: 'codeyun.plate', version: 1, session: '${session}', type: 'load',
  payload: { value, readOnly, contentKey: 'fixture', loadId: 1 }
}, location.origin);
addEventListener('message', event => {
  if (event.data.type === 'ready') window.ready = true;
});
</script>`;

(async () => {
  const server = http.createServer(async (request, response) => {
    const pathname = new URL(request.url, 'http://localhost').pathname;
    if (pathname === '/fixture') { response.setHeader('Content-Type', 'text/html'); response.end(fixture); return; }
    const file = path.resolve(publicRoot, '.' + pathname);
    if (!file.startsWith(publicRoot + path.sep)) { response.writeHead(403).end(); return; }
    try {
      const content = await fs.readFile(file);
      const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png' };
      response.setHeader('Content-Type', mime[path.extname(file)] || 'application/octet-stream');
      response.end(content);
    } catch { response.writeHead(404).end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({ channel: 'chrome', headless: true });
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(String(error)));
    const origin = `http://127.0.0.1:${server.address().port}`;
    await page.context().route('https://example.test/**', route => route.fulfill({ contentType: 'text/html', body: 'Link destination' }));
    await page.goto(origin + '/fixture');
    await page.waitForFunction(() => window.ready);
    const links = [
      ['self', 'https://example.test/self', '_self'],
      ['parent', '/fixture?parent', '_parent'],
      ['top', '/fixture?top', '_top'],
      ['named', 'https://example.test/named', 'body-frame'],
      ['relative', './plate.html?relative', undefined],
      ['hash', '#section', '_self'],
      ['unsafe', 'javascript:alert(1)', '_self'],
    ];
    const value = links.map(([text, url, target]) => ({ type: 'p', children: [{ type: 'a', url, ...(target ? { target } : {}), children: [{ text }] }] }));
    await page.evaluate(value => window.loadBody(value), value);
    const frame = page.frames().find(frame => frame.url().includes('plate.html'));
    await frame.getByText('self', { exact: true }).waitFor();
    assert.ok(await frame.locator('a').count(), await frame.locator('[data-slate-editor]').evaluate(element => element.innerHTML));
    const originalFrameUrl = frame.url();
    for (const [text, url] of links.slice(0, -1)) {
      console.log('Checking', text);
      const anchor = frame.locator('a').filter({ hasText: text });
      assert.equal(await anchor.getAttribute('target'), '_blank');
      assert.equal(await anchor.getAttribute('rel'), 'noopener noreferrer');
      assert.equal(await anchor.getAttribute('href'), new URL(url, originalFrameUrl).href);
      const popupPromise = page.waitForEvent('popup');
      await anchor.click();
      const popup = await popupPromise;
      await popup.waitForURL(new URL(url, originalFrameUrl).href);
      assert.equal(frame.url(), originalFrameUrl, 'body iframe must retain its document');
      assert.equal(page.url(), origin + '/fixture', 'host must retain its workspace');
      await popup.close();
    }
    assert.equal(await frame.locator('a').filter({ hasText: 'unsafe' }).getAttribute('href'), null, 'Plate URL sanitization remains active');
    await page.evaluate(value => window.loadBody(value, false), value);
    await frame.locator('[contenteditable=true]').waitFor();
    assert.equal(await frame.locator('a').filter({ hasText: 'self' }).getAttribute('target'), '_blank', 'editing uses the same link policy');
    assert.deepEqual(errors, []);
    console.log('PASS Plate links: stored targets, relative/hash URLs, new tabs, retained iframe, sanitization and editing');
  } finally {
    await browser?.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
