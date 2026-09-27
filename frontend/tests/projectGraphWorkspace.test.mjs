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
  router: `import {reactive} from 'vue'; export const route=reactive({query:{doc:'a'}}); export const useRoute=()=>route; export const useRouter=()=>({replace:async value=>{route.query=value.query}}); export const onBeforeRouteLeave=()=>{};`,
  storage: `export const docs=[{id:'a',title:'Alpha',folderId:'nested',bytes:new Uint8Array(),revision:1},{id:'b',title:'Beta',bytes:new Uint8Array(),revision:1}]; export const browserGraphStorage={}; export const listBrowserGraphDocuments=async()=>docs; export const listGraphFolders=async()=>[{id:'parent',title:'Parent',parentId:''},{id:'nested',title:'Nested',parentId:'parent'},{id:'empty',title:'Empty',parentId:''}]; export const changeGraphLibrary=async()=>{};`,
  editor: `import {h} from 'vue'; export const control={fail:false,flushes:0}; export default {props:['documentId'],setup(p,{expose}){expose({flush:async()=>{control.flushes++; if(control.fail)throw new Error('save failed')},exportDocument(){}});return()=>h('div',{'data-editor':p.documentId},'canvas')}};`,
}
const compiled = await build({
 stdin:{contents:`export {default as Page} from './src/plugins/modules/project-graph/page.vue'; export {control} from 'editor-mock';`,resolveDir:frontend},bundle:true,write:false,format:'esm',platform:'node',
 plugins:[{name:'graph-test',setup(builder){
  builder.onResolve({filter:/^(vue|pinia)$/},args=>({path:pathToFileURL(path.join(frontend,args.path==='vue'?'node_modules/vue/index.mjs':'node_modules/pinia/dist/pinia.mjs')).href,external:true}))
  builder.onResolve({filter:/^(vue-router|editor-mock)$/},args=>({namespace:'mock',path:args.path==='vue-router'?'router':'editor'}))
  builder.onResolve({filter:/ProjectGraphEditor\.vue$/},()=>({namespace:'mock',path:'editor'}))
  builder.onResolve({filter:/^\.\/storage$/},()=>({namespace:'mock',path:'storage'}))
  builder.onResolve({filter:/^@\//},args=>({path:path.join(frontend,'src',args.path.slice(2))+(path.extname(args.path)?'':'.ts')}))
  builder.onResolve({filter:/\.css$/},args=>({path:args.path,namespace:'css'}))
  builder.onLoad({filter:/.*/,namespace:'css'},()=>({contents:''}))
  builder.onLoad({filter:/.*/,namespace:'mock'},args=>({contents:mocks[args.path],resolveDir:frontend,loader:'ts'}))
  builder.onLoad({filter:/\.vue$/},async({path:filename})=>{const {descriptor,errors}=parse(await readFile(filename,'utf8'),{filename});assert.deepEqual(errors,[]);return {contents:compileScript(descriptor,{id:filename,inlineTemplate:true}).content,loader:'ts'}})
 }}]
})
const {Page,control}=await import(`data:text/javascript;base64,${Buffer.from(compiled.outputFiles[0].text).toString('base64')}`)
const settle=async()=>{await nextTick();await new Promise(resolve=>setTimeout(resolve,0));await nextTick()}
test('PG uses shared tools, compact resource tree and editor tabs with save protection',async()=>{
 const app=createApp(Page);app.directive('context-menu',{});app.mount('#app');await settle();await settle()
 assert.ok(document.querySelector('.dock-workspace'))
 assert.ok(document.querySelector('[data-editor="a"]'))
 assert.ok(document.querySelector('main.reader-content [role="tablist"]'))
 assert.ok([...document.querySelectorAll('[role="treeitem"]')].some(el=>el.textContent.includes('Parent / Nested')))
 assert.equal(document.body.textContent.includes('此文件夹还没有'),false)
 const beta=[...document.querySelectorAll('[role="treeitem"]')].find(el=>el.textContent.includes('Beta.prg'))
 beta.click();await settle();await settle()
 assert.ok(document.querySelector('[data-editor="b"]'))
 assert.equal(document.querySelectorAll('[role="tab"]').length,2)
 assert.ok(control.flushes>0)
 control.fail=true
 document.querySelector('[aria-label="关闭 Beta.prg"]').click();await settle()
 assert.equal(document.querySelectorAll('[role="tab"]').length,2,'failed save keeps both tab and editor')
 assert.ok(document.querySelector('[data-editor="b"]'))
 control.fail=false
 document.querySelector('[aria-label="关闭 Beta.prg"]').click();await settle();await settle()
 assert.equal(document.querySelectorAll('[role="tab"]').length,1)
 assert.ok(document.querySelector('[data-editor="a"]'))
 app.unmount()
})
