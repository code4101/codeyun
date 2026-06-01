<template>
  <div class="device-file-page">
    <section v-if="showEmptyPanel" class="empty-panel">
      <div class="empty-badge">{{ emptyBadgeText }}</div>
      <h2>{{ emptyStateTitle }}</h2>
      <p>{{ emptyStateDescription }}</p>
      <div class="empty-actions">
        <el-button type="primary" @click="router.push('/cluster/runtime')">去运行管理</el-button>
      </div>
    </section>

    <section v-else class="browser-panel" v-loading="isLoadingListing">
      <section
        v-if="directoryEntries.length || mediaItems.length || !isDeviceRootPath(normalizedPathInput)"
        class="waterfall-media-section"
      >
        <div class="media-actions">
          <el-button size="small" class="collapse-toggle-btn" @click="showSidebar = !showSidebar">
            {{ showSidebar ? '收起边栏和目录' : '展开边栏和目录' }}
          </el-button>
        </div>

        <ImageGalleryWorkspace
          :images="mediaItems"
          :show-sidebar="showSidebar"
          :show-sidebar-when-empty="true"
          :show-sort-program="false"
          :show-thumbnail-scale-control="false"
          :show-view-mode-control="false"
          :show-result-label="false"
          :show-gallery-summary="false"
          :show-gallery-top="showSidebar"
          layout-mode="top-panels"
          :show-visible-count-stat="false"
          :summary-total-bytes="mediaTotalBytes"
          :preserve-order="true"
          :storage-key-prefix="galleryStorageKey"
          item-label="媒体"
          item-count-label="项媒体"
          :empty-inline-text="mediaEmptyInlineText"
          :show-folder-filter="false"
          :ensure-image-ready="ensureMediaReady"
          :set-video-cover="setVideoCover"
          :update-image-weight="updateImageWeight"
          :delete-image="deleteImage"
          :reveal-image-in-folder="revealDeviceMediaInFolder"
          :open-pdf-document="openPdfDocument"
          delete-button-text="删除文件"
          set-cover-button-text="设为封面"
          open-pdf-button-text="打开阅读器"
          show-quick-delete-for-non-positive-weight
          @update:show-sidebar="showSidebar = $event"
        >
          <template #gallery-top>
            <section class="device-directory-panel">
              <div class="directory-config-row">
                <div class="directory-config-field">
                  <span class="directory-config-label">{{ deviceFieldLabel }}</span>
                  <el-select
                    v-model="selectedEntryId"
                    size="large"
                    class="directory-config-select"
                    placeholder="选择设备"
                    :disabled="isLoadingDevices || !devices.length || isDeviceLocked"
                  >
                    <el-option
                      v-for="device in devices"
                      :key="device.id"
                      :label="device.name || device.device_id"
                      :value="device.id"
                    />
                  </el-select>
                </div>

                <div class="directory-config-field directory-config-field-limit">
                  <span class="directory-config-label">加载上限</span>
                  <el-input-number
                    v-model="mediaScanLimitInput"
                    size="large"
                    class="directory-config-limit"
                    :min="MIN_DEVICE_MEDIA_SCAN_LIMIT"
                    :max="MAX_DEVICE_MEDIA_SCAN_LIMIT"
                    :step="500"
                    :precision="0"
                    controls-position="right"
                    @change="handleMediaScanLimitChange"
                  />
                </div>
              </div>

              <slot
                name="config-after"
                :selected-entry-id="selectedEntryId"
                :selected-path="normalizedPathInput"
                :devices="devices"
                :can-browse="canBrowse"
              />

              <div class="directory-toolbar">
                <el-input
                  v-model="pathInputValue"
                  size="large"
                  clearable
                  class="directory-path-input"
                  :placeholder="pathInputPlaceholder"
                  :disabled="!selectedEntryId"
                  @keyup.enter="handleSubmitPath"
                  @blur="handlePathBlur"
                />
                <el-button
                  type="primary"
                  size="large"
                  class="directory-action-button"
                  :loading="isLoadingListing"
                  :disabled="!canBrowse"
                  @click="handleSubmitPath"
                >
                  进入目录
                </el-button>
                <el-button
                  size="large"
                  class="directory-action-button"
                  :disabled="!canGoUp || isLoadingListing"
                  @click="goToParentDirectory"
                >
                  上一级
                </el-button>
                <el-switch
                  v-model="recursiveDisplay"
                  class="directory-recursive-toggle"
                  inline-prompt
                  active-text="递归检索"
                  inactive-text="当前目录"
                  :width="112"
                  aria-label="是否递归检索"
                />
                <span class="directory-section-count">{{ directoryEntries.length }}项</span>
              </div>

              <div v-if="hasFixedRootBoundary" class="directory-fixed-root-hint">
                当前页面限制在 {{ normalizedFixedRootPath }} 及其子目录。
              </div>

              <slot
                name="directory-after"
                :selected-entry-id="selectedEntryId"
                :selected-path="normalizedPathInput"
                :directory-entries="directoryEntries"
                :media-items="mediaItems"
                :can-browse="canBrowse"
                :reload-directory="loadDirectory"
              />

              <div v-if="directoryEntries.length" class="directory-strip">
                <button
                  v-for="entry in pagedDirectoryEntries"
                  :key="entry.path"
                  type="button"
                  class="directory-chip"
                  @click="openDirectory(entry.path)"
                >
                  <el-icon class="directory-chip-icon"><FolderOpened /></el-icon>
                  <span class="directory-chip-name" :title="entry.name">{{ entry.name }}</span>
                </button>
              </div>
              <div v-if="directoryEntries.length > DEFAULT_DIRECTORY_PAGE_SIZE" class="directory-pagination">
                <el-pagination
                  small
                  background
                  :current-page="currentDirectoryPage"
                  :page-size="DEFAULT_DIRECTORY_PAGE_SIZE"
                  :total="directoryEntries.length"
                  layout="prev, pager, next"
                  @current-change="handleDirectoryPageChange"
                />
              </div>
              <div v-if="!directoryEntries.length" class="directory-empty-state">
                当前目录下没有子目录
              </div>
            </section>
          </template>

          <template #gallery-controls="{ thumbnailScale, viewMode, setThumbnailScale, setViewMode }">
            <div class="media-top-toolbar">
              <div class="media-top-toolbar-left">
                <div class="media-toolbar-group media-toolbar-group-mode">
                  <el-button-group class="media-view-mode-switch">
                    <el-button
                      :type="viewMode === 'masonry' ? 'primary' : 'default'"
                      :class="{ 'is-active': viewMode === 'masonry' }"
                      @click="setViewMode('masonry')"
                    >
                      瀑布流
                    </el-button>
                    <el-button
                      :type="viewMode === 'grid' ? 'primary' : 'default'"
                      :class="{ 'is-active': viewMode === 'grid' }"
                      @click="setViewMode('grid')"
                    >
                      卡片
                    </el-button>
                  </el-button-group>
                </div>

                <div class="media-toolbar-group media-toolbar-group-scale">
                  <span class="media-toolbar-label">缩放比例</span>
                  <el-slider
                    class="media-toolbar-slider"
                    :min="10"
                    :max="100"
                    :step="5"
                    :model-value="thumbnailScale"
                    @update:model-value="(value) => setThumbnailScale(Number(value))"
                  />
                  <span class="media-toolbar-value">{{ thumbnailScale }}%</span>
                </div>

                <div v-if="mediaVisualHashHint" class="media-toolbar-group media-toolbar-group-index">
                  <span class="media-toolbar-label">视觉索引</span>
                  <span class="media-index-pill" :class="`is-${mediaVisualHashHint.tone}`">
                    <el-icon v-if="mediaVisualHashHint.loading" class="media-index-pill-icon is-loading">
                      <Loading />
                    </el-icon>
                    <span>{{ mediaVisualHashHint.label }}</span>
                  </span>
                  <el-tooltip effect="light" placement="top">
                    <template #content>
                      <div class="media-index-tooltip">{{ mediaVisualHashHint.detail }}</div>
                    </template>
                    <button type="button" class="media-index-help" aria-label="视觉索引说明">
                      <el-icon><QuestionFilled /></el-icon>
                    </button>
                  </el-tooltip>
                </div>
              </div>

              <div v-if="mediaTotalCount > 0" class="media-pagination-inline media-pagination-inline-top">
                <el-pagination
                  small
                  background
                  :current-page="currentMediaPage"
                  :page-size="mediaPageSize"
                  :page-sizes="MEDIA_PAGE_SIZE_OPTIONS"
                  :total="mediaTotalCount"
                  layout="total, sizes, prev, pager, next, jumper"
                  :disabled="isLoadingListing || isLoadingMediaPage"
                  @current-change="handleMediaPageChange"
                  @size-change="handleMediaPageSizeChange"
                />
              </div>

              <slot
                name="toolbar-after"
                :selected-entry-id="selectedEntryId"
                :selected-path="normalizedPathInput"
                :media-items="mediaItems"
                :media-total-count="mediaTotalCount"
                :can-browse="canBrowse"
                :reload-directory="loadDirectory"
              />
            </div>
          </template>

          <template #sidebar-extra>
            <div class="device-gallery-sidebar-stack">
              <GallerySortProgramBar
                v-model="directorySortProgram"
                title="目录排序"
                caption="基于 devicefile 索引聚合递归大小、文件数、最新修改时间和权重。"
                help-text="只影响当前层子目录顺序，不重新递归扫描设备目录。"
                :field-options="DIRECTORY_SORT_FIELD_OPTIONS"
                :default-program="DEFAULT_DIRECTORY_SORT_PROGRAM"
                collapse-meta-to-tooltip
              />
              <GallerySortProgramBar
                v-model="backendSortProgram"
                title="媒体排序"
                empty-text=""
                :show-caption="false"
                :show-help-text="false"
                :show-hint="false"
              />
            </div>
          </template>
        </ImageGalleryWorkspace>

        <div v-if="mediaTotalCount > 0" class="media-pagination-bar">
          <el-pagination
            small
            background
            :current-page="currentMediaPage"
            :page-size="mediaPageSize"
            :page-sizes="MEDIA_PAGE_SIZE_OPTIONS"
            :total="mediaTotalCount"
            layout="total, sizes, prev, pager, next, jumper"
            :disabled="isLoadingListing || isLoadingMediaPage"
            @current-change="handleMediaPageChange"
            @size-change="handleMediaPageSizeChange"
          />
        </div>
      </section>
    </section>

    <el-dialog
      v-model="previewVisible"
      class="preview-dialog"
      width="92vw"
      top="4vh"
      destroy-on-close
    >
      <template #header>
        <div v-if="previewImage" class="preview-header">
          <div class="preview-actions">
            <el-button :disabled="!hasPreviousImage" @click="handleShowPrevious">上一项</el-button>
            <el-button type="primary" :disabled="!hasNextImage" @click="handleShowNext">下一项</el-button>
            <el-button plain :loading="downloadingPath === previewImage.absolutePath" @click="downloadPreviewFile">
              下载
            </el-button>
            <el-button
              v-if="previewImage.kind === 'pdf'"
              plain
              :loading="openingPdfPath === previewImage.absolutePath"
              @click="openPdfDocument(previewImage)"
            >
              打开阅读器
            </el-button>
          </div>
          <el-tag>{{ previewPositionText }}</el-tag>
        </div>
      </template>

      <div v-if="previewImage" class="preview-layout">
        <div class="preview-stage">
          <template v-if="canRenderPreviewMedia(previewImage)">
            <img
              v-if="!shouldRenderPreviewAsVideo(previewImage) && !shouldRenderPreviewAsPdf(previewImage)"
              :src="previewImage.url"
              :alt="previewImage.name"
              class="preview-image"
            />
            <video
              v-else-if="shouldRenderPreviewAsVideo(previewImage)"
              ref="previewVideoRef"
              class="preview-video"
              :src="previewImage.url"
              controls
              playsinline
              preload="metadata"
            />
            <iframe
              v-else
              class="preview-pdf"
              :src="previewImage.url"
              :title="previewImage.name"
            />
          </template>
          <div v-else class="preview-placeholder">{{ getMediaLoadingText(previewImage, true) }}</div>
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
              <span class="meta-value">{{ getPreviewFormatLabel(previewImage) }}</span>
            </div>
            <div v-if="previewImage.kind === 'video' && hasDuration(previewImage)" class="meta-row">
              <span class="meta-label">时长</span>
              <span class="meta-value">{{ formatDuration(previewImage.duration) }}</span>
            </div>
            <div class="meta-row">
              <span class="meta-label">分辨率</span>
              <span class="meta-value">{{ formatResolution(previewImage) }}</span>
            </div>
            <div class="meta-row">
              <span class="meta-label">大小</span>
              <span class="meta-value">{{ formatFileSize(previewImage.size) }}</span>
            </div>
            <div class="meta-row">
              <span class="meta-label">创建时间</span>
              <span class="meta-value">
                {{ typeof previewImage.createdAt === 'number' ? formatDate(previewImage.createdAt) : '--' }}
              </span>
            </div>
            <div class="meta-row">
              <span class="meta-label">修改时间</span>
              <span class="meta-value">{{ formatDate(previewImage.modifiedAt) }}</span>
            </div>
            <div class="meta-row">
              <span class="meta-label">路径</span>
              <span class="meta-value meta-value-break">{{ previewImage.absolutePath }}</span>
            </div>
            <div class="meta-actions">
              <el-button
                plain
                class="preview-reveal-button"
                :loading="revealingPath === previewImage.absolutePath"
                @click="revealPreviewInFolder"
              >
                打开所在目录
              </el-button>
            </div>
          </div>
        </div>
      </div>
    </el-dialog>

    <slot
      name="page-after"
      :selected-entry-id="selectedEntryId"
      :selected-path="normalizedPathInput"
      :directory-entries="directoryEntries"
      :media-items="mediaItems"
      :listing="listing"
      :can-browse="canBrowse"
      :reload-directory="loadDirectory"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ElMessage } from 'element-plus';
import { Document, FolderOpened, Loading, Picture, QuestionFilled, VideoCamera } from '@element-plus/icons-vue';

import {
  fetchDeviceDirectoryItems,
  fetchDeviceFileBlob,
  fetchDeviceFileStreamUrl,
  fetchDeviceMedia,
  fetchDeviceMediaBlob,
  fetchDeviceThumbnailBlob,
  revealDeviceEntryInFolder,
  setDeviceFileCover,
  setDeviceFileWeight,
  startDeviceEntryDelete,
  fetchDeviceEntryDeleteTask,
  type DeviceDirectoryItem,
  type DeviceDirectorySortField,
  type DeviceDirectorySortProgram,
  type DeviceDirectoryListing,
  type DeviceDeleteTask,
  type DeviceFileSelector,
  type DeviceImageRecord,
  type DeviceMediaListRequest,
  type DeviceMediaListing,
  type DeviceMediaVisualHashStatus,
} from '@/api/deviceFiles';
import { createPdfDocumentFromDeviceFile } from '@/api/pdfDocuments';
import GallerySortProgramBar from '@/components/GallerySortProgramBar.vue';
import ImageGalleryWorkspace from '@/components/ImageGalleryWorkspace.vue';
import { taskStore } from '@/store/taskStore';
import {
  cloneGallerySortProgram,
  createDefaultGallerySortProgram,
  formatDate,
  formatDuration,
  formatFileSize,
  formatResolution,
  normalizeGallerySortProgram,
  type GalleryImage,
  type GallerySortProgram,
  type GalleryUrlVariant,
} from '@/utils/imageGallery';
import { monitorPolledTask } from '@/utils/longTask';

const props = withDefaults(defineProps<{
  fixedDeviceId?: string;
  fixedRootPath?: string;
}>(), {
  fixedDeviceId: '',
  fixedRootPath: '',
});

interface DeviceBrowserImage extends GalleryImage {
  filePath: string;
  absolutePath: string;
  createdAt?: number | null;
  isFetchingUrl?: boolean;
}

interface SortFieldOption {
  value: string;
  label: string;
}

const DEVICE_ROOT_SENTINEL = '__device_root__';
const DEVICE_ROOT_LABEL = '设备根目录';
const DEVICE_PATH_STORAGE_PREFIX = 'codeyun_device_media_browser_path';
const DEVICE_SORT_PROGRAM_STORAGE_SUFFIX = '_backend_sort_program';
const DEVICE_DIRECTORY_SORT_PROGRAM_STORAGE_SUFFIX = '_directory_sort_program';
const DEVICE_SCAN_LIMIT_STORAGE_SUFFIX = '_media_scan_limit';
const DEVICE_RECURSIVE_STORAGE_SUFFIX = '_recursive_display';
const THUMBNAIL_MAX_EDGE = 360;
const MAX_CACHED_FULL_MEDIA = 8;
const DEFAULT_DEVICE_MEDIA_PAGE_SIZE = 50;
const DEFAULT_DIRECTORY_PAGE_SIZE = 20;
const MEDIA_PAGE_SIZE_OPTIONS = [50, 100, 200];
const DEFAULT_DEVICE_MEDIA_SCAN_LIMIT = 2000;
const MIN_DEVICE_MEDIA_SCAN_LIMIT = 100;
const MAX_DEVICE_MEDIA_SCAN_LIMIT = 50000;
const STREAMABLE_VIDEO_MIME_TYPES = new Set(['video/mp4', 'video/webm']);
const STREAMABLE_VIDEO_EXTENSIONS = ['.mp4', '.webm'];
const THUMBNAIL_WARM_CONCURRENCY = 4;

const DIRECTORY_SORT_FIELD_OPTIONS: SortFieldOption[] = [
  { value: 'name', label: '目录名' },
  { value: 'recursive_total_bytes', label: '递归总大小' },
  { value: 'recursive_file_count', label: '递归文件数' },
  { value: 'latest_descendant_modified_at', label: '最新文件修改时间' },
  { value: 'max_weight', label: '最大权重' },
  { value: 'weighted_file_count', label: '加权文件数' },
  { value: 'modified_at', label: '目录修改时间' },
];

const DEFAULT_DIRECTORY_SORT_PROGRAM: DeviceDirectorySortProgram = {
  rules: [{ field: 'recursive_total_bytes', direction: 'desc', nulls: 'last' }],
};

const LEGACY_DIRECTORY_SORT_PROGRAM: DeviceDirectorySortProgram = {
  rules: [{ field: 'name', direction: 'asc', nulls: 'last' }],
};

const isDirectorySortField = (value: unknown): value is DeviceDirectorySortField =>
  value === 'name'
  || value === 'modified_at'
  || value === 'recursive_total_bytes'
  || value === 'recursive_file_count'
  || value === 'latest_descendant_modified_at'
  || value === 'max_weight'
  || value === 'weighted_file_count';

const normalizeDirectorySortProgram = (value?: Partial<DeviceDirectorySortProgram> | null): DeviceDirectorySortProgram => ({
  rules: Array.isArray(value?.rules) && value.rules.length
    ? value.rules.map((rule) => ({
        field: isDirectorySortField(rule?.field) ? rule.field : 'name',
        direction: rule?.direction === 'asc' ? 'asc' : 'desc',
        nulls: rule?.nulls === 'first' ? 'first' : 'last',
      }))
    : DEFAULT_DIRECTORY_SORT_PROGRAM.rules.map((rule) => ({ ...rule })),
});

const cloneDirectorySortProgram = (value?: Partial<DeviceDirectorySortProgram> | null): DeviceDirectorySortProgram =>
  JSON.parse(JSON.stringify(normalizeDirectorySortProgram(value)));

const isSameDirectorySortProgram = (
  left?: Partial<DeviceDirectorySortProgram> | null,
  right?: Partial<DeviceDirectorySortProgram> | null
) => JSON.stringify(normalizeDirectorySortProgram(left)) === JSON.stringify(normalizeDirectorySortProgram(right));

const route = useRoute();
const router = useRouter();

const getQueryString = (value: unknown) => {
  if (Array.isArray(value)) {
    return typeof value[0] === 'string' ? value[0] : '';
  }
  return typeof value === 'string' ? value : '';
};

const devices = computed(() => taskStore.devices);
const normalizedFixedDeviceId = computed(() => (props.fixedDeviceId || '').trim().toLowerCase());
const selectedEntryId = ref(getQueryString(route.query.entry_id));
const selectedPath = ref(DEVICE_ROOT_SENTINEL);
const pathInputValue = ref('');
const listing = ref<DeviceDirectoryListing | null>(null);
const mediaItems = ref<DeviceBrowserImage[]>([]);
const recursiveDisplay = ref(false);
const showSidebar = ref(true);
const isLoadingDevices = ref(false);
const isLoadingListing = ref(false);
const isLoadingMediaPage = ref(false);
const downloadingPath = ref('');
const revealingPath = ref('');
const openingPdfPath = ref('');
const directorySortProgram = ref<DeviceDirectorySortProgram>(cloneDirectorySortProgram(DEFAULT_DIRECTORY_SORT_PROGRAM));
const backendSortProgram = ref<GallerySortProgram>(createDefaultGallerySortProgram());
const previewVisible = ref(false);
const previewImageId = ref<string | null>(null);
const previewVideoRef = ref<HTMLVideoElement | null>(null);
const mediaTotalCount = ref(0);
const mediaTotalBytes = ref(0);
const mediaPageSize = ref(DEFAULT_DEVICE_MEDIA_PAGE_SIZE);
const currentMediaPage = ref(1);
const currentDirectoryPage = ref(1);
const mediaSnapshotId = ref<string | null>(null);
const mediaListingDirty = ref(false);
const mediaVisualHashStatus = ref<DeviceMediaVisualHashStatus | null>(null);
const mediaScanLimit = ref(DEFAULT_DEVICE_MEDIA_SCAN_LIMIT);
const mediaScanLimitInput = ref(DEFAULT_DEVICE_MEDIA_SCAN_LIMIT);
const matchesFixedDevice = (device: { device_id?: string; name?: string }, target: string) => {
  const normalizedDeviceId = (device.device_id || '').trim().toLowerCase();
  const normalizedDeviceName = (device.name || '').trim().toLowerCase();
  return normalizedDeviceId === target || normalizedDeviceName === target;
};
const lockedDevice = computed(() => {
  const targetDeviceId = normalizedFixedDeviceId.value;
  if (!targetDeviceId) {
    return null;
  }
  return devices.value.find((device) => matchesFixedDevice(device, targetDeviceId)) ?? null;
});
const isDeviceLocked = computed(() => Boolean(normalizedFixedDeviceId.value));
const lockedEntryId = computed(() => lockedDevice.value?.id ?? '');
let directoryLoadVersion = 0;
let mediaLoadVersion = 0;
const pendingMediaRequests = new Map<string, Promise<void>>();

const isAbsolutePath = (value: string) => /^(?:[a-zA-Z]:[\\/]|\\\\|\/|~(?:[\\/]|$))/.test((value || '').trim());

const isDeviceRootPath = (value: string) => (value || '').trim() === DEVICE_ROOT_SENTINEL;

const getPathStorageKey = (entryId: string) => `${DEVICE_PATH_STORAGE_PREFIX}:${entryId || 'default'}`;
const getScanLimitStorageKey = (storageKey: string) => `${storageKey}${DEVICE_SCAN_LIMIT_STORAGE_SUFFIX}`;
const getRecursiveStorageKey = (storageKey: string) => `${storageKey}${DEVICE_RECURSIVE_STORAGE_SUFFIX}`;
const getDirectorySortStorageKey = (storageKey: string) => `${storageKey}${DEVICE_DIRECTORY_SORT_PROGRAM_STORAGE_SUFFIX}`;

const normalizeMediaScanLimit = (value: unknown) => {
  const parsed = Number.parseInt(String(value ?? ''), 10);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return DEFAULT_DEVICE_MEDIA_SCAN_LIMIT;
  }
  return Math.min(MAX_DEVICE_MEDIA_SCAN_LIMIT, Math.max(MIN_DEVICE_MEDIA_SCAN_LIMIT, Math.floor(parsed)));
};

const loadPersistedBackendSortProgram = (storageKey: string): GallerySortProgram => {
  if (typeof window === 'undefined' || typeof window.localStorage === 'undefined') {
    return createDefaultGallerySortProgram();
  }

  try {
    const savedValue = window.localStorage.getItem(`${storageKey}${DEVICE_SORT_PROGRAM_STORAGE_SUFFIX}`) || '';
    return savedValue
      ? normalizeGallerySortProgram(JSON.parse(savedValue))
      : createDefaultGallerySortProgram();
  } catch (error) {
    console.warn('Failed to load persisted backend media sort program', error);
    return createDefaultGallerySortProgram();
  }
};

const loadPersistedDirectorySortProgram = (storageKey: string): DeviceDirectorySortProgram => {
  if (typeof window === 'undefined' || typeof window.localStorage === 'undefined') {
    return cloneDirectorySortProgram(DEFAULT_DIRECTORY_SORT_PROGRAM);
  }

  try {
    const savedValue = window.localStorage.getItem(getDirectorySortStorageKey(storageKey)) || '';
    if (!savedValue) {
      return cloneDirectorySortProgram(DEFAULT_DIRECTORY_SORT_PROGRAM);
    }

    const normalizedProgram = normalizeDirectorySortProgram(JSON.parse(savedValue));
    if (isSameDirectorySortProgram(normalizedProgram, LEGACY_DIRECTORY_SORT_PROGRAM)) {
      return cloneDirectorySortProgram(DEFAULT_DIRECTORY_SORT_PROGRAM);
    }
    return normalizedProgram;
  } catch (error) {
    console.warn('Failed to load persisted directory sort program', error);
    return cloneDirectorySortProgram(DEFAULT_DIRECTORY_SORT_PROGRAM);
  }
};

const persistBackendSortProgram = (storageKey: string, value: GallerySortProgram) => {
  if (typeof window === 'undefined' || typeof window.localStorage === 'undefined') {
    return;
  }

  try {
    window.localStorage.setItem(
      `${storageKey}${DEVICE_SORT_PROGRAM_STORAGE_SUFFIX}`,
      JSON.stringify(normalizeGallerySortProgram(value))
    );
  } catch (error) {
    console.warn('Failed to persist backend media sort program', error);
  }
};

const persistDirectorySortProgram = (storageKey: string, value: DeviceDirectorySortProgram) => {
  if (typeof window === 'undefined' || typeof window.localStorage === 'undefined') {
    return;
  }

  try {
    window.localStorage.setItem(
      getDirectorySortStorageKey(storageKey),
      JSON.stringify(normalizeDirectorySortProgram(value))
    );
  } catch (error) {
    console.warn('Failed to persist directory sort program', error);
  }
};

const loadPersistedMediaScanLimit = (storageKey: string) => {
  if (typeof window === 'undefined' || typeof window.localStorage === 'undefined') {
    return DEFAULT_DEVICE_MEDIA_SCAN_LIMIT;
  }

  try {
    const savedValue = window.localStorage.getItem(getScanLimitStorageKey(storageKey)) || '';
    return normalizeMediaScanLimit(savedValue);
  } catch (error) {
    console.warn('Failed to load persisted device browser media scan limit', error);
    return DEFAULT_DEVICE_MEDIA_SCAN_LIMIT;
  }
};

const persistMediaScanLimit = (storageKey: string, value: number) => {
  if (typeof window === 'undefined' || typeof window.localStorage === 'undefined') {
    return;
  }

  try {
    window.localStorage.setItem(getScanLimitStorageKey(storageKey), String(normalizeMediaScanLimit(value)));
  } catch (error) {
    console.warn('Failed to persist device browser media scan limit', error);
  }
};

const loadPersistedRecursiveDisplay = (storageKey: string) => {
  if (typeof window === 'undefined' || typeof window.localStorage === 'undefined') {
    return false;
  }

  try {
    const savedValue = (window.localStorage.getItem(getRecursiveStorageKey(storageKey)) || '').trim().toLowerCase();
    return savedValue === '1' || savedValue === 'true';
  } catch (error) {
    console.warn('Failed to load persisted device browser recursive display state', error);
    return false;
  }
};

const persistRecursiveDisplay = (storageKey: string, value: boolean) => {
  if (typeof window === 'undefined' || typeof window.localStorage === 'undefined') {
    return;
  }

  try {
    window.localStorage.setItem(getRecursiveStorageKey(storageKey), value ? '1' : '0');
  } catch (error) {
    console.warn('Failed to persist device browser recursive display state', error);
  }
};

const formatPathInput = (value: string) => (isDeviceRootPath(value) ? DEVICE_ROOT_LABEL : value);

const normalizePathInput = (value: string) => {
  const trimmed = (value || '').trim();
  if (!trimmed || trimmed === DEVICE_ROOT_LABEL || trimmed === DEVICE_ROOT_SENTINEL) {
    return DEVICE_ROOT_SENTINEL;
  }
  return isAbsolutePath(trimmed) ? trimmed : '';
};

const normalizeComparablePath = (value: string) => {
  let normalized = (value || '').trim().replace(/\//g, '\\').replace(/\\+/g, '\\');
  if (/^[a-zA-Z]:\\?$/.test(normalized)) {
    return `${normalized.slice(0, 2)}\\`.toLowerCase();
  }
  normalized = normalized.replace(/\\+$/, '');
  return normalized.toLowerCase();
};

const isSameOrSubPath = (candidate: string, root: string) => {
  const normalizedCandidate = normalizeComparablePath(candidate);
  const normalizedRoot = normalizeComparablePath(root);
  return normalizedCandidate === normalizedRoot || normalizedCandidate.startsWith(`${normalizedRoot}\\`);
};

const normalizedFixedRootPath = computed(() => {
  const normalized = normalizePathInput(props.fixedRootPath);
  return normalized && !isDeviceRootPath(normalized) ? normalized : '';
});
const hasFixedRootBoundary = computed(() => Boolean(normalizedFixedRootPath.value));
const emptyBadgeText = '设备文件';
const deviceFieldLabel = computed(() => (isDeviceLocked.value ? '设备（固定）' : '设备'));
const pathInputPlaceholder = computed(() =>
  hasFixedRootBoundary.value
    ? `当前页面限制在 ${normalizedFixedRootPath.value} 及其子目录`
    : '输入绝对路径，例如 D:\\home\\chenkunze\\data'
);

const isPathWithinFixedRoot = (pathValue: string) => {
  if (!hasFixedRootBoundary.value) {
    return true;
  }
  if (isDeviceRootPath(pathValue)) {
    return false;
  }
  return isSameOrSubPath(pathValue, normalizedFixedRootPath.value);
};

const applyPathConstraints = (value: string, options?: { fallbackToRoot?: boolean }) => {
  const normalized = normalizePathInput(value);
  if (!normalized) {
    return '';
  }
  if (!hasFixedRootBoundary.value) {
    return normalized;
  }
  if (isDeviceRootPath(normalized)) {
    return options?.fallbackToRoot === false ? '' : normalizedFixedRootPath.value;
  }
  if (isPathWithinFixedRoot(normalized)) {
    return normalized;
  }
  return options?.fallbackToRoot === false ? '' : normalizedFixedRootPath.value;
};

const loadPersistedPath = (entryId: string) => {
  if (typeof window === 'undefined' || typeof window.localStorage === 'undefined') {
    return DEVICE_ROOT_SENTINEL;
  }

  try {
    const savedValue = window.localStorage.getItem(getPathStorageKey(entryId)) || '';
    return normalizePathInput(savedValue) || DEVICE_ROOT_SENTINEL;
  } catch (error) {
    console.warn('Failed to load persisted device browser path', error);
    return DEVICE_ROOT_SENTINEL;
  }
};

const persistSelectedPath = (entryId: string, value: string) => {
  if (typeof window === 'undefined' || typeof window.localStorage === 'undefined') {
    return;
  }

  try {
    window.localStorage.setItem(getPathStorageKey(entryId), value || DEVICE_ROOT_SENTINEL);
  } catch (error) {
    console.warn('Failed to persist device browser path', error);
  }
};

const resolvePathForEntry = (entryId: string, routePathValue: string) => {
  const routePath = routePathValue
    ? applyPathConstraints(routePathValue, { fallbackToRoot: true })
    : '';
  if (routePath) {
    return routePath;
  }
  const persistedPath = applyPathConstraints(loadPersistedPath(entryId), { fallbackToRoot: true });
  if (persistedPath) {
    return persistedPath;
  }
  return hasFixedRootBoundary.value ? normalizedFixedRootPath.value : DEVICE_ROOT_SENTINEL;
};

const resolveInitialPath = (entryId: string) => resolvePathForEntry(entryId, getQueryString(route.query.path));

const canBrowseFor = (entryId: string, pathValue: string) =>
  Boolean(entryId && (isDeviceRootPath(pathValue) || isAbsolutePath(pathValue)));

selectedPath.value = resolveInitialPath(selectedEntryId.value);
pathInputValue.value = formatPathInput(selectedPath.value);
recursiveDisplay.value = false;

const getAbsoluteParentPath = (value: string) => {
  let current = (value || '').trim();
  if (!current) {
    return '';
  }
  if (/^[a-zA-Z]:[\\/]?$/.test(current)) {
    return '';
  }
  if (/^\\\\[^\\/]+[\\/][^\\/]+[\\/]?$/.test(current)) {
    return '';
  }
  current = current.replace(/[\\/]+$/, '');
  const parent = current.replace(/[\\/][^\\/]+$/, '');
  if (!parent || parent === current) {
    return '';
  }
  if (/^[a-zA-Z]:$/.test(parent)) {
    return `${parent}\\`;
  }
  if (/^\\\\[^\\/]+[\\/][^\\/]+$/.test(parent)) {
    return `${parent}\\`;
  }
  return parent;
};

const normalizedPathInput = computed(() => normalizePathInput(selectedPath.value));
const canBrowse = computed(() => canBrowseFor(selectedEntryId.value, selectedPath.value));
const isLockedDeviceMissing = computed(() =>
  isDeviceLocked.value && !lockedEntryId.value && devices.value.length > 0
);
const showEmptyPanel = computed(() => !devices.value.length || isLockedDeviceMissing.value);
const emptyStateTitle = computed(() =>
  isLockedDeviceMissing.value ? `未找到设备 ${props.fixedDeviceId}` : '还没有可用设备'
);
const emptyStateDescription = computed(() => {
  if (isLockedDeviceMissing.value) {
    const targetPath = normalizedFixedRootPath.value || '固定目录';
    return `当前页面固定使用设备 ${props.fixedDeviceId}，但当前账号下没有找到对应入口。先到运行管理里添加或授权该设备，再回来浏览 ${targetPath}。`;
  }
  return '先到运行管理里添加本地或远程设备入口，再从设备上下文里浏览真实目录。';
});
const listingItems = computed(() => listing.value?.items ?? []);
const galleryStorageKey = computed(() => `device_media_gallery_${selectedEntryId.value || 'default'}`);
const getParentPathWithinConstraints = (value: string) => {
  const parent = getAbsoluteParentPath(value);
  if (!parent) {
    return '';
  }
  if (!hasFixedRootBoundary.value) {
    return parent;
  }
  return isPathWithinFixedRoot(parent) ? parent : normalizedFixedRootPath.value;
};
const canGoUp = computed(() => {
  if (!canBrowse.value || isDeviceRootPath(normalizedPathInput.value)) {
    return false;
  }
  const parent = getAbsoluteParentPath(normalizedPathInput.value);
  if (!parent) {
    return false;
  }
  if (!hasFixedRootBoundary.value) {
    return true;
  }
  return normalizeComparablePath(normalizedPathInput.value) !== normalizeComparablePath(normalizedFixedRootPath.value)
    && isPathWithinFixedRoot(parent);
});
const directoryEntries = computed(() => listingItems.value.filter((entry) => entry.is_dir));
const directoryPageCount = computed(() =>
  Math.max(1, Math.ceil(directoryEntries.value.length / DEFAULT_DIRECTORY_PAGE_SIZE))
);
const pagedDirectoryEntries = computed(() => {
  const offset = Math.max(0, (Math.max(1, currentDirectoryPage.value) - 1) * DEFAULT_DIRECTORY_PAGE_SIZE);
  return directoryEntries.value.slice(offset, offset + DEFAULT_DIRECTORY_PAGE_SIZE);
});
const orderedMediaItems = computed(() => mediaItems.value);
const duplicateClusterRuleActive = computed(() =>
  backendSortProgram.value.rules.some((rule) => rule.field === 'duplicate_cluster')
);
const mediaVisualHashHint = computed(() => {
  const status = mediaVisualHashStatus.value;
  if (!duplicateClusterRuleActive.value || !status || status.total_image_count <= 0) {
    return null;
  }
  const scopeLabel = '当前页';
  return {
    label: status.complete
      ? `${scopeLabel}索引已就绪 ${status.indexed_count}/${status.total_image_count}`
      : `${scopeLabel}索引补齐中 ${status.indexed_count}/${status.total_image_count}`,
    tone: status.complete ? 'ready' : 'active',
    loading: !status.complete,
    detail: [
      '当前排序包含“重复聚簇”，后端只会对当前页条目复用或补齐视觉哈希，并做页内聚簇。',
      `${scopeLabel}图片：${status.total_image_count} 张`,
      `已就绪：${status.indexed_count} 张`,
      status.missing_count > 0 ? `待补齐：${status.missing_count} 张` : '待补齐：0 张',
      status.computed_count > 0 ? `本次现算：${status.computed_count} 张` : '',
      status.reused_content_hash_count > 0 ? `同内容复用：${status.reused_content_hash_count} 张` : '',
      status.prewarm_scheduled_count > 0 ? `后台已排队预热：${status.prewarm_scheduled_count} 张` : '',
    ].filter(Boolean).join('\n'),
  };
});
const previewIndex = computed(() => {
  if (!previewImageId.value) {
    return -1;
  }
  return orderedMediaItems.value.findIndex((item) => item.id === previewImageId.value);
});
const previewImage = computed(() => {
  if (previewIndex.value < 0) {
    return null;
  }
  return orderedMediaItems.value[previewIndex.value] ?? null;
});
const hasPreviousImage = computed(() => previewIndex.value > 0);
const hasNextImage = computed(
  () => previewIndex.value >= 0 && previewIndex.value < orderedMediaItems.value.length - 1
);
const previewPositionText = computed(() => {
  if (previewIndex.value < 0) {
    return '-- / --';
  }
  return `${previewIndex.value + 1} / ${orderedMediaItems.value.length}`;
});
const mediaEmptyInlineText = computed(() =>
  isLoadingMediaPage.value
    ? '媒体索引加载中，目录可先浏览'
    : '当前筛选条件下没有可显示的媒体'
);

const syncPathInputFromSelection = () => {
  pathInputValue.value = formatPathInput(selectedPath.value);
};

let suppressNextBackendSortProgramReload = false;
let suppressNextDirectorySortProgramReload = false;
let suppressNextRecursiveDisplayReload = false;

const restoreBackendSortProgram = (storageKey: string) => {
  suppressNextBackendSortProgramReload = true;
  backendSortProgram.value = loadPersistedBackendSortProgram(storageKey);
};

const restoreDirectorySortProgram = (storageKey: string) => {
  suppressNextDirectorySortProgramReload = true;
  directorySortProgram.value = loadPersistedDirectorySortProgram(storageKey);
};

const restoreMediaScanLimit = (storageKey: string) => {
  const restoredLimit = loadPersistedMediaScanLimit(storageKey);
  mediaScanLimit.value = restoredLimit;
  mediaScanLimitInput.value = restoredLimit;
};

const restoreRecursiveDisplay = (storageKey: string) => {
  suppressNextRecursiveDisplayReload = true;
  recursiveDisplay.value = loadPersistedRecursiveDisplay(storageKey);
};

restoreBackendSortProgram(galleryStorageKey.value);
restoreDirectorySortProgram(galleryStorageKey.value);
restoreMediaScanLimit(galleryStorageKey.value);
restoreRecursiveDisplay(galleryStorageKey.value);

const commitPathInput = async (options?: { load?: boolean }) => {
  const rawNormalizedPath = normalizePathInput(pathInputValue.value);
  if (!rawNormalizedPath) {
    syncPathInputFromSelection();
    if (options?.load) {
      ElMessage.warning('请输入绝对路径');
    }
    return false;
  }

  const normalizedPath = applyPathConstraints(rawNormalizedPath, { fallbackToRoot: false });
  if (!normalizedPath) {
    syncPathInputFromSelection();
    if (options?.load && hasFixedRootBoundary.value) {
      ElMessage.warning(`当前页面只允许浏览 ${normalizedFixedRootPath.value} 及其子目录`);
    }
    return false;
  }

  selectedPath.value = normalizedPath;
  syncPathInputFromSelection();
  if (options?.load) {
    await syncRouteQuery('push');
    await loadDirectory();
  }
  return true;
};

const handleSubmitPath = () => {
  void commitPathInput({ load: true });
};

const handlePathBlur = () => {
  void commitPathInput();
};

const buildDirectoryListPayload = () => ({
  absolute_path: normalizedPathInput.value,
  sort_program: cloneDirectorySortProgram(directorySortProgram.value),
});

const buildEntryPayload = (item: DeviceDirectoryItem): DeviceFileSelector => {
  return { absolute_path: item.path };
};

const buildImagePayload = (image: DeviceBrowserImage): DeviceFileSelector => {
  return { absolute_path: image.absolutePath };
};

const getMediaPageOffset = (page = currentMediaPage.value) =>
  Math.max(0, (Math.max(1, page) - 1) * Math.max(1, mediaPageSize.value));

const buildMediaListPayload = (
  offset = getMediaPageOffset(),
  options?: { includeSnapshot?: boolean }
): DeviceMediaListRequest => ({
  absolute_path: normalizedPathInput.value,
  recursive: recursiveDisplay.value,
  scan_limit: mediaScanLimit.value,
  sort_program: cloneGallerySortProgram(backendSortProgram.value),
  snapshot_id: options?.includeSnapshot === false ? undefined : (mediaSnapshotId.value ?? undefined),
  offset,
  limit: mediaPageSize.value,
});

const resetMediaPagination = () => {
  mediaTotalCount.value = 0;
  mediaTotalBytes.value = 0;
  currentMediaPage.value = 1;
  mediaSnapshotId.value = null;
  mediaListingDirty.value = false;
  mediaVisualHashStatus.value = null;
};

const mapDeviceMediaRecord = (record: DeviceImageRecord): DeviceBrowserImage => {
  const absolutePath = record.absolute_path || record.path;
  return {
    id: String(record.id),
    name: record.name,
    relativePath: record.relative_path,
    folderPath: record.folder_path || '',
    folderDisplayPath: getParentPath(absolutePath) || normalizedPathInput.value,
    size: record.size,
    createdAt: typeof record.created_at === 'number' ? record.created_at : null,
    modifiedAt: record.modified_at,
    url: null,
    urlVariant: null,
    thumbnailFailed: false,
    thumbnailVersion: null,
    lastAccessedAt: null,
    urlNeedsRevoke: false,
    width: typeof record.width === 'number' ? record.width : null,
    height: typeof record.height === 'number' ? record.height : null,
    kind: record.kind ?? 'image',
    mimeType: record.mime_type ?? null,
    duration: typeof record.duration_ms === 'number' ? record.duration_ms / 1000 : null,
    weight: typeof record.weight === 'number' ? record.weight : 0,
    duplicateClusterOrder: typeof record.duplicate_cluster_order === 'number' ? record.duplicate_cluster_order : null,
    duplicateClusterDistance: typeof record.duplicate_cluster_distance === 'number' ? record.duplicate_cluster_distance : null,
    duplicateClusterMemberOrder: typeof record.duplicate_cluster_member_order === 'number' ? record.duplicate_cluster_member_order : null,
    duplicateClusterSize: typeof record.duplicate_cluster_size === 'number' ? record.duplicate_cluster_size : null,
    filePath: absolutePath,
    absolutePath,
  };
};

const applyMediaListing = (mediaListing: DeviceMediaListing, options?: { append?: boolean }) => {
  mediaTotalCount.value = mediaListing.total_count ?? 0;
  mediaTotalBytes.value = mediaListing.total_bytes ?? 0;
  mediaSnapshotId.value = mediaListing.snapshot_id ?? mediaSnapshotId.value;
  mediaListingDirty.value = false;
  mediaVisualHashStatus.value = mediaListing.visual_hash_status ?? null;

  const nextItems = mediaListing.media.map(mapDeviceMediaRecord);
  if (options?.append) {
    const existingIds = new Set(mediaItems.value.map((item) => item.id));
    mediaItems.value = mediaItems.value.concat(nextItems.filter((item) => !existingIds.has(item.id)));
    return;
  }

  replaceMediaItems(nextItems);
};

const loadMediaPage = async (
  page = currentMediaPage.value,
  options?: { resetSnapshot?: boolean }
) => {
  if (!selectedEntryId.value || !canBrowse.value) {
    resetMediaPagination();
    replaceMediaItems([]);
    return false;
  }

  const entryId = selectedEntryId.value;
  const pathValue = normalizedPathInput.value;
  const targetPage = Math.max(1, Math.floor(page || 1));
  const requestVersion = ++mediaLoadVersion;
  const shouldResetSnapshot = Boolean(options?.resetSnapshot || mediaListingDirty.value);
  if (shouldResetSnapshot) {
    mediaSnapshotId.value = null;
  }
  isLoadingMediaPage.value = true;
  try {
    const mediaResult = await fetchDeviceMedia(
      entryId,
      buildMediaListPayload(getMediaPageOffset(targetPage), {
        includeSnapshot: !shouldResetSnapshot,
      })
    );
    if (
      mediaLoadVersion !== requestVersion
      || selectedEntryId.value !== entryId
      || normalizedPathInput.value !== pathValue
    ) {
      return false;
    }

    applyMediaListing(mediaResult);
    currentMediaPage.value = targetPage;
    return true;
  } catch (error) {
    if (
      mediaLoadVersion === requestVersion
      && selectedEntryId.value === entryId
      && normalizedPathInput.value === pathValue
    ) {
      console.error('Failed to load device media page', error);
      ElMessage.error('读取媒体分页失败');
    }
    return false;
  } finally {
    if (
      mediaLoadVersion === requestVersion
      && selectedEntryId.value === entryId
      && normalizedPathInput.value === pathValue
    ) {
      isLoadingMediaPage.value = false;
    }
  }
};

const getParentPath = (filePath: string) => {
  const normalized = (filePath || '').trim();
  const lastSeparatorIndex = Math.max(normalized.lastIndexOf('/'), normalized.lastIndexOf('\\'));

  if (lastSeparatorIndex < 0) {
    return '';
  }
  if (lastSeparatorIndex === 0) {
    return normalized[0];
  }

  const parentPath = normalized.slice(0, lastSeparatorIndex);
  if (/^[a-zA-Z]:$/.test(parentPath)) {
    return `${parentPath}\\`;
  }
  return parentPath;
};

const hasDuration = (image: DeviceBrowserImage) =>
  typeof image.duration === 'number' && Number.isFinite(image.duration) && image.duration >= 0;

const getPreviewFolderPath = (image: DeviceBrowserImage) =>
  image.folderDisplayPath || image.folderPath || normalizedPathInput.value || '根目录';

const getPreviewFormatLabel = (image: DeviceBrowserImage) => {
  const source = image.name || image.relativePath || '';
  const lastDotIndex = source.lastIndexOf('.');
  if (lastDotIndex >= 0 && lastDotIndex < source.length - 1) {
    return source.slice(lastDotIndex + 1).toLowerCase();
  }

  const mimeSubtype = image.mimeType?.split('/')[1];
  if (mimeSubtype) {
    return mimeSubtype.toLowerCase();
  }

  if (image.kind === 'pdf') return 'pdf';
  return image.kind === 'video' ? 'video' : 'image';
};

const getMediaLoadingText = (image: DeviceBrowserImage | null, full = false) => {
  if (image?.kind === 'pdf') {
    return full ? 'PDF 加载中' : 'PDF';
  }
  if (image?.thumbnailFailed) {
    return image.kind === 'video' ? '暂无视频首帧' : '缩略图失败';
  }
  if (full) return '原图加载中';
  return image?.kind === 'video' ? '视频封面加载中' : '图片加载中';
};

const isVideoMedia = (image: DeviceBrowserImage) => image.kind === 'video';
const canRenderPreviewMedia = (image: DeviceBrowserImage | null) =>
  Boolean(image?.url && (image.urlVariant === 'full' || !image.isFetchingUrl));
const shouldRenderPreviewAsVideo = (image: DeviceBrowserImage | null) =>
  Boolean(image && image.kind === 'video' && image.urlVariant === 'full');
const shouldRenderPreviewAsPdf = (image: DeviceBrowserImage | null) =>
  Boolean(image && image.kind === 'pdf' && image.urlVariant === 'full');

const isNativeStreamableVideo = (image: DeviceBrowserImage) => {
  if (image.kind !== 'video') {
    return false;
  }

  const normalizedMimeType = (image.mimeType || '').toLowerCase();
  if (normalizedMimeType && STREAMABLE_VIDEO_MIME_TYPES.has(normalizedMimeType)) {
    return true;
  }

  const normalizedName = image.name.toLowerCase();
  return STREAMABLE_VIDEO_EXTENSIONS.some((extension) => normalizedName.endsWith(extension));
};

const revokeMediaUrls = (items: DeviceBrowserImage[]) => {
  for (const item of items) {
    if (item.url && item.urlNeedsRevoke) {
      URL.revokeObjectURL(item.url);
    }
  }
};

const clearMediaUrl = (item: DeviceBrowserImage) => {
  if (item.url && item.urlNeedsRevoke) {
    URL.revokeObjectURL(item.url);
  }
  item.url = null;
  item.urlVariant = null;
  item.lastAccessedAt = null;
  item.urlNeedsRevoke = false;
};

const invalidateThumbnailCache = (item: DeviceBrowserImage) => {
  item.thumbnailFailed = false;
  item.thumbnailVersion = Date.now();
  if (item.urlVariant === 'thumbnail') {
    clearMediaUrl(item);
  }
};

const trimLoadedMediaCache = (protectedImageId?: string) => {
  const trimByVariant = (variant: GalleryUrlVariant, limit: number) => {
    const loadedItems = mediaItems.value
      .filter((item) => item.url && item.urlVariant === variant && !item.isFetchingUrl && item.id !== protectedImageId)
      .sort((left, right) => (left.lastAccessedAt ?? 0) - (right.lastAccessedAt ?? 0));

    const overflow = loadedItems.length - limit;
    if (overflow <= 0) {
      return;
    }

    for (const item of loadedItems.slice(0, overflow)) {
      clearMediaUrl(item);
    }
  };

  trimByVariant('full', MAX_CACHED_FULL_MEDIA);
};

const replaceMediaUrlFromBlob = (item: DeviceBrowserImage, blob: Blob, variant: GalleryUrlVariant) => {
  clearMediaUrl(item);
  item.url = URL.createObjectURL(blob);
  item.urlNeedsRevoke = true;
  item.urlVariant = variant;
  item.thumbnailFailed = false;
  item.lastAccessedAt = Date.now();
  trimLoadedMediaCache(item.id);
};

const replaceMediaUrlFromRemote = (item: DeviceBrowserImage, url: string, variant: GalleryUrlVariant) => {
  clearMediaUrl(item);
  item.url = url;
  item.urlNeedsRevoke = false;
  item.urlVariant = variant;
  item.thumbnailFailed = false;
  item.lastAccessedAt = Date.now();
  trimLoadedMediaCache(item.id);
};

const replaceMediaItems = (nextItems: DeviceBrowserImage[]) => {
  revokeMediaUrls(mediaItems.value);
  pendingMediaRequests.clear();
  mediaItems.value = nextItems;
};

const removeMediaItemLocally = (imageId: string) => {
  const target = mediaItems.value.find((item) => item.id === imageId);
  if (!target) {
    return;
  }

  clearMediaUrl(target);
  pendingMediaRequests.delete(imageId);
  mediaItems.value = mediaItems.value.filter((item) => item.id !== imageId);
  mediaTotalCount.value = Math.max(0, mediaTotalCount.value - 1);
  mediaTotalBytes.value = Math.max(0, mediaTotalBytes.value - (target.size || 0));
};

const markMediaListingDirty = () => {
  mediaListingDirty.value = true;
  mediaSnapshotId.value = null;
};

const buildRouteQuery = (entryId = selectedEntryId.value, pathValue = normalizedPathInput.value) => {
  const nextQuery: Record<string, string> = {};
  if (entryId) {
    nextQuery.entry_id = entryId;
  }
  if (!isDeviceRootPath(pathValue)) {
    nextQuery.path = pathValue;
  }
  return nextQuery;
};

const syncRouteQuery = async (mode: 'replace' | 'push' = 'replace') => {
  const currentRoutePath = normalizePathInput(getQueryString(route.query.path)) || DEVICE_ROOT_SENTINEL;
  const currentQuery = buildRouteQuery(getQueryString(route.query.entry_id), currentRoutePath);
  const nextQuery = buildRouteQuery();

  if (currentQuery.entry_id === nextQuery.entry_id && currentQuery.path === nextQuery.path) {
    return false;
  }

  await router[mode]({
    path: route.path,
    query: nextQuery,
  });
  return true;
};

const loadDirectory = async () => {
  if (!canBrowse.value) {
    listing.value = null;
    currentDirectoryPage.value = 1;
    resetMediaPagination();
    replaceMediaItems([]);
    return;
  }

  const listingRequestVersion = ++directoryLoadVersion;
  const mediaRequestVersion = ++mediaLoadVersion;
  const entryId = selectedEntryId.value;
  const pathValue = normalizedPathInput.value;
  const shouldClearMediaBeforeReload = listing.value?.absolute_path !== pathValue;
  isLoadingListing.value = true;
  isLoadingMediaPage.value = true;
  currentDirectoryPage.value = 1;
  currentMediaPage.value = 1;
  mediaSnapshotId.value = null;
  if (shouldClearMediaBeforeReload) {
    resetMediaPagination();
    replaceMediaItems([]);
  }
  const mediaResultPromise = fetchDeviceMedia(entryId, buildMediaListPayload(0, { includeSnapshot: false }))
    .then((value) => ({ status: 'fulfilled' as const, value }))
    .catch((reason) => ({ status: 'rejected' as const, reason }));
  try {
    const directoryResult = await fetchDeviceDirectoryItems(entryId, buildDirectoryListPayload());
    if (
      listingRequestVersion !== directoryLoadVersion
      || selectedEntryId.value !== entryId
      || normalizedPathInput.value !== pathValue
    ) {
      return;
    }

    listing.value = directoryResult;
    isLoadingListing.value = false;
    await syncRouteQuery();

    const mediaResult = await mediaResultPromise;

    if (
      mediaRequestVersion === mediaLoadVersion
      && selectedEntryId.value === entryId
      && normalizedPathInput.value === pathValue
      && mediaResult.status === 'fulfilled'
    ) {
      applyMediaListing(mediaResult.value);
    } else if (
      mediaRequestVersion === mediaLoadVersion
      && selectedEntryId.value === entryId
      && normalizedPathInput.value === pathValue
    ) {
      resetMediaPagination();
      replaceMediaItems([]);
      ElMessage.warning('目录已加载，但媒体预览信息读取失败');
    }
  } catch (error) {
    console.error('Failed to list device directory', error);
    listing.value = null;
    resetMediaPagination();
    replaceMediaItems([]);
    ElMessage.error(
      normalizedPathInput.value
        ? `目录读取失败：${normalizedPathInput.value}`
        : '读取设备文件失败'
    );
  } finally {
    if (
      listingRequestVersion === directoryLoadVersion
      && selectedEntryId.value === entryId
      && normalizedPathInput.value === pathValue
    ) {
      isLoadingListing.value = false;
    }
    if (
      mediaRequestVersion === mediaLoadVersion
      && selectedEntryId.value === entryId
      && normalizedPathInput.value === pathValue
    ) {
      isLoadingMediaPage.value = false;
    }
  }
};

const handleMediaPageChange = (page: number) => {
  if (page === currentMediaPage.value || isLoadingListing.value || isLoadingMediaPage.value) {
    return;
  }
  void loadMediaPage(page);
};

const handleDirectoryPageChange = (page: number) => {
  currentDirectoryPage.value = Math.min(directoryPageCount.value, Math.max(1, Math.floor(page || 1)));
};

const handleMediaPageSizeChange = (pageSize: number) => {
  const normalizedPageSize = Math.max(1, Math.floor(pageSize || DEFAULT_DEVICE_MEDIA_PAGE_SIZE));
  if (normalizedPageSize === mediaPageSize.value) {
    return;
  }
  mediaPageSize.value = normalizedPageSize;
  currentMediaPage.value = 1;
  void loadMediaPage(1, { resetSnapshot: true });
};

const handleMediaScanLimitChange = (nextLimit?: number) => {
  const normalizedLimit = normalizeMediaScanLimit(nextLimit);
  mediaScanLimitInput.value = normalizedLimit;
  if (normalizedLimit === mediaScanLimit.value) {
    return;
  }
  mediaScanLimit.value = normalizedLimit;
  persistMediaScanLimit(galleryStorageKey.value, normalizedLimit);
  currentMediaPage.value = 1;
  if (canBrowse.value) {
    void loadMediaPage(1, { resetSnapshot: true });
  }
};

const openDirectory = async (path: string) => {
  selectedPath.value = path;
  syncPathInputFromSelection();
  await syncRouteQuery('push');
  await loadDirectory();
};

const goToParentDirectory = async () => {
  if (!canGoUp.value) {
    return;
  }
  const parentPath = getParentPathWithinConstraints(normalizedPathInput.value);
  selectedPath.value = parentPath;
  syncPathInputFromSelection();
  await syncRouteQuery('push');
  await loadDirectory();
};

const downloadFile = async (item: DeviceDirectoryItem | DeviceBrowserImage) => {
  const isDirectory = 'is_dir' in item ? item.is_dir : false;
  if (isDirectory || !selectedEntryId.value) {
    return;
  }
  const targetPath = 'absolutePath' in item ? item.absolutePath : item.path;
  downloadingPath.value = targetPath;
  try {
    const payload = 'absolutePath' in item ? { absolute_path: item.absolutePath } : buildEntryPayload(item);
    const blob = await fetchDeviceFileBlob(selectedEntryId.value, payload);
    const objectUrl = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = objectUrl;
    anchor.download = item.name;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(objectUrl);
  } catch (error) {
    console.error('Failed to download device file', error);
    ElMessage.error('下载文件失败');
  } finally {
    downloadingPath.value = '';
  }
};

const downloadPreviewFile = async () => {
  if (!previewImage.value) {
    return;
  }
  await downloadFile(previewImage.value);
};

const revealDeviceMediaInFolder = async (image: GalleryImage) => {
  if (!selectedEntryId.value) {
    return false;
  }

  const targetImage = image as DeviceBrowserImage;
  const targetPath = targetImage.absolutePath;
  if (!targetPath) {
    return false;
  }

  revealingPath.value = targetPath;
  try {
    const result = await revealDeviceEntryInFolder(selectedEntryId.value, buildImagePayload(targetImage));
    if (!result.launched) {
      ElMessage.info(result.detail || '当前设备不支持直接打开所在目录');
    }
    return result.launched;
  } catch (error) {
    console.error('Failed to reveal device media file in folder', error);
    ElMessage.error('打开所在目录失败');
    return false;
  } finally {
    if (revealingPath.value === targetPath) {
      revealingPath.value = '';
    }
  }
};

const revealPreviewInFolder = async () => {
  if (!previewImage.value) {
    return;
  }
  await revealDeviceMediaInFolder(previewImage.value);
};

const openPdfDocument = async (image: GalleryImage) => {
  if (!selectedEntryId.value) {
    return false;
  }
  const target = image as DeviceBrowserImage;
  if (target.kind !== 'pdf' || !target.absolutePath) {
    return false;
  }

  openingPdfPath.value = target.absolutePath;
  try {
    const document = await createPdfDocumentFromDeviceFile({
      entry_id: selectedEntryId.value,
      absolute_path: target.absolutePath,
    });
    await router.push(`/pdf/${document.id}`);
    return true;
  } catch (error) {
    console.error('Failed to open PDF document reader', error);
    ElMessage.error('打开 PDF 阅读器失败');
    return false;
  } finally {
    if (openingPdfPath.value === target.absolutePath) {
      openingPdfPath.value = '';
    }
  }
};

const setVideoCover = async (imageId: string, cover: Blob) => {
  const target = mediaItems.value.find((item) => item.id === imageId);
  if (!target || !selectedEntryId.value) return false;

  try {
    await setDeviceFileCover(selectedEntryId.value, buildImagePayload(target), cover);
    invalidateThumbnailCache(target);
    if (target.urlVariant !== 'full') {
      await ensureMediaReady(target);
    }
    ElMessage.success('封面已更新');
    return true;
  } catch (error) {
    console.error('Failed to set device media cover', error);
    ElMessage.error('设置封面失败');
    return false;
  }
};

const updateImageWeight = async (imageId: string, nextWeight: number) => {
  const target = mediaItems.value.find((item) => item.id === imageId);
  if (!target || !selectedEntryId.value) return false;

  try {
    await setDeviceFileWeight(selectedEntryId.value, buildImagePayload(target), nextWeight);
    target.weight = nextWeight;
    return true;
  } catch (error) {
    console.error('Failed to update device media weight', error);
    ElMessage.error('更新权重失败');
    return false;
  }
};

const deleteImage = async (imageId: string) => {
  const target = mediaItems.value.find((item) => item.id === imageId);
  if (!target || !selectedEntryId.value) return false;
  const entryId = selectedEntryId.value;

  try {
    const started = await startDeviceEntryDelete(entryId, { absolute_path: target.absolutePath });
    await monitorPolledTask<DeviceDeleteTask>({
      initial: started.task,
      poll: (task) => fetchDeviceEntryDeleteTask(entryId, task.task_id || task.id),
      isRunning: (task) => task.status === 'pending' || task.status === 'running',
      getUpdatedAt: (task) => task.updated_at,
      getError: (task) => task.status === 'failed' ? (task.error_message || '删除文件失败') : '',
      pollIntervalMs: 1000,
      idleTimeoutMs: 30_000,
    });
    if (listing.value) {
      listing.value = {
        ...listing.value,
        items: listing.value.items.filter((item) => item.path !== target.absolutePath),
      };
    }
    removeMediaItemLocally(imageId);
    markMediaListingDirty();
    ElMessage.success('文件已删除');
    return true;
  } catch (error) {
    console.error('Failed to delete device media file', error);
    ElMessage.error('删除文件失败');
    return false;
  }
};

const ensureMediaReady = async (image: GalleryImage, options?: { full?: boolean }) => {
  const target = mediaItems.value.find((item) => item.id === image.id);
  if (!target || !selectedEntryId.value) {
    return;
  }

  const desiredVariant: GalleryUrlVariant = options?.full ? 'full' : 'thumbnail';
  if (target.url && target.urlVariant === desiredVariant) {
    target.lastAccessedAt = Date.now();
    return;
  }
  if (target.kind === 'pdf' && desiredVariant === 'thumbnail') {
    target.urlVariant = 'thumbnail';
    target.thumbnailFailed = false;
    target.lastAccessedAt = Date.now();
    return;
  }
  if (desiredVariant === 'thumbnail' && target.thumbnailFailed) {
    return;
  }

  const pendingRequest = pendingMediaRequests.get(target.id);
  if (pendingRequest) {
    await pendingRequest;
    const updatedTarget = mediaItems.value.find((item) => item.id === image.id);
    if (updatedTarget?.url && updatedTarget.urlVariant === desiredVariant) {
      return;
    }
  }

  const requestVersion = mediaLoadVersion;
  const entryId = selectedEntryId.value;
  const pathValue = normalizedPathInput.value;
  const payload = buildImagePayload(target);
  const requestPromise = (async () => {
    target.isFetchingUrl = true;
    try {
      const latestTarget = mediaItems.value.find((item) => item.id === image.id);
      if (
        !latestTarget
        || mediaLoadVersion !== requestVersion
        || selectedEntryId.value !== entryId
        || normalizedPathInput.value !== pathValue
      ) {
        return;
      }

      if (desiredVariant === 'full' && isNativeStreamableVideo(target)) {
        const streamUrl = await fetchDeviceFileStreamUrl(entryId, payload);
        if (!streamUrl) {
          throw new Error('Empty stream url');
        }
        const currentTarget = mediaItems.value.find((item) => item.id === image.id);
        if (
          !currentTarget
          || mediaLoadVersion !== requestVersion
          || selectedEntryId.value !== entryId
          || normalizedPathInput.value !== pathValue
        ) {
          return;
        }
        replaceMediaUrlFromRemote(currentTarget, streamUrl, desiredVariant);
        return;
      }

      const blob = desiredVariant === 'thumbnail'
        ? await fetchDeviceThumbnailBlob(entryId, payload, {
          max_edge: THUMBNAIL_MAX_EDGE,
          cache_key: target.thumbnailVersion ?? undefined,
        })
        : await fetchDeviceMediaBlob(entryId, payload);
      const currentTarget = mediaItems.value.find((item) => item.id === image.id);
      if (
        !currentTarget
        || mediaLoadVersion !== requestVersion
        || selectedEntryId.value !== entryId
        || normalizedPathInput.value !== pathValue
      ) {
        return;
      }
      replaceMediaUrlFromBlob(currentTarget, blob, desiredVariant);
    } finally {
      const latestTarget = mediaItems.value.find((item) => item.id === image.id);
      if (latestTarget) {
        latestTarget.isFetchingUrl = false;
      }
      pendingMediaRequests.delete(image.id);
    }
  })();

  pendingMediaRequests.set(target.id, requestPromise);
  try {
    await requestPromise;
  } catch (error) {
    const latestTarget = mediaItems.value.find((item) => item.id === image.id);
    if (latestTarget && desiredVariant === 'thumbnail') {
      latestTarget.thumbnailFailed = true;
    }
    if (
      mediaLoadVersion === requestVersion
      && selectedEntryId.value === entryId
      && normalizedPathInput.value === pathValue
    ) {
      console.warn('Failed to fetch device media asset', target.filePath, error);
    }
  }
};

const warmCurrentDirectoryThumbnails = async () => {
  if (!orderedMediaItems.value.length) {
    return;
  }

  const queue = orderedMediaItems.value.filter(
    (item) => !item.url && !item.thumbnailFailed && !item.isFetchingUrl
  );
  for (let index = 0; index < queue.length; index += THUMBNAIL_WARM_CONCURRENCY) {
    const batch = queue.slice(index, index + THUMBNAIL_WARM_CONCURRENCY);
    await Promise.allSettled(batch.map((item) => ensureMediaReady(item)));
  }
};

const handleOpenPreview = async (imageId: string) => {
  const image = orderedMediaItems.value.find((item) => item.id === imageId);
  if (!image) {
    return;
  }
  previewImageId.value = imageId;
  previewVisible.value = true;
  await ensureMediaReady(image, { full: true });
};

const handleShowPrevious = async () => {
  if (!hasPreviousImage.value) {
    return;
  }
  const nextImage = orderedMediaItems.value[previewIndex.value - 1];
  if (!nextImage) {
    return;
  }
  await handleOpenPreview(nextImage.id);
};

const handleShowNext = async () => {
  if (!hasNextImage.value) {
    return;
  }
  const nextImage = orderedMediaItems.value[previewIndex.value + 1];
  if (!nextImage) {
    return;
  }
  await handleOpenPreview(nextImage.id);
};

const isInteractiveTarget = (target: EventTarget | null) => {
  if (
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement ||
    target instanceof HTMLVideoElement ||
    target instanceof HTMLButtonElement
  ) {
    return true;
  }

  return target instanceof HTMLElement && target.isContentEditable;
};

const handleKeydown = (event: KeyboardEvent) => {
  if (!previewVisible.value || isInteractiveTarget(event.target)) {
    return;
  }

  if (event.key === 'ArrowLeft' && hasPreviousImage.value) {
    event.preventDefault();
    void handleShowPrevious();
  }

  if (event.key === 'ArrowRight' && hasNextImage.value) {
    event.preventDefault();
    void handleShowNext();
  }
};

watch(
  () => [route.query.entry_id, route.query.path],
  ([nextEntryId, nextPath]) => {
    const previousEntryId = selectedEntryId.value;
    const previousPath = normalizePathInput(selectedPath.value) || DEVICE_ROOT_SENTINEL;
    const normalizedEntryId = isDeviceLocked.value
      ? lockedEntryId.value
      : getQueryString(nextEntryId);
    const nextPathString = getQueryString(nextPath);
    const normalizedPath = resolvePathForEntry(normalizedEntryId, nextPathString);

    if (normalizedEntryId !== selectedEntryId.value) {
      selectedEntryId.value = normalizedEntryId;
    }
    if (normalizedPath !== selectedPath.value) {
      selectedPath.value = normalizedPath;
    }

    if (normalizedEntryId === previousEntryId && normalizedPath !== previousPath) {
      if (canBrowseFor(normalizedEntryId, normalizedPath)) {
        void loadDirectory();
      } else {
        listing.value = null;
        resetMediaPagination();
        replaceMediaItems([]);
      }
    }
  }
);

watch(lockedEntryId, (nextEntryId) => {
  if (!isDeviceLocked.value) {
    return;
  }
  if (!nextEntryId) {
    if (selectedEntryId.value) {
      selectedEntryId.value = '';
    }
    return;
  }
  if (selectedEntryId.value !== nextEntryId) {
    selectedEntryId.value = nextEntryId;
  }
});

watch(selectedEntryId, async (nextEntryId) => {
  if (isDeviceLocked.value && lockedEntryId.value && nextEntryId !== lockedEntryId.value) {
    selectedEntryId.value = lockedEntryId.value;
    return;
  }
  listing.value = null;
  resetMediaPagination();
  replaceMediaItems([]);
  restoreDirectorySortProgram(`device_media_gallery_${nextEntryId || 'default'}`);
  restoreBackendSortProgram(`device_media_gallery_${nextEntryId || 'default'}`);
  restoreMediaScanLimit(`device_media_gallery_${nextEntryId || 'default'}`);
  restoreRecursiveDisplay(`device_media_gallery_${nextEntryId || 'default'}`);
  const routeEntryId = getQueryString(route.query.entry_id);
  const restoredPath = resolvePathForEntry(
    nextEntryId,
    routeEntryId === nextEntryId ? getQueryString(route.query.path) : ''
  );
  if (restoredPath !== selectedPath.value) {
    selectedPath.value = restoredPath;
  }
  syncPathInputFromSelection();
  await syncRouteQuery(routeEntryId && routeEntryId !== nextEntryId ? 'push' : 'replace');
  if (canBrowse.value) {
    await loadDirectory();
  }
});

watch(selectedPath, (nextPath) => {
  persistSelectedPath(selectedEntryId.value, nextPath || DEVICE_ROOT_SENTINEL);
  syncPathInputFromSelection();
});

watch(directoryPageCount, (nextPageCount) => {
  if (currentDirectoryPage.value > nextPageCount) {
    currentDirectoryPage.value = nextPageCount;
  }
});

watch(recursiveDisplay, (nextRecursive) => {
  persistRecursiveDisplay(galleryStorageKey.value, nextRecursive);
  if (suppressNextRecursiveDisplayReload) {
    suppressNextRecursiveDisplayReload = false;
    return;
  }
  if (canBrowse.value) {
    currentMediaPage.value = 1;
    void loadMediaPage(1, { resetSnapshot: true });
  }
});

watch(
  directorySortProgram,
  (nextProgram) => {
    persistDirectorySortProgram(galleryStorageKey.value, nextProgram);
    if (suppressNextDirectorySortProgramReload) {
      suppressNextDirectorySortProgramReload = false;
      return;
    }
    currentDirectoryPage.value = 1;
    if (canBrowse.value) {
      void loadDirectory();
    }
  },
  { deep: true }
);

watch(
  backendSortProgram,
  (nextProgram) => {
    persistBackendSortProgram(galleryStorageKey.value, nextProgram);
    if (suppressNextBackendSortProgramReload) {
      suppressNextBackendSortProgramReload = false;
      return;
    }
    if (canBrowse.value) {
      currentMediaPage.value = 1;
      void loadMediaPage(1, { resetSnapshot: true });
    }
  },
  { deep: true }
);

watch(
  () => orderedMediaItems.value.length,
  () => {
    void warmCurrentDirectoryThumbnails();
  }
);

watch(previewVisible, (visible) => {
  if (!visible) {
    previewImageId.value = null;
    previewVideoRef.value?.pause();
  }
});

watch(orderedMediaItems, (items) => {
  if (previewImageId.value && !items.some((item) => item.id === previewImageId.value)) {
    previewVisible.value = false;
  }
});

onMounted(async () => {
  window.addEventListener('keydown', handleKeydown);

  isLoadingDevices.value = true;
  try {
    await taskStore.fetchDevices();
  } finally {
    isLoadingDevices.value = false;
  }

  if (!devices.value.length) {
    selectedEntryId.value = '';
    return;
  }

  if (isDeviceLocked.value) {
    if (!lockedEntryId.value) {
      selectedEntryId.value = '';
      return;
    }
    if (selectedEntryId.value !== lockedEntryId.value) {
      selectedEntryId.value = lockedEntryId.value;
      return;
    }
  } else if (!selectedEntryId.value || !devices.value.some((device) => device.id === selectedEntryId.value)) {
    selectedEntryId.value = devices.value[0].id;
    return;
  }

  await syncRouteQuery();
  if (canBrowse.value) {
    await loadDirectory();
  }
});

onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleKeydown);
  revokeMediaUrls(mediaItems.value);
});
</script>

<style scoped>
.device-file-page {
  min-height: 100%;
  padding: 24px;
  box-sizing: border-box;
  background:
    radial-gradient(circle at top left, rgba(14, 116, 144, 0.1), transparent 24%),
    radial-gradient(circle at top right, rgba(22, 163, 74, 0.08), transparent 20%),
    linear-gradient(180deg, #f2f8fb 0%, #edf4f5 100%);
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.hero-panel,
.empty-panel,
.browser-panel {
  background: rgba(255, 255, 255, 0.94);
  border: 1px solid rgba(148, 163, 184, 0.18);
  border-radius: 24px;
  box-shadow: 0 18px 40px rgba(15, 23, 42, 0.08);
}

.hero-panel {
  padding: 24px 28px;
  display: grid;
  grid-template-columns: minmax(240px, 320px) minmax(0, 1fr);
  align-items: start;
  gap: 24px;
}

.hero-copy {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0;
  padding-top: 6px;
}

.eyebrow,
.section-label {
  font-size: 12px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: #64748b;
}

.hero-copy h1,
.browser-toolbar h2 {
  margin: 0;
  font-size: 34px;
  line-height: 1.1;
  color: #0f172a;
}

.hero-copy p,
.browser-toolbar p {
  margin: 0;
  font-size: 15px;
  color: #475569;
}

.hero-actions {
  display: flex;
  flex-direction: column;
  gap: 14px;
  width: min(100%, 680px);
  margin-left: auto;
  min-width: 0;
  padding: 18px 20px;
  border-radius: 22px;
  border: 1px solid rgba(148, 163, 184, 0.18);
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.96) 0%, rgba(248, 250, 252, 0.92) 100%);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.78),
    0 10px 24px rgba(15, 23, 42, 0.05);
}

.hero-field-row {
  display: grid;
  grid-template-columns: 88px minmax(0, 1fr);
  align-items: center;
  gap: 16px;
  min-width: 0;
}

.hero-field-label {
  font-size: 14px;
  font-weight: 600;
  color: #475569;
  text-align: left;
}

.hero-select {
  width: 100%;
}

.hero-limit-input {
  width: 220px;
}

.hero-actions :deep(.el-select__wrapper),
.hero-actions :deep(.el-input__wrapper),
.hero-actions :deep(.el-input-number) {
  border-radius: 14px;
}

.hero-actions :deep(.el-input-number) {
  width: 100%;
}

.hero-actions :deep(.el-input__inner) {
  text-align: left;
}

.hero-actions :deep(.el-input-number .el-input__inner) {
  text-align: left;
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
  background: linear-gradient(135deg, #0f766e 0%, #2563eb 100%);
  color: #ffffff;
  font-size: 13px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  box-shadow: 0 18px 36px rgba(15, 118, 110, 0.24);
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

.empty-actions {
  margin-top: 22px;
}

.browser-panel {
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-height: 640px;
  flex: 1;
}

.browser-toolbar {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.toolbar-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.view-mode-switch :deep(.el-radio-button__inner) {
  min-width: 92px;
}

.table-shell {
  flex: 1;
  min-height: 0;
}

.icon-shell {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 16px;
}

.waterfall-media-section {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.device-directory-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
  height: 100%;
}

.directory-config-row {
  display: grid;
  grid-template-columns: minmax(280px, 1fr) 220px;
  gap: 16px;
  align-items: end;
}

.directory-config-field {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.directory-config-label {
  color: #475569;
  font-size: 13px;
  font-weight: 600;
}

.directory-config-select,
.directory-config-limit {
  width: 100%;
}

.directory-config-row :deep(.el-select__wrapper),
.directory-config-row :deep(.el-input-number) {
  border-radius: 16px;
}

.directory-config-row :deep(.el-input-number) {
  width: 100%;
}

.directory-config-row :deep(.el-input__inner),
.directory-config-row :deep(.el-input-number .el-input__inner) {
  text-align: left;
}

.directory-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  min-width: 0;
}

.directory-section-count {
  color: #64748b;
  font-size: 12px;
}

.directory-path-input {
  flex: 1 1 420px;
  min-width: 280px;
}

.directory-action-button {
  min-width: 104px;
}

.directory-recursive-toggle {
  align-self: center;
  display: inline-flex;
  align-items: center;
  height: 40px;
  margin: 0;
}

.directory-recursive-toggle :deep(.el-switch__core) {
  height: 40px;
  min-height: 40px;
  border-radius: 999px;
}

.directory-recursive-toggle :deep(.el-switch__core .el-switch__inner .is-text) {
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.01em;
}

.directory-recursive-toggle :deep(.el-switch__action) {
  width: 24px;
  height: 24px;
}

.directory-section-count {
  margin-left: auto;
  min-width: 58px;
  min-height: 36px;
  padding: 0 10px;
  border-radius: 999px;
  background: #f8fafc;
  border: 1px solid rgba(148, 163, 184, 0.2);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  text-align: center;
  font-size: 11px;
  font-weight: 600;
  line-height: 1;
  white-space: nowrap;
}

.directory-toolbar :deep(.el-input__wrapper) {
  border-radius: 16px;
}

.directory-fixed-root-hint {
  margin-top: 10px;
  padding: 10px 12px;
  border-radius: 14px;
  background: #f8fafc;
  border: 1px solid rgba(148, 163, 184, 0.22);
  color: #556877;
  font-size: 12px;
  line-height: 1.6;
  word-break: break-all;
}

.collapse-toggle-btn {
  --el-button-bg-color: #f8fafc;
  --el-button-border-color: rgba(148, 163, 184, 0.28);
  --el-button-text-color: #334155;
  --el-button-hover-bg-color: #eff6ff;
  --el-button-hover-border-color: rgba(59, 130, 246, 0.34);
  --el-button-hover-text-color: #1d4ed8;
  --el-button-active-bg-color: #dbeafe;
  --el-button-active-border-color: rgba(59, 130, 246, 0.4);
  --el-button-active-text-color: #1d4ed8;
  min-height: 30px;
  padding-inline: 12px;
  border-radius: 999px;
  font-weight: 600;
}

.media-actions,
.media-pagination-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.media-actions {
  padding: 0 4px;
}

.media-pagination-bar {
  padding: 0 4px;
  justify-content: flex-end;
}

.media-pagination-inline {
  margin-left: auto;
}

.media-top-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.media-top-toolbar-left {
  display: flex;
  align-items: center;
  gap: 18px;
  flex-wrap: wrap;
  min-width: 0;
}

.media-toolbar-group {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.media-toolbar-group-mode {
  gap: 0;
}

.media-toolbar-group-scale {
  min-width: 280px;
}

.media-toolbar-group-index {
  gap: 8px;
}

.media-toolbar-label {
  color: #64748b;
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
}

.media-view-mode-switch :deep(.el-button) {
  min-width: 76px;
  font-weight: 600;
}

.media-toolbar-slider {
  width: 160px;
}

.media-toolbar-value {
  min-width: 44px;
  color: #334155;
  font-size: 13px;
  font-weight: 600;
  text-align: right;
  white-space: nowrap;
}

.media-index-pill {
  min-height: 32px;
  padding: 0 12px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 600;
  line-height: 1;
  white-space: nowrap;
  border: 1px solid transparent;
}

.media-index-pill.is-ready,
.media-index-pill.is-cached {
  background: rgba(236, 253, 245, 0.96);
  border-color: rgba(16, 185, 129, 0.2);
  color: #047857;
}

.media-index-pill.is-active,
.media-index-pill.is-warming {
  background: rgba(239, 246, 255, 0.96);
  border-color: rgba(59, 130, 246, 0.2);
  color: #1d4ed8;
}

.media-index-pill-icon {
  font-size: 13px;
}

.media-index-help {
  width: 28px;
  height: 28px;
  border: none;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: rgba(248, 250, 252, 0.9);
  color: #64748b;
  cursor: pointer;
  transition: background-color 0.18s ease, color 0.18s ease;
}

.media-index-help:hover {
  background: rgba(219, 234, 254, 0.92);
  color: #2563eb;
}

.media-index-tooltip {
  max-width: 280px;
  line-height: 1.6;
  white-space: pre-line;
}

.device-gallery-sidebar-stack {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.device-gallery-sort-select {
  width: 100%;
}

.directory-strip {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 6px;
  align-content: start;
}

.directory-chip {
  border: none;
  border-radius: 10px;
  background: rgba(248, 250, 252, 0.72);
  box-shadow: none;
  padding: 7px 10px;
  width: 100%;
  min-height: 34px;
  display: inline-flex;
  align-items: center;
  justify-content: flex-start;
  gap: 6px;
  color: #0f172a;
  cursor: pointer;
  font-size: 11px;
  line-height: 1.2;
  text-align: left;
  transition: background-color 0.16s ease, color 0.16s ease;
}

.directory-chip:hover,
.directory-chip:focus-visible {
  background: rgba(239, 246, 255, 0.96);
  color: #1d4ed8;
}

.directory-chip-icon {
  color: #b45309;
  flex-shrink: 0;
  font-size: 12px;
}

.directory-chip-name {
  flex: 1;
  min-width: 0;
  text-align: left;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.directory-pagination {
  display: flex;
  justify-content: flex-end;
  padding-top: 2px;
}

.directory-empty-state {
  min-height: 116px;
  border-radius: 18px;
  border: 1px dashed rgba(148, 163, 184, 0.34);
  background:
    radial-gradient(circle at top right, rgba(59, 130, 246, 0.08), transparent 34%),
    linear-gradient(180deg, rgba(248, 250, 252, 0.98) 0%, rgba(241, 245, 249, 0.95) 100%);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  color: #64748b;
  text-align: center;
}

.entry-button {
  border: none;
  background: transparent;
  display: inline-flex;
  align-items: center;
  gap: 10px;
  padding: 0;
  color: #0f172a;
  cursor: pointer;
  font: inherit;
}

.entry-button.is-directory {
  color: #1d4ed8;
}

.entry-button.is-previewable {
  color: #0f766e;
}

.entry-button:hover .entry-name {
  text-decoration: underline;
}

.entry-label {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  color: #0f172a;
}

.entry-icon {
  font-size: 16px;
  flex-shrink: 0;
}

.row-actions {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.entry-name,
.entry-path {
  word-break: break-all;
}

.entry-card {
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 22px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.98) 0%, rgba(248, 250, 252, 0.95) 100%);
  box-shadow: 0 14px 34px rgba(15, 23, 42, 0.08);
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  text-align: left;
  cursor: pointer;
  transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease;
}

.entry-card:hover:not(.is-static),
.entry-card:focus-visible:not(.is-static) {
  transform: translateY(-2px);
  box-shadow: 0 20px 40px rgba(15, 23, 42, 0.12);
  border-color: rgba(37, 99, 235, 0.28);
}

.entry-card.is-static {
  cursor: default;
}

.entry-card:disabled {
  opacity: 1;
}

.entry-card-thumb {
  position: relative;
  min-height: 180px;
  border-radius: 18px;
  overflow: hidden;
  background:
    radial-gradient(circle at top left, rgba(37, 99, 235, 0.16), transparent 36%),
    linear-gradient(180deg, #eff6ff 0%, #dbeafe 100%);
}

.entry-card-placeholder,
.entry-card-media {
  width: 100%;
  height: 100%;
  min-height: 180px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.entry-card-media {
  object-fit: cover;
}

.waterfall-video {
  object-fit: cover;
}

.entry-card-placeholder {
  color: #475569;
  font-size: 14px;
  padding: 16px;
  text-align: center;
}

.entry-card-placeholder.is-directory {
  background:
    radial-gradient(circle at top left, rgba(250, 204, 21, 0.28), transparent 34%),
    linear-gradient(180deg, #fef3c7 0%, #fde68a 100%);
  color: #92400e;
}

.entry-card-placeholder :deep(svg) {
  width: 46px;
  height: 46px;
}

.entry-card-placeholder-media {
  background:
    radial-gradient(circle at top left, rgba(15, 118, 110, 0.18), transparent 38%),
    linear-gradient(180deg, #ecfeff 0%, #cffafe 100%);
}

.entry-card-badges {
  position: absolute;
  top: 12px;
  right: 12px;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.entry-card-kind {
  padding: 4px 8px;
  border-radius: 999px;
  background: rgba(15, 23, 42, 0.72);
  color: #ffffff;
  font-size: 12px;
}

.entry-card-body {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.entry-card-name {
  font-size: 16px;
  font-weight: 600;
  color: #0f172a;
  word-break: break-word;
}

.entry-card-type {
  font-size: 13px;
  color: #0f766e;
}

.entry-card-meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 12px;
  color: #64748b;
}

.entry-card-actions {
  margin-top: auto;
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 24px;
}

.waterfall-shell {
  column-width: 280px;
  column-gap: 18px;
}

.waterfall-item {
  display: inline-block;
  width: 100%;
  margin: 0 0 18px;
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 22px;
  background: rgba(255, 255, 255, 0.97);
  box-shadow: 0 14px 34px rgba(15, 23, 42, 0.08);
  overflow: hidden;
  cursor: pointer;
  break-inside: avoid;
  transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease;
}

.waterfall-item:hover,
.waterfall-item:focus-visible {
  transform: translateY(-2px);
  box-shadow: 0 20px 40px rgba(15, 23, 42, 0.12);
  border-color: rgba(37, 99, 235, 0.28);
}

.waterfall-frame {
  position: relative;
  background:
    radial-gradient(circle at top left, rgba(37, 99, 235, 0.16), transparent 36%),
    linear-gradient(180deg, #eff6ff 0%, #dbeafe 100%);
}

.waterfall-media {
  display: block;
  width: 100%;
  height: auto;
  max-height: 520px;
  object-fit: cover;
}

.waterfall-body {
  padding: 12px 14px 14px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.waterfall-empty {
  min-height: 160px;
  border-radius: 22px;
  border: 1px dashed rgba(148, 163, 184, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #64748b;
  background: rgba(248, 250, 252, 0.7);
}

.media-empty-inline {
  min-height: 120px;
  border-radius: 18px;
  border: 1px dashed rgba(148, 163, 184, 0.35);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #64748b;
  background: rgba(248, 250, 252, 0.65);
}

.preview-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  width: 100%;
}

.preview-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.preview-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  gap: 20px;
  min-height: 68vh;
}

.preview-stage,
.meta-card {
  border-radius: 24px;
  border: 1px solid rgba(148, 163, 184, 0.18);
  background: rgba(248, 250, 252, 0.86);
}

.preview-stage {
  min-height: 68vh;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  background:
    radial-gradient(circle at top left, rgba(37, 99, 235, 0.12), transparent 28%),
    linear-gradient(180deg, #f8fafc 0%, #e2e8f0 100%);
}

.preview-image,
.preview-video {
  width: 100%;
  max-height: 68vh;
  object-fit: contain;
}

.preview-pdf {
  width: 100%;
  height: 68vh;
  border: 0;
  border-radius: 14px;
  background: #ffffff;
}

.preview-placeholder {
  color: #475569;
  font-size: 15px;
}

.preview-sidebar {
  min-width: 0;
}

.meta-card {
  height: 100%;
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.meta-row {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.meta-actions {
  margin-top: 4px;
  padding-top: 12px;
  border-top: 1px solid rgba(148, 163, 184, 0.18);
}

.preview-reveal-button {
  width: 100%;
}

.meta-label {
  font-size: 12px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #64748b;
}

.meta-value {
  color: #0f172a;
  word-break: break-word;
}

.meta-value-break {
  font-size: 13px;
  color: #334155;
}

@media (max-width: 780px) {
  .device-file-page {
    padding: 16px;
  }

  .hero-panel,
  .empty-panel,
  .browser-panel {
    border-radius: 20px;
  }

  .hero-panel {
    padding: 18px;
    grid-template-columns: 1fr;
  }

  .hero-copy h1,
  .browser-toolbar h2 {
    font-size: 28px;
  }

  .browser-panel {
    min-height: 0;
  }

  .hero-select,
  .hero-limit-input {
    width: 100%;
  }

  .hero-actions {
    width: 100%;
    padding: 14px;
  }

  .hero-field-row {
    grid-template-columns: 1fr;
    gap: 8px;
  }

  .directory-config-row {
    grid-template-columns: 1fr;
  }

  .media-pagination-bar,
  .directory-toolbar {
    flex-direction: column;
    align-items: stretch;
  }

  .directory-path-input {
    min-width: 0;
  }

  .directory-action-button,
  .directory-section-count {
    margin-left: 0;
  }

  .media-top-toolbar,
  .media-top-toolbar-left {
    align-items: stretch;
  }

  .media-top-toolbar-left,
  .media-toolbar-group {
    flex-direction: column;
  }

  .media-toolbar-group-scale,
  .media-toolbar-slider {
    min-width: 0;
    width: 100%;
  }

  .hero-actions :deep(.el-button),
  .hero-actions :deep(.el-select),
  .hero-actions :deep(.el-input-number),
  .toolbar-actions,
  .view-mode-switch {
    flex: 1 1 100%;
  }

  .icon-shell {
    grid-template-columns: 1fr;
  }

  .waterfall-directory-grid {
    grid-template-columns: 1fr;
  }

  .waterfall-shell {
    column-width: auto;
    columns: 1;
  }

  .preview-layout {
    grid-template-columns: 1fr;
    min-height: auto;
  }

  .preview-stage {
    min-height: 44vh;
  }

  .preview-image,
  .preview-video,
  .preview-pdf {
    max-height: 44vh;
  }

  .preview-pdf {
    height: 44vh;
  }
}

@media (min-width: 781px) and (max-width: 1180px) {
  .directory-config-row {
    grid-template-columns: minmax(0, 1fr) 220px;
  }

  .hero-panel {
    grid-template-columns: 1fr;
  }

  .hero-copy {
    padding-top: 0;
  }

  .hero-actions {
    width: 100%;
  }
}
</style>
