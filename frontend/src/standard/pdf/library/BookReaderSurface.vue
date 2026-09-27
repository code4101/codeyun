<script setup lang="ts">
import { ElDialog } from 'element-plus'
import { inject } from 'vue'
import { readerTabContext } from './readerWorkspaceContext'
import './readerPreview.css'

defineOptions({ inheritAttrs: false })
defineProps<{ modelValue: boolean; standalone?: boolean; pageHref: string; headerless?: boolean }>()
defineEmits<{ 'update:modelValue': [value: boolean] }>()
const embedded = inject(readerTabContext, null)
</script>

<template>
  <section v-if="embedded" class="reader-embedded"><slot /></section>
  <section v-else-if="standalone" v-bind="$attrs" class="book-reader-page">
    <header v-if="!headerless" class="el-dialog__header"><slot name="header" /></header>
    <main class="el-dialog__body"><slot /></main>
  </section>
  <ElDialog v-else v-bind="$attrs" class="book-reader-dialog reader-preview-shell" :class="{ 'is-headerless': headerless }" :show-close="!headerless" align-center :model-value="modelValue" @update:model-value="$emit('update:modelValue', $event)">
    <template #header>
      <div class="reader-dialog-heading">
        <div class="reader-dialog-title"><slot name="header" /></div>
        <a v-if="pageHref" class="reader-page-link" :href="pageHref" target="_blank" rel="noopener">独立页面打开 ↗</a>
      </div>
    </template>
    <slot />
  </ElDialog>
</template>

<style scoped>
.reader-embedded { display: flex; flex-direction: column; width: 100%; height: 100%; min-width: 0; min-height: 0; overflow: hidden; }
/* 工作区贴边；标题栏自行负责留白，避免活动栏再套一圈内边距。 */
:global(.el-dialog.book-reader-dialog.reader-preview-shell) { display: flex; flex-direction: column; height: calc(100dvh - 48px); padding: 0; overflow: hidden; }
:global(.el-dialog.book-reader-dialog > .el-dialog__header) { flex: none; padding: 16px 16px 12px; }
:global(.el-dialog.book-reader-dialog.is-headerless > .el-dialog__header) { display: none; }
:global(.book-reader-dialog > .el-dialog__body) { flex: 1; min-height: 0; overflow: hidden; }
.book-reader-page {
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
  width: 100% !important;
  height: 100% !important;
  min-width: 0 !important;
  min-height: 0 !important;
  max-width: none !important;
  max-height: none !important;
  padding: 0;
  resize: none !important;
  overflow: hidden;
  background: var(--reader-content, #fff);
}
.book-reader-page::after { display: none; }
.book-reader-page > header { flex: none; padding: 16px 16px 12px; }
.book-reader-page > header :deep(.reader-window-heading) { display: flex; align-items: center; justify-content: space-between; min-height: 28px; gap: 16px; padding: 0; }
.book-reader-page > main { flex: 1; min-height: 0; overflow: hidden; padding: 0; }
.reader-dialog-heading { display: flex; align-items: center; gap: 12px; padding-right: 44px; }
.reader-dialog-title { flex: 1; min-width: 0; }
.reader-dialog-heading :deep(.reader-window-heading) { padding-right: 0; }
.reader-page-link { flex: none; white-space: nowrap; color: var(--el-color-primary); font-size: 13px; line-height: 32px; }
</style>
