<script setup lang="ts">
import { onMounted } from 'vue'
import { useReaderWorkspace } from './useReaderWorkspace'
const workspace = useReaderWorkspace()
import LibraryTreeBranch from './LibraryTreeBranch.vue'
import { useLibraryTree } from './useLibraryTree'
const tree = useLibraryTree()
onMounted(() => tree.load())
</script>
<template>
  <div class="library-tree-panel">
    <button class="open-bookshelf" @click="workspace.openShelf()">打开书架 ↗</button>
    <p v-if="tree.error" role="alert">{{ tree.error }}</p>
    <LibraryTreeBranch :nodes="tree.nodes" />
  </div>
</template>
<style scoped>
.library-tree-panel { flex: none; padding: 4px; font-size: 12px; }
.open-bookshelf { width: 100%; padding: 7px 10px; margin-bottom: 4px; text-align: left; border: 1px solid var(--reader-border); border-radius: 4px; background: var(--reader-panel); color: var(--reader-text); cursor: pointer; }
.open-bookshelf:hover { background: var(--reader-hover); }
/* 列表按内容自然展开；唯一滚动视口是停靠框架的 dock-tool-content。 */
</style>
