import assert from 'node:assert/strict'
import test from 'node:test'
import { build } from 'esbuild'
import { fileURLToPath } from 'node:url'
const source = fileURLToPath(new URL('../../integrations/project-graph/overlay/selectionDetails.tsx', import.meta.url))
const shared = fileURLToPath(new URL('../src/components/rich-text/plateValue.ts', import.meta.url))
const compiled = await build({entryPoints:[source],bundle:true,write:false,format:'esm',plugins:[{name:'contracts',setup(b){
 b.onResolve({filter:/^@\//},args=>({path:args.path,namespace:'stub'}))
 b.onResolve({filter:/^\.\/plateValue$/},()=>({path:shared}))
 b.onLoad({filter:/.*/,namespace:'stub'},()=>({contents:'export class Project {}; export class Entity {}; export class ControllerUtils {}; export const ProjectState={Unsaved:"unsaved"};'}))
}}]})
const {updateNodeDetails}=await import(`data:text/javascript;base64,${Buffer.from(compiled.outputFiles[0].text).toString('base64')}`)
const paragraph=text=>[{type:'p',children:[{text}]}]
test('placeholder edits do not create a body; real content persists and clearing removes it',()=>{
 const entity={uuid:'a',details:[]};let steps=0,syncs=0
 const project={stageManager:{getEntities:()=>[entity]},syncAssociationManager:{syncFrom(){syncs++}},historyManager:{recordStep(){steps++}}}
 for(const value of [[],paragraph(''),paragraph(' \n '),[{type:'h1',children:[{text:'',bold:true}]}]]) updateNodeDetails(project,'a',value)
 assert.deepEqual(entity.details,[]);assert.equal(steps,0);assert.equal(project.projectState,undefined)
 updateNodeDetails(project,'a',paragraph('正文'))
 assert.deepEqual(entity.details,paragraph('正文'));assert.equal(steps,1);assert.equal(project.projectState,'unsaved')
 updateNodeDetails(project,'a',paragraph('正文'));assert.equal(steps,1,'identical content does not save again')
 updateNodeDetails(project,'missing',paragraph('wrong object'));assert.equal(steps,1)
 updateNodeDetails(project,'a',paragraph(''));assert.deepEqual(entity.details,[]);assert.equal(steps,2)
 updateNodeDetails(project,'a',paragraph('  '));assert.equal(steps,2)
 for(const content of [[{type:'img',url:'data:image/png;base64,abc',children:[{text:''}]}],[{type:'equation',texExpression:'x+1',children:[{text:''}]}]]){
  updateNodeDetails(project,'a',content);assert.deepEqual(entity.details,content)
 }
 assert.equal(syncs,steps)
})
