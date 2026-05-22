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
                <el-button
                  class="save-frame-button"
                  :icon="Download"
                  size="small"
                  plain
                  :loading="saveFrameLoading"
                  :disabled="!selectedEntryId"
                  @click="saveCurrentFrame"
                >
                  保存帧
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
          <div class="live-workspace">
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

            <aside class="code-panel">
              <div class="code-panel-head">
                <span>伪代码</span>
                <div class="code-panel-actions">
                  <el-button size="small" :loading="pseudoCompileLoading" @click="compilePseudoCode">编译</el-button>
                  <el-button size="small" type="primary" plain :loading="pseudoStartLoading" @click="startPseudoCode">启动</el-button>
                </div>
              </div>
              <div class="code-scope-list">
                <section v-for="scopeInfo in codeCardScopes" :key="scopeInfo.scope" class="code-scope">
                  <div class="code-scope-head">
                    <span>{{ scopeInfo.label }}</span>
                    <button
                      type="button"
                      class="code-add"
                      :title="`新建${scopeInfo.label}`"
                      :aria-label="`新建${scopeInfo.label}`"
                      @click="addCodeCard(scopeInfo.scope)"
                    >
                      +
                    </button>
                  </div>
                  <div class="code-card-list">
                    <section
                      v-for="(card, index) in codeCardsByScope(scopeInfo.scope)"
                      :key="card.id"
                      class="code-card"
                      :class="{ 'is-expanded': isCodeCardExpanded(card.id) }"
                    >
                      <div v-if="isCodeCardExpanded(card.id)" class="code-card-head">
                        <el-input
                          v-model="card.title"
                          size="small"
                          placeholder="标题"
                          @input="scheduleCodeCardSave(card)"
                          @blur="saveCodeCardNow(card)"
                        />
                        <button
                          type="button"
                          class="code-card-collapse"
                          title="折叠卡片"
                          aria-label="折叠卡片"
                          @click="toggleCodeCard(card.id)"
                        >
                          ^
                        </button>
                        <button
                          type="button"
                          class="code-card-delete"
                          title="删除卡片"
                          aria-label="删除卡片"
                          @click="deleteCodeCard(card.id)"
                        >
                          -
                        </button>
                      </div>
                      <div v-else class="code-card-summary-row">
                        <button
                          type="button"
                          class="code-card-title-button"
                          @click="toggleCodeCard(card.id)"
                        >
                          <span>{{ codeCardTitle(card, scopeInfo.scope, index) }}</span>
                        </button>
                        <button
                          type="button"
                          class="code-card-delete"
                          title="删除卡片"
                          aria-label="删除卡片"
                          @click="deleteCodeCard(card.id)"
                        >
                          -
                        </button>
                      </div>
                      <el-input
                        v-if="isCodeCardExpanded(card.id)"
                        v-model="card.body"
                        class="code-card-body"
                        type="textarea"
                        placeholder="段落"
                        :autosize="{ minRows: 5, maxRows: 16 }"
                        @input="scheduleCodeCardSave(card)"
                        @blur="saveCodeCardNow(card)"
                      />
                    </section>
                    <div v-if="!codeCardsByScope(scopeInfo.scope).length && !codeCardsLoading" class="code-card-empty">
                      {{ scopeInfo.emptyText }}
                    </div>
                  </div>
                </section>
              </div>
              <div class="code-output-panel">
                <div class="code-output-tabs">
                  <button
                    type="button"
                    class="code-output-tab"
                    :class="{ 'is-active': pseudoOutputTab === 'log' }"
                    @click="pseudoOutputTab = 'log'"
                  >
                    日志
                  </button>
                  <button
                    type="button"
                    class="code-output-tab"
                    :class="{ 'is-active': pseudoOutputTab === 'result' }"
                    @click="pseudoOutputTab = 'result'"
                  >
                    结果
                  </button>
                </div>
                <pre class="code-output-box">{{ pseudoOutputText }}</pre>
              </div>
            </aside>
          </div>

          <section class="screenshot-panel">
            <div class="screenshot-head">
              <button type="button" class="screenshot-toggle" @click="toggleScreenshotPanel">
                <span class="screenshot-caret">{{ screenshotPanelOpen ? '▼' : '▶' }}</span>
                <span>截图</span>
              </button>
              <div v-if="screenshotImages.length" class="screenshot-nav">
                <el-button
                  :icon="ArrowLeft"
                  size="small"
                  circle
                  :disabled="!canSelectPreviousScreenshotImage"
                  @click="selectAdjacentScreenshotImage(-1)"
                />
                <div class="screenshot-current-item" @dblclick="startScreenshotJumpEdit">
                  <el-input
                    v-if="screenshotJumpEditing"
                    ref="screenshotJumpInputRef"
                    v-model="screenshotJumpText"
                    class="screenshot-jump-input"
                    size="small"
                    @keydown.enter.prevent="confirmScreenshotJump"
                    @keydown.esc.prevent="cancelScreenshotJump"
                    @blur="cancelScreenshotJump"
                  />
                  <span v-else class="screenshot-name" title="双击输入编号跳转">
                    {{ selectedScreenshotImage?.filename }}
                  </span>
                </div>
                <el-button
                  :icon="ArrowRight"
                  size="small"
                  circle
                  :disabled="!canSelectNextScreenshotImage"
                  @click="selectAdjacentScreenshotImage(1)"
                />
                <button
                  type="button"
                  class="screenshot-delete"
                  :disabled="screenshotDeleting"
                  title="删除这张截图"
                  aria-label="删除这张截图"
                  @click="deleteCurrentScreenshotImage"
                >
                  -
                </button>
              </div>
              <span class="screenshot-summary">{{ screenshotPanelSummary }}</span>
            </div>
            <div v-if="screenshotPanelOpen" class="screenshot-body">
              <div v-if="screenshotLoading && !screenshotImages.length" class="screenshot-empty">加载中</div>
              <div v-else-if="!screenshotImages.length" class="screenshot-empty">暂无截图</div>
              <div v-if="selectedScreenshotImage" class="screenshot-editor">
                <div class="screenshot-preview">
                  <div ref="screenshotImageWrapRef" class="screenshot-image-wrap">
                    <img
                      v-if="screenshotImageUrl"
                      ref="screenshotImageRef"
                      class="screenshot-image"
                      :src="screenshotImageUrl"
                      alt="截图"
                      draggable="false"
                      @load="handleScreenshotImageLoad"
                      @error="handleScreenshotImageError"
                    />
                    <div v-else class="screenshot-image-placeholder">加载图片</div>
                    <canvas
                      ref="screenshotOverlayCanvasRef"
                      class="screenshot-overlay-canvas"
                      @pointerdown="handleScreenshotPointerDown"
                      @pointermove="handleScreenshotPointerMove"
                      @pointerup="handleScreenshotPointerUp"
                      @pointerleave="handleScreenshotPointerLeave"
                      @contextmenu.prevent="handleScreenshotContextMenu"
                    />
                  </div>
                </div>

                <div class="screenshot-pre-panel" @contextmenu.prevent.stop="openScreenshotBoxListPanelContextMenu">
                  <div v-if="screenshotBoxes.length" class="screenshot-box-list">
                    <div
                      v-for="(box, index) in screenshotBoxes"
                      :key="box.id"
                      class="screenshot-box-row"
                      :class="{ 'is-active': selectedScreenshotBoxId === box.id }"
                      @click="selectScreenshotBox(box.id)"
                      @contextmenu.prevent.stop="openScreenshotBoxListContextMenu($event, box.id)"
                    >
                      <span class="screenshot-box-number">{{ index + 1 }}</span>
                      <el-input
                        v-model="box.name"
                        size="small"
                        @focus="selectScreenshotBox(box.id)"
                        @input="handleScreenshotBoxNameInput"
                      />
                    </div>
                  </div>
                  <div v-else class="screenshot-empty">拖拽画框</div>
                </div>
              </div>
            </div>
          </section>

          <section v-if="matchResults.length" class="match-panel">
            <div class="match-head">
              <span>匹配</span>
              <span class="match-summary">共{{ matchResults.length }}次</span>
            </div>
            <div class="match-body">
              <div class="match-preview">
                <div ref="matchImageWrapRef" class="match-image-wrap">
                  <img
                    v-if="selectedMatchResult"
                    ref="matchImageRef"
                    class="match-image"
                    :src="selectedMatchResult.imageUrl"
                    alt="匹配帧"
                    draggable="false"
                    @load="handleMatchImageLoad"
                  />
                  <canvas ref="matchOverlayCanvasRef" class="match-overlay-canvas" />
                </div>
              </div>
              <div class="match-list">
                <button
                  v-for="(entry, index) in matchResultEntries"
                  :key="entry.id"
                  type="button"
                  class="match-row"
                  :class="{
                    'is-active': selectedMatchEntry?.id === entry.id,
                    'is-fixed': entry.kind === 'fixed',
                    'is-template': entry.kind === 'template',
                  }"
                  @click="selectMatchEntry(entry.id)"
                >
                  <span class="match-number">{{ index + 1 }}</span>
                  <span class="match-name">{{ entry.name }}</span>
                  <span class="match-kind">{{ entry.label }}</span>
                  <strong class="match-score">{{ entry.similarity }}%</strong>
                </button>
              </div>
            </div>
          </section>
        </div>
      </div>

      <div
        v-if="screenshotBoxContextMenu.visible"
        class="screenshot-box-menu"
        :style="{ left: `${screenshotBoxContextMenu.x}px`, top: `${screenshotBoxContextMenu.y}px` }"
        @click.stop
        @pointerdown.stop
      >
        <button type="button" :disabled="matchingBoxId === screenshotBoxContextMenu.boxId" @click="runScreenshotBoxContextMatch">
          匹配
        </button>
      </div>

      <div
        v-if="screenshotBoxListContextMenu.visible"
        class="screenshot-box-menu"
        :style="{ left: `${screenshotBoxListContextMenu.x}px`, top: `${screenshotBoxListContextMenu.y}px` }"
        @click.stop
        @pointerdown.stop
      >
        <button v-if="screenshotBoxListContextMenu.boxId" type="button" @click="copyScreenshotBoxFromListContext">
          复制
        </button>
        <button type="button" :disabled="!copiedScreenshotBox" @click="pasteScreenshotBoxFromListContext">
          粘贴
        </button>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ElMessage, ElMessageBox } from 'element-plus';
import {
  ArrowLeft,
  ArrowRight,
  Download,
  Refresh,
} from '@element-plus/icons-vue';
import {
  clickFanxiuGameWindow2,
  compileFanxiuPseudoCode,
  createFanxiuPseudoCodeCard,
  createFanxiuGameWindow2StreamToken,
  deleteFanxiuPseudoCodeCard,
  deleteFanxiuGameWindow2Screenshot,
  dragFanxiuGameWindow2,
  getFanxiuGameWindow2MatchImage,
  getFanxiuGameWindow2Screenshot,
  getFanxiuGameWindow2PreLabel,
  listFanxiuPseudoCodeCards,
  listFanxiuGameWindow2Screenshots,
  matchFanxiuGameWindow2Screenshot,
  saveFanxiuGameWindow2Frame,
  saveFanxiuGameWindow2PreLabel,
  startFanxiuPseudoCode,
  updateFanxiuPseudoCodeCard,
  type FanxiuGameWindow2MatchBox,
  type FanxiuGameWindow2MatchResponse,
  type FanxiuGameWindow2ScreenshotItem,
  type FanxiuGameWindow2PreLabelBox,
  type FanxiuGameWindow2PreLabelPayload,
  type FanxiuPseudoCodeCard,
  type FanxiuPseudoCodeCardScope,
  type FanxiuPseudoCodeRunResponse,
} from '@/api/fanxiu';
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

type ScreenshotResizeHandle = 'top-left' | 'bottom-right';

interface ScreenshotResizeState {
  pointerId: number;
  boxId: string;
  handle: ScreenshotResizeHandle;
  original: OverlayBox;
}

interface ControlClickState {
  pointerId: number;
  frameX: number;
  frameY: number;
  clientX: number;
  clientY: number;
  startedAt: number;
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

type CodeCard = FanxiuPseudoCodeCard;
type PseudoOutputTab = 'log' | 'result';

interface CodeCardScopeInfo {
  scope: FanxiuPseudoCodeCardScope;
  label: string;
  emptyText: string;
}

interface LegacyCodeCard {
  title?: unknown;
  body?: unknown;
}

interface ScreenshotBoxContextMenu {
  visible: boolean;
  x: number;
  y: number;
  boxId: string;
}

interface CopiedScreenshotBox {
  name: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

interface MatchResult extends FanxiuGameWindow2MatchResponse {
  id: string;
  imageUrl: string;
  createdAt: number;
}

type MatchResultEntryKind = 'fixed' | 'template';

interface MatchResultEntry {
  id: string;
  kind: MatchResultEntryKind;
  label: string;
  name: string;
  similarity: number;
  result: MatchResult;
}

const route = useRoute();
const router = useRouter();
const DEVICE_STORAGE_KEY = 'fanxiu.gameWindow2.entryId';
const WINDOW_STORAGE_KEY = 'fanxiu.gameWindow2.windowKey';
const WINDOW_CONFIG_STORAGE_PREFIX = 'fanxiu.gameWindow2.windowConfig';
const SCREENSHOT_SELECTION_STORAGE_PREFIX = 'fanxiu.gameWindow2.screenshotFilename';
const LEGACY_CODE_CARDS_STORAGE_KEY = 'fanxiu.gameWindow2.codeCards';
const GAME_WINDOW_SERVICE_KEY = 'fanxiu-game-window';
const codeCardScopes: CodeCardScopeInfo[] = [
  { scope: 'guard', label: '守护', emptyText: '暂无守护' },
  { scope: 'action', label: '动作', emptyText: '暂无动作' },
];
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
const saveFrameLoading = ref(false);
const screenshotPanelOpen = ref(true);
const screenshotLoaded = ref(false);
const screenshotLoading = ref(false);
const screenshotSaving = ref(false);
const screenshotDeleting = ref(false);
const screenshotImages = ref<FanxiuGameWindow2ScreenshotItem[]>([]);
const selectedScreenshotFilename = ref('');
const screenshotImageUrl = ref('');
const screenshotNaturalWidth = ref(0);
const screenshotNaturalHeight = ref(0);
const screenshotBoxes = ref<OverlayBox[]>([]);
const selectedScreenshotBoxId = ref<string | null>(null);
const screenshotDraftState = ref<DraftState | null>(null);
const screenshotDraftBox = ref<OverlayBox | null>(null);
const screenshotResizeState = ref<ScreenshotResizeState | null>(null);
const screenshotDirty = ref(false);
const screenshotJumpEditing = ref(false);
const screenshotJumpText = ref('');
const screenshotBoxContextMenu = ref<ScreenshotBoxContextMenu>({
  visible: false,
  x: 0,
  y: 0,
  boxId: '',
});
const screenshotBoxListContextMenu = ref<ScreenshotBoxContextMenu>({
  visible: false,
  x: 0,
  y: 0,
  boxId: '',
});
const copiedScreenshotBox = ref<CopiedScreenshotBox | null>(null);
const matchingBoxId = ref<string | null>(null);
const matchResults = ref<MatchResult[]>([]);
const selectedMatchEntryId = ref('');
const codeCards = ref<CodeCard[]>([]);
const codeCardsLoading = ref(false);
const expandedCodeCardIds = ref<string[]>([]);
const pseudoCompileLoading = ref(false);
const pseudoStartLoading = ref(false);
const pseudoOutputTab = ref<PseudoOutputTab>('log');
const pseudoExecutionLog = ref('尚未执行');
const pseudoExecutionResult = ref('');

const streamImageRef = ref<HTMLImageElement | null>(null);
const imageWrapRef = ref<HTMLDivElement | null>(null);
const overlayCanvasRef = ref<HTMLCanvasElement | null>(null);
const screenshotImageRef = ref<HTMLImageElement | null>(null);
const screenshotImageWrapRef = ref<HTMLDivElement | null>(null);
const screenshotOverlayCanvasRef = ref<HTMLCanvasElement | null>(null);
const screenshotJumpInputRef = ref<{ focus: () => void } | null>(null);
const matchImageRef = ref<HTMLImageElement | null>(null);
const matchImageWrapRef = ref<HTMLDivElement | null>(null);
const matchOverlayCanvasRef = ref<HTMLCanvasElement | null>(null);
const naturalWidth = ref(0);
const naturalHeight = ref(0);
const selectedBoxId = ref<string | null>(null);
const boxes = ref<OverlayBox[]>([]);
const draftState = ref<DraftState | null>(null);
const draftBox = ref<OverlayBox | null>(null);
const controlClickState = ref<ControlClickState | null>(null);

let resizeObserver: ResizeObserver | null = null;
let pollTimer: number | null = null;
let screenshotSaveTimer: number | null = null;
let tokenRequestSeq = 0;
let lastInputErrorAt = 0;
let isApplyingWindowConfig = false;
const codeCardSaveTimers = new Map<string, number>();

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
const selectedScreenshotImage = computed(() => (
  screenshotImages.value.find((item) => item.filename === selectedScreenshotFilename.value) ?? null
));
const selectedScreenshotIndex = computed(() => (
  screenshotImages.value.findIndex((item) => item.filename === selectedScreenshotFilename.value)
));
const canSelectPreviousScreenshotImage = computed(() => selectedScreenshotIndex.value > 0);
const canSelectNextScreenshotImage = computed(() => (
  selectedScreenshotIndex.value >= 0 && selectedScreenshotIndex.value < screenshotImages.value.length - 1
));
const matchResultEntries = computed<MatchResultEntry[]>(() => {
  return matchResults.value.flatMap((result) => {
    const name = result.box.name || '未命名';
    const entries: MatchResultEntry[] = [
      {
        id: `${result.id}:fixed`,
        kind: 'fixed',
        label: '原位',
        name,
        similarity: result.fixed_similarity ?? result.similarity,
        result,
      },
    ];
    if (result.template_similarity !== undefined) {
      entries.push({
        id: `${result.id}:template`,
        kind: 'template',
        label: '模板',
        name,
        similarity: result.template_similarity,
        result,
      });
    }
    return entries;
  });
});
const selectedMatchEntry = computed(() => (
  matchResultEntries.value.find((item) => item.id === selectedMatchEntryId.value) ?? matchResultEntries.value[0] ?? null
));
const selectedMatchResult = computed(() => selectedMatchEntry.value?.result ?? null);
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
const screenshotPanelSummary = computed(() => {
  if (!selectedEntryId.value) return '未选设备';
  if (!screenshotLoaded.value) return '未加载';
  return `共${screenshotImages.value.length}张`;
});
const pseudoOutputText = computed(() => {
  if (pseudoOutputTab.value === 'result') return pseudoExecutionResult.value || '暂无结果';
  return pseudoExecutionLog.value || '暂无日志';
});

const normalizeLegacyCodeCards = (raw: unknown): Array<{ title: string; body: string }> => {
  if (!Array.isArray(raw)) return [];
  return raw.map((item) => {
    const card = item as LegacyCodeCard;
    return {
      title: typeof card.title === 'string' ? card.title : '',
      body: typeof card.body === 'string' ? card.body : '',
    };
  }).filter((card) => card.title.trim() || card.body.trim());
};

const sortCodeCards = (cards: CodeCard[]) => {
  return [...cards].sort((a, b) => (
    a.order_index - b.order_index
    || a.created_at - b.created_at
    || a.id.localeCompare(b.id)
  ));
};

const codeCardsByScope = (scope: FanxiuPseudoCodeCardScope) => (
  sortCodeCards(codeCards.value.filter((card) => card.scope === scope))
);

const nextCodeCardOrder = (scope: FanxiuPseudoCodeCardScope) => {
  const scopedCards = codeCardsByScope(scope);
  return scopedCards.length ? Math.max(...scopedCards.map((card) => card.order_index)) + 1 : 0;
};

const importLegacyCodeCards = async () => {
  const rawText = window.localStorage.getItem(LEGACY_CODE_CARDS_STORAGE_KEY);
  if (!rawText) {
    return;
  }
  try {
    const legacyCards = normalizeLegacyCodeCards(JSON.parse(rawText));
    if (!legacyCards.length) {
      window.localStorage.removeItem(LEGACY_CODE_CARDS_STORAGE_KEY);
      return;
    }
    const createdCards: CodeCard[] = [];
    for (const [index, card] of legacyCards.entries()) {
      createdCards.push(await createFanxiuPseudoCodeCard({
        scope: 'action',
        title: card.title || `动作${index + 1}`,
        body: card.body,
        order_index: index,
      }));
    }
    codeCards.value = sortCodeCards([...codeCards.value, ...createdCards]);
    window.localStorage.removeItem(LEGACY_CODE_CARDS_STORAGE_KEY);
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  }
};

const loadCodeCards = async () => {
  codeCardsLoading.value = true;
  try {
    const payload = await listFanxiuPseudoCodeCards();
    codeCards.value = payload.items;
    if (!payload.items.length) await importLegacyCodeCards();
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  } finally {
    codeCardsLoading.value = false;
  }
};

const clearCodeCardSaveTimer = (id: string) => {
  const timer = codeCardSaveTimers.get(id);
  if (!timer) return;
  window.clearTimeout(timer);
  codeCardSaveTimers.delete(id);
};

const applySavedCodeCard = (savedCard: CodeCard) => {
  const index = codeCards.value.findIndex((card) => card.id === savedCard.id);
  if (index >= 0) codeCards.value[index] = savedCard;
};

const saveCodeCardNow = async (card: CodeCard) => {
  clearCodeCardSaveTimer(card.id);
  try {
    const savedCard = await updateFanxiuPseudoCodeCard(card.id, {
      scope: card.scope,
      title: card.title,
      body: card.body,
      enabled: card.enabled,
      order_index: card.order_index,
    });
    applySavedCodeCard(savedCard);
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  }
};

const scheduleCodeCardSave = (card: CodeCard) => {
  clearCodeCardSaveTimer(card.id);
  codeCardSaveTimers.set(card.id, window.setTimeout(() => {
    void saveCodeCardNow(card);
  }, 600));
};

const flushCodeCardSaves = async () => {
  const ids = [...codeCardSaveTimers.keys()];
  const cards = ids
    .map((id) => codeCards.value.find((card) => card.id === id))
    .filter((card): card is CodeCard => Boolean(card));
  await Promise.all(cards.map((card) => saveCodeCardNow(card)));
};

const addCodeCard = async (scope: FanxiuPseudoCodeCardScope) => {
  const scopeInfo = codeCardScopes.find((item) => item.scope === scope);
  const index = codeCardsByScope(scope).length + 1;
  try {
    const card = await createFanxiuPseudoCodeCard({
      scope,
      title: `${scopeInfo?.label ?? '卡片'}${index}`,
      body: '',
      enabled: true,
      order_index: nextCodeCardOrder(scope),
    });
    codeCards.value = sortCodeCards([...codeCards.value, card]);
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  }
};

const deleteCodeCard = async (id: string) => {
  const card = codeCards.value.find((item) => item.id === id);
  if (!card) return;
  try {
    await ElMessageBox.confirm(`删除 ${card.title.trim() || '未命名卡片'}？`, '删除伪代码卡片', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    });
  } catch {
    return;
  }
  clearCodeCardSaveTimer(id);
  try {
    await deleteFanxiuPseudoCodeCard(id);
    codeCards.value = codeCards.value.filter((item) => item.id !== id);
    expandedCodeCardIds.value = expandedCodeCardIds.value.filter((expandedId) => expandedId !== id);
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  }
};

const codeCardTitle = (card: CodeCard, scope: FanxiuPseudoCodeCardScope, index: number) => {
  const scopeInfo = codeCardScopes.find((item) => item.scope === scope);
  return card.title.trim() || `${scopeInfo?.label ?? '卡片'}${index + 1}`;
};

const isCodeCardExpanded = (id: string) => expandedCodeCardIds.value.includes(id);

const toggleCodeCard = (id: string) => {
  if (isCodeCardExpanded(id)) {
    expandedCodeCardIds.value = expandedCodeCardIds.value.filter((expandedId) => expandedId !== id);
  } else {
    expandedCodeCardIds.value = [...expandedCodeCardIds.value, id];
  }
};

const applyPseudoCodeRunResponse = (actionLabel: string, response: FanxiuPseudoCodeRunResponse) => {
  const summary = [
    `${actionLabel}完成`,
    response.script_path ? `脚本：${response.script_path}` : '',
    response.compiled_cards ? `卡片：${response.compiled_cards}，缓存命中 ${response.cache_hits}，重新编译 ${response.cache_misses}` : '',
    response.log,
  ].filter(Boolean).join('\n');
  pseudoExecutionLog.value = summary || `${actionLabel}完成`;
  pseudoExecutionResult.value = response.result || '';
};

const compilePseudoCode = async () => {
  await flushCodeCardSaves();
  pseudoCompileLoading.value = true;
  pseudoOutputTab.value = 'log';
  pseudoExecutionLog.value = '编译中...';
  try {
    const response = await compileFanxiuPseudoCode({
      entry_id: selectedEntryId.value,
      timeout: 300,
    });
    applyPseudoCodeRunResponse('编译', response);
    ElMessage.success('伪代码已编译');
  } catch (error) {
    const message = getErrorMessage(error);
    pseudoExecutionLog.value = `编译失败：${message}`;
    ElMessage.error(message);
  } finally {
    pseudoCompileLoading.value = false;
  }
};

const startPseudoCode = async () => {
  pseudoStartLoading.value = true;
  pseudoOutputTab.value = 'log';
  pseudoExecutionLog.value = '启动中...';
  try {
    const response = await startFanxiuPseudoCode({ timeout: 120 });
    applyPseudoCodeRunResponse('启动', response);
    if (response.result) pseudoOutputTab.value = 'result';
    ElMessage.success('伪代码已启动');
  } catch (error) {
    const message = getErrorMessage(error);
    pseudoExecutionLog.value = `启动失败：${message}`;
    ElMessage.error(message);
  } finally {
    pseudoStartLoading.value = false;
  }
};

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
const isDefaultScreenshotBoxName = (name: string, index = 0) => {
  const trimmed = name.trim();
  return trimmed === `框${index + 1}` || /^框\d+$/.test(trimmed);
};

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

const getScreenshotSelectionStorageKey = (entryId = selectedEntryId.value) => {
  return `${SCREENSHOT_SELECTION_STORAGE_PREFIX}.${entryId || 'default'}`;
};

const loadPersistedScreenshotFilename = (entryId = selectedEntryId.value) => {
  if (!entryId) return '';
  return window.localStorage.getItem(getScreenshotSelectionStorageKey(entryId)) || '';
};

const persistSelectedScreenshotFilename = (filename: string, entryId = selectedEntryId.value) => {
  if (!entryId) return;
  if (filename) {
    window.localStorage.setItem(getScreenshotSelectionStorageKey(entryId), filename);
  } else {
    window.localStorage.removeItem(getScreenshotSelectionStorageKey(entryId));
  }
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
  screenshotImages.value = [];
  screenshotLoaded.value = false;
  clearScreenshotSelection();
  clearMatchResults();
  persistEntrySelection(selectedEntryId.value);
  applyWindowConfig();
  await Promise.all([refreshStreamToken(), loadRuntimeStatus()]);
  if (screenshotPanelOpen.value) await loadScreenshotList();
  restartStream();
};

const handleWindowChange = async () => {
  controlEnabled.value = false;
  controlClickState.value = null;
  naturalWidth.value = 0;
  naturalHeight.value = 0;
  streamError.value = '';
  clearMatchResults();
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

const drawScreenshotBox = (
  ctx: CanvasRenderingContext2D,
  box: OverlayBox,
  displayWidth: number,
  displayHeight: number,
  options: { active?: boolean; draft?: boolean } = {},
) => {
  if (!screenshotNaturalWidth.value || !screenshotNaturalHeight.value) return;
  const scaleX = displayWidth / screenshotNaturalWidth.value;
  const scaleY = displayHeight / screenshotNaturalHeight.value;
  const x = box.x * scaleX;
  const y = box.y * scaleY;
  const w = box.w * scaleX;
  const h = box.h * scaleY;

  ctx.save();
  ctx.lineWidth = options.active ? 2 : 1.5;
  ctx.strokeStyle = options.draft ? '#e6a23c' : (options.active ? '#ff4d4f' : '#f97316');
  ctx.fillStyle = options.active ? 'rgba(255, 77, 79, 0.12)' : 'rgba(249, 115, 22, 0.08)';
  if (options.draft) ctx.setLineDash([6, 4]);
  ctx.fillRect(x, y, w, h);
  ctx.strokeRect(x, y, w, h);
  if (options.active && !options.draft) {
    const handleSize = 8;
    ctx.fillStyle = '#fff';
    ctx.strokeStyle = '#ff4d4f';
    ctx.lineWidth = 1.5;
    ctx.fillRect(x - handleSize / 2, y - handleSize / 2, handleSize, handleSize);
    ctx.strokeRect(x - handleSize / 2, y - handleSize / 2, handleSize, handleSize);
    ctx.fillRect(x + w - handleSize / 2, y + h - handleSize / 2, handleSize, handleSize);
    ctx.strokeRect(x + w - handleSize / 2, y + h - handleSize / 2, handleSize, handleSize);
  }
  ctx.restore();
};

const drawScreenshotOverlay = () => {
  const canvas = screenshotOverlayCanvasRef.value;
  if (!canvas) return;

  const rect = canvas.getBoundingClientRect();
  const width = rect.width;
  const height = rect.height;
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
  screenshotBoxes.value.forEach((box) => {
    drawScreenshotBox(ctx, box, width, height, { active: selectedScreenshotBoxId.value === box.id });
  });
  if (screenshotDraftBox.value) {
    drawScreenshotBox(ctx, normalizeScreenshotBox(screenshotDraftBox.value), width, height, { draft: true });
  }
};

const syncScreenshotCanvas = () => {
  const canvas = screenshotOverlayCanvasRef.value;
  const wrap = screenshotImageWrapRef.value;
  if (!canvas || !wrap) return;
  const rect = wrap.getBoundingClientRect();
  canvas.style.width = `${rect.width}px`;
  canvas.style.height = `${rect.height}px`;
  drawScreenshotOverlay();
};

const drawMatchOverlay = () => {
  const canvas = matchOverlayCanvasRef.value;
  if (!canvas) return;
  const rect = canvas.getBoundingClientRect();
  const width = rect.width;
  const height = rect.height;
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

  const result = selectedMatchResult.value;
  if (!result || !result.width || !result.height) return;

  const drawMatchBox = (box: FanxiuGameWindow2MatchBox, color: string, fill: string, dashed = false) => {
    const scaleX = width / result.width;
    const scaleY = height / result.height;
    const x = box.x * scaleX;
    const y = box.y * scaleY;
    const w = box.w * scaleX;
    const h = box.h * scaleY;
    ctx.save();
    ctx.lineWidth = 2;
    ctx.strokeStyle = color;
    ctx.fillStyle = fill;
    if (dashed) ctx.setLineDash([6, 4]);
    ctx.fillRect(x, y, w, h);
    ctx.strokeRect(x, y, w, h);
    ctx.restore();
  };

  const fixedBox = result.current_box || result.box;
  drawMatchBox(fixedBox, '#10b981', 'rgba(16, 185, 129, 0.12)');
  if (result.template_box) {
    drawMatchBox(result.template_box, '#f97316', 'rgba(249, 115, 22, 0.1)', true);
  }
};

const syncMatchCanvas = () => {
  const canvas = matchOverlayCanvasRef.value;
  const wrap = matchImageWrapRef.value;
  if (!canvas || !wrap) return;
  const rect = wrap.getBoundingClientRect();
  canvas.style.width = `${rect.width}px`;
  canvas.style.height = `${rect.height}px`;
  drawMatchOverlay();
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

const saveCurrentFrame = async () => {
  if (!selectedEntryId.value) return;
  saveFrameLoading.value = true;
  try {
    const result = await saveFanxiuGameWindow2Frame({
      entry_id: selectedEntryId.value,
      title: targetTitle.value.trim(),
      mode: 'screen',
      area: captureArea.value,
      crop: cropText.value.trim(),
      trim_border: trimBorderText.value.trim(),
      rotate: rotateDegrees.value,
      fixed_width: 0,
      fixed_height: 0,
      quality: Number(quality.value) || selectedWindowScene.value.defaults.quality,
    });
    ElMessage.success(`已保存 截图/${result.filename}`);
    if (screenshotPanelOpen.value) {
      await loadScreenshotList(result.filename);
    }
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  } finally {
    saveFrameLoading.value = false;
  }
};

const toMatchBoxPayload = (box: OverlayBox): FanxiuGameWindow2MatchBox => ({
  name: box.name.trim(),
  x: Math.round(box.x),
  y: Math.round(box.y),
  w: Math.round(box.w),
  h: Math.round(box.h),
});

const appendMatchResult = (response: FanxiuGameWindow2MatchResponse, imageUrl: string) => {
  const item: MatchResult = {
    ...response,
    id: `${response.match_filename}-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    imageUrl,
    createdAt: Date.now(),
  };
  matchResults.value = [item, ...matchResults.value];
  selectedMatchEntryId.value = `${item.id}:fixed`;
};

const clearMatchResults = () => {
  matchResults.value.forEach((item) => URL.revokeObjectURL(item.imageUrl));
  matchResults.value = [];
  selectedMatchEntryId.value = '';
};

const handleMatchImageLoad = () => {
  void nextTick(syncMatchCanvas);
};

const selectMatchEntry = (id: string) => {
  selectedMatchEntryId.value = id;
  void nextTick(syncMatchCanvas);
};

const runScreenshotBoxMatch = async (box: OverlayBox) => {
  if (!selectedEntryId.value || !selectedScreenshotFilename.value) return;
  matchingBoxId.value = box.id;
  try {
    await flushScreenshotAutosave();
    const response = await matchFanxiuGameWindow2Screenshot({
      entry_id: selectedEntryId.value,
      filename: selectedScreenshotFilename.value,
      box: toMatchBoxPayload(box),
      title: targetTitle.value.trim(),
      mode: 'screen',
      area: captureArea.value,
      crop: cropText.value.trim(),
      trim_border: trimBorderText.value.trim(),
      rotate: rotateDegrees.value,
      fixed_width: 0,
      fixed_height: 0,
      quality: Number(quality.value) || selectedWindowScene.value.defaults.quality,
    });
    const blob = await getFanxiuGameWindow2MatchImage(selectedEntryId.value, response.match_filename);
    appendMatchResult(response, URL.createObjectURL(blob));
    await nextTick();
    syncMatchCanvas();
    const fixedText = `原位${response.fixed_similarity ?? response.similarity}%`;
    const templateText = response.template_similarity === undefined ? '' : ` 模板${response.template_similarity}%`;
    ElMessage.success(`${fixedText}${templateText}`);
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  } finally {
    matchingBoxId.value = null;
  }
};

const revokeScreenshotImageUrl = () => {
  if (!screenshotImageUrl.value) return;
  URL.revokeObjectURL(screenshotImageUrl.value);
  screenshotImageUrl.value = '';
};

const toScreenshotOverlayBoxes = (boxesPayload: FanxiuGameWindow2PreLabelBox[], filename: string): OverlayBox[] => {
  return boxesPayload.map((box, index) => ({
    id: `${filename}-${index}-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    name: isDefaultScreenshotBoxName(box.name || '', index) ? '' : box.name || '',
    x: Number(box.x) || 0,
    y: Number(box.y) || 0,
    w: Number(box.w) || 0,
    h: Number(box.h) || 0,
  }));
};

const toPreLabelPayload = (): FanxiuGameWindow2PreLabelPayload => ({
  version: 1,
  image: selectedScreenshotFilename.value,
  size: {
    width: screenshotNaturalWidth.value || selectedScreenshotImage.value?.width || 0,
    height: screenshotNaturalHeight.value || selectedScreenshotImage.value?.height || 0,
  },
  boxes: screenshotBoxes.value.map((box) => ({
    name: box.name,
    x: Math.round(box.x),
    y: Math.round(box.y),
    w: Math.round(box.w),
    h: Math.round(box.h),
  })),
});

const clearScreenshotSelection = () => {
  cancelScreenshotJump();
  closeScreenshotContextMenus();
  selectedScreenshotFilename.value = '';
  screenshotNaturalWidth.value = 0;
  screenshotNaturalHeight.value = 0;
  screenshotBoxes.value = [];
  selectedScreenshotBoxId.value = null;
  screenshotDraftState.value = null;
  screenshotDraftBox.value = null;
  screenshotResizeState.value = null;
  screenshotDirty.value = false;
  revokeScreenshotImageUrl();
  drawScreenshotOverlay();
};

const extractScreenshotNumber = (filename: string) => {
  const match = /^0*(\d+)\.[^.]+$/i.exec(filename);
  return match ? Number(match[1]) : NaN;
};

const findScreenshotByJumpText = (text: string) => {
  const value = text.trim();
  if (!value) return null;
  const lowerValue = value.toLowerCase();
  const exact = screenshotImages.value.find((item) => item.filename.toLowerCase() === lowerValue);
  if (exact) return exact;

  if (!/^\d+$/.test(value)) return null;
  const targetNumber = Number(value);
  if (!Number.isSafeInteger(targetNumber) || targetNumber <= 0) return null;
  const paddedFilename = `${String(targetNumber).padStart(4, '0')}.jpg`;
  return screenshotImages.value.find((item) => item.filename.toLowerCase() === paddedFilename)
    ?? screenshotImages.value.find((item) => extractScreenshotNumber(item.filename) === targetNumber)
    ?? null;
};

const startScreenshotJumpEdit = async () => {
  if (!screenshotImages.value.length || screenshotJumpEditing.value) return;
  const currentNumber = extractScreenshotNumber(selectedScreenshotFilename.value);
  screenshotJumpText.value = Number.isFinite(currentNumber) ? String(currentNumber) : selectedScreenshotFilename.value;
  screenshotJumpEditing.value = true;
  await nextTick();
  screenshotJumpInputRef.value?.focus();
};

const cancelScreenshotJump = () => {
  screenshotJumpEditing.value = false;
  screenshotJumpText.value = '';
};

const confirmScreenshotJump = async () => {
  const target = findScreenshotByJumpText(screenshotJumpText.value);
  if (!target) {
    ElMessage.warning('未找到对应截图');
    return;
  }
  cancelScreenshotJump();
  await selectScreenshotImage(target.filename);
};

const loadScreenshotList = async (preferFilename = '') => {
  if (!selectedEntryId.value) {
    clearScreenshotSelection();
    screenshotImages.value = [];
    screenshotLoaded.value = false;
    return;
  }
  screenshotLoading.value = true;
  try {
    const payload = await listFanxiuGameWindow2Screenshots(selectedEntryId.value);
    screenshotImages.value = payload.items;
    screenshotLoaded.value = true;
    const persistedFilename = loadPersistedScreenshotFilename();
    const targetFilename = preferFilename
      || (screenshotImages.value.some((item) => item.filename === selectedScreenshotFilename.value) ? selectedScreenshotFilename.value : '')
      || (screenshotImages.value.some((item) => item.filename === persistedFilename) ? persistedFilename : '')
      || screenshotImages.value[0]?.filename
      || '';
    if (targetFilename) {
      await selectScreenshotImage(targetFilename, true);
    } else {
      clearScreenshotSelection();
    }
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  } finally {
    screenshotLoading.value = false;
  }
};

const toggleScreenshotPanel = async () => {
  screenshotPanelOpen.value = !screenshotPanelOpen.value;
  if (screenshotPanelOpen.value) {
    await nextTick();
    if (!screenshotImages.value.length) {
      await loadScreenshotList();
    } else {
      syncScreenshotCanvas();
    }
  }
};

const clearScreenshotSaveTimer = () => {
  if (!screenshotSaveTimer) return;
  window.clearTimeout(screenshotSaveTimer);
  screenshotSaveTimer = null;
};

const flushScreenshotAutosave = async () => {
  clearScreenshotSaveTimer();
  if (screenshotDirty.value) await saveScreenshotPreLabel(true);
};

const selectScreenshotImage = async (filename: string, forceReload = false) => {
  if (!selectedEntryId.value || !filename) return;
  if (!forceReload && selectedScreenshotFilename.value === filename && screenshotImageUrl.value) return;
  closeScreenshotContextMenus();
  await flushScreenshotAutosave();
  selectedScreenshotFilename.value = filename;
  selectedScreenshotBoxId.value = null;
  screenshotDraftState.value = null;
  screenshotDraftBox.value = null;
  screenshotResizeState.value = null;
  screenshotDirty.value = false;
  revokeScreenshotImageUrl();
  try {
    const [blob, preLabel] = await Promise.all([
      getFanxiuGameWindow2Screenshot(selectedEntryId.value, filename),
      getFanxiuGameWindow2PreLabel(selectedEntryId.value, filename),
    ]);
    screenshotImageUrl.value = URL.createObjectURL(blob);
    screenshotBoxes.value = toScreenshotOverlayBoxes(preLabel.payload.boxes || [], filename);
    screenshotNaturalWidth.value = preLabel.payload.size?.width || selectedScreenshotImage.value?.width || 0;
    screenshotNaturalHeight.value = preLabel.payload.size?.height || selectedScreenshotImage.value?.height || 0;
    persistSelectedScreenshotFilename(filename);
    await nextTick();
    syncScreenshotCanvas();
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  }
};

const selectAdjacentScreenshotImage = async (delta: -1 | 1) => {
  if (!screenshotImages.value.length) return;
  const currentIndex = selectedScreenshotIndex.value >= 0 ? selectedScreenshotIndex.value : 0;
  const nextIndex = clamp(currentIndex + delta, 0, screenshotImages.value.length - 1);
  const nextItem = screenshotImages.value[nextIndex];
  if (!nextItem || nextItem.filename === selectedScreenshotFilename.value) return;
  await selectScreenshotImage(nextItem.filename);
};

const deleteCurrentScreenshotImage = async () => {
  if (!selectedEntryId.value || !selectedScreenshotFilename.value || screenshotDeleting.value) return;
  const filename = selectedScreenshotFilename.value;
  try {
    await ElMessageBox.confirm(`删除 ${filename}？`, '删除截图', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    });
  } catch {
    return;
  }

  const currentIndex = selectedScreenshotIndex.value;
  const nextFilename = screenshotImages.value[currentIndex + 1]?.filename
    || screenshotImages.value[currentIndex - 1]?.filename
    || '';
  screenshotDeleting.value = true;
  clearScreenshotSaveTimer();
  try {
    await deleteFanxiuGameWindow2Screenshot(selectedEntryId.value, filename);
    clearScreenshotSelection();
    await loadScreenshotList(nextFilename);
    ElMessage.success(`已删除 ${filename}`);
  } catch (error) {
    if (screenshotDirty.value) markScreenshotDirty();
    ElMessage.error(getErrorMessage(error));
  } finally {
    screenshotDeleting.value = false;
  }
};

const saveScreenshotPreLabel = async (silent = false) => {
  if (!selectedEntryId.value || !selectedScreenshotFilename.value) return;
  if (silent && !screenshotDirty.value) return;
  if (!silent) screenshotSaving.value = true;
  try {
    await saveFanxiuGameWindow2PreLabel(
      selectedEntryId.value,
      selectedScreenshotFilename.value,
      toPreLabelPayload(),
    );
    screenshotDirty.value = false;
    const item = selectedScreenshotImage.value;
    if (item) item.pre_label_exists = true;
    if (!silent) ElMessage.success('已保存标注');
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  } finally {
    if (!silent) screenshotSaving.value = false;
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

const normalizeScreenshotBox = (box: OverlayBox): OverlayBox => {
  const maxX = screenshotNaturalWidth.value || Number.MAX_SAFE_INTEGER;
  const maxY = screenshotNaturalHeight.value || Number.MAX_SAFE_INTEGER;
  const x1 = clamp(Math.min(box.x, box.x + box.w), 0, maxX);
  const y1 = clamp(Math.min(box.y, box.y + box.h), 0, maxY);
  const x2 = clamp(Math.max(box.x, box.x + box.w), 0, maxX);
  const y2 = clamp(Math.max(box.y, box.y + box.h), 0, maxY);
  return {
    ...box,
    x: Math.round(x1),
    y: Math.round(y1),
    w: Math.round(Math.max(0, x2 - x1)),
    h: Math.round(Math.max(0, y2 - y1)),
  };
};

const getScreenshotFramePoint = (event: PointerEvent | MouseEvent) => {
  const canvas = screenshotOverlayCanvasRef.value;
  if (!canvas || !screenshotNaturalWidth.value || !screenshotNaturalHeight.value) return null;
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;
  return {
    x: clamp((event.clientX - rect.left) * screenshotNaturalWidth.value / rect.width, 0, screenshotNaturalWidth.value),
    y: clamp((event.clientY - rect.top) * screenshotNaturalHeight.value / rect.height, 0, screenshotNaturalHeight.value),
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

const findScreenshotBoxAt = (x: number, y: number) => {
  for (let index = screenshotBoxes.value.length - 1; index >= 0; index -= 1) {
    const box = screenshotBoxes.value[index];
    if (x >= box.x && x <= box.x + box.w && y >= box.y && y <= box.y + box.h) {
      return index;
    }
  }
  return -1;
};

const findScreenshotResizeHandleAt = (x: number, y: number): { index: number; handle: ScreenshotResizeHandle } | null => {
  const canvas = screenshotOverlayCanvasRef.value;
  if (!canvas || !screenshotNaturalWidth.value || !screenshotNaturalHeight.value) return null;
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;
  const toleranceX = 10 * screenshotNaturalWidth.value / rect.width;
  const toleranceY = 10 * screenshotNaturalHeight.value / rect.height;

  for (let index = screenshotBoxes.value.length - 1; index >= 0; index -= 1) {
    const box = screenshotBoxes.value[index];
    const corners: Array<{ handle: ScreenshotResizeHandle; x: number; y: number }> = [
      { handle: 'top-left', x: box.x, y: box.y },
      { handle: 'bottom-right', x: box.x + box.w, y: box.y + box.h },
    ];
    for (const corner of corners) {
      if (Math.abs(x - corner.x) <= toleranceX && Math.abs(y - corner.y) <= toleranceY) {
        return { index, handle: corner.handle };
      }
    }
  }
  return null;
};

const setScreenshotCanvasCursor = (cursor: string) => {
  const canvas = screenshotOverlayCanvasRef.value;
  if (!canvas || canvas.style.cursor === cursor) return;
  canvas.style.cursor = cursor;
};

const updateScreenshotCanvasCursor = (event?: PointerEvent | MouseEvent) => {
  if (screenshotResizeState.value) {
    setScreenshotCanvasCursor('nwse-resize');
    return;
  }
  if (screenshotDraftState.value) {
    setScreenshotCanvasCursor('crosshair');
    return;
  }
  if (!event) {
    setScreenshotCanvasCursor('crosshair');
    return;
  }
  const point = getScreenshotFramePoint(event);
  if (!point) {
    setScreenshotCanvasCursor('crosshair');
    return;
  }
  if (findScreenshotResizeHandleAt(point.x, point.y)) {
    setScreenshotCanvasCursor('nwse-resize');
    return;
  }
  setScreenshotCanvasCursor(findScreenshotBoxAt(point.x, point.y) >= 0 ? 'pointer' : 'crosshair');
};

const normalizeControlPoint = (point: { x: number; y: number }) => ({
  x: Math.round(clamp(point.x, 0, Math.max(0, naturalWidth.value - 1))),
  y: Math.round(clamp(point.y, 0, Math.max(0, naturalHeight.value - 1))),
});

const buildRemoteInputPayloadBase = () => ({
  entry_id: selectedEntryId.value,
  title: targetTitle.value.trim(),
  mode: 'screen' as const,
  area: captureArea.value,
  crop: cropText.value.trim(),
  trim_border: trimBorderText.value.trim(),
  rotate: rotateDegrees.value,
  fixed_width: 0,
  fixed_height: 0,
  frame_width: naturalWidth.value,
  frame_height: naturalHeight.value,
});

const sendRemoteClick = async (point: { x: number; y: number }) => {
  if (!selectedEntryId.value || !naturalWidth.value || !naturalHeight.value) return;
  const normalized = normalizeControlPoint(point);
  try {
    await clickFanxiuGameWindow2({
      ...buildRemoteInputPayloadBase(),
      x: normalized.x,
      y: normalized.y,
    });
  } catch (error) {
    const now = Date.now();
    if (now - lastInputErrorAt > 1500) {
      lastInputErrorAt = now;
      ElMessage.error(getErrorMessage(error));
    }
  }
};

const sendRemoteDrag = async (
  startPoint: { x: number; y: number },
  endPoint: { x: number; y: number },
  durationMs: number,
) => {
  if (!selectedEntryId.value || !naturalWidth.value || !naturalHeight.value) return;
  const start = normalizeControlPoint(startPoint);
  const end = normalizeControlPoint(endPoint);
  try {
    await dragFanxiuGameWindow2({
      ...buildRemoteInputPayloadBase(),
      start_x: start.x,
      start_y: start.y,
      end_x: end.x,
      end_y: end.y,
      duration_ms: Math.round(clamp(durationMs, 80, 2000)),
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
    startedAt: Date.now(),
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
  const point = getFramePoint(event) ?? { x: state.frameX, y: state.frameY };
  if (moveDistance > 8) {
    void sendRemoteDrag(
      { x: state.frameX, y: state.frameY },
      point,
      Date.now() - state.startedAt,
    );
    return;
  }
  void sendRemoteClick(point);
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
  if (controlClickState.value?.pointerId === event.pointerId) {
    event.preventDefault();
    event.stopPropagation();
    return;
  }
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

const handleScreenshotImageLoad = () => {
  const image = screenshotImageRef.value;
  if (!image) return;
  screenshotNaturalWidth.value = image.naturalWidth;
  screenshotNaturalHeight.value = image.naturalHeight;
  void nextTick(syncScreenshotCanvas);
};

const handleScreenshotImageError = () => {
  ElMessage.error('截图加载失败');
};

const markScreenshotDirty = () => {
  screenshotDirty.value = true;
  clearScreenshotSaveTimer();
  screenshotSaveTimer = window.setTimeout(() => {
    screenshotSaveTimer = null;
    void saveScreenshotPreLabel(true);
  }, 600);
};

const selectScreenshotBox = (id: string | null) => {
  selectedScreenshotBoxId.value = id;
  drawScreenshotOverlay();
};

const closeScreenshotBoxContextMenu = () => {
  if (!screenshotBoxContextMenu.value.visible) return;
  screenshotBoxContextMenu.value = {
    visible: false,
    x: 0,
    y: 0,
    boxId: '',
  };
};

const closeScreenshotBoxListContextMenu = () => {
  if (!screenshotBoxListContextMenu.value.visible) return;
  screenshotBoxListContextMenu.value = {
    visible: false,
    x: 0,
    y: 0,
    boxId: '',
  };
};

const closeScreenshotContextMenus = () => {
  closeScreenshotBoxContextMenu();
  closeScreenshotBoxListContextMenu();
};

const openScreenshotBoxContextMenu = (event: MouseEvent, boxId: string) => {
  event.preventDefault();
  closeScreenshotBoxListContextMenu();
  const box = screenshotBoxes.value.find((item) => item.id === boxId);
  if (!box) {
    closeScreenshotBoxContextMenu();
    return;
  }
  selectedScreenshotBoxId.value = box.id;
  screenshotBoxContextMenu.value = {
    visible: true,
    x: event.clientX,
    y: event.clientY,
    boxId: box.id,
  };
  drawScreenshotOverlay();
};

const openScreenshotBoxListContextMenu = (event: MouseEvent, boxId: string) => {
  event.preventDefault();
  closeScreenshotBoxContextMenu();
  const box = screenshotBoxes.value.find((item) => item.id === boxId);
  if (!box) {
    closeScreenshotBoxListContextMenu();
    return;
  }
  selectedScreenshotBoxId.value = box.id;
  screenshotBoxListContextMenu.value = {
    visible: true,
    x: event.clientX,
    y: event.clientY,
    boxId: box.id,
  };
  drawScreenshotOverlay();
};

const openScreenshotBoxListPanelContextMenu = (event: MouseEvent) => {
  event.preventDefault();
  closeScreenshotBoxContextMenu();
  if (!copiedScreenshotBox.value) {
    closeScreenshotBoxListContextMenu();
    return;
  }
  screenshotBoxListContextMenu.value = {
    visible: true,
    x: event.clientX,
    y: event.clientY,
    boxId: '',
  };
};

const copyScreenshotBoxFromListContext = () => {
  const box = screenshotBoxes.value.find((item) => item.id === screenshotBoxListContextMenu.value.boxId);
  closeScreenshotBoxListContextMenu();
  if (!box) return;
  copiedScreenshotBox.value = {
    name: box.name,
    x: Math.round(box.x),
    y: Math.round(box.y),
    w: Math.round(box.w),
    h: Math.round(box.h),
  };
};

const pasteScreenshotBoxFromListContext = () => {
  const copied = copiedScreenshotBox.value;
  closeScreenshotBoxListContextMenu();
  if (!copied || !selectedScreenshotFilename.value) return;
  const maxWidth = screenshotNaturalWidth.value || Math.max(copied.x + copied.w, copied.w);
  const maxHeight = screenshotNaturalHeight.value || Math.max(copied.y + copied.h, copied.h);
  const x = Math.round(clamp(copied.x, 0, Math.max(0, maxWidth - 4)));
  const y = Math.round(clamp(copied.y, 0, Math.max(0, maxHeight - 4)));
  const w = Math.round(clamp(copied.w, 4, Math.max(4, maxWidth - x)));
  const h = Math.round(clamp(copied.h, 4, Math.max(4, maxHeight - y)));
  const nextBox: OverlayBox = {
    id: `${selectedScreenshotFilename.value}-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    name: copied.name,
    x,
    y,
    w,
    h,
  };
  screenshotBoxes.value.push(nextBox);
  selectedScreenshotBoxId.value = nextBox.id;
  markScreenshotDirty();
  drawScreenshotOverlay();
};

const runScreenshotBoxContextMatch = async () => {
  const box = screenshotBoxes.value.find((item) => item.id === screenshotBoxContextMenu.value.boxId);
  closeScreenshotBoxContextMenu();
  if (!box) return;
  await runScreenshotBoxMatch(box);
};

const handleScreenshotBoxNameInput = () => {
  markScreenshotDirty();
  drawScreenshotOverlay();
};

const handleScreenshotPointerDown = (event: PointerEvent) => {
  closeScreenshotContextMenus();
  if (event.button !== 0) return;
  const point = getScreenshotFramePoint(event);
  if (!point) return;

  const resizeHit = findScreenshotResizeHandleAt(point.x, point.y);
  if (resizeHit) {
    event.preventDefault();
    setScreenshotCanvasCursor('nwse-resize');
    const box = screenshotBoxes.value[resizeHit.index];
    selectedScreenshotBoxId.value = box.id;
    screenshotResizeState.value = {
      pointerId: event.pointerId,
      boxId: box.id,
      handle: resizeHit.handle,
      original: { ...box },
    };
    screenshotOverlayCanvasRef.value?.setPointerCapture(event.pointerId);
    drawScreenshotOverlay();
    return;
  }

  const hitIndex = findScreenshotBoxAt(point.x, point.y);
  if (hitIndex >= 0) {
    setScreenshotCanvasCursor('pointer');
    selectScreenshotBox(screenshotBoxes.value[hitIndex].id);
    return;
  }

  setScreenshotCanvasCursor('crosshair');
  selectedScreenshotBoxId.value = null;
  screenshotDraftState.value = {
    pointerId: event.pointerId,
    startX: point.x,
    startY: point.y,
  };
  screenshotDraftBox.value = {
    id: 'draft',
    name: '',
    x: point.x,
    y: point.y,
    w: 0,
    h: 0,
  };
  screenshotOverlayCanvasRef.value?.setPointerCapture(event.pointerId);
  drawScreenshotOverlay();
};

const handleScreenshotPointerMove = (event: PointerEvent) => {
  const resizeState = screenshotResizeState.value;
  if (resizeState?.pointerId === event.pointerId) {
    setScreenshotCanvasCursor('nwse-resize');
    const point = getScreenshotFramePoint(event);
    if (!point) return;
    const box = screenshotBoxes.value.find((item) => item.id === resizeState.boxId);
    if (!box) return;
    const original = resizeState.original;
    if (resizeState.handle === 'top-left') {
      const right = original.x + original.w;
      const bottom = original.y + original.h;
      const nextX = clamp(point.x, 0, right - 4);
      const nextY = clamp(point.y, 0, bottom - 4);
      box.x = Math.round(nextX);
      box.y = Math.round(nextY);
      box.w = Math.round(right - nextX);
      box.h = Math.round(bottom - nextY);
    } else {
      const nextRight = clamp(point.x, original.x + 4, screenshotNaturalWidth.value || Number.MAX_SAFE_INTEGER);
      const nextBottom = clamp(point.y, original.y + 4, screenshotNaturalHeight.value || Number.MAX_SAFE_INTEGER);
      box.w = Math.round(nextRight - original.x);
      box.h = Math.round(nextBottom - original.y);
    }
    drawScreenshotOverlay();
    return;
  }

  const state = screenshotDraftState.value;
  if (!state || state.pointerId !== event.pointerId) {
    updateScreenshotCanvasCursor(event);
    return;
  }
  setScreenshotCanvasCursor('crosshair');
  const point = getScreenshotFramePoint(event);
  if (!point) return;
  screenshotDraftBox.value = {
    id: 'draft',
    name: '',
    x: state.startX,
    y: state.startY,
    w: point.x - state.startX,
    h: point.y - state.startY,
  };
  drawScreenshotOverlay();
};

const finishScreenshotDraft = () => {
  const normalized = screenshotDraftBox.value ? normalizeScreenshotBox(screenshotDraftBox.value) : null;
  screenshotDraftState.value = null;
  screenshotDraftBox.value = null;
  if (!normalized || normalized.w < 4 || normalized.h < 4) {
    drawScreenshotOverlay();
    return;
  }

  const nextBox = {
    ...normalized,
    id: `${selectedScreenshotFilename.value}-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    name: '',
  };
  screenshotBoxes.value.push(nextBox);
  selectedScreenshotBoxId.value = nextBox.id;
  markScreenshotDirty();
  drawScreenshotOverlay();
};

const finishScreenshotResize = (event: PointerEvent) => {
  const resizeState = screenshotResizeState.value;
  if (!resizeState || resizeState.pointerId !== event.pointerId) return false;
  screenshotOverlayCanvasRef.value?.releasePointerCapture(event.pointerId);
  screenshotResizeState.value = null;
  markScreenshotDirty();
  drawScreenshotOverlay();
  updateScreenshotCanvasCursor(event);
  return true;
};

const handleScreenshotPointerUp = (event: PointerEvent) => {
  if (finishScreenshotResize(event)) return;
  if (screenshotDraftState.value?.pointerId !== event.pointerId) return;
  screenshotOverlayCanvasRef.value?.releasePointerCapture(event.pointerId);
  finishScreenshotDraft();
  updateScreenshotCanvasCursor(event);
};

const handleScreenshotPointerLeave = (event: PointerEvent) => {
  if (finishScreenshotResize(event)) return;
  if (screenshotDraftState.value?.pointerId !== event.pointerId) {
    updateScreenshotCanvasCursor();
    return;
  }
  finishScreenshotDraft();
  updateScreenshotCanvasCursor();
};

const handleScreenshotContextMenu = (event: MouseEvent) => {
  const point = getScreenshotFramePoint(event);
  if (!point) {
    closeScreenshotContextMenus();
    return;
  }
  const hitIndex = findScreenshotBoxAt(point.x, point.y);
  if (hitIndex < 0) {
    closeScreenshotContextMenus();
    return;
  }
  openScreenshotBoxContextMenu(event, screenshotBoxes.value[hitIndex].id);
};

const deleteSelectedScreenshotBox = () => {
  if (!selectedScreenshotBoxId.value) return;
  screenshotBoxes.value = screenshotBoxes.value.filter((box) => box.id !== selectedScreenshotBoxId.value);
  selectedScreenshotBoxId.value = null;
  markScreenshotDirty();
  drawScreenshotOverlay();
};

const undoLastScreenshotBox = () => {
  const removed = screenshotBoxes.value.pop();
  if (removed && selectedScreenshotBoxId.value === removed.id) selectedScreenshotBoxId.value = null;
  if (removed) markScreenshotDirty();
  drawScreenshotOverlay();
};

const handleKeydown = (event: KeyboardEvent) => {
  const target = event.target as HTMLElement | null;
  if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return;
  if (target?.isContentEditable) return;
  if (!screenshotPanelOpen.value || !selectedScreenshotImage.value) return;

  if (event.key === 'Delete' || event.key === 'Backspace') {
    deleteSelectedScreenshotBox();
    event.preventDefault();
    return;
  }
  if ((event.ctrlKey && event.key.toLowerCase() === 'z') || event.key.toLowerCase() === 'z') {
    undoLastScreenshotBox();
    event.preventDefault();
  }
};

const handleWindowResize = () => {
  syncCanvas();
  syncScreenshotCanvas();
  syncMatchCanvas();
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
  window.addEventListener('resize', handleWindowResize);
  window.addEventListener('click', closeScreenshotContextMenus);
  void loadCodeCards();
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
    if (screenshotPanelOpen.value) void loadScreenshotList();
  }
  startPolling();
  void nextTick(syncCanvas);
});

onBeforeUnmount(() => {
  stopPolling();
  if (screenshotDirty.value) void flushScreenshotAutosave();
  void flushCodeCardSaves();
  window.removeEventListener('keydown', handleKeydown);
  window.removeEventListener('resize', handleWindowResize);
  window.removeEventListener('click', closeScreenshotContextMenus);
  resizeObserver?.disconnect();
  if (streamImageRef.value) streamImageRef.value.src = '';
  revokeScreenshotImageUrl();
  clearMatchResults();
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

.muted {
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
  display: block;
}

.viewer-pane {
  min-width: 0;
  min-height: 0;
  padding: 16px;
  overflow: auto;
}

.live-workspace {
  display: flex;
  align-items: flex-start;
  gap: 14px;
}

.image-wrap {
  position: relative;
  flex: 0 0 auto;
  display: inline-block;
  line-height: 0;
  background: #111827;
  border: 1px solid #d1d5db;
}

.stream-image {
  display: block;
  max-width: calc(100vw - 260px);
  max-height: calc(100dvh - 160px);
  user-select: none;
}

.paused-placeholder {
  width: min(520px, calc(100vw - 260px));
  height: min(760px, calc(100dvh - 160px));
  display: grid;
  place-items: center;
  color: #9ca3af;
  background: #111827;
}

.overlay-canvas {
  position: absolute;
  inset: 0;
  cursor: default;
  pointer-events: none;
  touch-action: none;
}

.image-wrap.is-control-enabled .overlay-canvas {
  cursor: pointer;
  pointer-events: auto;
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

.code-panel {
  flex: 1 1 360px;
  min-width: 320px;
  max-width: 560px;
  padding-left: 14px;
  border-left: 1px solid #e5e7eb;
}

.code-panel-head {
  min-height: 28px;
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  color: #111827;
  font-size: 16px;
  font-weight: 700;
}

.code-panel-actions {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.code-scope-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.code-scope {
  min-width: 0;
}

.code-scope-head {
  min-height: 24px;
  margin-bottom: 6px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #374151;
  font-size: 13px;
  font-weight: 700;
}

.code-add,
.code-card-collapse,
.code-card-delete {
  width: 24px;
  height: 24px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 4px;
  line-height: 1;
  cursor: pointer;
}

.code-add {
  color: #2563eb;
  font-size: 18px;
}

.code-add:hover {
  background: #eff6ff;
  border-color: #bfdbfe;
}

.code-card-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.code-card {
  padding: 8px;
  background: #fff;
  border: 1px solid #d8e0ea;
  border-radius: 4px;
}

.code-card-head {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 24px 24px;
  align-items: center;
  gap: 8px;
}

.code-card-summary-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 24px;
  align-items: center;
  gap: 8px;
}

.code-card-title-button {
  min-width: 0;
  height: 24px;
  padding: 0;
  display: inline-flex;
  align-items: center;
  color: #1f2937;
  background: transparent;
  border: none;
  text-align: left;
  cursor: pointer;
}

.code-card-title-button span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.code-card-title-button:hover {
  color: #2563eb;
}

.code-card-collapse {
  color: #64748b;
  font-size: 14px;
}

.code-card-collapse:hover {
  background: #f8fafc;
  border-color: #cbd5e1;
}

.code-card-delete {
  color: #dc2626;
  font-size: 18px;
}

.code-card-delete:hover {
  background: #fef2f2;
  border-color: #fecaca;
}

.code-card-body {
  display: block;
  margin-top: 8px;
}

.code-card-body :deep(.el-textarea__inner) {
  line-height: 1.5;
}

.code-card-empty {
  min-height: 28px;
  display: flex;
  align-items: center;
  color: #94a3b8;
  font-size: 12px;
}

.code-output-panel {
  margin-top: 14px;
  padding-top: 10px;
  border-top: 1px solid #e5e7eb;
}

.code-output-tabs {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.code-output-tab {
  height: 26px;
  padding: 0 10px;
  color: #475569;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 4px;
  cursor: pointer;
}

.code-output-tab.is-active {
  color: #1d4ed8;
  background: #eff6ff;
  border-color: #bfdbfe;
}

.code-output-box {
  min-height: 120px;
  max-height: 260px;
  margin: 8px 0 0;
  padding: 10px;
  overflow: auto;
  color: #1f2937;
  background: #fff;
  border: 1px solid #d8e0ea;
  border-radius: 4px;
  font-size: 12px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
}

.screenshot-panel {
  width: min(980px, calc(100vw - 260px));
  margin-top: 14px;
  padding-top: 10px;
  border-top: 1px solid #e5e7eb;
}

.screenshot-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.screenshot-toggle {
  min-height: 28px;
  padding: 0;
  display: inline-flex;
  align-items: center;
  gap: 10px;
  color: #111827;
  background: transparent;
  border: none;
  font-size: 16px;
  font-weight: 700;
  cursor: pointer;
}

.screenshot-summary {
  color: #6b7280;
  font-size: 12px;
  font-weight: 400;
  white-space: nowrap;
}

.screenshot-caret {
  width: 12px;
  color: #64748b;
  font-size: 10px;
}

.screenshot-body {
  margin-top: 10px;
}

.screenshot-empty {
  margin-top: 10px;
  color: #6b7280;
  font-size: 13px;
  line-height: 1.6;
}

.screenshot-nav {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.screenshot-current-item {
  min-width: 76px;
  max-width: 140px;
  height: 24px;
  padding: 0 2px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #374151;
}

.screenshot-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.screenshot-jump-input {
  width: 70px;
}

.screenshot-jump-input :deep(.el-input__wrapper) {
  padding-left: 6px;
  padding-right: 6px;
}

.screenshot-delete {
  width: 24px;
  height: 24px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #dc2626;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 4px;
  font-size: 18px;
  line-height: 1;
  cursor: pointer;
}

.screenshot-delete:hover:not(:disabled) {
  background: #fef2f2;
  border-color: #fecaca;
}

.screenshot-delete:disabled {
  color: #cbd5e1;
  cursor: not-allowed;
}

.screenshot-editor {
  margin-top: 12px;
  display: grid;
  grid-template-columns: minmax(320px, max-content) 240px;
  gap: 14px;
  align-items: start;
}

.screenshot-preview {
  min-width: 0;
  overflow: auto;
}

.screenshot-image-wrap {
  position: relative;
  display: inline-block;
  line-height: 0;
  background: #111827;
  border: 1px solid #d1d5db;
}

.screenshot-image {
  display: block;
  max-width: min(720px, calc(100vw - 520px));
  user-select: none;
}

.screenshot-image-placeholder {
  width: 360px;
  height: 640px;
  display: grid;
  place-items: center;
  color: #9ca3af;
  background: #111827;
}

.screenshot-overlay-canvas {
  position: absolute;
  inset: 0;
  cursor: crosshair;
  touch-action: none;
}

.screenshot-pre-panel {
  min-width: 0;
}

.screenshot-box-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.screenshot-box-row {
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr);
  align-items: center;
  gap: 8px;
  padding: 4px 6px;
  border: 1px solid transparent;
  border-radius: 4px;
  cursor: pointer;
}

.screenshot-box-row:hover {
  background: #f8fafc;
}

.screenshot-box-row.is-active {
  border-color: #f97316;
  background: #fff7ed;
}

.screenshot-box-number {
  width: 24px;
  height: 24px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  background: #eef2f7;
  color: #1f2937;
  font-size: 12px;
  line-height: 1;
  white-space: nowrap;
}

.screenshot-box-menu {
  position: fixed;
  z-index: 2000;
  min-width: 76px;
  padding: 4px;
  background: #fff;
  border: 1px solid #d8e0ea;
  border-radius: 4px;
  box-shadow: 0 10px 24px rgba(15, 23, 42, 0.14);
}

.screenshot-box-menu button {
  width: 100%;
  min-height: 26px;
  padding: 0 12px;
  color: #1f2937;
  background: transparent;
  border: none;
  border-radius: 3px;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
}

.screenshot-box-menu button:hover:not(:disabled) {
  background: #f3f6fb;
}

.screenshot-box-menu button:disabled {
  color: #9ca3af;
  cursor: not-allowed;
}

.match-panel {
  width: min(980px, calc(100vw - 260px));
  margin-top: 14px;
  padding-top: 10px;
  border-top: 1px solid #e5e7eb;
}

.match-head {
  min-height: 28px;
  display: flex;
  align-items: center;
  gap: 12px;
  color: #111827;
  font-size: 16px;
  font-weight: 700;
}

.match-summary {
  color: #6b7280;
  font-size: 12px;
  font-weight: 400;
  white-space: nowrap;
}

.match-body {
  margin-top: 10px;
  display: grid;
  grid-template-columns: minmax(320px, max-content) 240px;
  gap: 14px;
  align-items: start;
}

.match-preview {
  min-width: 0;
  overflow: auto;
}

.match-image-wrap {
  position: relative;
  display: inline-block;
  line-height: 0;
  background: #111827;
  border: 1px solid #d1d5db;
}

.match-image {
  display: block;
  max-width: min(720px, calc(100vw - 520px));
  user-select: none;
}

.match-overlay-canvas {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.match-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.match-row {
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr) 36px 44px;
  align-items: center;
  gap: 8px;
  padding: 4px 6px;
  color: #1f2937;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 4px;
  cursor: pointer;
}

.match-row:hover {
  background: #f8fafc;
}

.match-row.is-active {
  border-color: #10b981;
  background: #ecfdf5;
}

.match-row.is-template.is-active {
  border-color: #f97316;
  background: #fff7ed;
}

.match-number {
  width: 24px;
  height: 24px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  background: #eef2f7;
  color: #1f2937;
  font-size: 12px;
  line-height: 1;
}

.match-name {
  min-width: 0;
  overflow: hidden;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.match-row strong {
  font-weight: 700;
}

.match-kind,
.match-score {
  white-space: nowrap;
  text-align: right;
  font-size: 12px;
}

.match-row.is-fixed .match-kind,
.match-row.is-fixed .match-score {
  color: #047857;
}

.match-row.is-template .match-kind,
.match-row.is-template .match-score {
  color: #c2410c;
}


@media (max-width: 980px) {
  .topbar {
    align-items: stretch;
    flex-direction: column;
  }

  .stream-controls {
    justify-content: flex-start;
  }

  .live-workspace {
    flex-direction: column;
  }

  .stream-image,
  .paused-placeholder {
    max-width: calc(100vw - 32px);
  }

  .code-panel {
    width: calc(100vw - 32px);
    min-width: 0;
    max-width: none;
    padding-top: 12px;
    padding-left: 0;
    border-top: 1px solid #e5e7eb;
    border-left: none;
  }

  .screenshot-panel {
    width: calc(100vw - 32px);
  }

  .match-panel {
    width: calc(100vw - 32px);
  }

  .screenshot-editor {
    grid-template-columns: 1fr;
  }

  .match-body {
    grid-template-columns: 1fr;
  }

  .screenshot-image {
    max-width: calc(100vw - 32px);
  }

  .match-image {
    max-width: calc(100vw - 32px);
  }
}
</style>
