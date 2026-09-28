import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {transform} from 'esbuild'
const {code}=await transform(await readFile(new URL('../../integrations/project-graph/overlay/viewState.ts',import.meta.url),'utf8'),{loader:'ts',format:'esm'})
const {createViewStateStore}=await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`)
test('view preferences are validated, deduplicated and isolated by user and file',()=>{
 const data=new Map();let writes=0;const storage={getItem:key=>data.get(key),setItem(key,value){writes++;data.set(key,value)}}
 const a=createViewStateStore('1:10',storage),b=createViewStateStore('1:11',storage),other=createViewStateStore('2:10',storage)
 const view={scale:.5,x:100,y:-300};a.write(view);a.write(view);assert.equal(writes,1)
 assert.deepEqual(a.read(),view);assert.equal(b.read(),undefined);assert.equal(other.read(),undefined)
 for(const scale of [0,-1,NaN,Infinity])a.write({...view,scale})
 assert.deepEqual(a.read(),view)
 data.set('1:11','broken');assert.equal(b.read(),undefined)
 assert.doesNotThrow(()=>createViewStateStore('x',{getItem(){throw Error()},setItem(){throw Error()}}).write(view))
})
