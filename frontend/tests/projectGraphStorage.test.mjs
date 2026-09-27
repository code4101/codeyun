import assert from 'node:assert/strict'
import test from 'node:test'
import { build } from 'esbuild'

const user = { user: { id: 1, username: 'one' }, token: '' }
const token = sub => `header.${Buffer.from(JSON.stringify({sub})).toString('base64url')}.sig`
user.token = token('one')
globalThis.graphTestUser = user
const compiled = await build({ entryPoints:['frontend/src/plugins/modules/project-graph/storage.ts'],
  bundle:true,write:false,format:'esm',platform:'node', plugins:[{name:'user',setup(b){
    b.onResolve({filter:/^@\/store\/userStore$/},()=>({path:'user',namespace:'mock'}))
    b.onLoad({filter:/.*/,namespace:'mock'},()=>({contents:'export const useUserStore=()=>globalThis.graphTestUser'}))
  }}] })
const {createGraphLibrary} = await import(`data:text/javascript;base64,${Buffer.from(compiled.outputFiles[0].text).toString('base64')}`)

test('server adapter preserves numeric IDs and isolates pending requests across account switches', async()=>{
  const library = createGraphLibrary()
  let calls=0, lastBody
  globalThis.fetch=async(url,options)=>{
    calls++;lastBody=JSON.parse(options.body??'null')
    return {ok:true,status:200,json:async()=>({id:108500,title:'test',revision:2,parentId:0,updatedAt:1})}
  }
  const bytes=new Uint8Array([1,2,255])
  const result=await library.storage.write('108500','test',bytes,1)
  assert.equal(result.id,'108500')
  assert.deepEqual(result.bytes,bytes)
  assert.equal(lastBody.expectedRevision,1)
  assert.equal(lastBody.content,'AQL/')
  user.token=token('two') // login installs token before loading the new profile
  await assert.rejects(library.create('bad'),/用户已切换/)
  assert.equal(calls,1)
  user.token=token('one')
  let finish
  globalThis.fetch=()=>new Promise(resolve=>{finish=resolve})
  const pending=library.list()
  user.user={id:2,username:'two'};user.token=token('two')
  finish({ok:true,status:200,json:async()=>({entries:[]})})
  await assert.rejects(pending,/用户已切换/)
})
