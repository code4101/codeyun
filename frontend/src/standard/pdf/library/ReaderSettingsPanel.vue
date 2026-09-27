<script setup lang="ts">
import { computed, ref } from 'vue'
import SettingsBranch from '@/components/settings/SettingsBranch.vue'
import { filterSettings, type SettingNode } from '@/components/settings/settingsTree'
import { libraryReaderTheme, LIBRARY_READER_THEME_OPTIONS, type LibraryReaderTheme } from './readerTheme'

const props = defineProps<{ fontSize?: number; sections?: readonly SettingNode[] }>()
const emit = defineEmits<{ fontSize: [value: number] }>()
const query = ref('')
// 共用配置树只收录功能偏好；布局通过活动栏、拖拽和右键菜单直接操作。
const nodes = computed<SettingNode[]>(() => [
  { id: 'appearance', kind: 'group', label: '阅读外观', description: '主题应用于所有阅读器。', children: [
    { id: 'theme', kind: 'select', label: '主题', options: LIBRARY_READER_THEME_OPTIONS,
      read: () => libraryReaderTheme.value, write: value => { libraryReaderTheme.value = value as LibraryReaderTheme } },
    ...(props.fontSize === undefined ? [] : [{ id: 'fontSize', kind: 'number' as const, label: '字号（px）', min: 12, max: 24,
      read: () => props.fontSize!, write: (value: number) => emit('fontSize', value) }]),
  ] },
  ...(props.sections ?? []),
])
const filtered = computed(() => filterSettings(nodes.value, query.value))
</script>
<template>
  <section class="reader-settings">
    <input v-model="query" type="search" aria-label="搜索配置" placeholder="搜索配置">
    <SettingsBranch :key="query.trim()" :nodes="filtered" />
    <p v-if="!filtered.length">没有匹配的配置</p>
  </section>
</template>
<style scoped>
.reader-settings { padding: 8px; overflow: auto; height: 100%; box-sizing: border-box; font-size: 12px; color: var(--reader-text); }
.reader-settings > input { box-sizing: border-box; width: 100%; margin-bottom: 10px; padding: 6px 8px; border: 1px solid var(--reader-border, #aaa); border-radius: 4px; font: inherit; background: var(--reader-content); color: inherit; }
</style>
