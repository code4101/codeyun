<template>
  <BookReaderSurface standalone :model-value="true" page-href="" class="pdf-resource-page library-reader-theme-dialog" :class="libraryReaderThemeClass" v-loading="loading">
    <template #header>
    <header class="pdf-toolbar reader-window-heading" v-context-menu="($event: MouseEvent) => contextMenu?.open($event)">
      <div class="pdf-toolbar-left">
        <div class="pdf-title" :title="documentDetail?.title || ''">
          {{ documentDetail?.title || errorText || 'PDF' }}
        </div>
      </div>


    </header>
    </template>

    <ReaderLayout @context-menu="contextMenu?.open($event)" :dock="dock" @resized="refreshReaderLayout">
      <template #toc>
        <ReaderNavigationPanel v-if="documentDetail">
          <PdfOutlinePanel
            :entries="outlineEntries"
            :outline-level="outlineLevel"
            :split-level="chapterOutline.level"
            @update:outline-level="setOutlineLevel"
            :current-page="currentPage"
            :page-count="pageCount || documentDetail?.metadata.page_count || 1"
            :can-edit="canEditOutline && !outlineErrorText"
            :busy="outlineLoading || outlineSaving"
            :error="outlineErrorText"
            :can-embed="outlineCanEmbed && canManageAccess"
            @navigate="goToPage"
            @select-section="selectPdfSection"
            @change="changeOutline"

            @reload="loadPdfOutline"
            @search-section="openSectionSearch"
          />

        </ReaderNavigationPanel>
      </template>
      <template #search>
        <PdfBookSearch v-if="documentDetail" :key="documentDetail.id" :pdf-id="documentDetail.id" :scope="bookSearchScope" @clear-scope="bookSearchScope = null" @navigate="navigateSearchResult" />
      </template>
      <template #ocr="{ active }">
        <PdfBookOcrControl v-if="documentDetail" :pdf-id="documentDetail.id" :revision="documentDetail.content_hash || ''" :active="active" :can-control="canManageAccess" />
      </template>
      <template #info>
          <div v-if="documentDetail" class="info-panel">
            <div class="meta-row">
              <span class="meta-label">权限</span>
              <span class="meta-value">{{ accessRoleLabel }}</span>
            </div>
            <div class="meta-row">
              <span class="meta-label">页数</span>
              <span class="meta-value">{{ pageCount || '--' }}</span>
            </div>
            <div class="meta-row" title="按 OCR 汉字、字母、数字统计，含页眉及注释，不含空白和标点">
              <span class="meta-label">估算字数</span>
              <span class="meta-value">{{ textStats ? `${formatCharacterCount(textStats.characters)} 字${textStats.recognized_pages < pageCount ? '（已识别部分）' : ''}` : textStatsError || '统计中…' }}</span>
            </div>
            <div class="meta-row">
              <span class="meta-label">当前页</span>
              <span class="meta-value">{{ renderedPage || currentPage }}</span>
            </div>
            <div class="meta-row">
              <span class="meta-label">缩放</span>
              <span class="meta-value">{{ zoomLabel }}</span>
            </div>
            <div class="meta-row">
              <span class="meta-label">格式</span>
              <span class="meta-value">{{ documentDetail.mime_type || 'application/pdf' }}</span>
            </div>
            <div class="meta-row">
              <span class="meta-label">大小</span>
              <span class="meta-value">{{ formatBytes(documentDetail.size_bytes) }}</span>
            </div>
          </div>
      </template>
      <template #default>
      <section
        ref="stageRef"
        class="pdf-stage"
        tabindex="0"
        @wheel="handleStageWheel"
        v-context-menu="($event: MouseEvent) => (contextMenu?.open($event))"
      >
      <div class="pdf-content-toolbar">
        <el-button
          :icon="ArrowLeft"
          text
          :disabled="!canGoPrevious"
          @click="goPreviousPage"
        />
        <el-input-number
          v-model="currentPage"
          size="small"
          class="page-number-input"
          :min="1"
          :max="pageInputMax"
          :precision="0"
          :controls="false"
          controls-position="right"
          :disabled="!pdfDocument"
          @change="handlePageInputChange"
        />
        <span class="page-total">/ {{ pageCount || '--' }}</span>
        <el-button
          :icon="ArrowRight"
          text
          :disabled="!canGoNext"
          @click="goNextPage"
        />
        <el-button
          :icon="MagicStick"
          text
          title="随机页"
          :disabled="!canGoRandomPage"
          @click="goRandomPage"
        >
          随机页
        </el-button>
        <div class="zoom-controls" aria-label="页面缩放">
          <el-button
            :icon="ZoomOut"
            text
            title="缩小"
            :disabled="!canZoomOut"
            @click="zoomOutPage"
          />
          <el-select
            v-model="zoom"
            size="small"
            class="zoom-select"
            :disabled="!pdfDocument"
            @change="handleZoomChange"
          >
            <el-option label="适合宽度" value="page-width" />
            <el-option label="适合页面" value="page-fit" />
            <el-option
              v-for="percent in ZOOM_PERCENT_OPTIONS"
              :key="percent"
              :label="`${percent}%`"
              :value="String(percent)"
            />
          </el-select>
          <el-button
            :icon="ZoomIn"
            text
            title="放大"
            :disabled="!canZoomIn"
            @click="zoomInPage"
          />
        </div>
        <el-button
          :icon="Refresh"
          text
          :disabled="!documentDetail"
          :loading="contentLoading"
          @click="reloadContentUrl"
        />
        <el-button
          v-if="canManageAccess"
          :icon="Share"
          text
          @click="openShareDialog"
        >
          分享
        </el-button>
      </div>

        <div class="reader-view-tabs" role="tablist" aria-label="阅读方式">
          <button role="tab" :aria-selected="readingView === 'pdf'" :class="{active: readingView === 'pdf'}" @click="readingView = 'pdf'">原始 PDF</button>
          <button role="tab" :aria-selected="readingView === 'ocr'" :class="{active: readingView === 'ocr'}" @click="readingView = 'ocr'">OCR 文本</button>
        </div>
        <PdfPageFind :surface="searchSurface" :enabled="readingView === 'pdf'" :request="pageFindRequest" />
        <PdfSectionReader ref="ocrReader" :entries="outlineEntries" :outline-ids="chapterOutline.items.map(item => item.id)" :scope-id="chapterOutline.scopeId" :total="pageCount || documentDetail?.metadata.page_count || 0" @source="readingView = 'pdf'; goToPage($event)" @location="handleOcrLocation" v-if="documentDetail" v-show="readingView === 'ocr'" :pdf-id="documentDetail.id" :page="currentPage" :active="readingView === 'ocr'" :revision="documentDetail.content_hash || ''" />
        <div v-if="readerErrorText || errorText" v-show="readingView === 'pdf'" class="reader-empty">
          <el-empty :description="readerErrorText || errorText" />
          <el-button
            v-if="readerErrorText && documentDetail"
            type="primary"
            plain
            :loading="contentLoading"
            @click="reloadContentUrl"
          >重新加载</el-button>
        </div>
        <PdfContinuousReader v-else-if="sectionRange && pdfDocument && documentDetail" v-show="readingView === 'pdf'" ref="continuousReader"
          :document="pdfDocument" :pdf-id="documentDetail.id" :revision="documentDetail.content_hash || ''"
          :start="sectionRange.start" :end="sectionRange.end" :page="currentPage" :zoom="zoom" :crop-enabled="cropEnabled"
          @page="handleContinuousPage" @text-ready="searchSurface = $event ? { root: $event } : null" @scale="renderedZoomPercent = $event" />
        <div v-else v-show="readingView === 'pdf'" class="pdf-page-scroll">
          <div class="pdf-page-shell" :class="{ 'is-rendering': pageRendering }">
            <PdfPageCrop :enabled="cropEnabled" :revision="pdfTextViewport">
            <img v-if="bootstrapPreview && !renderedPage" :src="bootstrapPreview" class="bootstrap-preview" alt="当前页预览" />
            <canvas v-show="!bootstrapPreview || renderedPage > 0" ref="canvasRef" class="pdf-canvas" />
            <PdfTextAnnotationLayer
              v-if="documentDetail && pdfTextContent && pdfTextViewport"
              :pdf-id="documentDetail.id"
              :page-number="renderedPage || currentPage"
              :source-revision="documentDetail.content_hash || ''"
              :prefer-ocr="!!pageFindRequest"
              :text-content="pdfTextContent"
              :viewport="pdfTextViewport"
              @text-ready="searchSurface = $event ? {root: $event} : null"
            />
            </PdfPageCrop>
            <div v-if="(contentLoading || pageRendering) && !bootstrapPreview" class="reader-loading">
              {{ contentLoading ? 'PDF 加载中' : '页面渲染中' }}
            </div>
          </div>
        </div>
      </section>
      </template>
      <template #settings><ReaderSettingsPanel /></template>
      <template #outline>
        <ReaderNavigationPanel>
          <ReaderTocTree v-if="chapterOutline.items.length" :items="chapterOutline.items" :active-id="readingView === 'ocr' ? ocrActiveId : chapterOutline.activeId" :storage-key="`codeyun.pdf.chapter-outline.${documentDetail?.id}`" @select="navigateChapterOutline" />
          <p v-else class="outline-empty">当前页暂无章节大纲</p>
        </ReaderNavigationPanel>
      </template>
    </ReaderLayout>
    <ReaderContextMenu ref="contextMenu" :crop-controls="readingView === 'pdf'" :crop-enabled="cropEnabled" @crop="togglePageCrop" />

    <el-dialog v-model="shareDialogVisible" title="分享 PDF" width="420px">
      <div v-loading="shareLoading" class="share-panel">
        <div class="share-row">
          <span>公开查看</span>
          <el-switch v-model="publicShareEnabled" @change="handlePublicShareChange" />
        </div>
        <el-input v-if="publicShareEnabled" :model-value="publicUrl" readonly>
          <template #append>
            <el-button @click="copyPublicUrl">复制</el-button>
          </template>
        </el-input>
      </div>
    </el-dialog>
  </BookReaderSurface>
</template>

<script setup lang="ts">
import BookReaderSurface from '../library/BookReaderSurface.vue'
import { useReaderDock as useDockLayout, readerTabContext } from '../library/readerWorkspaceContext'
import { inject } from 'vue'
const workspaceTab = inject(readerTabContext, null)
import { pdfDockTools } from '../library/readerDockTools'
import ReaderSettingsPanel from '../library/ReaderSettingsPanel.vue'
import ReaderContextMenu from '../library/ReaderContextMenu.vue'
import ReaderTocTree from '../library/ReaderTocTree.vue'
import ReaderNavigationPanel from '../library/ReaderNavigationPanel.vue'
import { libraryReaderThemeClass } from '../library/readerTheme'
import { useReaderTreeSplit } from '../library/useReaderTreeSplit'
import { pdfChapterOutline } from './pdfChapterOutline'
import PdfContinuousReader from './PdfContinuousReader.vue'
import PdfPageCrop from './PdfPageCrop.vue'
import { pdfSectionRange } from './pdfSectionRange'
import ReaderLayout from '../library/ReaderDockLayout.vue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue';
import { useRoute } from 'vue-router';
import { ElMessage, ElMessageBox } from 'element-plus';
import {
  ArrowLeft,
  ArrowRight,
  MagicStick,
  Refresh,
  Share,
  ZoomIn,
  ZoomOut,
} from '@element-plus/icons-vue';
import {
  GlobalWorkerOptions,
  RenderingCancelledException,
  type PageViewport,
  getDocument,
  type PDFDocumentLoadingTask,
  type PDFDocumentProxy,
  type RenderTask,
} from 'pdfjs-dist';
import type { TextContent } from 'pdfjs-dist/types/src/display/api';
import pdfWorkerUrl from 'pdfjs-dist/build/pdf.worker.mjs?url';

import PdfOutlinePanel from './PdfOutlinePanel.vue';
import {formatCharacterCount} from './formatCharacterCount';
import PdfSectionReader from './PdfSectionReader.vue';
import PdfPageFind from './PdfPageFind.vue';
import PdfBookSearch, {type SearchScope} from './PdfBookSearch.vue';
import PdfBookOcrControl from './PdfBookOcrControl.vue';
import { useUserStore } from '@/store/userStore';
import { readPdfBinary, writePdfBinary, deletePdfBinary } from './pdfBinaryCache';
import PdfTextAnnotationLayer from './PdfTextAnnotationLayer.vue';
import {
  getPdfOcrTextStats,
  fetchPdfAccess,
  fetchPdfOutline,
  renewPdfReaderLease,
  savePdfOutline,

  fetchPdfPagePreview,
  type PdfOutlineEntry,
  clearMyPdfPageNotes,
  fetchPdfContentUrl,
  fetchPdfDocument,
  fetchPdfPageNote,
  updatePdfAccess,
  updatePdfPageNote,
  updatePdfUserState,
  type PdfAccessGrantUpdate,
  type PdfAccessResponse,
  type PdfDocumentDetail,
  type PdfPageNote,
  type PdfResourceRole,
  type PdfUserState,
} from '@/api/pdfDocuments';

// The public server previously served `.mjs` as `application/octet-stream` and
// cached that response as immutable. Keep a version on the module URL so
// browsers that saw the bad MIME response fetch the corrected worker again.
GlobalWorkerOptions.workerSrc = `${pdfWorkerUrl}?module-mime=1`;

const route = useRoute();
const props = defineProps<{ documentId?: number }>();
const PDFJS_WASM_URL = '/pdfjs/wasm/';
const VALID_SIDEBAR_TABS = ['outline', 'info', 'search', 'ocr'] as const;
const ZOOM_PERCENT_OPTIONS = [25, 50, 75, 100, 125, 150, 175, 200, 250, 300, 400] as const;

type PdfSidebarTab = typeof VALID_SIDEBAR_TABS[number];

interface ZoomAnchor {
  scrollElement: HTMLElement;
  clientX: number;
  clientY: number;
  ratioX: number;
  ratioY: number;
}

const documentDetail = ref<PdfDocumentDetail | null>(null);
const contentUrl = ref('');
const lastPdfLoadError = ref('');
const loading = ref(false);
const contentLoading = ref(false);
const pageRendering = ref(false);
const errorText = ref('');
const readerErrorText = ref('');
const currentPage = ref(1);
const renderedPage = ref(0);
const pageCount = ref(0);
const zoom = ref('page-width');
const renderedZoomPercent = ref(100);
const dock = useDockLayout('codeyun.reader.dock.pdf.v1', pdfDockTools);
const contextMenu = ref<InstanceType<typeof ReaderContextMenu>>();
const textStats = ref<{characters:number; recognized_pages:number} | null>(null);
const textStatsError = ref('');
let textStatsVersion = 0;
const readingView = ref<'pdf' | 'ocr'>('pdf');
const ocrReader = ref<InstanceType<typeof PdfSectionReader>>();
const ocrContextPage = ref(1);
const ocrActiveId = ref('');
watch(readingView, view => { if (view === 'ocr') { ocrContextPage.value = currentPage.value; ocrActiveId.value = ''; } }, {flush: 'sync'});
function handleOcrLocation(id: string, page: number) {
  ocrActiveId.value = id;
  currentPage.value = page;
  scheduleReaderStateSave();
}
const searchSurface = shallowRef<{root: HTMLElement} | null>(null);
const bookSearchScope = ref<SearchScope | null>(null);
const pageFindRequest = ref<{query:string; occurrence:number} | null>(null);
function openSectionSearch(id:string) {
  const entries = outlineEntries.value;
  const index = entries.findIndex(entry => entry.id === id);
  if (index < 0) return;
  const entry = entries[index];
  let boundary = index + 1;
  while (boundary < entries.length && entries[boundary].level > entry.level) boundary++;
  const start = entry.page || entries.slice(index + 1,boundary).find(item => item.page)?.page;
  if (!start) { ElMessage.info('该目录没有页码，暂不能确定搜索范围'); return; }
  const next = entries.slice(boundary).find(item => item.page != null && item.page >= start)?.page;
  bookSearchScope.value = {title:entry.title,start,end:next ? Math.max(start,next - 1) : pageInputMax.value};
  dock.open('search');
}
let searchNavigationVersion = 0;
async function navigateSearchResult(hit:{page:number; occurrence:number; query:string}) {
  const version = ++searchNavigationVersion;
  readingView.value = 'pdf';
  await goToPage(hit.page);
  if (version === searchNavigationVersion) pageFindRequest.value = {query:hit.query,occurrence:hit.occurrence};
}
const { outlineLevel, setOutlineLevel } = useReaderTreeSplit(() => `pdf:${props.documentId}`);
const outlineEntries = ref<PdfOutlineEntry[]>([]);
const selectedSectionId = ref('');
const continuousReader = ref<InstanceType<typeof PdfContinuousReader>>();
const sectionRange = computed(() => pdfSectionRange(outlineEntries.value, selectedSectionId.value, pageCount.value || documentDetail.value?.metadata.page_count || 0));
function selectPdfSection(id: string) {
  selectedSectionId.value = id;
  renderVersion++; renderTask?.cancel(); renderTask = null; pageRendering.value = false;
  pdfTextContent.value = null; pdfTextViewport.value = null; searchSurface.value = null;
  const range = sectionRange.value;
  if (range) {
    currentPage.value = range.start;
    if (readingView.value === 'ocr') ocrContextPage.value = range.start;
    void nextTick(() => continuousReader.value?.scrollToPage(range.start));
    scheduleReaderStateSave();
  }
}
function handleContinuousPage(page: number) {
  if (readingView.value !== 'pdf') return;
  if (page === currentPage.value) return;
  currentPage.value = page;
  renderedPage.value = page;
  scheduleReaderStateSave();
}
const chapterOutline = computed(() => pdfChapterOutline(outlineEntries.value, readingView.value === 'ocr' ? ocrContextPage.value : currentPage.value, outlineLevel.value));
function navigateChapterOutline(id: string) {
  if (readingView.value === 'ocr') { void ocrReader.value?.scrollToSection(id); return; }
  const entry = outlineEntries.value.find(item => item.id === id);
  if (entry?.page) void goToPage(entry.page);
}
const outlineRevision = ref('');
const outlineSaving = ref(false);
const outlineCanEmbed = ref(false);
const canEditOutline = computed(() => ['editor', 'manager'].includes(documentDetail.value?.access.role || ''));
const bootstrapPreview = ref('');
let previewAbort: AbortController | null = null;
let backgroundLoadTimer: number | null = null;
let documentLoadVersion = 0;
const outlineLoading = ref(false);
const outlineErrorText = ref('');
const pageNote = ref<PdfPageNote | null>(null);
const pageNoteContent = ref('');
const pageNoteLoading = ref(false);
const pageNoteSaving = ref(false);
const pageNoteErrorText = ref('');
const pageNoteLoadedPage = ref(0);
const shareDialogVisible = ref(false);
const shareLoading = ref(false);
const accessInfo = ref<PdfAccessResponse | null>(null);
const publicShareEnabled = ref(false);
const stageRef = ref<HTMLElement | null>(null);
const canvasRef = ref<HTMLCanvasElement | null>(null);
const pdfDocument = shallowRef<PDFDocumentProxy | null>(null);
const pdfTextContent = shallowRef<TextContent | null>(null);
const pdfTextViewport = shallowRef<PageViewport | null>(null);

let resizeObserver: ResizeObserver | null = null;
let loadingTask: PDFDocumentLoadingTask | null = null;
let renderTask: RenderTask | null = null;
let renderVersion = 0;
let outlineLoadVersion = 0;
let pageNoteLoadVersion = 0;
let stateSaveTimer: number | null = null;
let pageNoteSaveTimer: number | null = null;
let pageNoteApplying = false;
let pendingPageNoteSave: { pdfId: number; pageNumber: number; contentHtml: string } | null = null;
let pendingWheelZoom: { direction: 'in' | 'out'; anchor: ZoomAnchor | null } | null = null;

const pdfId = computed(() => normalizePositiveInt(props.documentId));
const cropEnabled = ref(false);
watch(pdfId, id => {
  try { cropEnabled.value = localStorage.getItem(`codeyun.pdf.crop.${id}`) === 'true'; } catch { cropEnabled.value = false; }
}, { immediate: true });
function togglePageCrop() {
  cropEnabled.value = !cropEnabled.value;
  try { localStorage.setItem(`codeyun.pdf.crop.${pdfId.value}`, String(cropEnabled.value)); } catch { /* Session-only preference. */ }
}

const canManageAccess = computed(() => Boolean(documentDetail.value?.access.capabilities.can_manage_access));
const canUsePageNotes = computed(() => Boolean(documentDetail.value?.access.capabilities.can_update_page_notes));
const canEditPageNote = computed(() => canUsePageNotes.value && (pageNote.value?.can_edit ?? true));
const pageInputMax = computed(() => Math.max(pageCount.value || currentPage.value || 1, 1));
const canGoPrevious = computed(() => Boolean(pdfDocument.value && currentPage.value > 1 && !pageRendering.value));
const canGoNext = computed(() => Boolean(
  pdfDocument.value
  && pageCount.value > 0
  && currentPage.value < pageCount.value
  && !pageRendering.value,
));
const canGoRandomPage = computed(() => Boolean(
  pdfDocument.value
  && pageCount.value > 1
  && !pageRendering.value,
));
const canZoomOut = computed(() => Boolean(
  pdfDocument.value
  && renderedZoomPercent.value > ZOOM_PERCENT_OPTIONS[0]
  && !pageRendering.value,
));
const canZoomIn = computed(() => Boolean(
  pdfDocument.value
  && renderedZoomPercent.value < ZOOM_PERCENT_OPTIONS[ZOOM_PERCENT_OPTIONS.length - 1]
  && !pageRendering.value,
));
const publicUrl = computed(() => `${window.location.origin}/reader?id=${documentDetail.value?.id ?? ''}`);
const accessRoleLabel = computed(() => getRoleLabel(documentDetail.value?.access.role ?? 'none'));
const zoomLabel = computed(() => {
  const option = [
    ['page-width', '适合宽度'],
    ['page-fit', '适合页面'],
  ].find(([value]) => value === zoom.value);
  return option?.[1] ?? `${zoom.value}%`;
});
const pageNoteEditorKey = computed(() => `${documentDetail.value?.id ?? 'pdf'}:${pageNoteLoadedPage.value || currentPage.value}`);

function normalizePositiveInt(value: unknown): number | null {
  const raw = Array.isArray(value) ? value[0] : value;
  const numeric = Number(raw);
  return Number.isInteger(numeric) && numeric > 0 ? numeric : null;
}

function getRoleLabel(role: PdfResourceRole) {
  switch (role) {
    case 'manager':
      return '管理者';
    case 'editor':
      return '可编辑';
    case 'viewer':
      return '可查看';
    case 'deny':
      return '已拒绝';
    default:
      return '无权限';
  }
}

function formatBytes(value?: number | null) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return '--';
  if (value < 1024) return `${value} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let size = value / 1024;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  return `${size.toFixed(size >= 10 ? 1 : 2)} ${units[unitIndex]}`;
}

function clampPage(value: number) {
  const upper = Math.max(pageCount.value || value || 1, 1);
  return Math.min(Math.max(Math.floor(value || 1), 1), upper);
}

function isPdfSidebarTab(value: unknown): value is PdfSidebarTab {
  return VALID_SIDEBAR_TABS.includes(value as PdfSidebarTab);
}

function applyUserState(state?: PdfUserState | null) {
  currentPage.value = Math.max(1, Math.floor(state?.current_page || 1));
  zoom.value = state?.zoom && state.zoom !== 'auto' ? state.zoom : 'page-width';
  selectedSectionId.value = typeof state?.state_json?.section_id === 'string' ? state.state_json.section_id : '';
  const savedSidebarTab = state?.state_json?.sidebar_tab;
  const legacyTool = isPdfSidebarTab(savedSidebarTab) && savedSidebarTab !== 'outline' ? savedSidebarTab : 'toc';
  dock.importLegacy(legacyTool, state?.sidebar_open ?? true);
}

function clearCanvas() {
  const canvas = canvasRef.value;
  if (!canvas) return;
  const context = canvas.getContext('2d');
  if (context) {
    context.clearRect(0, 0, canvas.width, canvas.height);
  }
  canvas.width = 0;
  canvas.height = 0;
  canvas.style.width = '0px';
  canvas.style.height = '0px';
}

async function destroyPdfRuntime() {
  if (backgroundLoadTimer != null) window.clearTimeout(backgroundLoadTimer);
  backgroundLoadTimer = null;
  renderVersion += 1;
  if (renderTask) {
    renderTask.cancel();
    renderTask = null;
  }
  const oldTask = loadingTask;
  const oldDocument = pdfDocument.value;
  loadingTask = null;
  pdfDocument.value = null;
  pageCount.value = 0;
  renderedPage.value = 0;
  pageRendering.value = false;
  clearCanvas();
  pdfTextContent.value = null;
  pdfTextViewport.value = null;
  if (oldTask) await oldTask.destroy().catch(() => undefined);
  else if (oldDocument) await oldDocument.destroy().catch(() => undefined);
}

function getStageAvailableSize() {
  const stage = stageRef.value;
  const width = Math.max((stage?.clientWidth ?? 900) - 56, 320);
  const height = Math.max((stage?.clientHeight ?? 700) - 100, 320);
  return { width, height };
}

// PDF uses 72 points/inch; CSS uses 96 pixels/inch. DPR only controls sharpness.
const PDF_TO_CSS_UNITS = 96 / 72;
function resolvePageScale(baseWidth: number, baseHeight: number) {
  if (/^\d+$/.test(zoom.value)) {
    return PDF_TO_CSS_UNITS * Math.max(0.25, Math.min(Number(zoom.value) / 100, 4));
  }
  const available = getStageAvailableSize();
  const widthScale = available.width / baseWidth;
  if (zoom.value === 'page-fit') {
    return Math.max(.25 * PDF_TO_CSS_UNITS, Math.min(widthScale, available.height / baseHeight, 4 * PDF_TO_CSS_UNITS));
  }
  return Math.max(.25 * PDF_TO_CSS_UNITS, Math.min(widthScale, 4 * PDF_TO_CSS_UNITS));
}

function getCurrentZoomPercent() {
  if (/^\d+$/.test(zoom.value)) {
    return Number(zoom.value);
  }
  return renderedZoomPercent.value;
}

function getNearestZoomPercent(direction: 'in' | 'out') {
  const currentPercent = getCurrentZoomPercent();
  if (direction === 'in') {
    return ZOOM_PERCENT_OPTIONS.find((value) => value > currentPercent + 0.1)
      ?? ZOOM_PERCENT_OPTIONS[ZOOM_PERCENT_OPTIONS.length - 1];
  }
  return [...ZOOM_PERCENT_OPTIONS].reverse().find((value) => value < currentPercent - 0.1)
    ?? ZOOM_PERCENT_OPTIONS[0];
}

function getPdfScrollElement() {
  return stageRef.value?.querySelector<HTMLElement>('.pdf-page-scroll') ?? null;
}

function clampRatio(value: number) {
  if (!Number.isFinite(value)) return 0.5;
  return Math.min(Math.max(value, 0), 1);
}

function createZoomAnchor(event: WheelEvent): ZoomAnchor | null {
  const canvas = canvasRef.value;
  const scrollElement = getPdfScrollElement();
  if (!canvas || !scrollElement || canvas.clientWidth <= 0 || canvas.clientHeight <= 0) {
    return null;
  }
  const rect = canvas.getBoundingClientRect();
  return {
    scrollElement,
    clientX: event.clientX,
    clientY: event.clientY,
    ratioX: clampRatio((event.clientX - rect.left) / rect.width),
    ratioY: clampRatio((event.clientY - rect.top) / rect.height),
  };
}

function restoreZoomAnchor(anchor: ZoomAnchor | null) {
  if (!anchor || !canvasRef.value) return;
  const rect = canvasRef.value.getBoundingClientRect();
  const targetX = rect.left + rect.width * anchor.ratioX;
  const targetY = rect.top + rect.height * anchor.ratioY;
  anchor.scrollElement.scrollLeft += targetX - anchor.clientX;
  anchor.scrollElement.scrollTop += targetY - anchor.clientY;
}

async function setZoomValue(nextZoom: string, anchor: ZoomAnchor | null = null) {
  if (!pdfDocument.value) return;
  zoom.value = nextZoom;
  await nextTick();
  await renderCurrentPage();
  await nextTick();
  restoreZoomAnchor(anchor);
}

async function renderCurrentPage(options?: { persist?: boolean }) {
  await nextTick();
  if (sectionRange.value && pdfDocument.value) {
    await nextTick();
    if (options?.persist !== false) scheduleReaderStateSave();
    return;
  }
  const documentProxy = pdfDocument.value;
  const canvas = canvasRef.value;
  if (!documentProxy || !canvas) return;

  const targetPage = clampPage(currentPage.value);
  currentPage.value = targetPage;
  const version = ++renderVersion;
  readerErrorText.value = '';

  if (renderTask) {
    renderTask.cancel();
    renderTask = null;
  }

  pageRendering.value = true;
  pdfTextContent.value = null;
  pdfTextViewport.value = null;
  try {
    const page = await documentProxy.getPage(targetPage);
    if (version !== renderVersion) return;

    const baseViewport = page.getViewport({ scale: 1 });
    const cssScale = resolvePageScale(baseViewport.width, baseViewport.height);
    renderedZoomPercent.value = Math.round(cssScale / PDF_TO_CSS_UNITS * 100);
    const outputScale = Math.max(window.devicePixelRatio || 1, 1);
    const cssViewport = page.getViewport({ scale: cssScale });
    const context = canvas.getContext('2d');
    if (!context) {
      throw new Error('无法创建 PDF 渲染画布');
    }

    canvas.width = Math.floor(cssViewport.width * outputScale);
    canvas.height = Math.floor(cssViewport.height * outputScale);
    canvas.style.width = `${Math.floor(cssViewport.width)}px`;
    canvas.style.height = `${Math.floor(cssViewport.height)}px`;
    context.setTransform(1, 0, 0, 1, 0, 0);
    context.clearRect(0, 0, canvas.width, canvas.height);

    const task = page.render({
      canvas,
      canvasContext: context,
      viewport: cssViewport,
      transform: outputScale === 1 ? undefined : [outputScale, 0, 0, outputScale, 0, 0],
    });
    const textContentPromise = page.getTextContent();
    renderTask = task;
    await task.promise;
    if (version !== renderVersion) return;
    const textContent = await textContentPromise;
    if (version !== renderVersion) return;

    renderedPage.value = targetPage;
    pdfTextContent.value = textContent;
    pdfTextViewport.value = cssViewport;
    if (options?.persist !== false) {
      scheduleReaderStateSave();
    }
  } catch (error) {
    if (version !== renderVersion || error instanceof RenderingCancelledException) return;
    console.warn('Failed to render PDF page:', error);
    readerErrorText.value = 'PDF 页面渲染失败';
  } finally {
    if (version === renderVersion) {
      renderTask = null;
      pageRendering.value = false;
      if (pendingWheelZoom) {
        const nextWheelZoom = pendingWheelZoom;
        pendingWheelZoom = null;
        window.setTimeout(() => {
          applyDirectionalZoom(nextWheelZoom.direction, nextWheelZoom.anchor);
        }, 0);
      }
    }
  }
}

async function loadPdfOutline() {
  const id = documentDetail.value?.id;
  if (!id) return;
  const version = ++outlineLoadVersion;
  outlineLoading.value = true;
  outlineErrorText.value = '';
  try {
    const result = await fetchPdfOutline(id);
    if (version !== outlineLoadVersion || documentDetail.value?.id !== id) return;
    outlineEntries.value = result.entries;
    outlineRevision.value = result.revision;
    outlineCanEmbed.value = result.can_embed;
  } catch {
    if (version === outlineLoadVersion) outlineErrorText.value = '目录加载失败，请重试';
  } finally {
    if (version === outlineLoadVersion) outlineLoading.value = false;
  }
}

async function changeOutline(entries: PdfOutlineEntry[]) {
  const id = documentDetail.value?.id;
  if (!id || outlineSaving.value) return;
  outlineSaving.value = true;
  try {
    const result = await savePdfOutline(id, outlineRevision.value, entries);
    if (documentDetail.value?.id !== id) return;
    outlineEntries.value = result.entries;
    outlineRevision.value = result.revision;
  } catch (error: any) {
    if (documentDetail.value?.id === id) {
      outlineErrorText.value = error.response?.data?.detail || '目录保存失败，请重新加载后重试';
    }
  } finally {
    outlineSaving.value = false;
  }
}


function clearBootstrapPreview() {
  previewAbort?.abort();
  previewAbort = null;
  if (bootstrapPreview.value) URL.revokeObjectURL(bootstrapPreview.value);
  bootstrapPreview.value = '';
}

function showBootstrapPreview(id: number, page: number) {
  clearBootstrapPreview();
  const controller = new AbortController();
  previewAbort = controller;
  void fetchPdfPagePreview(id, page, controller.signal).then(blob => {
    if (!controller.signal.aborted && documentDetail.value?.id === id && !renderedPage.value) {
      bootstrapPreview.value = URL.createObjectURL(blob);
    }
  }).catch(() => { /* Preview is optional; the PDF remains the source of truth. */ });
}

function pdfCacheKey() {
  const userId = useUserStore().user?.id;
  const detail = documentDetail.value;
  return userId && detail?.content_hash ? `${userId}:${detail.id}:${detail.content_hash}` : '';
}

async function loadPdfContent(url: string): Promise<boolean> {
  const generation = documentLoadVersion;
  await destroyPdfRuntime();
  if (generation !== documentLoadVersion) return false;
  const cacheKey = pdfCacheKey();
  if (documentDetail.value && canUsePageNotes.value) {
    showBootstrapPreview(documentDetail.value.id, currentPage.value);
  }
  if (!url) return false;

  contentLoading.value = true;
  readerErrorText.value = '';
  lastPdfLoadError.value = '';
  try {
    const cached = cacheKey ? await readPdfBinary(cacheKey) : null;
    if (generation !== documentLoadVersion) return false;
    loadingTask = getDocument({
      ...(cached ? { data: cached } : { url }),
      // The content URL is signed and supports byte ranges.  Keep streaming
      // disabled so large PDFs do not have to cross the public tunnel in full
      // before PDF.js can resolve the cross-reference table and first page.
      disableStream: true,
      disableAutoFetch: true,
      rangeChunkSize: 1024 * 1024,
      useSystemFonts: true,
      wasmUrl: PDFJS_WASM_URL,
    });
    const documentProxy = await loadingTask.promise;
    if (generation !== documentLoadVersion) { await documentProxy.destroy(); return false; }
    pdfDocument.value = documentProxy;
    pageCount.value = documentProxy.numPages;
    currentPage.value = clampPage(currentPage.value);
    await nextTick();
    await renderCurrentPage({ persist: false });
    if (generation !== documentLoadVersion) return false;
    clearBootstrapPreview();
    if (!cached && (documentDetail.value?.size_bytes ?? Infinity) <= 100 * 1024 * 1024) {
      // User-visible page first. PDF.js reuses its range buffers when filling in
      // the rest, then IndexedDB makes subsequent opens independent of signed URLs.
      backgroundLoadTimer = window.setTimeout(() => {
        backgroundLoadTimer = null;
        if (pdfDocument.value !== documentProxy) return;
        void documentProxy.getData().then(data => {
          if (cacheKey && pdfDocument.value === documentProxy) return writePdfBinary(cacheKey, data);
        }).catch(() => { /* Closing a reader cancels background transfer. */ });
      }, 1500);
    }
    return true;
  } catch (error) {
    if (generation !== documentLoadVersion) return false;
    if (cacheKey) await deletePdfBinary(cacheKey);
    console.warn('Failed to load PDF content:', error);
    lastPdfLoadError.value = error instanceof Error
      ? error.message.slice(0, 160)
      : String(error || '未知错误').slice(0, 160);
    return false;
  } finally {
    if (generation === documentLoadVersion) contentLoading.value = false;
  }
}

async function loadPdfDocument() {
  bookSearchScope.value = null;
  pageFindRequest.value = null;
  searchNavigationVersion++;
  const generation = ++documentLoadVersion;
  outlineLoadVersion += 1;
  outlineEntries.value = [];
  outlineLoading.value = true;
  outlineErrorText.value = '';
  clearBootstrapPreview();
  flushPendingPageNoteSave();
  resetPageNoteState();
  if (pdfId.value == null) {
    errorText.value = 'PDF 地址无效';
    documentDetail.value = null;
    contentUrl.value = '';
    await destroyPdfRuntime();
    return;
  }

  loading.value = true;
  errorText.value = '';
  try {
    const detail = await fetchPdfDocument(pdfId.value);
    if (generation !== documentLoadVersion) return;
    if (!detail) {
      errorText.value = 'PDF 不存在或不可访问';
      documentDetail.value = null;
      contentUrl.value = '';
      await destroyPdfRuntime();
      return;
    }
    documentDetail.value = detail;
    workspaceTab?.title(detail.display_title || detail.title);
    if (!workspaceTab) document.title = `${detail.title || 'PDF'} - CodeYun`;
    applyUserState(detail.my_state);
    void loadPdfOutline();
    loading.value = false;
    await reloadContentUrl();
  } catch (error) {
    if (generation !== documentLoadVersion) return;
    console.warn('Failed to load PDF document:', error);
    outlineLoading.value = false;
    outlineErrorText.value = '图书加载失败，请重试';
    errorText.value = '没有权限访问该 PDF';
    documentDetail.value = null;
    contentUrl.value = '';
    await destroyPdfRuntime();
  } finally {
    if (generation === documentLoadVersion) loading.value = false;
  }
}

async function reloadContentUrl() {
  if (!documentDetail.value) return;
  const id = documentDetail.value.id;
  const generation = documentLoadVersion;
  contentLoading.value = true;
  readerErrorText.value = '';
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      if (generation !== documentLoadVersion) return;
      const result = await fetchPdfContentUrl(id);
      if (generation !== documentLoadVersion) return;
      contentUrl.value = result.url;
      if (await loadPdfContent(result.url)) {
        contentLoading.value = false;
        return;
      }
    } catch (error) {
      console.warn('Failed to load PDF content URL:', error);
    }
    if (generation !== documentLoadVersion) return;
    if (attempt < 2) {
      await new Promise((resolve) => window.setTimeout(resolve, 400 * (attempt + 1)));
    }
  }
  contentLoading.value = false;
  readerErrorText.value = lastPdfLoadError.value
    ? `PDF 内容加载失败：${lastPdfLoadError.value}`
    : 'PDF 内容加载失败，请刷新后重试';
  ElMessage.error('PDF 内容加载失败');
}

function scheduleReaderStateSave() {
  if (!documentDetail.value?.access.capabilities.can_update_state) return;
  if (stateSaveTimer != null) {
    window.clearTimeout(stateSaveTimer);
  }
  stateSaveTimer = window.setTimeout(() => {
    stateSaveTimer = null;
    void persistReaderState();
  }, 300);
}

async function persistReaderState() {
  if (workspaceTab && !workspaceTab.canPersist()) return
  if (!documentDetail.value?.access.capabilities.can_update_state) return;
  try {
    const stateJson = {
      ...(documentDetail.value.my_state?.state_json || {}),
      section_id: selectedSectionId.value,
    };
    const state = await updatePdfUserState(documentDetail.value.id, {
      current_page: clampPage(currentPage.value),
      zoom: zoom.value || 'page-width',
      // 兼容旧客户端的进度协议，新工具布局由独立偏好保存。
      sidebar_open: dock.regionOpen('left'),
      state_json: stateJson,
    });
    documentDetail.value.my_state = state;
  } catch (error) {
    console.warn('Failed to save PDF reader state:', error);
  }
}

function applyPageNote(nextNote: PdfPageNote | null, pageNumber: number) {
  pageNoteApplying = true;
  pageNote.value = nextNote;
  pageNoteLoadedPage.value = pageNumber;
  pageNoteContent.value = nextNote?.content_html || '';
  nextTick(() => {
    pageNoteApplying = false;
  });
}

function resetPageNoteState() {
  pageNoteLoadVersion += 1;
  pageNote.value = null;
  pageNoteContent.value = '';
  pageNoteLoadedPage.value = 0;
  pageNoteLoading.value = false;
  pageNoteSaving.value = false;
  pageNoteErrorText.value = '';
  pendingPageNoteSave = null;
  if (pageNoteSaveTimer != null) {
    window.clearTimeout(pageNoteSaveTimer);
    pageNoteSaveTimer = null;
  }
}

function flushPendingPageNoteSave() {
  if (pageNoteSaveTimer != null) {
    window.clearTimeout(pageNoteSaveTimer);
    pageNoteSaveTimer = null;
  }
  if (pendingPageNoteSave) {
    void persistPendingPageNote();
  }
}

async function loadCurrentPageNote() {
  const detail = documentDetail.value;
  if (!detail || !canUsePageNotes.value) {
    resetPageNoteState();
    return;
  }

  const targetPage = clampPage(currentPage.value);
  if (pageNoteLoadedPage.value === targetPage && pageNote.value != null) return;

  const version = ++pageNoteLoadVersion;
  pageNoteLoading.value = true;
  pageNoteErrorText.value = '';
  try {
    const note = await fetchPdfPageNote(detail.id, targetPage);
    if (version !== pageNoteLoadVersion) return;
    applyPageNote(note, targetPage);
  } catch (error) {
    console.warn('Failed to load PDF page note:', error);
    if (version === pageNoteLoadVersion) {
      applyPageNote(null, targetPage);
      pageNoteErrorText.value = '未能读取页面笔记，请重试';
    }
  } finally {
    if (version === pageNoteLoadVersion) {
      pageNoteLoading.value = false;
    }
  }
}

function schedulePageNoteSave(pageNumber: number, contentHtml: string) {
  const detail = documentDetail.value;
  if (!detail || !canEditPageNote.value) return;
  if (
    pendingPageNoteSave
    && (pendingPageNoteSave.pdfId !== detail.id || pendingPageNoteSave.pageNumber !== pageNumber)
  ) {
    flushPendingPageNoteSave();
  }
  pendingPageNoteSave = {
    pdfId: detail.id,
    pageNumber,
    contentHtml,
  };
  if (pageNoteSaveTimer != null) {
    window.clearTimeout(pageNoteSaveTimer);
  }
  pageNoteSaveTimer = window.setTimeout(() => {
    pageNoteSaveTimer = null;
    void persistPendingPageNote();
  }, 600);
}

async function persistPendingPageNote() {
  if (workspaceTab && !workspaceTab.canPersist()) return;
  const pending = pendingPageNoteSave;
  if (!pending) return;
  pendingPageNoteSave = null;
  pageNoteSaving.value = true;
  try {
    const saved = await updatePdfPageNote(pending.pdfId, pending.pageNumber, {
      content_html: pending.contentHtml,
    });
    if (documentDetail.value?.id === pending.pdfId && pageNoteLoadedPage.value === pending.pageNumber) {
      pageNote.value = saved;
      // Keep the editor's local value: server-normalized empty HTML can trigger
      // another editor change, and an older response must not erase new typing.
    }
  } catch (error) {
    console.warn('Failed to save PDF page note:', error);
    pageNoteErrorText.value = '页面笔记保存失败';
  } finally {
    pageNoteSaving.value = false;
  }
}

function handlePageNoteContentUpdate(value: string) {
  const previous = pageNoteContent.value;
  pageNoteContent.value = value;
  if (pageNoteApplying || pageNoteLoading.value || !canEditPageNote.value) return;
  if (normalizePageNoteContent(value) === normalizePageNoteContent(previous)) return;
  const targetPage = pageNoteLoadedPage.value || currentPage.value;
  schedulePageNoteSave(targetPage, value);
}

function normalizePageNoteContent(html: string): string {
  // Rich-text editors represent an empty value as <p><br></p>; the API returns
  // ''. Treat both as empty while preserving media-only notes and formatting.
  const container = document.createElement('template');
  container.innerHTML = html;
  if (!container.content.querySelector('img,video,audio,iframe')
      && !(container.content.textContent || '').replace(/[\u200b\u00a0]/g, ' ').trim()) return '';
  return html;
}

async function clearAllMyNotes() {
  const detail = documentDetail.value;
  if (!detail || !canEditPageNote.value) return;
  try {
    await ElMessageBox.confirm(
      '这只会清空你在本书中的全部页面笔记，不影响原书或其他读者。此操作不可撤销。',
      '清空全部笔记',
      { type: 'warning', confirmButtonText: '清空', cancelButtonText: '取消' },
    );
    resetPageNoteState();
    const result = await clearMyPdfPageNotes(detail.id);
    await loadCurrentPageNote();
    ElMessage.success(`已清空 ${result.deleted_count} 条笔记`);
  } catch (error) {
    if (error === 'cancel' || error === 'close') return;
    ElMessage.error('清空笔记失败');
  }
}

async function goToPage(page: number) {
  if (readingView.value === 'ocr') {
    currentPage.value = page; ocrContextPage.value = page;
    await nextTick(); await ocrReader.value?.scrollToPage(page); return;
  }
  if (!pdfDocument.value) return;
  const nextPage = clampPage(page);
  if (sectionRange.value) {
    if (nextPage >= sectionRange.value.start && nextPage <= sectionRange.value.end) {
      currentPage.value = nextPage;
      await nextTick();
      continuousReader.value?.scrollToPage(nextPage);
      scheduleReaderStateSave();
      return;
    }
    selectedSectionId.value = '';
    await nextTick();
  }
  if (nextPage === currentPage.value && renderedPage.value === nextPage && canvasRef.value?.width && canvasRef.value?.height) return;
  currentPage.value = nextPage;
  await renderCurrentPage();
}

function goPreviousPage() {
  if (!canGoPrevious.value) return;
  void goToPage(currentPage.value - 1);
}

function goNextPage() {
  if (!canGoNext.value) return;
  void goToPage(currentPage.value + 1);
}

function isTextInputTarget(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) return false;
  return Boolean(target.closest('input, textarea, select, [contenteditable="true"], [role="textbox"]'));
}

function handleReaderKeydown(event: KeyboardEvent) {
  if (workspaceTab && !workspaceTab.active.value) return
  if (
    event.defaultPrevented
    || event.isComposing
    || event.ctrlKey
    || event.metaKey
    || event.altKey
    || isTextInputTarget(event.target)
  ) return;

  if (event.key === 'ArrowLeft' && canGoPrevious.value) {
    event.preventDefault();
    goPreviousPage();
  } else if (event.key === 'ArrowRight' && canGoNext.value) {
    event.preventDefault();
    goNextPage();
  }
}

function goRandomPage() {
  if (!canGoRandomPage.value) return;
  const count = Math.max(pageCount.value || 1, 1);
  let targetPage = Math.floor(Math.random() * count) + 1;
  if (count > 1 && targetPage === currentPage.value) {
    targetPage = (targetPage % count) + 1;
  }
  void goToPage(targetPage);
}

function handlePageInputChange() {
  void goToPage(currentPage.value);
}

async function handleZoomChange() {
  await setZoomValue(zoom.value);
}

function zoomOutPage() {
  if (!canZoomOut.value) return;
  void setZoomValue(String(getNearestZoomPercent('out')));
}

function zoomInPage() {
  if (!canZoomIn.value) return;
  void setZoomValue(String(getNearestZoomPercent('in')));
}

function applyDirectionalZoom(direction: 'in' | 'out', anchor: ZoomAnchor | null = null) {
  if (direction === 'in' && !canZoomIn.value) return;
  if (direction === 'out' && !canZoomOut.value) return;
  void setZoomValue(String(getNearestZoomPercent(direction)), anchor);
}

function handleStageWheel(event: WheelEvent) {
  if (readingView.value !== 'pdf') return;
  if (!event.ctrlKey || !pdfDocument.value) return;
  event.preventDefault();
  const direction = event.deltaY < 0 ? 'in' : 'out';
  const anchor = createZoomAnchor(event);
  if (pageRendering.value) {
    pendingWheelZoom = { direction, anchor };
    return;
  }
  applyDirectionalZoom(direction, anchor);
}

async function refreshReaderLayout() {
  if (workspaceTab && !workspaceTab.active.value) return;
  await nextTick();
  await renderCurrentPage({ persist: false });
}

async function openShareDialog() {
  if (!documentDetail.value) return;
  shareDialogVisible.value = true;
  shareLoading.value = true;
  try {
    accessInfo.value = await fetchPdfAccess(documentDetail.value.id);
    publicShareEnabled.value = accessInfo.value.grants.some(
      (grant) => grant.subject_type === 'anonymous' && grant.role === 'viewer',
    );
  } catch (error) {
    console.warn('Failed to load PDF access:', error);
    ElMessage.error('读取分享设置失败');
  } finally {
    shareLoading.value = false;
  }
}

async function handlePublicShareChange(value: string | number | boolean) {
  if (!documentDetail.value || !accessInfo.value) return;
  shareLoading.value = true;
  try {
    const grants: PdfAccessGrantUpdate[] = accessInfo.value.grants
      .filter((grant) => grant.subject_type !== 'anonymous')
      .map((grant) => ({
        subject_type: grant.subject_type,
        subject_user_id: grant.subject_user_id ?? null,
        username: grant.username || undefined,
        role: grant.role,
      }));
    if (Boolean(value)) {
      grants.push({ subject_type: 'anonymous', subject_user_id: null, username: undefined, role: 'viewer' });
    }
    accessInfo.value = await updatePdfAccess(documentDetail.value.id, grants);
    publicShareEnabled.value = Boolean(value);
    ElMessage.success(publicShareEnabled.value ? '公开查看已开启' : '公开查看已关闭');
  } catch (error) {
    console.warn('Failed to update PDF access:', error);
    publicShareEnabled.value = !Boolean(value);
    ElMessage.error('更新分享设置失败');
  } finally {
    shareLoading.value = false;
  }
}

async function copyPublicUrl() {
  if (!publicUrl.value) return;
  try {
    await navigator.clipboard.writeText(publicUrl.value);
    ElMessage.success('链接已复制');
  } catch {
    ElMessage.warning('当前浏览器不允许自动复制');
  }
}

watch(() => [documentDetail.value?.id, dock.visible('info')] as const, async ([id, open]) => {
  const version = ++textStatsVersion;
  if (!id || !open) return;
  textStats.value = null; textStatsError.value = '';
  try {
    const result = await getPdfOcrTextStats(id);
    if (version === textStatsVersion) textStats.value = result;
  } catch { if (version === textStatsVersion) textStatsError.value = '暂不可用'; }
});

watch(pdfId, () => {
  void loadPdfDocument();
});

watch(() => workspaceTab?.active.value, active => {
  if (!workspaceTab) return;
  if (active) void refreshReaderLayout();
  else { void persistReaderState(); flushPendingPageNoteSave(); }
});

let readerLeaseTimer: ReturnType<typeof setInterval> | undefined;
function renewReaderLease() {
  const id = documentDetail.value?.id;
  if (id) void renewPdfReaderLease(id).catch(() => undefined);
}
onMounted(() => {
  readerLeaseTimer = setInterval(renewReaderLease, 120000);
  window.addEventListener('keydown', handleReaderKeydown);
  resizeObserver = new ResizeObserver(() => {
    if (workspaceTab && !workspaceTab.active.value) return;
    if (stageRef.value?.closest('.is-resizing')) return;
    if (zoom.value === 'page-width' || zoom.value === 'page-fit') {
      void renderCurrentPage({ persist: false });
    }
  });
  if (stageRef.value) {
    resizeObserver.observe(stageRef.value);
  }
  void loadPdfDocument();
});

onBeforeUnmount(() => {
  void persistReaderState();
  clearInterval(readerLeaseTimer);
  documentLoadVersion += 1;
  clearBootstrapPreview();
  window.removeEventListener('keydown', handleReaderKeydown);
  if (stateSaveTimer != null) {
    window.clearTimeout(stateSaveTimer);
    stateSaveTimer = null;
  }
  if (pageNoteSaveTimer != null) {
    window.clearTimeout(pageNoteSaveTimer);
    pageNoteSaveTimer = null;
  }
  flushPendingPageNoteSave();
  resizeObserver?.disconnect();
  resizeObserver = null;
  void destroyPdfRuntime();
});
</script>

<style scoped>
.bootstrap-preview { display: block; max-width: 100%; max-height: calc(100vh - 130px); object-fit: contain; }
.pdf-resource-page {
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: var(--reader-content);
  color: var(--reader-text);
}

.pdf-toolbar {
  box-sizing: border-box;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  min-height: 28px;
  padding: 0;

}

.pdf-toolbar-left,
.pdf-content-toolbar,
.pdf-toolbar-right {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.pdf-content-toolbar { justify-content: center; flex-wrap: wrap; flex: none; padding: 4px 12px; border-bottom: 1px solid var(--reader-border); background: var(--reader-content); }
.outline-empty { font-size: 12px; color: var(--reader-muted); }
.pdf-canvas, .bootstrap-preview { filter: var(--preview-page-filter); }

.pdf-toolbar-right {
  justify-content: flex-end;
}

.pdf-title {
  overflow: hidden;
  color: var(--reader-heading);
  font-size: 16px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.page-number-input {
  width: 96px;
}

.page-total {
  min-width: 48px;
  color: var(--reader-muted);
  font-size: 13px;
}

.zoom-controls {
  display: inline-flex;
  align-items: center;
  gap: 2px;
}

.zoom-select {
  width: 112px;
}

.pdf-stage {
  display: flex;
  flex-direction: column;
  position: relative;
  flex: 1;
  min-width: 0;
  min-height: 0;
  background: var(--preview-stage);
  outline: none;
}

.pdf-page-scroll {
  flex: 1;
  min-height: 0;
  box-sizing: border-box;
  width: 100%;
  overflow: auto;
  padding: var(--reader-content-top-inset, 12px) 24px;
}

.reader-view-tabs { display: flex; gap: 24px; padding: 0 24px; min-height: 44px; background: var(--reader-content); border-bottom: 1px solid var(--reader-border); }
.reader-view-tabs button { border: 0; border-bottom: 2px solid transparent; background: transparent; color: var(--reader-muted); padding: 0 2px; font: inherit; cursor: pointer; }
.reader-view-tabs button.active { color: var(--reader-link); border-bottom-color: var(--reader-link); }

.pdf-page-shell {
  position: relative;
  width: fit-content;
  min-width: 120px;
  min-height: 160px;
  margin: 0 auto;
}

.pdf-canvas {
  display: block;
  background: var(--reader-content);
  box-shadow: 0 12px 32px rgba(15, 23, 42, 0.18);
}

.reader-loading {
  position: absolute;
  top: 12px;
  right: 12px;
  padding: 6px 10px;
  border-radius: 999px;
  background: rgba(15, 23, 42, 0.78);
  color: #fff;
  font-size: 12px;
}

.reader-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: 100%;
}

.info-panel {
  padding: 12px;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.inline-action {
  border: 0;
  background: transparent;
  color: var(--reader-link);
  cursor: pointer;
  font: inherit;
  font-size: 12px;
}

.inline-action:disabled {
  color: #cbd5e1;
  cursor: default;
}

.sidebar-empty {
  padding: 18px 8px;
  color: var(--reader-muted);
  font-size: 13px;
  text-align: center;
}

.meta-row {
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr);
  gap: 12px;
  padding: 9px 0;
  border-bottom: 1px solid #eef2f7;
  font-size: 13px;
}

.meta-label {
  color: var(--reader-muted);
}

.meta-value {
  min-width: 0;
  overflow: hidden;
  color: var(--reader-heading);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.share-panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.share-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

@media (max-width: 760px) {
  .pdf-content-toolbar { justify-content: flex-start; }
  .pdf-page-scroll { padding: 12px; }
}
</style>
