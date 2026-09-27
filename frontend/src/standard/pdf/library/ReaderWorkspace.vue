<script setup lang="ts">
import { computed, provide, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import BookReaderSurface from './BookReaderSurface.vue'
import ReaderTabHost from './ReaderTabHost.vue'
import ReaderDockLayout from './ReaderDockLayout.vue'
import ReaderSettingsPanel from './ReaderSettingsPanel.vue'
import ReaderContextMenu from './ReaderContextMenu.vue'
import ReaderWindowActions from './ReaderWindowActions.vue'
import { readerSurfaceContext } from './readerWorkspaceContext'
import { useReaderWorkspace } from './useReaderWorkspace'
import { readerLocation } from './bookReaderRoute'
import { readerTabKey } from './readerWorkspaceState'
import { useReaderInstances } from './useReaderInstances'
import { libraryReaderThemeClass } from './readerTheme'

const props = defineProps<{ standalone?: boolean }>()
const workspace = useReaderWorkspace()
const router = useRouter()
const contextMenu = ref<InstanceType<typeof ReaderContextMenu>>()
const shown = computed(() => Boolean(props.standalone || workspace.visible))
const mounted = useReaderInstances(computed(() => workspace.state.tabs.map(readerTabKey)), computed(() => workspace.state.active), shown)
const activeTab = computed(() => workspace.state.tabs.find(tab => readerTabKey(tab) === workspace.state.active))
const pageHref = computed(() => router.resolve(readerLocation(activeTab.value)).href)
provide(readerSurfaceContext, { standalone: computed(() => Boolean(props.standalone)), pageHref, close: () => { workspace.visible = false } })
watch(() => activeTab.value?.title, title => { if (props.standalone) document.title = `${title || '阅读工作区'} · CodeYun` })
function refresh() { if (shown.value) void workspace.refresh().catch(() => undefined) }
onMounted(() => { void workspace.initialize().catch(() => undefined); window.addEventListener('focus', refresh) })
onBeforeUnmount(() => window.removeEventListener('focus', refresh))
</script>
<template>
  <BookReaderSurface headerless :standalone="standalone" :model-value="shown" @update:model-value="workspace.visible = $event" :page-href="pageHref"
    class="reader-workspace library-reader-theme-dialog" :class="libraryReaderThemeClass" width="calc(100vw - 64px)" append-to-body :close-on-click-modal="false" :close-on-press-escape="false">
    <div class="workspace-body">
      <div class="workspace-readers">
        <template v-for="tab in workspace.state.tabs" :key="readerTabKey(tab)">
          <ReaderTabHost v-if="mounted.has(readerTabKey(tab))" v-show="readerTabKey(tab) === workspace.state.active" :tab="tab" :active="shown && readerTabKey(tab) === workspace.state.active" />
        </template>
        <ReaderDockLayout v-if="!workspace.state.tabs.length" :dock="workspace.dock" @context-menu="contextMenu?.open($event)">
          <template #settings><ReaderSettingsPanel /></template>
          <ReaderWindowActions />
          <div class="workspace-empty">从左侧图书馆选择一本书开始阅读。</div>
        </ReaderDockLayout>
      </div>
    </div>
    <ReaderContextMenu ref="contextMenu" :dock="workspace.dock" />
  </BookReaderSurface>
</template>
<style scoped>
.workspace-body { display: flex; flex-direction: column; height: 100%; min-height: 0; }
.workspace-readers { display: flex; flex-direction: column; flex: 1; min-height: 0; overflow: hidden; }
.workspace-readers > .reader-tab-host { flex: 1; }
.workspace-empty { padding: 32px; color: var(--reader-muted); }
</style>
