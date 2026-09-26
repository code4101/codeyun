import { onBeforeUnmount, ref, shallowRef } from 'vue'
import { viewportSamples, type AdminFeature, type AdminViewport } from './adminRegions'
import type { RefinementResult } from './adminRefinement'

export interface AdminWorker {
  onmessage: ((event: MessageEvent<RefinementResult & {id:number}>) => void) | null
  onerror: ((event: Event) => void) | null
  postMessage(message: {id:number; samples?:Array<[number,number] | null>; expanded?:string[]}): void
  terminate(): void
}

/** Only view sampling runs on the main thread. Geometry preparation and queries run in a worker. */
export function useAdminRegions(createWorker: () => AdminWorker = () => new Worker(new URL('./adminRegions.worker.ts', import.meta.url), {type:'module'}) as unknown as AdminWorker) {
  const features = shallowRef<AdminFeature[]>([])
  const error = ref('')
  let worker: AdminWorker | undefined
  let expanded: string[] = []
  let revision = 0, signature = ''
  let interacting = false
  let lastView: AdminViewport | null = null, enabled = true
  let timer: ReturnType<typeof setTimeout> | undefined
  let cancelIdle: (() => void) | undefined
  let pending: (RefinementResult & {id:number}) | undefined
  // Optional overlays never force an update while the browser is busy handling input.
  function whenIdle(task: () => void) {
    cancelIdle?.()
    const run = () => { cancelIdle=undefined; task() }
    if (typeof requestIdleCallback === 'function') {
      const handle=requestIdleCallback(run)
      cancelIdle=()=>cancelIdleCallback(handle)
    } else {
      const handle=setTimeout(run,32)
      cancelIdle=()=>clearTimeout(handle)
    }
  }
  function cancel() {
    clearTimeout(timer); cancelIdle?.(); cancelIdle=undefined; pending=undefined
    revision++; worker?.postMessage({id:revision})
  }
  function update(view: AdminViewport | null, active: boolean) {
    lastView=view; enabled=active
    cancel()
    if (!active) { features.value=[]; expanded=[]; signature=''; error.value=''; return }
    if (interacting || !view || !view.width || !view.height) return
    const id=revision
    timer=setTimeout(() => whenIdle(() => {
      if (id!==revision || interacting || !enabled) return
      try {
        if (!worker) {
          worker=createWorker()
          worker.onmessage=({data}) => {
            if (data.id!==revision || interacting || !enabled) return
            pending=data
            whenIdle(() => {
              const result=pending; pending=undefined
              if (!result || result.id!==revision || interacting || !enabled) return
              expanded=result.expanded; error.value=result.error
              // Accept the coarse snapshot too: old branch details must disappear
              // before the newly focused branch finishes loading.
              const key=result.features.map(f=>f.properties.code).join(',')
              // Coalesce worker progress and keep identical SVG paths / MapLibre sources intact.
              if (key!==signature) { signature=key; features.value=result.features }
            })
          }
          worker.onerror=() => { error.value='行政区后台加载失败'; worker?.terminate(); worker=undefined }
        }
        worker.postMessage({id,samples:viewportSamples(view),expanded})
      } catch { error.value='行政区后台加载失败' }
    }),300)
  }
  function setInteracting(value:boolean) {
    if (interacting===value) return
    interacting=value
    if (value) cancel()
    else update(lastView,enabled)
  }
  onBeforeUnmount(()=>{cancel();worker?.terminate();worker=undefined})
  return {features,error,update,setInteracting}
}
