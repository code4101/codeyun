<template>
  <div class="timeline-notes" v-loading="loading">
    <div class="timeline-toolbar">
      <div><strong>时间轴</strong><span class="hint">滚轮缩放 · 拖动平移 · 点击编辑</span></div>
      <div class="actions">
        <el-button size="small" aria-label="缩小时间轴" @click="zoom(1.6)">−</el-button>
        <el-button size="small" aria-label="放大时间轴" @click="zoom(1 / 1.6)">＋</el-button>
        <el-button size="small" @click="fit">全景</el-button>
        <el-button size="small" @click="center = Date.now()">今天</el-button>
        <el-select v-model="density" size="small" style="width: 92px" aria-label="节点密度">
          <el-option label="疏朗" :value="1" /><el-option label="适中" :value="2" /><el-option label="丰富" :value="3" />
        </el-select>
        <el-button size="small" :disabled="loading" @click="reload">刷新</el-button>
      </div>
    </div>
    <div class="timeline-summary">
      <span>{{ rangeLabel }} · 显示 {{ layout.length }} / {{ inRange }} 个节点</span>
      <span>优先保留高权重节点<span v-if="total > notes.length"> · 已载入最近 {{ notes.length }} / {{ total }} 条</span></span>
    </div>
    <div ref="surface" class="timeline-surface" tabindex="0" aria-label="笔记时间轴，左右键平移，加减键缩放"
      @wheel.prevent="wheel" @pointerdown="pointerDown" @pointermove="pointerMove" @pointerup="pointerUp"
      @pointercancel="pointerUp" @lostpointercapture="drag = null" @keydown="keydown">
      <div v-for="tick in ticks" :key="tick.time" class="tick" :style="{ left: `${tick.x}px` }">
        <span>{{ tick.label }}</span>
      </div>
      <div class="axis" />
      <div v-if="todayX >= 0 && todayX <= width" class="today" :style="{ left: `${todayX}px` }"><span>今天</span></div>
      <template v-for="item in layout" :key="item.note.id">
        <div class="stem" :style="{ left: `${item.x}px`, top: `${axisY}px`, height: `${24 + item.lane * 96}px`, background: color(item.note) }" />
        <i class="dot" :style="{ left: `${item.x}px`, top: `${axisY}px`, background: color(item.note), width: `${radius(item.importance)}px`, height: `${radius(item.importance)}px` }" />
        <button class="note-card" :class="{ selected: selected === String(item.note.id) }"
          :style="{ left: `${item.left}px`, top: `${axisY + 24 + item.lane * 96}px`, borderTopColor: color(item.note) }"
          :title="`${item.note.title}\n${formatDate(item.note.start_at)} · 权重 ${item.note.weight}`"
          @pointerdown.stop @click="selected = String(item.note.id)">
          <span class="card-meta">{{ formatDate(item.note.start_at) }} <b>W {{ item.note.weight }}</b></span>
          <strong>{{ item.note.title || '无标题' }}</strong>
        </button>
      </template>
      <div v-if="!loading && !layout.length" class="empty">
        {{ failed ? '加载失败，请点击刷新重试' : notes.length ? '这段时间暂无节点，拖动探索或返回全景' : '暂无笔记，创建带起始时间的节点后会显示在这里' }}
      </div>
      <div class="scale-caption">{{ Math.max(1, Math.round(span / DAY)) }} 天视野</div>
    </div>
    <el-drawer v-model="detailOpen" title="节点详情" size="min(680px, 95vw)" append-to-body destroy-on-close>
      <NoteDetailPanel v-if="selected" :note-id="selected" editor-layout="fill" @delete="selected = ''" />
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, ref } from 'vue';
import { buildScanNoteProgramRequest, createIncludeAllProgram, useNoteStore, type NoteNode } from '@/api/notes';
import { ensureNoteTypePaletteLoaded, getNodeTheme } from '@/utils/nodeConfig';
import { layoutTimeline } from '@/utils/noteTimeline';

const props = defineProps<{ tabId: string; active?: boolean }>();
const NoteDetailPanel = defineAsyncComponent(() => import('@/components/NoteDetailPanel.vue'));
const store = useNoteStore();
const DAY = 86400000;
const axisY = 90;
const surface = ref<HTMLElement>();
const width = ref(900);
const height = ref(500);
const center = ref(Date.now());
const span = ref(30 * DAY);
const density = ref(2);
const loading = ref(false);
const failed = ref(false);
const total = ref(0);
const selected = ref('');
const detailOpen = computed({ get: () => Boolean(selected.value), set: value => { if (!value) selected.value = ''; } });
const notes = computed(() => store.getTabNotes(props.tabId).filter(n => Number.isFinite(n.start_at)));
const start = computed(() => center.value - span.value / 2);
const inRange = computed(() => notes.value.filter(n => n.start_at >= start.value && n.start_at <= start.value + span.value).length);
const lanes = computed(() => Math.min(density.value, Math.max(1, Math.floor((height.value - axisY - 60) / 96))));
const layout = computed(() => layoutTimeline(notes.value, start.value, span.value, width.value, lanes.value));
const todayX = computed(() => (Date.now() - start.value) / span.value * width.value);
const formatDate = (time: number) => new Date(time).toLocaleDateString('zh-CN');
const rangeLabel = computed(() => `${formatDate(start.value)} — ${formatDate(start.value + span.value)}`);
const color = (note: NoteNode) => getNodeTheme(note.node_type, note.color, note.note_types).baseColor;
const radius = (importance: number) => Math.max(6, Math.min(22, 9 + importance * 1.5));
const ticks = computed(() => {
  const count = Math.max(2, Math.floor(width.value / 130));
  return Array.from({ length: count }, (_, i) => {
    const x = (i + 0.5) * width.value / count;
    const time = start.value + x / width.value * span.value;
    const date = new Date(time);
    const label = span.value < 3 * DAY
      ? date.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
      : formatDate(time);
    return { x, time, label };
  });
});
function fit() {
  if (!notes.value.length) return;
  let min = Infinity, max = -Infinity;
  for (const note of notes.value) { min = Math.min(min, note.start_at); max = Math.max(max, note.start_at); }
  center.value = (min + max) / 2;
  span.value = Math.max(DAY, (max - min) * 1.15);
}
function zoom(factor: number, anchor = 0.5) {
  const next = Math.max(DAY / 24, Math.min(365250 * DAY, span.value * factor));
  center.value += (anchor - 0.5) * (span.value - next);
  span.value = next;
}
function wheel(event: WheelEvent) {
  const rect = surface.value!.getBoundingClientRect();
  zoom(Math.exp(Math.max(-0.5, Math.min(0.5, event.deltaY * (event.deltaMode === 1 ? 16 : 1) * 0.002))),
    Math.max(0, Math.min(1, (event.clientX - rect.left) / width.value)));
}
const drag = ref<{ id: number; x: number; center: number } | null>(null);
function pointerDown(event: PointerEvent) {
  if (event.button !== 0) return;
  drag.value = { id: event.pointerId, x: event.clientX, center: center.value };
  surface.value?.setPointerCapture(event.pointerId);
}
function pointerMove(event: PointerEvent) {
  if (drag.value?.id === event.pointerId) center.value = drag.value.center - (event.clientX - drag.value.x) / width.value * span.value;
}
function pointerUp(event: PointerEvent) {
  if (surface.value?.hasPointerCapture(event.pointerId)) surface.value.releasePointerCapture(event.pointerId);
  drag.value = null;
}
function keydown(event: KeyboardEvent) {
  if (event.target !== surface.value) return;
  if (!['ArrowLeft', 'ArrowRight', '+', '=', '-'].includes(event.key)) return;
  event.preventDefault();
  if (event.key === 'ArrowLeft') center.value -= span.value / 5;
  else if (event.key === 'ArrowRight') center.value += span.value / 5;
  else zoom(event.key === '-' ? 1.6 : 1 / 1.6);
}
async function reload() {
  loading.value = true;
  failed.value = false;
  try {
    const result = await store.queryNoteProgramForTab(props.tabId, buildScanNoteProgramRequest(createIncludeAllProgram(), {
      limit: 1000, order_by: 'start_at', order_desc: true, include_edges: false, include_custom_fields: false,
    }));
    if (!result) { failed.value = true; return; }
    total.value = result.data.total_nodes;
    fit();
  } catch { failed.value = true; }
  finally { loading.value = false; }
}
let observer: ResizeObserver | undefined;
onMounted(() => {
  observer = new ResizeObserver(entries => {
    const rect = entries[0]?.contentRect;
    if (rect && rect.width > 0) { width.value = rect.width; height.value = rect.height; }
  });
  if (surface.value) observer.observe(surface.value);
  void ensureNoteTypePaletteLoaded().catch(() => {});
  void reload();
});
onBeforeUnmount(() => observer?.disconnect());
</script>

<style scoped>
.timeline-notes { height: 100%; min-height: 0; display: flex; flex-direction: column; color: #334155; }
.timeline-toolbar { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; padding: 16px 20px 12px; }
.hint { color: #8491a5; font-size: 12px; margin-left: 16px; }
.actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.actions :deep(.el-button + .el-button) { margin-left: 0; }
.timeline-summary { padding: 0 20px 12px; display: flex; justify-content: space-between; gap: 8px; flex-wrap: wrap; font-size: 12px; color: #8290a4; }
.timeline-surface { position: relative; flex: 1; min-height: 280px; overflow: hidden; background: radial-gradient(ellipse at 50% 0%, #eff5ff, #fafbfe 70%); cursor: grab; touch-action: none; user-select: none; }
.timeline-surface:active { cursor: grabbing; }
.timeline-surface:focus-visible { outline: 2px solid #409eff; outline-offset: -2px; }
.axis { position: absolute; top: 90px; left: 0; right: 0; height: 2px; background: #cbd7e9; }
.tick { position: absolute; top: 58px; bottom: 0; border-left: 1px dashed #dce4f0; pointer-events: none; }
.tick span { position: absolute; top: -25px; transform: translateX(-50%); white-space: nowrap; font-size: 11px; color: #7d8ca3; }
.today { position: absolute; top: 66px; bottom: 0; border-left: 1px dashed #e9ac6a; pointer-events: none; }
.today span { position: absolute; top: 0; left: 5px; font-size: 11px; color: #c78843; white-space: nowrap; }
.stem { position: absolute; width: 1px; opacity: .3; pointer-events: none; }
.dot { position: absolute; border-radius: 50%; transform: translate(-50%, -50%); border: 3px solid #fafbfe; pointer-events: none; }
.note-card { position: absolute; box-sizing: border-box; width: 176px; height: 80px; border: 1px solid #e2e8f0; border-top: 3px solid; border-radius: 8px; background: white; padding: 10px 12px; text-align: left; cursor: pointer; color: #334155; box-shadow: 0 3px 10px #24395a08; }
.note-card:hover, .note-card.selected { box-shadow: 0 4px 16px #407ac526; outline: 2px solid #95b9ec; z-index: 2; }
.card-meta { display: flex; justify-content: space-between; gap: 4px; font-size: 10px; color: #8995a7; margin-bottom: 6px; }
.card-meta b { font-weight: 500; }
.note-card strong { font-size: 13px; font-weight: 550; line-height: 19px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; overflow-wrap: anywhere; }
.empty { position: absolute; top: 52%; width: 100%; text-align: center; color: #8b98ab; font-size: 13px; }
.scale-caption { position: absolute; bottom: 15px; right: 20px; font-size: 11px; color: #8b98ab; }
</style>
