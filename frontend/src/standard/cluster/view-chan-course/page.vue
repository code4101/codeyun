<template>
  <ClusterFileBrowserPage
    ref="browserRef"
    fixed-device-id="codepc_mf"
    fixed-root-path="D:\home\chenkunze\data\m2311禅课合辑"
  >
    <template #toolbar-after="slotProps">
      <div class="chan-random-actions">
        <el-button
          type="primary"
          size="small"
          :loading="isOpeningRandom"
          :disabled="!slotProps.canBrowse"
          @click="openRandomClip(slotProps)"
        >
          随机新片段
        </el-button>
        <el-button
          size="small"
          :disabled="!currentClip"
          title="重新打开刚才随机到的这个片段，不重新随机"
          @click="openCurrentClip"
        >
          再看此段
        </el-button>
        <span v-if="currentClip" class="chan-random-current" :title="currentClip.name">
          {{ currentClip.name }} · {{ formatClipRange(currentClip.start, currentClip.end) }}
        </span>
      </div>
      <slot name="toolbar-after" v-bind="slotProps" />
    </template>

    <template #directory-after="slotProps">
      <slot name="directory-after" v-bind="slotProps" />
    </template>

    <template #page-after="slotProps">
      <slot name="page-after" v-bind="slotProps" />
    </template>
  </ClusterFileBrowserPage>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import { ElMessage } from 'element-plus';
import { fetchDeviceMedia, type DeviceImageRecord } from '@/api/deviceFiles';
import ClusterFileBrowserPage from '@/standard/cluster/files/page.vue';
import type { GallerySortProgram } from '@/utils/imageGallery';

const CLIP_DURATION_SECONDS = 5 * 60;
const CLIP_OVERLAP_SECONDS = 5;
const RANDOM_CANDIDATE_LIMIT = 200;
const DOWNLOADING_STABLE_AGE_MS = 10 * 60 * 1000;
const RECENT_RANDOM_VIDEO_MEMORY_LIMIT = 80;
const RECENT_RANDOM_VIDEO_EXCLUDE_LIMIT = 20;
const RANDOM_PLAYABLE_ATTEMPT_LIMIT = 16;
const VIDEO_PROBE_TIMEOUT_MS = 8_000;
const DOWNLOADING_EXTENSION_SUFFIXES = [
  '.crdownload',
  '.download',
  '.part',
  '.tmp',
  '.aria2',
  '.!qb',
  '.!ut',
  '.td',
  '.downloading',
];

interface BrowserExpose {
  openMediaClip: (imageId: string, startSeconds: number, endSeconds: number) => Promise<void>;
  openMediaRecordClip: (record: DeviceImageRecord, startSeconds: number, endSeconds: number) => Promise<void>;
  ensureMediaPlayableUrl: (imageId: string) => Promise<string>;
  ensureMediaRecordPlayableUrl: (record: DeviceImageRecord) => Promise<string>;
}

interface MediaItem {
  id: string;
  name: string;
  kind?: string;
  duration?: number | null;
  size?: number | null;
  modifiedAt?: number | null;
  sourceRecord?: DeviceImageRecord;
}

interface VideoSegment {
  item: MediaItem;
  start: number;
  end: number;
}

interface VideoSegmentGroup {
  item: MediaItem;
  segments: VideoSegment[];
}

interface CurrentClip {
  id: string;
  name: string;
  start: number;
  end: number;
  sourceRecord?: DeviceImageRecord;
}

interface ToolbarSlotProps {
  selectedEntryId?: string;
  selectedPath?: string;
  mediaItems?: MediaItem[];
  canBrowse?: boolean;
  recursiveDisplay?: boolean;
  mediaScanLimit?: number;
}

const browserRef = ref<BrowserExpose | null>(null);
const currentClip = ref<CurrentClip | null>(null);
const isOpeningRandom = ref(false);
const recentRandomVideoIds = ref<string[]>([]);

const getMediaItemSize = (item: MediaItem) => {
  const value = item.size ?? item.sourceRecord?.size;
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
};

const getMediaItemModifiedAt = (item: MediaItem) => {
  const value = item.modifiedAt ?? item.sourceRecord?.modified_at;
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
};

const hasDownloadingSuffix = (name: string) => {
  const lowerName = name.toLowerCase();
  return DOWNLOADING_EXTENSION_SUFFIXES.some((suffix) => lowerName.endsWith(suffix));
};

const isProbablyDownloadingItem = (item: MediaItem) => {
  if (hasDownloadingSuffix(item.name)) {
    return true;
  }
  const size = getMediaItemSize(item);
  if (size !== null && size <= 0) {
    return true;
  }
  const modifiedAt = getMediaItemModifiedAt(item);
  if (modifiedAt === null) {
    return false;
  }
  return Date.now() - modifiedAt < DOWNLOADING_STABLE_AGE_MS;
};

const isVideoItem = (item: MediaItem) =>
  item.kind === 'video'
  && typeof item.duration === 'number'
  && Number.isFinite(item.duration)
  && item.duration > 0
  && !isProbablyDownloadingItem(item);

const buildVideoSegments = (item: MediaItem): VideoSegment[] => {
  if (!isVideoItem(item)) {
    return [];
  }

  const duration = Math.max(0, item.duration || 0);
  const segments: VideoSegment[] = [];
  let start = 0;
  while (start < duration) {
    const end = Math.min(duration, start + CLIP_DURATION_SECONDS);
    if (end > start) {
      segments.push({ item, start, end });
    }
    if (end >= duration) {
      break;
    }
    start = Math.max(0, end - CLIP_OVERLAP_SECONDS);
  }
  return segments;
};

const getVideoSegments = (items: MediaItem[] | undefined) =>
  (Array.isArray(items) ? items : []).flatMap(buildVideoSegments);

const getVideoSegmentGroups = (items: MediaItem[] | undefined): VideoSegmentGroup[] =>
  (Array.isArray(items) ? items : [])
    .map((item) => ({ item, segments: buildVideoSegments(item) }))
    .filter((group) => group.segments.length > 0);

const shuffleArray = <T,>(items: T[]) => {
  const shuffled = [...items];
  for (let index = shuffled.length - 1; index > 0; index -= 1) {
    const swapIndex = Math.floor(Math.random() * (index + 1));
    [shuffled[index], shuffled[swapIndex]] = [shuffled[swapIndex], shuffled[index]];
  }
  return shuffled;
};

const buildRandomSegmentAttempts = (groups: VideoSegmentGroup[]) => {
  if (!groups.length) {
    return [];
  }
  const excludeLimit = Math.min(RECENT_RANDOM_VIDEO_EXCLUDE_LIMIT, Math.max(0, groups.length - 1));
  const recentIds = new Set(recentRandomVideoIds.value.slice(-excludeLimit));
  const freshGroups = groups.filter((group) => !recentIds.has(group.item.id));
  const fallbackGroups = groups.filter((group) => recentIds.has(group.item.id));
  return [...shuffleArray(freshGroups), ...shuffleArray(fallbackGroups)]
    .slice(0, RANDOM_PLAYABLE_ATTEMPT_LIMIT)
    .map((group) => group.segments[Math.floor(Math.random() * group.segments.length)]);
};

const rememberRandomVideo = (segment: VideoSegment) => {
  recentRandomVideoIds.value = [
    ...recentRandomVideoIds.value.filter((id) => id !== segment.item.id),
    segment.item.id,
  ].slice(-RECENT_RANDOM_VIDEO_MEMORY_LIMIT);
};

const probeVideoSegmentPlayable = (url: string, start: number, end: number) => new Promise<boolean>((resolve) => {
  if (typeof document === 'undefined' || !url) {
    resolve(false);
    return;
  }

  const video = document.createElement('video');
  let done = false;
  let seekingProbe = false;
  const cleanup = () => {
    window.clearTimeout(timer);
    video.removeAttribute('src');
    video.load();
  };
  const finish = (value: boolean) => {
    if (done) {
      return;
    }
    done = true;
    cleanup();
    resolve(value);
  };
  const timer = window.setTimeout(() => finish(false), VIDEO_PROBE_TIMEOUT_MS);

  video.preload = 'auto';
  video.muted = true;
  video.playsInline = true;
  video.oncanplay = () => {
    if (seekingProbe) {
      finish(true);
    }
  };
  video.onseeked = () => finish(true);
  video.onerror = () => finish(false);
  video.onloadedmetadata = () => {
    const duration = Number.isFinite(video.duration) ? video.duration : 0;
    if (duration <= 0) {
      finish(false);
      return;
    }
    const targetTime = Math.min(Math.max(0, start), Math.max(0, duration - 0.2));
    if (targetTime <= 0.1) {
      finish(true);
      return;
    }
    try {
      seekingProbe = true;
      video.currentTime = targetTime;
    } catch (error) {
      console.warn('Failed to seek random video probe', error);
      finish(false);
    }
  };

  const baseUrl = url.split('#')[0];
  video.src = `${baseUrl}#t=${Math.max(0, start).toFixed(3)},${Math.max(start, end).toFixed(3)}`;
  video.load();
});

const ensurePlayableSegment = async (segment: VideoSegment) => {
  const url = segment.item.sourceRecord
    ? await browserRef.value?.ensureMediaRecordPlayableUrl(segment.item.sourceRecord)
    : await browserRef.value?.ensureMediaPlayableUrl(segment.item.id);
  if (!url) {
    return false;
  }
  return probeVideoSegmentPlayable(url, segment.start, segment.end);
};

const fetchRandomMediaCandidates = async (slotProps: ToolbarSlotProps): Promise<MediaItem[]> => {
  if (!slotProps.selectedEntryId || !slotProps.selectedPath || !slotProps.canBrowse) {
    return [];
  }
  const randomSortProgram: GallerySortProgram = {
    rules: [{ field: 'random', direction: 'asc', nulls: 'last' }],
  };
  const listing = await fetchDeviceMedia(slotProps.selectedEntryId, {
    absolute_path: slotProps.selectedPath,
    recursive: Boolean(slotProps.recursiveDisplay),
    scan_limit: Math.max(RANDOM_CANDIDATE_LIMIT, Number(slotProps.mediaScanLimit || 0) || RANDOM_CANDIDATE_LIMIT),
    sort_program: randomSortProgram,
    offset: 0,
    limit: RANDOM_CANDIDATE_LIMIT,
  });
  return listing.media.map((item) => ({
    id: String(item.id),
    name: item.name,
    kind: item.kind,
    duration: typeof item.duration_ms === 'number' ? item.duration_ms / 1000 : null,
    size: item.size,
    modifiedAt: item.modified_at,
    sourceRecord: item,
  }));
};

const openSegment = async (segment: VideoSegment) => {
  currentClip.value = {
    id: segment.item.id,
    name: segment.item.name,
    start: segment.start,
    end: segment.end,
    sourceRecord: segment.item.sourceRecord,
  };
  if (segment.item.sourceRecord) {
    await browserRef.value?.openMediaRecordClip(segment.item.sourceRecord, segment.start, segment.end);
  } else {
    await browserRef.value?.openMediaClip(segment.item.id, segment.start, segment.end);
  }
};

const openRandomClip = async (slotProps: ToolbarSlotProps) => {
  if (isOpeningRandom.value) {
    return;
  }
  isOpeningRandom.value = true;
  try {
    const randomItems = await fetchRandomMediaCandidates(slotProps);
    const randomGroups = getVideoSegmentGroups(randomItems);
    const groups = randomGroups.length ? randomGroups : getVideoSegmentGroups(slotProps.mediaItems);
    const segmentAttempts = buildRandomSegmentAttempts(groups);
    if (!segmentAttempts.length) {
      ElMessage.info('当前结果里没有可随机的视频');
      return;
    }
    for (const segment of segmentAttempts) {
      if (await ensurePlayableSegment(segment)) {
        await openSegment(segment);
        rememberRandomVideo(segment);
        return;
      }
    }
    ElMessage.warning('随机候选都无法直接播放，请稍后再试或增加加载上限');
  } catch (error) {
    console.error('Failed to open random chan course clip', error);
    const fallbackSegments = buildRandomSegmentAttempts(getVideoSegmentGroups(slotProps.mediaItems));
    for (const segment of fallbackSegments) {
      if (await ensurePlayableSegment(segment)) {
        await openSegment(segment);
        rememberRandomVideo(segment);
        return;
      }
    }
    if (!fallbackSegments.length) {
      ElMessage.error('随机片段加载失败');
      return;
    }
    ElMessage.error('没有找到可直接播放的随机片段');
  } finally {
    isOpeningRandom.value = false;
  }
};

const openCurrentClip = async () => {
  const clip = currentClip.value;
  if (!clip) {
    return;
  }
  if (clip.sourceRecord) {
    await browserRef.value?.openMediaRecordClip(clip.sourceRecord, clip.start, clip.end);
    return;
  }
  await browserRef.value?.openMediaClip(clip.id, clip.start, clip.end);
};

const formatClock = (seconds: number) => {
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const restSeconds = total % 60;
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, '0')}:${String(restSeconds).padStart(2, '0')}`;
  }
  return `${minutes}:${String(restSeconds).padStart(2, '0')}`;
};

const formatClipRange = (start: number, end: number) => `${formatClock(start)}-${formatClock(end)}`;
</script>

<style scoped>
.chan-random-actions {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 28px;
  max-width: min(520px, 100%);
}

.chan-random-current {
  min-width: 0;
  overflow: hidden;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
