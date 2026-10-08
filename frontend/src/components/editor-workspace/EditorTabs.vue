<script setup lang="ts">
import { nextTick, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { TabRecency } from './tabRecency'
export interface EditorTab { id: string; title: string; closable?: boolean }
const props = withDefaults(defineProps<{ tabs: EditorTab[]; active: string; visible?: boolean; label?: string; evict?: (id: string) => void | Promise<unknown>; evictionPaused?: boolean }>(), { visible: true, label: '打开的资源', evictionPaused: false })
const emit = defineEmits<{ activate: [id: string]; close: [id: string]; move: [id: string, before: string]; contextmenu: [event: MouseEvent, id: string] }>()
const recency = new TabRecency()
let evicting = false
let disposed = false
onBeforeUnmount(() => { disposed = true })
/** 串行走业务关闭接口，保留保存/拒绝关闭语义；拒绝后等待下一次状态变化。 */
async function enforceLimit() {
  if (evicting || props.evictionPaused || !props.evict) return
  evicting = true
  try {
    while (!disposed && !props.evictionPaused) {
      const id = recency.victim(props.tabs, props.active)
      if (!id) break
      await props.evict(id)
      await nextTick()
      if (props.tabs.some(tab => tab.id === id)) break
    }
  } catch { /* 关闭失败由业务接口呈现；保留标签。 */ }
  finally { evicting = false }
}
watch(() => [props.tabs.map(tab => [tab.id, tab.closable]), props.active, props.evictionPaused], () => {
  recency.sync(props.tabs, props.active)
  void enforceLimit()
}, { immediate: true, flush: 'post' })
// 按字符数量截断，避免固定像素宽度提前省略中文标题。
function tabLabel(title: string) {
  const characters = Array.from(title)
  return characters.length > 50 ? `${characters.slice(0, 50).join('')}…` : title
}
const dragged = ref('')
const tabsRoot = ref<HTMLElement>()
async function revealActiveTab() {
  const retainTabFocus = Boolean(document.activeElement?.closest('[role="tablist"]'))
  await nextTick()
  if (!props.visible) return
  const selected = tabsRoot.value?.querySelector<HTMLElement>('[aria-selected="true"]')
  selected?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  if (retainTabFocus) selected?.focus({ preventScroll: true })
}
watch(() => [props.active, props.visible], revealActiveTab)
onMounted(revealActiveTab)
function activate(key: string) { recency.touch(key); emit('activate', key) }
function close(key: string) { if (props.tabs.find(tab => tab.id === key)?.closable !== false) emit('close', key) }
function tabKeydown(event: KeyboardEvent, index: number) {
  const tabs = props.tabs
  let target = index
  if (event.key === 'ArrowRight') target = (index + 1) % tabs.length
  else if (event.key === 'ArrowLeft') target = (index + tabs.length - 1) % tabs.length
  else if (event.key === 'Home') target = 0
  else if (event.key === 'End') target = tabs.length - 1
  else if (event.key === 'Delete') { event.preventDefault(); close(tabs[index]!.id); return }
  else return
  event.preventDefault()
  event.stopPropagation()
  activate(tabs[target]!.id)
  ;(tabsRoot.value?.querySelectorAll<HTMLElement>('[role="tab"]')[target])?.focus()
}
</script>
<template>
    <div class="editor-tab-strip">
      <div ref="tabsRoot" class="editor-tabs" role="tablist" :aria-label="label">
        <div v-for="(tab, index) in tabs" :key="tab.id" class="editor-tab" :class="{ selected: tab.id === active }"
          draggable="true" @dragstart="dragged = tab.id" @dragend="dragged = ''" @dragover.prevent @drop.prevent="dragged && emit('move', dragged, tab.id)"
          @auxclick.middle.prevent="close(tab.id)" @contextmenu="emit('contextmenu', $event, tab.id)">
          <button role="tab" :aria-selected="tab.id === active" :tabindex="tab.id === active ? 0 : -1"
            :title="tab.title" :aria-label="tab.title" @click="activate(tab.id)" @keydown="tabKeydown($event, index)">{{ tabLabel(tab.title) }}</button>
          <button v-if="tab.closable !== false" class="close-tab" :aria-label="`关闭 ${tab.title}`" @click="close(tab.id)">×</button>
        </div>
      </div>
      <slot name="actions" />
    </div>
</template>
<style scoped>
.editor-tab-strip { display: flex; align-items: flex-start; flex: none; min-width: 0; background: var(--reader-panel); }
/* All workspace views share wrapping tabs; a single long title fits the available row. */
.editor-tabs { display: flex; flex-wrap: wrap; flex: 1; min-width: 0; background: var(--reader-panel); border-bottom: 1px solid var(--reader-border); }
.editor-tab { display: flex; flex: none; min-width: 0; max-width: 100%; box-sizing: border-box; border-right: 1px solid var(--reader-border); border-top: 2px solid transparent; }
.editor-tab.selected { background: var(--reader-content); border-top-color: var(--reader-active-text); }
.editor-tab button { background: transparent; color: var(--reader-text); border: 0; cursor: pointer; font: inherit; font-size: 12px; }
.editor-tab [role=tab] { padding: 9px 10px; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.editor-tab .close-tab { flex: none; padding: 0 8px; font-size: 17px; }
.editor-tab button:hover { background: var(--reader-hover); }
.editor-tab button:focus { outline: none; }
.editor-tab button:focus-visible { outline: 2px solid var(--reader-active-text); outline-offset: -2px; }
</style>
