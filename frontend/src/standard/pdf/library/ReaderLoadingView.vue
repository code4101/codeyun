<script setup lang="ts">
import ReaderDockLayout from './ReaderDockLayout.vue'
import ReaderStatus from './ReaderStatus.vue'
import ReaderSettingsPanel from './ReaderSettingsPanel.vue'
import { useReaderDock } from './readerWorkspaceContext'
import { bookDockTools, pdfDockTools, skillDockTools } from './readerDockTools'
import type { ReaderTab } from './readerWorkspaceState'
const props = defineProps<{ kind: ReaderTab['kind'] }>()
const dock = useReaderDock('', props.kind === 'pdf' ? pdfDockTools : props.kind === 'skill' ? skillDockTools : bookDockTools)
</script>
<template>
  <ReaderDockLayout :dock="dock">
    <template #settings><ReaderSettingsPanel /></template>
    <ReaderStatus message="正在加载阅读器…" />
  </ReaderDockLayout>
</template>
