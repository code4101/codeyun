<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { GallerySnapshot, GalleryCommand, GalleryPreview, GalleryCanvasDrag } from './gallery'

const props = defineProps<{ state: GallerySnapshot | null; scope: string; disabled: boolean }>()
const emit = defineEmits<{ command: [command: GalleryCommand] }>()
const query = ref(''), searchOpen = ref(false), newGroup = ref(''), creating = ref(false), over = ref('')
const collapsed = ref(new Set<string>(['done', 'abandoned']))
const menu = ref(''), choosingGroup = ref(false)
const editing = ref<{ kind: 'group' | 'item'; id: string; title: string }>()
const root = ref<HTMLElement>()
const locked = computed(() => props.disabled || !props.state || props.state.readOnly)
watch(locked, value => { if (value) { menu.value = ''; choosingGroup.value = false; editing.value = undefined; creating.value = false } })
const count = (groupId: string) => props.state?.items.filter(item => item.groupId === groupId).length ?? 0
const items = (groupId: string) => props.state?.items.filter(item => item.groupId === groupId && item.title.toLocaleLowerCase().includes(query.value.trim().toLocaleLowerCase())) ?? []
const expanded = (id: string) => !!query.value.trim() || !collapsed.value.has(id)
watch(() => props.scope, () => { query.value = ''; searchOpen.value = false; creating.value = false; editing.value = undefined; menu.value = ''; choosingGroup.value = false; collapsed.value = new Set(['done', 'abandoned']) })
function toggleSearch() { searchOpen.value = !searchOpen.value; if (!searchOpen.value) query.value = '' }
function toggleCreate() { if (!locked.value) { creating.value = !creating.value; newGroup.value = ''; menu.value = '' } }
/** Explicit rows choose their group; blank space and the tool heading use the
 * first group. The workspace validates the source canvas before forwarding. */
function canvasDrag(value: GalleryCanvasDrag) {
  over.value = ''
  if (locked.value || value.phase === 'cancel' || !root.value) return
  const target = document.elementFromPoint(value.x, value.y)
  const tool = root.value.closest('[data-dock-tool="gallery"]')
  if (!target || !(root.value.contains(target) || (tool && tool.contains(target)))) return
  const id = target.closest<HTMLElement>('[data-gallery-group]')?.dataset.galleryGroup ?? props.state?.groups[0]?.id
  const group = props.state?.groups.find(item => item.id === id)
  if (!group) return
  if (value.phase === 'end') store(group.id, value.ids)
  else over.value = group.id
  return group.title
}
defineExpose({ toggleSearch, toggleCreate, locked, searchOpen, creating, canvasDrag })
function create() {
  if (locked.value || !newGroup.value.trim()) return
  emit('command', { action: 'create-group', title: newGroup.value.trim() }); newGroup.value = ''; creating.value = false
}
function rename() {
  if (!editing.value || !editing.value.title.trim() || locked.value) return
  const edit = editing.value
  emit('command', edit.kind === 'group' ? { action: 'rename-group', groupId: edit.id, title: edit.title } : { action: 'rename-item', itemId: edit.id, title: edit.title })
  editing.value = undefined
}
function edit(kind: 'group' | 'item', id: string, title: string) { editing.value = { kind, id, title }; menu.value = '' }
function store(groupId: string, ids = props.state?.selection.ids ?? []) {
  if (!locked.value && ids.length) { emit('command', { action: 'store', groupId, selectedIds: ids }); collapsed.value.delete(groupId); choosingGroup.value = false }
}
function dragSelection(event: DragEvent) {
  if (locked.value || !props.state?.selection.ids.length) { event.preventDefault(); return }
  event.dataTransfer!.effectAllowed = 'move'
  event.dataTransfer!.setData('application/x-codeyun-gallery-selection', JSON.stringify({ scope: props.scope, ids: props.state.selection.ids }))
}
function dragItem(event: DragEvent, itemId: string) {
  menu.value = ''
  if (locked.value) { event.preventDefault(); return }
  event.dataTransfer!.effectAllowed = 'move'
  event.dataTransfer!.setData('application/x-codeyun-gallery-item', JSON.stringify({ documentId: props.scope, itemId }))
}
function dragOver(event: DragEvent, groupId: string) {
  if (locked.value || !event.dataTransfer?.types.some(type => ['application/x-codeyun-gallery-selection', 'application/x-codeyun-gallery-item'].includes(type))) return
  event.preventDefault(); event.dataTransfer.dropEffect = 'move'; over.value = groupId
}
function drop(event: DragEvent, groupId: string) {
  over.value = ''
  if (locked.value) return
  event.preventDefault()
  try {
    const selection = event.dataTransfer?.getData('application/x-codeyun-gallery-selection')
    if (selection) { const value = JSON.parse(selection); if (value.scope === props.scope && Array.isArray(value.ids)) store(groupId, value.ids); return }
    const raw = event.dataTransfer?.getData('application/x-codeyun-gallery-item')
    if (raw) { const value = JSON.parse(raw); if (value.documentId === props.scope && typeof value.itemId === 'string') { emit('command', { action: 'move', itemId: value.itemId, groupId }); collapsed.value.delete(groupId) } }
  } catch { /* Ignore foreign drag payloads. */ }
}
function edgePath(preview: GalleryPreview, edge: GalleryPreview['edges'][number]) {
  const source = preview.nodes.find(node => node.id === edge.source), target = preview.nodes.find(node => node.id === edge.target)
  if (!source || !target) return ''
  const x = source.x + source.width / 2, y = source.y + source.height / 2
  const tx = target.x + target.width / 2, ty = target.y + target.height / 2
  return `M${x},${y} H${(x + tx) / 2} V${ty} H${tx}`
}
</script>

<template>
  <section ref="root" class="gallery-tool" aria-label="图库内容" @keydown.esc="menu = ''; choosingGroup = false; editing = undefined; creating = false" @focusout="event => { if (!(event.currentTarget as HTMLElement).contains(event.relatedTarget as Node)) menu = '' }">
    <div v-if="searchOpen" class="search"><svg viewBox="0 0 24 24"><circle cx="10" cy="10" r="6"/><path d="m15 15 5 5"/></svg><input v-model="query" autofocus type="search" placeholder="搜索子图" aria-label="搜索图库子图"></div>
    <form v-if="creating" class="inline-form" @submit.prevent="create"><input v-model="newGroup" autofocus maxlength="120" placeholder="分组名称" aria-label="新分组名称" :disabled="locked"><button :disabled="locked || !newGroup.trim()" aria-label="添加分组">✓</button><button type="button" aria-label="取消添加分组" @click="creating = false">×</button></form>
    <form v-if="editing" class="inline-form" @submit.prevent="rename"><input v-model="editing.title" autofocus maxlength="120" aria-label="修改图库名称" :disabled="locked"><button :disabled="locked || !editing.title.trim()" aria-label="保存名称">✓</button><button type="button" aria-label="取消改名" @click="editing = undefined">×</button></form>
    <p v-if="!state" class="empty">{{ scope ? '正在加载…' : '打开画布后查看图库' }}</p>
    <p v-else-if="state.readOnly" class="readonly">只读</p>
    <div class="groups">
      <section v-for="group in state?.groups ?? []" :key="group.id" class="group" :class="{ over: over === group.id }" :data-gallery-group="group.id" @dragover="dragOver($event, group.id)" @dragleave="event => { if (!(event.currentTarget as HTMLElement).contains(event.relatedTarget as Node)) over = '' }" @drop="drop($event, group.id)">
        <div class="group-heading">
          <button class="group-title" :aria-expanded="expanded(group.id)" @click="collapsed.has(group.id) ? collapsed.delete(group.id) : collapsed.add(group.id)"><svg viewBox="0 0 16 16" class="chevron" :class="{ expanded: expanded(group.id) }"><path d="m6 4 4 4-4 4"/></svg><span>{{ group.title }}</span><small class="count">{{ count(group.id) }}</small></button>
          <button v-if="!locked" class="icon hover-action" :class="{ visible: menu === group.id }" :aria-label="`${group.title}分组菜单`" :aria-expanded="menu === group.id" title="分组菜单" @click="menu = menu === group.id ? '' : group.id">···</button>
        </div>
        <div v-if="menu === group.id" class="menu" role="group" :aria-label="`${group.title}分组操作`"><button @click="edit('group', group.id, group.title)">重命名</button><button :disabled="count(group.id) > 0 || locked" :title="count(group.id) ? '请先取回或移动分组中的子图' : '删除空分组'" @click="emit('command', { action: 'delete-group', groupId: group.id }); menu = ''">删除分组</button></div>
        <template v-if="expanded(group.id)">
          <article v-for="item in items(group.id)" :key="item.id" class="item" :class="{ 'menu-open': menu === item.id }" :data-gallery-item="item.id" :draggable="!locked" @dragstart.stop="dragItem($event, item.id)">
            <div class="item-row">
              <div class="thumbnail" aria-hidden="true"><svg v-if="item.preview?.nodes.length" viewBox="0 0 96 62"><path v-for="(edge, index) in item.preview.edges" :key="index" :d="edgePath(item.preview, edge)" class="preview-edge"/><rect v-for="node in item.preview.nodes" :key="node.id" :x="node.x" :y="node.y" :width="node.width" :height="node.height" rx="1.5" class="preview-node"/></svg><svg v-else viewBox="0 0 24 24" class="fallback"><rect x="4" y="4" width="16" height="16" rx="3"/><path d="M8 9h8M8 13h5"/></svg></div>
              <div class="item-copy"><div class="item-title" :title="item.title">{{ item.title }}</div><small>{{ item.objectCount }} 个对象</small></div>
              <div v-if="!locked" class="item-actions hover-action"><button class="icon take" :aria-label="`取回画布：${item.title}`" title="取回画布" @click="emit('command', { action: 'take', itemId: item.id }); menu = ''"><svg viewBox="0 0 24 24"><path d="m8 5-4 4 4 4M4 9h10a5 5 0 0 1 0 10h-3"/></svg></button><button class="icon" :aria-label="`子图${item.title}菜单`" :aria-expanded="menu === item.id" title="子图菜单" @click="menu = menu === item.id ? '' : item.id">···</button></div>
            </div>
            <div v-if="menu === item.id" class="menu" role="group" :aria-label="`${item.title}操作`"><button @click="edit('item', item.id, item.title)">重命名</button><button v-for="target in state?.groups.filter(value => value.id !== group.id)" :key="target.id" @click="emit('command', { action: 'move', itemId: item.id, groupId: target.id }); menu = ''">移至{{ target.title }}</button></div>
          </article>
          <p v-if="query.trim() && !items(group.id).length" class="empty search-empty">没有匹配的子图</p>
        </template>
      </section>
      <p v-if="state && !state.groups.length" class="empty">点击 + 添加分组</p>
    </div>
    <div v-if="!locked && state?.selection.ids.length" class="selection-area">
      <div v-if="choosingGroup" class="destination" role="group" aria-label="选择收纳分组"><button v-for="group in state.groups" :key="group.id" :disabled="locked" @click="store(group.id)">{{ group.title }}<span>收纳</span></button><p v-if="!state.groups.length" class="empty">先添加一个分组</p></div>
      <button class="selection" draggable="true" :aria-expanded="choosingGroup" @dragstart="dragSelection" @click="choosingGroup = !choosingGroup"><svg viewBox="0 0 24 24"><path d="M4 8h16v12H4zM3 4h18v4H3zM10 12h4"/></svg><span>收纳选中</span><small>{{ state.selection.ids.length }}</small><svg class="selection-chevron" viewBox="0 0 16 16"><path d="m4 6 4 4 4-4"/></svg></button>
    </div>
  </section>
</template>

<style scoped>
.gallery-tool{display:flex;flex-direction:column;min-height:0;height:100%;box-sizing:border-box;color:var(--reader-text);font-size:12px;position:relative}
button,input{font:inherit;color:inherit;box-sizing:border-box;min-width:0}button{cursor:pointer;border:0;background:transparent}button:disabled{opacity:.4;cursor:default}button:focus-visible,input:focus-visible{outline:2px solid var(--reader-active-text,#609ef8);outline-offset:-2px}svg{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round;flex:none}small,.empty,.readonly{color:var(--reader-muted)}p{margin:0}.empty{padding:14px 12px;font-size:11px}.readonly{padding:6px 12px;font-size:10px}
.search,.inline-form{display:flex;align-items:center;gap:6px;margin:8px 10px;padding:5px 7px;background:var(--reader-content);border:1px solid var(--reader-border);border-radius:6px}.search svg{width:14px;height:14px;color:var(--reader-muted)}input{flex:1;width:0;background:transparent;border:0;padding:3px;outline:none}.inline-form button{padding:2px 4px}.groups{flex:1;overflow:auto;min-height:0;padding:2px 0}.group{border-bottom:1px solid var(--reader-border);position:relative}.group.over{background:var(--reader-hover);box-shadow:inset 3px 0 var(--reader-active-text,#609ef8)}.group-heading{display:flex;align-items:center;gap:4px;padding:0 10px;min-height:42px}.group-title{display:flex;align-items:center;gap:8px;flex:1;text-align:left;font-weight:600;padding:10px 0}.group-title span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.chevron{width:12px;height:12px;color:var(--reader-muted);transition:transform .15s}.chevron.expanded{transform:rotate(90deg)}.count{font-size:10px;font-weight:400;background:var(--reader-hover);border-radius:4px;padding:2px 5px;line-height:1.2}.icon{display:flex;justify-content:center;align-items:center;width:26px;height:26px;flex:none;border-radius:4px;font-size:16px}.icon:hover,.menu button:hover,.destination button:hover{background:var(--reader-hover)}.hover-action{opacity:0;pointer-events:none}.group-heading:hover .hover-action,.group-heading:focus-within .hover-action,.hover-action.visible,.item:hover .hover-action,.item:focus-within .hover-action,.item.menu-open .hover-action{opacity:1;pointer-events:auto}
.item{margin:0 7px;border-radius:6px;cursor:grab}.item-row{display:flex;align-items:center;gap:9px;padding:8px 5px;min-height:70px;border-top:1px solid var(--reader-border)}.item:hover,.item:focus-within,.item.menu-open{background:var(--reader-hover)}.thumbnail{width:70px;height:46px;flex:none;background:var(--reader-content);border:1px solid var(--reader-border);border-radius:4px;display:flex;align-items:center;justify-content:center;overflow:hidden}.thumbnail>svg{width:100%;height:100%}.thumbnail>svg.fallback{width:22px;height:22px;color:var(--reader-muted)}.preview-edge{stroke:var(--reader-muted);stroke-width:1;vector-effect:non-scaling-stroke}.preview-node{fill:var(--reader-hover);stroke:var(--reader-active-text,#609ef8);stroke-width:.8;vector-effect:non-scaling-stroke}.item-copy{flex:1;min-width:0}.item-title{white-space:nowrap;text-overflow:ellipsis;overflow:hidden;line-height:1.6;font-weight:500}.item-copy small{font-size:10px;display:block;margin-top:3px}.item-actions{display:flex;flex:none;gap:1px}.take{color:var(--reader-active-text,#609ef8)}.menu{display:flex;flex-direction:column;border:1px solid var(--reader-border);background:var(--reader-content);border-radius:5px;margin:0 10px 8px;padding:3px}.menu button{text-align:left;padding:7px 9px;border-radius:3px;font-size:11px}.search-empty{padding:4px 12px 10px}.selection-area{flex:none;padding:10px;border-top:1px solid var(--reader-border)}.selection{display:flex;align-items:center;gap:7px;width:100%;padding:8px 10px;border-radius:6px;color:var(--reader-active-text,#609ef8);background:var(--reader-hover);cursor:grab}.selection svg{width:16px;height:16px}.selection span{flex:1;text-align:left}.selection small{color:inherit;font-size:10px}.selection .selection-chevron{width:12px;height:12px}.destination{display:flex;flex-direction:column;margin-bottom:7px;border:1px solid var(--reader-border);border-radius:6px;padding:3px;background:var(--reader-content)}.destination button{display:flex;justify-content:space-between;padding:8px;border-radius:4px;text-align:left}.destination span{font-size:10px;color:var(--reader-muted)}
@media(hover:none){.hover-action{opacity:1;pointer-events:auto}}
</style>

