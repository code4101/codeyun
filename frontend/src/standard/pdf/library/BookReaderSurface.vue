<script setup lang="ts">
import { inject } from 'vue'
import { readerTabContext } from './readerWorkspaceContext'
import './readerPreview.css'
defineOptions({ inheritAttrs: false })
defineProps<{ headerless?: boolean }>()
const embedded = inject(readerTabContext, null)
</script>
<template>
  <section v-if="embedded" class="reader-embedded"><slot /></section>
  <section v-else v-bind="$attrs" class="book-reader-page">
    <header v-if="!headerless"><slot name="header" /></header>
    <main class="book-reader-body"><slot /></main>
  </section>
</template>
<style scoped>
.reader-embedded { display: flex; flex-direction: column; width: 100%; height: 100%; min-width: 0; min-height: 0; overflow: hidden; }
.book-reader-page {
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
  width: 100% !important;
  height: 100% !important;
  min-width: 0 !important;
  min-height: 0 !important;
  max-width: none !important;
  max-height: none !important;
  padding: 0;
  resize: none !important;
  overflow: hidden;
  background: var(--reader-content, #fff);
}
.book-reader-page::after { display: none; }
.book-reader-page > header { flex: none; padding: 16px 16px 12px; }
.book-reader-page > header :deep(.reader-window-heading) { display: flex; align-items: center; justify-content: space-between; min-height: 28px; gap: 16px; padding: 0; }
.book-reader-page > main { flex: 1; min-height: 0; overflow: hidden; padding: 0; }
</style>
