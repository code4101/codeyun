import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import test from 'node:test'
import { build } from 'esbuild'
import { parse, compileScript } from '@vue/compiler-sfc'
import { JSDOM } from 'jsdom'

const dom = new JSDOM('<body><div id="app"></div></body>', { url: 'http://localhost/' })
for (const key of ['window', 'document', 'Element', 'HTMLElement', 'SVGElement', 'Node', 'localStorage']) globalThis[key] = dom.window[key]
globalThis.ResizeObserver = class { observe() {} disconnect() {} }
globalThis.requestAnimationFrame = () => 0
globalThis.cancelAnimationFrame = () => {}
dom.window.HTMLElement.prototype.scrollIntoView = function () {}
const { nextTick } = await import('vue')
const { createPinia, setActivePinia } = await import('pinia')
const { createApp } = await import('vue')
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const base = path.join(frontend, 'src/standard/pdf/library')
const mocks = {
  api: `import {applyLocalWorkspaceCommand} from '${path.join(base, 'readerWorkspaceState.ts').replaceAll('\\', '/')}';
    export const mock = { states: new Map(), user: 1, fail: false, calls: [], mounts: 0, unmounts: 0 };
    const empty = () => ({tabs:[], active:'', layout:{}, revision:0});
    export default {async get(){if(mock.fail) throw new Error('offline'); return {data:structuredClone(mock.states.get(mock.user) ?? empty())}},
      async post(url,command){if(mock.fail) throw new Error('offline'); mock.calls.push(command);
        const state=applyLocalWorkspaceCommand(mock.states.get(mock.user) ?? empty(),command); mock.states.set(mock.user,state); return {data:structuredClone(state)}}};`,
  user: `import {defineStore} from 'pinia'; import {ref,computed} from 'vue'; export const useUserStore=defineStore('test-user',()=>{const user=ref({id:1}); const isAuthenticated=computed(()=>!!user.value); return {user,isAuthenticated}})`,
  router: `export const useRouter=()=>({resolve:()=>({href:'/reader'})})`,
  element: `import {defineComponent,h,ref} from 'vue'; export const ElDialog=defineComponent({props:['modelValue'],emits:['update:modelValue'],setup(p,{slots,emit}){const once=ref(false);return()=>{if(p.modelValue)once.value=true;return h('div',{style:{display:p.modelValue?'':'none'}},once.value?[slots.header?.(),slots.default?.(),h('button',{'data-close-dialog':'',onClick:()=>emit('update:modelValue',false)},'close')]:[])}}}); export const ElMenu=ElDialog, ElMenuItem=ElDialog, ElSubMenu=ElDialog;`,
  shelf: `import {h} from 'vue'; export default {render:()=>h('input',{'data-shelf-search':'',placeholder:'搜索图书'})}`,
  library: `import {h} from 'vue'; export default {render:()=>h('div','图书馆')}`,
  context: `export default {setup(p,{expose}){expose({open(){}});return()=>null}}`,
  menu: `import {h} from 'vue'; export default {props:['items'],emits:['select'],setup(p,{emit}){
    const render=items=>items.map(item=>item.children?h('section',{'data-menu':item.id},render(item.children)):h('button',{'data-command':item.id,disabled:item.disabled,onClick:()=>emit('select',item.id)},item.label));
    return()=>h('header',{class:'workspace-menu'},render(p.items))}}`,
  plugins: `import {defineAsyncComponent,defineComponent,h,ref,onMounted,onUnmounted} from 'vue';
    import {mock} from 'api';
    import {useReaderDock} from '${path.join(base, 'readerWorkspaceContext.ts').replaceAll('\\', '/')}';
    import Layout from '${path.join(base, 'ReaderDockLayout.vue').replaceAll('\\', '/')}';
    const reader=defineComponent({props:['bookId'],setup(p){const query=ref('');const dock=useReaderDock('test',[{id:'toc',title:'目录',icon:'document',position:'left',open:true}]);
      onMounted(()=>mock.mounts++);onUnmounted(()=>mock.unmounts++);
      return()=>h(Layout,{dock},{default:()=>h('input',{'data-book':p.bookId,value:query.value,onInput:e=>query.value=e.target.value}),toc:()=>h('span',p.bookId),settings:()=>h('div',{'data-settings':p.bookId})})}});
    const plugin={component:reader,props:tab=>({bookId:tab.id})}; export const readerPlugins={pdf:plugin,ebook:plugin,skill:{...plugin,component:defineAsyncComponent(()=>new Promise(resolve=>{mock.resolveReader=()=>resolve(reader)}))}};`,
}
const compiled = await build({
  stdin: { contents: `export {default as Workspace} from './src/standard/pdf/library/ReaderWorkspace.vue'; export {useReaderWorkspace} from './src/standard/pdf/library/useReaderWorkspace.ts'; export {useUserStore} from '@/store/userStore'; export {mock} from 'api';`, resolveDir: frontend },
  bundle: true, write: false, format: 'esm', platform: 'node',
  plugins: [{ name: 'workspace-test', setup(builder) {
    builder.onResolve({ filter: /\/WorkspaceMenu\.vue$/ }, () => ({ namespace: 'mock', path: 'menu' }))
    builder.onResolve({ filter: /^(vue|pinia)$/ }, args => ({ path: pathToFileURL(path.join(frontend, args.path === 'vue' ? 'node_modules/vue/index.mjs' : 'node_modules/pinia/dist/pinia.mjs')).href, external: true }))
    builder.onResolve({ filter: /^(api|@\/api|@\/store\/userStore|vue-router|element-plus)$/ }, args => ({ namespace: 'mock', path: ({ api:'api', '@/api':'api', '@/store/userStore':'user', 'vue-router':'router', 'element-plus':'element' })[args.path] }))
    builder.onResolve({ filter: /(?:readerPlugins|BookshelfView\.vue|ReaderContextMenu\.vue)$/ }, args => ({ namespace: 'mock', path: args.path.includes('BookshelfView') ? 'shelf' : args.path.includes('readerPlugins') ? 'plugins' : 'context' }))
    builder.onResolve({ filter: /^@\// }, args => ({ path: path.join(frontend, 'src', args.path.slice(2)) + (path.extname(args.path) ? '' : '.ts') }))
    builder.onResolve({ filter: /\.css$/ }, args => ({ path: args.path, namespace: 'css' }))
    builder.onLoad({ filter: /.*/, namespace: 'css' }, () => ({ contents: '' }))
    builder.onLoad({ filter: /.*/, namespace: 'mock' }, args => ({ contents: mocks[args.path], resolveDir: frontend, loader: 'ts' }))
    builder.onLoad({ filter: /\.vue$/ }, async ({ path: filename }) => {
      const { descriptor, errors } = parse(await readFile(filename, 'utf8'), { filename })
      assert.deepEqual(errors, [])
      return { contents: compileScript(descriptor, { id: filename, inlineTemplate: true }).content, loader: 'ts' }
    })
  } }],
})
const { Workspace, useReaderWorkspace, useUserStore, mock } = await import(`data:text/javascript;base64,${Buffer.from(compiled.outputFiles[0].text).toString('base64')}`)
const settle = async () => { await nextTick(); await new Promise(resolve => setTimeout(resolve, 0)); await nextTick() }

test('workspace preserves reader instances across tab switches and hiding, restores server tabs and isolates accounts', async () => {
  const pinia = createPinia()
  setActivePinia(pinia)
  const app = createApp(Workspace, { standalone: false })
  app.use(pinia)
  app.directive('context-menu', {})
  app.mount('#app')
  const workspace = useReaderWorkspace()
  await workspace.open({kind:'ebook',id:'a',title:'A'})
  await settle()
  const input = document.querySelector('[data-book="a"]')
  assert.ok(input)
  const windowMenu = document.querySelector('[data-menu="window"]')
  assert.equal(windowMenu.querySelector('[data-command^="tool:"]'), null, 'tools are controlled by the activity bar')
  assert.equal(windowMenu.querySelector('[data-command="close-tools"]'), null)
  const opened = []
  window.open = (...args) => opened.push(args)
  windowMenu.querySelector('[data-command="open-standalone"]').click()
  assert.deepEqual(opened, [['/reader', '_blank', 'noopener,noreferrer']])
  assert.equal(workspace.state.active, 'ebook:a')
  assert.equal(document.querySelector('[data-menu="view"]'), null)
  const regionCommand = windowMenu.querySelector('[data-command="layout:region:left"]')
  assert.ok(regionCommand, 'region controls belong to the window menu')
  const wasVisible = workspace.dock.state.value.regions.left.visible
  regionCommand.click()
  await settle()
  assert.equal(workspace.dock.state.value.regions.left.visible, !wasVisible)
  workspace.dock.move('toc', 'bottom')
  windowMenu.querySelector('[data-command="layout:reset"]').click()
  await settle()
  assert.equal(workspace.dock.state.value.regions.left.visible, true)
  assert.equal(workspace.dock.position('toc'), 'left')
  const center = input.closest('main.reader-content')
  const tabs = center.querySelector('[role="tablist"]')
  assert.ok(tabs, 'book tabs belong to the central reading column')
  assert.ok(center.firstElementChild.contains(tabs), 'tabs precede reading content')
  assert.equal(tabs.closest('.dock-region'), null, 'tabs never occupy the tool regions')
  input.value = 'A 的搜索状态'
  input.dispatchEvent(new dom.window.Event('input', {bubbles:true}))
  await workspace.open({kind:'pdf',id:'b',title:'B'})
  await settle()
  assert.equal(mock.mounts, 2)
  assert.ok(document.querySelector('[data-book="b"]').closest('main.reader-content').querySelector('[role="tablist"]'))
  assert.equal(mock.unmounts, 0)
  await workspace.open({kind:'ebook',id:'a'})
  await settle()
  assert.equal(document.querySelector('[data-book="a"]'), input)
  assert.equal(input.value, 'A 的搜索状态')
  assert.equal(workspace.state.tabs.length, 2)
  workspace.visible = false
  await settle()
  assert.equal(workspace.visible, false)
  await workspace.open({kind:'ebook',id:'c',title:'C'})
  await settle()
  assert.deepEqual(workspace.state.tabs.map(tab=>tab.id), ['a','b','c'])
  assert.equal(workspace.state.active,'ebook:c')
  assert.equal(mock.unmounts,0)
  workspace.dock.resize('left', 380)
  await new Promise(resolve=>setTimeout(resolve,250))
  assert.equal(mock.states.get(1).layout.regions.left.size,380)
  await workspace.command({action:'close',key:'pdf:b'})
  await settle()
  assert.equal(mock.unmounts,1)
  mock.fail=true
  await workspace.open({kind:'ebook',id:'d'})
  assert.equal(workspace.state.active, 'ebook:d')
  assert.equal(document.querySelector('.workspace-error'), null)
  mock.fail=false
  await new Promise(resolve => setTimeout(resolve, 1100))
  assert.equal(workspace.state.active,'ebook:d')
  assert.equal(mock.states.get(1).active, 'ebook:d')
  app.unmount()

  setActivePinia(createPinia())
  const restored=useReaderWorkspace()
  await restored.initialize()
  assert.deepEqual(restored.state.tabs.map(tab=>tab.id),['a','c','d'])
  assert.equal(restored.dock.state.value.regions.left.size,380)
  mock.user=2
  useUserStore().user={id:2}
  await settle()
  assert.deepEqual(restored.state.tabs,[])
  await restored.open({kind:'ebook',id:'other'})
  assert.deepEqual(restored.state.tabs.map(tab=>tab.id),['other'])
  assert.deepEqual(mock.states.get(1).tabs.map(tab=>tab.id),['a','c','d'])
})


test('idle readers unload after thirty minutes while their tabs remain available', async t => {
  t.mock.timers.enable({ apis: ['Date', 'setInterval'], now: 1000 })
  mock.states.clear()
  mock.user = 1
  mock.fail = false
  const pinia = createPinia()
  const mountedApp = createApp(Workspace, { standalone: false })
  mountedApp.use(pinia)
  setActivePinia(pinia)
  mountedApp.directive('context-menu', {})
  mountedApp.mount('#app')
  t.after(() => mountedApp.unmount())
  const store = useReaderWorkspace()
  await store.open({kind:'ebook',id:'idle-a',title:'A'})
  await settle()
  const original = document.querySelector('[data-book="idle-a"]')
  await store.open({kind:'ebook',id:'idle-b',title:'B'})
  await settle()
  t.mock.timers.tick(29 * 60 * 1000)
  await settle()
  assert.equal(document.querySelector('[data-book="idle-a"]'), original)
  t.mock.timers.tick(60 * 1000)
  await settle()
  assert.equal(document.querySelector('[data-book="idle-a"]'), null)
  assert.ok(document.querySelector('[data-book="idle-b"]'), 'active reader is never evicted')
  assert.deepEqual(store.state.tabs.map(tab => tab.id), ['idle-a', 'idle-b'])
  await store.open({kind:'ebook',id:'idle-a'})
  await settle()
  assert.ok(document.querySelector('[data-book="idle-a"]'))
  assert.notEqual(document.querySelector('[data-book="idle-a"]'), original)
  store.visible = false
  await settle()
  t.mock.timers.tick(30 * 60 * 1000)
  await settle()
  assert.equal(document.querySelectorAll('[data-book]').length, 0)
  assert.equal(store.state.tabs.length, 2)
  store.visible = true
  await settle()
  assert.ok(document.querySelector('[data-book="idle-a"]'))
  assert.equal(document.querySelector('[data-book="idle-b"]'), null, 'only the selected shortcut reloads')
})


test('offline loading and successive edits retry silently without losing local actions', async () => {
  mock.states.clear()
  mock.user = 1
  mock.fail = true
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useReaderWorkspace()
  await store.initialize()
  await store.open({kind:'ebook',id:'offline-a'})
  await store.open({kind:'ebook',id:'offline-b'})
  await store.command({action:'close',key:'ebook:offline-a'})
  await store.command({action:'layout',layout:{version:2,regions:{left:{size:420}}}})
  assert.deepEqual(store.state.tabs.map(tab => tab.id), ['offline-b'])
  assert.equal(store.state.active, 'ebook:offline-b')
  assert.equal(store.state.layout.regions.left.size, 420)
  mock.fail = false
  window.dispatchEvent(new dom.window.Event('online'))
  await settle()
  assert.deepEqual(mock.states.get(1).tabs.map(tab => tab.id), ['offline-b'])
  assert.equal(mock.states.get(1).active, 'ebook:offline-b')
  assert.equal(mock.states.get(1).layout.regions.left.size, 420)
  assert.deepEqual(store.state.tabs.map(tab => tab.id), ['offline-b'])
})


test('bookshelf is a singleton functional tab and preserves browsing while switching books', async () => {
  mock.states.clear(); mock.calls.length = 0; mock.user = 1; mock.fail = false
  const pinia = createPinia()
  setActivePinia(pinia)
  const app = createApp(Workspace, { standalone: true })
  app.use(pinia); app.directive('context-menu', {}); app.mount('#app')
  try {
    const store = useReaderWorkspace()
    await store.initialize()
    store.openShelf(); store.openShelf()
    await settle(); await settle()
    assert.equal(document.querySelectorAll('.workspace-shelf').length, 1)
    const search = document.querySelector('[data-shelf-search]')
    assert.ok(search)
    search.value = '资本论'
    await store.open({kind:'ebook',id:'from-shelf',title:'资本论'})
    await settle()
    assert.equal(store.shelfActive, false)
    assert.equal(document.querySelector('.workspace-shelf').style.display, 'none')
    assert.ok(document.querySelector('[data-book="from-shelf"]'))
    store.dock.move('toc', 'right'); store.dock.open('toc'); store.dock.resize('right', 350)
    await settle()
    const host = document.querySelector('[data-book="from-shelf"]').closest('.reader-tab-host')
    const tool = host.querySelector('[data-dock-tool="toc"]')
    const layoutBefore = host.querySelector('.dock-workspace').getAttribute('style')
    const toolParent = tool.parentElement
    await store.activateTab('view:bookshelf'); await settle()
    assert.equal(host.querySelector('[data-dock-tool="toc"]'), tool, 'tool instance survives switching to the shelf')
    assert.equal(tool.parentElement, toolParent, 'tool remains in the same region')
    assert.notEqual(tool.style.display, 'none')
    assert.equal(host.querySelector('.dock-workspace').getAttribute('style'), layoutBefore)
    assert.equal(search.closest('.reader-tab-host'), host, 'shelf occupies the existing central region')
    assert.equal(document.querySelector('[data-shelf-search]'), search)
    assert.equal(search.value, '资本论')
    assert.equal(document.querySelector('[data-book="from-shelf"]').closest('.reader-document-content').style.display, 'none')
    assert.notEqual(document.querySelector('[data-book="from-shelf"]').closest('.reader-tab-host').style.display, 'none')
    assert.equal(mock.calls.some(c => c.tab?.kind === 'bookshelf' || c.key === 'view:bookshelf'), false)
    await store.open({kind:'ebook',id:'another-book',title:'另一本书'}); await settle()
    await store.activateTab('view:bookshelf'); await settle()
    assert.equal(document.querySelector('[data-shelf-search]'), search, 'moving between reader layouts preserves the shelf instance')
    assert.equal(search.closest('.reader-tab-host'), document.querySelector('[data-book="another-book"]').closest('.reader-tab-host'))
    await store.closeTab('ebook:another-book'); await settle()
    assert.equal(search.closest('.reader-tab-host'), host, 'closing the current resource rehomes the shelf safely')

    await store.closeTab('view:bookshelf'); await settle()
    assert.equal(store.shelfActive, false)
    assert.equal(document.querySelector('.workspace-shelf'), null)
    store.openShelf()
    await store.activateTab('ebook:from-shelf')
    await store.closeTab('ebook:from-shelf'); await settle()
    assert.equal(store.shelfActive, true, 'closing the last book returns to the open shelf')
    assert.ok(document.querySelector('[data-shelf-search]').closest('.reader-functional-content'))
  } finally { app.unmount() }
})


test('cold reader load retains themed dock and tabs until the module is ready', async () => {
  mock.states.clear(); mock.user = 1; mock.fail = false
  const pinia = createPinia(); setActivePinia(pinia)
  const app = createApp(Workspace, {standalone:true}); app.use(pinia); app.directive('context-menu', {}); app.mount('#app')
  try {
    const store = useReaderWorkspace()
    await store.open({kind:'skill',id:'cold',title:'延迟加载'})
    await settle()
    const host = document.querySelector('.reader-tab-host')
    assert.ok(host.closest('.library-reader-theme-dialog'))
    assert.ok(host.querySelector('.dock-workspace'))
    assert.ok(host.querySelector('[role="tablist"]'))
    assert.equal(host.querySelector('[role="status"]').textContent.trim(), '正在加载阅读器…')
    mock.resolveReader(); await settle(); await settle()
    assert.equal(host.querySelector('[role="status"]'), null)
    assert.ok(host.querySelector('[data-book="cold"]'))
    assert.ok(host.querySelector('[role="tablist"]'))
  } finally { app.unmount() }
})


test('settings opens as a central functional tab from the menu and closes cleanly', async () => {
  mock.states.clear(); mock.user = 1; mock.fail = false
  const pinia = createPinia(); setActivePinia(pinia)
  const app = createApp(Workspace, {standalone:true}); app.use(pinia); app.directive('context-menu', {}); app.mount('#app')
  try {
    const store = useReaderWorkspace()
    await store.open({kind:'ebook',id:'s1',title:'S1'})
    await settle()
    assert.equal(store.settingsActive, false)
    document.querySelector('[data-command="open-settings"]').click()
    await settle()
    assert.equal(store.settingsActive, true)
    const selected = document.querySelector('[role="tab"][aria-selected="true"]')
    assert.equal(selected.textContent.trim(), '设置')
    assert.equal(document.querySelector('[data-settings="s1"]').closest('.reader-settings-content').style.display, '')
    assert.equal(document.querySelector('[data-book="s1"]').closest('.reader-document-content').style.display, 'none')
    await store.activateTab('ebook:s1'); await settle()
    assert.equal(store.settingsActive, false)
    assert.equal(document.querySelector('[data-book="s1"]').closest('.reader-document-content').style.display, '')
    await store.activateTab('view:settings'); await settle()
    assert.equal(store.settingsActive, true)
    await store.closeTab('view:settings'); await settle()
    assert.equal(store.settingsOpen, false)
    assert.equal(store.settingsActive, false)
    assert.equal(document.querySelector('[role="tab"][aria-selected="true"]').textContent.trim(), 'S1')
  } finally { app.unmount() }
})
