<script setup lang="ts">
import { computed, defineAsyncComponent, onBeforeUnmount, ref } from 'vue';
import { useRouter } from 'vue-router';
import { ElMessage } from 'element-plus';
import { ArrowUp, Document, Download, Folder, View } from '@element-plus/icons-vue';

import api from '@/api';
import { importPdfDocumentFromLocalPath } from '@/api/pdfDocuments';
import DocPage from '@/components/DocPage.vue';
import {
  formatPreviewKindLabel,
  resolveCodeyunPreviewKind,
  type CodeyunPreviewKind,
} from '@/utils/filePreviewRegistry';
import { formatDate, formatFileSize } from '@/utils/imageGallery';

interface FileExplorerItem {
  name: string;
  path: string;
  is_dir: boolean;
  size?: number | null;
  modified_at?: number | null;
}

const router = useRouter();

const path = ref('D:\\');
const currentPath = ref('');
const files = ref<FileExplorerItem[]>([]);
const loading = ref(false);
const previewLoading = ref(false);
const previewError = ref('');
const selectedFile = ref<FileExplorerItem | null>(null);
const selectedPreviewKind = ref<CodeyunPreviewKind>('unsupported');
const previewBlob = ref<Blob | null>(null);
const previewUrl = ref('');

const hasSelection = computed(() => Boolean(selectedFile.value));
const selectedFileName = computed(() => selectedFile.value?.name || '');
const selectedFilePath = computed(() => selectedFile.value?.path || '');
const selectedMimeType = computed(() => previewBlob.value?.type || '');
const selectedSize = computed(() => selectedFile.value?.size ?? previewBlob.value?.size);
const selectedKindLabel = computed(() => formatPreviewKindLabel(selectedPreviewKind.value));
const GenericFileViewer = defineAsyncComponent(() => import('@/components/GenericFileViewer.vue'));

const sortedFiles = computed(() => files.value);

function revokePreviewUrl() {
  if (previewUrl.value) {
    URL.revokeObjectURL(previewUrl.value);
    previewUrl.value = '';
  }
}

function resetPreview() {
  revokePreviewUrl();
  selectedFile.value = null;
  selectedPreviewKind.value = 'unsupported';
  previewBlob.value = null;
  previewError.value = '';
}

const listDir = async (targetPath = path.value) => {
  loading.value = true;
  try {
    const res = await api.post('/fs/list_dir', { path: targetPath });
    files.value = res.data.items ?? [];
    currentPath.value = res.data.current_path ?? targetPath;
    path.value = currentPath.value || targetPath;
    resetPreview();
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '目录加载失败');
  } finally {
    loading.value = false;
  }
};

function joinParentPath(value: string) {
  const normalized = value.replace(/[\\/]+$/, '');
  const separatorIndex = Math.max(normalized.lastIndexOf('\\'), normalized.lastIndexOf('/'));
  if (separatorIndex <= 0) {
    return value;
  }
  const parent = normalized.slice(0, separatorIndex);
  return /^[A-Za-z]:$/.test(parent) ? `${parent}\\` : parent;
}

function openParentDirectory() {
  if (!currentPath.value) {
    return;
  }
  void listDir(joinParentPath(currentPath.value));
}

function openDirectory(item: FileExplorerItem) {
  void listDir(item.path);
}

async function fetchFileBlob(item: FileExplorerItem) {
  const response = await api.get('/fs/content', {
    params: { absolute_path: item.path },
    responseType: 'blob',
    timeout: 60000,
  });
  return response.data as Blob;
}

async function openPdfInCodeyun(item: FileExplorerItem) {
  previewLoading.value = true;
  try {
    const document = await importPdfDocumentFromLocalPath({ absolute_path: item.path });
    await router.push(`/pdf/${document.id}`);
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '打开 PDF 阅读器失败');
  } finally {
    previewLoading.value = false;
  }
}

async function previewFile(item: FileExplorerItem) {
  if (item.is_dir) {
    openDirectory(item);
    return;
  }

  resetPreview();
  selectedFile.value = item;
  selectedPreviewKind.value = resolveCodeyunPreviewKind(item.name || item.path);

  if (selectedPreviewKind.value === 'pdf') {
    await openPdfInCodeyun(item);
    return;
  }

  if (selectedPreviewKind.value === 'unsupported') {
    previewError.value = '这个格式暂未接入预览，可先下载或用系统应用打开。';
    return;
  }

  previewLoading.value = true;
  try {
    const blob = await fetchFileBlob(item);
    previewBlob.value = blob;
    if (selectedPreviewKind.value === 'image' || selectedPreviewKind.value === 'media') {
      previewUrl.value = URL.createObjectURL(blob);
    }
  } catch (error: any) {
    previewError.value = error?.response?.data?.detail || '文件预览加载失败';
  } finally {
    previewLoading.value = false;
  }
}

async function downloadSelectedFile() {
  if (!selectedFile.value) {
    return;
  }
  try {
    const blob = previewBlob.value ?? await fetchFileBlob(selectedFile.value);
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = selectedFile.value.name || 'download';
    link.click();
    URL.revokeObjectURL(url);
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '下载失败');
  }
}

function formatItemSize(item: FileExplorerItem) {
  if (item.is_dir) {
    return '';
  }
  return typeof item.size === 'number' ? formatFileSize(item.size) : '--';
}

function formatItemModifiedAt(item: FileExplorerItem) {
  return typeof item.modified_at === 'number' ? formatDate(item.modified_at) : '--';
}

void listDir();

onBeforeUnmount(() => {
  revokePreviewUrl();
});
</script>

<template>
  <DocPage title="文件浏览器" description="浏览本地文件系统并预览常见附件">
    <div class="file-explorer">
      <div class="file-toolbar">
        <el-button :icon="ArrowUp" :disabled="!currentPath" @click="openParentDirectory" />
        <el-input v-model="path" class="path-input" placeholder="输入路径" @keyup.enter="listDir()">
          <template #append>
            <el-button :loading="loading" @click="listDir()">跳转</el-button>
          </template>
        </el-input>
      </div>

      <div class="file-workbench">
        <section class="file-list-pane">
          <el-table
            :data="sortedFiles"
            table-layout="auto"
            :fit="false"
            height="100%"
            v-loading="loading"
            highlight-current-row
            @row-dblclick="previewFile"
          >
            <el-table-column label="名称" min-width="260">
              <template #default="{ row }">
                <button type="button" class="file-name-button" @click="previewFile(row)">
                  <el-icon class="file-name-icon">
                    <Folder v-if="row.is_dir" />
                    <Document v-else />
                  </el-icon>
                  <span class="file-name-text" :title="row.name">{{ row.name }}</span>
                </button>
              </template>
            </el-table-column>
            <el-table-column label="类型" width="110">
              <template #default="{ row }">
                <el-tag v-if="row.is_dir" size="small">目录</el-tag>
                <el-tag v-else size="small" type="info">{{ formatPreviewKindLabel(resolveCodeyunPreviewKind(row.name)) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="大小" width="110">
              <template #default="{ row }">{{ formatItemSize(row) }}</template>
            </el-table-column>
            <el-table-column label="修改时间" width="180">
              <template #default="{ row }">{{ formatItemModifiedAt(row) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="92" fixed="right">
              <template #default="{ row }">
                <el-button
                  v-if="!row.is_dir"
                  :icon="View"
                  text
                  title="预览"
                  @click.stop="previewFile(row)"
                />
              </template>
            </el-table-column>
          </el-table>
        </section>

        <section class="preview-pane" v-loading="previewLoading">
          <div v-if="hasSelection" class="preview-header">
            <div class="preview-title">
              <strong :title="selectedFileName">{{ selectedFileName }}</strong>
              <span>{{ selectedKindLabel }}</span>
            </div>
            <el-button
              :icon="Download"
              text
              :disabled="previewLoading"
              title="下载"
              @click="downloadSelectedFile"
            />
          </div>

          <div v-if="previewError" class="preview-empty is-error">
            {{ previewError }}
          </div>
          <div v-else-if="!hasSelection" class="preview-empty">
            选择文件后预览
          </div>
          <div v-else-if="selectedPreviewKind === 'image' && previewUrl" class="native-preview-stage">
            <img :src="previewUrl" :alt="selectedFileName">
          </div>
          <div v-else-if="selectedPreviewKind === 'media' && previewUrl" class="native-preview-stage">
            <video :src="previewUrl" controls />
          </div>
          <GenericFileViewer
            v-else-if="selectedPreviewKind === 'generic'"
            class="generic-preview"
            :file-blob="previewBlob"
            :filename="selectedFileName"
            :mime-type="selectedMimeType"
            :size="selectedSize ?? undefined"
          />
        </section>
      </div>

      <div v-if="selectedFilePath" class="selected-path" :title="selectedFilePath">
        {{ selectedFilePath }}
      </div>
    </div>
  </DocPage>
</template>

<style scoped>
.file-explorer {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: calc(100vh - 180px);
}

.file-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
}

.path-input {
  max-width: 920px;
}

.file-workbench {
  display: grid;
  grid-template-columns: minmax(520px, 0.92fr) minmax(420px, 1.08fr);
  gap: 12px;
  min-height: 640px;
  flex: 1;
}

.file-list-pane,
.preview-pane {
  min-width: 0;
  min-height: 0;
  border: 1px solid #dcdfe6;
  background: #fff;
}

.file-list-pane {
  overflow: hidden;
}

.preview-pane {
  display: flex;
  flex-direction: column;
}

.file-name-button {
  display: inline-flex;
  align-items: center;
  max-width: 100%;
  gap: 8px;
  padding: 0;
  border: 0;
  background: transparent;
  color: #1f2937;
  cursor: pointer;
  font: inherit;
}

.file-name-button:hover .file-name-text {
  color: #1f6feb;
}

.file-name-icon {
  flex: 0 0 auto;
  color: #697586;
}

.file-name-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.preview-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 44px;
  padding: 7px 10px;
  border-bottom: 1px solid #ebeef5;
}

.preview-title {
  display: flex;
  align-items: baseline;
  min-width: 0;
  gap: 8px;
}

.preview-title strong {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #1f2937;
  font-size: 14px;
}

.preview-title span {
  flex: 0 0 auto;
  color: #8a95a3;
  font-size: 12px;
}

.preview-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 1;
  min-height: 320px;
  padding: 24px;
  color: #8a95a3;
}

.preview-empty.is-error {
  color: #b42318;
}

.native-preview-stage,
.generic-preview {
  flex: 1;
  min-height: 0;
}

.native-preview-stage {
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: auto;
  background: #f6f7f9;
}

.native-preview-stage img,
.native-preview-stage video {
  display: block;
  max-width: 100%;
  max-height: 100%;
}

.native-preview-stage video {
  width: 100%;
  height: 100%;
  background: #111827;
}

.selected-path {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #697586;
  font-size: 12px;
}

@media (max-width: 1180px) {
  .file-workbench {
    grid-template-columns: 1fr;
  }

  .preview-pane {
    min-height: 520px;
  }
}
</style>
