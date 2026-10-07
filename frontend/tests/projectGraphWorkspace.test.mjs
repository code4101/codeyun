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
const base = path.join(frontend, 'src/plugins/modules/project-graph')
const mocks = {
  share: `export default {render:()=>null};`,
  router: `import {reactive} from 'vue'; export const route=reactive({name:'ProjectGraph',params:{},query:{doc:'a'},hash:'#canvas',matched:[],meta:{supportsContentOnly:true}}); export const useRoute=()=>route; export const useRouter=()=>({replace:async value=>{route.query=value.query},resolve:target=>({href:'/'+target.name+'?doc='+target.query.doc+(target.query.ui?'&ui='+target.query.ui:'')+target.hash})}); export const onBeforeRouteLeave=()=>{};`,
  menu: `import {h} from 'vue'; export default {props:['items'],emits:['select'],setup(p,{emit}){
    const render=items=>items.map(item=>item.children?h('section',{'data-menu':item.id},render(item.children)):h('button',{'data-command':item.id,onClick:()=>emit('select',item.id)},item.label));
    return()=>h('header',{class:'workspace-menu'},render(p.items))}}`,
  storage: `export const docs=[{id:'a',title:'Alpha',folderId:'nested',bytes:new Uint8Array(),revision:1},{id:'b',title:'Beta',bytes:new Uint8Array(),revision:1}]; export const browserGraphStorage={}; export const listBrowserGraphDocuments=async()=>docs; export const listGraphFolders=async()=>[{id:'parent',title:'Parent',parentId:''},{id:'nested',title:'Nested',parentId:'parent'},{id:'empty',title:'Empty',parentId:''}]; export const changeGraphLibrary=async action=>{Object.assign(docs.find(doc=>doc.id===action.id),action)}; export const createGraphLibrary=()=>({ownerId:1,storage:browserGraphStorage,change:changeGraphLibrary,migrateBrowser:async()=>{},list:async()=>({documents:docs.map(doc=>({...doc})),folders:await listGraphFolders()})});`,
  editor: `import {h} from 'vue'; export const control={fail:false,flushes:0,downloads:0,command:null}; export default {props:['documentId','storage','title'],setup(p,{expose,emit}){control.command=value=>emit('command',value);control.persist=()=>p.storage.write(p.documentId,p.title,new Uint8Array([1]),0);expose({refreshMenu(){},executeMenu(){},focusAuxiliary(){},closeAuxiliary(){},flush:async()=>{control.flushes++; if(control.fail)throw new Error('save failed')},exportDocument(){control.downloads++}});return()=>h('div',{'data-editor':p.documentId},'canvas')}};`,
}
const compiled = await build({
 stdin:{contents:`export {default as Page} from './src/plugins/modules/project-graph/GraphWorkspace.vue'; export {control} from 'editor-mock'; export {route} from 'vue-router';`,resolveDir:frontend},bundle:true,write:false,format:'esm',platform:'node',
 plugins:[{name:'graph-test',setup(builder){
  builder.onResolve({filter:/\/WorkspaceMenu\.vue$/},()=>({namespace:'mock',path:'menu'}))
  builder.onResolve({filter:/^(vue|pinia)$/},args=>({path:pathToFileURL(path.join(frontend,args.path==='vue'?'node_modules/vue/index.mjs':'node_modules/pinia/dist/pinia.mjs')).href,external:true}))
  builder.onResolve({filter:/^(vue-router|editor-mock)$/},args=>({namespace:'mock',path:args.path==='vue-router'?'router':'editor'}))
  builder.onResolve({filter:/GraphShareDialog\.vue$/},()=>({namespace:'mock',path:'share'}))
  builder.onResolve({filter:/ProjectGraphEditor\.vue$/},()=>({namespace:'mock',path:'editor'}))
  builder.onResolve({filter:/^\.\/storage$/},()=>({namespace:'mock',path:'storage'}))
  builder.onResolve({filter:/^@\//},args=>({path:path.join(frontend,'src',args.path.slice(2))+(path.extname(args.path)?'':'.ts')}))
  builder.onResolve({filter:/\.css$/},args=>({path:args.path,namespace:'css'}))
  builder.onLoad({filter:/.*/,namespace:'css'},()=>({contents:''}))
  builder.onLoad({filter:/.*/,namespace:'mock'},args=>({contents:mocks[args.path],resolveDir:frontend,loader:'ts'}))
  builder.onLoad({filter:/\.vue$/},async({path:filename})=>{const {descriptor,errors}=parse(await readFile(filename,'utf8'),{filename});assert.deepEqual(errors,[]);return {contents:compileScript(descriptor,{id:filename,inlineTemplate:true}).content,loader:'ts'}})
 }}]
})
const {Page,control,route}=await import(`data:text/javascript;base64,${Buffer.from(compiled.outputFiles[0].text).toString('base64')}`)
const settle=async()=>{await nextTick();await new Promise(resolve=>setTimeout(resolve,0));await nextTick()}

test('PG restores the last active tab, including settings, while explicit links take priority', async () => {
 localStorage.clear(); control.fail=false
 localStorage.setItem('codeyun.project-graph.tabs:1', JSON.stringify(['a','view:journal','journal:2026-09-29','removed-daily-id','b']))
 route.query={}
 let app=createApp(Page); app.directive('context-menu',{}); app.mount('#app'); await settle(); await settle()
 assert.deepEqual(JSON.parse(localStorage.getItem('codeyun.project-graph.tabs:1')),['a','b'],'obsolete daily tabs are discarded')
 const beta=[...document.querySelectorAll('[role="tab"]')].find(el=>el.textContent.includes('Beta'))
 beta.click(); await settle(); await settle()
 assert.equal(JSON.parse(localStorage.getItem('codeyun.project-graph.selection:1')).document,'b')
 app.unmount(); route.query={}
 app=createApp(Page); app.directive('context-menu',{}); app.mount('#app'); await settle(); await settle()
 assert.ok(document.querySelector('[data-editor="b"]'))
 control.command('settings'); await settle(); app.unmount(); route.query={}
 app=createApp(Page); app.directive('context-menu',{}); app.mount('#app'); await settle(); await settle()
 assert.equal(document.querySelector('[role="tab"][aria-selected="true"]').textContent.trim(),'设置')
 app.unmount(); route.query={doc:'a'}
 app=createApp(Page); app.directive('context-menu',{}); app.mount('#app'); await settle(); await settle()
 assert.ok(document.querySelector('[data-editor="a"]'))
 assert.equal(document.querySelector('[role="tab"][aria-selected="true"]').textContent.trim(),'Alpha.prg')
 app.unmount(); localStorage.clear(); route.query={doc:'a'}
})
test('PG uses shared tools, compact resource tree and editor tabs with save protection',async()=>{
 const app=createApp(Page);app.directive('context-menu',{});app.mount('#app');await settle();await settle()
 assert.ok(document.querySelector('.dock-workspace'))
 assert.ok(document.querySelector('.graph-workspace > .workspace-menu + .dock-workspace'),'menu is above the whole dock, outside the editor tabs')
 assert.ok(document.querySelector('[data-editor="a"]'))
 const opened=[]
 window.open=(...args)=>opened.push(args)
 document.querySelector('[data-menu="window"] [data-command="open-standalone"]').click()
 assert.deepEqual(opened,[['/ProjectGraph?doc=a&ui=1#canvas','_blank','noopener,noreferrer']])
 const canvas = document.querySelector('[data-editor="a"]')
 route.query.ui='0'; await settle()
 assert.equal(document.querySelector('.workspace-menu').style.display,'none')
 assert.equal(document.querySelector('.dock-region').style.display,'none')
 assert.equal(document.querySelector('[data-editor="a"]'),canvas,'ui changes preserve the editor instance')
 route.query.ui='1'; await settle()
 assert.notEqual(document.querySelector('.workspace-menu').style.display,'none')
 assert.equal(document.querySelector('[data-editor="a"]'),canvas)
 const leftRegion=document.querySelector('[data-menu="window"] [data-command="layout:region:left"]')
 assert.ok(leftRegion.textContent.includes('✓'))
 leftRegion.click();await settle()
 assert.equal(leftRegion.textContent.includes('✓'),false)
 document.querySelector('[data-menu="window"] [data-command="layout:reset"]').click();await settle()
 assert.ok(leftRegion.textContent.includes('✓'))
 assert.ok(document.querySelector('main.reader-content [role="tablist"]'))
 assert.ok([...document.querySelectorAll('[role="treeitem"]')].some(el=>el.textContent.includes('Parent / Nested')))
 assert.equal(document.body.textContent.includes('此文件夹还没有'),false)
 const beta=[...document.querySelectorAll('[role="treeitem"]')].find(el=>el.textContent.includes('Beta.prg'))
 beta.click();await settle();await settle()
 assert.ok(document.querySelector('[data-editor="b"]'))
 assert.equal(document.querySelectorAll('[role="tab"]').length,2)
 assert.ok(control.flushes>0)
 control.command('new');await settle()
 assert.ok(document.querySelector('#graph-name'),'menu uses the host new-file dialog')
 ;[...document.querySelectorAll('.dialog-actions button')].find(el=>el.textContent==='取消').click();await settle()
control.command('settings');await settle()
assert.ok(document.querySelector('input[placeholder="搜索配置"]'))
assert.equal(document.querySelectorAll('[role="tab"]').length,3,'settings opens as a central tab')
document.querySelector('[aria-label="关闭 设置"]').click();await settle()
assert.equal(document.querySelectorAll('[role="tab"]').length,2)
const downloads=control.downloads
 control.command('download');await settle()
 assert.equal(control.downloads,downloads+1,'menu flushes before downloading')
 control.fail=true
 control.command('download');await settle()
 assert.equal(control.downloads,downloads+1,'failed save prevents menu export')
 document.querySelector('[aria-label="关闭 Beta.prg"]').click();await settle()
 assert.equal(document.querySelectorAll('[role="tab"]').length,2,'failed save keeps both tab and editor')
 assert.ok(document.querySelector('[data-editor="b"]'))
 control.fail=false
 document.querySelector('[aria-label="关闭 Beta.prg"]').click();await settle();await settle()
 assert.equal(document.querySelectorAll('[role="tab"]').length,1)
 assert.ok(document.querySelector('[data-editor="a"]'))
 assert.equal(document.querySelector('[aria-label="每日记录"]'),null)
 assert.equal(document.querySelector('.journal-navigation'),null)
 app.unmount()
})
