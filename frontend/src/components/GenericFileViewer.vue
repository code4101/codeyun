<template>
  <div class="generic-file-viewer">
    <FileViewer
      v-if="fileBlob"
      :key="viewerKey"
      class="generic-file-viewer__surface"
      :file="fileBlob"
      :filename="filename"
      :name="filename"
      :type="mimeType"
      :size="size"
      :options="viewerOptions"
    />
    <div v-else class="generic-file-viewer__empty">选择一个文件后预览</div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { FileViewer, type FileViewerOptions } from '@file-viewer/vue3';
import officePreset from '@file-viewer/preset-office';
import litePreset from '@file-viewer/preset-lite';
import archiveRenderer from '@file-viewer/renderer-archive';
import emailRenderer from '@file-viewer/renderer-email';
import '@file-viewer/vue3/dist/vue3.css';

const props = withDefaults(defineProps<{
  fileBlob: Blob | null;
  filename: string;
  mimeType?: string;
  size?: number;
}>(), {
  mimeType: '',
  size: undefined,
});

const viewerKey = computed(() => [
  props.filename,
  props.mimeType,
  props.size ?? '',
].join(':'));

const viewerOptions = computed<FileViewerOptions>(() => ({
  preset: [litePreset, officePreset],
  renderers: [archiveRenderer, emailRenderer],
  rendererMode: 'replace',
  theme: 'light',
  toolbar: {
    position: 'bottom-right',
    download: true,
    print: true,
    exportHtml: true,
    zoom: true,
  },
  search: {
    maxMatches: 1000,
    caseSensitive: false,
  },
  archive: {
    cache: true,
    workerTimeoutMs: 30000,
  },
  pdf: {
    toolbar: false,
    streaming: 'same-origin',
  },
}));
</script>

<style scoped>
.generic-file-viewer {
  box-sizing: border-box;
  width: 100%;
  height: 100%;
  min-height: 0;
  background: #fff;
}

.generic-file-viewer__surface {
  width: 100%;
  height: 100%;
  min-height: 0;
}

.generic-file-viewer__empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  min-height: 320px;
  color: #8a95a3;
  font-size: 14px;
}
</style>
