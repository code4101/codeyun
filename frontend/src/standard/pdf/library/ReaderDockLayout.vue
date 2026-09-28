<script setup lang="ts">
import DockWorkspace from '@/components/docking/DockWorkspace.vue'
import type { DockController } from '@/components/docking/useDockLayout'
import ReaderTabs from './ReaderTabs.vue'
import { computed, inject, onBeforeUnmount, shallowRef, useSlots, watchPostEffect } from 'vue'
import { readerCentralViewContext, readerTabContext } from './readerWorkspaceContext'
import { useReaderWorkspace } from './useReaderWorkspace'
const workspaceTab = inject(readerTabContext, null)
const centralView = inject(readerCentralViewContext, null)
const workspace = useReaderWorkspace()
const slots = useSlots()
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
// 设置由菜单打开为中央标签；复用阅读器提供的配置面板，保留各阅读器的上下文配置项。
const showSettings = computed(() => (workspaceTab !== null || centralView !== null)
  && (workspaceTab?.active.value ?? true)
  && Boolean(slots.settings)
  && workspace.settingsActive)
</script>
<template>
  <DockWorkspace :dock="dock" @resized="emit('resized')" @context-menu="emit('contextMenu', $event)">
    <template v-for="name in Object.keys($slots).filter(name => name !== 'default' && name !== 'settings')" #[name]="scope"><slot :name="name" v-bind="scope ?? {}" /></template>
    <template #default>
      <ReaderTabs v-if="workspaceTab || centralView" />
      <div v-if="centralView" ref="shelfTarget" v-show="centralView.shelfActive.value" class="reader-functional-content" />
      <div v-show="showSettings" class="reader-settings-content"><slot name="settings" /></div>
      <template v-if="centralView">
        <div v-show="!centralView.shelfActive.value && !showSettings" class="reader-document-content"><slot /></div>
      </template>
      <slot v-else />
    </template>
  </DockWorkspace>
</template>

<style scoped>
.reader-functional-content { display: flex; flex: 1; min-width: 0; min-height: 0; overflow: hidden; }
.reader-settings-content { display: flex; flex: 1; min-width: 0; min-height: 0; overflow: hidden; }
/* 保留原正文子项参与中央 flex 布局；切功能页时只隐藏正文，不重建工具。 */
.reader-document-content { display: contents; }
</style>
