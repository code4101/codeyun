<template>
  <div class="pdf-resource-page" v-loading="loading">
    <header class="pdf-toolbar">
      <div class="pdf-toolbar-left">
        <div class="pdf-title" :title="documentDetail?.title || ''">
          {{ documentDetail?.title || errorText || 'PDF' }}
        </div>
      </div>

      <div class="pdf-toolbar-center">
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
        <el-select
          v-model="zoom"
          size="small"
          class="zoom-select"
          :disabled="!pdfDocument"
          @change="handleZoomChange"
        >
          <el-option label="适合宽度" value="page-width" />
          <el-option label="适合页面" value="page-fit" />
          <el-option label="100%" value="100" />
          <el-option label="125%" value="125" />
          <el-option label="150%" value="150" />
          <el-option label="200%" value="200" />
        </el-select>
      </div>

      <div class="pdf-toolbar-right">
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
    </header>

    <main class="pdf-main">
      <aside v-if="documentDetail" class="pdf-activity-rail" aria-label="PDF 导航">
        <button
          type="button"
          class="activity-button"
          :class="{ 'is-active': sidebarTab === 'outline', 'is-open': sidebarOpen && sidebarTab === 'outline' }"
          title="目录"
          :aria-pressed="sidebarOpen && sidebarTab === 'outline'"
          @click="handleActivityClick('outline')"
        >
          <el-icon><MenuIcon /></el-icon>
          <span>目录</span>
        </button>
        <button
          type="button"
          class="activity-button"
          :class="{ 'is-active': sidebarTab === 'pages', 'is-open': sidebarOpen && sidebarTab === 'pages' }"
          title="页面"
          :aria-pressed="sidebarOpen && sidebarTab === 'pages'"
          @click="handleActivityClick('pages')"
        >
          <el-icon><DocumentIcon /></el-icon>
          <span>页面</span>
        </button>
        <button
          type="button"
          class="activity-button"
          :class="{ 'is-active': sidebarTab === 'page-note', 'is-open': sidebarOpen && sidebarTab === 'page-note' }"
          title="页面笔记"
          :aria-pressed="sidebarOpen && sidebarTab === 'page-note'"
          @click="handleActivityClick('page-note')"
        >
          <el-icon><EditPen /></el-icon>
          <span>笔记</span>
        </button>
        <button
          type="button"
          class="activity-button"
          :class="{ 'is-active': sidebarTab === 'info', 'is-open': sidebarOpen && sidebarTab === 'info' }"
          title="信息"
          :aria-pressed="sidebarOpen && sidebarTab === 'info'"
          @click="handleActivityClick('info')"
        >
          <el-icon><InfoFilled /></el-icon>
          <span>信息</span>
        </button>
      </aside>

      <aside
        v-if="sidebarOpen && documentDetail"
        class="pdf-sidebar"
        :class="{ 'is-page-note': sidebarTab === 'page-note' }"
      >
        <div class="sidebar-header">
          <span class="sidebar-title">{{ sidebarTitle }}</span>
          <el-button
            :icon="Fold"
            class="sidebar-collapse-button"
            text
            title="收起面板"
            @click="closeSidebar"
          />
        </div>

        <div class="sidebar-body">
          <div v-if="sidebarTab === 'outline'" class="outline-panel">
            <div v-if="outlineItems.length > 0" class="outline-actions">
              <span>{{ flatOutlineItems.length }} 项</span>
              <button type="button" class="inline-action" @click="expandAllOutline">展开</button>
              <button type="button" class="inline-action" @click="collapseAllOutline">折叠</button>
            </div>
            <div v-if="outlineLoading" class="sidebar-empty">目录加载中</div>
            <div v-else-if="outlineErrorText" class="sidebar-empty">{{ outlineErrorText }}</div>
            <div v-else-if="outlineItems.length === 0" class="sidebar-empty">没有内置目录</div>
            <template v-else>
              <button
                v-for="item in visibleOutlineItems"
                :key="item.id"
                type="button"
                class="outline-item"
                :class="{ 'is-active': item.id === activeOutlineItemId }"
                :style="{ paddingLeft: `${8 + item.level * 14}px` }"
                :disabled="item.page == null && item.children.length === 0"
                :title="item.title"
                @click="goToOutlineItem(item)"
              >
                <span
                  v-if="item.children.length > 0"
                  class="outline-toggle"
                  @click.stop="toggleOutlineExpanded(item)"
                >
                  <el-icon>
                    <CaretBottom v-if="isOutlineExpanded(item.id)" />
                    <CaretRight v-else />
                  </el-icon>
                </span>
                <span v-else class="outline-toggle-placeholder" />
                <span class="outline-title">{{ item.title }}</span>
                <span v-if="item.page" class="outline-page">{{ item.page }}</span>
              </button>
            </template>
          </div>

          <div v-else-if="sidebarTab === 'pages'" class="page-nav-panel">
            <div class="page-nav-toolbar">
              <el-input-number
                v-model="pageNavInput"
                size="small"
                class="page-nav-input"
                :min="1"
                :max="pageInputMax"
                :precision="0"
                :controls="false"
                :disabled="!pdfDocument"
                @change="handlePageNavInputChange"
              />
              <span>/ {{ pageCount || '--' }}</span>
            </div>
            <div class="page-nav-range">
              <button
                type="button"
                class="inline-action"
                :disabled="pageNavStart <= 1"
                @click="shiftPageNavWindow(-1)"
              >
                上一段
              </button>
              <span>{{ pageNavStart }}-{{ pageNavEnd }}</span>
              <button
                type="button"
                class="inline-action"
                :disabled="pageNavEnd >= pageCount"
                @click="shiftPageNavWindow(1)"
              >
                下一段
              </button>
            </div>
            <div class="page-nav-grid">
              <button
                v-for="page in visiblePageNumbers"
                :key="page"
                type="button"
                class="page-nav-item"
                :class="{ 'is-active': page === currentPage }"
                @click="goToPage(page)"
              >
                {{ page }}
              </button>
            </div>
          </div>

          <div v-else-if="sidebarTab === 'page-note'" class="page-note-panel">
            <div class="page-note-toolbar">
              <span>第 {{ currentPage }} 页</span>
              <span class="page-note-status">{{ pageNoteStatusText }}</span>
            </div>
            <div v-if="!canUsePageNotes" class="sidebar-empty">登录后可记录页面笔记</div>
            <div v-else-if="pageNoteErrorText" class="sidebar-empty">{{ pageNoteErrorText }}</div>
            <div v-else v-loading="pageNoteLoading" class="page-note-editor">
              <NoteEditor
                :key="pageNoteEditorKey"
                :model-value="pageNoteContent"
                mode="simple"
                layout="flow"
                :min-height="320"
                :read-only="!canEditPageNote"
                :show-toolbar="canEditPageNote"
                :auto-focus-on-empty="false"
                @update:model-value="handlePageNoteContentUpdate"
              />
            </div>
          </div>

          <div v-else class="info-panel">
            <div class="meta-row">
              <span class="meta-label">权限</span>
              <span class="meta-value">{{ accessRoleLabel }}</span>
            </div>
            <div class="meta-row">
              <span class="meta-label">页数</span>
              <span class="meta-value">{{ pageCount || '--' }}</span>
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
        </div>
      </aside>

      <section
        ref="stageRef"
        class="pdf-stage"
        tabindex="0"
        @keydown.left.prevent="goPreviousPage"
        @keydown.right.prevent="goNextPage"
      >
        <div v-if="readerErrorText || errorText" class="reader-empty">
          <el-empty :description="readerErrorText || errorText" />
        </div>
        <div v-else class="pdf-page-scroll">
          <div class="pdf-page-shell" :class="{ 'is-rendering': pageRendering }">
            <canvas ref="canvasRef" class="pdf-canvas" />
            <div v-if="contentLoading || pageRendering" class="reader-loading">
              {{ contentLoading ? 'PDF 加载中' : '页面渲染中' }}
            </div>
          </div>
        </div>
      </section>
    </main>

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
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue';
import { useRoute } from 'vue-router';
import { ElMessage } from 'element-plus';
import {
  ArrowLeft,
  ArrowRight,
  CaretBottom,
  CaretRight,
  Document as DocumentIcon,
  EditPen,
  Fold,
  InfoFilled,
  Menu as MenuIcon,
  Refresh,
  Share,
} from '@element-plus/icons-vue';
import {
  GlobalWorkerOptions,
  RenderingCancelledException,
  getDocument,
  type PDFDocumentLoadingTask,
  type PDFDocumentProxy,
  type RenderTask,
} from 'pdfjs-dist';
import pdfWorkerUrl from 'pdfjs-dist/build/pdf.worker.mjs?url';

import NoteEditor from '@/components/NoteEditor.vue';
import {
  fetchPdfAccess,
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

GlobalWorkerOptions.workerSrc = pdfWorkerUrl;

const route = useRoute();
const PDFJS_WASM_URL = '/pdfjs/wasm/';
const VALID_SIDEBAR_TABS = ['outline', 'pages', 'page-note', 'info'] as const;
const PAGE_NAV_WINDOW_SIZE = 96;

type PdfSidebarTab = typeof VALID_SIDEBAR_TABS[number];

interface PdfOutlineRawItem {
  title?: string;
  dest?: string | unknown[] | null;
  items?: PdfOutlineRawItem[];
}

interface PdfOutlineItem {
  id: string;
  title: string;
  page: number | null;
  level: number;
  children: PdfOutlineItem[];
}

const documentDetail = ref<PdfDocumentDetail | null>(null);
const contentUrl = ref('');
const loading = ref(false);
const contentLoading = ref(false);
const pageRendering = ref(false);
const errorText = ref('');
const readerErrorText = ref('');
const currentPage = ref(1);
const renderedPage = ref(0);
const pageCount = ref(0);
const zoom = ref('page-width');
const sidebarOpen = ref(true);
const sidebarTab = ref<PdfSidebarTab>('outline');
const outlineItems = ref<PdfOutlineItem[]>([]);
const outlineLoading = ref(false);
const outlineErrorText = ref('');
const expandedOutlineIds = ref<string[]>([]);
const pageNavInput = ref(1);
const pageNavStart = ref(1);
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

const pdfId = computed(() => normalizePositiveInt(route.params.pdfId));
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
const publicUrl = computed(() => `${window.location.origin}/pdf/${documentDetail.value?.id ?? ''}`);
const accessRoleLabel = computed(() => getRoleLabel(documentDetail.value?.access.role ?? 'none'));
const flatOutlineItems = computed(() => flattenOutlineItems(outlineItems.value));
const visibleOutlineItems = computed(() => {
  const expandedIds = new Set(expandedOutlineIds.value);
  const result: PdfOutlineItem[] = [];
  const visit = (items: PdfOutlineItem[]) => {
    items.forEach((item) => {
      result.push(item);
      if (item.children.length > 0 && expandedIds.has(item.id)) {
        visit(item.children);
      }
    });
  };
  visit(outlineItems.value);
  return result;
});
const activeOutlineItemId = computed(() => {
  let activeItem: PdfOutlineItem | null = null;
  flatOutlineItems.value.forEach((item) => {
    if (item.page == null || item.page > currentPage.value) return;
    if (
      activeItem == null
      || item.page > (activeItem.page ?? 0)
      || (item.page === activeItem.page && item.level > activeItem.level)
    ) {
      activeItem = item;
    }
  });
  return activeItem?.id ?? '';
});
const pageNavEnd = computed(() => Math.min(pageNavStart.value + PAGE_NAV_WINDOW_SIZE - 1, pageCount.value || 1));
const visiblePageNumbers = computed(() => {
  const end = pageNavEnd.value;
  return Array.from(
    { length: Math.max(end - pageNavStart.value + 1, 0) },
    (_item, index) => pageNavStart.value + index,
  );
});
const zoomLabel = computed(() => {
  const option = [
    ['page-width', '适合宽度'],
    ['page-fit', '适合页面'],
  ].find(([value]) => value === zoom.value);
  return option?.[1] ?? `${zoom.value}%`;
});
const sidebarTitle = computed(() => getSidebarTabLabel(sidebarTab.value));
const pageNoteEditorKey = computed(() => `${documentDetail.value?.id ?? 'pdf'}:${pageNoteLoadedPage.value || currentPage.value}`);
const pageNoteStatusText = computed(() => {
  if (!canUsePageNotes.value) return '未登录';
  if (pageNoteSaving.value) return '保存中';
  if (pageNoteLoading.value) return '加载中';
  if (pageNote.value?.exists) return '已保存';
  return '空白';
});

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

function getSidebarTabLabel(tab: PdfSidebarTab) {
  switch (tab) {
    case 'pages':
      return '页面';
    case 'page-note':
      return '页面笔记';
    case 'info':
      return '信息';
    default:
      return '目录';
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

function normalizeStringArray(value: unknown) {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === 'string' && item.trim() !== '');
}

function flattenOutlineItems(items: PdfOutlineItem[]) {
  const result: PdfOutlineItem[] = [];
  const visit = (nodes: PdfOutlineItem[]) => {
    nodes.forEach((node) => {
      result.push(node);
      if (node.children.length > 0) {
        visit(node.children);
      }
    });
  };
  visit(items);
  return result;
}

function applyUserState(state?: PdfUserState | null) {
  currentPage.value = Math.max(1, Math.floor(state?.current_page || 1));
  pageNavInput.value = currentPage.value;
  ensurePageNavWindow(currentPage.value);
  zoom.value = state?.zoom && state.zoom !== 'auto' ? state.zoom : 'page-width';
  sidebarOpen.value = state?.sidebar_open ?? true;
  const savedSidebarTab = state?.state_json?.sidebar_tab;
  sidebarTab.value = isPdfSidebarTab(savedSidebarTab) ? savedSidebarTab : 'outline';
  expandedOutlineIds.value = normalizeStringArray(state?.state_json?.expanded_outline_ids);
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
  renderVersion += 1;
  outlineLoadVersion += 1;
  if (renderTask) {
    renderTask.cancel();
    renderTask = null;
  }
  if (loadingTask) {
    await loadingTask.destroy().catch(() => undefined);
    loadingTask = null;
  }
  if (pdfDocument.value) {
    await pdfDocument.value.destroy().catch(() => undefined);
    pdfDocument.value = null;
  }
  pageCount.value = 0;
  renderedPage.value = 0;
  pageRendering.value = false;
  outlineItems.value = [];
  outlineLoading.value = false;
  outlineErrorText.value = '';
  pageNavInput.value = 1;
  pageNavStart.value = 1;
  clearCanvas();
}

function getStageAvailableSize() {
  const stage = stageRef.value;
  const width = Math.max((stage?.clientWidth ?? 900) - 56, 320);
  const height = Math.max((stage?.clientHeight ?? 700) - 56, 320);
  return { width, height };
}

function resolvePageScale(baseWidth: number, baseHeight: number) {
  if (/^\d+$/.test(zoom.value)) {
    return Math.max(0.25, Math.min(Number(zoom.value) / 100, 4));
  }
  const available = getStageAvailableSize();
  const widthScale = available.width / baseWidth;
  if (zoom.value === 'page-fit') {
    return Math.max(0.25, Math.min(widthScale, available.height / baseHeight, 4));
  }
  return Math.max(0.25, Math.min(widthScale, 4));
}

function normalizePageNavStart(value: number) {
  const count = Math.max(pageCount.value || 1, 1);
  const maxStart = Math.max(count - PAGE_NAV_WINDOW_SIZE + 1, 1);
  return Math.min(Math.max(Math.floor(value || 1), 1), maxStart);
}

function ensurePageNavWindow(page: number) {
  const targetPage = clampPage(page);
  const currentStart = normalizePageNavStart(pageNavStart.value);
  const currentEnd = Math.min(currentStart + PAGE_NAV_WINDOW_SIZE - 1, Math.max(pageCount.value || 1, 1));
  if (targetPage >= currentStart && targetPage <= currentEnd) {
    pageNavStart.value = currentStart;
    return;
  }
  pageNavStart.value = normalizePageNavStart(targetPage - Math.floor(PAGE_NAV_WINDOW_SIZE / 2));
}

function shiftPageNavWindow(direction: number) {
  pageNavStart.value = normalizePageNavStart(pageNavStart.value + direction * PAGE_NAV_WINDOW_SIZE);
}

async function renderCurrentPage(options?: { persist?: boolean }) {
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
  try {
    const page = await documentProxy.getPage(targetPage);
    if (version !== renderVersion) return;

    const baseViewport = page.getViewport({ scale: 1 });
    const cssScale = resolvePageScale(baseViewport.width, baseViewport.height);
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
      canvasContext: context,
      viewport: cssViewport,
      transform: outputScale === 1 ? undefined : [outputScale, 0, 0, outputScale, 0, 0],
    });
    renderTask = task;
    await task.promise;
    if (version !== renderVersion) return;

    renderedPage.value = targetPage;
    if (options?.persist !== false) {
      scheduleReaderStateSave();
    }
  } catch (error) {
    if (error instanceof RenderingCancelledException) return;
    console.warn('Failed to render PDF page:', error);
    readerErrorText.value = 'PDF 页面渲染失败';
  } finally {
    if (version === renderVersion) {
      renderTask = null;
      pageRendering.value = false;
    }
  }
}

async function resolveOutlineDestinationPage(
  documentProxy: PDFDocumentProxy,
  dest: PdfOutlineRawItem['dest'],
) {
  if (!dest) return null;
  try {
    const destination = Array.isArray(dest)
      ? dest
      : await documentProxy.getDestination(dest);
    const pageRef = destination?.[0];
    if (typeof pageRef === 'number') {
      return Math.min(Math.max(pageRef + 1, 1), documentProxy.numPages);
    }
    if (pageRef && typeof pageRef === 'object') {
      const pageIndex = await documentProxy.getPageIndex(
        pageRef as Parameters<PDFDocumentProxy['getPageIndex']>[0],
      );
      return Math.min(Math.max(pageIndex + 1, 1), documentProxy.numPages);
    }
  } catch (error) {
    console.warn('Failed to resolve PDF outline destination:', error);
  }
  return null;
}

async function appendOutlineItems(
  documentProxy: PDFDocumentProxy,
  rawItems: PdfOutlineRawItem[],
  level = 0,
  prefix = 'outline',
) {
  const result: PdfOutlineItem[] = [];
  for (const [index, item] of rawItems.entries()) {
    const id = `${prefix}-${index}`;
    result.push({
      id,
      title: (item.title || '未命名目录').trim() || '未命名目录',
      page: await resolveOutlineDestinationPage(documentProxy, item.dest ?? null),
      level,
      children: item.items?.length
        ? await appendOutlineItems(documentProxy, item.items, level + 1, id)
        : [],
    });
  }
  return result;
}

function collectExpandableOutlineIds(items: PdfOutlineItem[], options?: { maxLevel?: number }) {
  const result: string[] = [];
  const visit = (nodes: PdfOutlineItem[]) => {
    nodes.forEach((node) => {
      if (node.children.length > 0 && (options?.maxLevel == null || node.level <= options.maxLevel)) {
        result.push(node.id);
      }
      if (node.children.length > 0) {
        visit(node.children);
      }
    });
  };
  visit(items);
  return result;
}

function initializeOutlineExpansion(items: PdfOutlineItem[]) {
  const expandableIds = new Set(collectExpandableOutlineIds(items));
  const savedIds = expandedOutlineIds.value.filter((id) => expandableIds.has(id));
  expandedOutlineIds.value = savedIds.length > 0
    ? savedIds
    : collectExpandableOutlineIds(items, { maxLevel: 1 });
}

async function loadPdfOutline(documentProxy: PDFDocumentProxy) {
  const version = ++outlineLoadVersion;
  outlineLoading.value = true;
  outlineErrorText.value = '';
  outlineItems.value = [];
  try {
    const rawOutline = await documentProxy.getOutline() as PdfOutlineRawItem[] | null;
    if (version !== outlineLoadVersion || pdfDocument.value !== documentProxy) return;
    if (!rawOutline?.length) {
      outlineItems.value = [];
      return;
    }
    const items = await appendOutlineItems(documentProxy, rawOutline);
    if (version === outlineLoadVersion && pdfDocument.value === documentProxy) {
      outlineItems.value = items;
      initializeOutlineExpansion(items);
    }
  } catch (error) {
    console.warn('Failed to load PDF outline:', error);
    if (version === outlineLoadVersion) {
      outlineErrorText.value = '目录加载失败';
    }
  } finally {
    if (version === outlineLoadVersion) {
      outlineLoading.value = false;
    }
  }
}

async function loadPdfContent(url: string) {
  await destroyPdfRuntime();
  if (!url) return;

  contentLoading.value = true;
  readerErrorText.value = '';
  try {
    loadingTask = getDocument({
      url,
      disableRange: true,
      disableStream: true,
      useSystemFonts: true,
      wasmUrl: PDFJS_WASM_URL,
    });
    const documentProxy = await loadingTask.promise;
    pdfDocument.value = documentProxy;
    pageCount.value = documentProxy.numPages;
    currentPage.value = clampPage(currentPage.value);
    pageNavInput.value = currentPage.value;
    ensurePageNavWindow(currentPage.value);
    void loadPdfOutline(documentProxy);
    await nextTick();
    await renderCurrentPage({ persist: false });
  } catch (error) {
    console.warn('Failed to load PDF content:', error);
    readerErrorText.value = 'PDF 内容加载失败';
  } finally {
    contentLoading.value = false;
  }
}

async function loadPdfDocument() {
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
    if (!detail) {
      errorText.value = 'PDF 不存在或不可访问';
      documentDetail.value = null;
      contentUrl.value = '';
      await destroyPdfRuntime();
      return;
    }
    documentDetail.value = detail;
    document.title = `${detail.title || 'PDF'} - CodeYun`;
    applyUserState(detail.my_state);
    await reloadContentUrl();
  } catch (error) {
    console.warn('Failed to load PDF document:', error);
    errorText.value = '没有权限访问该 PDF';
    documentDetail.value = null;
    contentUrl.value = '';
    await destroyPdfRuntime();
  } finally {
    loading.value = false;
  }
}

async function reloadContentUrl() {
  if (!documentDetail.value) return;
  contentLoading.value = true;
  try {
    const result = await fetchPdfContentUrl(documentDetail.value.id);
    contentUrl.value = result.url;
    await loadPdfContent(result.url);
  } catch (error) {
    console.warn('Failed to load PDF content URL:', error);
    readerErrorText.value = 'PDF 内容加载失败';
    ElMessage.error('PDF 内容加载失败');
  } finally {
    contentLoading.value = false;
  }
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
  if (!documentDetail.value?.access.capabilities.can_update_state) return;
  try {
    const stateJson = {
      ...(documentDetail.value.my_state?.state_json || {}),
      sidebar_tab: sidebarTab.value,
      expanded_outline_ids: expandedOutlineIds.value,
    };
    const state = await updatePdfUserState(documentDetail.value.id, {
      current_page: clampPage(currentPage.value),
      zoom: zoom.value || 'page-width',
      sidebar_open: sidebarOpen.value,
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
      pageNoteErrorText.value = '页面笔记加载失败';
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
      pageNoteContent.value = saved.content_html;
    }
  } catch (error) {
    console.warn('Failed to save PDF page note:', error);
    pageNoteErrorText.value = '页面笔记保存失败';
  } finally {
    pageNoteSaving.value = false;
  }
}

function handlePageNoteContentUpdate(value: string) {
  pageNoteContent.value = value;
  if (pageNoteApplying || !canEditPageNote.value) return;
  const targetPage = pageNoteLoadedPage.value || currentPage.value;
  schedulePageNoteSave(targetPage, value);
}

async function goToPage(page: number) {
  if (!pdfDocument.value) return;
  const nextPage = clampPage(page);
  if (nextPage === currentPage.value && renderedPage.value === nextPage) return;
  currentPage.value = nextPage;
  pageNavInput.value = nextPage;
  ensurePageNavWindow(nextPage);
  await renderCurrentPage();
}

function goToOutlineItem(item: PdfOutlineItem) {
  if (item.page == null) {
    if (item.children.length > 0) {
      toggleOutlineExpanded(item);
    }
    return;
  }
  void goToPage(item.page);
}

function isOutlineExpanded(id: string) {
  return expandedOutlineIds.value.includes(id);
}

function toggleOutlineExpanded(item: PdfOutlineItem) {
  if (item.children.length === 0) return;
  const nextIds = new Set(expandedOutlineIds.value);
  if (nextIds.has(item.id)) {
    nextIds.delete(item.id);
  } else {
    nextIds.add(item.id);
  }
  expandedOutlineIds.value = Array.from(nextIds);
  scheduleReaderStateSave();
}

function expandAllOutline() {
  expandedOutlineIds.value = collectExpandableOutlineIds(outlineItems.value);
  scheduleReaderStateSave();
}

function collapseAllOutline() {
  expandedOutlineIds.value = [];
  scheduleReaderStateSave();
}

function goPreviousPage() {
  if (!canGoPrevious.value) return;
  void goToPage(currentPage.value - 1);
}

function goNextPage() {
  if (!canGoNext.value) return;
  void goToPage(currentPage.value + 1);
}

function handlePageInputChange() {
  void goToPage(currentPage.value);
}

function handlePageNavInputChange() {
  void goToPage(pageNavInput.value);
}

async function handleZoomChange() {
  await nextTick();
  await renderCurrentPage();
}

async function refreshReaderLayout() {
  await nextTick();
  await renderCurrentPage({ persist: false });
}

async function closeSidebar() {
  if (!sidebarOpen.value) return;
  sidebarOpen.value = false;
  scheduleReaderStateSave();
  await refreshReaderLayout();
}

async function handleActivityClick(tab: PdfSidebarTab) {
  const shouldCollapse = sidebarOpen.value && sidebarTab.value === tab;
  sidebarTab.value = tab;
  sidebarOpen.value = !shouldCollapse;
  scheduleReaderStateSave();
  await refreshReaderLayout();
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

watch(pdfId, () => {
  void loadPdfDocument();
});

watch([currentPage, sidebarTab, sidebarOpen, canUsePageNotes], () => {
  if (sidebarOpen.value && sidebarTab.value === 'page-note') {
    if (pendingPageNoteSave && pendingPageNoteSave.pageNumber !== currentPage.value) {
      flushPendingPageNoteSave();
    }
    void loadCurrentPageNote();
  }
});

onMounted(() => {
  resizeObserver = new ResizeObserver(() => {
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
.pdf-resource-page {
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: #fff;
  color: #1f2937;
}

.pdf-toolbar {
  box-sizing: border-box;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
  align-items: center;
  gap: 12px;
  min-height: 48px;
  padding: 8px 14px;
  border-bottom: 1px solid #e5e7eb;
}

.pdf-toolbar-left,
.pdf-toolbar-center,
.pdf-toolbar-right {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.pdf-toolbar-center {
  justify-content: center;
}

.pdf-toolbar-right {
  justify-content: flex-end;
}

.pdf-title {
  overflow: hidden;
  color: #111827;
  font-size: 14px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.page-number-input {
  width: 96px;
}

.page-total {
  min-width: 48px;
  color: #64748b;
  font-size: 13px;
}

.zoom-select {
  width: 112px;
}

.pdf-main {
  display: flex;
  flex: 1;
  min-width: 0;
  min-height: 0;
}

.pdf-stage {
  position: relative;
  flex: 1;
  min-width: 0;
  min-height: 0;
  background: #f1f5f9;
  outline: none;
}

.pdf-page-scroll {
  box-sizing: border-box;
  width: 100%;
  height: 100%;
  overflow: auto;
  padding: 28px;
}

.pdf-page-shell {
  position: relative;
  width: fit-content;
  min-width: 120px;
  min-height: 160px;
  margin: 0 auto;
}

.pdf-canvas {
  display: block;
  background: #fff;
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
  align-items: center;
  justify-content: center;
  height: 100%;
}

.pdf-activity-rail {
  box-sizing: border-box;
  display: flex;
  flex: 0 0 48px;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 10px 6px;
  border-right: 1px solid #e5e7eb;
  background: #fff;
}

.activity-button {
  display: inline-flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  width: 36px;
  min-height: 50px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: #64748b;
  cursor: pointer;
  font: inherit;
  font-size: 12px;
  line-height: 1.1;
}

.activity-button .el-icon {
  font-size: 17px;
}

.activity-button:hover {
  background: #f1f5f9;
  color: #2563eb;
}

.activity-button.is-active {
  background: #eff6ff;
  color: #1d4ed8;
}

.activity-button.is-open {
  box-shadow: inset 3px 0 0 #3b82f6;
}

.pdf-sidebar {
  box-sizing: border-box;
  display: flex;
  flex: 0 0 288px;
  flex-direction: column;
  width: 288px;
  min-height: 0;
  padding: 14px;
  border-right: 1px solid #e5e7eb;
  background: #fff;
}

.pdf-sidebar.is-page-note {
  flex-basis: 360px;
  width: 360px;
}

.sidebar-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  flex-shrink: 0;
  min-height: 32px;
  padding-bottom: 8px;
  border-bottom: 1px solid #eef2f7;
}

.sidebar-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  color: #0f172a;
  font-size: 14px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sidebar-collapse-button {
  flex: 0 0 auto;
}

.sidebar-body {
  flex: 1;
  min-height: 0;
  margin-top: 12px;
  overflow: auto;
}

.outline-panel,
.info-panel,
.page-nav-panel,
.page-note-panel {
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.outline-actions,
.page-nav-range {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 28px;
  margin-bottom: 8px;
  color: #64748b;
  font-size: 12px;
}

.outline-actions span,
.page-nav-range span {
  flex: 1;
  min-width: 0;
}

.inline-action {
  border: 0;
  background: transparent;
  color: #2563eb;
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
  color: #64748b;
  font-size: 13px;
  text-align: center;
}

.outline-item {
  box-sizing: border-box;
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr) auto;
  align-items: center;
  gap: 4px;
  width: 100%;
  min-height: 32px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: #334155;
  cursor: pointer;
  font: inherit;
  font-size: 13px;
  text-align: left;
}

.outline-item:hover {
  background: #f1f5f9;
}

.outline-item.is-active {
  background: #e8f2ff;
  color: #1d4ed8;
}

.outline-item:disabled {
  color: #94a3b8;
  cursor: default;
}

.outline-toggle,
.outline-toggle-placeholder {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  color: #64748b;
}

.outline-toggle {
  border-radius: 4px;
}

.outline-toggle:hover {
  background: rgba(37, 99, 235, 0.08);
  color: #2563eb;
}

.outline-title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.outline-page {
  color: #64748b;
  font-size: 12px;
}

.page-nav-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  color: #64748b;
  font-size: 12px;
}

.page-nav-input {
  width: 86px;
}

.page-nav-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(44px, 1fr));
  gap: 6px;
}

.page-note-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-height: 28px;
  margin-bottom: 8px;
  color: #64748b;
  font-size: 12px;
}

.page-note-status {
  flex: 0 0 auto;
}

.page-note-editor {
  min-height: 0;
}

.page-note-editor :deep(.editor-container) {
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  background: #fff;
}

.page-note-editor :deep(.editor-toolbar-row) {
  padding: 4px 6px;
  overflow-x: auto;
}

.page-note-editor :deep(.editor-content-area.is-flow) {
  min-height: 320px;
}

.page-nav-item {
  height: 30px;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  background: #fff;
  color: #334155;
  cursor: pointer;
  font: inherit;
  font-size: 12px;
}

.page-nav-item:hover {
  border-color: #93c5fd;
  color: #1d4ed8;
}

.page-nav-item.is-active {
  border-color: #3b82f6;
  background: #3b82f6;
  color: #fff;
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
  color: #64748b;
}

.meta-value {
  min-width: 0;
  overflow: hidden;
  color: #0f172a;
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
  .pdf-toolbar {
    grid-template-columns: minmax(0, 1fr);
  }

  .pdf-toolbar-center,
  .pdf-toolbar-right {
    justify-content: flex-start;
  }

  .pdf-main {
    flex-direction: column;
  }

  .pdf-activity-rail {
    flex: 0 0 auto;
    flex-direction: row;
    justify-content: flex-start;
    width: 100%;
    padding: 6px 10px;
    overflow-x: auto;
    border-right: 0;
    border-bottom: 1px solid #e5e7eb;
  }

  .activity-button {
    flex-direction: row;
    width: auto;
    min-height: 32px;
    padding: 0 10px;
  }

  .activity-button.is-open {
    box-shadow: inset 0 -3px 0 #3b82f6;
  }

  .pdf-page-scroll {
    padding: 14px;
  }

  .pdf-sidebar {
    flex: 0 0 auto;
    width: auto;
    max-height: 240px;
    border-right: 0;
    border-bottom: 1px solid #e5e7eb;
  }
}
</style>
