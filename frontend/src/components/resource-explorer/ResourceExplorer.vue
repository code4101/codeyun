<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import ResourceFileIcon from './ResourceFileIcon.vue'
import { resourceRows, type ResourceNode, type ResourceRow } from './resourceTree'
const props = withDefaults(defineProps<{ nodes: ResourceNode[]; selectedId?: string; compactFolders?: boolean; label?: string }>(), { compactFolders: true, label: '资源管理器' })
const emit = defineEmits<{
  open: [node: ResourceNode]
  toggle: [nodes: ResourceNode[], expanded: boolean]
  retry: [node: ResourceNode]
  contextmenu: [event: MouseEvent, node: ResourceNode]
}>()
const rows = computed(() => resourceRows(props.nodes, props.compactFolders))
const root = ref<HTMLElement>()
const focusedId = ref('')
const focusId = computed(() => rows.value.find(row => row.id === focusedId.value)?.id ?? rows.value.find(row => row.path.some(node => node.id === props.selectedId))?.id ?? rows.value[0]?.id)
function activate(row: ResourceRow) {
  focusedId.value = row.id
  if (row.node.kind === 'file') emit('open', row.node)
  else emit('toggle', row.path, !row.expanded)
}
async function focus(id?: string) {
  if (!id) return
  focusedId.value = id
  await nextTick()
  const index = rows.value.findIndex(row => row.id === id)
  root.value?.querySelectorAll<HTMLElement>('[role="treeitem"]')[index]?.focus()
}
function keydown(event: KeyboardEvent, row: ResourceRow, index: number) {
  if (!['ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  event.stopPropagation()
  if (event.key === 'ArrowDown') void focus(rows.value[Math.min(index + 1, rows.value.length - 1)]?.id)
  else if (event.key === 'ArrowUp') void focus(rows.value[Math.max(index - 1, 0)]?.id)
  else if (event.key === 'Home') void focus(rows.value[0]?.id)
  else if (event.key === 'End') void focus(rows.value[rows.value.length - 1]?.id)
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
  <div ref="root" class="resource-tree" role="tree" :aria-label="label">
    <template v-for="(row, index) in rows" :key="row.id">
      <button class="resource-row" role="treeitem" :aria-level="row.depth + 1" :aria-expanded="row.node.kind === 'directory' ? row.expanded : undefined"
        :aria-selected="row.path.some(node => node.id === selectedId)" :tabindex="row.id === focusId ? 0 : -1" :title="row.label"
        :style="{ paddingLeft: `${4 + row.depth * 7}px` }" @focus="focusedId = row.id" @click="activate(row)" @keydown="keydown($event, row, index)"
        @contextmenu="emit('contextmenu', $event, row.node)">
        <svg class="chevron" :class="{ expanded: row.expanded, leaf: row.node.kind === 'file' }" viewBox="0 0 16 16" aria-hidden="true"><path d="m6 3 5 5-5 5" /></svg>
        <slot name="icon" :node="row.node">
          <ResourceFileIcon v-if="row.node.kind === 'file'" :name="row.node.name" />
        </slot>
        <span class="row-label"><slot name="label" :row="row">{{ row.label }}</slot></span>
      </button>
      <div v-if="row.expanded && row.node.loading" class="status" role="status" :style="{ paddingLeft: `${22 + row.depth * 7}px` }">加载中…</div>
      <button v-else-if="row.expanded && row.node.error" class="status retry" @click="emit('retry', row.node)">{{ row.node.error }}</button>
    </template>
  </div>
</template>
<style scoped>
.resource-tree { font-size: 12px; color: var(--resource-tree-text, var(--reader-text, var(--el-text-color-primary))); }
.resource-row { display: flex; align-items: center; gap: 4px; box-sizing: border-box; width: 100%; height: 24px; padding: 0 6px; border: 0; border-radius: 3px; background: transparent; color: inherit; font: inherit; text-align: left; cursor: pointer; }
.resource-row:hover { background: var(--resource-tree-hover, var(--reader-hover, var(--el-fill-color-light))); }
.resource-row[aria-selected=true] { background: var(--resource-tree-active, var(--reader-active, var(--el-color-primary-light-9))); color: var(--resource-tree-accent, var(--reader-active-text, var(--el-color-primary))); }
.resource-row:focus-visible { outline: 1px solid var(--resource-tree-accent, var(--reader-active-text, var(--el-color-primary))); outline-offset: -1px; }
.row-label { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.chevron { width: 12px; height: 12px; flex: none; fill: none; stroke: currentColor; stroke-width: 1.2; opacity: .7; }
.chevron.expanded { transform: rotate(90deg); }
.chevron.leaf { visibility: hidden; }
.status { padding: 4px 6px; font-size: 11px; color: var(--resource-tree-muted, var(--reader-muted, var(--el-text-color-secondary))); }
.retry { border: 0; background: transparent; cursor: pointer; }
</style>
