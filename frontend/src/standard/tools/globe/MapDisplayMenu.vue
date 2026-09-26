<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { displayLabels, type DisplayKey, type MapDisplay } from './mapDisplay'

const props = defineProps<{ x: number; y: number; flat: boolean; settings: MapDisplay }>()
const emit = defineEmits<{ close: []; toggle: [key: DisplayKey] }>()
const panel = ref<HTMLElement>()
const left = ref(props.x)
const top = ref(props.y)
const keys = computed(() => props.flat ? ['autoRegions', 'adminBoundaries', 'adminNames', 'countries'] as DisplayKey[] : Object.keys(displayLabels) as DisplayKey[])
function outside(event: PointerEvent) {
  if (!panel.value?.contains(event.target as Node)) emit('close')
}
onMounted(async () => {
  await nextTick()
  const bounds = panel.value?.getBoundingClientRect()
  left.value = Math.max(8, Math.min(props.x, window.innerWidth - (bounds?.width ?? 180) - 8))
  top.value = Math.max(8, Math.min(props.y, window.innerHeight - (bounds?.height ?? 260) - 8))
  panel.value?.querySelector('input')?.focus()
  document.addEventListener('pointerdown', outside, true)
})
onBeforeUnmount(() => document.removeEventListener('pointerdown', outside, true))
</script>

<template>
  <Teleport to="body">
    <div ref="panel" class="map-display-menu" role="dialog" aria-label="地图显示"
      :style="{ left: `${left}px`, top: `${top}px` }" @contextmenu.prevent @keydown.esc.stop.prevent="emit('close')">
      <strong>地图显示</strong>
      <label v-for="key in keys" :key="key">
        <input type="checkbox" :checked="settings[key]" @change="emit('toggle', key)">
        {{ displayLabels[key] }}
      </label>
    </div>
  </Teleport>
</template>

<style scoped>
.map-display-menu { position: fixed; z-index: 3000; min-width: 160px; max-height: calc(100dvh - 16px); overflow: auto; box-sizing: border-box; padding: 10px; background: #fff; color: #263e5a; border: 1px solid #dbe4ee; border-radius: 10px; box-shadow: 0 6px 24px #17395a24; font-size: 13px; }
strong { display: block; padding: 5px 8px 9px; }
label { display: flex; align-items: center; gap: 10px; padding: 8px; border-radius: 5px; cursor: pointer; }
label:hover, label:focus-within { background: #eef4fa; }
input { accent-color: #1767cf; }
</style>
