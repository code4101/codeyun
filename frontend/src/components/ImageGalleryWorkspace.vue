<template>
  <section
    v-if="images.length || showSidebarWhenEmpty"
    class="workspace"
    :class="{
      'is-sidebar-collapsed': !showSidebarModel,
      'is-top-panels-layout': isTopPanelsLayout,
    }"
  >
    <div
      v-if="isTopPanelsLayout && (showSidebarModel || (showGalleryTop && $slots['gallery-top']))"
      class="workspace-top-panels"
      :class="{ 'is-single-panel': !(showSidebarModel && showGalleryTop && $slots['gallery-top']) }"
    >
      <section v-if="showSidebarModel" class="sidebar-panel">
        <ImageGallerySidebarPanel
          :source-label="sourceLabel"
          :source-tag="sourceTag"
          :source-tag-type="sourceTagType"
          :show-visible-count-stat="showVisibleCountStat"
          :visible-count="visibleImages.length"
          :loaded-count="images.length"
          :item-count-label="itemCountLabel"
          :total-bytes="totalBytes"
          :summary-total-bytes="summaryTotalBytes"
          :keyword="keyword"
          :show-sort-program="showSortProgram"
          :sort-program="sortProgram"
          :sort-summary-label="sortSummaryLabel"
          :show-thumbnail-scale-control="showThumbnailScaleControl"
          :thumbnail-scale="thumbnailScale"
          :show-view-mode-control="showViewModeControl"
          :view-mode="viewMode"
          :view-mode-label="viewModeLabel"
          :show-folder-filter="showFolderFilter"
          :current-folder-label="currentFolderLabel"
          :all-items-label="allItemsLabel"
          :images-length="images.length"
          :folder-filter="folderFilter"
          :all-folders-key="ALL_FOLDERS"
          :folder-options="folderOptions"
          @update:keyword="keyword = $event"
          @update:sort-program="sortProgram = $event"
          @update:thumbnail-scale="thumbnailScale = $event"
          @update:view-mode="viewMode = $event"
          @update:folder-filter="folderFilter = $event"
        >
          <template v-if="$slots['sidebar-extra']" #extra>
            <slot name="sidebar-extra" />
          </template>
        </ImageGallerySidebarPanel>
      </section>

      <section v-if="showGalleryTop && $slots['gallery-top']" class="gallery-top-panel">
        <slot name="gallery-top" />
      </section>
    </div>

    <aside v-else-if="showSidebarModel" class="sidebar-panel">
      <ImageGallerySidebarPanel
        :source-label="sourceLabel"
        :source-tag="sourceTag"
        :source-tag-type="sourceTagType"
        :show-visible-count-stat="showVisibleCountStat"
        :visible-count="visibleImages.length"
        :loaded-count="images.length"
        :item-count-label="itemCountLabel"
        :total-bytes="totalBytes"
        :summary-total-bytes="summaryTotalBytes"
        :keyword="keyword"
        :show-sort-program="showSortProgram"
        :sort-program="sortProgram"
        :sort-summary-label="sortSummaryLabel"
        :show-thumbnail-scale-control="showThumbnailScaleControl"
        :thumbnail-scale="thumbnailScale"
        :show-view-mode-control="showViewModeControl"
        :view-mode="viewMode"
        :view-mode-label="viewModeLabel"
        :show-folder-filter="showFolderFilter"
        :current-folder-label="currentFolderLabel"
        :all-items-label="allItemsLabel"
        :images-length="images.length"
        :folder-filter="folderFilter"
        :all-folders-key="ALL_FOLDERS"
        :folder-options="folderOptions"
        @update:keyword="keyword = $event"
        @update:sort-program="sortProgram = $event"
        @update:thumbnail-scale="thumbnailScale = $event"
        @update:view-mode="viewMode = $event"
        @update:folder-filter="folderFilter = $event"
      >
        <template v-if="$slots['sidebar-extra']" #extra>
          <slot name="sidebar-extra" />
        </template>
      </ImageGallerySidebarPanel>
    </aside>

    <main class="gallery-panel">
      <div v-if="$slots['gallery-controls']" class="gallery-controls">
        <slot
          name="gallery-controls"
          :thumbnail-scale="thumbnailScale"
          :view-mode="viewMode"
          :set-thumbnail-scale="handleThumbnailScaleChange"
          :set-view-mode="handleViewModeChange"
        />
      </div>

      <div v-if="!isTopPanelsLayout && showGalleryTop && $slots['gallery-top']" class="gallery-top">
        <slot name="gallery-top" />
      </div>

      <div
        v-if="showGallerySummary || showFolderFilter || keyword"
        class="gallery-toolbar"
      >
        <div>
          <template v-if="showGallerySummary">
            <div v-if="showResultLabel" class="section-label">浏览结果</div>
            <h2>{{ visibleImages.length }} {{ itemCountLabel }}</h2>
          </template>
          <p v-if="showFolderFilter || keyword">
            <template v-if="showFolderFilter">目录筛：{{ currentFolderLabel }}</template>
            <template v-if="keyword">
              <span v-if="showFolderFilter"> · </span>
              搜索：{{ keyword }}
            </template>
          </p>
        </div>
      </div>

      <div
        v-if="visibleImages.length"
        ref="galleryScrollRef"
        class="gallery-scroll"
        tabindex="-1"
        @scroll="handleGalleryScroll"
      >
        <div
          v-if="viewMode === 'grid'"
          class="gallery-grid"
          :style="{ gridTemplateColumns: `repeat(auto-fill, minmax(${thumbnailWidth}px, 1fr))` }"
        >
          <article
            v-for="image in visibleImages"
            :key="image.id"
            :ref="(element) => registerMediaCard(image.id, element)"
            :data-image-id="image.id"
            class="image-card"
            @click="handleOpenPreview(image.id)"
            @keydown.enter.prevent="handleOpenPreview(image.id)"
            @keydown.space.prevent="handleOpenPreview(image.id)"
            role="button"
            tabindex="0"
          >
            <div class="thumb-frame">
              <template v-if="image.url">
                <img
                  v-if="!isPdf(image) && (!isVideo(image) || image.urlVariant === 'thumbnail')"
                  class="thumb-media"
                  :src="image.url"
                  :alt="image.name"
                  loading="lazy"
                  @load="handleImageLoad(image.id, $event)"
                />
                <video
                  v-else-if="isVideo(image)"
                  class="thumb-media thumb-video"
                  :src="image.url"
                  preload="metadata"
                  muted
                  playsinline
                  @loadedmetadata="handleVideoMetadata(image.id, $event)"
                />
              </template>
              <div v-if="isPdf(image)" class="document-placeholder">
                <span class="document-placeholder-icon">PDF</span>
              </div>
              <div v-else-if="!image.url" class="thumb-placeholder">{{ getLoadingText(image) }}</div>

              <div class="media-badge-group">
                <span class="media-kind-badge">{{ getMediaFormatLabel(image) }}</span>
                <span v-if="isVideo(image) && hasDuration(image)" class="media-duration-badge">
                  {{ formatDuration(image.duration) }}
                </span>
              </div>

              <button
                v-if="shouldShowQuickDeleteButton(image)"
                type="button"
                class="media-quick-delete-button"
                :class="{ 'is-disabled': deletingImageId === image.id }"
                :disabled="deletingImageId === image.id"
                @click.stop="handleDeleteImage(image.id)"
              >
                删
              </button>

              <button
                v-if="openPdfDocument && isPdf(image)"
                type="button"
                class="media-open-reader-button"
                :class="{ 'is-disabled': openingPdfImageId === image.id }"
                :disabled="openingPdfImageId === image.id"
                @click.stop="handleOpenPdfDocument(image)"
              >
                阅读
              </button>

              <div v-if="shouldShowWeightPanel(image)" class="media-weight-panel" @click.stop>
                <button
                  type="button"
                  class="media-weight-button"
                  :class="{ 'is-disabled': isWeightUpdating(image.id) }"
                  :disabled="isWeightUpdating(image.id)"
                  @click.stop="handleAdjustImageWeight(image.id, 1)"
                >
                  <el-icon><ArrowUp /></el-icon>
                </button>
                <span class="media-weight-value">{{ getImageWeight(image) }}</span>
                <button
                  type="button"
                  class="media-weight-button"
                  :class="{ 'is-disabled': isWeightUpdating(image.id) }"
                  :disabled="isWeightUpdating(image.id)"
                  @click.stop="handleAdjustImageWeight(image.id, -1)"
                >
                  <el-icon><ArrowDown /></el-icon>
                </button>
              </div>
            </div>

            <div class="image-meta">
              <div class="image-name" :title="image.name">{{ image.name }}</div>
              <div class="image-path" :title="image.relativePath">{{ image.relativePath }}</div>
              <div class="image-subline">
                <span>{{ formatFileSize(image.size) }}</span>
                <span>{{ formatDate(image.modifiedAt) }}</span>
              </div>
            </div>
          </article>
        </div>

        <div
          v-else-if="renderedMasonryColumns.some((column) => column.length)"
          class="gallery-masonry"
          :style="{ gridTemplateColumns: `repeat(${masonryColumnCount}, minmax(0, 1fr))` }"
        >
          <div
            v-for="(column, columnIndex) in renderedMasonryColumns"
            :key="columnIndex"
            class="masonry-column"
          >
            <article
              v-for="image in column"
              :key="image.id"
              :ref="(element) => registerMediaCard(image.id, element)"
              :data-image-id="image.id"
              class="masonry-item"
              @click="handleOpenPreview(image.id)"
              @keydown.enter.prevent="handleOpenPreview(image.id)"
              @keydown.space.prevent="handleOpenPreview(image.id)"
              role="button"
              tabindex="0"
            >
              <div class="masonry-frame">
                <template v-if="image.url">
                  <img
                    v-if="!isPdf(image) && (!isVideo(image) || image.urlVariant === 'thumbnail')"
                    class="masonry-thumb"
                    :src="image.url"
                    :alt="image.name"
                    loading="lazy"
                    @load="handleImageLoad(image.id, $event)"
                  />
                  <video
                    v-else-if="isVideo(image)"
                    class="masonry-thumb masonry-video"
                    :src="image.url"
                    preload="metadata"
                    muted
                    playsinline
                    @loadedmetadata="handleVideoMetadata(image.id, $event)"
                  />
                </template>
                <div v-if="isPdf(image)" class="document-placeholder is-masonry">
                  <span class="document-placeholder-icon">PDF</span>
                </div>
                <div v-else-if="!image.url" class="masonry-placeholder">{{ getLoadingText(image) }}</div>

                <div class="media-badge-group">
                  <span class="media-kind-badge">{{ getMediaFormatLabel(image) }}</span>
                  <span v-if="isVideo(image) && hasDuration(image)" class="media-duration-badge">
                    {{ formatDuration(image.duration) }}
                  </span>
                </div>

                <button
                  v-if="shouldShowQuickDeleteButton(image)"
                  type="button"
                  class="media-quick-delete-button"
                  :class="{ 'is-disabled': deletingImageId === image.id }"
                  :disabled="deletingImageId === image.id"
                  @click.stop="handleDeleteImage(image.id)"
                >
                  删
                </button>

                <button
                  v-if="openPdfDocument && isPdf(image)"
                  type="button"
                  class="media-open-reader-button"
                  :class="{ 'is-disabled': openingPdfImageId === image.id }"
                  :disabled="openingPdfImageId === image.id"
                  @click.stop="handleOpenPdfDocument(image)"
                >
                  阅读
                </button>

                <div v-if="shouldShowWeightPanel(image)" class="media-weight-panel" @click.stop>
                  <button
                    type="button"
                    class="media-weight-button"
                    :class="{ 'is-disabled': isWeightUpdating(image.id) }"
                    :disabled="isWeightUpdating(image.id)"
                    @click.stop="handleAdjustImageWeight(image.id, 1)"
                  >
                    <el-icon><ArrowUp /></el-icon>
                  </button>
                  <span class="media-weight-value">{{ getImageWeight(image) }}</span>
                  <button
                    type="button"
                    class="media-weight-button"
                    :class="{ 'is-disabled': isWeightUpdating(image.id) }"
                    :disabled="isWeightUpdating(image.id)"
                    @click.stop="handleAdjustImageWeight(image.id, -1)"
                  >
                    <el-icon><ArrowDown /></el-icon>
                  </button>
                </div>
              </div>
            </article>
          </div>
        </div>

        <div v-else class="gallery-loading-inline">缩略图加载中</div>

        <div
          v-if="viewMode === 'masonry' && masonryTargetCount < visibleImages.length"
          ref="masonryLoadMoreSentinelRef"
          class="gallery-load-more-sentinel"
          aria-hidden="true"
        />
      </div>

      <div v-else class="empty-inline">
        <div class="empty-inline-text">{{ emptyInlineText }}</div>
      </div>
    </main>
  </section>

  <section v-else class="empty-panel">
    <div class="empty-badge">{{ emptyBadge }}</div>
    <h2>{{ emptyTitle }}</h2>
    <p>{{ emptyDescription }}</p>
    <div v-if="emptySteps.length" class="empty-steps">
      <span v-for="step in emptySteps" :key="step">{{ step }}</span>
    </div>
  </section>

  <el-dialog
    v-model="previewVisible"
    class="preview-dialog"
    :class="{ 'is-pdf-preview': previewImage && isPdf(previewImage) }"
    width="92vw"
    top="4vh"
    destroy-on-close
    @close-auto-focus="handlePreviewCloseAutoFocus"
  >
    <template #header>
      <div class="preview-header" v-if="previewImage">
        <div class="preview-actions">
          <el-button
            v-if="previewClip && isVideo(previewImage)"
            type="primary"
            @click="playPreviewClip"
          >
            播放片段
          </el-button>
          <el-button :disabled="!hasPreviousImage" @click="handleShowPrevious">上一张</el-button>
          <el-button type="primary" :disabled="!hasNextImage" @click="handleShowNext">下一张</el-button>
          <el-button
            v-if="setVideoCover && isVideo(previewImage)"
            plain
            :loading="settingCoverImageId === previewImage.id"
            @click="handleSetVideoCover"
          >
            {{ setCoverButtonText }}
          </el-button>
          <el-button
            v-if="openFileInLocalBrowser && isVideo(previewImage)"
            plain
            :loading="openingLocalBrowserImageId === previewImage.id"
            @click="handleOpenFileInLocalBrowser(previewImage)"
          >
            {{ openLocalBrowserButtonText }}
          </el-button>
          <el-button
            v-if="deleteImage"
            type="danger"
            plain
            :loading="deletingImageId === previewImage.id"
            @click="handleDeleteImage(previewImage.id)"
          >
            {{ deleteButtonText }}
          </el-button>
          <el-button
            v-if="openPdfDocument && isPdf(previewImage)"
            plain
            :loading="openingPdfImageId === previewImage.id"
            @click="handleOpenPdfDocument(previewImage)"
          >
            {{ openPdfButtonText }}
          </el-button>
        </div>
        <el-tag>{{ previewPositionText }}</el-tag>
      </div>
    </template>

    <div v-if="previewImage" class="preview-layout">
      <div class="preview-stage">
        <template v-if="previewImage.url">
          <img
            v-if="!isVideo(previewImage) && !isPdf(previewImage)"
            :src="previewImage.url"
            :alt="previewImage.name"
            @load="handleImageLoad(previewImage.id, $event)"
          />
          <video
            v-else-if="isVideo(previewImage)"
            ref="previewVideoRef"
            class="preview-video"
            :src="previewVideoSource"
            controls
            playsinline
            preload="metadata"
            @loadedmetadata="handlePreviewVideoLoadedMetadata(previewImage.id, $event)"
            @timeupdate="handlePreviewVideoTimeUpdate"
            @seeking="handlePreviewVideoSeeking"
            @waiting="handlePreviewVideoWaiting"
            @canplay="handlePreviewVideoCanPlay"
            @playing="handlePreviewVideoPlaying"
            @error="handlePreviewVideoError"
          />
          <iframe
            v-else
            class="preview-pdf"
            :src="previewImage.url"
            :title="previewImage.name"
          />
        </template>
        <div v-else class="preview-placeholder">{{ getLoadingText(previewImage) }}</div>
        <div
          v-if="previewClipStatusText"
          class="preview-clip-status"
          :class="{ 'is-error': previewClipStatus === 'error' }"
        >
          {{ previewClipStatusText }}
        </div>
      </div>

      <div class="preview-sidebar">
        <div class="meta-card">
          <div class="meta-row">
            <span class="meta-label">文件夹</span>
            <span class="meta-value">{{ getPreviewFolderPath(previewImage) }}</span>
          </div>
          <div class="meta-row">
            <span class="meta-label">文件名</span>
            <span class="meta-value">{{ previewImage.name }}</span>
          </div>
          <div class="meta-row">
            <span class="meta-label">格式</span>
            <span class="meta-value">{{ getMediaFormatLabel(previewImage) }}</span>
          </div>
          <div v-if="isVideo(previewImage) && hasDuration(previewImage)" class="meta-row">
            <span class="meta-label">时长</span>
            <span class="meta-value">{{ formatDuration(previewImage.duration) }}</span>
          </div>
          <div v-if="previewClip && isVideo(previewImage)" class="meta-row">
            <span class="meta-label">片段</span>
            <span class="meta-value">{{ previewClipLabel }}</span>
          </div>
          <div class="meta-row">
            <span class="meta-label">分辨率</span>
            <span class="meta-value">{{ formatResolution(previewImage) }}</span>
          </div>
          <div class="meta-row">
            <span class="meta-label">大小</span>
            <span class="meta-value">{{ formatFileSize(previewImage.size) }}</span>
          </div>
          <div
            v-if="typeof previewImage.createdAt === 'number' || previewImage.createdAt === null"
            class="meta-row"
          >
            <span class="meta-label">创建时间</span>
            <span class="meta-value">
              {{ typeof previewImage.createdAt === 'number' ? formatDate(previewImage.createdAt) : '--' }}
            </span>
          </div>
          <div class="meta-row">
            <span class="meta-label">修改时间</span>
            <span class="meta-value">{{ formatDate(previewImage.modifiedAt) }}</span>
          </div>
          <div v-if="revealImageInFolder" class="meta-actions">
            <el-button
              plain
              class="preview-reveal-button"
              :loading="revealingImageId === previewImage.id"
              @click="handleRevealImageInFolder"
            >
              {{ revealButtonText }}
            </el-button>
          </div>
        </div>
      </div>
    </div>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, toRef, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { ArrowDown, ArrowUp } from '@element-plus/icons-vue';

import ImageGallerySidebarPanel from '@/components/ImageGallerySidebarPanel.vue';
import {
  ALL_FOLDERS,
  cloneGallerySortProgram,
  formatDate,
  formatDuration,
  formatFileSize,
  formatResolution,
  isPdfGalleryItem,
  isVideoGalleryItem,
  type GalleryImage,
  type GallerySortProgram,
  useImageGalleryState,
} from '@/utils/imageGallery';
import {
  createEmptyMasonryColumnHeights,
  createEmptyMasonryColumnIds,
  estimateMasonryColumnCount,
  estimateMasonryColumnWidth,
  estimateMasonryItemHeight as estimateMasonryItemHeightBase,
  estimateMasonryReferenceWidth,
  getKnownMasonryAspectRatio,
  getMasonryBatchSize as getMasonryBatchSizeBase,
  isMasonryRenderable,
} from '@/utils/imageGalleryMasonry';

const props = withDefaults(
  defineProps<{
    images: GalleryImage[];
    sourceLabel?: string;
    sourceTag?: string;
    sourceTagType?: '' | 'primary' | 'success' | 'warning' | 'info' | 'danger';
    emptyBadge: string;
    emptyTitle: string;
    emptyDescription: string;
    emptyInlineText?: string;
    emptySteps?: string[];
    storageKeyPrefix?: string;
    showSidebar: boolean;
    showSidebarWhenEmpty?: boolean;
    showFolderFilter?: boolean;
    showSortProgram?: boolean;
    showThumbnailScaleControl?: boolean;
    showViewModeControl?: boolean;
    showResultLabel?: boolean;
    showGallerySummary?: boolean;
    showGalleryTop?: boolean;
    layoutMode?: 'default' | 'top-panels';
    showVisibleCountStat?: boolean;
    summaryTotalBytes?: number | null;
    preserveOrder?: boolean;
    ensureImageReady?: (image: GalleryImage, options?: { full?: boolean }) => Promise<void>;
    setVideoCover?: (imageId: string, cover: Blob) => Promise<boolean>;
    updateImageWeight?: (imageId: string, nextWeight: number) => Promise<boolean>;
    deleteImage?: (imageId: string) => Promise<boolean>;
    revealImageInFolder?: (image: GalleryImage) => Promise<boolean | void>;
    openFileInLocalBrowser?: (image: GalleryImage) => Promise<boolean | void>;
    openPdfDocument?: (image: GalleryImage) => Promise<boolean | void>;
    deleteButtonText?: string;
    setCoverButtonText?: string;
    revealButtonText?: string;
    openLocalBrowserButtonText?: string;
    openPdfButtonText?: string;
    deleteTip?: string;
    itemLabel?: string;
    itemCountLabel?: string;
    showQuickDeleteForNonPositiveWeight?: boolean;
  }>(),
  {
    sourceLabel: '',
    sourceTag: '',
    sourceTagType: 'primary',
    emptySteps: () => [],
    storageKeyPrefix: 'image_gallery',
    showSidebarWhenEmpty: false,
    showFolderFilter: true,
    showSortProgram: true,
    showThumbnailScaleControl: true,
    showViewModeControl: true,
    showResultLabel: true,
    showGallerySummary: true,
    showGalleryTop: true,
    layoutMode: 'default',
    showVisibleCountStat: true,
    summaryTotalBytes: null,
    preserveOrder: false,
    deleteButtonText: '删除图片',
    setCoverButtonText: '设为封面',
    revealButtonText: '打开所在目录',
    openLocalBrowserButtonText: '浏览器打开原文件',
    openPdfButtonText: '打开阅读器',
    deleteTip: '',
    itemLabel: '图片',
    itemCountLabel: '张图片',
    showQuickDeleteForNonPositiveWeight: false,
  }
);

const emit = defineEmits<{
  (event: 'update:showSidebar', value: boolean): void;
  (event: 'update:sortProgram', value: GallerySortProgram): void;
}>();

interface PreviewClipRange {
  start: number;
  end: number;
  isAutoPauseArmed: boolean;
}

type PreviewClipStatus = 'idle' | 'loading' | 'seeking' | 'waiting' | 'ready' | 'error';

const showSidebarModel = computed({
  get: () => props.showSidebar,
  set: (value: boolean) => emit('update:showSidebar', value),
});

const allItemsLabel = computed(() => `全部${props.itemLabel}`);
const emptyInlineText = computed(() =>
  props.emptyInlineText || `当前筛选条件下没有可显示的${props.itemLabel}`
);
const showGallerySummary = computed(() => props.showGallerySummary);
const showVisibleCountStat = computed(() => props.showVisibleCountStat);
const showThumbnailScaleControl = computed(() => props.showThumbnailScaleControl);
const showViewModeControl = computed(() => props.showViewModeControl);
const isTopPanelsLayout = computed(() => props.layoutMode === 'top-panels');
const summaryTotalBytes = computed(() =>
  typeof props.summaryTotalBytes === 'number' && Number.isFinite(props.summaryTotalBytes) && props.summaryTotalBytes >= 0
    ? props.summaryTotalBytes
    : totalBytes.value
);

const {
  galleryScrollRef,
  keyword,
  folderFilter,
  sortProgram,
  sortSummaryLabel,
  galleryWidth,
  thumbnailScale,
  thumbnailWidth,
  viewMode,
  viewModeLabel,
  previewVisible,
  previewImageId,
  previewIndex,
  previewImage,
  previewPositionText,
  hasPreviousImage,
  hasNextImage,
  folderOptions,
  currentFolderLabel,
  visibleImages,
  totalBytes,
  setPreviewImage,
  handleImageLoad,
  handleVideoMetadata,
} = useImageGalleryState(toRef(props, 'images'), {
  storageKeyPrefix: props.storageKeyPrefix,
  showSidebar: showSidebarModel,
  allItemsLabel: allItemsLabel.value,
  enableFolderFilter: toRef(props, 'showFolderFilter'),
  preserveOrder: toRef(props, 'preserveOrder'),
});

watch(
  sortProgram,
  (value) => {
    emit('update:sortProgram', cloneGallerySortProgram(value));
  },
  { deep: true, immediate: true }
);

const deletingImageId = ref<string | null>(null);
const revealingImageId = ref<string | null>(null);
const settingCoverImageId = ref<string | null>(null);
const openingLocalBrowserImageId = ref<string | null>(null);
const openingPdfImageId = ref<string | null>(null);
const updatingWeightById = ref<Record<string, boolean>>({});
const mediaCardElements = new Map<string, Element>();
const galleryScrollTop = ref(0);
const pendingFocusRestoreImageId = ref<string | null>(null);
let mediaVisibilityObserver: IntersectionObserver | null = null;
let masonryLoadMoreObserver: IntersectionObserver | null = null;
const lastPreviewedVideoId = ref<string | null>(null);
const previewVideoRef = ref<HTMLVideoElement | null>(null);
const previewClip = ref<PreviewClipRange | null>(null);
const previewClipStatus = ref<PreviewClipStatus>('idle');

const handleThumbnailScaleChange = (value: number) => {
  thumbnailScale.value = value;
};

const handleViewModeChange = (value: 'grid' | 'masonry') => {
  viewMode.value = value;
};
const masonryLoadMoreSentinelRef = ref<HTMLElement | null>(null);
let preserveNextMasonryReset = false;
const THUMBNAIL_WARM_CONCURRENCY = 3;
const MAX_PREWARM_VISIBLE_MEDIA = 18;
const MASONRY_INITIAL_ROW_COUNT = 6;
const MASONRY_LOAD_MORE_ROW_COUNT = 4;
const MASONRY_LOAD_MORE_THRESHOLD = 320;
const queuedThumbnailIds = new Set<string>();
const thumbnailWarmQueue: string[] = [];
const masonryRenderedColumnIds = ref<string[][]>([]);
const masonryRenderedColumnHeights = ref<number[]>([]);
const masonryAspectRatioHint = ref(1);
const masonryTargetCount = ref(0);
const masonryLoadedSourceCount = ref(0);
const isMasonryBatchLoading = ref(false);
let masonryBatchSession = 0;
let masonryBatchPromise: Promise<void> | null = null;
let activeThumbnailWarmCount = 0;

const getPreviewFolderPath = (image: GalleryImage) => image.folderDisplayPath || image.folderPath || '根目录';
const isVideo = (image: GalleryImage) => isVideoGalleryItem(image);
const isPdf = (image: GalleryImage) => isPdfGalleryItem(image);
const hasDuration = (image: GalleryImage) =>
  typeof image.duration === 'number' && Number.isFinite(image.duration) && image.duration >= 0;
const getImageWeight = (image: GalleryImage) =>
  typeof image.weight === 'number' && Number.isFinite(image.weight) ? Math.trunc(image.weight) : 0;
const shouldShowWeightPanel = (image: GalleryImage) =>
  typeof image.weight === 'number' || Boolean(props.updateImageWeight);
const shouldShowQuickDeleteButton = (image: GalleryImage) =>
  Boolean(props.deleteImage)
  && Boolean(props.showQuickDeleteForNonPositiveWeight)
  && getImageWeight(image) <= 0;
const isWeightUpdating = (imageId: string) => Boolean(updatingWeightById.value[imageId]);
const getMediaFormatLabel = (image: GalleryImage) => {
  const source = image.name || image.relativePath || '';
  const lastDotIndex = source.lastIndexOf('.');
  if (lastDotIndex >= 0 && lastDotIndex < source.length - 1) {
    return source.slice(lastDotIndex + 1).toLowerCase();
  }

  const mimeSubtype = image.mimeType?.split('/')[1];
  if (mimeSubtype) {
    return mimeSubtype.toLowerCase();
  }

  if (isPdf(image)) return 'pdf';
  return isVideo(image) ? 'video' : 'image';
};
const getLoadingText = (image: GalleryImage | null) => {
  if (image && isPdf(image)) return 'PDF 加载中';
  if (image?.thumbnailFailed) {
    return image && isVideo(image) ? '暂无视频首帧，点击预览播放' : '缩略图生成失败';
  }
  return image && isVideo(image) ? '视频首帧加载中' : '缩略图加载中';
};
const previewClipLabel = computed(() => (
  previewClip.value
    ? `${formatDuration(previewClip.value.start)} - ${formatDuration(previewClip.value.end)}`
    : ''
));
const previewVideoSource = computed(() => {
  const url = previewImage.value?.url || '';
  return url.split('#')[0];
});
const previewClipStatusText = computed(() => {
  const clip = previewClip.value;
  if (!clip) {
    return '';
  }
  if (previewClipStatus.value === 'loading') {
    return '正在加载视频';
  }
  if (previewClipStatus.value === 'seeking') {
    return `正在定位到 ${formatDuration(clip.start)}`;
  }
  if (previewClipStatus.value === 'waiting') {
    return `正在缓冲 ${formatDuration(clip.start)} 附近的数据`;
  }
  if (previewClipStatus.value === 'error') {
    return '片段加载失败，可能是视频编码或浏览器不支持直接跳播';
  }
  return '';
});

const ensureImage = async (imageId: string, options?: { full?: boolean }) => {
  if (!props.ensureImageReady) return true;
  const image = props.images.find((item) => item.id === imageId);
  if (!image) return false;
  const desiredVariant = options?.full ? 'full' : 'thumbnail';
  if (image.url && image.urlVariant === desiredVariant) return true;
  await props.ensureImageReady(image, options);
  return true;
};

const handleAdjustImageWeight = async (imageId: string, delta: number) => {
  if (!props.updateImageWeight || isWeightUpdating(imageId)) {
    return;
  }

  const image = props.images.find((item) => item.id === imageId);
  if (!image) {
    return;
  }

  const currentWeight = getImageWeight(image);
  updatingWeightById.value = {
    ...updatingWeightById.value,
    [imageId]: true,
  };
  try {
    await props.updateImageWeight(imageId, currentWeight + delta);
  } finally {
    const nextState = { ...updatingWeightById.value };
    delete nextState[imageId];
    updatingWeightById.value = nextState;
  }
};

const estimateMasonryAspectRatio = () => {
  const samples = visibleImages.value
    .slice(0, 60)
    .map((image) => getKnownMasonryAspectRatio(image))
    .filter((ratio): ratio is number => ratio !== null)
    .sort((left, right) => left - right);

  if (!samples.length) {
    return 1;
  }

  return samples[Math.floor(samples.length / 2)] ?? 1;
};

const masonryReferenceWidth = computed(() =>
  estimateMasonryReferenceWidth(thumbnailWidth.value, masonryAspectRatioHint.value)
);

const masonryColumnCount = computed(() => {
  const width = galleryWidth.value || galleryScrollRef.value?.clientWidth || 0;
  return estimateMasonryColumnCount(width, masonryReferenceWidth.value);
});

const masonryColumnWidth = computed(() => {
  const width = galleryWidth.value || galleryScrollRef.value?.clientWidth || 0;
  return estimateMasonryColumnWidth(width, masonryColumnCount.value, thumbnailWidth.value);
});

const getMasonryBatchSize = (rowCount: number) =>
  getMasonryBatchSizeBase(masonryColumnCount.value, rowCount);

const estimateMasonryItemHeight = (image: GalleryImage) =>
  estimateMasonryItemHeightBase(image, masonryColumnWidth.value, masonryAspectRatioHint.value);

const createCurrentEmptyMasonryColumnIds = (columnCount = masonryColumnCount.value) =>
  createEmptyMasonryColumnIds(columnCount);

const createCurrentEmptyMasonryColumnHeights = (columnCount = masonryColumnCount.value) =>
  createEmptyMasonryColumnHeights(columnCount);

const ensureMasonryColumnState = (columnCount = masonryColumnCount.value) => {
  const normalizedColumnCount = Math.max(1, columnCount);
  if (masonryRenderedColumnIds.value.length !== normalizedColumnCount) {
    masonryRenderedColumnIds.value = createCurrentEmptyMasonryColumnIds(normalizedColumnCount);
  }
  if (masonryRenderedColumnHeights.value.length !== normalizedColumnCount) {
    masonryRenderedColumnHeights.value = createCurrentEmptyMasonryColumnHeights(normalizedColumnCount);
  }
};

const rebuildMasonryColumnHeights = () => {
  ensureMasonryColumnState();
  const imageById = new Map(props.images.map((image) => [image.id, image]));
  masonryRenderedColumnHeights.value = masonryRenderedColumnIds.value.map((columnIds) =>
    columnIds.reduce((sum, imageId) => {
      const image = imageById.get(imageId);
      if (!image) {
        return sum;
      }
      return sum + estimateMasonryItemHeight(image);
    }, 0)
  );
};

const renderedMasonryColumns = computed(() => {
  const candidateById = new Map(visibleImages.value.map((image) => [image.id, image]));
  const columns = masonryRenderedColumnIds.value.map((columnIds) =>
    columnIds
      .map((imageId) => candidateById.get(imageId) ?? null)
      .filter((image): image is GalleryImage => Boolean(image))
  );
  return columns.length ? columns : createCurrentEmptyMasonryColumnIds();
});

const syncRenderedMasonryColumns = () => {
  ensureMasonryColumnState();
  const candidateIdSet = new Set(visibleImages.value.map((image) => image.id));
  masonryRenderedColumnIds.value = masonryRenderedColumnIds.value.map((columnIds) =>
    columnIds.filter((imageId) => candidateIdSet.has(imageId))
  );
  rebuildMasonryColumnHeights();
};

const resetMasonryState = () => {
  masonryBatchSession += 1;
  masonryRenderedColumnIds.value = createCurrentEmptyMasonryColumnIds();
  masonryRenderedColumnHeights.value = createCurrentEmptyMasonryColumnHeights();
  masonryAspectRatioHint.value = estimateMasonryAspectRatio();
  masonryTargetCount.value = Math.min(visibleImages.value.length, getMasonryBatchSize(MASONRY_INITIAL_ROW_COUNT));
  masonryLoadedSourceCount.value = 0;
  isMasonryBatchLoading.value = false;
  masonryBatchPromise = null;
};

const reconcileMasonryAfterRemoval = () => {
  masonryBatchSession += 1;
  masonryBatchPromise = null;
  isMasonryBatchLoading.value = false;
  syncRenderedMasonryColumns();
  masonryAspectRatioHint.value = estimateMasonryAspectRatio();
  const renderedCount = masonryRenderedColumnIds.value.reduce((sum, columnIds) => sum + columnIds.length, 0);
  masonryTargetCount.value = Math.min(Math.max(renderedCount, masonryTargetCount.value), visibleImages.value.length);
  masonryLoadedSourceCount.value = Math.min(Math.max(renderedCount, masonryLoadedSourceCount.value), masonryTargetCount.value);
};

const appendRenderedMasonryBatch = (images: GalleryImage[]) => {
  ensureMasonryColumnState();
  const renderedIdSet = new Set(masonryRenderedColumnIds.value.flat());
  for (const image of images) {
    const currentImage = props.images.find((item) => item.id === image.id);
    if (!currentImage || !isMasonryRenderable(currentImage) || renderedIdSet.has(currentImage.id)) {
      continue;
    }
    let targetColumnIndex = 0;
    for (let columnIndex = 1; columnIndex < masonryRenderedColumnHeights.value.length; columnIndex += 1) {
      if (masonryRenderedColumnHeights.value[columnIndex] < masonryRenderedColumnHeights.value[targetColumnIndex]) {
        targetColumnIndex = columnIndex;
      }
    }

    masonryRenderedColumnIds.value[targetColumnIndex]?.push(currentImage.id);
    masonryRenderedColumnHeights.value[targetColumnIndex] += estimateMasonryItemHeight(currentImage);
    renderedIdSet.add(currentImage.id);
  }
};

const warmMasonryBatch = async (images: GalleryImage[], session: number) => {
  const queue = [...images];
  const workerCount = Math.min(THUMBNAIL_WARM_CONCURRENCY, queue.length);

  await Promise.all(
    Array.from({ length: workerCount }, async () => {
      while (queue.length) {
        if (session !== masonryBatchSession) {
          return;
        }

        const image = queue.shift();
        if (!image) {
          return;
        }

        const currentImage = props.images.find((item) => item.id === image.id);
        if (!currentImage || isMasonryRenderable(currentImage)) {
          continue;
        }

        try {
          await ensureImage(image.id);
        } catch (error) {
          console.warn('Failed to warm masonry media thumbnail', image.id, error);
        }
      }
    })
  );
};

const ensureMasonryBatches = () => {
  if (viewMode.value !== 'masonry') {
    return;
  }
  if (masonryBatchPromise) {
    return;
  }

  const session = masonryBatchSession;
  masonryBatchPromise = (async () => {
    while (session === masonryBatchSession) {
      const targetCount = Math.min(masonryTargetCount.value, visibleImages.value.length);
      if (masonryLoadedSourceCount.value >= targetCount) {
        break;
      }

      const nextBatch = visibleImages.value.slice(masonryLoadedSourceCount.value, targetCount);
      masonryLoadedSourceCount.value = targetCount;
      if (!nextBatch.length) {
        break;
      }

      isMasonryBatchLoading.value = true;
      await warmMasonryBatch(nextBatch, session);
      if (session !== masonryBatchSession) {
        return;
      }

      appendRenderedMasonryBatch(nextBatch);
    }
  })()
    .finally(() => {
      if (session === masonryBatchSession) {
        isMasonryBatchLoading.value = false;
      }
      masonryBatchPromise = null;
      if (session === masonryBatchSession) {
        syncRenderedMasonryColumns();
        if (masonryLoadedSourceCount.value < Math.min(masonryTargetCount.value, visibleImages.value.length)) {
          ensureMasonryBatches();
        }
      }
    });
};

const resetThumbnailWarmQueue = () => {
  thumbnailWarmQueue.length = 0;
  queuedThumbnailIds.clear();
};

const flushThumbnailWarmQueue = () => {
  if (!props.ensureImageReady) {
    resetThumbnailWarmQueue();
    return;
  }

  while (activeThumbnailWarmCount < THUMBNAIL_WARM_CONCURRENCY && thumbnailWarmQueue.length) {
    const imageId = thumbnailWarmQueue.shift();
    if (!imageId) {
      continue;
    }

    queuedThumbnailIds.delete(imageId);
    const image = props.images.find((item) => item.id === imageId);
    if (
      !image
      || isPdf(image)
      || image.urlVariant === 'thumbnail'
      || image.urlVariant === 'full'
      || image.thumbnailFailed
    ) {
      continue;
    }

    activeThumbnailWarmCount += 1;
    void ensureImage(imageId)
      .catch((error) => {
        console.warn('Failed to warm gallery media thumbnail', imageId, error);
      })
      .finally(() => {
        activeThumbnailWarmCount = Math.max(0, activeThumbnailWarmCount - 1);
        flushThumbnailWarmQueue();
      });
  }
};

const scheduleThumbnailWarm = (imageId: string) => {
  if (!props.ensureImageReady || queuedThumbnailIds.has(imageId)) {
    return;
  }

  const image = props.images.find((item) => item.id === imageId);
  if (
    !image
    || isPdf(image)
    || image.urlVariant === 'thumbnail'
    || image.urlVariant === 'full'
    || image.thumbnailFailed
  ) {
    return;
  }

  queuedThumbnailIds.add(imageId);
  thumbnailWarmQueue.push(imageId);
  flushThumbnailWarmQueue();
};

const prewarmVisibleMedia = () => {
  if (viewMode.value === 'masonry') {
    ensureMasonryBatches();
    return;
  }

  for (const image of visibleImages.value.slice(0, MAX_PREWARM_VISIBLE_MEDIA)) {
    scheduleThumbnailWarm(image.id);
  }
};

const maybeLoadMoreMasonry = () => {
  if (viewMode.value !== 'masonry' || masonryTargetCount.value >= visibleImages.value.length) {
    return;
  }

  const galleryElement = galleryScrollRef.value;
  if (!galleryElement) {
    return;
  }

  const distanceToBottom = galleryElement.scrollHeight - galleryElement.scrollTop - galleryElement.clientHeight;
  if (distanceToBottom > MASONRY_LOAD_MORE_THRESHOLD) {
    return;
  }

  const nextCount = Math.min(visibleImages.value.length, masonryTargetCount.value + getMasonryBatchSize(MASONRY_LOAD_MORE_ROW_COUNT));
  if (nextCount === masonryTargetCount.value) {
    return;
  }

  masonryTargetCount.value = nextCount;
  ensureMasonryBatches();
};

resetMasonryState();

const handleGalleryScroll = () => {
  galleryScrollTop.value = galleryScrollRef.value?.scrollTop ?? 0;
  maybeLoadMoreMasonry();
};

const disconnectMediaVisibilityObserver = () => {
  mediaVisibilityObserver?.disconnect();
  mediaVisibilityObserver = null;
};

const disconnectMasonryLoadMoreObserver = () => {
  masonryLoadMoreObserver?.disconnect();
  masonryLoadMoreObserver = null;
};

const registerMediaCard = (imageId: string, element: Element | null) => {
  const previousElement = mediaCardElements.get(imageId);
  if (previousElement && mediaVisibilityObserver) {
    mediaVisibilityObserver.unobserve(previousElement);
  }

  if (!element) {
    mediaCardElements.delete(imageId);
    return;
  }

  mediaCardElements.set(imageId, element);
  if (mediaVisibilityObserver) {
    mediaVisibilityObserver.observe(element);
  }
};

const rebuildMediaVisibilityObserver = async () => {
  disconnectMediaVisibilityObserver();
  if (
    viewMode.value !== 'grid' ||
    !props.ensureImageReady ||
    !galleryScrollRef.value ||
    typeof IntersectionObserver === 'undefined'
  ) {
    return;
  }

  await nextTick();
  mediaVisibilityObserver = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        const imageId = (entry.target as HTMLElement).dataset.imageId;
        if (!imageId) continue;
        const image = props.images.find((item) => item.id === imageId);
        if (!image || isPdf(image) || image.urlVariant === 'thumbnail' || image.urlVariant === 'full') {
          continue;
        }
        scheduleThumbnailWarm(imageId);
      }
    },
    {
      root: galleryScrollRef.value,
      rootMargin: '240px 0px 240px 0px',
      threshold: 0.01,
    }
  );

  for (const element of mediaCardElements.values()) {
    mediaVisibilityObserver.observe(element);
  }
};

const rebuildMasonryLoadMoreObserver = async () => {
  disconnectMasonryLoadMoreObserver();
  if (
    viewMode.value !== 'masonry' ||
    !galleryScrollRef.value ||
    !masonryLoadMoreSentinelRef.value ||
    typeof IntersectionObserver === 'undefined'
  ) {
    return;
  }

  await nextTick();
  if (!galleryScrollRef.value || !masonryLoadMoreSentinelRef.value) {
    return;
  }

  masonryLoadMoreObserver = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) {
          continue;
        }
        maybeLoadMoreMasonry();
      }
    },
    {
      root: galleryScrollRef.value,
      rootMargin: '0px 0px 480px 0px',
      threshold: 0.01,
    }
  );

  masonryLoadMoreObserver.observe(masonryLoadMoreSentinelRef.value);
};

const handleOpenPreview = async (imageId: string) => {
  previewClip.value = null;
  previewClipStatus.value = 'idle';
  await ensureImage(imageId, { full: true });
  setPreviewImage(imageId);
};

const normalizePreviewClip = (startSeconds: number, endSeconds: number): PreviewClipRange => {
  const start = Math.max(0, Number(startSeconds) || 0);
  const end = Math.max(start, Number(endSeconds) || start);
  return { start, end, isAutoPauseArmed: true };
};

const seekPreviewClipStart = async () => {
  const clip = previewClip.value;
  if (!clip) {
    return;
  }
  previewClipStatus.value = 'seeking';
  await nextTick();
  const video = previewVideoRef.value;
  if (!video) {
    return;
  }

  const applySeek = () => {
    const duration = Number.isFinite(video.duration) ? video.duration : clip.start;
    const boundedStart = Math.max(0, Math.min(clip.start, Math.max(0, duration - 0.2)));
    try {
      video.currentTime = boundedStart;
    } catch (error) {
      console.warn('Failed to seek preview clip start', error);
      previewClipStatus.value = 'error';
      return;
    }
    void video.play().catch(() => undefined);
  };

  if (video.readyState >= 1) {
    applySeek();
  } else {
    video.addEventListener('loadedmetadata', applySeek, { once: true });
  }
};

const openMediaClip = async (imageId: string, startSeconds: number, endSeconds: number) => {
  previewClip.value = normalizePreviewClip(startSeconds, endSeconds);
  previewClipStatus.value = 'loading';
  await ensureImage(imageId, { full: true });
  setPreviewImage(imageId);
  await seekPreviewClipStart();
};

const playPreviewClip = async () => {
  const clip = previewClip.value;
  const video = previewVideoRef.value;
  if (!clip || !video) {
    return;
  }
  const duration = Number.isFinite(video.duration) ? video.duration : clip.start;
  video.currentTime = Math.max(0, Math.min(clip.start, Math.max(0, duration - 0.2)));
  await video.play().catch(() => undefined);
};

const handlePreviewVideoLoadedMetadata = (imageId: string, event: Event) => {
  handleVideoMetadata(imageId, event);
  if (previewClip.value) {
    void seekPreviewClipStart();
  }
};

const handlePreviewVideoSeeking = () => {
  if (previewClip.value) {
    previewClipStatus.value = 'seeking';
  }
};

const handlePreviewVideoWaiting = () => {
  if (previewClip.value) {
    previewClipStatus.value = 'waiting';
  }
};

const handlePreviewVideoCanPlay = () => {
  if (previewClip.value) {
    previewClipStatus.value = 'ready';
  }
};

const handlePreviewVideoPlaying = () => {
  if (previewClip.value) {
    previewClipStatus.value = 'ready';
  }
};

const handlePreviewVideoError = () => {
  if (previewClip.value) {
    previewClipStatus.value = 'error';
  }
};

const handlePreviewVideoTimeUpdate = () => {
  const clip = previewClip.value;
  const video = previewVideoRef.value;
  if (!clip || !video) {
    return;
  }
  if (video.currentTime < clip.end) {
    clip.isAutoPauseArmed = true;
    return;
  }
  if (clip.isAutoPauseArmed) {
    clip.isAutoPauseArmed = false;
    video.pause();
  }
};

const handleShowPrevious = async () => {
  if (!hasPreviousImage.value) return;
  const nextImage = visibleImages.value[previewIndex.value - 1];
  if (!nextImage) return;
  await handleOpenPreview(nextImage.id);
};

const handleShowNext = async () => {
  if (!hasNextImage.value) return;
  const nextImage = visibleImages.value[previewIndex.value + 1];
  if (!nextImage) return;
  await handleOpenPreview(nextImage.id);
};

const getFocusedImageId = () => {
  if (typeof document === 'undefined') {
    return null;
  }
  const activeElement = document.activeElement;
  if (!(activeElement instanceof HTMLElement)) {
    return null;
  }
  const mediaElement = activeElement.closest('[data-image-id]');
  if (!(mediaElement instanceof HTMLElement)) {
    return null;
  }
  return mediaElement.dataset.imageId ?? null;
};

const restoreGalleryFocus = (preferredImageId: string | null) => {
  const targetElement = preferredImageId ? mediaCardElements.get(preferredImageId) : null;
  if (targetElement instanceof HTMLElement) {
    targetElement.focus({ preventScroll: true });
    return true;
  }
  if (galleryScrollRef.value) {
    galleryScrollRef.value.focus({ preventScroll: true });
    return true;
  }
  return false;
};

const restoreGalleryScrollPosition = async (scrollTop: number) => {
  await nextTick();
  const galleryElement = galleryScrollRef.value;
  if (!galleryElement) {
    galleryScrollTop.value = scrollTop;
    return;
  }
  const applyScroll = () => {
    const maxScrollTop = Math.max(0, galleryElement.scrollHeight - galleryElement.clientHeight);
    const nextScrollTop = Math.max(0, Math.min(scrollTop, maxScrollTop));
    galleryElement.scrollTop = nextScrollTop;
    galleryScrollTop.value = nextScrollTop;
  };
  applyScroll();
  window.requestAnimationFrame(applyScroll);
};

const handleDeleteImage = async (imageId: string) => {
  if (!props.deleteImage) return;

  const visibleIndex = visibleImages.value.findIndex((item) => item.id === imageId);
  const nextImageId =
    visibleImages.value[visibleIndex + 1]?.id ??
    visibleImages.value[visibleIndex - 1]?.id ??
    null;
  const focusedImageId = getFocusedImageId();
  const shouldRestoreCardFocus = focusedImageId === imageId;
  const previousScrollTop = galleryScrollRef.value?.scrollTop ?? galleryScrollTop.value;

  deletingImageId.value = imageId;
  preserveNextMasonryReset = viewMode.value === 'masonry';
  try {
    const deleted = await props.deleteImage(imageId);
    if (!deleted) {
      preserveNextMasonryReset = false;
      return;
    }
    if (previewImageId.value === imageId) {
      if (nextImageId) {
        await handleOpenPreview(nextImageId);
      } else {
        pendingFocusRestoreImageId.value = nextImageId;
        setPreviewImage(null);
      }
    }
    await restoreGalleryScrollPosition(previousScrollTop);
    if (shouldRestoreCardFocus && !previewVisible.value) {
      window.requestAnimationFrame(() => {
        restoreGalleryFocus(nextImageId);
      });
    }
  } finally {
    deletingImageId.value = null;
  }
};

const handlePreviewCloseAutoFocus = () => {
  const targetImageId = pendingFocusRestoreImageId.value;
  pendingFocusRestoreImageId.value = null;
  window.requestAnimationFrame(() => {
    restoreGalleryFocus(targetImageId);
  });
};

const handleRevealImageInFolder = async () => {
  if (!props.revealImageInFolder || !previewImage.value) {
    return;
  }

  const targetImageId = previewImage.value.id;
  revealingImageId.value = targetImageId;
  try {
    await props.revealImageInFolder(previewImage.value);
  } finally {
    if (revealingImageId.value === targetImageId) {
      revealingImageId.value = null;
    }
  }
};

const handleOpenPdfDocument = async (image: GalleryImage) => {
  if (!props.openPdfDocument) {
    return;
  }

  openingPdfImageId.value = image.id;
  try {
    await props.openPdfDocument(image);
  } finally {
    if (openingPdfImageId.value === image.id) {
      openingPdfImageId.value = null;
    }
  }
};

const handleOpenFileInLocalBrowser = async (image: GalleryImage) => {
  if (!props.openFileInLocalBrowser) {
    return;
  }

  openingLocalBrowserImageId.value = image.id;
  try {
    await props.openFileInLocalBrowser(image);
  } finally {
    if (openingLocalBrowserImageId.value === image.id) {
      openingLocalBrowserImageId.value = null;
    }
  }
};

const capturePreviewFrameBlob = async () => {
  const video = previewVideoRef.value;
  if (!video || video.readyState < 2 || !video.videoWidth || !video.videoHeight) {
    return null;
  }

  const maxEdge = 960;
  const scale = Math.min(1, maxEdge / Math.max(video.videoWidth, video.videoHeight));
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.round(video.videoWidth * scale));
  canvas.height = Math.max(1, Math.round(video.videoHeight * scale));

  const context = canvas.getContext('2d');
  if (!context) {
    return null;
  }

  context.drawImage(video, 0, 0, canvas.width, canvas.height);
  return await new Promise<Blob | null>((resolve) => {
    canvas.toBlob((blob) => resolve(blob), 'image/jpeg', 0.9);
  });
};

const handleSetVideoCover = async () => {
  if (!props.setVideoCover || !previewImage.value || !isVideo(previewImage.value)) {
    return;
  }

  settingCoverImageId.value = previewImage.value.id;
  try {
    const blob = await capturePreviewFrameBlob();
    if (!blob) {
      ElMessage.error('当前视频帧还不能截图，请等待视频画面加载后再试');
      return;
    }

    const saved = await props.setVideoCover(previewImage.value.id, blob);
    if (!saved) {
      return;
    }
  } finally {
    settingCoverImageId.value = null;
  }
};

const isAppendOnlyVisibleSequence = (nextIds: string[], previousIds: string[]) => {
  if (!previousIds.length || nextIds.length < previousIds.length) {
    return false;
  }
  return previousIds.every((imageId, index) => nextIds[index] === imageId);
};

watch(
  () => visibleImages.value.map((image) => image.id),
  (nextIds, previousIds = []) => {
    const isAppendOnly = viewMode.value === 'masonry' && isAppendOnlyVisibleSequence(nextIds, previousIds);
    if (viewMode.value === 'masonry' && preserveNextMasonryReset) {
      preserveNextMasonryReset = false;
      reconcileMasonryAfterRemoval();
    } else if (isAppendOnly) {
      preserveNextMasonryReset = false;
      syncRenderedMasonryColumns();
    } else {
      preserveNextMasonryReset = false;
      resetMasonryState();
      syncRenderedMasonryColumns();
    }
    resetThumbnailWarmQueue();
    void rebuildMediaVisibilityObserver();
    void rebuildMasonryLoadMoreObserver();
    prewarmVisibleMedia();
    void nextTick().then(() => maybeLoadMoreMasonry());
  }
);

watch(galleryScrollRef, () => {
  syncRenderedMasonryColumns();
  void rebuildMediaVisibilityObserver();
  void rebuildMasonryLoadMoreObserver();
  prewarmVisibleMedia();
  void nextTick().then(() => maybeLoadMoreMasonry());
});

watch(
  () => props.ensureImageReady,
  () => {
    resetMasonryState();
    syncRenderedMasonryColumns();
    resetThumbnailWarmQueue();
    void rebuildMediaVisibilityObserver();
    void rebuildMasonryLoadMoreObserver();
    prewarmVisibleMedia();
    void nextTick().then(() => maybeLoadMoreMasonry());
  }
);

watch(
  () => visibleImages.value.map((image) => `${image.id}:${isMasonryRenderable(image) ? 1 : 0}`).join('|'),
  () => {
    if (viewMode.value !== 'masonry') {
      syncRenderedMasonryColumns();
      return;
    }
    syncRenderedMasonryColumns();
    ensureMasonryBatches();
    void rebuildMasonryLoadMoreObserver();
  }
);

watch(masonryColumnCount, () => {
  if (viewMode.value !== 'masonry') {
    return;
  }
  resetMasonryState();
  syncRenderedMasonryColumns();
  prewarmVisibleMedia();
  void nextTick().then(() => maybeLoadMoreMasonry());
});

watch(thumbnailWidth, (nextWidth, previousWidth) => {
  if (viewMode.value !== 'masonry' || nextWidth === previousWidth) {
    return;
  }
  resetMasonryState();
  syncRenderedMasonryColumns();
  prewarmVisibleMedia();
  void nextTick().then(() => maybeLoadMoreMasonry());
});

watch(
  () => visibleImages.value.map((image) => `${image.id}:${image.width ?? ''}x${image.height ?? ''}`).join('|'),
  () => {
    if (viewMode.value !== 'masonry') {
      return;
    }
    rebuildMasonryColumnHeights();
  }
);

watch(viewMode, () => {
  resetMasonryState();
  syncRenderedMasonryColumns();
  resetThumbnailWarmQueue();
  void rebuildMediaVisibilityObserver();
  void rebuildMasonryLoadMoreObserver();
  prewarmVisibleMedia();
  void nextTick().then(() => maybeLoadMoreMasonry());
});

watch(previewImage, (image) => {
  if (image && isVideo(image)) {
    lastPreviewedVideoId.value = image.id;
  }
});

watch(previewVisible, (visible) => {
  if (visible) {
    return;
  }

  previewClip.value = null;
  previewClipStatus.value = 'idle';
  if (!lastPreviewedVideoId.value) {
    return;
  }

  const imageId = lastPreviewedVideoId.value;
  lastPreviewedVideoId.value = null;
  void ensureImage(imageId).catch((error) => {
    console.warn('Failed to restore video thumbnail', imageId, error);
  });
});

onMounted(() => {
  void rebuildMediaVisibilityObserver();
  void rebuildMasonryLoadMoreObserver();
  prewarmVisibleMedia();
});

onBeforeUnmount(() => {
  disconnectMediaVisibilityObserver();
  disconnectMasonryLoadMoreObserver();
  resetThumbnailWarmQueue();
});

defineExpose({
  openMediaClip,
});
</script>

<style scoped>
.workspace {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 310px minmax(0, 1fr);
  gap: 18px;
}

.workspace.is-top-panels-layout {
  display: flex;
  flex-direction: column;
}

.workspace.is-sidebar-collapsed {
  grid-template-columns: minmax(0, 1fr);
}

.workspace-top-panels {
  display: grid;
  grid-template-columns: 310px minmax(0, 1fr);
  gap: 18px;
  align-items: stretch;
}

.workspace-top-panels.is-single-panel {
  grid-template-columns: minmax(0, 1fr);
}

.sidebar-panel,
.gallery-top-panel,
.gallery-panel,
.empty-panel {
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid rgba(148, 163, 184, 0.18);
  border-radius: 24px;
  box-shadow: 0 18px 40px rgba(15, 23, 42, 0.08);
  min-height: 0;
}

.sidebar-panel {
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.gallery-top-panel {
  min-width: 0;
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.gallery-controls {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.sidebar-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.eyebrow,
.section-label {
  font-size: 12px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: #64748b;
}

.sidebar-header h2,
.gallery-toolbar h2,
.preview-header h3 {
  margin: 6px 0 0;
  font-size: 22px;
  color: #0f172a;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.stat-card {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 18px;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.stat-card-wide {
  grid-column: 1 / -1;
}

.stat-card strong {
  font-size: 28px;
  line-height: 1;
  color: #0f172a;
}

.stat-value-compact {
  font-size: 19px;
  line-height: 1.08;
  letter-spacing: -0.02em;
  white-space: nowrap;
}

.stat-label,
.section-value,
.gallery-toolbar p,
.preview-header p,
.meta-label,
.image-path,
.image-subline {
  color: #64748b;
}

.control-group {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.section-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.folders-group {
  flex: 1;
  min-height: 0;
}

.folder-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  overflow: auto;
  padding-right: 4px;
}

.folder-item {
  width: 100%;
  border: none;
  background: transparent;
  border-radius: 16px;
  padding: 10px 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  cursor: pointer;
  text-align: left;
  color: #334155;
  transition:
    background-color 0.2s ease,
    color 0.2s ease,
    transform 0.2s ease;
}

.folder-item:hover {
  background: #eef4ff;
  color: #1d4ed8;
  transform: translateY(-1px);
}

.folder-item.is-active {
  background: linear-gradient(135deg, #2563eb 0%, #0f766e 100%);
  color: #ffffff;
  box-shadow: 0 12px 28px rgba(37, 99, 235, 0.22);
}

.folder-item.is-active .folder-path,
.folder-item.is-active .folder-count {
  color: rgba(255, 255, 255, 0.82);
}

.folder-text {
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.folder-name,
.image-name {
  font-weight: 600;
  color: inherit;
}

.folder-path,
.image-path {
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.folder-count {
  font-size: 12px;
  color: #94a3b8;
}

.gallery-panel {
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.gallery-top {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.gallery-toolbar {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.view-mode-switch {
  width: fit-content;
}

.gallery-toolbar p,
.preview-header p {
  margin: 8px 0 0;
  word-break: break-all;
}

.gallery-scroll {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding-right: 2px;
}

.gallery-grid {
  display: grid;
  gap: 16px;
  align-items: start;
}

.gallery-masonry {
  display: grid;
  gap: 0;
}

.masonry-column {
  display: flex;
  flex-direction: column;
  gap: 0;
  min-width: 0;
}

.gallery-loading-inline {
  min-height: 180px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #64748b;
  font-size: 13px;
}

.gallery-load-more-sentinel {
  width: 100%;
  height: 1px;
}

.image-card {
  border: 1px solid #dbe4f0;
  background: linear-gradient(180deg, #ffffff 0%, #f8fbff 100%);
  border-radius: 20px;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  align-self: start;
  text-align: left;
  cursor: pointer;
  transition:
    transform 0.2s ease,
    box-shadow 0.2s ease,
    border-color 0.2s ease;
}

.image-card:hover {
  transform: translateY(-3px);
  border-color: #93c5fd;
  box-shadow: 0 18px 36px rgba(30, 41, 59, 0.12);
}

.image-card:focus-visible,
.masonry-item:focus-visible {
  outline: 2px solid #2563eb;
  outline-offset: 3px;
}

.thumb-frame,
.masonry-frame {
  position: relative;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  background:
    linear-gradient(135deg, rgba(148, 163, 184, 0.18), rgba(226, 232, 240, 0.1)),
    linear-gradient(45deg, #eff6ff 25%, transparent 25%),
    linear-gradient(-45deg, #eff6ff 25%, transparent 25%);
  background-size: auto, 18px 18px, 18px 18px;
  background-position: 0 0, 0 0, 9px 9px;
}

.thumb-frame {
  border-radius: 16px;
  min-height: 140px;
}

.masonry-frame {
  border-radius: 0;
}

.thumb-media,
.masonry-thumb {
  width: 100%;
  height: auto;
  object-fit: contain;
  display: block;
  background: #e2e8f0;
  pointer-events: none;
}

.thumb-video,
.masonry-video {
  background: #0f172a;
}

.document-placeholder {
  width: 100%;
  min-height: 140px;
  aspect-ratio: 4 / 3;
  display: flex;
  align-items: center;
  justify-content: center;
  background:
    linear-gradient(180deg, rgba(248, 250, 252, 0.98), rgba(226, 232, 240, 0.96));
  color: #b91c1c;
}

.document-placeholder.is-masonry {
  min-height: 180px;
}

.document-placeholder-icon {
  min-width: 64px;
  height: 82px;
  border-radius: 8px;
  border: 1px solid rgba(185, 28, 28, 0.22);
  background: #ffffff;
  box-shadow: 0 12px 28px rgba(15, 23, 42, 0.1);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  font-weight: 700;
  letter-spacing: 0;
}

.media-badge-group {
  position: absolute;
  top: 10px;
  left: 10px;
  z-index: 1;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.media-kind-badge,
.media-duration-badge {
  padding: 6px 10px;
  border-radius: 999px;
  background: rgba(15, 23, 42, 0.78);
  color: #f8fafc;
  font-size: 11px;
  letter-spacing: 0.04em;
  backdrop-filter: blur(8px);
}

.media-weight-panel {
  position: absolute;
  right: 8px;
  bottom: 8px;
  z-index: 1;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 5px;
  border-radius: 12px;
  background: rgba(15, 23, 42, 0.82);
  color: #f8fafc;
  backdrop-filter: blur(8px);
}

.media-quick-delete-button {
  position: absolute;
  top: 8px;
  right: 8px;
  z-index: 1;
  min-width: 30px;
  height: 24px;
  border: none;
  padding: 0 8px;
  border-radius: 999px;
  background: rgba(185, 28, 28, 0.9);
  color: #fff7ed;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.02em;
  cursor: pointer;
  box-shadow: 0 6px 16px rgba(127, 29, 29, 0.28);
  transition:
    background-color 0.2s ease,
    opacity 0.2s ease,
    transform 0.2s ease;
}

.media-quick-delete-button:hover:not(.is-disabled) {
  background: rgba(153, 27, 27, 0.96);
  transform: translateY(-1px);
}

.media-quick-delete-button.is-disabled {
  opacity: 0.55;
  cursor: wait;
}

.media-open-reader-button {
  position: absolute;
  left: 8px;
  bottom: 8px;
  z-index: 1;
  height: 26px;
  border: none;
  padding: 0 10px;
  border-radius: 999px;
  background: rgba(37, 99, 235, 0.92);
  color: #eff6ff;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
  box-shadow: 0 6px 16px rgba(30, 64, 175, 0.24);
  transition:
    background-color 0.2s ease,
    opacity 0.2s ease,
    transform 0.2s ease;
}

.media-open-reader-button:hover:not(.is-disabled) {
  background: rgba(29, 78, 216, 0.96);
  transform: translateY(-1px);
}

.media-open-reader-button.is-disabled {
  opacity: 0.55;
  cursor: wait;
}

.media-weight-value {
  min-width: 12px;
  text-align: center;
  font-size: 10px;
  font-weight: 600;
  line-height: 1;
}

.media-weight-button {
  width: 14px;
  height: 14px;
  border: none;
  padding: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
  background: rgba(248, 250, 252, 0.16);
  color: #f8fafc;
  cursor: pointer;
  transition:
    background-color 0.2s ease,
    opacity 0.2s ease,
    transform 0.2s ease;
}

.media-weight-button:hover:not(.is-disabled) {
  background: rgba(248, 250, 252, 0.28);
  transform: translateY(-1px);
}

.media-weight-button.is-disabled {
  opacity: 0.45;
  cursor: wait;
}

.media-weight-button :deep(svg) {
  width: 8px;
  height: 8px;
}

.thumb-placeholder,
.masonry-placeholder,
.preview-placeholder {
  width: 100%;
  min-height: 140px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #64748b;
  font-size: 13px;
}

.image-meta {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.image-name {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: #0f172a;
}

.image-subline {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-size: 12px;
}

.masonry-item {
  width: 100%;
  display: block;
  margin: 0;
  border: none;
  padding: 0;
  background: transparent;
  border-radius: 0;
  overflow: hidden;
  cursor: pointer;
  break-inside: avoid;
  box-shadow: none;
  transition: opacity 0.2s ease;
}

.masonry-item:hover {
  transform: none;
  box-shadow: none;
  opacity: 0.96;
}

.empty-panel {
  padding: 56px 24px;
  text-align: center;
}

.empty-badge {
  width: fit-content;
  min-width: 96px;
  height: 42px;
  margin: 0 auto 18px;
  padding: 0 18px;
  border-radius: 999px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #2563eb 0%, #0f766e 100%);
  color: #ffffff;
  font-size: 13px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  box-shadow: 0 18px 36px rgba(37, 99, 235, 0.24);
}

.empty-panel h2 {
  margin: 0 0 10px;
  color: #0f172a;
}

.empty-panel p {
  max-width: 620px;
  margin: 0 auto;
  color: #475569;
}

.empty-steps {
  margin-top: 22px;
  display: flex;
  justify-content: center;
  gap: 12px;
  flex-wrap: wrap;
}

.empty-steps span {
  padding: 10px 14px;
  border-radius: 999px;
  background: #eff6ff;
  color: #1d4ed8;
  font-size: 13px;
}

.empty-inline {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

.empty-inline-text {
  padding: 24px 28px;
  border-radius: 18px;
  background: #f8fafc;
  color: #64748b;
}

.preview-dialog :deep(.el-dialog) {
  max-width: 1240px;
  border-radius: 24px;
  max-height: 96vh;
  display: flex;
  flex-direction: column;
}

.preview-dialog :deep(.el-dialog__body) {
  padding-top: 8px;
  display: flex;
  flex-direction: column;
  flex: 1;
  height: calc(96vh - 118px);
  min-height: 0;
  overflow: hidden;
}

.preview-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.preview-header-main {
  min-width: 0;
}

.preview-header-side {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 10px;
}

.preview-tags {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.preview-layout {
  display: grid;
  grid-template-rows: minmax(0, 1fr) auto;
  gap: 18px;
  flex: 1;
  min-height: 0;
  height: 100%;
}

.preview-stage {
  position: relative;
  min-height: 0;
  border-radius: 22px;
  background: #0f172a;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  overflow: hidden;
}

.preview-stage img,
.preview-video {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
}

.preview-video {
  width: 100%;
  background: #000000;
}

.preview-clip-status {
  position: absolute;
  left: 24px;
  top: 24px;
  max-width: min(520px, calc(100% - 48px));
  padding: 8px 12px;
  border-radius: 8px;
  background: rgba(15, 23, 42, 0.82);
  color: #f8fafc;
  font-size: 13px;
  line-height: 1.5;
  pointer-events: none;
}

.preview-clip-status.is-error {
  background: rgba(153, 27, 27, 0.9);
}

.preview-pdf {
  width: 100%;
  height: 100%;
  min-height: 0;
  border: 0;
  border-radius: 12px;
  background: #ffffff;
}

.preview-sidebar {
  display: flex;
  flex-direction: column;
  gap: 14px;
  max-height: min(22vh, 240px);
  overflow: auto;
  padding-right: 4px;
}

.preview-dialog.is-pdf-preview :deep(.el-dialog) {
  max-width: none;
  height: 92vh;
}

.preview-dialog.is-pdf-preview :deep(.el-dialog__body) {
  height: auto;
}

.preview-dialog.is-pdf-preview .preview-layout {
  grid-template-columns: minmax(0, 1fr) minmax(260px, 320px);
  grid-template-rows: minmax(0, 1fr);
  align-items: stretch;
}

.preview-dialog.is-pdf-preview .preview-stage {
  padding: 0;
  background: #e2e8f0;
}

.preview-dialog.is-pdf-preview .preview-sidebar {
  max-height: none;
}

.meta-card {
  border: 1px solid #e2e8f0;
  border-radius: 18px;
  background: #f8fafc;
  padding: 16px;
}

.meta-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.meta-row + .meta-row {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid #e2e8f0;
}

.meta-actions {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid #e2e8f0;
}

.meta-value {
  color: #0f172a;
  text-align: right;
  word-break: break-all;
}

.preview-reveal-button {
  width: 100%;
}

.preview-actions {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

.preview-actions-top {
  justify-content: flex-end;
}

.action-tip {
  font-size: 12px;
  color: #64748b;
}

.preview-action-tip {
  max-width: 360px;
  text-align: right;
}

@media (max-width: 1180px) {
  .workspace {
    grid-template-columns: 1fr;
  }

  .folder-list {
    max-height: 280px;
  }

  .preview-dialog :deep(.el-dialog__body) {
    height: calc(96vh - 132px);
  }

  .preview-dialog.is-pdf-preview :deep(.el-dialog__body) {
    height: auto;
  }

  .preview-dialog.is-pdf-preview .preview-layout {
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: minmax(0, 1fr) minmax(0, auto);
  }

  .preview-dialog.is-pdf-preview .preview-sidebar {
    max-height: min(20vh, 180px);
  }
}

@media (max-width: 780px) {
  .workspace-top-panels {
    grid-template-columns: 1fr;
  }

  .sidebar-panel,
  .gallery-panel,
  .empty-panel {
    border-radius: 20px;
  }

  .sidebar-panel,
  .gallery-panel {
    padding: 18px;
  }

  .stats-grid {
    grid-template-columns: 1fr;
  }

  .stat-card-wide {
    grid-column: auto;
  }

  .gallery-toolbar {
    align-items: flex-start;
  }

  .preview-header,
  .preview-header-side,
  .preview-actions-top {
    align-items: flex-start;
  }

  .preview-header {
    flex-direction: column;
  }

  .preview-action-tip {
    max-width: none;
    text-align: left;
  }

  .image-subline,
  .meta-row {
    flex-direction: column;
    align-items: flex-start;
  }

  .meta-value {
    text-align: left;
  }

  .preview-sidebar {
    max-height: min(28vh, 320px);
  }

  .preview-dialog :deep(.el-dialog__body) {
    height: calc(96vh - 148px);
  }
}
</style>
