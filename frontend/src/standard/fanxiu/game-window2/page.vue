<template>
  <div class="game-window-page">
    <section class="stage-pane">
      <div class="topbar">
        <div class="topbar-content">
          <h2>游戏窗口</h2>
          <div class="stream-controls">
            <div class="control-row">
              <div class="control-group source-controls">
                <div class="control-field">
                  <span class="control-label">设备</span>
                  <el-select
                    v-model="selectedEntryId"
                    class="device-select"
                    size="small"
                    placeholder="选择设备"
                    @change="handleEntryChange"
                  >
                    <el-option
                      v-for="device in devices"
                      :key="device.id"
                      :label="device.name"
                      :value="device.id"
                    />
                  </el-select>
                </div>
                <div class="control-field">
                  <span class="control-label">窗口</span>
                  <el-select
                    v-model="selectedWindowKey"
                    class="window-select"
                    size="small"
                    placeholder="选择窗口"
                    @change="handleWindowChange"
                  >
                    <el-option
                      v-for="scene in windowScenes"
                      :key="scene.key"
                      :label="scene.label"
                      :value="scene.key"
                    />
                  </el-select>
                </div>
                <el-button
                  class="connection-button"
                  :type="connectionButtonType"
                  :plain="!connectionReady"
                  :icon="Refresh"
                  size="small"
                  :loading="connectionButtonLoading"
                  :disabled="!selectedEntryId"
                  @click="connectWindow"
                >
                  {{ connectionButtonText }}
                </el-button>
              </div>
            </div>
            <div class="control-row option-row">
              <div class="control-group option-controls">
                <div class="control-field">
                  <span class="control-label">分辨率</span>
                  <span class="size-value">{{ naturalSizeText }}</span>
                </div>
                <div class="control-field">
                  <span class="control-label">画面裁边</span>
                  <el-input
                    v-model="trimBorderText"
                    class="crop-input"
                    size="small"
                    placeholder="左,上,右,下"
                    @keyup.enter="restartStream"
                  />
                </div>
                <div class="control-field">
                  <span class="control-label">旋转</span>
                  <el-select v-model="rotateDegrees" class="rotate-select" size="small" @change="restartStream">
                    <el-option label="0°" value="0" />
                    <el-option label="90°" value="90" />
                    <el-option label="180°" value="180" />
                    <el-option label="270°" value="270" />
                  </el-select>
                </div>
                <div class="control-field">
                  <span class="control-label">FPS</span>
                  <el-input-number v-model="fps" class="number-input" size="small" :min="1" :max="30" controls-position="right" @change="restartStream" />
                </div>
                <div class="control-field">
                  <span class="control-label">质量</span>
                  <el-input-number v-model="quality" class="number-input" size="small" :min="1" :max="100" controls-position="right" @change="restartStream" />
                </div>
              </div>
            </div>
            <div class="control-row switch-row">
              <div v-if="selectedWindowKey === 'sunlogin'" class="switch-field">
                <span class="control-label">自动关向日葵广告弹窗</span>
                <el-switch
                  v-model="autoDismissPopup"
                  size="small"
                  @change="restartStream"
                />
              </div>
              <div class="switch-field">
                <el-checkbox
                  v-model="controlEnabled"
                  class="compact-checkbox"
                  :disabled="!selectedEntryId || !streamEnabled"
                >
                  交互操作
                </el-checkbox>
                <el-tooltip content="支持在画面中用鼠标远程操作" placement="top">
                  <span class="help-mark">?</span>
                </el-tooltip>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="workspace">
        <div class="viewer-pane">
          <div ref="imageWrapRef" class="image-wrap" :class="{ 'is-control-enabled': controlEnabled }">
            <img
              v-if="streamEnabled && streamUrl"
              ref="streamImageRef"
              class="stream-image"
              :src="streamUrl"
              alt="凡修云手机窗口"
              draggable="false"
              @load="handleImageLoad"
              @error="handleStreamError"
            />
            <div v-else class="paused-placeholder">{{ placeholderText }}</div>
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
            <div class="shortcut-note">Delete 删除选中，H 隐藏/显示，Z 撤销</div>
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

    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ElMessage, ElMessageBox } from 'element-plus';
import {
  Close,
  Delete,
  DocumentCopy,
  Refresh,
} from '@element-plus/icons-vue';
import { clickFanxiuGameWindow2, createFanxiuGameWindow2StreamToken } from '@/api/fanxiu';
import {
  fetchRuntimeStatus,
  triggerRuntimeItem,
  type RuntimeItem,
  type RuntimeStatusResponse,
} from '@/api/runtime';
import { taskStore, type Device } from '@/store/taskStore';

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

interface ControlClickState {
  pointerId: number;
  frameX: number;
  frameY: number;
  clientX: number;
  clientY: number;
}

type WindowSceneKey = 'star-cloud-phone' | 'sunlogin';
type CaptureArea = 'outer' | 'client';
type RotateDegrees = '0' | '90' | '180' | '270';

interface WindowSceneDefaults {
  targetTitle: string;
  cropText: string;
  captureArea: CaptureArea;
  trimBorderText: string;
  rotateDegrees: RotateDegrees;
  fps: number;
  quality: number;
  autoDismissPopup: boolean;
}

interface WindowSceneConfig {
  trimBorderText: string;
  rotateDegrees: RotateDegrees;
  fps: number;
  quality: number;
  autoDismissPopup: boolean;
}

interface WindowScene {
  key: WindowSceneKey;
  label: string;
  defaults: WindowSceneDefaults;
}

const route = useRoute();
const router = useRouter();
const DEVICE_STORAGE_KEY = 'fanxiu.gameWindow2.entryId';
const WINDOW_STORAGE_KEY = 'fanxiu.gameWindow2.windowKey';
const WINDOW_CONFIG_STORAGE_PREFIX = 'fanxiu.gameWindow2.windowConfig';
const GAME_WINDOW_SERVICE_KEY = 'fanxiu-game-window';
const windowScenes: WindowScene[] = [
  {
    key: 'star-cloud-phone',
    label: '星星云手机',
    defaults: {
      targetTitle: '云手机',
      cropText: '0,0,0,0',
      trimBorderText: '0,0,0,0',
      captureArea: 'client',
      rotateDegrees: '0',
      fps: 12,
      quality: 82,
      autoDismissPopup: false,
    },
  },
  {
    key: 'sunlogin',
    label: '向日葵',
    defaults: {
      targetTitle: '1249152866',
      cropText: '0,49,4,4',
      trimBorderText: '0,0,0,0',
      captureArea: 'outer',
      rotateDegrees: '90',
      fps: 10,
      quality: 80,
      autoDismissPopup: true,
    },
  },
];

const devices = computed(() => taskStore.devices);
const selectedEntryId = ref('');
const selectedWindowKey = ref<WindowSceneKey>('star-cloud-phone');
const runtimeStatus = ref<RuntimeStatusResponse | null>(null);
const runtimeLoading = ref(false);
const connectionLoading = ref(false);

const trimBorderText = ref('0,0,0,0');
const rotateDegrees = ref<RotateDegrees>('0');
const fps = ref(12);
const quality = ref(82);
const autoDismissPopup = ref(false);
const streamEnabled = ref(true);
const streamNonce = ref(Date.now());
const streamError = ref('');
const streamToken = ref('');
const streamTokenExpiresAt = ref(0);
const streamTokenLoading = ref(false);
const layerVisible = ref(true);
const controlEnabled = ref(false);

const streamImageRef = ref<HTMLImageElement | null>(null);
const imageWrapRef = ref<HTMLDivElement | null>(null);
const overlayCanvasRef = ref<HTMLCanvasElement | null>(null);
const naturalWidth = ref(0);
const naturalHeight = ref(0);
const selectedBoxId = ref<string | null>(null);
const boxes = ref<OverlayBox[]>([]);
const draftState = ref<DraftState | null>(null);
const draftBox = ref<OverlayBox | null>(null);
const controlClickState = ref<ControlClickState | null>(null);

let resizeObserver: ResizeObserver | null = null;
let pollTimer: number | null = null;
let tokenRequestSeq = 0;
let lastInputErrorAt = 0;
let isApplyingWindowConfig = false;

const selectedDevice = computed<Device | null>(() => (
  devices.value.find((device) => device.id === selectedEntryId.value) ?? null
));
const selectedWindowScene = computed(() => (
  windowScenes.find((scene) => scene.key === selectedWindowKey.value) ?? windowScenes[0]
));
const targetTitle = computed(() => selectedWindowScene.value.defaults.targetTitle);
const cropText = computed(() => selectedWindowScene.value.defaults.cropText);
const captureArea = computed(() => selectedWindowScene.value.defaults.captureArea);
const serviceItem = computed<RuntimeItem | null>(() => (
  runtimeStatus.value?.items.find((item) => item.source === 'builtin' && item.key === GAME_WINDOW_SERVICE_KEY) ?? null
));
const serviceActive = computed(() => Boolean(serviceItem.value?.active));
const selectedBox = computed(() => boxes.value.find((box) => box.id === selectedBoxId.value) ?? null);
const controlReady = computed(() => Boolean(
  controlEnabled.value
  && selectedEntryId.value
  && streamEnabled.value
  && naturalWidth.value
  && naturalHeight.value
));
const naturalSizeText = computed(() => {
  if (!selectedEntryId.value) return '未连接';
  if (!naturalWidth.value || !naturalHeight.value) return '等待画面';
  return `${naturalHeight.value} x ${naturalWidth.value}`;
});
const placeholderText = computed(() => {
  if (!selectedEntryId.value) return '选择设备';
  if (!streamEnabled.value) return '画面已暂停';
  if (!streamToken.value) return '正在准备画面流';
  return '等待画面';
});

const streamUrl = computed(() => {
  if (!selectedEntryId.value || !streamToken.value) return '';
  const params = new URLSearchParams({
    token: streamToken.value,
    title: targetTitle.value.trim(),
    fps: String(fps.value),
    quality: String(quality.value),
    mode: 'screen',
    area: captureArea.value,
    crop: cropText.value.trim(),
    trim_border: trimBorderText.value.trim(),
    rotate: rotateDegrees.value,
    auto_dismiss_popup: selectedWindowKey.value === 'sunlogin' && autoDismissPopup.value ? 'true' : 'false',
    popup_check_interval: '3',
    nonce: String(streamNonce.value),
  });
  return `/api/fanxiu/game-window2/stream?${params.toString()}`;
});
const connectionReady = computed(() => Boolean(
  selectedEntryId.value
  && serviceActive.value
  && streamEnabled.value
  && streamUrl.value
  && naturalWidth.value
  && naturalHeight.value
  && !streamError.value
));
const connectionButtonLoading = computed(() => connectionLoading.value || streamTokenLoading.value);
const connectionButtonType = computed(() => (connectionReady.value ? 'success' : 'info'));
const connectionButtonText = computed(() => {
  if (connectionReady.value) return '运行中';
  if (connectionButtonLoading.value || (streamEnabled.value && streamToken.value && !streamError.value)) return '连接中';
  return '连接';
});

const clamp = (value: number, min: number, max: number) => Math.min(Math.max(value, min), max);

const getErrorMessage = (error: unknown) => {
  if (typeof error === 'object' && error && 'response' in error) {
    const maybeError = error as { response?: { data?: { detail?: string } }, message?: string };
    return maybeError.response?.data?.detail || maybeError.message || '请求失败';
  }
  return error instanceof Error ? error.message : '请求失败';
};

const getQueryEntryId = () => {
  const raw = route.query.entry_id;
  return Array.isArray(raw) ? raw[0] || '' : raw || '';
};

const isWindowSceneKey = (value: string): value is WindowSceneKey => {
  return windowScenes.some((scene) => scene.key === value);
};

const getQueryWindowKey = () => {
  const raw = route.query.window;
  const value = Array.isArray(raw) ? raw[0] || '' : raw || '';
  return isWindowSceneKey(value) ? value : '';
};

const chooseDefaultEntryId = () => {
  const queryEntryId = getQueryEntryId();
  if (queryEntryId && devices.value.some((device) => device.id === queryEntryId)) return queryEntryId;
  const savedEntryId = window.localStorage.getItem(DEVICE_STORAGE_KEY) || '';
  if (savedEntryId && devices.value.some((device) => device.id === savedEntryId)) return savedEntryId;
  const mi15 = devices.value.find((device) => {
    const haystack = `${device.id} ${device.device_id} ${device.name}`.toLowerCase();
    return haystack.includes('mi15');
  });
  return mi15?.id || devices.value[0]?.id || '';
};

const chooseDefaultWindowKey = (): WindowSceneKey => {
  const queryWindowKey = getQueryWindowKey();
  if (queryWindowKey) return queryWindowKey;
  const savedWindowKey = window.localStorage.getItem(WINDOW_STORAGE_KEY) || '';
  if (isWindowSceneKey(savedWindowKey)) return savedWindowKey;
  return 'star-cloud-phone';
};

const normalizeWindowConfig = (raw: Partial<WindowSceneConfig>, fallback: WindowSceneDefaults): WindowSceneConfig => {
  const rotate = raw.rotateDegrees;
  const nextFps = Number(raw.fps ?? fallback.fps);
  const nextQuality = Number(raw.quality ?? fallback.quality);
  return {
    trimBorderText: raw.trimBorderText || fallback.trimBorderText,
    rotateDegrees: rotate === '0' || rotate === '90' || rotate === '180' || rotate === '270'
      ? rotate
      : fallback.rotateDegrees,
    fps: Number.isFinite(nextFps) ? Math.min(Math.max(Math.round(nextFps), 1), 30) : fallback.fps,
    quality: Number.isFinite(nextQuality) ? Math.min(Math.max(Math.round(nextQuality), 1), 100) : fallback.quality,
    autoDismissPopup: typeof raw.autoDismissPopup === 'boolean' ? raw.autoDismissPopup : fallback.autoDismissPopup,
  };
};

const getWindowConfigStorageKey = (entryId: string, windowKey: WindowSceneKey) => {
  return `${WINDOW_CONFIG_STORAGE_PREFIX}.${entryId || 'default'}.${windowKey}`;
};

const readWindowConfig = (entryId: string, windowKey: WindowSceneKey): WindowSceneConfig => {
  const scene = windowScenes.find((item) => item.key === windowKey) ?? windowScenes[0];
  const rawText = window.localStorage.getItem(getWindowConfigStorageKey(entryId, windowKey));
  if (!rawText) return normalizeWindowConfig({}, scene.defaults);
  try {
    const raw = JSON.parse(rawText) as Partial<WindowSceneConfig>;
    return normalizeWindowConfig(raw, scene.defaults);
  } catch {
    return normalizeWindowConfig({}, scene.defaults);
  }
};

const currentWindowConfig = (): WindowSceneConfig => ({
  trimBorderText: trimBorderText.value,
  rotateDegrees: rotateDegrees.value,
  fps: Number(fps.value) || selectedWindowScene.value.defaults.fps,
  quality: Number(quality.value) || selectedWindowScene.value.defaults.quality,
  autoDismissPopup: autoDismissPopup.value,
});

const persistWindowConfig = () => {
  if (isApplyingWindowConfig || !selectedWindowKey.value) return;
  const key = getWindowConfigStorageKey(selectedEntryId.value, selectedWindowKey.value);
  window.localStorage.setItem(key, JSON.stringify(currentWindowConfig()));
};

const applyWindowConfig = () => {
  isApplyingWindowConfig = true;
  const config = readWindowConfig(selectedEntryId.value, selectedWindowKey.value);
  trimBorderText.value = config.trimBorderText;
  rotateDegrees.value = config.rotateDegrees;
  fps.value = config.fps;
  quality.value = config.quality;
  autoDismissPopup.value = config.autoDismissPopup;
  window.requestAnimationFrame(() => {
    isApplyingWindowConfig = false;
  });
};

const persistEntrySelection = (entryId: string) => {
  if (entryId) {
    window.localStorage.setItem(DEVICE_STORAGE_KEY, entryId);
  } else {
    window.localStorage.removeItem(DEVICE_STORAGE_KEY);
  }
  const nextQuery = { ...route.query };
  if (entryId) {
    nextQuery.entry_id = entryId;
  } else {
    delete nextQuery.entry_id;
  }
  nextQuery.window = selectedWindowKey.value;
  void router.replace({ path: route.path, query: nextQuery });
};

const persistWindowSelection = () => {
  window.localStorage.setItem(WINDOW_STORAGE_KEY, selectedWindowKey.value);
  const nextQuery = { ...route.query, window: selectedWindowKey.value };
  if (selectedEntryId.value) nextQuery.entry_id = selectedEntryId.value;
  void router.replace({ path: route.path, query: nextQuery });
};

const refreshStreamToken = async () => {
  const entryId = selectedEntryId.value;
  if (!entryId) {
    streamToken.value = '';
    streamTokenExpiresAt.value = 0;
    return;
  }
  const requestSeq = ++tokenRequestSeq;
  streamTokenLoading.value = true;
  try {
    const payload = await createFanxiuGameWindow2StreamToken(entryId);
    if (requestSeq !== tokenRequestSeq || selectedEntryId.value !== entryId) return;
    streamToken.value = payload.token;
    streamTokenExpiresAt.value = Date.now() + Math.max(60, payload.expires_in_seconds - 30) * 1000;
  } catch (error) {
    if (requestSeq === tokenRequestSeq) {
      streamToken.value = '';
      streamTokenExpiresAt.value = 0;
      ElMessage.error(getErrorMessage(error));
    }
  } finally {
    if (requestSeq === tokenRequestSeq) streamTokenLoading.value = false;
  }
};

const ensureStreamToken = async () => {
  if (streamToken.value && streamTokenExpiresAt.value > Date.now() + 60_000) return;
  await refreshStreamToken();
};

const loadRuntimeStatus = async (silent = false) => {
  const entryId = selectedEntryId.value;
  if (!entryId) {
    runtimeStatus.value = null;
    return;
  }
  runtimeLoading.value = !silent;
  try {
    runtimeStatus.value = await fetchRuntimeStatus(entryId);
  } catch (error) {
    if (!silent) ElMessage.error(getErrorMessage(error));
  } finally {
    runtimeLoading.value = false;
  }
};

const handleEntryChange = async () => {
  controlEnabled.value = false;
  controlClickState.value = null;
  naturalWidth.value = 0;
  naturalHeight.value = 0;
  streamError.value = '';
  streamToken.value = '';
  streamTokenExpiresAt.value = 0;
  persistEntrySelection(selectedEntryId.value);
  applyWindowConfig();
  await Promise.all([refreshStreamToken(), loadRuntimeStatus()]);
  restartStream();
};

const handleWindowChange = async () => {
  controlEnabled.value = false;
  controlClickState.value = null;
  naturalWidth.value = 0;
  naturalHeight.value = 0;
  streamError.value = '';
  persistWindowSelection();
  applyWindowConfig();
  await restartStream();
};

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
  const message = '未获取到画面，检查设备入口、画面流服务和窗口场景。';
  streamError.value = message;
  ElMessage.error(message);
};

const restartStream = async () => {
  streamError.value = '';
  streamEnabled.value = true;
  await ensureStreamToken();
  streamNonce.value = Date.now();
  void nextTick(syncCanvas);
};

const connectWindow = async () => {
  if (!selectedEntryId.value) return;
  connectionLoading.value = true;
  try {
    if (!serviceActive.value) {
      await triggerRuntimeItem(selectedEntryId.value, 'builtin', GAME_WINDOW_SERVICE_KEY);
    }
    await loadRuntimeStatus(true);
    await restartStream();
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  } finally {
    connectionLoading.value = false;
  }
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

const sendRemoteClick = async (point: { x: number; y: number }) => {
  if (!selectedEntryId.value || !naturalWidth.value || !naturalHeight.value) return;
  try {
    await clickFanxiuGameWindow2({
      entry_id: selectedEntryId.value,
      x: Math.round(point.x),
      y: Math.round(point.y),
      title: targetTitle.value.trim(),
      mode: 'screen',
      area: captureArea.value,
      crop: cropText.value.trim(),
      trim_border: trimBorderText.value.trim(),
      rotate: rotateDegrees.value,
      fixed_width: 0,
      fixed_height: 0,
      frame_width: naturalWidth.value,
      frame_height: naturalHeight.value,
    });
  } catch (error) {
    const now = Date.now();
    if (now - lastInputErrorAt > 1500) {
      lastInputErrorAt = now;
      ElMessage.error(getErrorMessage(error));
    }
  }
};

const beginControlClick = (event: PointerEvent) => {
  if (event.button !== 0 || !controlReady.value) return;
  const point = getFramePoint(event);
  if (!point) return;
  event.preventDefault();
  event.stopPropagation();
  controlClickState.value = {
    pointerId: event.pointerId,
    frameX: point.x,
    frameY: point.y,
    clientX: event.clientX,
    clientY: event.clientY,
  };
  overlayCanvasRef.value?.setPointerCapture(event.pointerId);
};

const finishControlClick = (event: PointerEvent) => {
  const state = controlClickState.value;
  if (!state || state.pointerId !== event.pointerId) return;
  event.preventDefault();
  event.stopPropagation();
  overlayCanvasRef.value?.releasePointerCapture(event.pointerId);
  controlClickState.value = null;
  const moveDistance = Math.hypot(event.clientX - state.clientX, event.clientY - state.clientY);
  if (moveDistance > 8) return;
  const point = getFramePoint(event) ?? { x: state.frameX, y: state.frameY };
  void sendRemoteClick(point);
};

const cancelControlClick = (event: PointerEvent) => {
  const state = controlClickState.value;
  if (!state || state.pointerId !== event.pointerId) return;
  overlayCanvasRef.value?.releasePointerCapture(event.pointerId);
  controlClickState.value = null;
};

const selectBox = (id: string | null) => {
  selectedBoxId.value = id;
  drawOverlay();
};

const handlePointerDown = (event: PointerEvent) => {
  if (controlEnabled.value) {
    beginControlClick(event);
    return;
  }
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
  if (controlClickState.value?.pointerId === event.pointerId) return;
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
  if (controlClickState.value?.pointerId === event.pointerId) {
    finishControlClick(event);
    return;
  }
  if (draftState.value?.pointerId !== event.pointerId) return;
  overlayCanvasRef.value?.releasePointerCapture(event.pointerId);
  finishDraft();
};

const handlePointerLeave = (event: PointerEvent) => {
  if (controlClickState.value?.pointerId === event.pointerId) {
    cancelControlClick(event);
    return;
  }
  if (draftState.value?.pointerId !== event.pointerId) return;
  finishDraft();
};

const handleContextMenu = (event: MouseEvent) => {
  if (controlEnabled.value) return;
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

const startPolling = () => {
  stopPolling();
  pollTimer = window.setInterval(() => {
    void loadRuntimeStatus(true);
  }, 5000);
};

const stopPolling = () => {
  if (pollTimer) {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }
};

watch(layerVisible, drawOverlay);
watch(boxes, drawOverlay, { deep: true });
watch(
  [trimBorderText, rotateDegrees, fps, quality, autoDismissPopup],
  persistWindowConfig,
);

onMounted(async () => {
  window.addEventListener('keydown', handleKeydown);
  window.addEventListener('resize', syncCanvas);
  resizeObserver = new ResizeObserver(syncCanvas);
  if (imageWrapRef.value) resizeObserver.observe(imageWrapRef.value);

  await taskStore.fetchDevices();
  selectedEntryId.value = chooseDefaultEntryId();
  selectedWindowKey.value = chooseDefaultWindowKey();
  applyWindowConfig();
  if (selectedEntryId.value) {
    persistEntrySelection(selectedEntryId.value);
    persistWindowSelection();
    await Promise.all([refreshStreamToken(), loadRuntimeStatus()]);
  }
  startPolling();
  void nextTick(syncCanvas);
});

onBeforeUnmount(() => {
  stopPolling();
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
  min-height: 78px;
  padding: 10px 16px 12px;
  display: flex;
  align-items: flex-start;
  justify-content: flex-start;
  background: #fff;
  border-bottom: 1px solid #e5e7eb;
}

.topbar-content {
  width: 100%;
  min-width: 0;
  flex: 1 1 auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.topbar-content h2 {
  margin: 0;
  color: #111827;
  font-size: 20px;
  font-weight: 700;
  line-height: 1.25;
  white-space: nowrap;
}

.muted,
.empty-note,
.shortcut-note {
  color: #6b7280;
}

.stream-controls {
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
}

.control-row {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px 16px;
  flex-wrap: wrap;
  justify-content: flex-start;
}

.control-group {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}

.switch-field {
  display: flex;
  align-items: center;
  gap: 6px;
}

.compact-checkbox {
  height: 22px;
  margin-right: 0;
  color: #374151;
  font-size: 12px;
}

.help-mark {
  width: 14px;
  height: 14px;
  display: inline-grid;
  place-items: center;
  color: #6b7280;
  border: 1px solid #cbd5e1;
  border-radius: 50%;
  font-size: 10px;
  line-height: 1;
  cursor: help;
}

.source-controls {
  padding-right: 0;
}

.control-field {
  display: flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
}

.control-label {
  flex: none;
  color: #6b7280;
  font-size: 12px;
  white-space: nowrap;
}

.size-value {
  min-width: 64px;
  color: #374151;
  font-size: 13px;
  white-space: nowrap;
}

.device-select {
  width: 132px;
}

.window-select {
  width: 126px;
}

.connection-button {
  min-width: 74px;
}

.crop-input {
  width: 128px;
}

.rotate-select {
  width: 56px;
}

.number-input {
  width: 58px;
}

.number-input :deep(.el-input__wrapper) {
  padding-left: 6px;
  padding-right: 24px;
}

.number-input :deep(.el-input-number__increase),
.number-input :deep(.el-input-number__decrease) {
  width: 18px;
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

.image-wrap.is-control-enabled .overlay-canvas {
  cursor: pointer;
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

.shortcut-note {
  margin-top: 8px;
  font-size: 12px;
  line-height: 1.5;
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
