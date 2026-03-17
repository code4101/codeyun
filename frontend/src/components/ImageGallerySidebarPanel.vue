<template>
  <div class="sidebar-content">
    <div v-if="sourceLabel || sourceTag" class="sidebar-header">
      <div>
        <div class="section-label">当前来源</div>
        <h2>{{ sourceLabel }}</h2>
      </div>
      <el-tag v-if="sourceTag" :type="sourceTagType" effect="plain">{{ sourceTag }}</el-tag>
    </div>

    <div class="stats-grid">
      <div v-if="showVisibleCountStat" class="stat-card">
        <span class="stat-label">当前可见 / 已加载</span>
        <strong>{{ visibleCount }} / {{ loadedCount }}</strong>
        <small>{{ itemCountLabel }}</small>
      </div>
      <div class="stat-card" :class="{ 'stat-card-wide': !showVisibleCountStat }">
        <span class="stat-label">当页体积 / 总体积</span>
        <strong class="stat-value-compact">{{ formatFileSize(totalBytes) }} / {{ formatFileSize(resolvedSummaryTotalBytes) }}</strong>
      </div>
    </div>

    <div v-if="$slots.extra" class="control-group">
      <slot name="extra" />
    </div>

    <div class="control-group">
      <div class="section-label">搜索</div>
      <el-input
        v-model="keywordModel"
        clearable
        placeholder="按文件名或相对路径筛选"
      />
    </div>

    <div v-if="showSortProgram" class="control-group">
      <GallerySortProgramBar
        v-model="sortProgramModel"
        :caption="sortSummaryLabel"
        help-text="像星图笔记一样按规则链配置。"
      />
    </div>

    <div v-if="showThumbnailScaleControl" class="control-group">
      <div class="section-row">
        <span class="section-label">缩放比例</span>
        <span class="section-value">{{ thumbnailScale }}%</span>
      </div>
      <el-slider v-model="thumbnailScaleModel" :min="10" :max="100" :step="5" />
    </div>

    <div v-if="showViewModeControl" class="control-group">
      <div class="section-row">
        <span class="section-label">浏览模式</span>
      </div>
      <el-button-group class="view-mode-switch">
        <el-button :type="viewMode === 'masonry' ? 'primary' : 'default'" @click="viewModeModel = 'masonry'">
          瀑布流
        </el-button>
        <el-button :type="viewMode === 'grid' ? 'primary' : 'default'" @click="viewModeModel = 'grid'">
          卡片
        </el-button>
      </el-button-group>
    </div>

    <div v-if="showFolderFilter" class="control-group folders-group">
      <div class="section-row">
        <span class="section-label">文件夹</span>
        <span class="section-value">{{ currentFolderLabel }}</span>
      </div>

      <div class="folder-list">
        <button
          type="button"
          class="folder-item"
          :class="{ 'is-active': folderFilter === allFoldersKey }"
          @click="folderFilterModel = allFoldersKey"
        >
          <span class="folder-text">
            <span class="folder-name">{{ allItemsLabel }}</span>
            <span class="folder-path">显示整个目录树</span>
          </span>
          <span class="folder-count">{{ imagesLength }}</span>
        </button>

        <button
          v-for="folder in folderOptions"
          :key="folder.key"
          type="button"
          class="folder-item"
          :class="{ 'is-active': folderFilter === folder.key }"
          @click="folderFilterModel = folder.key"
        >
          <span
            class="folder-text"
            :style="{ paddingLeft: `${folder.depth * 16}px` }"
          >
            <span class="folder-name">{{ folder.label }}</span>
            <span class="folder-path">{{ folder.fullPath }}</span>
          </span>
          <span class="folder-count">{{ folder.count }}</span>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import GallerySortProgramBar from '@/components/GallerySortProgramBar.vue';
import { formatFileSize, type FolderOption, type GallerySortProgram } from '@/utils/imageGallery';

const props = withDefaults(
  defineProps<{
    sourceLabel?: string;
    sourceTag?: string;
    sourceTagType?: '' | 'primary' | 'success' | 'warning' | 'info' | 'danger';
    showVisibleCountStat?: boolean;
    visibleCount: number;
    loadedCount: number;
    itemCountLabel?: string;
    totalBytes: number;
    summaryTotalBytes?: number | null;
    keyword: string;
    showSortProgram?: boolean;
    sortProgram: GallerySortProgram;
    sortSummaryLabel?: string;
    showThumbnailScaleControl?: boolean;
    thumbnailScale: number;
    showViewModeControl?: boolean;
    viewMode: 'grid' | 'masonry';
    viewModeLabel: string;
    showFolderFilter?: boolean;
    currentFolderLabel?: string;
    allItemsLabel?: string;
    imagesLength: number;
    folderFilter: string;
    allFoldersKey: string;
    folderOptions: FolderOption[];
  }>(),
  {
    sourceLabel: '',
    sourceTag: '',
    sourceTagType: 'primary',
    showVisibleCountStat: true,
    itemCountLabel: '张图片',
    summaryTotalBytes: null,
    showSortProgram: true,
    sortSummaryLabel: '',
    showThumbnailScaleControl: true,
    showViewModeControl: true,
    showFolderFilter: true,
    currentFolderLabel: '',
    allItemsLabel: '全部图片',
  }
);

const emit = defineEmits<{
  (event: 'update:keyword', value: string): void;
  (event: 'update:sortProgram', value: GallerySortProgram): void;
  (event: 'update:thumbnailScale', value: number): void;
  (event: 'update:viewMode', value: 'grid' | 'masonry'): void;
  (event: 'update:folderFilter', value: string): void;
}>();

const resolvedSummaryTotalBytes = computed(() =>
  typeof props.summaryTotalBytes === 'number' && Number.isFinite(props.summaryTotalBytes) && props.summaryTotalBytes >= 0
    ? props.summaryTotalBytes
    : props.totalBytes
);

const keywordModel = computed({
  get: () => props.keyword,
  set: (value: string) => emit('update:keyword', value),
});

const sortProgramModel = computed({
  get: () => props.sortProgram,
  set: (value: GallerySortProgram) => emit('update:sortProgram', value),
});

const thumbnailScaleModel = computed({
  get: () => props.thumbnailScale,
  set: (value: number) => emit('update:thumbnailScale', value),
});

const viewModeModel = computed({
  get: () => props.viewMode,
  set: (value: 'grid' | 'masonry') => emit('update:viewMode', value),
});

const folderFilterModel = computed({
  get: () => props.folderFilter,
  set: (value: string) => emit('update:folderFilter', value),
});
</script>

<style scoped>
.sidebar-content {
  display: flex;
  flex-direction: column;
  gap: 18px;
  min-height: 0;
}

.sidebar-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.section-label {
  font-size: 12px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: #64748b;
}

.sidebar-header h2 {
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

.stat-card strong.stat-value-compact {
  font-size: 18px;
  line-height: 1.08;
  letter-spacing: -0.02em;
  white-space: nowrap;
}

.stat-label,
.section-value {
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

.folder-name {
  font-weight: 600;
  color: inherit;
}

.folder-path {
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.folder-count {
  font-size: 12px;
  color: #94a3b8;
}

@media (max-width: 780px) {
  .stats-grid {
    grid-template-columns: 1fr;
  }

  .stat-card-wide {
    grid-column: auto;
  }
}
</style>
