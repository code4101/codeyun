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
  storage: `export const docs=[{id:'a',title:'Alpha',folderId:'nested',bytes:new Uint8Array(),revision:1},{id:'b',title:'Beta',bytes:new Uint8Array(),revision:1}]; export const browserGraphStorage={}; export const listBrowserGraphDocuments=async()=>docs; export const listGraphFolders=async()=>[{id:'parent',title:'Parent',parentId:''},{id:'nested',title:'Nested',parentId:'parent'},{id:'empty',title:'Empty',parentId:''}]; export const changeGraphLibrary=async action=>{Object.assign(docs.find(doc=>doc.id===action.id),action)}; export const createGraphLibrary=()=>({ownerId:1,storage:browserGraphStorage,change:changeGraphLibrary,migrateBrowser:async()=>{},list:async()=>({documents:docs.map(doc=>({...doc})),folders:await listGraphFolders()}),openJournal:async day=>docs.find(doc=>doc.journalDate===day),saveJournal:async(day,bytes)=>{let doc=docs.find(doc=>doc.journalDate===day);if(!doc){doc={id:'day-'+day,title:day,journalDate:day,revision:0,bytes:new Uint8Array()};docs.push(doc)}return doc}});`,
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
 const today=new Date().toLocaleDateString('sv-SE')
 if(document.querySelector('.dock-rail [aria-label="每日记录"]').getAttribute('aria-pressed')!=='true') { document.querySelector('.dock-rail [aria-label="每日记录"]').click();await settle() }
 document.querySelector('.journal-heading button').click();await settle();await settle()
 assert.ok(document.querySelector(`[data-editor="journal:${today}"]`))
 assert.equal(document.querySelector('.journal-month button[aria-pressed="true"]').title.startsWith(today),true)
 const count=document.querySelectorAll('[role="tab"]').length
 document.querySelector('.journal-heading button').click();await settle();await settle()
 assert.equal(document.querySelectorAll('[role="tab"]').length,count,'today reopens the same daily canvas')
 const picker=document.querySelector('[aria-label="选择月份"]')
 picker.value='2026-01';picker.dispatchEvent(new dom.window.Event('change',{bubbles:true}));await settle()
 document.querySelector('[data-day="2026-01-01"]').click();await settle();await settle()
 assert.ok(document.querySelector('[data-editor="journal:2026-01-01"]'))
 control.fail=true
 document.querySelector('[data-day="2025-12-31"]').click();await settle()
 assert.ok(document.querySelector('[data-editor="journal:2026-01-01"]'),'failed save prevents switching or creating another day')
 control.fail=false
 document.querySelector('[data-day="2025-12-31"]').click();await settle();await settle()
 assert.ok(document.querySelector('[data-editor="journal:2025-12-31"]'),'calendar crosses year boundary')
 await control.persist();await settle();await settle()
 assert.equal(document.querySelectorAll('[role="tab"]').length,count,'saving and switching dates share one journal tab')
 assert.equal([...document.querySelectorAll('[role="treeitem"]')].some(el=>el.textContent.includes('2025-12-31')),false,'journal files stay out of the ordinary resource tree')
 const journalTab=()=>[...document.querySelectorAll('[role="tab"]')].find(el=>el.textContent.includes('每日记录'))
 assert.equal(journalTab().getAttribute('aria-selected'),'true')
 ;[...document.querySelectorAll('[role="tab"]')].find(el=>el.textContent.includes('Alpha.prg')).click();await settle();await settle()
 assert.ok(document.querySelector('[data-editor="a"]'))
 journalTab().click();await settle();await settle()
 assert.ok(document.querySelector('[data-editor="day-2025-12-31"]'),'journal tab returns to the last selected day')
 control.fail=true
 document.querySelector('[aria-label="关闭 每日记录"]').click();await settle()
 assert.ok(journalTab(),'failed save keeps the aggregate tab')
 control.fail=false
 document.querySelector('[aria-label="关闭 每日记录"]').click();await settle();await settle()
 assert.equal(journalTab(),undefined)
 assert.ok(document.querySelector('[data-editor="a"]'))
 if(document.querySelector('.dock-rail [aria-label="每日记录"]').getAttribute('aria-pressed')!=='true') { document.querySelector('.dock-rail [aria-label="每日记录"]').click();await settle() }
 document.querySelector('.journal-heading button').click();await settle();await settle()
 const returnDate=document.querySelector('[aria-label="选择月份"]')
 returnDate.value='2025-12';returnDate.dispatchEvent(new dom.window.Event('change',{bubbles:true}));await settle()
 document.querySelector('[data-day="2025-12-31"]').click();await settle();await settle()
 assert.equal(document.querySelector('.journal-toolbar'),null,'the calendar is the only date navigation')
 assert.ok(document.querySelector('[data-editor="day-2025-12-31"]'))
 const toggleDate=day=>document.querySelector(`[data-day="${day}"]`).dispatchEvent(new dom.window.MouseEvent('click',{bubbles:true,ctrlKey:true}))
 toggleDate('2025-12-30');await settle();await settle()
 assert.equal(document.querySelectorAll('.day-pane').length,2)
 journalTab().click();await settle()
 assert.equal(document.querySelectorAll('.day-pane').length,2,'activating the current tab preserves the selection')
 control.fail=true
 toggleDate('2025-12-30');await settle();await settle()
 assert.equal(document.querySelectorAll('.day-pane').length,2,'failed save prevents dismissing a selected canvas')
 control.fail=false
 toggleDate('2025-12-30');await settle();await settle()
 assert.equal(document.querySelectorAll('.day-pane').length,1)
 app.unmount()
})
