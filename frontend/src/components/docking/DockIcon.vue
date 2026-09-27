<script setup lang="ts">
import type { DockIcon } from './dockLayout'
defineProps<{ icon: DockIcon }>()
</script>
<template>
  <svg viewBox="0 0 24 24" aria-hidden="true">
    <path v-if="icon === 'document'" d="M5 3h11l3 3v15H5zM8 8h8M8 12h8M8 16h6" />
    <template v-else-if="icon === 'search'"><circle cx="10" cy="10" r="6" /><path d="m15 15 6 6" /></template>
    <path v-else-if="icon === 'ocr'" d="M8 3H3v5M16 3h5v5M3 16v5h5M21 16v5h-5M8 8h8M12 8v9M9 17h6" />
    <path v-else-if="icon === 'outline'" d="M4 5h16M8 10h12M8 15h12M4 20h16M3 10h1M3 15h1" />
    <template v-else-if="icon === 'settings'"><circle cx="12" cy="12" r="3" /><path d="m9 3 6 0 .5 3 2 1.2 2.8-1 3 5.2-2.3 2v2.3l2.3 2-3 5.2-2.8-1-2 1.2-.5 3H9l-.5-3-2-1.2-2.8 1-3-5.2 2.3-2v-2.3l-2.3-2 3-5.2 2.8 1 2-1.2Z" transform="translate(2 0) scale(.83)" /></template>
    <template v-else><circle cx="12" cy="12" r="9" /><path d="M12 10v7M12 6v2" /></template>
  </svg>
</template>
<style scoped>svg { width: 21px; height: 21px; fill: none; stroke: currentColor; stroke-width: 1.5; flex: none; }</style>
