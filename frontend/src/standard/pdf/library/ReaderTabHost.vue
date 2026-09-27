<script setup lang="ts">
import { computed, onErrorCaptured, provide, ref } from 'vue'
import { readerPlugins } from './readerPlugins'
import { readerTabContext } from './readerWorkspaceContext'
import { readerTabKey, type ReaderTab } from './readerWorkspaceState'
import { useReaderWorkspace } from './useReaderWorkspace'
import { useUserStore } from '@/store/userStore'
import ReaderDockLayout from './ReaderDockLayout.vue'
const props = defineProps<{ tab: ReaderTab; active: boolean }>()
const workspace = useReaderWorkspace()
const user = useUserStore()
const owner = user.user?.id
const error = ref('')
const attempt = ref(0)
const plugin = computed(() => readerPlugins[props.tab.kind])
provide(readerTabContext, {
  active: computed(() => props.active), dock: workspace.dock,
  canPersist: () => user.user?.id === owner,
  title(value) {
    if (user.user?.id === owner && value && value !== props.tab.title) void workspace.command({ action: 'title', key: readerTabKey(props.tab), title: value }).catch(() => undefined)
  },
})
onErrorCaptured(cause => { error.value = cause instanceof Error ? cause.message : '阅读器加载失败'; return false })
</script>
<template>
  <div class="reader-tab-host">
    <ReaderDockLayout v-if="error" :dock="workspace.dock">
      <div class="reader-tab-error" role="alert">{{ error }} <button @click="error = ''; attempt++">重试</button></div>
    </ReaderDockLayout>
    <component v-else :is="plugin.component" :key="attempt" v-bind="plugin.props(tab)" />
  </div>
</template>
<style scoped>
.reader-tab-host { height: 100%; min-height: 0; min-width: 0; display: flex; flex-direction: column; overflow: hidden; }
.reader-tab-error { padding: 24px; }
</style>
