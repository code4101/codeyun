<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import ResourceFileIcon from './ResourceFileIcon.vue'
import { resourceIndex, resourceRows, type ResourceNode, type ResourceRow } from './resourceTree'
const props = withDefaults(defineProps<{
  nodes: ResourceNode[]; selectedId?: string; compactFolders?: boolean; label?: string; multiSelect?: boolean; cutIds?: string[]
  /** 业务提供方决定哪些资源可移动；null 目标代表根目录。 */
  canDrag?: (node: ResourceNode) => boolean
  canDrop?: (source: ResourceNode, target: ResourceNode | null) => boolean
}>(), { compactFolders: true, label: '资源管理器' })
const emit = defineEmits<{
  open: [node: ResourceNode]
  toggle: [nodes: ResourceNode[], expanded: boolean]
  retry: [node: ResourceNode]
  contextmenu: [event: MouseEvent, node: ResourceNode, selection: ResourceNode[]]
  move: [sources: ResourceNode[], target: ResourceNode | null]
  command: [command: 'rename' | 'delete' | 'cut' | 'copy' | 'paste' | 'cancel' | 'new-file' | 'new-folder', selection: ResourceNode[], target: ResourceNode | null]
  selection: [nodes: ResourceNode[]]
}>()
const rows = computed(() => resourceRows(props.nodes, props.compactFolders))
const root = ref<HTMLElement>()
const focusedId = ref('')
const selected = ref<string[]>([])
const anchorId = ref('')
const index = computed(() => resourceIndex(props.nodes))
const selection = computed(() => selected.value.map(id => index.value.get(id)?.node).filter((node): node is ResourceNode => !!node))
watch(() => props.selectedId, id => { if (id && !selected.value.includes(id)) selected.value = [id] }, { immediate: true })
function select(row: ResourceRow, event?: MouseEvent | KeyboardEvent) {
  if (props.multiSelect && event?.shiftKey) {
    const start = rows.value.findIndex(item => item.id === anchorId.value)
    const end = rows.value.findIndex(item => item.id === row.id)
    selected.value = rows.value.slice(Math.max(0, Math.min(start, end)), Math.max(start, end) + 1).map(item => item.node.id)
  } else if (props.multiSelect && (event?.ctrlKey || event?.metaKey)) {
    selected.value = selected.value.includes(row.node.id) ? selected.value.filter(id => id !== row.node.id) : [...selected.value, row.node.id]
    anchorId.value = row.id
  } else { selected.value = [row.node.id]; anchorId.value = row.id }
  focusedId.value = row.id
  emit('selection', selection.value)
}
function context(event: MouseEvent, row: ResourceRow) {
  if (!selected.value.includes(row.node.id)) select(row)
  emit('contextmenu', event, row.node, selection.value)
}
const dragged = ref<ResourceNode[]>([])
const dropTarget = ref<string | null>(null)
let expandTimer: ReturnType<typeof setTimeout> | undefined
function clearHover() { clearTimeout(expandTimer); expandTimer = undefined }
function resetDrag() { dragged.value = []; dropTarget.value = null; clearHover() }
function leave(event: DragEvent) {
  if (event.relatedTarget && (event.currentTarget as HTMLElement).contains(event.relatedTarget as Node)) return
  dropTarget.value = null; clearHover()
}
function clearSelection() { selected.value = []; emit('selection', []) }
function rootKeys(event: KeyboardEvent) {
  const modifier = event.ctrlKey || event.metaKey
  const command = event.key === 'Escape' ? 'cancel' : modifier && event.key.toLowerCase() === 'v' ? 'paste'
    : modifier && event.key.toLowerCase() === 'n' ? event.shiftKey ? 'new-folder' : 'new-file' : null
  if (!props.multiSelect || !command) return
  event.preventDefault(); event.stopPropagation()
  emit('command', command, selection.value, null)
  if (command === 'cancel') resetDrag()
}
onBeforeUnmount(resetDrag)
function startDrag(event: DragEvent, node: ResourceNode) {
  if (!props.canDrag?.(node) || !event.dataTransfer) { event.preventDefault(); return }
  if (!selected.value.includes(node.id)) selected.value = [node.id]
  dragged.value = (props.multiSelect ? selection.value : [node]).filter(item => props.canDrag?.(item))
  event.dataTransfer.effectAllowed = 'move'
  event.dataTransfer.setData('text/plain', node.name)
}
function destination(target: ResourceNode | null) {
  return target?.kind === 'file' ? index.value.get(target.id)?.parent ?? null : target
}
function accepts(target: ResourceNode | null) {
  return dragged.value.length > 0 && dragged.value.every(source => !!props.canDrop?.(source, destination(target)))
}
function dragOver(event: DragEvent, target: ResourceNode | null) {
  event.stopPropagation()
  if (!accepts(target)) { dropTarget.value = null; clearHover(); if (event.dataTransfer) event.dataTransfer.dropEffect = 'none'; return }
  event.preventDefault()
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'move'
  const targetId = destination(target)?.id ?? ''
  if (dropTarget.value !== targetId) {
    clearHover()
    dropTarget.value = targetId
    const row = rows.value.find(item => item.node.id === targetId)
    if (row && !row.expanded) expandTimer = setTimeout(() => emit('toggle', row.path, true), 650)
  }
}
function drop(event: DragEvent, target: ResourceNode | null) {
  event.stopPropagation()
  if (accepts(target)) { event.preventDefault(); emit('move', dragged.value, destination(target)) }
  resetDrag()
}
const focusId = computed(() => rows.value.find(row => row.id === focusedId.value)?.id ?? rows.value.find(row => row.path.some(node => node.id === props.selectedId))?.id ?? rows.value[0]?.id)
function activate(row: ResourceRow, event?: MouseEvent) {
  select(row, event)
  if (props.multiSelect && (event?.ctrlKey || event?.metaKey || event?.shiftKey)) return
  focusedId.value = row.id
  if (row.node.kind === 'file') emit('open', row.node)
  else emit('toggle', row.path, !row.expanded)
}
async function focus(id?: string, event?: KeyboardEvent) {
  if (!id) return
  focusedId.value = id
  const row = rows.value.find(item => item.id === id)
  if (props.multiSelect && row && !(event?.ctrlKey || event?.metaKey)) select(row, event)
  await nextTick()
  const index = rows.value.findIndex(row => row.id === id)
  root.value?.querySelectorAll<HTMLElement>('[role="treeitem"]')[index]?.focus()
}
function keydown(event: KeyboardEvent, row: ResourceRow, index: number) {
  if (event.target !== event.currentTarget) return
  if (props.multiSelect) {
    const modifier = event.ctrlKey || event.metaKey
    if (modifier && event.key.toLowerCase() === 'a') {
      event.preventDefault(); event.stopPropagation(); selected.value = rows.value.map(item => item.node.id); emit('selection', selection.value); return
    }
    const command = modifier && event.key.toLowerCase() === 'n' ? event.shiftKey ? 'new-folder' : 'new-file'
      : event.key === 'F2' ? 'rename' : event.key === 'Delete' ? 'delete' : event.key === 'Escape' ? 'cancel'
      : modifier && event.key.toLowerCase() === 'x' ? 'cut' : modifier && event.key.toLowerCase() === 'c' ? 'copy'
      : modifier && event.key.toLowerCase() === 'v' ? 'paste' : null
    if (command) {
      event.preventDefault(); event.stopPropagation()
      if (!selection.value.length) select(row)
      emit('command', command, selection.value, destination(row.node)); if (command === 'cancel') resetDrag(); return
    }
  }
  if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); activate(row); return }
  if (!['ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  event.stopPropagation()
  if (event.key === 'ArrowDown') void focus(rows.value[Math.min(index + 1, rows.value.length - 1)]?.id, event)
  else if (event.key === 'ArrowUp') void focus(rows.value[Math.max(index - 1, 0)]?.id, event)
  else if (event.key === 'Home') void focus(rows.value[0]?.id, event)
  else if (event.key === 'End') void focus(rows.value[rows.value.length - 1]?.id, event)
  else if (event.key === 'ArrowLeft') {
    if (row.node.kind === 'directory' && row.expanded) emit('toggle', row.path, false)
    else void focus(row.parentId)
  } else if (row.node.kind === 'directory') {
    if (!row.expanded) emit('toggle', row.path, true)
    else if (rows.value[index + 1]?.parentId === row.id) void focus(rows.value[index + 1]?.id)
  }
}
</script>
<template>
  <div ref="root" class="resource-tree" :class="{ 'root-drop-target': dropTarget === '' }" role="tree" :tabindex="multiSelect ? -1 : undefined" :aria-label="label" :aria-multiselectable="multiSelect || undefined" @keydown.self="rootKeys" @click.self="clearSelection(); root?.focus()" @dragover="dragOver($event, null)" @drop="drop($event, null)" @dragleave="leave">
    <template v-for="(row, index) in rows" :key="row.id">
      <div class="resource-row" role="treeitem" :data-kind="row.node.kind" :aria-level="row.depth + 1" :aria-expanded="row.node.kind === 'directory' ? row.expanded : undefined"
        :aria-selected="row.path.some(node => node.id === (multiSelect ? undefined : selectedId) || selected.includes(node.id))" :tabindex="row.id === focusId ? 0 : -1" :title="row.label"
        :style="{ paddingLeft: `${4 + row.depth * 16}px` }" @focus="focusedId = row.id" @click="activate(row, $event)" @keydown="keydown($event, row, index)"
        :draggable="canDrag?.(row.node) ?? false" :class="{ 'drop-target': dropTarget === destination(row.node)?.id, dragging: dragged.some(node => node.id === row.node.id), cut: cutIds?.includes(row.node.id) }"
        @dragstart="startDrag($event, row.node)" @dragend="resetDrag" @dragover="dragOver($event, row.node)" @dragleave="leave" @drop="drop($event, row.node)"
        @contextmenu="context($event, row)">
        <span class="row-leading">
          <slot name="icon" :node="row.node">
            <svg v-if="row.node.kind === 'directory'" class="chevron" :class="{ expanded: row.expanded }" viewBox="0 0 16 16" aria-hidden="true"><path d="m6 3 5 5-5 5" /></svg>
            <ResourceFileIcon v-else :name="row.node.name" />
          </slot>
        </span>
        <span class="row-label"><slot name="label" :row="row">{{ row.label }}</slot></span>
        <slot name="actions" :node="row.node" />
      </div>
      <div v-if="row.expanded && row.node.loading" class="status" role="status" :style="{ paddingLeft: `${24 + row.depth * 16}px` }">加载中…</div>
      <button v-else-if="row.expanded && row.node.error" class="status retry" :style="{ paddingLeft: `${24 + row.depth * 16}px` }" @click="emit('retry', row.node)">{{ row.node.error }}</button>
    </template>
    <div v-if="dragged.length" class="root-drop" :class="{ 'drop-target': dropTarget === '' }" @dragover="dragOver($event, null)" @drop="drop($event, null)">移到根目录</div>
  </div>
</template>
<style scoped>
.resource-tree { min-height: 100%; box-sizing: border-box; font-size: 12px; color: var(--resource-tree-text, var(--reader-text, var(--el-text-color-primary))); }
.resource-row { display: flex; align-items: center; gap: 4px; box-sizing: border-box; width: 100%; height: 24px; padding: 0 6px; border: 0; border-radius: 3px; background: transparent; color: inherit; font: inherit; text-align: left; cursor: pointer; }
.resource-row:hover { background: var(--resource-tree-hover, var(--reader-hover, var(--el-fill-color-light))); }
.resource-row[aria-selected=true] { background: var(--resource-tree-active, var(--reader-active, var(--el-color-primary-light-9))); color: var(--resource-tree-accent, var(--reader-active-text, var(--el-color-primary))); }
.resource-row:focus-visible { outline: 1px solid var(--resource-tree-accent, var(--reader-active-text, var(--el-color-primary))); outline-offset: -1px; }
.row-label { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.row-leading { display: flex; align-items: center; justify-content: center; width: 16px; height: 16px; flex: none; }
.chevron { width: 12px; height: 12px; flex: none; fill: none; stroke: currentColor; stroke-width: 1.2; opacity: .7; }
.chevron.expanded { transform: rotate(90deg); }
.status { padding: 4px 6px; font-size: 11px; color: var(--resource-tree-muted, var(--reader-muted, var(--el-text-color-secondary))); }
.retry { border: 0; background: transparent; cursor: pointer; }
.root-drop { padding: 6px; margin-bottom: 4px; border: 1px dashed var(--reader-border, var(--el-border-color)); border-radius: 3px; }
.drop-target { outline: 1px solid var(--reader-active-text, var(--el-color-primary)); outline-offset: -1px; background: var(--reader-hover, var(--el-fill-color-light)); }
.dragging,.cut { opacity: .5; }
.resource-row { user-select: none; }
.row-leading :deep(img) { pointer-events: none; }
.root-drop-target { box-shadow: inset 0 0 0 1px var(--reader-active-text, var(--el-color-primary)); }
</style>
