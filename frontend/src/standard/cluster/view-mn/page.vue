<template>
  <ClusterFileBrowserPage
    fixed-device-id="codepc_mf"
    fixed-root-path="E:\data\m2510mn"
  >
    <template #directory-after="{ selectedPath, canBrowse, reloadDirectory }">
      <div
        v-if="isInventoryContext(selectedPath)"
        :key="normalizePath(selectedPath)"
        class="mn-media-sync-actions"
        @vue:mounted="loadInventoryContext(selectedPath, reloadDirectory)"
      >
        <el-button
          type="primary"
          size="small"
          :loading="isStartingAction(selectedPath, 'download')"
          :disabled="!canBrowse || isStarting(selectedPath) || isLocalTaskRunning(selectedPath)"
          @click="inferPlatformFromPath(selectedPath) === 'video'
            ? openVideoDownloadDialog(selectedPath, reloadDirectory)
            : startPlatformAction('download', selectedPath, reloadDirectory)"
        >
          补满 {{ targetCount(inferPlatformFromPath(selectedPath)) }}/{{ inventoryCount(inferPlatformFromPath(selectedPath)) }}
        </el-button>
        <el-button
          size="small"
          type="danger"
          plain
          :loading="isStartingAction(selectedPath, 'clean')"
          :disabled="!canBrowse || isStarting(selectedPath) || isLocalTaskRunning(selectedPath)"
          @click="startPlatformAction('clean', selectedPath, reloadDirectory)"
        >
          清理并换批
        </el-button>
      </div>
      <div
        v-if="isVideoReservoirContext(selectedPath)"
        class="mn-media-sync-actions"
      >
        <el-button type="primary" size="small" @click="openVideoDownloadDialog(selectedPath, reloadDirectory)">
          下载 Bilibili 视频
        </el-button>
        <span class="mn-media-sync-status">仅处理明确粘贴的链接，最高可用品质，重复 BVID 自动复用</span>
      </div>
    </template>
  </ClusterFileBrowserPage>

  <el-dialog v-model="videoDownloadDialogVisible" title="下载 Bilibili 视频" width="520px">
    <el-input
      v-model="videoUrlText"
      type="textarea"
      :rows="7"
      maxlength="4000"
      show-word-limit
      placeholder="每行一个 bilibili.com/video/BV... 链接"
    />
    <p class="mn-keyword-help">只下载当前浏览器登录态能够正常播放的最高品质，不绕过会员、付费或 DRM。</p>
    <template #footer>
      <el-button @click="videoDownloadDialogVisible = false">取消</el-button>
      <el-button type="primary" :loading="startingVideoDownload" @click="startVideoDownload">开始下载</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import axios from 'axios';
import { onBeforeUnmount, ref } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import ClusterFileBrowserPage from '@/standard/cluster/files/page.vue';
import {
  fetchMediaSyncInventoryStatus,
  fetchMediaSyncStatus,
  openMediaSyncCandidateLoginPage,
  startMediaSyncPlatformClean,
  startMediaSyncPlatformDownload,
  type MediaSyncStatus,
  type MediaSyncInventoryStatus,
} from '@/plugins/modules/media-sync/api';

const MN_ROOT_DIR = 'E:\\data\\m2510mn';
const MEDIA_SYNC_TARGET_COUNT = 200;
const VIDEO_SYNC_TARGET_COUNT = 20;

type MediaSyncAction = 'download' | 'clean';
type MediaSyncPlatform = 'pixiv' | 'pinterest' | 'video';

const statusByPath = ref<Record<string, MediaSyncStatus>>({});
const startingActionByPath = ref<Record<string, MediaSyncAction | ''>>({});
const runningActionByPath = ref<Record<string, MediaSyncAction | ''>>({});
const inventoryByPlatform = ref<Partial<Record<MediaSyncPlatform, MediaSyncInventoryStatus>>>({});
const videoUrlText = ref('');
const videoDownloadDialogVisible = ref(false);
const startingVideoDownload = ref(false);
const videoActionPath = ref(`${MN_ROOT_DIR}\\2、video`);
let videoReloadDirectory: (() => Promise<void>) | undefined;
const deliveredBatchReadyAtByPath = new Map<string, number>();
const pollTimers = new Map<string, number>();
let isUnmounted = false;

function isLocalTaskRunning(path: string) {
  const key = normalizePath(path);
  const status = statusByPath.value[key];
  return Boolean(
    runningActionByPath.value[key]
    || (status?.running && !isBackgroundCollection(status) && !isReadyBatch(status)),
  );
}

function isBackgroundCollection(status?: MediaSyncStatus) {
  return status?.stage === 'pixiv-collect-ids' || status?.stage === 'pinterest-collect-ids';
}

function isReadyBatch(status?: MediaSyncStatus) {
  return status?.stage === 'pixiv-batch-ready' || status?.stage === 'pinterest-batch-ready';
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
  if (name === '2、pixiv') return 'pixiv';
  if (name === '2、pinterest') return 'pinterest';
  if (name === '2、video') return 'video';
  return '';
}

function isLocalCandidatePool(path: string) {
  return Boolean(inferPlatformFromPath(path));
}

function isInventoryContext(path: string) {
  return isLocalCandidatePool(path);
}

function isVideoReservoirContext(path: string) {
  const segments = normalizePath(path).split('/').filter(Boolean);
  return segments[segments.length - 1] === '3、video';
}

function openVideoDownloadDialog(_selectedPath: string, reloadDirectory?: () => Promise<void>) {
  videoActionPath.value = `${MN_ROOT_DIR}\\2、video`;
  videoReloadDirectory = reloadDirectory;
  videoDownloadDialogVisible.value = true;
}

async function startVideoDownload() {
  const urls = videoUrlText.value.split(/\r?\n/).map(value => value.trim()).filter(Boolean);
  if (!urls.length) {
    ElMessage.warning('请至少粘贴一个 Bilibili 视频链接');
    return;
  }
  startingVideoDownload.value = true;
  try {
    const nextStatus = await startMediaSyncPlatformDownload({
      root_dir: MN_ROOT_DIR,
      path: videoActionPath.value,
      target_new_count: VIDEO_SYNC_TARGET_COUNT,
      urls,
    });
    const key = normalizePath(videoActionPath.value);
    statusByPath.value = { ...statusByPath.value, [key]: nextStatus };
    runningActionByPath.value = { ...runningActionByPath.value, [key]: 'download' };
    videoDownloadDialogVisible.value = false;
    videoUrlText.value = '';
    pollStatus(videoActionPath.value, videoReloadDirectory);
    ElMessage.success('视频下载已启动');
  } catch (error) {
    console.error('Failed to start Bilibili download', error);
    const detail = axios.isAxiosError(error) ? String(error.response?.data?.detail || '') : '';
    ElMessage.error(detail || '启动视频下载失败');
  } finally {
    startingVideoDownload.value = false;
  }
}

function inventoryCount(platform: MediaSyncPlatform | '') {
  if (!platform) return '—';
  return inventoryByPlatform.value[platform]?.inventory_count ?? '—';
}

function targetCount(platform: MediaSyncPlatform | '') {
  return platform === 'video' ? VIDEO_SYNC_TARGET_COUNT : MEDIA_SYNC_TARGET_COUNT;
}

async function loadInventoryStatus(platform: MediaSyncPlatform) {
  try {
    const directoryStem = platform;
    const nextStatus = await fetchMediaSyncInventoryStatus({
      root_dir: MN_ROOT_DIR,
      path: `${MN_ROOT_DIR}\\2、${directoryStem}`,
    });
    if (isUnmounted) return;
    inventoryByPlatform.value = { ...inventoryByPlatform.value, [platform]: nextStatus };
  } catch (error) {
    console.error(`Failed to load ${platform} local inventory`, error);
  }
}

function loadInventoryContext(path: string, reloadDirectory?: () => Promise<void>) {
  const platform = inferPlatformFromPath(path);
  if (!platform) return;
  void loadInventoryStatus(platform);
  // 独立 Worker 不随页面或后端生命周期结束；重新进入目录时必须主动接回状态与轮询。
  void syncRunningStatus(path, reloadDirectory);
}

function normalizePath(path: string) {
  return String(path || '').replace(/\\/g, '/').replace(/\/+/g, '/').toLowerCase();
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
          target_new_count: targetCount(inferPlatformFromPath(selectedPath)),
        })
      : await startPlatformCleanWithConfirmation(selectedPath);
    if (!nextStatus) return;
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
    if (nextStatus.running && !isBackgroundCollection(nextStatus)) {
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
      await deliverReadyBatch(selectedPath, nextStatus, reloadDirectory);
      if (!nextStatus.running) {
        clearPollTimer(key);
        const finishedAction = runningActionByPath.value[key];
        runningActionByPath.value = { ...runningActionByPath.value, [key]: '' };
        if (nextStatus.error) {
          if (nextStatus.needs_login) {
            ElMessage.warning(nextStatus.action_hint || '需要先完成登录，然后重新点击下载');
            void openLoginWindow(selectedPath);
          } else {
            ElMessage.error(
              nextStatus.error,
            );
          }
          return;
        }
        if (finishedAction && !deliveredBatchReadyAtByPath.has(key)) {
          ElMessage.success(actionSuccessMessage(finishedAction));
        }
        const platform = inferPlatformFromPath(selectedPath);
        if (platform) void loadInventoryStatus(platform);
        void reloadDirectory?.();
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
  return action === 'download' ? '启动补货失败' : '启动清理换批失败';
}

async function startPlatformCleanWithConfirmation(selectedPath: string) {
  const payload = { root_dir: MN_ROOT_DIR, path: selectedPath };
  try {
    return await startMediaSyncPlatformClean(payload);
  } catch (error) {
    const detail = axios.isAxiosError(error) ? error.response?.data?.detail : undefined;
    if (detail?.code !== 'unweighted_batch_confirmation_required') throw error;
    try {
      await ElMessageBox.confirm(
        `当前 ${Number(detail.review_count || 0)} 张图片没有任何权重标记，继续会删除整批并换入下一批。`,
        '确认清理未标记批次？',
        {
          type: 'warning',
          confirmButtonText: '仍然清理',
          cancelButtonText: '返回检查',
          distinguishCancelAndClose: true,
        },
      );
    } catch {
      return null;
    }
    return startMediaSyncPlatformClean({ ...payload, confirm_unweighted_batch: true });
  }
}

async function deliverReadyBatch(
  selectedPath: string,
  status: MediaSyncStatus,
  reloadDirectory?: () => Promise<void>,
) {
  const key = normalizePath(selectedPath);
  const batchReady = status.summary?.batch_ready;
  if (!batchReady || typeof batchReady !== 'object') return;
  const readyAt = Number((batchReady as Record<string, unknown>).ready_at || 0);
  if (!Number.isFinite(readyAt) || readyAt <= 0 || deliveredBatchReadyAtByPath.get(key) === readyAt) return;

  deliveredBatchReadyAtByPath.set(key, readyAt);
  runningActionByPath.value = { ...runningActionByPath.value, [key]: '' };
  const platform = inferPlatformFromPath(selectedPath);
  if (platform) await loadInventoryStatus(platform);
  await reloadDirectory?.();
  ElMessage.success('清理换批完成');
}

function actionSuccessMessage(action: MediaSyncAction) {
  return action === 'download' ? '待整理区已补满' : '清理换批完成';
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

.mn-keyword-help {
  margin: 8px 0 0;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

</style>
