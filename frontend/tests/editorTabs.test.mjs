import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import test from 'node:test'
import { build } from 'esbuild'
import { parse, compileScript } from '@vue/compiler-sfc'
import { JSDOM } from 'jsdom'

const dom = new JSDOM('<body><div id="app"></div></body>')
for (const key of ['window', 'document', 'Element', 'HTMLElement', 'SVGElement', 'Node']) globalThis[key] = dom.window[key]
dom.window.HTMLElement.prototype.scrollIntoView = function () {}
const { createApp, h, reactive, nextTick } = await import('vue')
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const compiled = await build({
  entryPoints: [path.join(frontend, 'src/components/editor-workspace/EditorTabs.vue')], bundle: true, write: false, format: 'esm', platform: 'node',
  plugins: [{ name: 'tabs-test', setup(builder) {
    builder.onResolve({ filter: /^vue$/ }, () => ({ path: pathToFileURL(path.join(frontend, 'node_modules/vue/index.mjs')).href, external: true }))
    builder.onLoad({ filter: /\.vue$/ }, async ({ path: filename }) => {
      const { descriptor } = parse(await readFile(filename, 'utf8'), { filename })
      return { contents: compileScript(descriptor, { id: filename, inlineTemplate: true }).content, loader: 'ts' }
    })
  } }],
})
const { default: EditorTabs } = await import(`data:text/javascript;base64,${Buffer.from(compiled.outputFiles[0].text).toString('base64')}`)
const settle = async () => { await nextTick(); await new Promise(resolve => setTimeout(resolve, 0)); await nextTick() }
const tabs = count => Array.from({ length: count }, (_, index) => ({ id: String(index), title: `Tab ${index}` }))

test('shared tabs evict by activation, preserve display order and protect fixed/current tabs', async () => {
  const state = reactive({ tabs: tabs(10), active: '9' })
  state.tabs[0].closable = false
  const closed = []
  const app = createApp({ render: () => h(EditorTabs, {
    ...state, onActivate: id => { state.active = id },
    evict: async id => { closed.push(id); state.tabs = state.tabs.filter(tab => tab.id !== id) },
  }) })
  app.mount('#app'); await settle()
  document.querySelector('[role="tab"][title="Tab 1"]').click(); await settle()
  assert.deepEqual(state.tabs.map(tab => tab.id), tabs(10).map(tab => tab.id))
  // Drag/display order is unrelated to LRU priority.
  state.tabs = [...state.tabs].reverse(); await settle()
  state.tabs.push({ id: '10', title: 'Tab 10' }); state.active = '10'; await settle()
  assert.deepEqual(closed, ['2'])
  assert.equal(state.tabs.length, 10)
  state.tabs.push({ id: '11', title: 'Tab 11' }); state.active = '11'; await settle()
  assert.deepEqual(closed, ['2', '3'])
  assert.ok(state.tabs.some(tab => tab.id === '0'))
  assert.ok(state.tabs.some(tab => tab.id === '1'))
  app.unmount()
})

test('restored overflow closes serially, pauses for business operations and stops on rejection', async () => {
  const state = reactive({ tabs: tabs(13), active: '12', evictionPaused: true })
  const closed = []
  let running = 0, peak = 0, reject = true
  const app = createApp({ render: () => h(EditorTabs, {
    ...state,
    evict: async id => {
      running++; peak = Math.max(peak, running); closed.push(id)
      await new Promise(resolve => setTimeout(resolve, 0)); running--
      if (reject) throw new Error('save failed')
      state.tabs = state.tabs.filter(tab => tab.id !== id)
    },
  }) })
  app.mount('#app'); await settle()
  assert.deepEqual(closed, [])
  state.evictionPaused = false; await settle(); await settle()
  assert.deepEqual(closed, ['0'])
  assert.equal(state.tabs.length, 13)
  reject = false; state.evictionPaused = true; await settle(); state.evictionPaused = false
  for (let index = 0; index < 6; index++) await settle()
  assert.deepEqual(closed, ['0', '0', '1', '2'])
  assert.equal(state.tabs.length, 10)
  assert.equal(peak, 1)
  assert.ok(state.tabs.some(tab => tab.id === '12'))
  app.unmount()
})
