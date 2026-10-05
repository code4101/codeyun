<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { Monitor, Cellphone, FolderOpened, ChromeFilled, ChatDotRound, Document, Cpu } from '@element-plus/icons-vue'
import api from '@/api'
import DockWorkspace from '@/components/docking/DockWorkspace.vue'
import { useDockLayout } from '@/components/docking/useDockLayout'
import WorkspaceMenu from '@/components/editor-workspace/WorkspaceMenu.vue'
import EditorTabs from '@/components/editor-workspace/EditorTabs.vue'
import { dockWindowMenuItems, executeDockWindowCommand } from '@/components/editor-workspace/workspaceMenu'
import TextTransferTool from './TextTransferTool.vue'
import '@/standard/pdf/library/readerTheme.css'
const dock = useDockLayout('codeyun.desktop-windows.dock.v1', [
  { id:'applications', title:'本机应用窗口', icon:'library', position:'left', open:true },
  { id:'text-transfer', title:'文本传递', icon:'document', position:'bottom', open:true },
])
const menus = computed(()=>[{id:'window',label:'窗口',children:dockWindowMenuItems(dock)}])
interface NativeWindow { id:string; title:string; application:string; width:number; height:number; read_only?:boolean; icon?:string }
const failedIcons = ref(new Set<string>())
const windows = ref<NativeWindow[]>([]), selected = ref(''), search = ref(''), listing = ref(false)
const openWindows = ref<NativeWindow[]>([])
const viewRevision = ref(0), pendingActions = ref(new Set<string>()), pendingViews = ref(new Set<number>())
let activationTail: Promise<unknown> = Promise.resolve()
const frameTasks = new Map<number, Promise<boolean>>()
const frameControllers = new Map<number, AbortController>()
const drafts = ref<Record<string,string>>({})
const visibleWindows = computed(()=>windows.value.filter(w=>`${w.application} ${w.title}`.toLowerCase().includes(search.value.toLowerCase())))
const current = computed(()=>windows.value.find(w=>w.id===selected.value) || openWindows.value.find(w=>w.id===selected.value))
function applicationIcon(window: NativeWindow) {
  const app = window.application.toLowerCase()
  if(window.read_only || /mumu|nemu/.test(app)) return Cellphone
  if(/chrome|msedge|firefox|browser/.test(app)) return ChromeFilled
  if(/explorer/.test(app)) return FolderOpened
  if(/codex|chatgpt/.test(app)) return ChatDotRound
  if(/code\.exe|terminal|powershell|cmd\.exe|python/.test(app)) return Cpu
  if(/word|excel|notepad|wps|acrobat/.test(app)) return Document
  return Monitor
}
async function loadWindows() {
  listing.value=true
  try {
    const {data} = await api.get<{windows:NativeWindow[]}>('/desktop-windows')
    windows.value=data.windows
    failedIcons.value=new Set()
    openWindows.value=openWindows.value.map(tab=>data.windows.find(window=>window.id===tab.id) || tab)
    if(!selected.value) {
      const preferred=data.windows.find(w=>/^(codex|chatgpt)\.exe$/i.test(w.application))
      if(preferred) await selectWindow(preferred.id)
    }
  } catch(e) { error.value=failure(e) } finally { listing.value=false }
}
async function selectWindow(id:string) {
  const window=windows.value.find(window=>window.id===id) || openWindows.value.find(window=>window.id===id)
  if(!window) return
  if(!openWindows.value.some(tab=>tab.id===id)) openWindows.value.push(window)
  clearGestures()
  const revision=++viewRevision.value
  for(const controller of frameControllers.values()) controller.abort()
  pendingViews.value.add(revision)
  selected.value=id; token.value=''; inputToken.value=''; windowId.value=''; anchor.value=undefined; picking.value=false; observing.value=false
  error.value=''; status.value='正在读取应用画面…'
  clearTimeout(later)
  canvas.value?.getContext('2d')?.clearRect(0,0,canvas.value.width,canvas.value.height)
  try {
    // Activation is never replayed after a lost response; only the frame GET retries.
    let notice=''
    try {
      // Native foreground changes stay ordered; superseded selections are skipped.
      const activation=activationTail.then(async()=>{
        if(disposed || revision!==viewRevision.value) return
        return api.post<{notice?:string}>('/desktop-windows/activate',{window_id:id},{timeout:15000})
      })
      activationTail=activation.catch(()=>undefined)
      notice=(await activation)?.data.notice || ''
    } catch(e) { if(!temporaryConnectionFailure(e)) throw e }
    if(disposed || revision!==viewRevision.value) return
    await refresh()
    if(notice && revision===viewRevision.value) error.value=notice
  } catch(e) { if(revision===viewRevision.value) error.value=failure(e) }
  finally { pendingViews.value.delete(revision) }
}
async function closeWindowTab(id:string) {
  const index=openWindows.value.findIndex(tab=>tab.id===id)
  if(index<0) return
  openWindows.value.splice(index,1)
  delete drafts.value[id]
  if(selected.value!==id) return
  const next=openWindows.value[Math.min(index,openWindows.value.length-1)]
  if(next) { await selectWindow(next.id); return }
  clearGestures(); clearTimeout(later)
  viewRevision.value++
  for(const controller of frameControllers.values()) controller.abort()
  selected.value=''; token.value=''; inputToken.value=''; windowId.value=''
  anchor.value=undefined; picking.value=false; observing.value=false; error.value=''; status.value='等待画面'
  canvas.value?.getContext('2d')?.clearRect(0,0,canvas.value.width,canvas.value.height)
}
function moveWindowTab(id:string, before:string) {
  if(id===before) return
  const index=openWindows.value.findIndex(tab=>tab.id===id)
  if(index<0 || !openWindows.value.some(tab=>tab.id===before)) return
  const [tab]=openWindows.value.splice(index,1)
  openWindows.value.splice(openWindows.value.findIndex(tab=>tab.id===before),0,tab!)
}
const canvas = ref<HTMLCanvasElement>(), token = ref(''), inputToken = ref(''), windowId = ref(''), error = ref(''), status = ref('等待画面')
const busy = computed(()=>pendingActions.value.has(selected.value) || pendingViews.value.has(viewRevision.value))
const observing = ref(false), picking = ref(false)
const text = computed({get:()=>drafts.value[selected.value] || '',set:value=>{drafts.value[selected.value]=value}})
const anchor = ref<{ x: number; y: number; width: number; height: number }>(), totalBytes = ref(0)
let captureMode: 'window'|'screen'|'adb' = 'window'
let disposed = false, timer: ReturnType<typeof setTimeout> | undefined
let later: ReturnType<typeof setTimeout> | undefined
interface Frame { token: string; input_token: string; window_id: string; capture_mode: 'window' | 'screen' | 'adb'; width: number; height: number; image_bytes: number; patches: {x:number; y:number; image:string}[] }
function temporaryConnectionFailure(e: unknown) {
  const error=e as {response?:{status?:number;data?:{detail?:string}}; code?:string}
  // Backend capture errors also use 503, but their detail requires user action.
  if(error.response?.data?.detail) return false
  return [502,503,504].includes(error.response?.status || 0) || ['ERR_NETWORK','ECONNABORTED','ETIMEDOUT'].includes(error.code || '')
}
function failure(e: unknown) {
  if(temporaryConnectionFailure(e)) return '画面连接暂时中断，服务可能正在更新。可点击刷新重试，低频观察会自动继续。'
  return (e as {response?:{data?:{detail?:string}}}).response?.data?.detail || String(e)
}
async function readFrame(requestedWindow:string, revision:number, previousToken:string, signal:AbortSignal) {
  for(let attempt=0; ; attempt++) {
    try {
      return await api.get<Frame>('/desktop-windows/frame', {params:{window_id:requestedWindow,token:previousToken || undefined},timeout:15000,signal})
    } catch(e) {
      if(!temporaryConnectionFailure(e) || attempt>=2 || disposed || revision!==viewRevision.value) throw e
      status.value='正在重新连接画面…'
      await new Promise(resolve=>setTimeout(resolve,750*(attempt+1)))
      if(disposed || revision!==viewRevision.value) throw e
    }
  }
}
async function refresh(): Promise<boolean> {
  if (disposed || !selected.value) return false
  const revision=viewRevision.value
  const pending=frameTasks.get(revision)
  if(pending) return pending
  const controller=new AbortController()
  frameControllers.set(revision,controller)
  const task=capture(selected.value,revision,token.value,controller.signal)
  frameTasks.set(revision,task)
  try { return await task }
  finally { frameTasks.delete(revision); frameControllers.delete(revision) }
}
async function capture(requestedWindow:string, revision:number, previousToken:string, signal:AbortSignal): Promise<boolean> {
  try {
    const {data} = await readFrame(requestedWindow,revision,previousToken,signal)
    if (disposed || revision!==viewRevision.value || !canvas.value) return false
    const target = canvas.value
    const resized = target.width !== data.width || target.height !== data.height
    if (resized) { target.width=data.width; target.height=data.height }
    if(resized || windowId.value !== data.window_id) anchor.value=undefined
    for (const patch of data.patches) {
      const image = new Image(); image.src = patch.image; await image.decode()
      if (disposed || revision!==viewRevision.value) return false
      target.getContext('2d')?.drawImage(image,patch.x,patch.y)
    }
    captureMode=data.capture_mode
    token.value=data.token; inputToken.value=data.input_token; windowId.value=data.window_id; totalBytes.value+=data.image_bytes
    status.value=`${data.capture_mode==='adb'?'模拟器独立画面 · 只读':data.capture_mode==='screen'?'屏幕兼容采集':'窗口采集'} · ${data.patches.length ? '画面已更新' : '画面无变化'} · ${new Date().toLocaleTimeString()} · 累计图像 ${(totalBytes.value/1024).toFixed(0)} KB`
    error.value=''; return true
  } catch(e) { if(!disposed && revision===viewRevision.value) { error.value=failure(e); if(!temporaryConnectionFailure(e)) observing.value=false }; return false }
}
function schedule() { clearTimeout(timer); timer=setTimeout(async()=>{ if(disposed)return; if(observing.value && !document.hidden && !busy.value) await refresh(); schedule() },2500) }
function feedback(revision:number) {
  if(disposed || revision!==viewRevision.value) return
  clearTimeout(later)
  later=setTimeout(async()=>{
    if(disposed || revision!==viewRevision.value) return
    await refresh()
    if(!disposed && revision===viewRevision.value) later=setTimeout(()=>{ if(revision===viewRevision.value) void refresh() },2500)
  },400)
}
function position(event: MouseEvent) { const rect=canvas.value!.getBoundingClientRect(); return {x:Math.max(0,Math.min(1,(event.clientX-rect.left)/rect.width)),y:Math.max(0,Math.min(1,(event.clientY-rect.top)/rect.height))} }
async function operate(payload: object) {
  if (busy.value || current.value?.read_only) return false
  const requestedWindow=selected.value, revision=viewRevision.value
  pendingActions.value.add(requestedWindow); error.value=''
  try {
    const previousWindow=windowId.value, previousWidth=canvas.value?.width, previousHeight=canvas.value?.height
    if(captureMode==='screen') await api.post('/desktop-windows/activate',{window_id:requestedWindow},{timeout:15000})
    if(revision!==viewRevision.value) return false
    if(!await refresh()) return false
    if(revision!==viewRevision.value) return false
    if(previousWindow !== windowId.value || previousWidth !== canvas.value?.width || previousHeight !== canvas.value?.height) { error.value='窗口已变化，画面已刷新，请重新选择操作位置'; return false }
    await api.post('/desktop-windows/input',{window_id:requestedWindow,token:inputToken.value,...payload},{timeout:15000})
    feedback(revision); return true
  }
  catch(e) { if(revision===viewRevision.value) { error.value=failure(e); observing.value=false }; return false }
  finally { pendingActions.value.delete(requestedWindow) }
}
type Point = {x:number;y:number}
type Button = 'left' | 'right' | 'middle'
let gesture: {id:number; button:Button; start:Point; points:Point[]; started:number; moved:boolean} | undefined
let pendingClick: {point:Point; button:Button; started:number; timer:ReturnType<typeof setTimeout>} | undefined
let wheelTimer: ReturnType<typeof setTimeout> | undefined
let queue: {window:string; payload:object}[] = []
let queueTimer: ReturnType<typeof setTimeout> | undefined
const drainingWindows = new Set<string>()
function enqueue(payload:object) {
  queue.push({window:selected.value,payload}); void drainQueue()
}
async function drainQueue() {
  const requestedWindow=selected.value
  if(drainingWindows.has(requestedWindow) || disposed) return
  if(busy.value) { clearTimeout(queueTimer); queueTimer=setTimeout(()=>void drainQueue(),100); return }
  drainingWindows.add(requestedWindow)
  try {
    while(queue.length && !disposed && requestedWindow===selected.value) {
      const operation=queue.shift()!
      if(operation.window!==selected.value) continue
      if(!await operate(operation.payload)) { queue=queue.filter(item=>item.window!==requestedWindow); break }
    }
  } finally { drainingWindows.delete(requestedWindow); if(queue.length && !disposed) void drainQueue() }
}
let wheelPending: {point:Point; delta:number; axis:'vertical'|'horizontal'} | undefined
function clearGestures() {
  if(pendingClick) clearTimeout(pendingClick.timer)
  pendingClick=undefined; gesture=undefined; wheelPending=undefined; queue=[]; clearTimeout(queueTimer); clearTimeout(wheelTimer)
}
function pointerDown(event:PointerEvent) {
  if(busy.value || !inputToken.value || event.button>2) return
  event.preventDefault()
  const button:Button=event.button===2?'right':event.button===1?'middle':'left'
  const point=position(event)
  if(picking.value) {
    if(button==='left') { anchor.value={...point,width:canvas.value!.width,height:canvas.value!.height}; picking.value=false }
    return
  }
  anchor.value=undefined
  gesture={id:event.pointerId,button,start:point,points:[point],started:performance.now(),moved:false}
  canvas.value!.setPointerCapture(event.pointerId)
}
function pointerMove(event:PointerEvent) {
  if(!gesture || gesture.id!==event.pointerId) return
  const point=position(event), rect=canvas.value!.getBoundingClientRect()
  if(Math.hypot((point.x-gesture.start.x)*rect.width,(point.y-gesture.start.y)*rect.height)>=4) gesture.moved=true
  if(gesture.points.length<95) gesture.points.push(point)
  else gesture.points[94]=point
}
function pointerUp(event:PointerEvent) {
  if(!gesture || gesture.id!==event.pointerId) return
  const currentGesture=gesture; pointerMove(event); gesture=undefined
  if(canvas.value?.hasPointerCapture(event.pointerId)) canvas.value.releasePointerCapture(event.pointerId)
  const point=position(event)
  if(currentGesture.moved) {
    if(pendingClick) { clearTimeout(pendingClick.timer); pendingClick=undefined }
    enqueue({action:'drag',button:currentGesture.button,path:[...currentGesture.points,point],duration_ms:Math.min(2000,Math.round(performance.now()-currentGesture.started))})
    return
  }
  if(currentGesture.button!=='left') {
    enqueue({action:'click',button:currentGesture.button,...point}); return
  }
  const rect=canvas.value!.getBoundingClientRect()
  if(pendingClick && performance.now()-pendingClick.started<500 && Math.hypot((point.x-pendingClick.point.x)*rect.width,(point.y-pendingClick.point.y)*rect.height)<6) {
    clearTimeout(pendingClick.timer); pendingClick=undefined
    enqueue({action:'double_click',button:'left',...point}); return
  }
  if(pendingClick) { clearTimeout(pendingClick.timer); const prior=pendingClick; pendingClick=undefined; enqueue({action:'click',button:prior.button,...prior.point}) }
  pendingClick={point,button:'left',started:performance.now(),timer:setTimeout(()=>{
    const click=pendingClick; pendingClick=undefined
    if(click) enqueue({action:'click',button:click.button,...click.point})
  },500)}
}
function cancelPointer() { gesture=undefined }
function scroll(event:WheelEvent) {
  if(current.value?.read_only) return
  anchor.value=undefined
  const horizontal=Math.abs(event.deltaX)>Math.abs(event.deltaY)
  const axis=horizontal?'horizontal':'vertical'
  const factor=event.deltaMode===1?40:event.deltaMode===2?360:1
  const delta=Math.round((horizontal?event.deltaX:-event.deltaY)*factor)
  if(!delta) return
  wheelPending={point:position(event),axis,delta:Math.max(-1200,Math.min(1200,(wheelPending?.axis===axis?wheelPending.delta:0)+delta))}
  clearTimeout(wheelTimer)
  wheelTimer=setTimeout(flushWheel,100)
}
function flushWheel() {
  if(disposed || !wheelPending) return
  if(busy.value) { wheelTimer=setTimeout(flushWheel,100); return }
  const wheel=wheelPending; wheelPending=undefined
  enqueue({action:'scroll',...wheel.point,delta:wheel.delta,axis:wheel.axis})
}
async function paste(send: boolean, sendKey:'enter'|'ctrl_enter') {
  if(!anchor.value || !text.value.trim()) return
  const requestedWindow=selected.value, draft=text.value, revision=viewRevision.value
  if(await operate({action:'text',x:anchor.value.x,y:anchor.value.y,text:draft,send,send_key:sendKey})) {
    if(drafts.value[requestedWindow]===draft) drafts.value[requestedWindow]=''
    if(send && revision===viewRevision.value) { observing.value=true; status.value='已提交桌面，正在观察回复' }
  }
}
onMounted(async()=>{await loadWindows(); schedule()})
onBeforeUnmount(()=>{disposed=true; for(const controller of frameControllers.values()) controller.abort(); clearGestures(); clearTimeout(timer); clearTimeout(later)})
</script>
<template>
  <div class="projection library-reader-theme-dialog is-reader-theme-dark">
    <WorkspaceMenu :items="menus" @select="id=>executeDockWindowCommand(dock,id)">
      <template #actions>
        <button class="menu-refresh" :disabled="busy || !selected" title="刷新画面" @click="refresh">↻ 刷新</button>
        <label class="menu-observe"><input v-model="observing" :disabled="!selected" type="checkbox" />低频观察 · 2.5 秒</label>
      </template>
    </WorkspaceMenu>
    <DockWorkspace :dock="dock">
      <template #applications-actions><button :disabled="listing" title="刷新窗口清单" aria-label="刷新窗口清单" @click="loadWindows">↻</button></template>
      <template #applications>
        <div class="window-search"><input v-model="search" placeholder="搜索应用或窗口…" aria-label="搜索本机窗口" /></div>
        <div class="window-list">
          <button v-for="window in visibleWindows" :key="window.id" :class="{selected:window.id===selected}" :title="`${window.title} · ${window.application}`" :aria-pressed="window.id===selected" @click="selectWindow(window.id)">
            <img v-if="window.icon && !failedIcons.has(window.id)" :src="window.icon" class="window-icon" alt="" @error="failedIcons.add(window.id)" />
            <component v-else :is="applicationIcon(window)" class="window-icon" aria-hidden="true" />
            <span class="window-title">{{ window.title }}</span>
          </button>
          <p v-if="!visibleWindows.length">{{ listing?'正在读取窗口…':'暂无匹配的可见窗口' }}</p>
        </div>
      </template>
      <template #text-transfer><p v-if="current?.read_only" class="window-note">当前画面只读，支持刷新与定时观察。</p><TextTransferTool v-else v-model="text" :busy="busy" :located="!!anchor" :target="current?.title || ''" @locate="picking=true" @transfer="paste" /></template>
      <EditorTabs v-if="openWindows.length" :tabs="openWindows" :active="selected" label="打开的应用窗口" @activate="selectWindow" @close="closeWindowTab" @move="moveWindowTab" />
      <p v-if="error" role="alert" class="error">{{ error }}</p>
      <p v-if="picking" class="locate">请点击画面中接收文本的输入框。<button @click="picking=false">取消</button></p>
      <div class="stage"><canvas v-show="selected" ref="canvas" :class="{picking}" aria-label="本机应用原生窗口，支持左右键、双击、拖拽和滚动" @pointerdown="pointerDown" @pointermove="pointerMove" @pointerup="pointerUp" @pointercancel="cancelPointer" @lostpointercapture="cancelPointer" @contextmenu.prevent @wheel.prevent="scroll" /><p v-if="!selected">选择窗口后按需同步画面</p></div>
      <div class="status-bar"><span>{{ current?.title || '选择应用窗口' }}</span><span title="左／右／中键、双击、横向／纵向滚动；拖拽松手后执行">{{ busy ? '正在执行操作…' : status }}</span></div>
    </DockWorkspace>
  </div>
</template>
<style scoped>
img.window-icon{object-fit:contain;background:#f5f5f5;border-radius:3px}
.projection{height:100%;width:100%;min-width:0;box-sizing:border-box;display:flex;flex-direction:column;background:#181818;color:#ddd;font:13px 'Segoe UI','Microsoft YaHei',sans-serif;overflow:hidden}button{background:#292929;border:1px solid #444;border-radius:7px;color:inherit;padding:7px 12px;cursor:pointer}button:disabled{opacity:.4;cursor:default}.stage{position:relative;flex:1;min-width:0;min-height:0;overflow:hidden;display:flex;align-items:center;justify-content:center;background:#111;padding:12px}.stage canvas{position:absolute;display:block;max-width:calc(100% - 24px);max-height:calc(100% - 24px);width:auto;height:auto;cursor:pointer;touch-action:none;user-select:none}.stage canvas.picking{cursor:crosshair}.menu-refresh{padding:2px 8px;height:24px;border-color:transparent;background:transparent;border-radius:4px;font-size:12px}.menu-refresh:hover{background:var(--reader-hover)}.menu-observe{display:flex;align-items:center;gap:4px;white-space:nowrap;font-size:11px;color:var(--reader-muted)}.status-bar{display:flex;justify-content:space-between;gap:12px;flex:none;min-height:24px;align-items:center;padding:0 12px;border-top:1px solid var(--reader-border);color:var(--reader-muted);font-size:11px}.status-bar span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.error,.locate{color:#f5b888;padding:10px 18px;margin:0}.window-search{padding:10px}.window-search input{width:100%;box-sizing:border-box;padding:9px;border:1px solid #444;border-radius:7px;background:#181818;color:inherit}.window-list{padding:0 6px}.window-list button{display:flex;align-items:center;text-align:left;gap:9px;width:100%;min-width:0;box-sizing:border-box;margin-bottom:2px;padding:7px 10px;border-color:transparent;background:transparent}.window-list button:hover{background:#292929}.window-list button.selected{background:#303030;border-color:#555}.window-icon{width:18px;height:18px;flex:none;color:#aaa}.window-list button.selected .window-icon{color:#ddd}.window-title{min-width:0;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.window-note{font-size:11px;color:#888}.window-note{padding:8px 12px;line-height:1.7}@media(max-width:760px){.menu-observe{font-size:10px}.status-bar{gap:8px;padding:0 8px}.status-bar span:first-child{max-width:30%}}
</style>
