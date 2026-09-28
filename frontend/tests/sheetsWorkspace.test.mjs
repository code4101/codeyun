import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import test from 'node:test'
import { build } from 'esbuild'
import { parse, compileScript } from '@vue/compiler-sfc'
import { JSDOM } from 'jsdom'

const dom = new JSDOM('<body><div id="app"></div></body>', { url: 'http://localhost/' })
for (const key of ['window', 'document', 'Element', 'HTMLElement', 'SVGElement', 'Node', 'localStorage', 'history', 'location']) globalThis[key] = dom.window[key]
globalThis.ResizeObserver = class { observe() {} disconnect() {} }
globalThis.requestAnimationFrame = () => 0
globalThis.cancelAnimationFrame = () => {}
dom.window.HTMLElement.prototype.scrollIntoView = function () {}
const { nextTick, createApp, h } = await import('vue')
const { createRouter, createMemoryHistory, RouterView } = await import('../node_modules/vue-router/dist/vue-router.mjs')
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const mocks = {
 api: `export const fetchWorkbooks=async()=>[{id:1,title:'Alpha',sheet_count:2,owner_user_id:1,updated_at:1,access:{role:'manager',capabilities:{can_manage_access:true}}},{id:2,title:'Beta',sheet_count:1,owner_user_id:2,updated_at:1,access:{role:'viewer',capabilities:{can_manage_access:false}}}];export const createWorkbook=async()=>{};export const deleteWorkbook=async()=>{};export const saveAsWorkbook=async()=>{};export const updateWorkbook=async()=>{};`,
 user: `export const useUserStore=()=>({user:{id:1},isAuthenticated:true});`,
 element: `import {h} from 'vue';const menu={setup(p,{slots}){return()=>h('div',slots.default?.())}};export const ElMenu=menu,ElSubMenu=menu,ElMenuItem=menu;export const ElMessage={error(){},warning(){}};export const ElMessageBox={};`,
 dialog: `export default {render(){return null}}`,
 resource: `import {h} from 'vue';import {useRoute,useRouter} from 'vue-router';export const control={fail:false,flushes:0};export default {setup(p,{expose}){const route=useRoute(),router=useRouter(); expose({flush:async()=>{control.flushes++;if(control.fail)throw new Error('save failed')}});return()=>h('div',{'data-editor':route.params.workbookId},[h('button',{'data-sheet':true,onClick:()=>router.replace({path:route.path,query:{sheet:'42'}})},'Sheet 42'),h('button',{'data-other':true,onClick:()=>router.push('/workbook/2')},'Other workbook')])}};`,
}
const compiled = await build({
 stdin:{contents:`export {default as Page} from './src/standard/notes/sheets-manager/page.vue';export {control} from 'resource-mock';`,resolveDir:frontend},bundle:true,write:false,format:'esm',platform:'node',
 plugins:[{name:'sheets-test',setup(builder){
  builder.onResolve({filter:/^(vue|vue-router)$/},args=>({path:pathToFileURL(path.join(frontend,args.path==='vue'?'node_modules/vue/index.mjs':'node_modules/vue-router/dist/vue-router.mjs')).href,external:true}))
  builder.onResolve({filter:/resource-mock|resource-view\/page.vue$/},()=>({namespace:'mock',path:'resource'}))
  builder.onResolve({filter:/^@\/api\/noteSheets$/},()=>({namespace:'mock',path:'api'}))
  builder.onResolve({filter:/^@\/store\/userStore$/},()=>({namespace:'mock',path:'user'}))
  builder.onResolve({filter:/^element-plus$/},()=>({namespace:'mock',path:'element'}))
  builder.onResolve({filter:/NoteSheetAccessDialog.vue$/},()=>({namespace:'mock',path:'dialog'}))
  builder.onResolve({filter:/^@\//},args=>({path:path.join(frontend,'src',args.path.slice(2))+(path.extname(args.path)?'':'.ts')}))
  builder.onResolve({filter:/\.css$/},args=>({path:args.path,namespace:'css'}))
  builder.onLoad({filter:/.*/,namespace:'css'},()=>({contents:''}))
  builder.onLoad({filter:/.*/,namespace:'mock'},args=>({contents:mocks[args.path],resolveDir:frontend,loader:'ts'}))
  builder.onLoad({filter:/\.vue$/},async({path:filename})=>{const {descriptor,errors}=parse(await readFile(filename,'utf8'),{filename});assert.deepEqual(errors,[]);return {contents:compileScript(descriptor,{id:filename,inlineTemplate:true}).content,loader:'ts'}})
 }}]
})
const {Page,control}=await import(`data:text/javascript;base64,${Buffer.from(compiled.outputFiles[0].text).toString('base64')}`)
const settle=async()=>{for(let i=0;i<5;i++){await nextTick();await new Promise(resolve=>setTimeout(resolve,0))}}
const button=text=>[...document.querySelectorAll('button')].find(el=>el.textContent.trim()===text)
const resource=text=>[...document.querySelectorAll('[role="treeitem"]')].find(el=>el.textContent.includes(text))
test('workbook tabs isolate sheet routing and retain the editor when saving fails',async()=>{
 const router=createRouter({history:createMemoryHistory(),routes:[{path:'/notes/sheets',component:Page},{path:'/workbook/:workbookId',component:{render:()=>null}},{path:'/other',component:{render:()=>null}}]})
 await router.push('/notes/sheets')
 const app=createApp({render:()=>h(RouterView)});app.use(router);app.directive('context-menu',{});app.directive('loading',{});app.config.warnHandler=(message)=>{if(!message.includes('Failed to resolve component')) console.log(message)};app.mount('#app');await settle()
 assert.ok(document.querySelector('.sheets-workspace > .workspace-menu + .dock-workspace'))
 assert.equal(document.querySelector('[aria-label="关闭 工作簿库"]'),null)
 resource('Alpha').click();await settle()
 assert.ok(document.querySelector('[data-editor="1"]'))
 assert.equal(router.currentRoute.value.path,'/notes/sheets')
 document.querySelector('[data-sheet]').click();await settle()
 assert.equal(router.currentRoute.value.query.sheet,'42')
 control.fail=true
 document.querySelector('[aria-label="关闭 Alpha"]').click();await settle()
 assert.ok(document.querySelector('[data-editor="1"]'),'failed save keeps editor mounted')
 assert.match(document.querySelector('[role="alert"]').textContent,/save failed/)
 resource('Beta').click();await settle()
 assert.ok(document.querySelector('[data-editor="1"]'),'failed save prevents resource switch')
 await router.push('/other');await settle()
 assert.equal(router.currentRoute.value.path,'/notes/sheets','failed save blocks leaving the workspace')
 control.fail=false
 document.querySelector('[data-other]').click();await settle()
 assert.ok(document.querySelector('[data-editor="2"]'),'resource navigation opens another workspace tab')
 assert.equal(document.querySelectorAll('[role="tab"]').length,3)
 button('Alpha').click();await settle()
 assert.equal(router.currentRoute.value.query.sheet,'42','each workbook remembers its sheet')
 document.querySelector('[aria-label="关闭 Alpha"]').click();await settle()
 assert.equal(document.querySelector('[data-editor]'),null)
 assert.equal(document.querySelectorAll('[role="tab"]').length,2)
 const search=document.querySelector('input[aria-label="搜索工作簿"]');search.value='Beta';search.dispatchEvent(new dom.window.Event('input',{bubbles:true}));await settle()
 assert.equal(document.querySelectorAll('[role="treeitem"]').length,1)
 button('我创建的').click();await settle()
 assert.equal(document.querySelectorAll('[role="treeitem"]').length,0)
 app.unmount()
})
