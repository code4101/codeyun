<script setup lang="ts">
import { onMounted } from 'vue'
import LibraryTreeBranch from './LibraryTreeBranch.vue'
import { useLibraryTree } from './useLibraryTree'
const tree = useLibraryTree()
onMounted(() => tree.load())
</script>
<template>
  <div class="library-tree-panel">
    <p v-if="tree.error" role="alert">{{ tree.error }}</p>
    <LibraryTreeBranch :nodes="tree.nodes" />
  </div>
</template>
<style scoped>
.library-tree-panel { flex: none; padding: 4px; font-size: 12px; }
/* 列表按内容自然展开；唯一滚动视口是停靠框架的 dock-tool-content。 */
</style>
