<script setup lang="ts">
import { computed, ref } from 'vue'
import type { GallerySnapshot, GalleryCommand } from './gallery'

const props = defineProps<{ state: GallerySnapshot | null; scope: string; disabled: boolean }>()
const emit = defineEmits<{ command: [command: GalleryCommand] }>()
const query = ref(''), newGroup = ref(''), over = ref('')
const collapsed = ref(new Set<string>())
const editing = ref<{ kind: 'group' | 'item'; id: string; title: string }>()
const locked = computed(() => props.disabled || !props.state || props.state.readOnly)
const count = (groupId: string) => props.state?.items.filter(item => item.groupId === groupId).length ?? 0
const items = (groupId: string) => props.state?.items.filter(item => item.groupId === groupId && item.title.toLocaleLowerCase().includes(query.value.trim().toLocaleLowerCase())) ?? []
function create() {
  if (locked.value || !newGroup.value.trim()) return
  emit('command', { action: 'create-group', title: newGroup.value.trim() }); newGroup.value = ''
}
function rename() {
  if (!editing.value || !editing.value.title.trim() || locked.value) return
  const edit = editing.value
  emit('command', edit.kind === 'group' ? { action: 'rename-group', groupId: edit.id, title: edit.title } : { action: 'rename-item', itemId: edit.id, title: edit.title })
  editing.value = undefined
}
function store(groupId: string, ids = props.state?.selection.ids ?? []) {
  if (!locked.value && ids.length) emit('command', { action: 'store', groupId, selectedIds: ids })
}
function dragSelection(event: DragEvent) {
  if (locked.value || !props.state?.selection.ids.length) { event.preventDefault(); return }
  event.dataTransfer!.effectAllowed = 'move'
  event.dataTransfer!.setData('application/x-codeyun-gallery-selection', JSON.stringify({ scope: props.scope, ids: props.state.selection.ids }))
}
function dragItem(event: DragEvent, itemId: string) {
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
    if (raw) { const value = JSON.parse(raw); if (value.documentId === props.scope && typeof value.itemId === 'string') emit('command', { action: 'move', itemId: value.itemId, groupId }) }
  } catch { /* Ignore foreign drag payloads. */ }
}
</script>

<template>
  <section class="gallery-tool" aria-label="图库">
    <header><strong>已收纳</strong><span>{{ state?.items.length ?? 0 }} 个子图</span></header>
    <p class="description">暂时移出画布的子图保存在这里，随时取回。</p>
    <div v-if="state" class="selection" :class="{ available: state.selection.ids.length && !locked }" :draggable="!locked && !!state.selection.ids.length" @dragstart="dragSelection">
      <strong>{{ state.selection.ids.length ? `已选 ${state.selection.ids.length} 个对象` : '在画布上选中一个子图' }}</strong>
      <small>{{ state.selection.ids.length ? '拖动这里到分组，或点击“收纳选中”' : '框选节点；内部连线会一同收纳' }}</small>
    </div>
    <p v-else class="empty">{{ scope ? '正在加载图库…' : '打开一张画布，查看它的图库' }}</p>
    <p v-if="state?.readOnly" class="empty">只读图库</p>
    <input v-if="state?.items.length" v-model="query" type="search" placeholder="搜索子图" aria-label="搜索图库子图">
    <form class="create-group" @submit.prevent="create"><input v-model="newGroup" maxlength="120" placeholder="新分组名称" aria-label="新分组名称" :disabled="locked"><button :disabled="locked || !newGroup.trim()">添加</button></form>
    <form v-if="editing" class="rename" @submit.prevent="rename"><input v-model="editing.title" maxlength="120" aria-label="修改图库名称" :disabled="locked"><button :disabled="locked || !editing.title.trim()">保存</button><button type="button" @click="editing = undefined">取消</button></form>
    <div class="groups">
      <section v-for="group in state?.groups ?? []" :key="group.id" class="group" :class="{ over: over === group.id }" :data-gallery-group="group.id" @dragover="dragOver($event, group.id)" @dragleave="over = ''" @drop="drop($event, group.id)">
        <div class="group-heading">
          <button class="group-title" :aria-expanded="!collapsed.has(group.id)" @click="collapsed.has(group.id) ? collapsed.delete(group.id) : collapsed.add(group.id)">{{ collapsed.has(group.id) ? '▸' : '▾' }} {{ group.title }} <small>{{ count(group.id) }}</small></button>
          <button class="icon" :title="`重命名${group.title}分组`" :aria-label="`重命名${group.title}分组`" :disabled="locked" @click="editing = { kind: 'group', id: group.id, title: group.title }">✎</button>
          <button class="icon" :title="`删除${group.title}空分组`" :aria-label="`删除${group.title}空分组`" :disabled="locked || count(group.id) > 0" @click="emit('command', { action: 'delete-group', groupId: group.id })">×</button>
        </div>
        <button class="store" :disabled="locked || !state?.selection.ids.length" @click="store(group.id)">收纳选中</button>
        <template v-if="!collapsed.has(group.id)">
          <article v-for="item in items(group.id)" :key="item.id" class="item" :data-gallery-item="item.id" :draggable="!locked" @dragstart.stop="dragItem($event, item.id)">
            <div class="item-title" :title="item.title">{{ item.title }}</div>
            <div class="item-footer"><small>{{ item.objectCount }} 个对象</small><button :disabled="locked" :aria-label="`重命名子图${item.title}`" @click="editing = { kind: 'item', id: item.id, title: item.title }">改名</button><button class="take" :disabled="locked" @click="emit('command', { action: 'take', itemId: item.id })">取回画布</button></div>
          </article>
          <p v-if="!items(group.id).length" class="empty">{{ query ? '没有匹配的子图' : '拖入选中子图，或点击上方收纳' }}</p>
        </template>
      </section>
      <p v-if="state && !state.groups.length" class="empty">添加一个分组，开始收纳子图。</p>
    </div>
    <footer>子图卡片可拖回画布，或拖到其他分组。</footer>
  </section>
</template>

<style scoped>
.gallery-tool{display:flex;flex-direction:column;gap:12px;padding:14px;min-height:0;height:100%;box-sizing:border-box;color:var(--reader-text);font-size:12px}
header{display:flex;justify-content:space-between;align-items:center}header strong{font-size:15px}header span,.description,small,.empty,footer{color:var(--reader-muted)}p{margin:0}.description{line-height:1.6}
button,input{font:inherit;color:inherit;border:1px solid var(--reader-border);border-radius:5px;background:var(--reader-content);padding:6px 8px;min-width:0;box-sizing:border-box}button{cursor:pointer}button:hover{background:var(--reader-hover)}button:disabled{opacity:.4;cursor:default}input{width:100%}
.selection{padding:11px;border:1px dashed var(--reader-border);border-radius:7px;display:flex;flex-direction:column;gap:6px}.selection.available{cursor:grab;border-color:var(--reader-active-text,#609ef8);background:var(--reader-hover)}
.create-group,.rename{display:flex;gap:5px}.create-group input,.rename input{flex:1;width:0}.groups{flex:1;overflow:auto;min-height:0;display:flex;flex-direction:column;gap:12px}.group{border:1px solid var(--reader-border);border-radius:7px;padding:8px;display:flex;flex-direction:column;gap:8px}.group.over{border-color:var(--reader-active-text,#609ef8);background:var(--reader-hover)}
.group-heading{display:flex;align-items:center;gap:4px}.group-title{flex:1;text-align:left;border:0;background:transparent;padding:4px 0;overflow-wrap:anywhere;font-weight:600}.group-title small{font-weight:400;margin-left:3px}.icon{border:0;background:transparent;padding:3px 5px}.store{font-size:11px;color:var(--reader-active-text,#609ef8)}
.item{border:1px solid var(--reader-border);border-radius:6px;background:var(--reader-content);padding:9px;cursor:grab}.item-title{white-space:pre-wrap;overflow-wrap:anywhere;max-height:72px;overflow:hidden;line-height:1.5}.item-footer{display:flex;gap:5px;align-items:center;margin-top:9px}.item-footer small{flex:1}.item-footer button{padding:3px 5px;font-size:11px}.take{color:var(--reader-active-text,#609ef8)}.empty{font-size:11px;line-height:1.6;padding:4px 0}footer{font-size:11px;line-height:1.5}
</style>
