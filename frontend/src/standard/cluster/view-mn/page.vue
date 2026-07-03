<template>
  <ClusterFileBrowserPage
    fixed-device-id="codepc_mf"
    fixed-root-path="D:\home\chenkunze\data\m2510mn"
  >
    <template #toolbar-after="{ selectedPath, canBrowse, reloadDirectory }">
      <div v-if="isLocalCandidatePool(selectedPath)" class="mn-media-sync-actions">
        <MediaSyncStatusProbe
          :selected-path="selectedPath"
          :active="isLocalCandidatePool(selectedPath)"
          @load="loadCacheStatus"
        />
        <span
          class="mn-media-cache-count"
          title="提前缓存的 remote id、详情页 URL 和原图 URL，不是已下载图片。"
        >
          URL缓存 <strong>{{ candidateCacheCountText(selectedPath) }}</strong>
        </span>
        <el-button
          size="small"
          plain
          :loading="isStartingAction(selectedPath, 'collect')"
          :disabled="!canBrowse || isStarting(selectedPath) || isLocalTaskRunning(selectedPath)"
          @click="startPlatformAction('collect', selectedPath, reloadDirectory)"
        >
          补充缓存
        </el-button>
        <el-button
          type="primary"
          size="small"
          :loading="isStartingAction(selectedPath, 'download')"
          :disabled="!canBrowse || isStarting(selectedPath) || isLocalTaskRunning(selectedPath)"
          @click="startPlatformAction('download', selectedPath, reloadDirectory)"
        >
          下载
        </el-button>
        <el-button
          size="small"
          type="danger"
          plain
          :loading="isStartingAction(selectedPath, 'clean')"
          :disabled="!canBrowse || isStarting(selectedPath) || isLocalTaskRunning(selectedPath)"
          @click="startPlatformAction('clean', selectedPath, reloadDirectory)"
        >
          清理
        </el-button>
        <span v-if="statusMessage(selectedPath)" class="mn-media-sync-status">{{ statusMessage(selectedPath) }}</span>
      </div>
    </template>
  </ClusterFileBrowserPage>
</template>

<script setup lang="ts">
import axios from 'axios';
import { defineComponent, onBeforeUnmount, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import ClusterFileBrowserPage from '@/standard/cluster/files/page.vue';
import {
  fetchMediaSyncCandidateCacheStatus,
  fetchMediaSyncStatus,
  openMediaSyncCandidateLoginPage,
  startMediaSyncPlatformClean,
  startMediaSyncPlatformCollect,
  startMediaSyncPlatformDownload,
  type MediaSyncStatus,
} from '@/plugins/modules/media-sync/api';

const MN_ROOT_DIR = 'D:\\home\\chenkunze\\data\\m2510mn';
const MEDIA_SYNC_TARGET_COUNT = 200;
const CACHE_STATUS_TTL_MS = 10_000;

type MediaSyncAction = 'collect' | 'download' | 'clean';
type MediaSyncPlatform = 'pixiv' | 'pinterest';

const statusByPath = ref<Record<string, MediaSyncStatus>>({});
const startingActionByPath = ref<Record<string, MediaSyncAction | ''>>({});
const runningActionByPath = ref<Record<string, MediaSyncAction | ''>>({});
const statusLoadedAtByPath = ref<Record<string, number>>({});
const statusLoadErrorByPath = ref<Record<string, boolean>>({});
const candidateCacheCountByPath = ref<Record<string, number>>({});
const loadingStatusPaths = new Set<string>();
const pollTimers = new Map<string, number>();
let isUnmounted = false;

const MediaSyncStatusProbe = defineComponent({
  name: 'MediaSyncStatusProbe',
  props: {
    selectedPath: { type: String, required: true },
    active: { type: Boolean, required: true },
  },
  emits: ['load'],
  setup(props, { emit }) {
    watch(
      () => [props.selectedPath, props.active] as const,
      ([selectedPath, active]) => {
        if (active && selectedPath) {
          emit('load', selectedPath);
        }
      },
      { immediate: true },
    );
    return () => null;
  },
});

function isLocalTaskRunning(path: string) {
  const key = normalizePath(path);
  return Boolean(statusByPath.value[key]?.running || runningActionByPath.value[key]);
}

function statusMessage(path: string) {
  if (!isLocalTaskRunning(path)) return '';
  const status = statusByPath.value[normalizePath(path)];
  const message = String(status?.message || '').trim();
  return status?.running ? message : '';
}

function isStarting(path: string) {
  return Boolean(startingActionByPath.value[normalizePath(path)]);
}

function isStartingAction(path: string, action: MediaSyncAction) {
  return startingActionByPath.value[normalizePath(path)] === action;
}

function inferPlatformFromPath(path: string): MediaSyncPlatform | '' {
  const segments = normalizePath(path).split('/').filter(Boolean);
  const name = segments[segments.length - 1] || '';
  if (name === '_pixiv') return 'pixiv';
  if (name === '_pinterest') return 'pinterest';
  return '';
}

function candidateCacheCountText(path: string) {
  const key = normalizePath(path);
  if (!inferPlatformFromPath(path)) return '';
  const count = candidateCacheCountByPath.value[key];
  if (typeof count === 'number') return String(count);
  return statusLoadErrorByPath.value[key] ? '取数失败' : '加载中';
}

function isLocalCandidatePool(path: string) {
  const segments = normalizePath(path).split('/').filter(Boolean);
  const name = segments[segments.length - 1] || '';
  return name.startsWith('_') && name.length > 1;
}

function normalizePath(path: string) {
  return String(path || '').replace(/\\/g, '/').replace(/\/+/g, '/').toLowerCase();
}

async function loadCacheStatus(selectedPath: string, options: { force?: boolean } = {}) {
  const key = normalizePath(selectedPath);
  if (!isLocalCandidatePool(selectedPath) || loadingStatusPaths.has(key)) return;
  const loadedAt = statusLoadedAtByPath.value[key] || 0;
  if (!options.force && statusByPath.value[key] && Date.now() - loadedAt < CACHE_STATUS_TTL_MS) return;

  loadingStatusPaths.add(key);
  statusLoadErrorByPath.value = { ...statusLoadErrorByPath.value, [key]: false };
  try {
    const nextStatus = await fetchMediaSyncCandidateCacheStatus({ path: selectedPath });
    if (isUnmounted) return;
    candidateCacheCountByPath.value = {
      ...candidateCacheCountByPath.value,
      [key]: nextStatus.pending_count,
    };
    statusLoadedAtByPath.value = { ...statusLoadedAtByPath.value, [key]: Date.now() };
  } catch (error) {
    statusLoadErrorByPath.value = { ...statusLoadErrorByPath.value, [key]: true };
    console.error('Failed to load local media cache status', error);
  } finally {
    loadingStatusPaths.delete(key);
  }
}

async function startPlatformAction(
  action: MediaSyncAction,
  selectedPath: string,
  reloadDirectory?: () => Promise<void>,
) {
  const key = normalizePath(selectedPath);
  if (!isLocalCandidatePool(selectedPath) || isStarting(selectedPath) || isLocalTaskRunning(selectedPath)) return;

  startingActionByPath.value = { ...startingActionByPath.value, [key]: action };
  runningActionByPath.value = { ...runningActionByPath.value, [key]: action };
  try {
    const nextStatus = action === 'download'
      ? await startMediaSyncPlatformDownload({
          root_dir: MN_ROOT_DIR,
          path: selectedPath,
          target_new_count: MEDIA_SYNC_TARGET_COUNT,
        })
      : action === 'collect'
        ? await startMediaSyncPlatformCollect({
            root_dir: MN_ROOT_DIR,
            path: selectedPath,
            target_new_count: MEDIA_SYNC_TARGET_COUNT,
          })
        : await startMediaSyncPlatformClean({
          root_dir: MN_ROOT_DIR,
          path: selectedPath,
        });
    if (isUnmounted) return;
    statusByPath.value = { ...statusByPath.value, [key]: nextStatus };
    pollStatus(selectedPath, reloadDirectory);
  } catch (error) {
    if (isUnmounted) return;
    console.error('Failed to start local media sync action', error);
    if (isAlreadyRunningError(error)) {
      ElMessage.info('已有后台任务正在运行');
      await syncRunningStatus(selectedPath, reloadDirectory);
      return;
    }
    ElMessage.error(actionStartErrorMessage(action));
    runningActionByPath.value = { ...runningActionByPath.value, [key]: '' };
  } finally {
    if (isUnmounted) return;
    startingActionByPath.value = { ...startingActionByPath.value, [key]: '' };
  }
}

async function syncRunningStatus(selectedPath: string, reloadDirectory?: () => Promise<void>) {
  const key = normalizePath(selectedPath);
  try {
    const nextStatus = await fetchMediaSyncStatus({ path: selectedPath, include_sources: false });
    if (isUnmounted) return;
    statusByPath.value = { ...statusByPath.value, [key]: nextStatus };
    if (nextStatus.running) {
      pollStatus(selectedPath, reloadDirectory);
    }
  } catch (error) {
    console.error('Failed to sync local media task status', error);
  }
}

function isAlreadyRunningError(error: unknown) {
  return axios.isAxiosError(error) && error.response?.status === 409;
}

function pollStatus(selectedPath: string, reloadDirectory?: () => Promise<void>) {
  const key = normalizePath(selectedPath);
  clearPollTimer(key);
  const timer = window.setInterval(async () => {
    try {
      const nextStatus = await fetchMediaSyncStatus({ path: selectedPath, include_sources: false });
      if (isUnmounted) return;
      statusByPath.value = { ...statusByPath.value, [key]: nextStatus };
      if (!nextStatus.running) {
        clearPollTimer(key);
        const finishedAction = runningActionByPath.value[key];
        runningActionByPath.value = { ...runningActionByPath.value, [key]: '' };
        if (nextStatus.error) {
          if (nextStatus.needs_login) {
            ElMessage.warning(nextStatus.action_hint || '需要先完成登录，然后重新点击下载');
            void openLoginWindow(selectedPath);
          } else {
            ElMessage.error(nextStatus.error);
          }
          return;
        }
        if (finishedAction) {
          ElMessage.success(actionSuccessMessage(finishedAction));
        }
        if (finishedAction !== 'collect') {
          void reloadDirectory?.();
        }
        void loadCacheStatus(selectedPath, { force: true });
      }
    } catch (error) {
      if (isUnmounted) return;
      console.error('Failed to poll local media sync status', error);
      clearPollTimer(key);
      runningActionByPath.value = { ...runningActionByPath.value, [key]: '' };
    }
  }, 1800);
  pollTimers.set(key, timer);
}

function actionStartErrorMessage(action: MediaSyncAction) {
  if (action === 'collect') return '启动补充缓存失败';
  return action === 'download' ? '启动下载失败' : '启动清理失败';
}

function actionSuccessMessage(action: MediaSyncAction) {
  if (action === 'collect') return '缓存补充完成';
  return action === 'download' ? '下载完成' : '清理完成';
}

async function openLoginWindow(selectedPath: string) {
  try {
    await openMediaSyncCandidateLoginPage({
      root_dir: MN_ROOT_DIR,
      path: selectedPath,
    });
    ElMessage.info('已打开登录窗口，登录完成后重新点击下载');
  } catch (error) {
    console.error('Failed to open local media login page', error);
  }
}

function clearPollTimer(key: string) {
  const timer = pollTimers.get(key);
  if (timer !== undefined) {
    window.clearInterval(timer);
    pollTimers.delete(key);
  }
}

onBeforeUnmount(() => {
  isUnmounted = true;
  for (const key of pollTimers.keys()) {
    clearPollTimer(key);
  }
});
</script>

<style scoped>
.mn-media-sync-actions {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 28px;
}

.mn-media-sync-status {
  max-width: 280px;
  overflow: hidden;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
