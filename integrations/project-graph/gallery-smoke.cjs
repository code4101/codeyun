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
        if (event.data?.channel === 'codeyun.project-graph' && ['gallery-state', 'gallery-drag', 'gallery-result', 'write', 'error', 'status'].includes(event.data.type))
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
  async function view(page, rid) {
    await page.waitForTimeout(350);
    const value = await frame(page).evaluate(id => JSON.parse(localStorage.getItem('codeyun.project-graph.view:1:' + id)), String(rid));
    assert.ok(value && Number.isFinite(value.scale), 'personal viewport was persisted'); return value;
  }
  async function settledZoom(page, rid) {
    let previous = await view(page, rid);
    for (let i = 0; i < 24; i++) {
      const next = await view(page, rid);
      if (next.scale === previous.scale && Math.abs(next.x - previous.x) < 1e-8 && Math.abs(next.y - previous.y) < 1e-8) return next;
      previous = next;
    }
    throw new Error('native wheel zoom animation did not settle');
  }
  function sameView(actual, expected, message = 'gallery operation must preserve viewport') {
    assert.equal(actual.scale, expected.scale, message);
    assert.ok(Math.abs(actual.x - expected.x) < 1e-8 && Math.abs(actual.y - expected.y) < 1e-8, message);
  }
  async function nodePoint(page, rid) {
    const viewport = await view(page, rid), box = await frame(page).locator('canvas').boundingBox();
    const rect = (await archive(rid)).stage[0].collisionBox.shapes[0];
    const x = box.x + box.width / 2 + (rect.location.x - viewport.x) * viewport.scale;
    const y = box.y + box.height / 2 + (rect.location.y - viewport.y) * viewport.scale;
    const left = Math.max(box.x + 12, x), right = Math.min(box.x + box.width - 12, x + rect.size.x * viewport.scale);
    const top = Math.max(box.y + 12, y), bottom = Math.min(box.y + box.height - 12, y + rect.size.y * viewport.scale);
    assert.ok(left < right && top < bottom, 'restored node has a visible draggable area');
    return { x: (left + right) / 2, y: (top + bottom) / 2 };
  }
  try {
    const title = '图库验收-' + Date.now(), rid = (await api('', 'POST', { title })).id;
    const owner = await open(rid);
    await frame(owner).locator('canvas').dblclick({ position: { x: 420, y: 320 } });
    await frame(owner).locator('textarea').fill('跨天推进的任务 A'); await owner.keyboard.press('Escape');
    await owner.locator('.gallery-tool .selection').waitFor();
    await owner.getByRole('button', { name: '正文', exact: true }).click({ modifiers: ['Control'] });
    const body = owner.frameLocator('iframe[title="Plate 正文编辑器"]').locator('[contenteditable=true]');
    await body.waitFor({ timeout: 45000 }); await body.click(); await body.pressSequentially('任务 A 的正文也应随子图保留');
    await owner.waitForFunction(() => window.galleryTrace.some(entry => entry.type === 'body-change' && entry.message.includes('任务 A 的正文也应随子图保留')));
    await owner.getByRole('button', { name: '图库', exact: true }).click();
    const beforeStoreView = await view(owner, rid);
    // Exercise the real native drag from inside PG's iframe into the Vue gallery.
    const canvasBox = await frame(owner).locator('canvas').boundingBox();
    const handle = { x: canvasBox.x + 500, y: canvasBox.y + 300, width: 1, height: 1 };
    const target = await owner.locator('[data-gallery-group="todo"]').boundingBox();
    await owner.mouse.move(handle.x + handle.width / 2, handle.y + handle.height / 2); await owner.mouse.down();
    await owner.mouse.move(handle.x + handle.width / 2 + 12, handle.y + handle.height / 2, { steps: 4 });
    await owner.mouse.move(target.x + target.width / 2, target.y + target.height / 2, { steps: 16 });
    await owner.mouse.move(target.x + target.width / 2 + 2, target.y + target.height / 2 + 2);
    await owner.locator('[data-gallery-group=todo].over').waitFor();
    await owner.locator('.gallery-drag-preview.accepted').waitFor();
    await owner.screenshot({ path: path.join(output, 'gallery-direct-drag.png') });
    await owner.mouse.up();
    await stored(owner, 1); await waitSaved(owner);
    sameView(await view(owner, rid), beforeStoreView, 'store must preserve zoom and position');
    let data = await archive(rid); assert.equal(data.stage.length, 0); assert.equal(data.items.length, 1);
    const taskId = data.items[0].objects['@order'].value[0], itemId = data.items[0].id;
    assert.equal(data.items[0].objects[taskId].text, '跨天推进的任务 A');
    assert.ok(JSON.stringify(data.items[0].objects[taskId].details).includes('任务 A 的正文也应随子图保留'));
    checks.push('direct graph drag across iframe → gallery, stable UUID and rich body');
    // Native Ctrl+Z must restore both sides, never duplicate the object.
    await frame(owner).locator('canvas').click({ position: { x: 100, y: 100 } });
    await owner.keyboard.press('Control+z'); await stored(owner, 0); await owner.waitForTimeout(1500);
    data = await archive(rid); assert.equal(data.stage.length, 1); assert.equal(data.items.length, 0);
    await owner.keyboard.press('Control+y'); await stored(owner, 1); await owner.waitForTimeout(1500);
    data = await archive(rid); assert.equal(data.stage.length, 0); assert.equal(data.items.length, 1);
    sameView(await view(owner, rid), beforeStoreView, 'undo/redo must preserve the viewport');
    checks.push('native undo/redo atomically spans canvas and gallery');
    await owner.getByRole('button', { name: '添加分组', exact: true }).click();
    await owner.getByLabel('新分组名称').fill('稍后继续'); await owner.getByRole('button', { name: '添加分组', exact: true }).last().click(); await waitSaved(owner);
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
    const zoomCanvas = await frame(imported).locator('canvas').boundingBox();
    await imported.mouse.move(zoomCanvas.x + zoomCanvas.width * .35, zoomCanvas.y + zoomCanvas.height * .42);
    await imported.mouse.wheel(0, 400); await imported.waitForTimeout(700);
    const beforeTakeView = await settledZoom(imported, importedId);
    assert.notEqual(beforeTakeView.scale, 1, 'test uses a user-selected non-default zoom');
    const dragFrom = await imported.locator('[data-gallery-item]').boundingBox(), dragTo = await frame(imported).locator('canvas').boundingBox();
    // Entering/leaving and Escape must only dispose preview, without taking.
    await imported.mouse.move(dragFrom.x + dragFrom.width / 2, dragFrom.y + 15); await imported.mouse.down();
    await imported.mouse.move(dragFrom.x - 15, dragFrom.y + 15, { steps: 3 });
    await imported.mouse.move(dragTo.x + 20, dragTo.y + dragTo.height / 2, { steps: 12 });
    await frame(imported).locator('[data-gallery-placement-preview]').waitFor({ state: 'visible' });
    await imported.mouse.move(dragFrom.x + 15, dragFrom.y + 15, { steps: 12 });
    await frame(imported).locator('[data-gallery-placement-preview]').waitFor({ state: 'hidden' });
    await imported.mouse.move(dragTo.x + 20, dragTo.y + dragTo.height / 2, { steps: 12 });
    await frame(imported).locator('[data-gallery-placement-preview]').waitFor({ state: 'visible' });
    await imported.keyboard.press('Escape'); await imported.mouse.up();
    await frame(imported).locator('[data-gallery-placement-preview]').waitFor({ state: 'hidden' });
    assert.equal((await archive(importedId)).items.length, 1);
    assert.equal((await archive(importedId)).stage.length, 0);
    checks.push('leaving canvas and Escape clear incoming preview without taking');

    await imported.mouse.move(dragFrom.x + dragFrom.width / 2, dragFrom.y + 15); await imported.mouse.down();
    await imported.mouse.move(dragFrom.x - 15, dragFrom.y + 15, { steps: 3 });
    const dropPoint = { x: Math.round(dragTo.x + dragTo.width * .6), y: Math.round(dragTo.y + dragTo.height * .65) };
    await imported.mouse.move(dragTo.x + 20, dragTo.y + dragTo.height / 2, { steps: 12 });
    const placement = frame(imported).locator('[data-gallery-placement-preview]');
    await placement.waitFor({ state: 'visible' });
    await imported.mouse.move(dropPoint.x, dropPoint.y, { steps: 12 });
    await imported.waitForTimeout(100);
    const previewRect = await placement.locator('rect').first().boundingBox();
    assert.ok(Math.abs(previewRect.x + previewRect.width / 2 - dropPoint.x) < 2 && Math.abs(previewRect.y + previewRect.height / 2 - dropPoint.y) < 2, 'live subgraph preview follows mouse in canvas');
    assert.equal((await archive(importedId)).stage.length, 0, 'preview does not take before release');
    await imported.screenshot({ path: path.join(output, 'gallery-placement-preview.png') });
    await imported.mouse.up();
    await stored(imported, 0); await waitSaved(imported);
    await placement.waitFor({ state: 'hidden' });
    sameView(await view(imported, importedId), beforeTakeView, 'drag take must preserve zoom and position');
    const positioned = (await archive(importedId)).stage[0].collisionBox.shapes[0];
    const expectedX = beforeTakeView.x + (dropPoint.x - dragTo.x - dragTo.width / 2) / beforeTakeView.scale;
    const expectedY = beforeTakeView.y + (dropPoint.y - dragTo.y - dragTo.height / 2) / beforeTakeView.scale;
    assert.ok(Math.abs(positioned.location.x + positioned.size.x / 2 - expectedX) < .1 && Math.abs(positioned.location.y + positioned.size.y / 2 - expectedY) < .1, 'drop positions subgraph center at mouse world coordinate under zoom/pan');
    await frame(imported).locator('canvas').click({ position: { x: 100, y: 100 } });
    await imported.keyboard.press('Control+z'); await stored(imported, 1); await imported.waitForTimeout(1000);
    assert.deepEqual((await archive(importedId)).items[0].objects[taskId].collisionBox.shapes[0], data.items[0].objects[taskId].collisionBox.shapes[0]);
    await imported.keyboard.press('Control+y'); await stored(imported, 0); await imported.waitForTimeout(1000);
    assert.deepEqual((await archive(importedId)).stage[0].collisionBox.shapes[0], positioned);
    await imported.keyboard.press('Control+a');
    checks.push('live placement preview and positioned take under zoom/pan; undo restores gallery geometry, redo restores drop point');
    for (let cycle = 0; cycle < 3; cycle++) {
      await imported.locator('.gallery-tool .selection').click();
      await imported.getByRole('group', { name: '选择收纳分组' }).getByRole('button', { name: /待办/ }).click(); await stored(imported, 1);
      await imported.locator('[data-gallery-item]').hover(); await imported.locator('[data-gallery-item] .take').click(); await stored(imported, 0);
      sameView(await view(imported, importedId), beforeTakeView);
    }
    checks.push('store, drag take, repeated take and undo/redo preserve zoom and camera position');
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
    const ownerCanvas = await frame(owner).locator('canvas').boundingBox();
    await owner.mouse.move(ownerCanvas.x + ownerCanvas.width / 2, ownerCanvas.y + ownerCanvas.height / 2);
    await owner.mouse.wheel(0, 400); await owner.waitForTimeout(700);
    const beforeCollabTake = await settledZoom(owner, rid), beforePeerTake = await view(peer, rid);
    assert.notEqual(beforeCollabTake.scale, beforePeerTake.scale, 'collaborators retain independent zoom');
    const collabCard = await owner.locator('[data-gallery-item]').boundingBox();
    const collabDrop = { x: Math.round(ownerCanvas.x + ownerCanvas.width * .55), y: Math.round(ownerCanvas.y + ownerCanvas.height * .6) };
    await owner.mouse.move(collabCard.x + 20, collabCard.y + 15); await owner.mouse.down();
    await owner.mouse.move(collabCard.x - 15, collabCard.y + 15, { steps: 3 });
    await owner.mouse.move(collabDrop.x, collabDrop.y, { steps: 16 });
    await frame(owner).locator('[data-gallery-placement-preview]').waitFor({ state: 'visible' });
    await owner.mouse.up(); await stored(owner, 0); await stored(peer, 0); await waitSaved(owner);
    data = await archive(rid); assert.equal(data.stage[0].uuid, taskId); assert.equal(data.items.length, 0);
    const collabRect = data.stage[0].collisionBox.shapes[0];
    assert.ok(Math.abs(collabRect.location.x + collabRect.size.x / 2 - (beforeCollabTake.x + (collabDrop.x - ownerCanvas.x - ownerCanvas.width / 2) / beforeCollabTake.scale)) < .1);
    assert.ok(Math.abs(collabRect.location.y + collabRect.size.y / 2 - (beforeCollabTake.y + (collabDrop.y - ownerCanvas.y - ownerCanvas.height / 2) / beforeCollabTake.scale)) < .1);

    sameView(await view(owner, rid), beforeCollabTake); sameView(await view(peer, rid), beforePeerTake);
    const point = await nodePoint(owner, rid);
    const peerTarget = await owner.locator('[data-dock-tool=gallery] .dock-tool-heading').boundingBox();
    await owner.mouse.move(point.x, point.y); await owner.mouse.down();
    await owner.mouse.move(peerTarget.x + peerTarget.width / 2, peerTarget.y + peerTarget.height / 2, { steps: 24 }); await owner.mouse.up();
    await stored(owner, 1); await stored(peer, 1); await waitSaved(owner);
    await peer.screenshot({ path: path.join(output, 'gallery-collaboration.png') });
    assert.equal((await archive(rid)).items[0].objects[taskId].uuid, taskId);
    checks.push('two real collaborative windows converge on take/store');
    await api('/' + rid + '/access', 'PUT', { userId: 2, role: 'viewer' });
    const viewer = await open(rid, 2); await stored(viewer, 1);
    assert.equal(await viewer.locator('[data-gallery-item] .take').count(), 0);
    assert.equal(await viewer.getByRole('button', { name: '添加分组', exact: true }).isDisabled(), true);

    checks.push('read-only shared gallery disables mutations');
    // An aborted direct drag must never store content or leave a ghost behind.
    const cancelRid = (await api('', 'POST', { title: '取消拖拽验收-' + Date.now() })).id, cancelPage = await open(cancelRid);
    await frame(cancelPage).locator('canvas').dblclick({ position: { x: 420, y: 320 } });
    await frame(cancelPage).locator('textarea').fill('取消收纳的任务'); await cancelPage.keyboard.press('Escape');
    await cancelPage.waitForTimeout(1500);
    await api('/' + cancelRid + '/collaboration', 'POST', { expectedRevision: (await archive(cancelRid)).file.revision });
    await cancelPage.reload();
    await cancelPage.frameLocator('iframe[title="ProjectGraph 编辑器"]').getByRole('status').filter({ hasText: '协作已连接' }).waitFor();
    const cancelCanvas = await frame(cancelPage).locator('canvas').boundingBox();
    const cancelTarget = await cancelPage.locator('[data-gallery-group=todo]').boundingBox();
    await cancelPage.waitForTimeout(1500);
    const beforeMove = (await archive(cancelRid)).stage[0].collisionBox.shapes[0].location;
    await cancelPage.mouse.move(cancelCanvas.x + 500, cancelCanvas.y + 300); await cancelPage.mouse.down();
    await cancelPage.mouse.move(cancelCanvas.x + 560, cancelCanvas.y + 300, { steps: 6 }); await cancelPage.mouse.up();
    await cancelPage.waitForTimeout(1500);
    assert.notDeepEqual((await archive(cancelRid)).stage[0].collisionBox.shapes[0].location, beforeMove);
    assert.equal(await cancelPage.locator('.gallery-drag-preview').count(), 0);
    checks.push('ordinary canvas movement stays native, without starting gallery drag');
    await cancelPage.mouse.move(cancelCanvas.x + 560, cancelCanvas.y + 300); await cancelPage.mouse.down();
    await cancelPage.mouse.move(cancelTarget.x + cancelTarget.width / 2, cancelTarget.y + cancelTarget.height / 2, { steps: 24 });
    await cancelPage.locator('.gallery-drag-preview.accepted').waitFor();
    await cancelPage.keyboard.press('Escape'); await cancelPage.mouse.up();
    await cancelPage.locator('.gallery-drag-preview').waitFor({ state: 'hidden' }); await cancelPage.waitForTimeout(1500);
    const cancelled = await archive(cancelRid); assert.equal(cancelled.stage.length, 1); assert.equal(cancelled.items.length, 0);
    checks.push('Escape cancels direct drag without storing or leaving a ghost');
    await frame(cancelPage).locator('canvas').click({ position: { x: 100, y: 100 } }); await cancelPage.keyboard.press('Control+a');
    await cancelPage.locator('.gallery-tool .selection').click();
    await cancelPage.getByRole('group', { name: '选择收纳分组' }).getByRole('button', { name: /待办/ }).click();
    await stored(cancelPage, 1); await cancelPage.waitForTimeout(1500);
    const afterCancel = await archive(cancelRid); assert.equal(afterCancel.items.length, 1); assert.equal(afterCancel.stage.length, 0);
    checks.push('cancelled collaborative gesture releases its hold; later edits still persist');
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
