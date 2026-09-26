/** Browser acceptance against the public UI and exported PRG files.
 * node smoke.cjs <url> <upstream-checkout> <temporary-output-directory> <playwright-module>
 * Uses an isolated, disposable browser context; never reads application databases.
 */
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs/promises');
const { createRequire } = require('node:module');
const [url, checkout, output, playwrightModule = 'playwright'] = process.argv.slice(2);
const { chromium } = require(playwrightModule);
const upstreamRequire = createRequire(path.resolve(checkout, 'app/package.json'));
const { ZipReader, Uint8ArrayReader, Uint8ArrayWriter } = upstreamRequire('@zip.js/zip.js');
const { decode } = upstreamRequire('@msgpack/msgpack');

async function main() {
  await fs.mkdir(output, { recursive: true });
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1680, height: 1000 } });
    const errors = [];
    context.on('page', page => page.on('pageerror', error => errors.push(String(error))));
    const page = await context.newPage();
    const frame = () => page.frames().find(frame => frame.url().includes('embed.html'));
    const ready = async target => {
      await target.getByRole('status').filter({ hasText: '已保存' }).waitFor({ timeout: 45000 });
    };
    const exportStage = async (target, name) => {
      const download = target.waitForEvent('download');
      await target.locator('.files button.active').click({ button: 'right' });
      await target.getByRole('menuitem', { name: '下载文件', exact: true }).click();
      const file = path.join(output, name + '.prg');
      await (await download).saveAs(file);
      const zip = new ZipReader(new Uint8ArrayReader(await fs.readFile(file)));
      try {
        const entry = (await zip.getEntries()).find(entry => entry.filename === 'stage.msgpack');
        return decode(await entry.getData(new Uint8ArrayWriter()));
      } finally { await zip.close(); }
    };
    const createNode = async (target, x, y, text) => {
      const editorFrame = target.frames().find(frame => frame.url().includes('embed.html'));
      await editorFrame.locator('canvas').dblclick({ position: { x, y } });
      await editorFrame.locator('textarea').fill(text);
      await target.keyboard.press('Escape');
      await target.waitForTimeout(1400);
    };
    await page.goto(url);
    await page.getByRole('navigation', { name: '图文件', exact: true }).click({ button: 'right', position: { x: 30, y: 100 } });
    await page.getByRole('menuitem', { name: '新建图文件', exact: true }).click();
    await page.getByLabel('名称', { exact: true }).fill('验收图文件');
    await page.getByRole('button', { name: '确定', exact: true }).click();
    await page.getByRole('dialog').waitFor({ state: 'hidden' });
    await ready(page);
    assert.equal(await frame().locator('[data-pg-tab-bar]').isVisible(), false, 'the host title replaces the lone canvas tab');
    await createNode(page, 400, 250, '集成验收节点');
    await frame().locator('[data-codeyun-details] header').filter({ hasText: '集成验收节点' }).waitFor();
    await frame().locator('[contenteditable=true]').fill('Markdown 正文：中文保存与刷新恢复');
    await page.waitForTimeout(1600);
    const pane = frame().locator('[data-codeyun-details]');
    const initialWidth = (await pane.boundingBox()).width;
    const resize = async (target, label, dx, dy) => {
      const editorFrame = target.frames().find(frame => frame.url().includes('embed.html'));
      const handle = await editorFrame.getByRole('separator', { name: label, exact: true }).boundingBox();
      await target.mouse.move(handle.x + handle.width / 2, handle.y + handle.height / 2);
      await target.mouse.down();
      await target.mouse.move(handle.x + handle.width / 2 + dx, handle.y + handle.height / 2 + dy, { steps: 10 });
      await target.mouse.up();
    };
    await frame().locator('[contenteditable=true]').evaluate(element => element.setAttribute('data-layout-test', 'retained'));
    await resize(page, '调整正文宽度', -80, 0);
    assert.ok(Math.abs((await pane.boundingBox()).width - initialWidth - 80) < 3);
    await resize(page, '调整正文宽度', 80, 0);
    await frame().getByLabel('正文位置').selectOption('bottom');
    assert.ok((await pane.boundingBox()).width > 1400);
    const initialHeight = (await pane.boundingBox()).height;
    await resize(page, '调整正文高度', 0, -60);
    const bottomHeight = (await pane.boundingBox()).height;
    assert.ok(Math.abs(bottomHeight - initialHeight - 60) < 3);
    assert.equal(await frame().locator('[contenteditable=true]').getAttribute('data-layout-test'), 'retained', 'docking must not remount the editor');
    assert.ok((await frame().locator('[contenteditable=true]').innerText()).includes('中文保存与刷新恢复'));
    await frame().getByLabel('正文位置').selectOption('right');
    assert.ok(Math.abs((await pane.boundingBox()).width - initialWidth) < 3);
    await page.screenshot({ path: path.join(output, 'node-details.png') });
    await createNode(page, 950, 520, '关联节点');
    const details = () => frame().locator('[data-codeyun-details] [contenteditable=true]');
    assert.equal((await details().innerText()).trim(), '', 'new selection must show its own empty document');
    await details().fill('第二个节点的独立正文');
    await frame().locator('canvas').click({ position: { x: 410, y: 250 } });
    await details().filter({ hasText: 'Markdown 正文：中文保存与刷新恢复' }).waitFor();
    await details().fill('Markdown 正文：中文保存与刷新恢复，快速切换追加');
    await frame().locator('canvas').click({ position: { x: 950, y: 520 } });
    await details().filter({ hasText: '第二个节点的独立正文' }).waitFor();
    await details().evaluate(element => element.setAttribute('data-sticky-test', 'retained'));
    await frame().locator('canvas').click({ position: { x: 60, y: 80 } });
    await page.waitForTimeout(150);
    assert.equal(await details().getAttribute('data-sticky-test'), 'retained', 'blank click must retain the open editor');
    await details().fill('第二个节点的独立正文，取消选择后继续编辑');
    await frame().locator('canvas').click({ position: { x: 410, y: 250 } });
    await details().filter({ hasText: '快速切换追加' }).waitFor();
    await details().evaluate(element => element.setAttribute('data-sticky-test', 'multiple'));
    await frame().locator('canvas').click({ position: { x: 950, y: 520 }, modifiers: ['Shift'] });
    await page.waitForTimeout(150);
    assert.equal(await details().getAttribute('data-sticky-test'), 'multiple', 'multiple selection must retain the open editor');
    await frame().locator('canvas').click({ position: { x: 410, y: 250 } });
    const canvas = await frame().locator('canvas').boundingBox();
    await page.mouse.move(canvas.x + 410, canvas.y + 250);
    await page.mouse.down({ button: 'right' });
    await page.mouse.move(canvas.x + 950, canvas.y + 520, { steps: 16 });
    await page.mouse.up({ button: 'right' });
    await page.keyboard.press('Escape');
    await page.waitForTimeout(1500);
    const first = await exportStage(page, 'before-reload');
    assert.ok(JSON.stringify(first).includes('Markdown 正文：中文保存与刷新恢复'));
    assert.ok(first.some(item => item._ === 'LineEdge'), 'right-drag should create a persisted connection');
    assert.ok(JSON.stringify(first.find(item => item.text === '集成验收节点').details).includes('快速切换追加'));
    assert.ok(!JSON.stringify(first.find(item => item.text === '集成验收节点').details).includes('第二个节点'));
    assert.ok(JSON.stringify(first.find(item => item.text === '关联节点').details).includes('第二个节点的独立正文'));
    assert.ok(JSON.stringify(first.find(item => item.text === '关联节点').details).includes('取消选择后继续编辑'));
    await page.reload();
    await ready(page);
    assert.deepEqual(await exportStage(page, 'after-reload'), first, 'refresh must preserve the native stage including rich text');

    // Import into a new resource and check semantic equality, not ZIP timestamps.
    const beforeImport = page.url();
    await page.locator('input[type=file]').setInputFiles(path.join(output, 'before-reload.prg'));
    await page.waitForURL(value => value.href !== beforeImport);
    await ready(page);
    assert.deepEqual(await exportStage(page, 'after-import'), first, 'PRG import/export must retain structured details');

    // Same-origin two-tab conflict: the stale tab must keep its edits and be exportable.
    const second = await context.newPage();
    await second.goto(page.url());
    await ready(second);
    await createNode(page, 950, 560, '第一页新增');
    await createNode(second, 1000, 600, '第二页冲突保留');
    await second.getByRole('alert').filter({ hasText: '另一页面已更新' }).waitFor();
    const conflict = await exportStage(second, 'conflict-backup');
    assert.ok(JSON.stringify(conflict).includes('第二页冲突保留'));
    const canonical = await context.newPage();
    await canonical.goto(page.url());
    await ready(canonical);
    const persisted = JSON.stringify(await exportStage(canonical, 'conflict-canonical'));
    assert.ok(persisted.includes('第一页新增'));
    assert.ok(!persisted.includes('第二页冲突保留'));
    const canonicalFrame = () => canonical.frames().find(frame => frame.url().includes('embed.html'));
    await canonicalFrame().getByLabel('正文位置').selectOption('bottom');
    await canonical.reload();
    await ready(canonical);
    assert.equal(await canonicalFrame().getByLabel('正文位置').inputValue(), 'bottom');
    assert.ok(Math.abs((await canonicalFrame().locator('[data-codeyun-details]').boundingBox()).height - bottomHeight) < 3);
    const confirmDialog = async () => {
      await canonical.getByRole('button', { name: '确定', exact: true }).click();
      await canonical.getByRole('dialog').waitFor({ state: 'hidden', timeout: 45000 });
    };
    const fileAction = async name => {
      if (!await canonical.locator('.files button.active').count()) await canonical.getByRole('navigation', { name: '文件夹', exact: true }).getByRole('button', { name: '▱ 文件', exact: true }).click();
      await canonical.locator('.files button.active').click({ button: 'right' });
      await canonical.getByRole('menuitem', { name, exact: true }).click();
    };
    assert.equal(await canonical.getByRole('button', { name: '新建文件夹', exact: true }).count(), 0);
    await canonical.getByRole('navigation', { name: '图文件', exact: true }).click({ button: 'right', position: { x: 30, y: 300 } });
    await canonical.getByRole('menuitem', { name: '新建文件夹', exact: true }).click();
    await canonical.getByLabel('名称', { exact: true }).fill('研究资料');
    await confirmDialog();
    await canonical.getByRole('navigation', { name: '文件夹', exact: true }).getByRole('button', { name: '研究资料' }).click();
    await canonical.getByRole('navigation', { name: '文件夹', exact: true }).getByRole('button', { name: '研究资料' }).click({ button: 'right' });
    await canonical.getByRole('menuitem', { name: '重命名文件夹', exact: true }).click();
    await canonical.getByLabel('名称', { exact: true }).fill('灵感草稿');
    await confirmDialog();
    await fileAction('另存为副本');
    await canonical.getByLabel('名称', { exact: true }).fill('结构草稿');
    await confirmDialog();
    const copied = await exportStage(canonical, 'library-copy');
    assert.ok(JSON.stringify(copied).includes('第一页新增'), 'copy must preserve content');
    await fileAction('重命名');
    await canonical.getByLabel('名称', { exact: true }).fill('项目结构');
    await confirmDialog();
    await canonical.reload(); await ready(canonical);
    await canonical.getByRole('navigation', { name: '图文件', exact: true }).getByRole('button', { name: '项目结构' }).waitFor();
    await fileAction('移动到…');
    await canonical.getByLabel('目标文件夹', { exact: true }).selectOption('');
    await confirmDialog();
    await canonical.getByRole('navigation', { name: '图文件', exact: true }).getByRole('button', { name: '项目结构' }).waitFor();
    await canonical.screenshot({ path: path.join(output, 'file-library.png') });
    await fileAction('删除'); await confirmDialog();
    assert.equal(await canonical.getByRole('navigation', { name: '图文件', exact: true }).getByRole('button', { name: '项目结构' }).count(), 0);
    await canonical.getByRole('navigation', { name: '文件夹', exact: true }).getByRole('button', { name: '灵感草稿' }).click();
    await canonical.getByRole('navigation', { name: '文件夹', exact: true }).getByRole('button', { name: '灵感草稿' }).click({ button: 'right' });
    await canonical.getByRole('menuitem', { name: '删除文件夹', exact: true }).click();
    await confirmDialog();
    await canonical.reload();
    assert.equal(await canonical.getByRole('navigation', { name: '文件夹', exact: true }).getByRole('button', { name: '灵感草稿' }).count(), 0);
    assert.deepEqual(errors, [], 'no uncaught browser errors');
    console.log('PASS: file folders, copy/rename/move/delete, refresh persistence, resizable docking, selection following, save, PRG round trip, conflict recovery');
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
