import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import test from 'node:test'
import { build } from 'esbuild'
import { compileScript, parse } from '@vue/compiler-sfc'
import { createRenderer, defineComponent, h, nextTick, onMounted, onUnmounted, ref } from 'vue'

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const vueURL = pathToFileURL(path.join(frontend, 'node_modules/vue/index.mjs')).href
// Compile the real SFCs, then use Vue's host renderer to verify component lifetime and UI events.
// This deliberately does not claim pixel/layout verification in a real browser.
const compiled = await build({
  stdin: { contents: "export { default as Workspace } from './src/components/docking/DockWorkspace.vue'; export { useDockLayout } from './src/components/docking/useDockLayout.ts'", resolveDir: frontend },
  bundle: true, write: false, format: 'esm', platform: 'node',
  plugins: [{ name: 'vue-test', setup(builder) {
    builder.onResolve({ filter: /^element-plus$/ }, () => ({ path: pathToFileURL(path.join(frontend, 'node_modules/element-plus/es/index.mjs')).href, external: true }))
    builder.onResolve({ filter: /^vue$/ }, () => ({ path: vueURL, external: true }))
    builder.onLoad({ filter: /\.vue$/ }, async ({ path: filename }) => {
      const { descriptor, errors } = parse(await readFile(filename, 'utf8'), { filename })
      assert.deepEqual(errors, [])
      return { contents: compileScript(descriptor, { id: filename, inlineTemplate: true }).content, loader: 'ts' }
    })
  } }],
})
const { Workspace, useDockLayout } = await import(`data:text/javascript;base64,${Buffer.from(compiled.outputFiles[0].text).toString('base64')}`)

test('tool state and mounted instance survive moves, region hiding, switching, resizing and reset', async () => {
  globalThis.window = new EventTarget()
  window.innerWidth = 640
  window.innerHeight = 480
  const storage = new Map()
  globalThis.localStorage = { getItem: k => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, v) }
  globalThis.ResizeObserver = class { observe() {} disconnect() {} }
  globalThis.requestAnimationFrame = () => 0
  globalThis.cancelAnimationFrame = () => {}
  globalThis.HTMLElement = class { static [Symbol.hasInstance](value) { return !!value?.props } }
  globalThis.getComputedStyle = () => ({ getPropertyValue: () => '' })
  const node = (type, text = '') => ({ type, text, props: {}, children: [], parent: null, style: {},
    focus() {}, querySelector() { return all(this).find(n => n.type === 'button') },
    getBoundingClientRect() { return { width: 160, height: 146 } },
  })
  const root = node('root')
  function all(n = root) { return [n, ...n.children.flatMap(child => all(child))] }
  const renderer = createRenderer({
    createElement: type => node(type), createText: text => node('#text', text), createComment: text => node('#comment', text),
    setText: (n, text) => { n.text = text }, setElementText: (n, text) => { n.text = text; n.children = [] },
    parentNode: n => n.parent, nextSibling: n => n.parent?.children[n.parent.children.indexOf(n) + 1] ?? null,
    querySelector: selector => selector === 'body' ? root : all().find(n => `#${n.props.id}` === selector),
    patchProp: (n, key, old, value) => { n.props[key] = value },
    insert(n, parent, anchor = null) {
      if (n.parent) n.parent.children.splice(n.parent.children.indexOf(n), 1)
      n.parent = parent
      const index = anchor ? parent.children.indexOf(anchor) : -1
      parent.children.splice(index < 0 ? parent.children.length : index, 0, n)
    },
    remove(n) { if (n.parent) n.parent.children.splice(n.parent.children.indexOf(n), 1); n.parent = null },
  })
  const tools = [
    { id: 'toc', title: '目录', icon: 'document', position: 'left', open: true },
    { id: 'search', title: '搜索', icon: 'search', position: 'left' },
    { id: 'info', title: '信息', icon: 'info', position: 'left' },
  ]
  let mounts = 0, unmounts = 0, dock
  const Search = defineComponent({ setup() {
    const query = ref('')
    onMounted(() => mounts++)
    onUnmounted(() => unmounts++)
    return () => h('input', { 'aria-label': '测试搜索词', value: query.value, onInput: e => { query.value = e.target.value } })
  } })
  const App = defineComponent({ setup() {
    dock = useDockLayout('test-dock', tools)
    return () => h(Workspace, { dock }, { default: () => h('article', '正文'), toc: () => h('div', '目录内容'), search: () => h(Search), info: () => h('div', '信息内容') })
  } })
  const app = renderer.createApp(App)
  app.directive('context-menu', {
    mounted(n, binding) { n.props.onContextmenu = binding.value },
    updated(n, binding) { n.props.onContextmenu = binding.value },
  })
  app.mount(root)
  await nextTick()
  assert.equal(mounts, 0, 'closed tools load lazily')
  const searchButton = all().find(n => n.type === 'button' && n.props['aria-label'] === '搜索')
  searchButton.props.onClick({ ctrlKey: true })
  await nextTick()
  assert.equal(mounts, 1)
  const input = all().find(n => n.type === 'input')
  input.props.onInput({ target: { value: '保留的搜索词' } })
  await nextTick()
  for (const position of ['right', 'bottom', 'left']) {
    dock.move('search', position)
    await nextTick()
    assert.equal(input.props.value, '保留的搜索词')
    assert.ok(input.parent.parent.parent.props.id.endsWith(position))
    assert.equal(mounts, 1)
    assert.equal(unmounts, 0)
  }
  dock.move('search', 'bottom')
  dock.toggleRegion('bottom')
  await nextTick()
  assert.equal(dock.visible('search'), false)
  dock.open('search')
  dock.resize('bottom', 320)
  dock.close('search')
  await nextTick()
  dock.open('search')
  await nextTick()
  assert.equal(input.props.value, '保留的搜索词')
  assert.equal(mounts, 1)
  assert.equal(JSON.parse(storage.get('test-dock')).regions.bottom.size, 320)
  // Three simultaneous tools, then remove the first: surviving panels occupy contiguous tracks.
  for (const side of ['left', 'right', 'bottom']) {
    for (const id of ['toc', 'search', 'info']) dock.move(id, side)
    await nextTick()
    assert.equal(dock.state.value.regions[side].active.length, 3)
    const click = (title, ctrlKey = false) => all().find(n => n.type === 'button' && n.props['aria-label'] === title).props.onClick({ ctrlKey })
    click('目录', true)
    await nextTick()
    assert.equal(dock.visible('toc'), false)
    assert.equal(dock.visible('search'), true)
    assert.equal(dock.visible('info'), true)
    const panel = input.parent.parent
    const axis = side === 'bottom' ? 'gridTemplateColumns' : 'gridTemplateRows'
    assert.equal(panel.parent.props.style[axis], 'minmax(0, 1fr) minmax(0, 1fr)')
    assert.equal(panel.props.style[side === 'bottom' ? 'gridColumn' : 'gridRow'], 1)
    assert.notEqual(panel.style.display, 'none')
    dock.split(side, 'search', 'info', .7)
    await nextTick()
    assert.equal(dock.state.value.regions[side].weights.search, 1.4)
    assert.ok(Math.abs(dock.state.value.regions[side].weights.info - .6) < 1e-10)
    dock.split(side, 'search', 'info', .5)
    click('搜索')
    await nextTick()
    assert.deepEqual(dock.state.value.regions[side].active, ['search'])
    click('搜索')
    await nextTick()
    assert.deepEqual(dock.state.value.regions[side].active, [])
    click('搜索', true)
    await nextTick()
    assert.equal(input.props.value, '保留的搜索词')
  }
  // The move menu cascades beside the parent; it must not insert destinations inline.
  const tocButton = all().find(n => n.type === 'button' && n.props['aria-label'] === '目录')
  tocButton.props.onContextmenu({ currentTarget: tocButton, clientX: 600, clientY: 430, preventDefault() {}, stopPropagation() {} })
  await nextTick()
  const menu = all().find(n => n.props.class === 'dock-tool-menu')
  const moveButton = menu.children.find(n => n.type === 'button')
  assert.equal(moveButton.props['aria-expanded'], false)
  moveButton.props.onMouseenter()
  await nextTick()
  const submenu = all(menu).find(n => String(n.props.class).includes('dock-move-submenu'))
  assert.ok(String(submenu.props.class).includes('opens-left'), 'near the right edge the submenu opens to the left')
  const choices = submenu.children.filter(n => n.type === 'button')
  assert.deepEqual(choices.map(n => n.text), ['左侧', '右侧'], 'current bottom position is excluded')
  choices[0].props.onClick()
  await nextTick()
  assert.equal(dock.position('toc'), 'left')
  assert.equal(all().some(n => n.props.class === 'dock-tool-menu'), false)
  const rail = all().find(n => n.type === 'nav' && n.props['aria-label'] === '左侧工具栏')
  rail.props.onContextmenu({ currentTarget: rail, clientX: 20, clientY: 470, preventDefault() {}, stopPropagation() {} })
  await nextTick()
  await nextTick()
  const railMenu = all().find(n => n.props['aria-label'] === '活动栏菜单')
  assert.equal(all(railMenu).filter(n => n.props.role === 'menuitemcheckbox').length, 3)
  assert.ok(parseFloat(railMenu.props.style.top) + 146 <= window.innerHeight)
  assert.equal(all(railMenu).some(n => n.text.includes('主题')), false)
  all(railMenu).find(n => n.text === '恢复默认布局').props.onClick()
  await nextTick()
  assert.equal(all().some(n => n.props.class === 'dock-tool-menu'), false)
  dock.reset()
  await nextTick()
  assert.equal(dock.visible('toc'), true)
  assert.equal(dock.position('search'), 'left')
  assert.equal(input.props.value, '保留的搜索词')
  app.unmount()
  assert.equal(unmounts, 1)
})
