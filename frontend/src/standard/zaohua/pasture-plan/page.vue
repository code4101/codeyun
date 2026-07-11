<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { fetchZaohuaPastureMeta, type ZaohuaPastureMeta } from '@/api/zaohua'

type CellType = 'crop' | 'spring' | 'pivot' | 'pavilion'
type CropKey = 'yuehua' | 'yangying' | 'tongluo'
type PlanCell = { x: number; y: number; type: CellType; crop?: CropKey }

const meta = ref<ZaohuaPastureMeta | null>(null)
const crops = {
  yuehua: { name: '月华草', image: '/api/zaohua/media/icons/item/herb/992434', tone: 'violet' },
  yangying: { name: '阳萤草', image: '/api/zaohua/media/icons/item/herb/100019', tone: 'amber' },
  tongluo: { name: '通络草', image: '/api/zaohua/media/icons/item/herb/992429', tone: 'green' },
} as const

const cells: PlanCell[] = [
  { x: 0, y: 0, type: 'pivot' }, { x: 0, y: 1, type: 'crop', crop: 'yangying' }, { x: 0, y: 2, type: 'pivot' }, { x: 0, y: 3, type: 'crop', crop: 'yangying' }, { x: 0, y: 4, type: 'pivot' },
  { x: 1, y: 0, type: 'crop', crop: 'yuehua' }, { x: 1, y: 1, type: 'spring' }, { x: 1, y: 2, type: 'crop', crop: 'yangying' }, { x: 1, y: 3, type: 'spring' }, { x: 1, y: 4, type: 'crop', crop: 'yuehua' },
  { x: 2, y: 0, type: 'pivot' }, { x: 2, y: 1, type: 'crop', crop: 'yuehua' }, { x: 2, y: 2, type: 'pivot' }, { x: 2, y: 3, type: 'crop', crop: 'yuehua' }, { x: 2, y: 4, type: 'pivot' },
  { x: 3, y: 0, type: 'crop', crop: 'tongluo' }, { x: 3, y: 1, type: 'spring' }, { x: 3, y: 2, type: 'crop', crop: 'yuehua' }, { x: 3, y: 3, type: 'spring' },
]

const byCoordinate = new Map(cells.map(cell => [`${cell.x},${cell.y}`, cell]))
const coefficient = (cell: PlanCell) => {
  if (cell.type !== 'crop') return 0
  const neighbors = [[cell.x - 1, cell.y], [cell.x + 1, cell.y], [cell.x, cell.y - 1], [cell.x, cell.y + 1]]
    .map(([x, y]) => byCoordinate.get(`${x},${y}`))
  return (1 + neighbors.filter(item => item?.type === 'spring').length)
    * (1 + neighbors.filter(item => item?.type === 'pivot').length)
}
const building = (type: CellType) => {
  const id = type === 'spring' ? 4 : type === 'pivot' ? 5 : type === 'pavilion' ? 7 : 0
  return meta.value?.buildings.find(item => item.build_id === id)
}
const totalValue = computed(() => cells.reduce((sum, cell) => sum + coefficient(cell), 0))

onMounted(async () => { meta.value = await fetchZaohuaPastureMeta() })
</script>

<template>
  <div class="demo-page">
    <header>
      <div>
        <h1>聚元丹洞天方案</h1>
        <p>结丹 · 19格　月华草×6 · 阳萤草×10 · 通络草×1</p>
      </div>
      <div class="headline-result"><b>{{ totalValue }}</b><span>洞天总价值</span></div>
    </header>

    <section class="stage">
      <div class="status-strip">
        <strong>约 5.0 日供应 1 炉</strong>
        <span class="danger">瓶颈：月华草 · 通络草</span>
        <span class="warning">阳萤草溢出约 5%</span>
      </div>
      <div class="board">
        <article
          v-for="(cell, index) in cells" :key="index"
          class="cell" :class="[cell.type, cell.crop ? crops[cell.crop].tone : '', cell.crop === 'yuehua' || cell.crop === 'tongluo' ? 'bottleneck' : '', cell.crop === 'yangying' ? 'overflow' : '']"
          :style="{ gridColumn: cell.x + 1, gridRow: cell.y + 1 }"
        >
          <template v-if="cell.type === 'crop' && cell.crop">
            <span class="corner">灵田</span>
            <span v-if="cell.crop === 'yuehua' || cell.crop === 'tongluo'" class="badge danger-bg">瓶颈</span>
            <span v-else-if="cell.crop === 'yangying'" class="badge warning-bg">+5%</span>
            <img :src="crops[cell.crop].image" alt="" />
            <b>{{ crops[cell.crop].name }}</b>
            <strong>×{{ coefficient(cell) }}</strong>
          </template>
          <template v-else>
            <img v-if="building(cell.type)?.image_url" :src="building(cell.type)?.image_url" alt="" />
            <b>{{ building(cell.type)?.name || (cell.type === 'spring' ? '灵泉' : cell.type === 'pivot' ? '灵枢台' : '悟丹亭') }}</b>
          </template>
        </article>
      </div>
    </section>
  </div>
</template>

<style scoped>
.demo-page { box-sizing: border-box; height: 100%; overflow: auto; padding: 20px 24px; color: var(--el-text-color-primary); }
header { display: flex; align-items: flex-start; justify-content: space-between; max-width: 850px; }
h1 { margin: 0; font-size: 22px; } p { margin: 6px 0 0; color: var(--el-text-color-secondary); }
.headline-result { display: flex; align-items: baseline; gap: 8px; } .headline-result b { font-size: 28px; } .headline-result span { color: var(--el-text-color-secondary); }
.stage { margin-top: 18px; width: max-content; }
.status-strip { display: flex; gap: 18px; align-items: center; margin-bottom: 12px; padding: 9px 12px; background: var(--el-fill-color-light); }
.danger { color: var(--el-color-danger); } .warning { color: var(--el-color-warning); }
.board { display: grid; grid-template-columns: repeat(5, 112px); grid-template-rows: repeat(5, 112px); gap: 8px; }
.cell { position: relative; box-sizing: border-box; width: 112px; height: 112px; border: 2px solid var(--el-border-color); background: var(--el-fill-color-light); display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 3px; }
.cell img { width: 52px; height: 45px; object-fit: contain; } .cell.crop img { width: 38px; height: 38px; }
.cell.crop strong { font-size: 18px; color: var(--el-color-success); }
.cell.violet { border-color: #8b5cf6; background: #f6f1ff; } .cell.amber { border-color: #f59e0b; background: #fff8e8; } .cell.green { border-color: #22a06b; background: #eefaf5; }
.cell.bottleneck { box-shadow: 0 0 0 2px rgb(245 108 108 / 28%); } .cell.overflow { opacity: .72; }
.corner { position: absolute; left: 5px; top: 4px; color: var(--el-text-color-secondary); font-size: 11px; }
.badge { position: absolute; right: 4px; top: 4px; padding: 1px 4px; color: white; font-size: 10px; }
.danger-bg { background: var(--el-color-danger); } .warning-bg { background: var(--el-color-warning); }
</style>
