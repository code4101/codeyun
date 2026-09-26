<script setup lang="ts">
withDefaults(defineProps<{ tocVisible: boolean; outlineVisible?: boolean; hasOutline?: boolean }>(), { outlineVisible: false, hasOutline: true })
defineEmits<{ 'update:tocVisible': [value: boolean]; 'update:outlineVisible': [value: boolean] }>()
</script>

<template>
  <div class="reader-layout-controls" role="group" aria-label="阅读布局">
    <button type="button" title="切换左侧目录" aria-label="切换左侧目录" :aria-pressed="tocVisible" @click="$emit('update:tocVisible', !tocVisible)">
      <svg viewBox="0 0 20 20" aria-hidden="true"><rect x="2" y="3" width="16" height="14" rx="1" /><path d="M7 3v14" /><path v-if="tocVisible" class="pane-fill" d="M3 4h3v12H3z" /></svg>
    </button>
    <button v-if="hasOutline" type="button" title="切换右侧大纲" aria-label="切换右侧大纲" :aria-pressed="outlineVisible" @click="$emit('update:outlineVisible', !outlineVisible)">
      <svg viewBox="0 0 20 20" aria-hidden="true"><rect x="2" y="3" width="16" height="14" rx="1" /><path d="M13 3v14" /><path v-if="outlineVisible" class="pane-fill" d="M14 4h3v12h-3z" /></svg>
    </button>
  </div>
</template>

<style scoped>
.reader-layout-controls { display: inline-flex; align-items: center; gap: 2px; flex: none; }
button { display: grid; place-items: center; width: 30px; height: 28px; border: 0; border-radius: 4px; background: transparent; color: var(--reader-muted, #657286); cursor: pointer; }
button:hover, button[aria-pressed=true] { color: var(--reader-active-text, #2368d1); background: var(--reader-hover, #edf2f7); }
button:focus-visible { outline: 2px solid var(--reader-link, #2368d1); }
svg { width: 19px; height: 19px; fill: none; stroke: currentColor; stroke-width: 1.4; }
.pane-fill { fill: currentColor; stroke: none; opacity: .35; }
</style>
