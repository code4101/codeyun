<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { readerTreeAncestors, readerTreeRows, type ReaderTreeItem } from './readerTree'
const props = defineProps<{ items: ReaderTreeItem[]; activeId: string; storageKey: string; expandAll?: boolean }>()
const emit = defineEmits<{ select: [id: string] }>()
const collapsed = ref(new Set<string>())
const navigation = ref<HTMLElement>()
watch(() => props.storageKey, key => {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(key) || '[]')
    collapsed.value = new Set(Array.isArray(value) ? value.filter((id): id is string => typeof id === 'string') : [])
  } catch { collapsed.value = new Set() }
}, { immediate: true })
function save() { try { localStorage.setItem(props.storageKey, JSON.stringify([...collapsed.value])) } catch { /* Session-only preference. */ } }
watch(() => [props.activeId, props.items] as const, () => {
  const next = new Set(collapsed.value)
  for (const id of readerTreeAncestors(props.items, props.activeId)) next.delete(id)
  collapsed.value = next
  save()
  void nextTick(() => navigation.value?.querySelector('[aria-current="location"]')?.scrollIntoView?.({ block: 'nearest' }))
}, { immediate: true })
const rows = computed(() => readerTreeRows(props.items, props.expandAll ? new Set() : collapsed.value))
// 纯叶子列表无需箭头槽；树形目录保留占位，使同层条目的文字对齐。
const hasBranches = computed(() => rows.value.some(item => item.hasChildren))
function toggle(id: string) {
  const next = new Set(collapsed.value)
  if (next.has(id)) next.delete(id); else next.add(id)
  collapsed.value = next; save()
}
</script>

<template>
  <nav ref="navigation" class="reader-tree" aria-label="全书目录">
    <div v-for="item in rows" :key="item.id" class="reader-tree-row" :class="{ active: item.id === activeId }" :style="{ paddingLeft: `calc(${item.depth} * var(--reader-tree-indent, 12px))` }">
      <button v-if="item.hasChildren" class="reader-tree-toggle" type="button" :aria-expanded="expandAll || !collapsed.has(item.id)" :aria-label="`${collapsed.has(item.id) ? '展开' : '收起'}${item.title}`" @click="toggle(item.id)">{{ expandAll || !collapsed.has(item.id) ? '▾' : '▸' }}</button>
      <span v-else-if="hasBranches" class="reader-tree-toggle" />
      <button type="button" class="reader-tree-target" :title="item.title" :aria-current="item.id === activeId ? 'location' : undefined" @click="emit('select', item.id)">
        <span v-if="item.number" class="reader-tree-number">{{ item.number }}</span>{{ item.title }}
      </button>
    </div>
  </nav>
</template>

<style scoped>
.reader-tree { flex: 1; min-height: 0; overflow: auto; }
.reader-tree-row { display: flex; align-items: center; min-height: 32px; border-radius: 4px; }
.reader-tree-row:hover { background: var(--reader-hover); }
.reader-tree-row.active { background: var(--reader-active); color: var(--reader-active-text); font-weight: 700; }
.reader-tree-toggle { flex: 0 0 var(--reader-tree-toggle-width, 16px); width: var(--reader-tree-toggle-width, 16px); padding: 0; }
.reader-tree-target { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; text-align: left; padding: 6px 2px; }
button { border: 0; background: transparent; color: inherit; font: inherit; font-size: 13px; cursor: pointer; }
.reader-tree-number { margin-right: .4em; color: var(--reader-muted); font-variant-numeric: tabular-nums; }
</style>
