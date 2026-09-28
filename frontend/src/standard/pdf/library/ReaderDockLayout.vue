<script setup lang="ts">
import DockWorkspace from '@/components/docking/DockWorkspace.vue'
import type { DockController } from '@/components/docking/useDockLayout'
import ReaderTabs from './ReaderTabs.vue'
import { inject, onBeforeUnmount, shallowRef, watchPostEffect } from 'vue'
import { readerCentralViewContext, readerTabContext } from './readerWorkspaceContext'
const workspaceTab = inject(readerTabContext, null)
const centralView = inject(readerCentralViewContext, null)
const shelfTarget = shallowRef<HTMLElement | null>(null)
watchPostEffect(() => {
  if (centralView && (workspaceTab?.active.value ?? true) && shelfTarget.value) {
    centralView.target.value = shelfTarget.value
    centralView.dock.value = props.dock
  }
})
onBeforeUnmount(() => {
  if (centralView && centralView.target.value === shelfTarget.value) {
    centralView.target.value = null
    centralView.dock.value = null
  }
})
const props = defineProps<{ dock: DockController }>()
const emit = defineEmits<{ resized: []; contextMenu: [event: MouseEvent] }>()
</script>
<template>
  <DockWorkspace :dock="dock" @resized="emit('resized')" @context-menu="emit('contextMenu', $event)">
    <template v-for="name in Object.keys($slots).filter(name => name !== 'default')" #[name]="scope"><slot :name="name" v-bind="scope ?? {}" /></template>
    <template #default>
      <ReaderTabs v-if="workspaceTab || centralView" />
      <div v-if="centralView" ref="shelfTarget" v-show="centralView.shelfActive.value" class="reader-functional-content" />
      <div v-if="centralView" v-show="!centralView.shelfActive.value" class="reader-document-content"><slot /></div>
      <slot v-else />
    </template>
  </DockWorkspace>
</template>

<style scoped>
.reader-functional-content { display: flex; flex: 1; min-width: 0; min-height: 0; overflow: hidden; }
/* 保留原正文子项参与中央 flex 布局；切功能页时只隐藏正文，不重建工具。 */
.reader-document-content { display: contents; }
</style>
