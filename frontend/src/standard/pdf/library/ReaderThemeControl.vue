<script setup lang="ts">
import {
  LIBRARY_READER_THEME_OPTIONS,
  libraryReaderTheme,
} from './readerTheme'
</script>

<template>
  <div class="reader-theme-control" aria-label="阅读主题">
    <span>阅读主题</span>
    <el-radio-group v-model="libraryReaderTheme" size="small">
      <el-radio-button
        v-for="theme in LIBRARY_READER_THEME_OPTIONS"
        :key="theme.value"
        :value="theme.value"
      >
        {{ theme.label }}
      </el-radio-button>
    </el-radio-group>
  </div>
</template>

<style scoped>
.reader-theme-control {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 8px;
}

.reader-theme-control > span {
  flex: 0 0 auto;
  color: var(--reader-muted, #657286);
  font-size: 12px;
}
</style>
