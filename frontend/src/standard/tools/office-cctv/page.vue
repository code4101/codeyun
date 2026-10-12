<script setup lang="ts">
import { computed, ref } from 'vue'

import { BOM, CAMERAS, CHECKLISTS, POINTS, SOURCES, type BomItem, type CameraPoint } from './plan'

const cameraById = new Map(CAMERAS.map(c => [c.id, c]))
const cameraOf = (point: CameraPoint) => cameraById.get(point.camera)!

// 点位表的“楼层”列按楼层合并单元格。
function mergeFloor({ rowIndex, columnIndex }: { rowIndex: number; columnIndex: number }) {
  if (columnIndex !== 0) return [1, 1]
  const floor = POINTS[rowIndex]?.floor
  if (rowIndex > 0 && POINTS[rowIndex - 1]?.floor === floor) return [0, 0]
  return [POINTS.filter(p => p.floor === floor).length, 1]
}

const yuan = (n: number) => `¥${Math.round(n).toLocaleString('zh-CN')}`
const subtotal = (row: BomItem): [number, number] => [row.unit[0] * row.qty, row.unit[1] * row.qty]
const total = BOM.reduce<[number, number]>((sum, row) => {
  const [lo, hi] = subtotal(row)
  return [sum[0] + lo, sum[1] + hi]
}, [0, 0])
const range = ([lo, hi]: [number, number]) => `${yuan(lo)} – ${yuan(hi)}`

// 存储估算：每路每天 = 码率(Mbps) × 86400 秒 ÷ 8 ÷ 1000 = 码率 × 10.8 GB。
const channels = ref(POINTS.length)
const bitrate = ref(4)
const days = ref(30)
const disks = ref(2)
const diskTb = ref(8)
const dailyGb = computed(() => channels.value * bitrate.value * 10.8)
const neededTb = computed(() => dailyGb.value * days.value / 1000)
const capacityTb = computed(() => disks.value * diskTb.value)
const coverDays = computed(() => Math.floor(capacityTb.value * 1000 / dailyGb.value))
const defaultCoverDays = Math.floor(2 * 8 * 1000 / (POINTS.length * 4 * 10.8))

const TOPOLOGY = [
  { title: `摄像头 × ${POINTS.length}`, detail: '全部为室内半球，吸顶或壁装' },
  { title: '六类网线', detail: '一根线同时供电和传数据，单段 ≤ 100 米' },
  { title: '16 路 PoE 录像机', detail: '放在带锁的弱电间，双 8TB 盘，HDMI 接显示器' },
  { title: '公司路由器', detail: 'NVR 的 LAN 口接入公司网络' },
  { title: '手机 / 电脑', detail: '海康互联 App · iVMS-4200 客户端' },
]
</script>

<template>
  <main class="cctv">
    <header>
      <h1>公司监控方案 · 海康两层套房 9 路（全室内）</h1>
      <p class="meta">适用于公寓或写字楼里的两层办公套房：只有一个入户门，门外的公共区域由物业监控，所以摄像头全部装在室内。全部采用 PoE 有线摄像头，配一台 NVR 集中录像。价格是 2025–2026 年的网页报价区间，下单前请再次询价。</p>
    </header>

    <section class="tiles">
      <div class="tile"><span class="label">摄像头</span><strong>{{ POINTS.length }} 路</strong><span class="sub">全部室内，均为 400 万</span></div>
      <div class="tile"><span class="label">录像机</span><strong>16 路 PoE</strong><span class="sub">还剩 {{ 16 - POINTS.length }} 路余量</span></div>
      <div class="tile"><span class="label">存储</span><strong>2 × 8TB</strong><span class="sub">按 4Mbps 计算，约可保存 {{ defaultCoverDays }} 天</span></div>
      <div class="tile"><span class="label">预算</span><strong>{{ (total[0] / 10000).toFixed(1) }}–{{ (total[1] / 10000).toFixed(1) }} 万</strong><span class="sub">含安装和 UPS，硬盘价格波动最大</span></div>
    </section>

    <section>
      <h2>系统连接</h2>
      <ol class="topology">
        <li v-for="node in TOPOLOGY" :key="node.title"><strong>{{ node.title }}</strong><span>{{ node.detail }}</span></li>
      </ol>
      <p class="hint">NVR 的 PoE 口自带一个独立的内部网段，摄像头插上就能用，而且不进入办公网。套房内的距离一般都在 100 米以内，所有网线直接拉到 NVR 就行。</p>
    </section>

    <section>
      <h2>点位规划</h2>
      <el-table :data="POINTS" size="small" stripe :span-method="mergeFloor">
        <el-table-column prop="floor" label="楼层" width="64" />
        <el-table-column prop="place" label="位置" width="150" />
        <el-table-column label="设备" min-width="200">
          <template #default="{ row }">
            <div>{{ cameraOf(row).name }}</div>
            <code class="model">{{ cameraOf(row).model }}</code>
          </template>
        </el-table-column>
        <el-table-column prop="reason" label="为什么这样装" min-width="260" />
      </el-table>
      <div class="models">
        <div v-for="c in CAMERAS" :key="c.id" class="model-card">
          <strong>{{ c.name }}</strong> <code class="model">{{ c.model }}</code>
          <p>{{ c.traits }}</p>
        </div>
      </div>
      <p class="hint">室内统一选 2.8mm 广角，套房房间进深小，4mm 反而会拍不全。会议室不建议装；办公区只拍全景和通道，不要拍屏幕。</p>
    </section>

    <section>
      <h2>设备清单与预算</h2>
      <el-table :data="BOM" size="small" stripe show-summary :summary-method="() => ['合计', '', '', '', range(total)]">
        <el-table-column prop="item" label="项目" width="150" />
        <el-table-column label="规格" min-width="260">
          <template #default="{ row }">
            {{ row.spec }}
            <div v-if="row.note" class="note">{{ row.note }}</div>
          </template>
        </el-table-column>
        <el-table-column prop="qty" label="数量" width="64" align="right" />
        <el-table-column label="单价" width="130" align="right">
          <template #default="{ row }">{{ range(row.unit) }}</template>
        </el-table-column>
        <el-table-column label="小计" width="150" align="right">
          <template #default="{ row }">{{ range(subtotal(row)) }}</template>
        </el-table-column>
      </el-table>
    </section>

    <section>
      <h2>存储估算</h2>
      <div class="calc">
        <label>路数 <el-input-number v-model="channels" :min="1" :max="16" size="small" /></label>
        <label>每路码率 Mbps <el-input-number v-model="bitrate" :min="0.5" :max="16" :step="0.5" size="small" /></label>
        <label>保存天数 <el-input-number v-model="days" :min="1" :max="365" size="small" /></label>
        <label>硬盘 <el-input-number v-model="disks" :min="1" :max="2" size="small" /> × <el-input-number v-model="diskTb" :min="1" :max="20" size="small" /> TB</label>
      </div>
      <p class="calc-result">
        每天写入约 <strong>{{ dailyGb.toFixed(0) }} GB</strong>，保存 {{ days }} 天需要 <strong>{{ neededTb.toFixed(1) }} TB</strong>；
        现有 {{ capacityTb }} TB 约可保存 <strong :class="{ warn: coverDays < days }">{{ coverDays }} 天</strong>。
      </p>
      <p class="hint">400 万像素 H.265 主码流通常设为 4Mbps。开启 H.265+（智能编码）后，办公室这类画面变化少的场景平均能降到 1.5–2Mbps，同样的硬盘可以多存一倍时间。</p>
    </section>

    <section class="checklists">
      <div v-for="list in CHECKLISTS" :key="list.title" class="checklist">
        <h2>{{ list.title }}</h2>
        <ul><li v-for="item in list.items" :key="item">{{ item }}</li></ul>
      </div>
    </section>

    <section>
      <h2>价格来源</h2>
      <ul class="sources">
        <li v-for="s in SOURCES" :key="s.url"><a :href="s.url" target="_blank" rel="noopener noreferrer">{{ s.title }}</a></li>
      </ul>
    </section>
  </main>
</template>

<style scoped>
.cctv { padding: 12px 16px 32px; max-width: 1100px; color: #26384d; }
h1 { margin: 0; font-size: 18px; color: #173454; }
h2 { margin: 0 0 8px; font-size: 15px; color: #173454; }
section { margin-top: 20px; }
.meta { margin: 4px 0 0; color: #708196; font-size: 12px; }
.hint { margin: 8px 0 0; color: #708196; font-size: 12px; line-height: 1.6; }
.note { color: #a0702a; font-size: 12px; }
.model { padding: 1px 4px; background: #f2f5f8; border-radius: 3px; font-size: 12px; }

.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px; }
.tile { display: flex; flex-direction: column; gap: 2px; padding: 12px 14px; border: 1px solid #e3e9f0; border-radius: 6px; background: #fafcfe; }
.tile .label { font-size: 12px; color: #708196; }
.tile strong { font-size: 20px; color: #173454; }
.tile .sub { font-size: 12px; color: #708196; }

.topology { display: flex; flex-wrap: wrap; gap: 6px 22px; margin: 0; padding: 0; list-style: none; }
.topology li { position: relative; display: flex; flex-direction: column; padding: 8px 12px; min-width: 140px; border: 1px solid #c9d7e6; border-radius: 6px; background: #f4f8fc; font-size: 12px; color: #4b5d72; }
.topology li strong { font-size: 13px; color: #173454; }
.topology li:not(:last-child)::after { content: '→'; position: absolute; right: -17px; top: 50%; transform: translateY(-50%); color: #8aa0b8; }

.models { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 8px; margin-top: 10px; }
.model-card { padding: 8px 10px; border: 1px solid #e3e9f0; border-radius: 6px; font-size: 13px; }
.model-card p { margin: 4px 0 0; font-size: 12px; color: #708196; }

.calc { display: flex; flex-wrap: wrap; gap: 10px 20px; font-size: 13px; }
.calc label { display: flex; align-items: center; gap: 6px; }
.calc-result { margin: 10px 0 0; font-size: 14px; }
.calc-result strong { color: #173454; }
.calc-result strong.warn { color: #c0392b; }

.checklists { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 12px; }
.checklist { padding: 12px 14px; border: 1px solid #e3e9f0; border-radius: 6px; }
.checklist ul { margin: 0; padding-left: 18px; font-size: 13px; line-height: 1.7; }
.sources { margin: 0; padding-left: 18px; font-size: 13px; line-height: 1.8; }
.sources a { color: #2a6bb0; }
</style>
