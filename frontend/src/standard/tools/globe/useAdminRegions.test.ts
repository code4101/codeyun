import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createRenderer } from 'vue'
import { useAdminRegions, type AdminWorker } from './useAdminRegions'
import { refineAdminRegions } from './adminRefinement'
import { normalizeRegions } from './adminRegions'
import type { FeatureCollection } from 'geojson'

function workerTransport(): AdminWorker {
  let current=0
  const worker: AdminWorker={onmessage:null,onerror:null,terminate(){current++},postMessage(message){
    current=message.id
    if (message.samples) void refineAdminRegions(message.samples,new Set(message.expanded),()=>current!==message.id,
      result=>worker.onmessage?.({data:{id:message.id,...result}} as MessageEvent))
  }}
  return worker
}
function mount(createWorker:()=>AdminWorker=workerTransport) {
  const renderer = createRenderer<object, object>({
    patchProp() {}, insert() {}, remove() {}, createElement: () => ({}), createText: () => ({}), createComment: () => ({}),
    setText() {}, setElementText() {}, parentNode: () => null, nextSibling: () => null,
  })
  let state!: ReturnType<typeof useAdminRegions>
  const app = renderer.createApp({ setup() { state = useAdminRegions(createWorker); return () => null } })
  app.mount({})
  return { state, unmount: () => app.unmount() }
}
const wait = () => new Promise(resolve => setTimeout(resolve, 650))
const viewport = {width:1000,height:600,unproject: (): [number,number] => [120.15,30.27]}
function dataset(code: number, parent: number, level: string) {
  return {type:'FeatureCollection',features:[{type:'Feature',properties:{adcode:code,parent:{adcode:parent},level,name:'测试区域',childrenNum:level==='city'?1:0,center:[120.15,30.27]},geometry:{type:'Polygon',coordinates:[[[119,29],[121,29],[121,31],[119,31],[119,29]]]}}]}
}

test('zoomed viewport refines China through provinces, cities and districts, then retracts on zoom out', async () => {
  const original = globalThis.fetch
  const requests: string[] = []
  globalThis.fetch = async url => {
    requests.push(String(url))
    return Response.json(String(url).includes('330000') ? dataset(330100,330000,'city') : dataset(330102,330100,'district'))
  }
  const {state,unmount} = mount()
  try {
    state.update(viewport,true)
    await wait()
    assert.ok(requests.some(url=>url.includes('330000_full')))
    assert.ok(requests.some(url=>url.includes('330100_full')))
    assert.ok(state.features.value.some(feature=>feature.properties.code==='330102'))
    state.update({...viewport,unproject:()=>null},true)
    await wait()
    assert.equal(state.features.value.length,0)
    assert.equal(state.error.value,'')
  } finally {unmount();globalThis.fetch=original}
})

test('turning refinement off cancels pending work and clears its overlays', async () => {
  const {state,unmount}=mount()
  try {
    state.update(viewport,true)
    state.update(viewport,false)
    await wait()
    assert.equal(state.features.value.length,0)
  } finally {unmount()}
})

test('dragging suspends work and discards late results without clearing existing boundaries', async () => {
  const messages: Parameters<AdminWorker['postMessage']>[0][]=[]
  const worker: AdminWorker={onmessage:null,onerror:null,postMessage(message){messages.push(message)},terminate(){}}
  const {state,unmount}=mount(()=>worker)
  try {
    state.update(viewport,true)
    await wait()
    const request=messages.find(m=>m.samples)!
    const boundaries=normalizeRegions(dataset(330102,330100,'district') as FeatureCollection)
    worker.onmessage?.({data:{id:request.id,features:boundaries,expanded:[],error:'',final:true}} as MessageEvent)
    await wait()
    assert.equal(state.features.value.length,1)
    const displayed=state.features.value
    worker.onmessage?.({data:{id:request.id,features:[...boundaries],expanded:[],error:'',final:true}} as MessageEvent)
    await wait()
    assert.equal(state.features.value,displayed)
    // A result already queued for idle must also be cancelled by a new gesture.
    worker.onmessage?.({data:{id:request.id,features:[],expanded:[],error:'queued',final:true}} as MessageEvent)
    state.setInteracting(true)
    await wait()
    worker.onmessage?.({data:{id:request.id,features:[],expanded:[],error:'stale',final:true}} as MessageEvent)
    assert.equal(state.error.value,'')
    assert.equal(state.features.value,displayed)
    state.update(viewport,true)
    await wait()
    assert.equal(messages.filter(m=>m.samples).length,1)
    state.setInteracting(false)
    await wait()
    assert.equal(messages.filter(m=>m.samples).length,2)
  } finally {unmount()}
})


test('busy browser defers optional work and a new gesture cancels queued idle callbacks', async () => {
  const originalRequest=globalThis.requestIdleCallback
  const originalCancel=globalThis.cancelIdleCallback
  const callbacks=new Map<number,IdleRequestCallback>()
  let next=0
  globalThis.requestIdleCallback=callback=>{ callbacks.set(++next,callback); return next }
  globalThis.cancelIdleCallback=id=>{ callbacks.delete(id) }
  const messages: Parameters<AdminWorker['postMessage']>[0][]=[]
  const worker: AdminWorker={onmessage:null,onerror:null,postMessage(message){messages.push(message)},terminate(){}}
  const {state,unmount}=mount(()=>worker)
  const flushIdle=()=>{
    const tasks=[...callbacks.values()]; callbacks.clear()
    tasks.forEach(task=>task({didTimeout:false,timeRemaining:()=>50}))
  }
  try {
    state.update(viewport,true)
    await wait()
    assert.equal(messages.length,0)
    assert.equal(callbacks.size,1)
    flushIdle()
    const request=messages.find(message=>message.samples)!
    assert.ok(request)
    worker.onmessage?.({data:{id:request.id,features:normalizeRegions(dataset(330102,330100,'district') as FeatureCollection),expanded:[],error:'',final:true}} as MessageEvent)
    assert.equal(state.features.value.length,0)
    assert.equal(callbacks.size,1)
    state.setInteracting(true)
    assert.equal(callbacks.size,0)
    flushIdle()
    assert.equal(state.features.value.length,0)
  } finally {
    unmount()
    if (originalRequest) globalThis.requestIdleCallback=originalRequest
    else Reflect.deleteProperty(globalThis,'requestIdleCallback')
    if (originalCancel) globalThis.cancelIdleCallback=originalCancel
    else Reflect.deleteProperty(globalThis,'cancelIdleCallback')
  }
})
