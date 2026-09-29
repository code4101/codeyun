<script setup lang="ts">
import { nextTick, ref } from 'vue'
import ProjectGraphEditor from './ProjectGraphEditor.vue'
import type { createGraphLibrary, GraphDocument, GraphStorage } from './storage'
import type { WorkspaceMenuItem } from '@/components/editor-workspace/workspaceMenu'
const props = defineProps<{ day: string; library: ReturnType<typeof createGraphLibrary>; detailsActive: boolean }>()
const emit = defineEmits<{ saved: []; error: [message: string]; focus: []; hints: [value: { keys: string[]; items: { displayKey: string; title: string }[]; page: string }]; mode: [value: { mode: string; readOnly: boolean; color: number[] }]; menu: [items: WorkspaceMenuItem[]]; command: [command: string]; details: [value: { id: string; title: string; value: unknown[] } | null]; auxiliary: [value: { tabs: { id: string; title: string }[]; active: string }] }>()
const editor = ref<InstanceType<typeof ProjectGraphEditor>>()
const document = ref<GraphDocument>()
const ready = props.library.openJournal(props.day).then(value => {
  document.value = value ?? { id: `journal:${props.day}`, title: props.day, journalDate: props.day, bytes: new Uint8Array(), revision: 0, updatedAt: 0 }
}).catch(error => { emit('error', String(error)) })
// Each pane owns its temporary identity and revision. First save never remounts it.
const storage: GraphStorage = {
  ...props.library.storage,
  async read(id) { return id.startsWith('journal:') ? document.value : props.library.storage.read(id) },
  async write(id, title, bytes, revision) {
    if (!id.startsWith('journal:')) return props.library.storage.write(id, title, bytes, revision)
    const saved = await props.library.saveJournal(props.day, bytes)
    if (saved) document.value = saved
    return saved ?? { ...document.value!, bytes }
  },
}
async function flush() { await ready; await nextTick(); if (!document.value || !editor.value) throw new Error('每日记录尚未加载'); await editor.value?.flush() }
defineExpose({ requestMode: () => editor.value?.requestMode(), setMode: (mode: string, color?: number[]) => editor.value?.setMode(mode, color), flush, read: async () => { await flush(); return document.value?.id.startsWith('journal:') ? document.value : props.library.storage.read(document.value!.id) },
  refreshMenu: () => editor.value?.refreshMenu(), executeMenu: (id: string) => editor.value?.executeMenu(id),
  exportDocument: () => editor.value?.exportDocument(), updateDetails: (id: string, value: unknown[]) => editor.value?.updateDetails(id, value),
  focusAuxiliary: (id: string) => editor.value?.focusAuxiliary(id), closeAuxiliary: (id: string) => editor.value?.closeAuxiliary(id) })
</script>
<template>
  <ProjectGraphEditor v-if="document" ref="editor" shared-toolbar :document-id="document.id" :title="document.title" :storage="storage" :view-state-key="`codeyun.project-graph.view:${library.ownerId}:${document.id}`" :details-active="detailsActive"
    @hints="emit('hints', $event)" @mode="emit('mode', $event)" @saved="emit('saved')" @error="emit('error', $event)" @focus="emit('focus')" @menu="emit('menu', $event)" @command="emit('command', $event)" @details="emit('details', $event)" @auxiliary="emit('auxiliary', $event)" />
  <div v-else class="day-loading">正在加载…</div>
</template>
<style scoped>.day-loading{display:grid;place-items:center;flex:1;color:var(--reader-muted)}</style>
