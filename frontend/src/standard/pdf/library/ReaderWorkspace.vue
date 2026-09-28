<script setup lang="ts">
import { computed, defineAsyncComponent, provide, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { useRouter } from 'vue-router'
import BookReaderSurface from './BookReaderSurface.vue'
import ReaderTabHost from './ReaderTabHost.vue'
import ReaderDockLayout from './ReaderDockLayout.vue'
import ReaderSettingsPanel from './ReaderSettingsPanel.vue'
import ReaderContextMenu from './ReaderContextMenu.vue'
import ReaderWorkspaceMenu from './ReaderWorkspaceMenu.vue'
const BookshelfView = defineAsyncComponent(() => import('./BookshelfView.vue').then(module => module.default))
import { readerCentralViewContext, readerSurfaceContext } from './readerWorkspaceContext'
import { useReaderWorkspace } from './useReaderWorkspace'
import { readerLocation } from './bookReaderRoute'
import { readerTabKey } from './readerWorkspaceState'
import { useReaderInstances } from './useReaderInstances'
import { libraryReaderThemeClass } from './readerTheme'
import type { DockController } from '@/components/docking/useDockLayout'

const props = defineProps<{ standalone?: boolean }>()
const workspace = useReaderWorkspace()
const router = useRouter()
const contextMenu = ref<InstanceType<typeof ReaderContextMenu>>()
const shown = computed(() => Boolean(props.standalone || workspace.visible))
const mounted = useReaderInstances(computed(() => workspace.state.tabs.map(readerTabKey)), computed(() => workspace.state.active), shown)
const activeTab = computed(() => workspace.state.tabs.find(tab => readerTabKey(tab) === workspace.state.active))
const shelfTarget = shallowRef<HTMLElement | null>(null)
const activeDock = shallowRef<DockController | null>(null)
provide(readerCentralViewContext, { shelfActive: computed(() => workspace.shelfActive), target: shelfTarget, dock: activeDock })
const pageHref = computed(() => router.resolve(workspace.shelfActive ? { name: 'ReaderWorkspace', query: { view: 'bookshelf' } } : readerLocation(activeTab.value)).href)
provide(readerSurfaceContext, { standalone: computed(() => Boolean(props.standalone)), pageHref, close: () => { workspace.visible = false } })
watch(() => workspace.shelfActive ? '书架' : activeTab.value?.title, title => { if (props.standalone) document.title = `${title || '阅读工作区'} · CodeYun` })
function refresh() { if (shown.value) void workspace.refresh().catch(() => undefined) }
onMounted(() => { void workspace.initialize().catch(() => undefined); window.addEventListener('focus', refresh) })
onBeforeUnmount(() => window.removeEventListener('focus', refresh))
</script>
<template>
  <BookReaderSurface headerless :standalone="standalone" :model-value="shown" @update:model-value="workspace.visible = $event" :page-href="pageHref"
    class="reader-workspace library-reader-theme-dialog" :class="libraryReaderThemeClass" width="calc(100vw - 64px)" append-to-body :close-on-click-modal="false" :close-on-press-escape="false">
    <div class="workspace-body">
      <ReaderWorkspaceMenu />
      <div class="workspace-readers">
        <Teleport v-if="workspace.shelfOpen" :to="shelfTarget" :disabled="!shelfTarget">
          <div v-show="workspace.shelfActive && shelfTarget" class="workspace-shelf">
            <div class="workspace-shelf-content"><BookshelfView /></div>
          </div>
        </Teleport>
        <template v-for="tab in workspace.state.tabs" :key="readerTabKey(tab)">
          <ReaderTabHost v-if="mounted.has(readerTabKey(tab))" v-show="readerTabKey(tab) === workspace.state.active" :tab="tab" :active="shown && readerTabKey(tab) === workspace.state.active" />
        </template>
        <ReaderDockLayout v-if="!workspace.state.tabs.length" :dock="workspace.dock" @context-menu="contextMenu?.open($event)">
          <template #settings><ReaderSettingsPanel /></template>
          <button class="empty-open-shelf" @click="workspace.openShelf()">打开书架</button>
          <div class="workspace-empty">打开书架，选择一本书开始阅读。</div>
        </ReaderDockLayout>
      </div>
    </div>
    <ReaderContextMenu ref="contextMenu" />
  </BookReaderSurface>
</template>
<style scoped>
.workspace-shelf { display: flex; flex-direction: column; flex: 1; min-height: 0; overflow: hidden; }
.workspace-shelf-content { flex: 1; min-height: 0; overflow: auto; }
.empty-open-shelf { align-self: flex-start; margin: 24px 32px 0; cursor: pointer; }
.workspace-body { display: flex; flex-direction: column; height: 100%; min-height: 0; }
.workspace-readers { display: flex; flex-direction: column; flex: 1; min-height: 0; overflow: hidden; }
.workspace-readers > .reader-tab-host { flex: 1; }
.workspace-empty { padding: 32px; color: var(--reader-muted); }
</style>
