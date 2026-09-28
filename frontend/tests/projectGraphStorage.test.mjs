import assert from 'node:assert/strict'
import test from 'node:test'
import { build } from 'esbuild'

const user = { user: { id: 1, username: 'one' }, token: '' }
const token = (sub, scope = 'user-session') => `header.${Buffer.from(JSON.stringify({sub, scope})).toString('base64url')}.sig`
user.token = token('1')
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
  user.user.username='renamed'
  await library.storage.write('108500','test',bytes,2)
  assert.equal(calls,2, 'username changes must preserve library access')
  for (const invalid of [token('one'), token('1', 'device'), token(1), 'invalid', '']) {
    user.token=invalid
    await assert.rejects(library.create('bad'),/用户已切换/)
  }
  user.token=token('2') // login installs token before loading the new profile
  await assert.rejects(library.create('bad'),/用户已切换/)
  assert.equal(calls,2)
  user.token=token('1')
  let finish
  globalThis.fetch=()=>new Promise(resolve=>{finish=resolve})
  const pending=library.list()
  user.user={id:2,username:'two'};user.token=token('2')
  finish({ok:true,status:200,json:async()=>({entries:[]})})
  await assert.rejects(pending,/用户已切换/)
})
