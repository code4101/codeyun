<script setup lang="ts">
import { inject } from 'vue'
import WorkbenchShell from '@/components/layout/WorkbenchShell.vue'
import { readerTabContext } from './readerWorkspaceContext'
import './readerPreview.css'
defineOptions({ inheritAttrs: false })
defineProps<{ headerless?: boolean }>()
const embedded = inject(readerTabContext, null)
</script>
<template>
  <section v-if="embedded" class="reader-embedded"><slot /></section>
  <WorkbenchShell v-else v-bind="$attrs" class="book-reader-page">
    <template v-if="!headerless" #top><div class="reader-heading"><slot name="header" /></div></template>
    <slot />
  </WorkbenchShell>
</template>
<style scoped>
.reader-embedded { display: flex; flex-direction: column; width: 100%; height: 100%; min-width: 0; min-height: 0; overflow: hidden; }
.book-reader-page {
  background: var(--reader-content, #fff);
}
.reader-heading { padding: 16px 16px 12px; }
.reader-heading :deep(.reader-window-heading) { display: flex; align-items: center; justify-content: space-between; min-height: 28px; gap: 16px; padding: 0; }
</style>
