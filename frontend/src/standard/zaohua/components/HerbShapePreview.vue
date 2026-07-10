<script setup lang="ts">
import { computed } from 'vue'

type HerbShape = {
  width: number
  height: number
  cells: Array<[number, number]>
}

const props = withDefaults(defineProps<{
  shape?: HerbShape | null
  imageUrl?: string
  label?: string
  color?: string
}>(), {
  shape: null,
  imageUrl: '',
  label: '',
  color: '#697176',
})

const occupied = computed(() => new Set(
  (props.shape?.cells || []).map(([x, y]) => `${x}:${y}`),
))
const cells = computed(() => {
  const width = Math.max(1, props.shape?.width || 1)
  const height = Math.max(1, props.shape?.height || 1)
  return Array.from({ length: width * height }, (_, index) => ({
    key: index,
    filled: occupied.value.has(`${index % width}:${Math.floor(index / width)}`),
  }))
})
const previewStyle = computed(() => ({
  '--shape-color': props.color,
  '--shape-columns': String(Math.max(1, props.shape?.width || 1)),
  '--shape-rows': String(Math.max(1, props.shape?.height || 1)),
  '--shape-width': `${Math.max(1, props.shape?.width || 1) * 52}px`,
  '--shape-height': `${Math.max(1, props.shape?.height || 1) * 52}px`,
}))
</script>

<template>
  <div v-if="shape?.cells.length" class="shape-preview" :style="previewStyle" :aria-label="`${label}占格形状`">
    <img v-if="imageUrl" class="shape-image" :src="imageUrl" :alt="`${label}占格形状图鉴`" />
    <div v-else class="shape-grid">
      <span v-for="cell in cells" :key="cell.key" :class="{ filled: cell.filled }"></span>
    </div>
  </div>
  <span v-else class="shape-empty">—</span>
</template>

<style scoped>
.shape-preview {
  position: relative;
  display: inline-grid;
  width: var(--shape-width);
  height: var(--shape-height);
  place-items: center;
}

.shape-grid {
  display: grid;
  grid-template-columns: repeat(var(--shape-columns), 52px);
  grid-template-rows: repeat(var(--shape-rows), 52px);
}

.shape-grid span {
  box-sizing: border-box;
  border: 1px solid #e1e5e2;
  border-radius: 3px;
  background: #fff;
}

.shape-grid span.filled {
  border-color: color-mix(in srgb, var(--shape-color) 64%, #cfd4d0);
  background: color-mix(in srgb, var(--shape-color) 26%, #fff);
}

.shape-image {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.shape-empty {
  color: #8a9094;
}
</style>
