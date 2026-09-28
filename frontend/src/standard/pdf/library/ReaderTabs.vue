<script setup lang="ts">
import { computed, inject } from 'vue'
import EditorTabs from '@/components/editor-workspace/EditorTabs.vue'
import { readerFileTitle } from './readerFileTitle'
import { readerTabKey } from './readerWorkspaceState'
import { useReaderWorkspace } from './useReaderWorkspace'
import { readerTabContext } from './readerWorkspaceContext'
const props = defineProps<{ visible?: boolean }>()
const workspace = useReaderWorkspace()
const current = inject(readerTabContext, null)
const resourceTabs = computed(() => workspace.state.tabs.map(tab => ({ id: readerTabKey(tab), title: readerFileTitle(tab.title || tab.id, '', tab.kind === 'pdf' ? 'pdf' : '') })))
const tabs = computed(() => [...(workspace.shelfOpen ? [{ id: 'view:bookshelf', title: '书架' }] : []), ...resourceTabs.value])
</script>
<template>
  <EditorTabs :tabs="tabs" :active="workspace.shelfActive ? 'view:bookshelf' : workspace.state.active" :visible="props.visible ?? current?.active.value ?? true" label="工作区视图"
    @activate="key => workspace.activateTab(key)" @close="key => workspace.closeTab(key)"
    @move="(key, before) => key !== 'view:bookshelf' && workspace.command({ action: 'move', key, before: before === 'view:bookshelf' ? workspace.state.tabs[0] && readerTabKey(workspace.state.tabs[0]) : before })">
  </EditorTabs>
</template>
