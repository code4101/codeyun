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
    export default {async get(){return {data:structuredClone(mock.states.get(mock.user) ?? empty())}},
      async post(url,command){if(mock.fail) throw new Error('offline'); mock.calls.push(command);
        const state=applyLocalWorkspaceCommand(mock.states.get(mock.user) ?? empty(),command); mock.states.set(mock.user,state); return {data:structuredClone(state)}}};`,
  user: `import {defineStore} from 'pinia'; import {ref,computed} from 'vue'; export const useUserStore=defineStore('test-user',()=>{const user=ref({id:1}); const isAuthenticated=computed(()=>!!user.value); return {user,isAuthenticated}})`,
  router: `export const useRouter=()=>({resolve:()=>({href:'/reader'})})`,
  element: `import {defineComponent,h,ref} from 'vue'; export const ElDialog=defineComponent({props:['modelValue'],emits:['update:modelValue'],setup(p,{slots,emit}){const once=ref(false);return()=>{if(p.modelValue)once.value=true;return h('div',{style:{display:p.modelValue?'':'none'}},once.value?[slots.header?.(),slots.default?.(),h('button',{'data-close-dialog':'',onClick:()=>emit('update:modelValue',false)},'close')]:[])}}}); export const ElMenu=ElDialog, ElMenuItem=ElDialog, ElSubMenu=ElDialog;`,
  library: `import {h} from 'vue'; export default {render:()=>h('div','图书馆')}`,
  context: `export default {setup(p,{expose}){expose({open(){}});return()=>null}}`,
  plugins: `import {defineComponent,h,ref,onMounted,onUnmounted} from 'vue';
    import {mock} from 'api';
    import {useReaderDock} from '${path.join(base, 'readerWorkspaceContext.ts').replaceAll('\\', '/')}';
    import Layout from '${path.join(base, 'ReaderDockLayout.vue').replaceAll('\\', '/')}';
    const reader=defineComponent({props:['bookId'],setup(p){const query=ref('');const dock=useReaderDock('test',[{id:'toc',title:'目录',icon:'document',position:'left',open:true}]);
      onMounted(()=>mock.mounts++);onUnmounted(()=>mock.unmounts++);
      return()=>h(Layout,{dock},{default:()=>h('input',{'data-book':p.bookId,value:query.value,onInput:e=>query.value=e.target.value}),toc:()=>h('span',p.bookId)})}});
    const plugin={component:reader,props:tab=>({bookId:tab.id})}; export const readerPlugins={pdf:plugin,ebook:plugin,skill:plugin};`,
}
const compiled = await build({
  stdin: { contents: `export {default as Workspace} from './src/standard/pdf/library/ReaderWorkspace.vue'; export {useReaderWorkspace} from './src/standard/pdf/library/useReaderWorkspace.ts'; export {useUserStore} from '@/store/userStore'; export {mock} from 'api';`, resolveDir: frontend },
  bundle: true, write: false, format: 'esm', platform: 'node',
  plugins: [{ name: 'workspace-test', setup(builder) {
    builder.onResolve({ filter: /^(vue|pinia)$/ }, args => ({ path: pathToFileURL(path.join(frontend, args.path === 'vue' ? 'node_modules/vue/index.mjs' : 'node_modules/pinia/dist/pinia.mjs')).href, external: true }))
    builder.onResolve({ filter: /^(api|@\/api|@\/store\/userStore|vue-router|element-plus)$/ }, args => ({ namespace: 'mock', path: ({ api:'api', '@/api':'api', '@/store/userStore':'user', 'vue-router':'router', 'element-plus':'element' })[args.path] }))
    builder.onResolve({ filter: /(?:readerPlugins|LibraryTreePanel\.vue|ReaderContextMenu\.vue)$/ }, args => ({ namespace: 'mock', path: args.path.includes('readerPlugins') ? 'plugins' : args.path.includes('LibraryTreePanel') ? 'library' : 'context' }))
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
  const app = createApp(Workspace)
  app.use(pinia)
  app.directive('context-menu', {})
  app.mount('#app')
  const workspace = useReaderWorkspace()
  await workspace.open({kind:'ebook',id:'a',title:'A'})
  await settle()
  const input = document.querySelector('[data-book="a"]')
  assert.ok(input)
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
  document.querySelector('[aria-label="关闭阅读工作区"]').click()
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
  await assert.rejects(workspace.open({kind:'ebook',id:'d'}))
  assert.ok(workspace.error)
  mock.fail=false
  await workspace.retry()
  assert.equal(workspace.state.active,'ebook:d')
  assert.equal(workspace.error,'')
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
