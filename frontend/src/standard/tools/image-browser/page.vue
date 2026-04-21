<template>
  <div class="image-browser-page">
    <section class="hero-panel">
        <div class="hero-copy">
          <div class="eyebrow">综合工具</div>
        <h1>本地文件浏览</h1>
        <p>
          这是一个轻量本地模式，只在浏览器里读取你手动选择的目录，不上传到服务器。
          当前主要支持图片和视频预览；设备侧的完整文件浏览能力以集群里的“设备任务 / 浏览文件”这套主线为主。
        </p>
      </div>

      <div class="hero-actions">
        <el-button
          v-if="mediaItems.length"
          size="large"
          @click="showSidebar = !showSidebar"
        >
          {{ showSidebar ? '收起侧栏' : '展开侧栏' }}
        </el-button>
        <el-button
          v-if="userStore.isAuthenticated"
          size="large"
          @click="router.push('/cluster/files')"
        >
          浏览设备文件
        </el-button>
        <el-button
          type="primary"
          size="large"
          :loading="isLoadingDirectory"
          :disabled="!directoryPickerSupported"
          @click="triggerDirectoryPicker"
        >
          {{ mediaItems.length ? '更换文件目录' : '选择文件目录' }}
        </el-button>
      </div>
    </section>

    <el-alert
      :title="pickerHintText"
      :type="directoryPickerSupported ? 'info' : 'warning'"
      :closable="false"
      class="picker-alert"
    />

    <ImageGalleryWorkspace
      :images="mediaItems"
      :show-sidebar="showSidebar"
      :source-label="activeSourceLabel"
      source-tag="浏览器本地"
      empty-badge="本地文件"
      empty-title="还没有加载文件"
      empty-description="选择一个本地目录后，会按相对路径构建文件夹列表，并在当前页面里预览当前已支持的文件类型。"
      :empty-steps="['1. 选择目录', '2. 按文件夹或关键字筛选', '3. 点击缩略图预览当前支持的文件']"
      storage-key-prefix="image_browser_local"
      delete-button-text="删除本地文件"
      item-label="文件"
      item-count-label="个文件"
      :delete-image="removeMedia"
      @update:show-sidebar="showSidebar = $event"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue';
import { useRouter } from 'vue-router';
import { ElMessage, ElMessageBox } from 'element-plus';

import ImageGalleryWorkspace from '@/components/ImageGalleryWorkspace.vue';
import { getFolderPath, type GalleryImage, type GalleryItemKind } from '@/utils/imageGallery';
import { useUserStore } from '@/store/userStore';

type HandlePermissionMode = 'read' | 'readwrite';
type HandlePermissionState = 'granted' | 'denied' | 'prompt';

interface HandlePermissionDescriptor {
  mode?: HandlePermissionMode;
}

interface BrowserFileHandle {
  kind: 'file';
  name: string;
  getFile: () => Promise<File>;
  queryPermission?: (descriptor?: HandlePermissionDescriptor) => Promise<HandlePermissionState>;
  requestPermission?: (descriptor?: HandlePermissionDescriptor) => Promise<HandlePermissionState>;
}

interface BrowserDirectoryHandle {
  kind: 'directory';
  name: string;
  entries: () => AsyncIterableIterator<[string, BrowserFileHandle | BrowserDirectoryHandle]>;
  removeEntry: (name: string, options?: { recursive?: boolean }) => Promise<void>;
  queryPermission?: (descriptor?: HandlePermissionDescriptor) => Promise<HandlePermissionState>;
  requestPermission?: (descriptor?: HandlePermissionDescriptor) => Promise<HandlePermissionState>;
}

interface DirectoryPickerOptionsLike {
  id?: string;
  mode?: HandlePermissionMode;
  startIn?: string;
}

interface WindowWithDirectoryPicker extends Window {
  showDirectoryPicker?: (options?: DirectoryPickerOptionsLike) => Promise<BrowserDirectoryHandle>;
}

interface LocalBrowserMedia extends GalleryImage {
  fileHandle: BrowserFileHandle;
  parentHandle: BrowserDirectoryHandle;
}

const LOCAL_IMAGE_EXTENSIONS = new Set([
  '.avif',
  '.bmp',
  '.gif',
  '.heic',
  '.jpeg',
  '.jpg',
  '.png',
  '.svg',
  '.tif',
  '.tiff',
  '.webp',
]);

const LOCAL_VIDEO_EXTENSIONS = new Set([
  '.avi',
  '.m4v',
  '.mkv',
  '.mov',
  '.mp4',
  '.mpeg',
  '.mpg',
  '.ogv',
  '.webm',
]);

const router = useRouter();
const userStore = useUserStore();

const directoryPickerSupported =
  typeof window !== 'undefined' &&
  window.isSecureContext &&
  typeof (window as WindowWithDirectoryPicker).showDirectoryPicker === 'function';

const mediaItems = ref<LocalBrowserMedia[]>([]);
const activeSourceLabel = ref('未选择目录');
const showSidebar = ref(true);
const isLoadingDirectory = ref(false);

const pickerHintText = computed(() =>
  directoryPickerSupported
    ? '当前使用目录授权模式加载本地文件，现阶段优先支持图片和视频预览，并可在 Chromium / Edge 的 localhost 或 https 页面里直接删除本地文件。'
    : '当前浏览器或页面环境不支持 showDirectoryPicker，无法启用真删除。请使用 Chromium / Edge，并通过 localhost 或 https 打开。'
);

const buildLocalFolderDisplayPath = (rootLabel: string, folderPath: string) => {
  const normalizedRoot = rootLabel.trim();
  if (!folderPath) {
    return normalizedRoot;
  }
  return `${normalizedRoot}/${folderPath}`;
};

const getExtension = (filename: string) => {
  const lastDotIndex = filename.lastIndexOf('.');
  if (lastDotIndex < 0) return '';
  return filename.slice(lastDotIndex).toLowerCase();
};

const resolveMediaKind = (file: File): GalleryItemKind | null => {
  if (file.type.startsWith('image/')) return 'image';
  if (file.type.startsWith('video/')) return 'video';

  const extension = getExtension(file.name);
  if (LOCAL_IMAGE_EXTENSIONS.has(extension)) return 'image';
  if (LOCAL_VIDEO_EXTENSIONS.has(extension)) return 'video';
  return null;
};

const triggerDirectoryPicker = async () => {
  const picker = (window as WindowWithDirectoryPicker).showDirectoryPicker;
  if (!directoryPickerSupported || !picker) {
    ElMessage.error('当前环境不支持目录授权读取，请改用 Chromium / Edge 的 localhost 或 https 页面');
    return;
  }

  isLoadingDirectory.value = true;

  try {
    const handle = await picker({
      id: 'codeyun-image-browser',
      mode: 'readwrite',
      startIn: 'pictures',
    });

    const permissionGranted = await ensureHandlePermission(handle, 'readwrite');
    if (!permissionGranted) {
      ElMessage.error('未授予目录读写权限，无法启用本地删除');
      return;
    }

    const nextMediaItems = await collectMediaFromDirectory(handle, handle.name);
    replaceMediaItems(nextMediaItems, handle.name);

    if (!nextMediaItems.length) {
      ElMessage.warning('所选目录里没有当前可识别的文件类型');
      return;
    }

    ElMessage.success(`已加载 ${nextMediaItems.length} 个文件`);
  } catch (error) {
    if (!isAbortLikeError(error)) {
      console.error('Failed to read directory', error);
      ElMessage.error('读取目录失败，请检查浏览器权限或控制台日志');
    }
  } finally {
    isLoadingDirectory.value = false;
  }
};

const replaceMediaItems = (nextMediaItems: LocalBrowserMedia[], sourceLabel: string) => {
  revokeMediaUrls(mediaItems.value);
  mediaItems.value = nextMediaItems;
  activeSourceLabel.value = sourceLabel;
};

const ensureHandlePermission = async (
  handle: BrowserDirectoryHandle | BrowserFileHandle,
  mode: HandlePermissionMode
) => {
  if (handle.queryPermission) {
    const state = await handle.queryPermission({ mode });
    if (state === 'granted') return true;
  }

  if (handle.requestPermission) {
    const state = await handle.requestPermission({ mode });
    return state === 'granted';
  }

  return true;
};

const collectMediaFromDirectory = async (
  directoryHandle: BrowserDirectoryHandle,
  rootLabel: string,
  parentParts: string[] = []
): Promise<LocalBrowserMedia[]> => {
  const nextMediaItems: LocalBrowserMedia[] = [];

  for await (const [, handle] of directoryHandle.entries()) {
    if (handle.kind === 'directory') {
      const childItems = await collectMediaFromDirectory(handle, rootLabel, [...parentParts, handle.name]);
      nextMediaItems.push(...childItems);
      continue;
    }

    const file = await handle.getFile();
    const kind = resolveMediaKind(file);
    if (!kind) continue;

    const relativePath = [...parentParts, file.name].join('/');
    const folderPath = getFolderPath(relativePath);

    nextMediaItems.push({
      id: `${relativePath}:${file.size}:${file.lastModified}`,
      name: file.name,
      relativePath,
      folderPath,
      folderDisplayPath: buildLocalFolderDisplayPath(rootLabel, folderPath),
      size: file.size,
      modifiedAt: file.lastModified,
      url: URL.createObjectURL(file),
      width: null,
      height: null,
      kind,
      mimeType: file.type || null,
      duration: null,
      fileHandle: handle,
      parentHandle: directoryHandle,
    });
  }

  return nextMediaItems;
};

const revokeMediaUrls = (items: LocalBrowserMedia[]) => {
  for (const media of items) {
    if (media.url) {
      URL.revokeObjectURL(media.url);
    }
  }
};

const removeMedia = async (imageId: string) => {
  const media = mediaItems.value.find((item) => item.id === imageId);
  if (!media) return false;

  try {
    await ElMessageBox.confirm(
      `将永久删除本地文件：${media.relativePath}`,
      '删除本地文件',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning',
      }
    );
  } catch {
    return false;
  }

  const permissionGranted = await ensureHandlePermission(media.parentHandle, 'readwrite');
  if (!permissionGranted) {
    ElMessage.error('没有目录写权限，无法删除本地文件');
    return false;
  }

  try {
    await media.parentHandle.removeEntry(media.fileHandle.name);
  } catch (error) {
    console.error('Failed to delete local file', error);
    ElMessage.error('删除本地文件失败，请检查目录权限或文件是否已被占用');
    return false;
  }

  if (media.url) {
    URL.revokeObjectURL(media.url);
  }
  mediaItems.value = mediaItems.value.filter((item) => item.id !== imageId);
  ElMessage.success('本地文件已删除');
  return true;
};

const isAbortLikeError = (error: unknown) =>
  error instanceof DOMException && (error.name === 'AbortError' || error.name === 'SecurityError');

onBeforeUnmount(() => {
  revokeMediaUrls(mediaItems.value);
});
</script>

<style scoped>
.image-browser-page {
  min-height: 100%;
  padding: 24px;
  box-sizing: border-box;
  background:
    radial-gradient(circle at top left, rgba(37, 99, 235, 0.1), transparent 28%),
    radial-gradient(circle at top right, rgba(16, 185, 129, 0.1), transparent 24%),
    linear-gradient(180deg, #f4f8ff 0%, #eef4f7 100%);
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.hero-panel {
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid rgba(148, 163, 184, 0.18);
  border-radius: 24px;
  box-shadow: 0 18px 40px rgba(15, 23, 42, 0.08);
  padding: 24px 28px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  flex-wrap: wrap;
}

.hero-copy {
  max-width: 760px;
}

.eyebrow {
  font-size: 12px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: #64748b;
}

.hero-copy h1 {
  margin: 6px 0 10px;
  font-size: 34px;
  line-height: 1.1;
  color: #0f172a;
}

.hero-copy p {
  margin: 0;
  font-size: 15px;
  color: #475569;
  max-width: 720px;
}

.hero-actions {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}

.picker-alert {
  border-radius: 18px;
}

@media (max-width: 780px) {
  .image-browser-page {
    padding: 16px;
  }

  .hero-panel {
    border-radius: 20px;
    padding: 18px;
  }

  .hero-copy h1 {
    font-size: 28px;
  }
}
</style>
