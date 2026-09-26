<script setup lang="ts">
import { computed, ref, useSlots } from 'vue'
import ReaderActivityBar from './ReaderActivityBar.vue'
import { useReaderColumnWidths } from './useReaderColumnWidths'

// Content providers own data and rendering; this component alone owns pane geometry.
const props = withDefaults(defineProps<{
  tocVisible?: boolean
  outlineVisible?: boolean
  tocWidth?: number
  outlineWidth?: number
  resizeToc?: boolean
}>(), { tocVisible: true, outlineVisible: true, tocWidth: 290, outlineWidth: 220, resizeToc: true })
const emit = defineEmits<{ 'update:tocVisible': [value: boolean]; 'update:outlineVisible': [value: boolean]; resized: [] }>()
const panes = ref<HTMLElement>()
const slots = useSlots()
const { actual, dragging, limit, start, move, finish, reset, key } = useReaderColumnWidths(panes, () => ({
  toc: props.tocWidth, outline: props.outlineWidth, resizeToc: props.resizeToc,
  tocVisible: props.tocVisible && Boolean(slots.toc), outlineVisible: props.outlineVisible && Boolean(slots.outline),
}), () => emit('resized'))
const widths = computed(() => ({ '--reader-toc-width': `${actual.value.toc}px`, '--reader-outline-width': `${actual.value.outline}px` }))
</script>

<template>
  <div class="reader-layout" :style="widths">
    <div class="reader-rail">
      <slot name="rail">
        <ReaderActivityBar v-if="$slots.toc" :items="[{ id: 'toc', title: '目录', icon: 'document' }]" :active-id="tocVisible ? 'toc' : ''" @select="$emit('update:tocVisible', !tocVisible)" />
      </slot>
    </div>
    <div ref="panes" class="reader-panes" :class="{ 'is-resizing': dragging, 'has-toc': tocVisible && $slots.toc, 'has-outline': outlineVisible && $slots.outline }">
      <aside v-if="$slots.toc" v-show="tocVisible" class="reader-toc" aria-label="目录与搜索"><slot name="toc" /></aside>
      <main class="reader-content">
        <slot />
        <template v-for="side in (['toc', 'outline'] as const)" :key="side">
          <div v-if="side === 'toc' ? resizeToc && tocVisible && $slots.toc : outlineVisible && $slots.outline"
            class="reader-divider" :class="[side, { active: dragging === side }]" role="separator" tabindex="0" aria-orientation="vertical"
            :aria-label="side === 'toc' ? '调整目录宽度' : '调整大纲宽度'" :aria-valuenow="actual[side]" :aria-valuemin="160" :aria-valuemax="limit(side)"
            title="拖动调整宽度，双击恢复默认" @pointerdown="start($event, side)" @pointermove="move" @pointerup="finish" @pointercancel="finish" @lostpointercapture="finish"
            @dblclick="reset(side)" @keydown="key($event, side)" />
        </template>
      </main>
      <aside v-if="$slots.outline" v-show="outlineVisible" class="reader-outline" aria-label="本章大纲"><slot name="outline" /></aside>
    </div>
  </div>
</template>

<style scoped>
.reader-layout { --reader-content-top-inset: 12px; display: flex; flex: 1; height: 100%; min-height: 0; min-width: 0; overflow: hidden; color: var(--reader-text, #273447); background: var(--reader-content, #fff); }
.reader-rail { display: flex; flex: none; }
.reader-panes { flex: 1; display: grid; min-width: 0; min-height: 0; grid-template-columns: minmax(0, 1fr); }
.reader-panes.has-toc { grid-template-columns: var(--reader-toc-width) minmax(0, 1fr); }
.reader-panes.has-outline { grid-template-columns: minmax(0, 1fr) var(--reader-outline-width); }
.reader-panes.has-toc.has-outline { grid-template-columns: var(--reader-toc-width) minmax(0, 1fr) var(--reader-outline-width); }
.reader-toc, .reader-outline, .reader-content { display: flex; flex-direction: column; min-width: 0; min-height: 0; }
.reader-toc, .reader-outline { overflow: hidden; }
.reader-outline { background: var(--reader-panel, #f7f9fb); }
/* Docked panes fill the workspace, even when their content is only a few lines. */
.reader-outline > :slotted(*) { flex: 1; min-height: 0; }
.reader-content { position: relative; }
.reader-toc { border-right: 1px solid var(--reader-border, #e4e9ef); background: var(--reader-panel, #f7f9fb); }
.reader-divider { position: absolute; top: 0; bottom: 0; width: 8px; z-index: 10; cursor: col-resize; touch-action: none; }
.reader-divider.toc { left: -4px; }
.reader-divider.outline { right: -4px; }
.reader-divider:hover, .reader-divider.active, .reader-divider:focus-visible { background: var(--reader-active-text, #2368d1); opacity: .4; outline: none; }
.reader-panes.is-resizing { user-select: none; cursor: col-resize; }
@media (max-width: 980px) {
  .reader-divider { display: none; }
  .reader-layout { flex-direction: column; }
  .reader-panes, .reader-panes.has-toc, .reader-panes.has-outline, .reader-panes.has-toc.has-outline { grid-template-columns: minmax(0, 1fr); grid-template-rows: minmax(0, 1fr); }
  .reader-panes.has-toc { grid-template-rows: minmax(100px, 28%) minmax(0, 1fr); }
  .reader-panes.has-outline { grid-template-rows: minmax(0, 1fr) minmax(100px, 20%); }
  .reader-panes.has-toc.has-outline { grid-template-rows: minmax(100px, 28%) minmax(0, 1fr) minmax(100px, 20%); }
  .reader-toc { border-right: 0; border-bottom: 1px solid var(--reader-border, #e4e9ef); }
  .reader-outline { border-top: 1px solid var(--reader-border, #e4e9ef); }
}
</style>
