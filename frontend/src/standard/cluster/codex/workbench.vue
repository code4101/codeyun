<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import MessageContent from './MessageContent.vue'
import api from '@/api'
import DockWorkspace from '@/components/docking/DockWorkspace.vue'
import { useDockLayout } from '@/components/docking/useDockLayout'
import WorkspaceMenu from '@/components/editor-workspace/WorkspaceMenu.vue'
import { dockWindowMenuItems, executeDockWindowCommand } from '@/components/editor-workspace/workspaceMenu'
import EditorTabs from '@/components/editor-workspace/EditorTabs.vue'
import ResourceExplorer from '@/components/resource-explorer/ResourceExplorer.vue'
import type { ResourceNode } from '@/components/resource-explorer/resourceTree'
import '@/standard/pdf/library/readerTheme.css'

interface Chat { id: string; title: string; cwd?: string; hostId?: string; kind?: string; status?: string }
interface Project { projectId: string; label: string; path?: string; hostId?: string }
interface Item { id?: string; type: string; text?: string; phase?: string; displayText?: string; attachments?: { name: string; path?: string; imageUrl?: string | null }[]; content?: { type: string; text?: string }[]; command?: string; status?: string; [key: string]: unknown }
interface Turn { id: string; status: string; items: Item[]; durationMs?: number | null; startedAt?: number }
interface Detail { thread: { id: string; title: string; cwd?: string; status?: { type: string } }; turns: Turn[]; page?: { hasMore: boolean; nextCursor?: string } }
const route = useRoute(), router = useRouter()
const dock = useDockLayout('codeyun.codex.workbench.dock.v1', [
  { id: 'chats', title: '项目与聊天', icon: 'library', position: 'left', open: true },
  { id: 'execution', title: '执行详情', icon: 'document', position: 'right' },
])
const chats = ref<Chat[]>([]), opened = ref<Chat[]>([]), selected = ref('')
const projects = ref<Project[]>([]), newProject = ref('')
const details = ref<Record<string, Detail>>({}), drafts = ref<Record<string, string>>({})
const search = ref(''), collapsed = ref(new Set<string>()), error = ref(''), notice = ref('')
const loading = ref(false), sending = ref(false), refreshing = ref(false), autoSync = ref(true)
const syncedAt = ref(''), feed = ref<HTMLElement>(), showProcess = ref(true)
const connected = ref(false)
let disposed = false, timer: ReturnType<typeof setTimeout> | undefined
const current = computed(() => details.value[selected.value])
const draftKey = computed(() => selected.value || `new:${newProject.value}`)
const draft = computed({ get: () => drafts.value[draftKey.value] || '', set: value => { drafts.value[draftKey.value] = value } })
const projectLabel = computed(() => projects.value.find(project => project.projectId === newProject.value)?.label || 'codeyun')
function pathKey(path: string) { return path.replace(/\\/g, '/').replace(/\/$/, '').toLowerCase() }
function projectFor(node: ResourceNode) { return projects.value.find(project => project.path && pathKey(project.path) === pathKey(node.id.slice(8))) }
const nodes = computed<ResourceNode[]>(() => {
  const groups = new Map<string, Chat[]>()
  for (const project of projects.value) if (project.path && (!search.value || project.label.toLowerCase().includes(search.value.toLowerCase()))) groups.set(project.path, [])
  for (const chat of chats.value.filter(chat => !search.value || `${chat.title} ${chat.cwd}`.toLowerCase().includes(search.value.toLowerCase()))) {
    const key = projects.value.find(project => project.path && chat.cwd && pathKey(project.path) === pathKey(chat.cwd))?.path || chat.cwd || '其他聊天'
    groups.set(key, [...(groups.get(key) || []), chat])
  }
  return [...groups].map(([key, rows]) => ({ id: `project:${key}`, name: key.split(/[\\/]/).filter(Boolean).pop() || key, kind: 'directory', compact: false, expanded: !collapsed.value.has(`project:${key}`), children: rows.map(chat => ({ id: chat.id, name: chat.title, kind: 'file' })) }))
})
const turns = computed(() => [...(current.value?.turns || [])].reverse())
const processItems = computed(() => turns.value.flatMap(turn => turn.items.filter(item => !['userMessage', 'agentMessage', 'reasoning'].includes(item.type))).reverse())
const menus = computed(() => [
  { id: 'chat', label: '聊天', children: [{ id: 'new', label: '新建 CodeYun 聊天' }, { id: 'refresh', label: '刷新会话' }, { id: 'history', label: '历史与工时' }] },
  { id: 'window', label: '窗口', children: dockWindowMenuItems(dock) },
])
function message(e: unknown) { return (e as { response?: { data?: { detail?: string } }; message?: string }).response?.data?.detail || (e as Error).message || String(e) }
function activity(turn: Turn) { return turn.items.filter(item => !['userMessage', 'reasoning'].includes(item.type) && (item.type !== 'agentMessage' || item.phase === 'commentary')) }
function latestProgress(turn: Turn) { return [...turn.items].reverse().find(item => item.type === 'agentMessage' && item.phase === 'commentary') }
function duration(turn: Turn) { const seconds = Math.round((turn.durationMs || 0) / 1000); return seconds ? `${Math.floor(seconds / 60) ? `${Math.floor(seconds / 60)} 分 ` : ''}${seconds % 60} 秒` : '' }
function state(value?: string) { return ({ active: '运行中', idle: '空闲', completed: '已完成', inProgress: '运行中', interrupted: '已中断', failed: '失败' } as Record<string, string>)[value || ''] || value || '就绪' }
function toolLabel(type: string) { return ({ commandExecution: '命令执行', mcpToolCall: '工具调用', fileChange: '文件修改', webSearch: '网页搜索' } as Record<string, string>)[type] || type }
async function loadList() {
  const [{ data }, { data: projectData }] = await Promise.all([
    api.get<{ pinnedThreads: Chat[]; threads: Chat[] }>('/codex/desktop/threads', { timeout: 60000 }),
    api.get<{ projects: Project[] }>('/codex/desktop/projects', { timeout: 60000 }),
  ])
  if (disposed) return
  chats.value = [...data.pinnedThreads, ...data.threads].filter(chat => chat.kind === 'codex' && chat.hostId === 'local')
  projects.value = projectData.projects.filter(project => project.hostId === 'local' && project.path)
  connected.value = true
  syncedAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
}
async function read(id: string, cursor?: string) {
  const { data } = await api.get<Detail>(`/codex/desktop/threads/${encodeURIComponent(id)}`, { params: cursor ? { cursor } : undefined, timeout: 60000 })
  if (disposed) return
  const before = feed.value
  const atBottom = !before || before.scrollHeight - before.scrollTop - before.clientHeight < 80
  const previous = details.value[id]
  // Older pages append to newest-first turns; periodic refresh retains already-loaded history.
  const base = cursor ? previous?.turns || [] : data.turns
  const extra = cursor ? data.turns : previous?.turns || []
  const seen = new Set(base.map(turn => turn.id))
  details.value[id] = { ...data, turns: [...base, ...extra.filter(turn => !seen.has(turn.id))], page: cursor || !previous ? data.page : previous.page }
  syncedAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  if (!cursor && id === selected.value && atBottom) { await nextTick(); feed.value?.scrollTo({ top: feed.value.scrollHeight }) }
}
async function open(id: string) {
  selected.value = id; error.value = ''; notice.value = ''
  const chat = chats.value.find(chat => chat.id === id)
  if (chat && !opened.value.some(tab => tab.id === id)) opened.value.push(chat)
  void router.replace({ query: { ...route.query, thread: id } })
  loading.value = true
  try { await read(id) } catch (e) { if (selected.value === id) error.value = message(e) }
  finally { if (selected.value === id) loading.value = false }
}
function newChat(projectId = '') { newProject.value = projectId; selected.value = ''; error.value = ''; notice.value = ''; void router.replace({ query: { ...route.query, thread: undefined } }); void nextTick(() => document.querySelector<HTMLTextAreaElement>('.composer textarea')?.focus()) }
function close(id: string) { opened.value = opened.value.filter(chat => chat.id !== id); if (selected.value === id) { const last = opened.value[opened.value.length - 1]; if (last) void open(last.id); else newChat() } }
function move(id: string, before: string) { if (id === before) return; const tab = opened.value.find(chat => chat.id === id); if (!tab) return; opened.value = opened.value.filter(chat => chat.id !== id); opened.value.splice(opened.value.findIndex(chat => chat.id === before), 0, tab) }
async function refresh() {
  if (refreshing.value || disposed) return
  refreshing.value = true
  try { await loadList(); if (selected.value) await read(selected.value); error.value = '' }
  catch (e) { error.value = message(e); connected.value = false }
  finally { refreshing.value = false }
}
async function older() { const id = selected.value, cursor = current.value?.page?.nextCursor; if (!cursor || loading.value) return; loading.value = true; try { await read(id, cursor) } catch (e) { error.value = message(e) } finally { loading.value = false } }
async function send() {
  const id = selected.value, prompt = draft.value.trim()
  const key = draftKey.value, projectId = newProject.value
  if (!prompt || sending.value || !connected.value) return
  sending.value = true; error.value = ''; notice.value = ''
  try {
    const { data } = await api.post<{ threadId?: string; thread?: { id: string } }>(id ? `/codex/desktop/threads/${encodeURIComponent(id)}/messages` : '/codex/desktop/threads', { prompt, ...(!id && projectId ? { project_id: projectId } : {}) }, { timeout: 100000 })
    drafts.value[key] = ''; notice.value = '已提交至 Codex 桌面'
    await loadList()
    const created = data.threadId || data.thread?.id
    if (!id && created) await open(created)
    else if (id) await read(id)
    else notice.value = '桌面已接收新聊天，请在左侧选择查看'
  } catch (e) { error.value = message(e) }
  finally { sending.value = false }
}
function command(id: string) { if (executeDockWindowCommand(dock, id)) return; if (id === 'new') newChat(); if (id === 'refresh') void refresh(); if (id === 'history') void router.push('/cluster/codex') }
function schedule() { timer = setTimeout(async () => { if (disposed) return; if (autoSync.value && !sending.value) await refresh(); if (!disposed) schedule() }, 10000) }
onMounted(async () => { await refresh(); const id = String(route.query.thread || ''); if (id) await open(id); if (!disposed) schedule() })
onBeforeUnmount(() => { disposed = true; clearTimeout(timer) })
</script>

<template>
  <div class="codex-workbench library-reader-theme-dialog is-reader-theme-dark" :class="{ standalone: route.query.ui === '1' }">
    <header class="brand"><div><strong>Codex</strong><span>CodeYun 工作台</span></div><div class="connection"><button @click="router.push('/cluster/window-control')">窗口远控</button><i :class="{ offline: !connected }" />{{ connected ? '本机桌面' : refreshing ? '正在连接' : '桌面未连接' }}<button :disabled="refreshing" @click="refresh">{{ refreshing ? '同步中…' : '刷新' }}</button></div></header>
    <WorkspaceMenu :items="menus" @select="command" />
    <DockWorkspace :dock="dock">
      <template #chats>
        <div class="navigation"><button class="new-chat" @click="newChat()">＋ 新建聊天</button><input v-model="search" aria-label="搜索聊天" placeholder="搜索项目与聊天…" /></div>
        <ResourceExplorer :nodes="nodes" :selected-id="selected" :compact-folders="false" label="项目与聊天" @open="open($event.id)" @toggle="(path, expanded) => path.forEach(node => expanded ? collapsed.delete(node.id) : collapsed.add(node.id))">
          <template #icon="{ node }"><span v-if="node.kind === 'directory'" class="tree-icon">{{ node.expanded ? '▾' : '▸' }}</span></template>
          <template #label="{ row }"><span>{{ row.label }}</span><i v-if="chats.find(chat => chat.id === row.id)?.status === 'active'" class="running" /></template>
          <template #actions="{ node }"><button v-if="node.kind === 'directory' && projectFor(node)" class="project-new" :aria-label="`在 ${node.name} 中新建会话`" :title="`在 ${node.name} 中新建会话`" @click.stop="newChat(projectFor(node)!.projectId)" @keydown.stop>＋</button></template>
        </ResourceExplorer>
        <p v-if="!chats.length && !refreshing" class="muted">{{ error ? '桌面连接不可用' : '暂无本机聊天' }}</p>
      </template>
      <template #execution>
        <div class="execution"><h4>{{ current?.thread.title || '选择聊天' }}</h4><p class="muted">{{ current?.thread.cwd }}</p><p>{{ state(current?.thread.status?.type) }}</p><label><input v-model="showProcess" type="checkbox" /> 在对话中展示过程消息</label><details v-for="(item, index) in processItems" :key="item.id || index"><summary>{{ toolLabel(item.type) }} · {{ state(item.status) }}</summary><pre>{{ JSON.stringify(item, null, 2) }}</pre></details><p v-if="!processItems.length" class="muted">工具调用与执行结果会显示在这里。</p></div>
      </template>
      <EditorTabs :tabs="opened.map(chat => ({ id: chat.id, title: chat.title }))" :active="selected" label="打开的聊天" @activate="open" @close="close" @move="move"><template #actions><button class="tab-new" aria-label="新建聊天" @click="newChat()">＋</button></template></EditorTabs>
      <div v-if="current" class="chat-heading"><span>{{ current.thread.title }}</span><button aria-label="查看执行详情" @click="dock.toggle('execution')">⋯</button></div>
      <div ref="feed" class="feed" :aria-busy="loading">
        <div v-if="!connected && error" class="connection-error" role="alert"><h2>暂时无法连接 Codex 桌面</h2><p>请确认桌面应用已打开。桌面重启后，需要在当前桌面聊天中重新绑定连接。</p><button :disabled="refreshing" @click="refresh">{{ refreshing ? '正在重试…' : '重新连接' }}</button><details><summary>连接诊断</summary><p>{{ error }}</p><code>uv run python scripts/codex_desktop.py bind</code></details></div>
        <div v-else-if="!selected" class="welcome"><div class="welcome-icon">⌘</div><h1>想在 CodeYun 中做些什么？</h1><p>连接本机 Codex，在浏览器里继续工作。</p><div class="suggestions"><button @click="draft = '分析当前工作区的变更，概括主要改动和风险。'">分析工作区变更</button><button @click="draft = '帮我定位一个问题。我会提供复现步骤。'">排查问题</button></div></div>
        <div v-else class="conversation">
          <button v-if="current?.page?.hasMore" :disabled="loading" class="older" @click="older">加载更早消息</button>
          <p v-if="loading && !current" class="muted">读取桌面聊天…</p>
          <section v-for="turn in turns" :key="turn.id" class="turn">
            <template v-for="(item, index) in turn.items" :key="item.id || index">
              <MessageContent v-if="item.type === 'userMessage' || item.type === 'agentMessage' && item.phase !== 'commentary'" :item="item" />
            </template>
            <details v-if="activity(turn).length" class="turn-activity"><summary><span>{{ turn.status === 'inProgress' ? '正在处理' : '已处理' }} {{ duration(turn) }}</span><span class="activity-count">{{ activity(turn).filter(item => item.type !== 'agentMessage').length }} 项执行记录</span></summary><div class="activity-body"><template v-for="(item, index) in activity(turn)" :key="item.id || index"><MessageContent v-if="item.type === 'agentMessage'" :item="item" /><details v-else class="tool-event"><summary>{{ toolLabel(item.type) }} <span>{{ state(item.status) }}</span></summary><pre>{{ item.command || JSON.stringify(item, null, 2) }}</pre></details></template></div></details>
            <div v-if="turn.status === 'inProgress'" class="live-progress"><MessageContent v-if="showProcess && latestProgress(turn)" :item="latestProgress(turn)!" /><p class="thinking"><i />正在处理</p></div>
          </section>
        </div>
      </div>
      <div class="composer-area">
        <p v-if="error && connected" class="alert" role="alert">{{ error }}</p><p v-if="notice" class="notice" role="status">{{ notice }}</p>
        <form class="composer" @submit.prevent="send"><div class="composer-context"><span>▱ {{ current?.thread.cwd?.split(/[\\/]/).pop() || projectLabel }}</span><span>本机 Codex 桌面</span></div><textarea v-model="draft" :aria-label="selected ? '继续聊天' : '新聊天消息'" :placeholder="selected ? '继续这段聊天…' : '描述你想完成的工作…'" @keydown="event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); send() } }" /><div class="composer-footer"><span>{{ !connected ? '等待桌面连接，草稿会保留' : selected ? '与桌面共同操作同一会话' : '新聊天将在 Codex 桌面创建' }}</span><button :disabled="sending || !connected || !draft.trim()" type="submit">{{ sending ? '提交中…' : '发送 ↑' }}</button></div></form>
        <footer><label><input v-model="autoSync" type="checkbox" /> 自动同步</label><span>{{ syncedAt ? `更新于 ${syncedAt}` : '等待连接' }}</span><span>Enter 发送 · Shift+Enter 换行</span></footer>
      </div>
    </DockWorkspace>
  </div>
</template>

<style scoped>
.codex-workbench { --reader-surface:#181818; --reader-content:#181818; --reader-panel:#202020; --reader-text:#dedede; --reader-heading:#eee; --reader-muted:#909090; --reader-border:#2e2e2e; --reader-hover:#2b2b2b; --reader-active:#29384d; --reader-active-text:#dce8fb; display:flex; flex-direction:column; height:100%; min-height:0; box-sizing:border-box; overflow:hidden; color:var(--reader-text); background:var(--reader-content); border:1px solid var(--reader-border); border-radius:12px; font-family:Inter,'Segoe UI','Microsoft YaHei',sans-serif; font-size:13px; }
.brand { display:flex; align-items:center; justify-content:space-between; padding:12px 18px; border-bottom:1px solid var(--reader-border); background:var(--reader-panel); }
.brand strong { font-size:19px; }.brand span { margin-left:14px; color:var(--reader-muted); font-size:12px; }.connection { display:flex; gap:10px; align-items:center; font-size:12px; }.connection i,.running { width:6px; height:6px; background:#6ecf9a; border-radius:50%; display:inline-block; }.connection i.offline { background:#f3a66a; }.running { margin-left:8px; flex:none; }
button,input,textarea { font:inherit; }button { cursor:pointer; color:inherit; background:transparent; border:1px solid var(--reader-border); border-radius:6px; padding:6px 10px; }button:hover { background:var(--reader-hover); }button:disabled { opacity:.5; cursor:default; }
.navigation { padding:12px; display:grid; gap:12px; }.new-chat { text-align:left; }.navigation input { width:100%; box-sizing:border-box; padding:8px 10px; border:1px solid var(--reader-border); border-radius:6px; background:var(--reader-content); color:inherit; }.tree-icon { color:var(--reader-muted); }.tab-new { border:0; border-radius:0; }
.feed { flex:1; min-height:0; overflow:auto; padding:24px clamp(16px,4vw,64px); }.welcome { min-height:100%; display:flex; flex-direction:column; align-items:center; justify-content:center; text-align:center; }.welcome-icon { font-size:48px; color:var(--reader-muted); }.welcome h1 { font-size:clamp(22px,2.5vw,32px); font-weight:500; margin:20px 0 8px; }.welcome p { color:var(--reader-muted); font-size:14px; }.suggestions { display:flex; gap:10px; margin:22px 0; flex-wrap:wrap; }.conversation { max-width:860px; margin:auto; }.older { display:block; margin:0 auto 20px; }.muted { color:var(--reader-muted); font-size:12px; padding:0 12px; overflow-wrap:anywhere; }
.composer-area { flex:none; padding:0 clamp(16px,4vw,64px) 14px; }.composer { max-width:860px; margin:auto; border:1px solid var(--reader-border); border-radius:16px; background:var(--reader-panel); padding:14px 16px; }.composer-context { display:flex; gap:18px; font-size:12px; color:var(--reader-muted); }.composer textarea { width:100%; min-height:70px; max-height:200px; resize:vertical; box-sizing:border-box; margin:10px 0; border:0; outline:0; color:inherit; background:transparent; line-height:1.6; }.composer-footer { display:flex; justify-content:space-between; align-items:center; font-size:11px; color:var(--reader-muted); }.composer-footer button { background:#3479dd; color:white; border:0; padding:8px 14px; }footer { max-width:860px; margin:10px auto 0; display:flex; flex-wrap:wrap; gap:12px; justify-content:space-between; font-size:11px; color:var(--reader-muted); }footer label { display:flex; align-items:center; gap:5px; }.alert,.notice { max-width:860px; margin:8px auto; font-size:13px; overflow-wrap:anywhere; }.alert { color:#f3a66a; }.notice { color:#6ecf9a; }.execution { padding:12px; font-size:12px; overflow-wrap:anywhere; }.execution h4 { margin:0 0 10px; }.execution .muted { padding:0; }.execution details { border-top:1px solid var(--reader-border); margin-top:12px; padding-top:10px; }.execution summary { cursor:pointer; }.execution pre { white-space:pre-wrap; font-size:11px; }.prose :deep(pre) { overflow:auto; padding:14px; background:#11151b; border-radius:8px; }.prose :deep(a) { color:#80afff; }.prose :deep(img) { max-width:100%; }.prose :deep(table) { display:block; overflow:auto; border-collapse:collapse; }.prose :deep(td),.prose :deep(th) { padding:6px 10px; border:1px solid var(--reader-border); }.prose :deep(p:first-child) { margin-top:0; }.prose :deep(p:last-child) { margin-bottom:0; }
@media(max-width:760px) { .brand { padding:10px; }.brand span { display:none; }.feed { padding:16px; }.composer-area { padding:0 12px 12px; }footer span:last-child { display:none; } }
.codex-workbench.standalone { height:100%; min-height:0; border:0; border-radius:0; }
.brand { padding:10px 18px; background:#1c1c1c; }.brand strong { font-size:17px; font-weight:600; }.connection button { border:0; color:#aaa; }
.navigation { gap:10px; }.new-chat { border:0; padding:9px 10px; font-size:14px; }.navigation input { background:#252525; border-color:transparent; border-radius:8px; font-size:12px; padding:9px 11px; }.navigation input:focus { outline:1px solid #505050; }
.chat-heading { display:flex; align-items:center; justify-content:space-between; flex:none; padding:12px 24px; color:#ccc; font-size:13px; }.chat-heading span { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }.chat-heading button { border:0; font-size:20px; padding:0 8px; color:#888; }
.feed { padding:14px clamp(20px,5vw,80px) 24px; scrollbar-width:thin; scrollbar-color:#404040 transparent; }.conversation { max-width:780px; }.turn { margin-bottom:26px; }.turn-activity { color:#898989; font-size:12px; margin:8px 0 20px; }.turn-activity>summary { display:flex; align-items:center; gap:12px; cursor:pointer; padding:9px 0; border-bottom:1px solid #282828; list-style:none; }.turn-activity>summary::before { content:'›'; font-size:16px; transition:transform .15s; }.turn-activity[open]>summary::before { transform:rotate(90deg); }.activity-count { font-size:11px; color:#626262; }.activity-body { padding:8px 14px; border-left:1px solid #333; margin-top:12px; }.tool-event { margin:8px 0; font-size:12px; }.tool-event summary { cursor:pointer; color:#aaa; }.tool-event summary span { color:#777; margin-left:10px; }.tool-event pre { max-height:260px; overflow:auto; white-space:pre-wrap; background:#222; border-radius:8px; padding:12px; font-size:11px; }.thinking { display:flex; align-items:center; gap:8px; color:#888; font-size:12px; }.thinking i { width:7px; height:7px; border-radius:50%; background:#8c8c8c; animation:pulse 1.8s infinite; }@keyframes pulse { 50% { opacity:.25; } }
.composer-area { padding-bottom:10px; }.composer { max-width:780px; background:#303030; border-color:#353535; border-radius:20px; padding:12px 16px 10px; box-shadow:0 8px 30px #0002; }.composer-context { font-size:11px; color:#aaa; gap:14px; }.composer textarea { min-height:52px; margin:10px 0 4px; font-size:14px; resize:none; }.composer textarea::placeholder { color:#888; }.composer-footer { font-size:10px; color:#8c8c8c; }.composer-footer button { background:#2869c7; border-radius:20px; padding:7px 13px; font-size:12px; }.composer-footer button:disabled { background:#444; color:#888; opacity:1; }footer { max-width:780px; font-size:10px; color:#777; margin-top:8px; }footer input { accent-color:#4c8fea; }.alert,.notice { max-width:780px; }
@media(prefers-reduced-motion:reduce) { .thinking i { animation:none; } }
.connection-error { max-width:780px; margin:12px auto 24px; padding:18px 22px; border:1px solid #644b35; border-radius:12px; background:#30261f; }.connection-error h2 { margin:0 0 8px; font-size:16px; }.connection-error p { font-size:13px; color:#c5b5a7; overflow-wrap:anywhere; }.connection-error details { margin-top:14px; font-size:12px; }.connection-error summary { cursor:pointer; }.connection-error code { overflow-wrap:anywhere; }
:deep(.resource-row[data-kind=file] .row-leading) { display:none; }
:deep(.resource-row .row-label) { flex:1; }
.project-new { flex:none; padding:0; width:24px; height:22px; border:0; font-size:18px; color:#a5a5a5; opacity:.6; }.project-new:hover { opacity:1; }
</style>
