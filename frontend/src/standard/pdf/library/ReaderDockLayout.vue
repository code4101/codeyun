<script setup lang="ts">
import DockWorkspace from '@/components/docking/DockWorkspace.vue'
import type { DockController } from '@/components/docking/useDockLayout'
import LibraryTreePanel from './LibraryTreePanel.vue'
import ReaderTabs from './ReaderTabs.vue'
import { inject } from 'vue'
import { readerTabContext } from './readerWorkspaceContext'
const workspaceTab = inject(readerTabContext, null)
defineProps<{ dock: DockController }>()
const emit = defineEmits<{ resized: []; contextMenu: [event: MouseEvent] }>()
</script>
<template>
  <DockWorkspace :dock="dock" @resized="emit('resized')" @context-menu="emit('contextMenu', $event)">
    <template v-for="name in Object.keys($slots).filter(name => name !== 'default' && name !== 'library')" #[name]="scope"><slot :name="name" v-bind="scope ?? {}" /></template>
    <template #default>
      <ReaderTabs v-if="workspaceTab" />
      <slot />
    </template>
    <template #library><LibraryTreePanel /></template>
  </DockWorkspace>
</template>
