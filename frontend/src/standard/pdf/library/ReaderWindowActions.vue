<script setup lang="ts">
import { inject } from 'vue'
import { readerSurfaceContext } from './readerWorkspaceContext'
const surface = inject(readerSurfaceContext, null)
</script>
<template>
  <div v-if="surface && !surface.standalone.value" class="reader-window-actions">
    <a :href="surface.pageHref.value" target="_blank" rel="noopener" title="独立页面打开" aria-label="独立页面打开">↗</a>
    <button title="关闭阅读工作区" aria-label="关闭阅读工作区" @click="surface.close()">×</button>
  </div>
</template>
<style scoped>
.reader-window-actions { display: flex; flex: none; margin-left: auto; align-items: center; background: var(--reader-panel); }
a, button { display: grid; place-items: center; width: 32px; height: 32px; border: 0; background: transparent; color: var(--reader-muted); text-decoration: none; font-size: 18px; cursor: pointer; }
a:hover, button:hover { background: var(--reader-hover); color: var(--reader-text); }
</style>
