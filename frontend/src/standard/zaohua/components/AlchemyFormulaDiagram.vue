<script setup lang="ts">
import { computed } from 'vue'

import type { ZaohuaAlchemySolution } from '@/api/zaohua'

type Placement = ZaohuaAlchemySolution['placements'][number]

const props = defineProps<{
  solution: ZaohuaAlchemySolution
  yangWidth: number
  yangHeight: number
  yinWidth: number
  yinHeight: number
}>()

const cellSize = computed(() => {
  const longest = Math.max(props.yangWidth, props.yangHeight, props.yinWidth, props.yinHeight)
  if (longest >= 10) return 16
  if (longest >= 7) return 20
  return 27
})

const sideSize = (side: 'yang' | 'yin') => side === 'yang'
  ? { width: props.yangWidth, height: props.yangHeight }
  : { width: props.yinWidth, height: props.yinHeight }

const boardCells = (side: 'yang' | 'yin') => {
  const { width, height } = sideSize(side)
  const occupied = new Set<string>()
  for (const placement of props.solution.placements) {
    if (placement.side !== side) continue
    for (const [x, y] of placement.cells) occupied.add(`${x}:${y}`)
  }
  return Array.from({ length: width * height }, (_, index) => {
    const x = index % width
    const y = Math.floor(index / width)
    return { key: `${side}:${x}:${y}`, occupied: occupied.has(`${x}:${y}`) }
  })
}

const boardPlacements = (side: 'yang' | 'yin') => props.solution.placements.filter(
  placement => placement.side === side,
)

const boardStyle = (side: 'yang' | 'yin') => ({
  '--formula-columns': String(sideSize(side).width),
  '--formula-cell-size': `${cellSize.value}px`,
})

const placementStyle = (placement: Placement) => {
  const xs = placement.cells.map(([x]) => x)
  const ys = placement.cells.map(([, y]) => y)
  const minX = Math.min(...xs)
  const minY = Math.min(...ys)
  const width = Math.max(...xs) - minX + 1
  const height = Math.max(...ys) - minY + 1
  const stride = cellSize.value + 2
  return {
    left: `${5 + minX * stride}px`,
    top: `${5 + minY * stride}px`,
    width: `${width * cellSize.value + (width - 1) * 2}px`,
    height: `${height * cellSize.value + (height - 1) * 2}px`,
    '--herb-image-width': `${placement.shape_width * cellSize.value + (placement.shape_width - 1) * 2}px`,
    '--herb-image-height': `${placement.shape_height * cellSize.value + (placement.shape_height - 1) * 2}px`,
    '--herb-rotation': `${placement.rotation}deg`,
  }
}
</script>

<template>
  <div class="formula-boards">
    <section v-for="side in (['yang', 'yin'] as const)" :key="side" class="formula-board-wrap">
      <span :class="['formula-side', `${side}-text`]">{{ side === 'yang' ? '阳' : '阴' }}</span>
      <div :class="['formula-board', `${side}-board`]" :style="boardStyle(side)" role="grid">
        <span
          v-for="cell in boardCells(side)"
          :key="cell.key"
          :class="['formula-cell', { occupied: cell.occupied }]"
          role="gridcell"
        ></span>
        <span
          v-for="(placement, index) in boardPlacements(side)"
          :key="`${placement.item_id}:${index}`"
          class="formula-herb"
          :style="placementStyle(placement)"
          :title="placement.name"
        >
          <img
            v-if="placement.shape_image_url"
            :src="placement.shape_image_url"
            :alt="placement.name"
            draggable="false"
          />
          <span v-else>{{ placement.name.slice(0, 1) }}</span>
        </span>
      </div>
    </section>
  </div>
</template>

<style scoped>
.formula-boards {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  max-width: 100%;
  overflow-x: auto;
}

.formula-board-wrap {
  display: grid;
  gap: 4px;
  justify-items: center;
}

.formula-side {
  font-size: 12px;
  font-weight: 600;
}

.yang-text {
  color: #945431;
}

.yin-text {
  color: #3d687b;
}

.formula-board {
  position: relative;
  display: grid;
  grid-template-columns: repeat(var(--formula-columns), var(--formula-cell-size));
  gap: 2px;
  width: max-content;
  padding: 4px;
  border: 1px solid #b9c0bc;
  background: #e2e6e2;
}

.yang-board {
  border-color: #bcaea5;
  background: #e8e0db;
}

.yin-board {
  border-color: #aab8be;
  background: #dde5e8;
}

.formula-cell {
  box-sizing: border-box;
  width: var(--formula-cell-size);
  height: var(--formula-cell-size);
  border: 1px solid #cbd1cd;
  background: #fafbf9;
}

.formula-cell.occupied {
  background: #f0f2f0;
}

.formula-herb {
  position: absolute;
  z-index: 1;
  display: grid;
  overflow: visible;
  color: #59615d;
  font-size: 11px;
  place-items: center;
}

.formula-herb img {
  position: absolute;
  top: 50%;
  left: 50%;
  display: block;
  width: var(--herb-image-width);
  height: var(--herb-image-height);
  max-width: none;
  object-fit: contain;
  pointer-events: none;
  transform: translate(-50%, -50%) rotate(var(--herb-rotation));
  transform-origin: center;
  user-select: none;
}
</style>
