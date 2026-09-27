<script setup lang="ts">
import { inject, nextTick, onMounted, ref, watch } from 'vue'
import ReaderWindowActions from './ReaderWindowActions.vue'
import { readerFileTitle } from './readerFileTitle'
import { readerTabKey } from './readerWorkspaceState'
import { useReaderWorkspace } from './useReaderWorkspace'
import { readerTabContext } from './readerWorkspaceContext'
const workspace = useReaderWorkspace()
const workspaceTab = inject(readerTabContext, null)
const dragged = ref('')
const tabsRoot = ref<HTMLElement>()
async function revealActiveTab() {
  const retainTabFocus = Boolean(document.activeElement?.closest('[role="tablist"]'))
  await nextTick()
  if (workspaceTab && !workspaceTab.active.value) return
  const selected = tabsRoot.value?.querySelector<HTMLElement>('[aria-selected="true"]')
  selected?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  if (retainTabFocus) selected?.focus({ preventScroll: true })
}
watch(() => [workspace.state.active, workspaceTab?.active.value], revealActiveTab)
onMounted(revealActiveTab)
function activate(key: string) { void workspace.command({ action: 'activate', key }).catch(() => undefined) }
function close(key: string) { void workspace.command({ action: 'close', key }).catch(() => undefined) }
function tabKeydown(event: KeyboardEvent, index: number) {
  const tabs = workspace.state.tabs
  let target = index
  if (event.key === 'ArrowRight') target = (index + 1) % tabs.length
  else if (event.key === 'ArrowLeft') target = (index + tabs.length - 1) % tabs.length
  else if (event.key === 'Home') target = 0
  else if (event.key === 'End') target = tabs.length - 1
  else if (event.key === 'Delete') { event.preventDefault(); close(readerTabKey(tabs[index]!)); return }
  else return
  event.preventDefault()
  event.stopPropagation()
  activate(readerTabKey(tabs[target]!))
  ;(tabsRoot.value?.querySelectorAll<HTMLElement>('[role="tab"]')[target])?.focus()
}
</script>
<template>
    <div class="reader-tab-strip">
      <div ref="tabsRoot" class="reader-tabs" role="tablist" aria-label="打开的图书">
        <div v-for="(tab, index) in workspace.state.tabs" :key="readerTabKey(tab)" class="reader-tab" :class="{ selected: readerTabKey(tab) === workspace.state.active }"
          draggable="true" @dragstart="dragged = readerTabKey(tab)" @dragend="dragged = ''" @dragover.prevent @drop.prevent="dragged && workspace.command({ action: 'move', key: dragged, before: readerTabKey(tab) }).catch(() => undefined)"
          @auxclick.middle.prevent="close(readerTabKey(tab))">
          <button role="tab" :aria-selected="readerTabKey(tab) === workspace.state.active" :tabindex="readerTabKey(tab) === workspace.state.active ? 0 : -1"
            :title="readerFileTitle(tab.title || tab.id, '', tab.kind === 'pdf' ? 'pdf' : '')" @click="activate(readerTabKey(tab))" @keydown="tabKeydown($event, index)">{{ readerFileTitle(tab.title || tab.id, '', tab.kind === 'pdf' ? 'pdf' : '') }}</button>
          <button class="close-tab" :aria-label="`关闭 ${readerFileTitle(tab.title || tab.id, '', tab.kind === 'pdf' ? 'pdf' : '')}`" @click="close(readerTabKey(tab))">×</button>
        </div>
      </div>
      <ReaderWindowActions />
    </div>
</template>
<style scoped>
.reader-tab-strip { display: flex; flex: none; min-width: 0; background: var(--reader-panel); }
.reader-tabs { display: flex; flex: 1; min-width: 0; overflow-x: auto; background: var(--reader-panel); border-bottom: 1px solid var(--reader-border); }
.reader-tab { display: flex; flex: none; border-right: 1px solid var(--reader-border); border-top: 2px solid transparent; }
.reader-tab.selected { background: var(--reader-content); border-top-color: var(--reader-active-text); }
.reader-tab button { background: transparent; color: var(--reader-text); border: 0; cursor: pointer; font: inherit; font-size: 12px; }
.reader-tab [role=tab] { padding: 9px 10px; max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.reader-tab .close-tab { padding: 0 8px; font-size: 17px; }
.reader-tab button:hover { background: var(--reader-hover); }
</style>
