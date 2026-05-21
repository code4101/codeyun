<template>
  <div class="game-window-page">
    <section class="stage-pane">
      <div class="topbar">
        <div class="topbar-title">
          <h2>游戏窗口</h2>
          <span>{{ naturalSizeText }}</span>
        </div>
        <div class="stream-controls">
          <el-input
            v-model="targetTitle"
            class="title-input"
            size="small"
            placeholder="窗口标题"
            @keyup.enter="restartStream"
          />
          <el-input
            v-model="cropText"
            class="crop-input"
            size="small"
            placeholder="窗口裁边 左,上,右,下"
            @keyup.enter="restartStream"
          />
          <el-input
            v-model="trimBorderText"
            class="crop-input"
            size="small"
            placeholder="画面裁边 左,上,右,下"
            @keyup.enter="restartStream"
          />
          <el-select v-model="captureArea" class="area-select" size="small" @change="restartStream">
            <el-option label="外框" value="outer" />
            <el-option label="客户区" value="client" />
          </el-select>
          <el-select v-model="rotateDegrees" class="rotate-select" size="small" @change="restartStream">
            <el-option label="0°" value="0" />
            <el-option label="90°" value="90" />
            <el-option label="180°" value="180" />
            <el-option label="270°" value="270" />
          </el-select>
          <el-input-number v-model="fps" class="number-input" size="small" :min="1" :max="30" controls-position="right" @change="restartStream" />
          <el-switch
            v-model="autoDismissPopup"
            size="small"
            active-text="自动关弹窗"
            inactive-text="手动弹窗"
            @change="restartStream"
          />
          <el-button :icon="Refresh" size="small" @click="restartStream">重连</el-button>
          <el-button :icon="streamEnabled ? VideoPause : VideoPlay" size="small" @click="toggleStream">
            {{ streamEnabled ? '暂停' : '继续' }}
          </el-button>
        </div>
      </div>

      <div class="workspace">
        <div class="viewer-pane">
          <div ref="imageWrapRef" class="image-wrap">
            <img
              v-if="streamEnabled"
              ref="streamImageRef"
              class="stream-image"
              :src="streamUrl"
              alt="凡修游戏窗口"
              draggable="false"
              @load="handleImageLoad"
              @error="handleStreamError"
            />
            <div v-else class="paused-placeholder">画面已暂停</div>
            <canvas
              ref="overlayCanvasRef"
              class="overlay-canvas"
              @pointerdown="handlePointerDown"
              @pointermove="handlePointerMove"
              @pointerup="handlePointerUp"
              @pointerleave="handlePointerLeave"
              @contextmenu.prevent="handleContextMenu"
            />
            <div v-if="streamError" class="stream-error">{{ streamError }}</div>
          </div>
        </div>

        <aside class="side-pane">
          <div class="panel-section">
            <div class="panel-heading">
              <span>定位框层</span>
              <el-switch v-model="layerVisible" size="small" active-text="显示" inactive-text="隐藏" />
            </div>
            <div class="tool-row">
              <el-button :icon="Delete" size="small" :disabled="!selectedBox" @click="deleteSelectedBox">删除</el-button>
              <el-button :icon="Close" size="small" :disabled="!boxes.length" @click="clearBoxes">清空</el-button>
              <el-button :icon="DocumentCopy" size="small" :disabled="!boxes.length" @click="copyBoxes">复制</el-button>
            </div>
          </div>

          <div class="panel-section">
            <div class="panel-heading">
              <span>选中框</span>
              <span class="muted">{{ selectedBox ? selectedBox.name : '未选中' }}</span>
            </div>
            <div v-if="selectedBox" class="coord-grid">
              <span>x</span><code>{{ Math.round(selectedBox.x) }}</code>
              <span>y</span><code>{{ Math.round(selectedBox.y) }}</code>
              <span>w</span><code>{{ Math.round(selectedBox.w) }}</code>
              <span>h</span><code>{{ Math.round(selectedBox.h) }}</code>
            </div>
            <div v-else class="empty-note">在画面上拖拽新增框，点击已有框选中。</div>
          </div>

          <div class="panel-section box-list-section">
            <div class="panel-heading">
              <span>框列表</span>
              <span class="muted">{{ boxes.length }} 项</span>
            </div>
            <div v-if="boxes.length" class="box-list">
              <button
                v-for="(box, index) in boxes"
                :key="box.id"
                type="button"
                class="box-row"
                :class="{ 'is-active': selectedBoxId === box.id }"
                @click="selectBox(box.id)"
              >
                <span class="box-index">{{ index + 1 }}</span>
                <el-input v-model="box.name" size="small" @click.stop @input="drawOverlay" />
                <code>{{ formatBox(box) }}</code>
              </button>
            </div>
            <div v-else class="empty-note">暂无定位框</div>
          </div>
        </aside>
      </div>

      <div class="statusbar">
        <span>{{ streamEnabled ? '流已连接' : '流已暂停' }}</span>
        <span>FPS {{ fps }}</span>
        <span>质量 {{ quality }}</span>
        <span>Delete 删除，H 隐藏，Z 撤销</span>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import {
  Close,
  Delete,
  DocumentCopy,
  Refresh,
  VideoPause,
  VideoPlay,
} from '@element-plus/icons-vue';

interface OverlayBox {
  id: string;
  name: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

interface DraftState {
  pointerId: number;
  startX: number;
  startY: number;
}

const targetTitle = ref('1249152866');
const cropText = ref('0,49,4,4');
const trimBorderText = ref('0,0,0,0');
const captureArea = ref<'outer' | 'client'>('outer');
const rotateDegrees = ref<'0' | '90' | '180' | '270'>('90');
const fps = ref(10);
const quality = ref(80);
const autoDismissPopup = ref(true);
const streamEnabled = ref(true);
const streamNonce = ref(Date.now());
const streamError = ref('');
const layerVisible = ref(true);

const streamImageRef = ref<HTMLImageElement | null>(null);
const imageWrapRef = ref<HTMLDivElement | null>(null);
const overlayCanvasRef = ref<HTMLCanvasElement | null>(null);
const naturalWidth = ref(0);
const naturalHeight = ref(0);
const selectedBoxId = ref<string | null>(null);
const boxes = ref<OverlayBox[]>([]);
const draftState = ref<DraftState | null>(null);
const draftBox = ref<OverlayBox | null>(null);

let resizeObserver: ResizeObserver | null = null;

const selectedBox = computed(() => boxes.value.find((box) => box.id === selectedBoxId.value) ?? null);
const naturalSizeText = computed(() => {
  if (!naturalWidth.value || !naturalHeight.value) return '等待画面';
  return `${naturalWidth.value} x ${naturalHeight.value}`;
});

const streamUrl = computed(() => {
  const params = new URLSearchParams({
    title: targetTitle.value.trim(),
    fps: String(fps.value),
    quality: String(quality.value),
    mode: 'screen',
    area: captureArea.value,
    crop: cropText.value.trim(),
    trim_border: trimBorderText.value.trim(),
    rotate: rotateDegrees.value,
    auto_dismiss_popup: autoDismissPopup.value ? 'true' : 'false',
    popup_check_interval: '3',
    nonce: String(streamNonce.value),
  });
  return `/api/fanxiu/game-window/stream?${params.toString()}`;
});

const clamp = (value: number, min: number, max: number) => Math.min(Math.max(value, min), max);

const normalizeBox = (box: OverlayBox): OverlayBox => {
  const x1 = clamp(Math.min(box.x, box.x + box.w), 0, naturalWidth.value || Number.MAX_SAFE_INTEGER);
  const y1 = clamp(Math.min(box.y, box.y + box.h), 0, naturalHeight.value || Number.MAX_SAFE_INTEGER);
  const x2 = clamp(Math.max(box.x, box.x + box.w), 0, naturalWidth.value || Number.MAX_SAFE_INTEGER);
  const y2 = clamp(Math.max(box.y, box.y + box.h), 0, naturalHeight.value || Number.MAX_SAFE_INTEGER);
  return {
    ...box,
    x: Math.round(x1),
    y: Math.round(y1),
    w: Math.round(Math.max(0, x2 - x1)),
    h: Math.round(Math.max(0, y2 - y1)),
  };
};

const getCanvasDisplaySize = () => {
  const canvas = overlayCanvasRef.value;
  if (!canvas) return { width: 0, height: 0 };
  const rect = canvas.getBoundingClientRect();
  return { width: rect.width, height: rect.height };
};

const drawBox = (
  ctx: CanvasRenderingContext2D,
  box: OverlayBox,
  displayWidth: number,
  displayHeight: number,
  options: { active?: boolean; draft?: boolean } = {},
) => {
  if (!naturalWidth.value || !naturalHeight.value) return;
  const scaleX = displayWidth / naturalWidth.value;
  const scaleY = displayHeight / naturalHeight.value;
  const x = box.x * scaleX;
  const y = box.y * scaleY;
  const w = box.w * scaleX;
  const h = box.h * scaleY;

  ctx.save();
  ctx.lineWidth = options.active ? 2 : 1.5;
  ctx.strokeStyle = options.draft ? '#e6a23c' : (options.active ? '#ff4d4f' : '#409eff');
  ctx.fillStyle = options.active ? 'rgba(255, 77, 79, 0.12)' : 'rgba(64, 158, 255, 0.08)';
  if (options.draft) ctx.setLineDash([6, 4]);
  ctx.fillRect(x, y, w, h);
  ctx.strokeRect(x, y, w, h);
  if (!options.draft) {
    const label = box.name || '未命名';
    ctx.font = '12px sans-serif';
    const labelWidth = ctx.measureText(label).width + 10;
    ctx.fillStyle = options.active ? '#ff4d4f' : '#409eff';
    ctx.fillRect(x, Math.max(0, y - 20), labelWidth, 18);
    ctx.fillStyle = '#fff';
    ctx.fillText(label, x + 5, Math.max(13, y - 7));
  }
  ctx.restore();
};

const drawOverlay = () => {
  const canvas = overlayCanvasRef.value;
  if (!canvas) return;

  const { width, height } = getCanvasDisplaySize();
  const dpr = window.devicePixelRatio || 1;
  const pixelWidth = Math.max(1, Math.round(width * dpr));
  const pixelHeight = Math.max(1, Math.round(height * dpr));
  if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
    canvas.width = pixelWidth;
    canvas.height = pixelHeight;
  }

  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  if (!layerVisible.value) return;

  boxes.value.forEach((box) => {
    drawBox(ctx, box, width, height, { active: selectedBoxId.value === box.id });
  });
  if (draftBox.value) {
    drawBox(ctx, normalizeBox(draftBox.value), width, height, { draft: true });
  }
};

const syncCanvas = () => {
  const canvas = overlayCanvasRef.value;
  const wrap = imageWrapRef.value;
  if (!canvas || !wrap) return;
  const rect = wrap.getBoundingClientRect();
  canvas.style.width = `${rect.width}px`;
  canvas.style.height = `${rect.height}px`;
  drawOverlay();
};

const handleImageLoad = () => {
  const image = streamImageRef.value;
  if (!image) return;
  streamError.value = '';
  naturalWidth.value = image.naturalWidth;
  naturalHeight.value = image.naturalHeight;
  void nextTick(syncCanvas);
};

const handleStreamError = () => {
  streamError.value = '未获取到画面，检查目标窗口标题和后端日志。';
};

const restartStream = () => {
  streamError.value = '';
  streamEnabled.value = true;
  streamNonce.value = Date.now();
  void nextTick(syncCanvas);
};

const toggleStream = () => {
  streamEnabled.value = !streamEnabled.value;
  if (streamEnabled.value) restartStream();
};

const getFramePoint = (event: PointerEvent | MouseEvent) => {
  const canvas = overlayCanvasRef.value;
  if (!canvas || !naturalWidth.value || !naturalHeight.value) return null;
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;
  return {
    x: clamp((event.clientX - rect.left) * naturalWidth.value / rect.width, 0, naturalWidth.value),
    y: clamp((event.clientY - rect.top) * naturalHeight.value / rect.height, 0, naturalHeight.value),
  };
};

const findBoxAt = (x: number, y: number) => {
  for (let index = boxes.value.length - 1; index >= 0; index -= 1) {
    const box = boxes.value[index];
    if (x >= box.x && x <= box.x + box.w && y >= box.y && y <= box.y + box.h) {
      return index;
    }
  }
  return -1;
};

const selectBox = (id: string | null) => {
  selectedBoxId.value = id;
  drawOverlay();
};

const handlePointerDown = (event: PointerEvent) => {
  if (event.button !== 0) return;
  const point = getFramePoint(event);
  if (!point) return;

  const hitIndex = findBoxAt(point.x, point.y);
  if (hitIndex >= 0) {
    selectBox(boxes.value[hitIndex].id);
    return;
  }

  selectedBoxId.value = null;
  draftState.value = {
    pointerId: event.pointerId,
    startX: point.x,
    startY: point.y,
  };
  draftBox.value = {
    id: 'draft',
    name: '',
    x: point.x,
    y: point.y,
    w: 0,
    h: 0,
  };
  overlayCanvasRef.value?.setPointerCapture(event.pointerId);
  drawOverlay();
};

const handlePointerMove = (event: PointerEvent) => {
  const state = draftState.value;
  if (!state || state.pointerId !== event.pointerId) return;
  const point = getFramePoint(event);
  if (!point) return;
  draftBox.value = {
    id: 'draft',
    name: '',
    x: state.startX,
    y: state.startY,
    w: point.x - state.startX,
    h: point.y - state.startY,
  };
  drawOverlay();
};

const finishDraft = () => {
  const normalized = draftBox.value ? normalizeBox(draftBox.value) : null;
  draftState.value = null;
  draftBox.value = null;
  if (!normalized || normalized.w < 4 || normalized.h < 4) {
    drawOverlay();
    return;
  }

  const nextBox = {
    ...normalized,
    id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
    name: `框${boxes.value.length + 1}`,
  };
  boxes.value.push(nextBox);
  selectedBoxId.value = nextBox.id;
  drawOverlay();
};

const handlePointerUp = (event: PointerEvent) => {
  if (draftState.value?.pointerId !== event.pointerId) return;
  overlayCanvasRef.value?.releasePointerCapture(event.pointerId);
  finishDraft();
};

const handlePointerLeave = (event: PointerEvent) => {
  if (draftState.value?.pointerId !== event.pointerId) return;
  finishDraft();
};

const handleContextMenu = (event: MouseEvent) => {
  const point = getFramePoint(event);
  if (!point) return;
  const hitIndex = findBoxAt(point.x, point.y);
  if (hitIndex < 0) return;
  const [removed] = boxes.value.splice(hitIndex, 1);
  if (selectedBoxId.value === removed.id) selectedBoxId.value = null;
  drawOverlay();
};

const deleteSelectedBox = () => {
  if (!selectedBoxId.value) return;
  boxes.value = boxes.value.filter((box) => box.id !== selectedBoxId.value);
  selectedBoxId.value = null;
  drawOverlay();
};

const clearBoxes = async () => {
  if (!boxes.value.length) return;
  try {
    await ElMessageBox.confirm('清空当前页面的所有定位框？', '清空定位框', {
      type: 'warning',
      confirmButtonText: '清空',
      cancelButtonText: '取消',
    });
  } catch {
    return;
  }
  boxes.value = [];
  selectedBoxId.value = null;
  drawOverlay();
};

const copyBoxes = async () => {
  const payload = {
    frame_size: [naturalWidth.value, naturalHeight.value],
    boxes: boxes.value.map((box) => ({
      name: box.name,
      preview_xywh: [Math.round(box.x), Math.round(box.y), Math.round(box.w), Math.round(box.h)],
    })),
  };
  await navigator.clipboard.writeText(JSON.stringify(payload, null, 2));
  ElMessage.success('已复制定位框 JSON');
};

const undoLastBox = () => {
  const removed = boxes.value.pop();
  if (removed && selectedBoxId.value === removed.id) selectedBoxId.value = null;
  drawOverlay();
};

const handleKeydown = (event: KeyboardEvent) => {
  const target = event.target as HTMLElement | null;
  if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return;
  if (target?.isContentEditable) return;

  if (event.key === 'Delete' || event.key === 'Backspace') {
    deleteSelectedBox();
    event.preventDefault();
    return;
  }
  if (event.key.toLowerCase() === 'h') {
    layerVisible.value = !layerVisible.value;
    event.preventDefault();
    return;
  }
  if ((event.ctrlKey && event.key.toLowerCase() === 'z') || event.key.toLowerCase() === 'z') {
    undoLastBox();
    event.preventDefault();
  }
};

const formatBox = (box: OverlayBox) => {
  return `${Math.round(box.x)},${Math.round(box.y)},${Math.round(box.w)},${Math.round(box.h)}`;
};

watch(layerVisible, drawOverlay);
watch(boxes, drawOverlay, { deep: true });

onMounted(() => {
  window.addEventListener('keydown', handleKeydown);
  window.addEventListener('resize', syncCanvas);
  resizeObserver = new ResizeObserver(syncCanvas);
  if (imageWrapRef.value) resizeObserver.observe(imageWrapRef.value);
  void nextTick(syncCanvas);
});

onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleKeydown);
  window.removeEventListener('resize', syncCanvas);
  resizeObserver?.disconnect();
  if (streamImageRef.value) streamImageRef.value.src = '';
});
</script>

<style scoped>
.game-window-page {
  min-height: 100%;
  background: #f6f8fb;
  color: #1f2937;
}

.stage-pane {
  min-height: 100%;
  display: flex;
  flex-direction: column;
}

.topbar {
  min-height: 58px;
  padding: 10px 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  background: #fff;
  border-bottom: 1px solid #e5e7eb;
}

.topbar-title {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
}

.topbar-title h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 700;
}

.topbar-title span,
.muted,
.empty-note,
.statusbar {
  color: #6b7280;
}

.stream-controls {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.title-input {
  width: 150px;
}

.crop-input {
  width: 168px;
}

.area-select {
  width: 82px;
}

.rotate-select {
  width: 92px;
}

.number-input {
  width: 92px;
}

.workspace {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(360px, 1fr) 320px;
}

.viewer-pane {
  min-width: 0;
  min-height: 0;
  padding: 16px;
  overflow: auto;
}

.image-wrap {
  position: relative;
  display: inline-block;
  line-height: 0;
  background: #111827;
  border: 1px solid #d1d5db;
}

.stream-image {
  display: block;
  max-width: calc(100vw - 580px);
  max-height: calc(100dvh - 160px);
  user-select: none;
}

.paused-placeholder {
  width: min(520px, calc(100vw - 580px));
  height: min(760px, calc(100dvh - 160px));
  display: grid;
  place-items: center;
  color: #9ca3af;
  background: #111827;
}

.overlay-canvas {
  position: absolute;
  inset: 0;
  cursor: crosshair;
  touch-action: none;
}

.stream-error {
  position: absolute;
  left: 12px;
  bottom: 12px;
  line-height: 1.4;
  padding: 8px 10px;
  color: #fef2f2;
  background: rgba(185, 28, 28, 0.88);
  border-radius: 4px;
}

.side-pane {
  min-width: 0;
  min-height: 0;
  padding: 14px;
  background: #fff;
  border-left: 1px solid #e5e7eb;
  overflow: auto;
}

.panel-section {
  padding: 12px 0;
  border-bottom: 1px solid #eef2f7;
}

.panel-section:first-child {
  padding-top: 0;
}

.panel-heading {
  min-height: 28px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  font-weight: 600;
}

.tool-row {
  margin-top: 10px;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.coord-grid {
  margin-top: 10px;
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 8px 12px;
  align-items: center;
}

.coord-grid code,
.box-row code {
  font-family: Consolas, 'Courier New', monospace;
  color: #374151;
  background: #f3f4f6;
  border-radius: 3px;
  padding: 2px 5px;
}

.empty-note {
  margin-top: 10px;
  line-height: 1.6;
}

.box-list-section {
  border-bottom: none;
}

.box-list {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.box-row {
  width: 100%;
  min-width: 0;
  padding: 6px;
  display: grid;
  grid-template-columns: 28px minmax(90px, 1fr);
  gap: 6px;
  align-items: center;
  border: 1px solid #e5e7eb;
  background: #fff;
  text-align: left;
  cursor: pointer;
}

.box-row.is-active {
  border-color: #409eff;
  background: #ecf5ff;
}

.box-row code {
  grid-column: 2;
  width: max-content;
}

.box-index {
  width: 24px;
  height: 24px;
  display: inline-grid;
  place-items: center;
  color: #fff;
  background: #409eff;
  border-radius: 4px;
  font-size: 12px;
}

.statusbar {
  min-height: 34px;
  padding: 0 16px;
  display: flex;
  align-items: center;
  gap: 18px;
  flex-wrap: wrap;
  background: #fff;
  border-top: 1px solid #e5e7eb;
  font-size: 13px;
}

@media (max-width: 980px) {
  .topbar {
    align-items: stretch;
    flex-direction: column;
  }

  .stream-controls {
    justify-content: flex-start;
  }

  .workspace {
    grid-template-columns: 1fr;
  }

  .side-pane {
    border-left: none;
    border-top: 1px solid #e5e7eb;
  }

  .stream-image,
  .paused-placeholder {
    max-width: calc(100vw - 32px);
  }
}
</style>
