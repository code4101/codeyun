<script setup lang="ts">
import { computed, defineAsyncComponent, onBeforeUnmount, reactive, ref, watch } from 'vue'
import api from '@/api'
import { useUserStore } from '@/store/userStore'
import type { ReaderTreeItem } from './readerTree'
const NoteEditor = defineAsyncComponent(() => import('@/components/NoteEditor.vue'))
const props = defineProps<{resourceType:string;resourceId:string;bookTitle:string;chapterId:string;items:ReaderTreeItem[];active?:boolean}>()
const user=useUserStore(), choice=ref('@current'), loading=ref(false), message=ref('')
const scopes=computed(()=>{
  const byId=new Map(props.items.map(item=>[item.id,item])), result:ReaderTreeItem[]=[], seen=new Set<string>()
  let item=byId.get(props.chapterId)
  while(item && !seen.has(item.id)) { result.push(item);seen.add(item.id);item=byId.get(item.parentId || '') }
  return result
})
const chapter=computed(()=>choice.value==='@current' ? props.chapterId : choice.value)
type Page={resource_type:string;resource_id:string;chapter_id:string;content:string;revision:number}
type Draft={page:Page;saved:string;error:string;owner:number;storageKey:string;pending?:Promise<void>}
const current=ref<Draft>(), drafts=new Map<string,Draft>()
// Successful autosaves are silent; only transient or actionable states occupy space.
const status=computed(()=>!user.user?.id ? '登录后可保存个人笔记' : loading.value ? '正在读取…' : current.value?.pending ? '正在保存…' : current.value && current.value.page.content!==current.value.saved ? '草稿' : '')
const key=(page:Pick<Page,'resource_type'|'resource_id'|'chapter_id'>)=>`codeyun.reading-note:${user.user?.id}:${JSON.stringify([page.resource_type,page.resource_id,page.chapter_id])}`
let timer:ReturnType<typeof setTimeout> | undefined, generation=0
function remember(draft:Draft) {
  try { localStorage.setItem(draft.storageKey,JSON.stringify({content:draft.page.content,revision:draft.page.revision})) } catch { /* server saving still works */ }
}
function edit(content:string) {
  if(!current.value || loading.value) return
  const draft=current.value;draft.page.content=content;drafts.set(draft.storageKey,draft);remember(draft)
  clearTimeout(timer);timer=setTimeout(()=>void save(draft),700)
}
async function save(draft:Draft) {
  if(draft.error || draft.owner!==user.user?.id) return
  while(draft.pending) { await draft.pending;if(draft.error || draft.owner!==user.user?.id) return }
  if(draft.page.content===draft.saved) { drafts.delete(draft.storageKey);return }
  const snapshot={...draft.page}
  draft.pending=(async()=>{
    try {
      const {data}=await api.put<Page>('/library-reading-notes',snapshot)
      draft.page.revision=data.revision;draft.saved=snapshot.content
      if(draft.page.content===snapshot.content) { drafts.delete(draft.storageKey);try { localStorage.removeItem(draft.storageKey) } catch {} }
      else remember(draft)
    } catch(e) { draft.error=(e as {response?:{data?:{detail?:string}}}).response?.data?.detail || '保存失败，草稿已保留，可重试';remember(draft) }
  })()
  await draft.pending;draft.pending=undefined
  if(!draft.error && draft.page.content!==draft.saved) await save(draft)
}
async function load() {
  const version=++generation, previous=current.value
  const scope={resource_type:props.resourceType,resource_id:props.resourceId,chapter_id:chapter.value}
  clearTimeout(timer);current.value=undefined;message.value='';loading.value=true
  if(previous) await save(previous)
  if(version!==generation) return
  try {
    const {data}=await api.get<Page>('/library-reading-notes',{params:scope})
    if(version!==generation) return
    let local:{content:string;revision:number} | undefined
    try { local=JSON.parse(localStorage.getItem(key(scope)) || 'null') || undefined } catch {}
    const draft=reactive<Draft>({page:{...data},saved:data.content,error:'',owner:user.user!.id,storageKey:key(scope)})
    if(local) {
      draft.page.content=local.content
      if(local.revision!==data.revision && local.content!==data.content) draft.error='其他窗口已更新，草稿已恢复；请复制草稿后重新读取最新版本。'
    }
    drafts.set(key(scope),draft);current.value=draft
  } catch(e) { if(version===generation) message.value=(e as {response?:{data?:{detail?:string}}}).response?.data?.detail || '笔记读取失败，请重试' }
  finally { if(version===generation) loading.value=false }
}
async function retry() { if(current.value) { current.value.error='';await save(current.value) } else await load() }
watch(()=>props.resourceId,()=>{choice.value='@current'})
watch(()=>props.chapterId,()=>{if(choice.value && choice.value!=='@current' && !scopes.value.some(item=>item.id===choice.value)) choice.value='@current'})
watch(()=>[props.resourceType,props.resourceId,chapter.value,props.active,user.user?.id],()=>{if(props.resourceId && props.active!==false && user.user?.id) void load()},{immediate:true})
onBeforeUnmount(()=>{generation++;clearTimeout(timer);for(const draft of drafts.values()) void save(draft)})
</script>
<template>
  <section class="reading-notes">
    <label class="scope">绑定范围<select v-model="choice" aria-label="笔记绑定范围">
      <option value="@current">{{ chapterId ? `当前章节 · ${scopes[0]?.title || '当前目录'}` : `整本书 · ${bookTitle}` }}</option>
      <option v-for="item in scopes.slice(1)" :key="item.id" :value="item.id">上级目录 · {{ item.title }}</option>
      <option value="">整本书 · {{ bookTitle }}</option>
    </select></label>
    <p v-if="status" class="status">{{ status }}</p>
    <p v-if="message || current?.error" class="error" role="alert">{{ message || current?.error }} <button @click="retry">重试</button></p>
    <NoteEditor v-if="current && !loading" :key="key(current.page)" :model-value="current.page.content" mode="simple" :auto-focus-on-empty="false" @update:model-value="edit" />
    <button v-if="current" class="save" :disabled="loading || !!current.pending" @click="retry">保存笔记</button>
  </section>
</template>
<style scoped>
.reading-notes{display:flex;flex-direction:column;flex:1;min-height:0;overflow:hidden;padding:8px;gap:6px}.scope{display:flex;flex-direction:column;gap:5px;font-size:12px}.scope select{min-width:0;width:100%;padding:6px;background:var(--reader-content);color:var(--reader-text);border:1px solid var(--reader-border)}.status{margin:0;color:var(--reader-muted);font-size:11px}.error{font-size:12px;color:#c66}.save{align-self:flex-end}.reading-notes :deep(.editor-container){flex:1;min-height:0}
</style>

<style>
.dock-region.bottom .reading-notes { padding: 4px 8px; gap: 4px; position: relative; }
.dock-region.bottom .scope { flex-direction: row; align-items: center; white-space: nowrap; padding-right: 70px; }
.dock-region.bottom .scope select { flex: 1; width: 0; padding: 3px 6px; }
.dock-region.bottom .save { position: absolute; right: 8px; top: 5px; }
</style>
