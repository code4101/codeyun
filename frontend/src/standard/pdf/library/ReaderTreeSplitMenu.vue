<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import ReaderOutlineLevelOptions from './ReaderOutlineLevelOptions.vue'
import { libraryReaderThemeClass } from './readerTheme'
defineProps<{ modelValue: number }>()
const emit = defineEmits<{ 'update:modelValue': [level: number] }>()
const position = ref<{ x: number; y: number }>()
const menu = ref<HTMLElement>()
function open(event: MouseEvent) {
  event.preventDefault(); event.stopPropagation()
  position.value = { x: Math.max(4, Math.min(event.clientX, innerWidth - 204)), y: Math.max(4, Math.min(event.clientY, innerHeight - 52)) }
}
function close(event: Event) { if (!menu.value?.contains(event.target as Node)) position.value = undefined }
function key(event: KeyboardEvent) { if (event.key === 'Escape') position.value = undefined }
function select(level: number) { emit('update:modelValue', level); position.value = undefined }
onMounted(() => { window.addEventListener('pointerdown', close); window.addEventListener('keydown', key); window.addEventListener('resize', close) })
onBeforeUnmount(() => { window.removeEventListener('pointerdown', close); window.removeEventListener('keydown', key); window.removeEventListener('resize', close) })
</script>
<template>
  <div class="reader-split-target" @contextmenu="open"><slot /></div>
  <Teleport to="body"><div v-if="position" ref="menu" class="reader-split-menu library-reader-theme-dialog" :class="libraryReaderThemeClass" role="menu" :style="{ left: `${position.x}px`, top: `${position.y}px` }"><ReaderOutlineLevelOptions :model-value="modelValue" @update:model-value="select" /></div></Teleport>
</template>
<style scoped>
.reader-split-target { display: flex; flex: 1; min-height: 0; flex-direction: column; }
.reader-split-menu { position: fixed; z-index: 10000; width: 196px; padding: 4px; border: 1px solid var(--reader-border); border-radius: 5px; box-shadow: 0 4px 18px #0002; }
</style>
