<script setup lang="ts">
defineProps<{ items: readonly { id: string; title: string; icon: 'document' | 'search' | 'info' | 'ocr' }[]; activeId: string }>()
defineEmits<{ select: [id: string] }>()
</script>

<template>
  <nav class="reader-activity-bar" aria-label="阅读活动栏">
    <button v-for="item in items" :key="item.id" type="button" :title="item.title" :aria-label="item.title" :aria-pressed="activeId === item.id" @click="$emit('select', item.id)">
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path v-if="item.icon === 'document'" d="M5 3h11l3 3v15H5zM8 8h8M8 12h8M8 16h6" />
        <template v-else-if="item.icon === 'search'"><circle cx="10" cy="10" r="6" /><path d="m15 15 6 6" /></template>
        <path v-else-if="item.icon === 'ocr'" d="M8 3H3v5M16 3h5v5M3 16v5h5M21 16v5h-5M8 8h8M12 8v9M9 17h6" />
        <template v-else><circle cx="12" cy="12" r="9" /><path d="M12 10v7M12 6v2" /></template>
      </svg>
    </button>
  </nav>
</template>

<style scoped>
.reader-activity-bar { width: 44px; background: var(--reader-panel, #f7f9fb); border-right: 1px solid var(--reader-border, #e4e9ef); }
button { display: grid; place-items: center; width: 44px; height: 44px; border: 0; border-left: 2px solid transparent; background: transparent; color: var(--reader-muted, #657286); cursor: pointer; }
button:hover { background: var(--reader-hover, #edf2f7); }
button[aria-pressed=true] { border-left-color: var(--reader-active-text, #2368d1); color: var(--reader-active-text, #2368d1); }
button:focus-visible { outline: 2px solid var(--reader-link, #2368d1); outline-offset: -3px; }
svg { width: 23px; height: 23px; fill: none; stroke: currentColor; stroke-width: 1.5; }
@media (max-width: 980px) {
  .reader-activity-bar { display: flex; width: 100%; border-right: 0; border-bottom: 1px solid var(--reader-border, #e4e9ef); }
}
</style>
