<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'
export interface EditorTab { id: string; title: string }
const props = withDefaults(defineProps<{ tabs: EditorTab[]; active: string; visible?: boolean; label?: string }>(), { visible: true, label: '打开的资源' })
const emit = defineEmits<{ activate: [id: string]; close: [id: string]; move: [id: string, before: string] }>()
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
function activate(key: string) { emit('activate', key) }
function close(key: string) { emit('close', key) }
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
          @auxclick.middle.prevent="close(tab.id)">
          <button role="tab" :aria-selected="tab.id === active" :tabindex="tab.id === active ? 0 : -1"
            :title="tab.title" @click="activate(tab.id)" @keydown="tabKeydown($event, index)">{{ tab.title }}</button>
          <button class="close-tab" :aria-label="`关闭 ${tab.title}`" @click="close(tab.id)">×</button>
        </div>
      </div>
      <slot name="actions" />
    </div>
</template>
<style scoped>
.editor-tab-strip { display: flex; flex: none; min-width: 0; background: var(--reader-panel); }
.editor-tabs { display: flex; flex: 1; min-width: 0; overflow-x: auto; background: var(--reader-panel); border-bottom: 1px solid var(--reader-border); }
.editor-tab { display: flex; flex: none; border-right: 1px solid var(--reader-border); border-top: 2px solid transparent; }
.editor-tab.selected { background: var(--reader-content); border-top-color: var(--reader-active-text); }
.editor-tab button { background: transparent; color: var(--reader-text); border: 0; cursor: pointer; font: inherit; font-size: 12px; }
.editor-tab [role=tab] { padding: 9px 10px; max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.editor-tab .close-tab { padding: 0 8px; font-size: 17px; }
.editor-tab button:hover { background: var(--reader-hover); }
</style>
