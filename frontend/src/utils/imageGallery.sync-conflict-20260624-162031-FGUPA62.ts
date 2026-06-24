import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch, type Ref } from 'vue';

export type GalleryItemKind = 'image' | 'video';
export type GalleryUrlVariant = 'thumbnail' | 'full';

export interface GalleryItem {
  id: string;
  name: string;
  relativePath: string;
  folderPath: string;
  folderDisplayPath?: string;
  size: number;
  createdAt?: number | null;
  modifiedAt: number;
  url: string | null;
  width: number | null;
  height: number | null;
  kind?: GalleryItemKind;
  mimeType?: string | null;
  duration?: number | null;
  weight?: number | null;
  urlVariant?: GalleryUrlVariant | null;
  thumbnailFailed?: boolean;
  thumbnailVersion?: string | number | null;
  lastAccessedAt?: number | null;
  urlNeedsRevoke?: boolean;
}

export type GalleryImage = GalleryItem;

export interface FolderOption {
  key: string;
  label: string;
  fullPath: string;
  depth: number;
  count: number;
}

type LegacyGallerySortMode = 'path' | 'modified-desc' | 'size-desc';
export type GallerySortField =
  | 'random'
  | 'weight'
  | 'modified_at'
  | 'size'
  | 'duration'
  | 'relative_path'
  | 'name'
  | 'folder_path'
  | 'kind'
  | 'width'
  | 'height'
  | 'resolution_area';
export type GallerySortDirection = 'asc' | 'desc';
export type GallerySortNulls = 'first' | 'last';

export interface GallerySortRule {
  field: GallerySortField;
  direction: GallerySortDirection;
  nulls: GallerySortNulls;
}

export interface GallerySortProgram {
  rules: GallerySortRule[];
}

export type GalleryViewMode = 'masonry' | 'grid';

export const ALL_FOLDERS = '__ALL__';
export const ROOT_FOLDER = '__ROOT__';
const DEFAULT_THUMBNAIL_REFERENCE_WIDTH = 360;
const GALLERY_SORT_FALLBACK_RULES: GallerySortRule[] = [
  { field: 'relative_path', direction: 'asc', nulls: 'last' },
  { field: 'name', direction: 'asc', nulls: 'last' },
];

const collator = new Intl.Collator('zh-CN', { numeric: true, sensitivity: 'base' });
const dateFormatter = new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' });
const isFiniteNumber = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);

export const createGallerySortRule = (
  field: GallerySortField = 'weight',
  direction: GallerySortDirection = 'desc',
  nulls: GallerySortNulls = 'last'
): GallerySortRule => ({
  field,
  direction,
  nulls,
});

export const normalizeGallerySortRule = (value?: Partial<GallerySortRule> | null): GallerySortRule => ({
  field: isGallerySortField(value?.field) ? value.field : 'weight',
  direction: value?.direction === 'asc' ? 'asc' : 'desc',
  nulls: 'last',
});

export const createDefaultGallerySortProgram = (): GallerySortProgram => ({
  rules: [
    createGallerySortRule('weight', 'desc', 'last'),
    createGallerySortRule('modified_at', 'desc', 'last'),
  ],
});

export const normalizeGallerySortProgram = (value?: Partial<GallerySortProgram> | null): GallerySortProgram => ({
  rules: Array.isArray(value?.rules)
    ? value.rules.map((rule) => normalizeGallerySortRule(rule))
    : createDefaultGallerySortProgram().rules,
});

export const cloneGallerySortProgram = (value?: Partial<GallerySortProgram> | null): GallerySortProgram =>
  JSON.parse(JSON.stringify(normalizeGallerySortProgram(value)));

export const getGallerySortFieldLabel = (field: GallerySortField) => {
  switch (field) {
    case 'random':
      return '随机';
    case 'weight':
      return '权重';
    case 'modified_at':
      return '修改时间';
    case 'size':
      return '文件大小';
    case 'duration':
      return '时长';
    case 'relative_path':
      return '相对路径';
    case 'name':
      return '文件名';
    case 'folder_path':
      return '文件夹';
    case 'kind':
      return '媒体类型';
    case 'width':
      return '宽度';
    case 'height':
      return '高度';
    case 'resolution_area':
      return '分辨率面积';
    default:
      return '未知字段';
  }
};

export const formatGallerySortSummary = (program?: Partial<GallerySortProgram> | null) => {
  const normalizedProgram = normalizeGallerySortProgram(program);
  if (!normalizedProgram.rules.length) {
    return '默认稳定顺序';
  }
  return normalizedProgram.rules
    .map((rule) => `${getGallerySortFieldLabel(rule.field)}${rule.direction === 'desc' ? '降序' : '升序'}`)
    .join(' -> ');
};

const isGallerySortField = (value: unknown): value is GallerySortField =>
  value === 'random'
  || value === 'weight'
  || value === 'modified_at'
  || value === 'size'
  || value === 'duration'
  || value === 'relative_path'
  || value === 'name'
  || value === 'folder_path'
  || value === 'kind'
  || value === 'width'
  || value === 'height'
  || value === 'resolution_area';

const getStablePseudoRandomValue = (value: string) => {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
};

const getGallerySortValue = (image: GalleryImage, field: GallerySortField): string | number | null => {
  switch (field) {
    case 'random':
      return getStablePseudoRandomValue(`${image.id}|${image.relativePath}|${image.name}`);
    case 'weight':
      return isFiniteNumber(image.weight) ? image.weight : 0;
    case 'modified_at':
      return image.modifiedAt;
    case 'size':
      return image.size;
    case 'duration':
      return isFiniteNumber(image.duration) ? image.duration : null;
    case 'relative_path':
      return image.relativePath || '';
    case 'name':
      return image.name || '';
    case 'folder_path':
      return image.folderPath || '';
    case 'kind':
      return image.kind === 'video' ? 'video' : 'image';
    case 'width':
      return isFiniteNumber(image.width) ? image.width : null;
    case 'height':
      return isFiniteNumber(image.height) ? image.height : null;
    case 'resolution_area':
      return isFiniteNumber(image.width) && isFiniteNumber(image.height) ? image.width * image.height : null;
    default:
      return null;
  }
};

const compareGallerySortRule = (left: GalleryImage, right: GalleryImage, rule: GallerySortRule) => {
  const leftValue = getGallerySortValue(left, rule.field);
  const rightValue = getGallerySortValue(right, rule.field);
  const leftMissing = leftValue === null || leftValue === undefined || (typeof leftValue === 'number' && !Number.isFinite(leftValue));
  const rightMissing = rightValue === null || rightValue === undefined || (typeof rightValue === 'number' && !Number.isFinite(rightValue));

  if (leftMissing || rightMissing) {
    if (leftMissing && rightMissing) {
      return 0;
    }
    return rule.nulls === 'first'
      ? (leftMissing ? -1 : 1)
      : (leftMissing ? 1 : -1);
  }

  let result = 0;
  if (typeof leftValue === 'number' && typeof rightValue === 'number') {
    result = leftValue - rightValue;
  } else {
    result = collator.compare(String(leftValue), String(rightValue));
  }

  if (result === 0) {
    return 0;
  }
  return rule.direction === 'desc' ? -result : result;
};

const migrateLegacySortMode = (value: string | null): GallerySortProgram => {
  const normalizedValue = value as LegacyGallerySortMode | null;
  if (normalizedValue === 'modified-desc') {
    return { rules: [createGallerySortRule('modified_at', 'desc', 'last')] };
  }
  if (normalizedValue === 'size-desc') {
    return { rules: [createGallerySortRule('size', 'desc', 'last')] };
  }
  if (normalizedValue === 'path') {
    return { rules: [createGallerySortRule('relative_path', 'asc', 'last')] };
  }
  return createDefaultGallerySortProgram();
};

export const sortGalleryImages = (images: GalleryImage[], program?: Partial<GallerySortProgram> | null) => {
  const normalizedProgram = normalizeGallerySortProgram(program);
  const effectiveRules = [...normalizedProgram.rules, ...GALLERY_SORT_FALLBACK_RULES];
  return [...images].sort((left, right) => {
    for (const rule of effectiveRules) {
      const result = compareGallerySortRule(left, right, rule);
      if (result !== 0) {
        return result;
      }
    }
    return collator.compare(left.id, right.id);
  });
};

export const useImageGalleryState = (
  images: Ref<GalleryImage[]>,
  options: {
    storageKeyPrefix?: string;
    showSidebar: Ref<boolean>;
    allItemsLabel?: string;
    enableFolderFilter?: Ref<boolean>;
    preserveOrder?: Ref<boolean>;
  }
) => {
  const storageKeyPrefix = options.storageKeyPrefix ?? 'image_gallery';
  const allItemsLabel = options.allItemsLabel ?? '全部图片';
  const enableFolderFilter = options.enableFolderFilter;
  const preserveOrder = options.preserveOrder;
  const galleryScrollRef = ref<HTMLElement | null>(null);
  const keyword = ref('');
  const folderFilter = ref(ALL_FOLDERS);
  const sortProgram = ref<GallerySortProgram>(createDefaultGallerySortProgram());
  const thumbnailScale = ref(30);
  const viewMode = ref<GalleryViewMode>('masonry');
  const previewVisible = ref(false);
  const previewImageId = ref<string | null>(null);
  const galleryWidth = ref(0);
  let galleryResizeObserver: ResizeObserver | null = null;

  const normalizedKeyword = computed(() => keyword.value.trim().toLowerCase());

  const totalBytes = computed(() => images.value.reduce((sum, image) => sum + image.size, 0));
  const sortSummaryLabel = computed(() => formatGallerySortSummary(sortProgram.value));

  const thumbnailWidth = computed(() => {
    const scaledWidth = Math.max(
      48,
      Math.round((DEFAULT_THUMBNAIL_REFERENCE_WIDTH * thumbnailScale.value) / 100)
    );
    if (!galleryWidth.value) return scaledWidth;
    return Math.min(scaledWidth, galleryWidth.value);
  });

  const viewModeLabel = computed(() => (viewMode.value === 'masonry' ? '瀑布流' : '卡片'));

  const currentFolderLabel = computed(() => {
    if (enableFolderFilter && !enableFolderFilter.value) return allItemsLabel;
    if (folderFilter.value === ALL_FOLDERS) return allItemsLabel;
    if (folderFilter.value === ROOT_FOLDER) return '根目录';
    return folderFilter.value;
  });

  const folderOptions = computed<FolderOption[]>(() => {
    if (enableFolderFilter && !enableFolderFilter.value) {
      return [];
    }

    const subtreeCounts = new Map<string, number>();
    let rootCount = 0;

    for (const image of images.value) {
      if (!image.folderPath) {
        rootCount += 1;
        continue;
      }

      const parts = image.folderPath.split('/');
      for (let index = 1; index <= parts.length; index += 1) {
        const currentPath = parts.slice(0, index).join('/');
        subtreeCounts.set(currentPath, (subtreeCounts.get(currentPath) ?? 0) + 1);
      }
    }

    const folders = Array.from(subtreeCounts.entries())
      .sort(([left], [right]) => collator.compare(left, right))
      .map(([path, count]) => ({
        key: path,
        label: path.split('/').pop() ?? path,
        fullPath: path,
        depth: path.split('/').length - 1,
        count,
      }));

    if (rootCount > 0) {
      folders.unshift({
        key: ROOT_FOLDER,
        label: '根目录',
        fullPath: '根目录',
        depth: 0,
        count: rootCount,
      });
    }

    return folders;
  });

  const visibleImages = computed(() => {
    const result = images.value.filter((image) => {
      const matchesFolder =
        (enableFolderFilter && !enableFolderFilter.value)
        || folderFilter.value === ALL_FOLDERS
        || isImageInFolder(image.folderPath, folderFilter.value);
      const matchesKeyword =
        !normalizedKeyword.value ||
        `${image.name} ${image.relativePath}`.toLowerCase().includes(normalizedKeyword.value);
      return matchesFolder && matchesKeyword;
    });

    if (preserveOrder?.value) {
      return result;
    }

    return sortGalleryImages(result, sortProgram.value);
  });

  const filteredBytes = computed(() => visibleImages.value.reduce((sum, image) => sum + image.size, 0));

  const previewIndex = computed(() => {
    if (!previewImageId.value) return -1;
    return visibleImages.value.findIndex((image) => image.id === previewImageId.value);
  });

  const previewImage = computed(() => {
    if (previewIndex.value < 0) return null;
    return visibleImages.value[previewIndex.value] ?? null;
  });

  const hasPreviousImage = computed(() => previewIndex.value > 0);
  const hasNextImage = computed(
    () => previewIndex.value >= 0 && previewIndex.value < visibleImages.value.length - 1
  );

  const previewPositionText = computed(() => {
    if (previewIndex.value < 0) return '-- / --';
    return `${previewIndex.value + 1} / ${visibleImages.value.length}`;
  });

  const setPreviewImage = (imageId: string | null) => {
    previewImageId.value = imageId;
    previewVisible.value = imageId !== null;
  };

  const updateMediaMetadata = (
    image: GalleryImage,
    width: number | null,
    height: number | null,
    duration: number | null = image.duration ?? null
  ) => {
    if (image.width === width && image.height === height && image.duration === duration) {
      return;
    }

    image.width = width;
    image.height = height;
    image.duration = duration;
  };

  const handleImageLoad = (imageId: string, event: Event) => {
    const target = event.target;
    if (!(target instanceof HTMLImageElement)) return;

    const image = images.value.find((item) => item.id === imageId);
    if (!image) return;

    const width = target.naturalWidth || null;
    const height = target.naturalHeight || null;
    updateMediaMetadata(image, width, height, image.duration ?? null);
  };

  const handleVideoMetadata = (imageId: string, event: Event) => {
    const target = event.target;
    if (!(target instanceof HTMLVideoElement)) return;

    const image = images.value.find((item) => item.id === imageId);
    if (!image) return;

    const width = target.videoWidth || null;
    const height = target.videoHeight || null;
    const duration = Number.isFinite(target.duration) && target.duration >= 0 ? target.duration : null;
    updateMediaMetadata(image, width, height, duration);
  };

  const attachGalleryObserver = () => {
    galleryResizeObserver?.disconnect();
    galleryResizeObserver = null;

    if (!galleryScrollRef.value) return;

    if (typeof ResizeObserver !== 'undefined') {
      galleryResizeObserver = new ResizeObserver((entries) => {
        const width = entries[0]?.contentRect.width;
        if (width) {
          galleryWidth.value = Math.floor(width);
        }
      });
      galleryResizeObserver.observe(galleryScrollRef.value);
    }

    galleryWidth.value = Math.floor(galleryScrollRef.value.clientWidth);
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
    if (!previewVisible.value) return;
    if (isInteractiveTarget(event.target)) return;

    if (event.key === 'ArrowLeft' && hasPreviousImage.value) {
      event.preventDefault();
      previewImageId.value = visibleImages.value[previewIndex.value - 1].id;
    }

    if (event.key === 'ArrowRight' && hasNextImage.value) {
      event.preventDefault();
      previewImageId.value = visibleImages.value[previewIndex.value + 1].id;
    }
  };

  watch(visibleImages, (nextImages) => {
    if (!previewVisible.value || !previewImageId.value) return;
    if (!nextImages.some((image) => image.id === previewImageId.value)) {
      previewVisible.value = false;
      previewImageId.value = null;
    }
  });

  watch(previewVisible, (visible) => {
    if (!visible) {
      previewImageId.value = null;
    }
  });

  watch(thumbnailScale, (value) => {
    localStorage.setItem(`${storageKeyPrefix}_thumbnail_scale`, String(value));
  });

  watch(options.showSidebar, (value) => {
    localStorage.setItem(`${storageKeyPrefix}_show_sidebar`, value ? '1' : '0');
  });

  watch(
    sortProgram,
    (value) => {
      localStorage.setItem(`${storageKeyPrefix}_sort_program`, JSON.stringify(normalizeGallerySortProgram(value)));
    },
    { deep: true }
  );

  watch(viewMode, (value) => {
    localStorage.setItem(`${storageKeyPrefix}_view_mode`, value);
  });

  watch(
    () => images.value.length,
    async (count) => {
      if (!count) {
        galleryResizeObserver?.disconnect();
        galleryResizeObserver = null;
        galleryWidth.value = 0;
        return;
      }

      await nextTick();
      attachGalleryObserver();
    }
  );

  watch(galleryScrollRef, () => {
    attachGalleryObserver();
  });

  if (enableFolderFilter) {
    watch(
      enableFolderFilter,
      (enabled) => {
        if (!enabled && folderFilter.value !== ALL_FOLDERS) {
          folderFilter.value = ALL_FOLDERS;
        }
      },
      { immediate: true }
    );
  }

  onMounted(() => {
    try {
      const savedShowSidebar = localStorage.getItem(`${storageKeyPrefix}_show_sidebar`);
      const savedThumbnailScale = localStorage.getItem(`${storageKeyPrefix}_thumbnail_scale`);
      const savedSortProgram = localStorage.getItem(`${storageKeyPrefix}_sort_program`);
      const savedLegacySortMode = localStorage.getItem(`${storageKeyPrefix}_sort_mode`);
      const savedViewMode = localStorage.getItem(`${storageKeyPrefix}_view_mode`);

      if (savedShowSidebar !== null) {
        options.showSidebar.value = savedShowSidebar === '1';
      }

      if (savedThumbnailScale !== null) {
        const parsedScale = Number(savedThumbnailScale);
        if (!Number.isNaN(parsedScale) && parsedScale >= 10 && parsedScale <= 100) {
          thumbnailScale.value = parsedScale;
        }
      }

      if (savedSortProgram) {
        sortProgram.value = normalizeGallerySortProgram(JSON.parse(savedSortProgram));
      } else {
        sortProgram.value = migrateLegacySortMode(savedLegacySortMode);
      }

      if (savedViewMode === 'masonry' || savedViewMode === 'grid') {
        viewMode.value = savedViewMode;
      }
    } catch (error) {
      console.warn('Failed to restore image gallery preferences', error);
    }

    window.addEventListener('keydown', handleKeydown);
  });

  onBeforeUnmount(() => {
    window.removeEventListener('keydown', handleKeydown);
    galleryResizeObserver?.disconnect();
  });

  return {
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
    filteredBytes,
    setPreviewImage,
    handleImageLoad,
    handleVideoMetadata,
  };
};

export const isImageInFolder = (imageFolderPath: string, selectedFolderPath: string) => {
  if (selectedFolderPath === ROOT_FOLDER) return imageFolderPath === '';
  return imageFolderPath === selectedFolderPath || imageFolderPath.startsWith(`${selectedFolderPath}/`);
};

export const getGalleryItemKind = (image: GalleryImage): GalleryItemKind =>
  image.kind === 'video' ? 'video' : 'image';

export const isVideoGalleryItem = (image: GalleryImage) => getGalleryItemKind(image) === 'video';

export const getFolderPath = (relativePath: string) => {
  const lastSlashIndex = relativePath.lastIndexOf('/');
  if (lastSlashIndex < 0) return '';
  return relativePath.slice(0, lastSlashIndex);
};

export const formatFolderLabel = (folderPath: string) => (folderPath ? folderPath : '根目录');

export const formatResolution = (image: GalleryImage) => {
  if (!image.width || !image.height) return '读取中';
  return `${image.height} x ${image.width}`;
};

export const formatDuration = (duration: number | null | undefined) => {
  if (typeof duration !== 'number' || !Number.isFinite(duration) || duration < 0) {
    return '读取中';
  }

  const totalSeconds = Math.round(duration);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;

  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
  }

  return `${minutes}:${String(seconds).padStart(2, '0')}`;
};

export const formatFileSize = (bytes: number) => {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
};

export const formatDate = (timestamp: number) => dateFormatter.format(new Date(timestamp));
