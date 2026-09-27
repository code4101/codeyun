<script setup lang="ts">
import { computed } from 'vue'
import { fileType } from './fileType'
const props = defineProps<{ name: string }>()
const type = computed(() => fileType(props.name))
</script>
<template>
  <img v-if="type === 'prg'" class="resource-file-icon" src="/plugins/project-graph/icon.png" alt="" aria-hidden="true" data-file-type="prg">
  <svg v-else class="resource-file-icon" :class="type" :data-file-type="type" viewBox="0 0 20 20" aria-hidden="true">
    <template v-if="type === 'ebook'">
      <path d="M10 4C7 2 4 2 2 3v13c3-1 5-1 8 1 3-2 5-2 8-1V3c-2-1-5-1-8 1zm0 0v13" />
      <path d="m4 6 3 1m-3 3 3 1m6-4 3-1m-3 5 3-1" />
    </template>
    <template v-else-if="type === 'html'">
      <path d="m6 5-5 5 5 5m8-10 5 5-5 5M12 3 8 17" />
    </template>
    <template v-else-if="type === 'markdown'">
      <rect x="1" y="3" width="18" height="14" rx="2" /><path d="M4 13V7l3 3 3-3v6m5-6v6m-2-2 2 2 2-2" />
    </template>
    <template v-else-if="type === 'image'">
      <rect x="2" y="2" width="16" height="16" rx="1" /><circle cx="7" cy="7" r="2" /><path d="m3 16 5-5 3 2 3-5 4 8" />
    </template>
    <template v-else>
      <path d="M4 1.5h8l4 4v13H4zM12 1.5v4h4" />
      <text v-if="type === 'pdf'" x="10" y="13.5" text-anchor="middle" stroke="none" fill="currentColor" font-size="5.5" font-weight="700" font-family="sans-serif">PDF</text>
      <path v-else d="M7 8h6M7 11h6M7 14h4" />
    </template>
  </svg>
</template>
<style scoped>
.resource-file-icon { width:16px;height:16px;flex:none;object-fit:contain;fill:none;stroke:currentColor;stroke-width:1.3;stroke-linecap:round;stroke-linejoin:round;color:var(--reader-muted,#778899); }
.pdf { color:#c96c66; }.ebook { color:#63a585; }.html { color:#cb9766; }.markdown { color:#669dcd; }.image { color:#ad87c4; }
</style>
