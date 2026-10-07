import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import postcss from 'postcss'
import { parse } from '@vue/compiler-sfc'
import { viewportUnitFallback, viewportDeclarations } from './viewportUnitFallback.test.mjs'

const require = createRequire(import.meta.url)
const { chromium } = require(process.env.CODEYUN_PLAYWRIGHT_MODULE || 'playwright')
const read = path => readFile(new URL(`../${path}`, import.meta.url), 'utf8')
const styles = async path => parse(await read(path)).descriptor.styles.map(style => style.content).join('\n')
const css = [await read('src/style.css'), await styles('src/layout/MainLayout.vue'),
  await styles('src/components/layout/WorkbenchShell.vue')].join('\n')

function legacyCss(source, removeFallback = false) {
  const root = postcss.parse(source)
  root.walkDecls(declaration => {
    // 模拟旧内核忽略动态视口单位；对照组也去掉新增的 vh 回退。
    if (/\bdvh\b|\d+dvh/.test(declaration.value)
      || (removeFallback && /\d+vh/.test(declaration.value))) declaration.remove()
  })
  return root.toString()
}

test('app navigation and reader fill the viewport with and without dvh support', async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CODEYUN_CHROME_EXECUTABLE })
  try {
    const page = await browser.newPage()
    for (const mode of ['modern', 'legacy', 'broken-control']) {
      const source = mode === 'modern' ? css : legacyCss(css, mode === 'broken-control')
      await page.setContent(`<!doctype html><style>${source}</style>
        <div id="app"><div class="common-layout"><div class="workbench-shell">
          <div class="shell-body"><aside class="el-aside main-aside" style="width:220px">
            <nav class="main-menu">导航</nav><div class="aside-account">账户</div>
          </aside><main class="shell-center"><header class="shell-chrome">电子书</header>
            <div style="flex:1;min-height:0;overflow:auto">正文</div>
          </main></div></div></div></div>`)
      for (const [width, height] of [[1440, 900], [960, 540], [640, 360]]) {
        await page.setViewportSize({ width, height })
        const geometry = await page.evaluate(() => ({
          layout: document.querySelector('.common-layout').getBoundingClientRect().height,
          navigationBottom: document.querySelector('.main-aside').getBoundingClientRect().bottom,
          readerBottom: document.querySelector('.shell-center').getBoundingClientRect().bottom,
          scrollHeight: document.scrollingElement.scrollHeight,
          scrollWidth: document.scrollingElement.scrollWidth,
        }))
        if (mode === 'broken-control') {
          assert.ok(geometry.layout < height / 2, 'control must reproduce the collapsed layout')
          continue
        }
        for (const key of ['layout', 'navigationBottom', 'readerBottom']) {
          assert.ok(Math.abs(geometry[key] - height) <= 1, `${mode} ${width}x${height}: ${key}=${geometry[key]}`)
        }
        assert.ok(geometry.scrollHeight <= height && geometry.scrollWidth <= width, 'viewport must contain the layout')
      }
    }
  } finally {
    await browser.close()
  }
})

test('all feature viewport expressions keep their sizes in the legacy CSS path', async () => {
  const declarations = await viewportDeclarations()
  const raw = declarations.map((declaration, index) => `#probe${index}{${declaration.property}:${declaration.value}}`).join('\n')
  const transformed = (await postcss([viewportUnitFallback()]).process(raw, { from: undefined })).css
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CODEYUN_CHROME_EXECUTABLE })
  try {
    const page = await browser.newPage()
    const body = declarations.map((_, index) => `<div id="probe${index}"></div>`).join('')
    for (const [width, height] of [[1440, 900], [960, 540], [640, 360]]) {
      await page.setViewportSize({ width, height })
      const measured = []
      for (const source of [transformed, legacyCss(transformed)]) {
        await page.setContent(`<!doctype html><style>${source}</style>${body}`)
        measured.push(await page.evaluate(properties => properties.map((property, index) =>
          getComputedStyle(document.querySelector(`#probe${index}`)).getPropertyValue(property)), declarations.map(declaration => declaration.property)))
      }
      assert.deepEqual(measured[1], measured[0], `${width}x${height}: legacy feature dimensions differ`)
    }
  } finally { await browser.close() }
})

test('window projection fills its workspace including a parent header at every viewport', async () => {
  const source = css + '\n' + await styles('src/components/layout/WorkbenchPane.vue') + '\n' + await styles('src/components/window-projection/WindowProjectionWorkspace.vue')
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CODEYUN_CHROME_EXECUTABLE })
  try {
    const page = await browser.newPage()
    for (const mode of ['modern', 'legacy']) {
      await page.setContent(`<!doctype html><style>${mode === 'legacy' ? legacyCss(source) : source}</style>
        <div id="app"><div class="common-layout"><div class="workbench-shell">
          <header style="height:40px;flex:none">父级菜单</header><div class="shell-body">
            <section class="workbench-pane"><div class="pane-body" style="overflow:hidden">
              <div class="projection"><header style="height:30px;flex:none">窗口</header>
                <div class="stage"><canvas width="600" height="400"></canvas></div>
                <footer class="status-bar">状态</footer>
              </div></div></section></div></div></div></div>`)
      for (const [width, height] of [[1440, 900], [960, 540], [640, 360]]) {
        await page.setViewportSize({ width, height })
        const geometry = await page.evaluate(() => {
          const projection = document.querySelector('.projection').getBoundingClientRect()
          const parent = document.querySelector('.pane-body').getBoundingClientRect()
          return { top: projection.top, bottom: projection.bottom, height: projection.height, parentHeight: parent.height }
        })
        assert.equal(geometry.top, 40)
        assert.equal(geometry.bottom, height, `${mode}: projection does not fill workspace`)
        assert.equal(geometry.height, geometry.parentHeight)
      }
    }
  } finally { await browser.close() }
})
