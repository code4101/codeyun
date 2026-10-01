/** Real Vue + native PG + isolated API acceptance. No production documents.
 * node gallery-smoke.cjs <server.json> <upstream> <playwright-module> <temp-output>
 */
const assert = require('node:assert/strict'), fs = require('node:fs/promises'), path = require('node:path');
const { createRequire } = require('node:module'), { pathToFileURL } = require('node:url');
const [manifest, upstream, playwrightModule, output] = process.argv.slice(2);
const root = path.resolve(__dirname, '../../frontend');
const frontendRequire = createRequire(path.join(root, 'package.json'));
const upstreamRequire = createRequire(path.resolve(upstream, 'app/package.json'));
const { ZipReader, ZipWriter, Uint8ArrayReader, Uint8ArrayWriter } = upstreamRequire('@zip.js/zip.js');
const { decode } = upstreamRequire('@msgpack/msgpack');

async function main() {
  console.log('Starting isolated gallery acceptance');
  await fs.mkdir(output, { recursive: true });
  const { url } = JSON.parse(await fs.readFile(manifest, 'utf8'));
  const tokens = await fetch(url + '/fixture/sessions').then(r => r.json());
  const { createServer, build } = await import(pathToFileURL(frontendRequire.resolve('vite')).href);
  const vue = (await import(pathToFileURL(frontendRequire.resolve('@vitejs/plugin-vue')).href)).default;
  const source = `import {createApp,h} from 'vue';import {createPinia} from 'pinia';import {createRouter,createWebHistory,RouterView} from 'vue-router';
  import ElementPlus from 'element-plus';import 'element-plus/dist/index.css';import '/src/style.css';import '/src/standard/pdf/library/readerTheme.css';
  import GraphWorkspace from '/src/plugins/modules/project-graph/GraphWorkspace.vue';import {useUserStore} from '/src/store/userStore.ts';
  import {contextMenuDirective} from '/src/directives/contextMenu.ts';import '/src/directives/contextMenu.css';
  const user=Number(new URLSearchParams(location.search).get('user')||1),tokens=await fetch('/fixture/sessions').then(r=>r.json());
  const app=createApp({render:()=>h(RouterView)}),pinia=createPinia();app.use(pinia);const store=useUserStore();store.setTokens(tokens[user],null);store.user={id:user,username:'test'+user,nickname:'测试用户'+user,is_superuser:false};
  app.use(ElementPlus);app.directive('context-menu',contextMenuDirective);const router=createRouter({history:createWebHistory(),routes:[{path:'/fixture-gallery',component:GraphWorkspace}]});app.use(router);await router.isReady();app.mount('#app');`;
  await build({ configFile: false, root, publicDir: path.join(root, 'public'), logLevel: 'error', define: { 'process.env.NODE_ENV': JSON.stringify('production') },
    plugins: [vue(), { name: 'gallery-host', resolveId(id) { if (id.endsWith('virtual:gallery-host')) return '\0gallery-host'; }, load(id) { if (id === '\0gallery-host') return source; } }],
    resolve: { alias: { '@': path.join(root, 'src') } }, build: { copyPublicDir: false, target: 'esnext', outDir: path.join(output, 'bundle'), emptyOutDir: false, lib: { entry: 'virtual:gallery-host', formats: ['es'], fileName: 'workspace' } } });
  const vite = await createServer({ configFile: false, root, publicDir: path.join(root, 'public'), cacheDir: path.join(output, 'vite-cache'), logLevel: 'error',
    optimizeDeps: { entries: [] }, resolve: { alias: { '@': path.join(root, 'src') } },
    plugins: [{ name: 'gallery-acceptance', configureServer(server) { server.middlewares.use(async (req, res, next) => {
      if (req.url === '/workspace.js' || req.url === '/workspace.css') { res.setHeader('Content-Type', req.url.endsWith('.css') ? 'text/css' : 'text/javascript'); res.end(await fs.readFile(path.join(output, 'bundle', req.url.slice(1)))); return; }
      if (req.url?.startsWith('/fixture-gallery')) { res.setHeader('Content-Type', 'text/html'); res.end('<!doctype html><html><meta charset="utf-8"><style>html,body,#app{height:100%;margin:0}</style><div id="app"></div><link rel="stylesheet" href="/workspace.css"><script type="module" src="/workspace.js"></script></html>'); return; }
      next();
    }); } }], server: { host: '127.0.0.1', port: 19849, strictPort: true, proxy: { '/api': { target: url, ws: true }, '/fixture/sessions': url } } });
  await vite.listen();
  const origin = 'http://127.0.0.1:' + vite.httpServer.address().port;
  console.log('Gallery workspace served at ' + origin);
  const { chromium } = require(playwrightModule), browser = await chromium.launch({ channel: 'chrome', headless: true });
  console.log('Isolated Chrome launched');
  const errors = [], pages = [], checks = [];
  async function api(suffix = '', method = 'GET', body) {
    const response = await fetch(url + '/api/project-graph/files' + suffix, { method, headers: { Authorization: 'Bearer ' + tokens[1], 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });
    const value = await response.json(); assert.ok(response.ok, JSON.stringify(value)); return value;
  }
  const frame = page => page.frames().find(f => f.url().includes('embed.html'));
  async function open(rid, user = 1) {
    const context = await browser.newContext({ viewport: { width: 1520, height: 980 } }), page = await context.newPage(); pages.push(page);
    page.on('pageerror', e => errors.push(String(e)));
    await page.goto(`${origin}/fixture-gallery?user=${user}&doc=${rid}`);
    await page.evaluate(() => {
      window.galleryTrace = [];
      window.addEventListener('message', event => {
        if (event.data?.channel === 'codeyun.plate' && event.data.type === 'change') window.galleryTrace.push({ type: 'body-change', message: JSON.stringify(event.data.payload.value), key: event.data.payload.contentKey });
        if (event.data?.channel === 'codeyun.project-graph' && ['gallery-state', 'gallery-result', 'write', 'error', 'status'].includes(event.data.type))
          window.galleryTrace.push({ type: event.data.type, id: event.data.id, error: event.data.error, state: event.data.payload?.state, count: event.data.payload?.items?.length, message: event.data.payload?.error ?? event.data.payload?.message });
      });
    });
    await page.frameLocator('iframe[title="ProjectGraph 编辑器"]').locator('canvas').waitFor({ timeout: 60000 });
    await page.locator('iframe[title="ProjectGraph 编辑器"]').waitFor({ state: 'visible' });
    await page.getByRole('button', { name: '图库', exact: true }).click();
    await page.locator('.gallery-tool [data-gallery-group="todo"]').waitFor();
    return page;
  }
  async function archive(rid) {
    const file = await api('/' + rid), zip = new ZipReader(new Uint8ArrayReader(Buffer.from(file.content, 'base64')));
    const members = {};
    try { for (const entry of await zip.getEntries()) if (!entry.directory) members[entry.filename] = await entry.getData(new Uint8ArrayWriter()); }
    finally { await zip.close(); }
    return { file, members, stage: decode(members['stage.msgpack']), index: members['gallery/index.msgpack'] ? decode(members['gallery/index.msgpack']) : null,
      items: Object.keys(members).filter(name => name.startsWith('gallery/items/')).map(name => decode(members[name])) };
  }
  async function stored(page, count) { await page.locator('.gallery-tool [data-gallery-item]').waitFor({ state: count ? 'visible' : 'hidden' }); }
  async function waitSaved(page) { await page.locator('main[aria-busy="false"]').waitFor(); assert.equal(await page.locator('.error[role="alert"]').count(), 0, await page.locator('.error').allTextContents()); }
  try {
    const title = '图库验收-' + Date.now(), rid = (await api('', 'POST', { title })).id;
    const owner = await open(rid);
    await frame(owner).locator('canvas').dblclick({ position: { x: 420, y: 320 } });
    await frame(owner).locator('textarea').fill('跨天推进的任务 A'); await owner.keyboard.press('Escape');
    await owner.locator('.gallery-tool .selection').filter({ hasText: '已选 1 个对象' }).waitFor();
    await owner.getByRole('button', { name: '正文', exact: true }).click({ modifiers: ['Control'] });
    const body = owner.frameLocator('iframe[title="Plate 正文编辑器"]').locator('[contenteditable=true]');
    await body.waitFor({ timeout: 45000 }); await body.click(); await body.pressSequentially('任务 A 的正文也应随子图保留');
    await owner.waitForFunction(() => window.galleryTrace.some(entry => entry.type === 'body-change' && entry.message.includes('任务 A 的正文也应随子图保留')));
    await owner.getByRole('button', { name: '图库', exact: true }).click();
    await owner.locator('[data-gallery-group="todo"] .store').click(); await stored(owner, 1); await waitSaved(owner);
    let data = await archive(rid); assert.equal(data.stage.length, 0); assert.equal(data.items.length, 1);
    const taskId = data.items[0].objects['@order'].value[0], itemId = data.items[0].id;
    assert.equal(data.items[0].objects[taskId].text, '跨天推进的任务 A');
    assert.ok(JSON.stringify(data.items[0].objects[taskId].details).includes('任务 A 的正文也应随子图保留'));
    checks.push('real canvas selection → gallery, stable UUID');
    // Native Ctrl+Z must restore both sides, never duplicate the object.
    await frame(owner).locator('canvas').click({ position: { x: 100, y: 100 } });
    await owner.keyboard.press('Control+z'); await stored(owner, 0); await owner.waitForTimeout(1500);
    data = await archive(rid); assert.equal(data.stage.length, 1); assert.equal(data.items.length, 0);
    await owner.keyboard.press('Control+y'); await stored(owner, 1); await owner.waitForTimeout(1500);
    data = await archive(rid); assert.equal(data.stage.length, 0); assert.equal(data.items.length, 1);
    checks.push('native undo/redo atomically spans canvas and gallery');
    await owner.getByLabel('新分组名称').fill('稍后继续'); await owner.locator('.create-group button').click(); await waitSaved(owner);
    const group = owner.locator('[data-gallery-group]').filter({ hasText: '稍后继续' });
    const laterId = await group.getAttribute('data-gallery-group');
    await owner.locator('[data-gallery-item]').dragTo(group); await waitSaved(owner);
    await group.locator('[data-gallery-item]').waitFor();
    data = await archive(rid); assert.equal(data.items[0].groupId, laterId); checks.push('custom group and actual card drag between groups');
    await owner.reload(); await owner.frameLocator('iframe[title="ProjectGraph 编辑器"]').locator('canvas').waitFor({ timeout: 60000 });
    await owner.locator('.gallery-tool [data-gallery-item]').waitFor();
    await owner.screenshot({ path: path.join(output, 'gallery-stored.png') });
    // Export through the real host download entry, then reimport this one file.
    await owner.getByRole('treeitem').filter({ hasText: title }).click({ button: 'right' });
    const download = owner.waitForEvent('download'); await owner.getByRole('menuitem', { name: '下载文件', exact: true }).click();
    const exported = path.join(output, 'gallery-project.prg'); await (await download).saveAs(exported);
    // Seed shared assets/opaque extensions through the public import boundary.
    const seedReader = new ZipReader(new Uint8ArrayReader(await fs.readFile(exported))), seedOutput = new Uint8ArrayWriter(), seedWriter = new ZipWriter(seedOutput, { level: 0 });
    for (const entry of await seedReader.getEntries()) if (!entry.directory) await seedWriter.add(entry.filename, new Uint8ArrayReader(await entry.getData(new Uint8ArrayWriter())));
    const assetName = 'attachments/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa.png';
    const asset = Uint8Array.from(Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1kAAAAASUVORK5CYII=', 'base64'));
    await seedWriter.add(assetName, new Uint8ArrayReader(asset));
    await seedWriter.add('another-module/preserved.bin', new Uint8ArrayReader(Uint8Array.from([1, 2, 3])));
    await seedWriter.close(); await seedReader.close();
    const importedId = (await api('', 'POST', { title: title + '-导入', content: Buffer.from(await seedOutput.getData()).toString('base64') })).id;
    const imported = await open(importedId); await stored(imported, 1); checks.push('refresh, native export, single-file reimport preserve gallery');
    const dragFrom = await imported.locator('[data-gallery-item]').boundingBox(), dragTo = await frame(imported).locator('canvas').boundingBox();
    await imported.mouse.move(dragFrom.x + dragFrom.width / 2, dragFrom.y + 15); await imported.mouse.down();
    await imported.mouse.move(dragFrom.x - 15, dragFrom.y + 15, { steps: 3 });
    await imported.mouse.move(dragTo.x + dragTo.width / 2, dragTo.y + dragTo.height / 2, { steps: 12 });
    await imported.mouse.move(dragTo.x + dragTo.width / 2 + 2, dragTo.y + dragTo.height / 2 + 2); await imported.mouse.up();
    await stored(imported, 0); await waitSaved(imported);
    const restored = await archive(importedId); assert.equal(restored.stage[0].uuid, taskId); assert.equal(restored.items.length, 0);
    assert.ok(JSON.stringify(restored.stage[0].details).includes('任务 A 的正文也应随子图保留'));
    assert.deepEqual(Array.from(restored.members[assetName]), Array.from(asset));
    assert.deepEqual(Array.from(restored.members['another-module/preserved.bin']), [1, 2, 3]);
    assert.equal((await archive(rid)).items.length, 1, 'each PRG owns an independent gallery');
    checks.push('cross-iframe drag take preserves UUID, rich body, shared assets and opaque members; per-PRG independence');
    // Standard PG parser reads the extended archive without changing its version.
    const esbuild = frontendRequire('esbuild');
    const parserPath = path.join(output, 'standard-parser.cjs');
    await esbuild.build({ entryPoints: [path.resolve(upstream, 'app/src/core/ProjectFile.ts')], outfile: parserPath, platform: 'node', format: 'cjs', bundle: true, alias: { '@': path.resolve(upstream, 'app/src') }, logLevel: 'silent' });
    const { Decoder } = upstreamRequire('@msgpack/msgpack');
    const parsed = await require(parserPath).parseProjectFile(Uint8Array.from(await fs.readFile(exported)), new Decoder(), new Map());
    assert.equal(parsed.serializedStageObjects.length, 0); assert.equal(parsed.metadata.version, '2.7.0');
    checks.push('unmodified standard PG parser opens extended PRG');
    // Real collaborative host: directory/items and movement propagate together.
    data = await archive(rid);
    await api('/' + rid + '/collaboration', 'POST', { expectedRevision: data.file.revision });
    await owner.reload(); await owner.frameLocator('iframe[title="ProjectGraph 编辑器"]').getByRole('status').filter({ hasText: '协作已连接' }).waitFor();
    const peer = await open(rid); await peer.frameLocator('iframe[title="ProjectGraph 编辑器"]').getByRole('status').filter({ hasText: '协作已连接' }).waitFor();
    await owner.locator('[data-gallery-item] .take').click(); await stored(owner, 0); await stored(peer, 0); await waitSaved(owner);
    data = await archive(rid); assert.equal(data.stage[0].uuid, taskId); assert.equal(data.items.length, 0);
    await owner.locator('.gallery-tool .selection').dragTo(owner.locator('[data-gallery-group="todo"]')); await stored(owner, 1); await stored(peer, 1); await waitSaved(owner);
    await peer.screenshot({ path: path.join(output, 'gallery-collaboration.png') });
    assert.equal((await archive(rid)).items[0].objects[taskId].uuid, taskId);
    checks.push('two real collaborative windows converge on take/store');
    await api('/' + rid + '/access', 'PUT', { userId: 2, role: 'viewer' });
    const viewer = await open(rid, 2); await stored(viewer, 1);
    assert.equal(await viewer.locator('[data-gallery-item] .take').isDisabled(), true);
    assert.equal(await viewer.locator('.create-group button').isDisabled(), true);
    checks.push('read-only shared gallery disables mutations');
    assert.deepEqual(errors, []);
    await fs.writeFile(path.join(output, 'result.json'), JSON.stringify({ passed: true, checks, resourceId: rid, itemId }, null, 2));
    console.log('PASS: ' + checks.join('; '));
  } catch (error) {
    if (pages[0] && !pages[0].isClosed()) console.error(await pages[0].evaluate(() => ({ busy: document.querySelector('main')?.getAttribute('aria-busy'), error: document.querySelector('.error')?.textContent, trace: window.galleryTrace?.slice(-15) })));
    if (pages[0] && !pages[0].isClosed()) await pages[0].screenshot({ path: path.join(output, 'failure.png') }).catch(() => {});
    throw error;
  } finally { await browser.close(); await vite.close(); }
}
const keepAlive = setInterval(() => {}, 1000);
main().catch(error => { console.error(error); process.exitCode = 1; }).finally(() => clearInterval(keepAlive));
