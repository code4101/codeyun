import { computed, ref, watch } from 'vue'

import './readerTheme.css'

export type LibraryReaderTheme = 'standard' | 'eye-care' | 'dark'

export const LIBRARY_READER_THEME_OPTIONS: Array<{
  value: LibraryReaderTheme
  label: string
}> = [
  { value: 'standard', label: '标准' },
  { value: 'eye-care', label: '护眼' },
  { value: 'dark', label: '深色' },
]

const LIBRARY_READER_THEME_STORAGE_KEY = 'codeyun.library.reader-theme'
const LEGACY_PDF_PREVIEW_THEME_STORAGE_KEY = 'codeyun.pdf-library.preview-theme'

function loadLibraryReaderTheme(): LibraryReaderTheme {
  try {
    const storedTheme = window.localStorage.getItem(LIBRARY_READER_THEME_STORAGE_KEY)
      || window.localStorage.getItem(LEGACY_PDF_PREVIEW_THEME_STORAGE_KEY)
    if (storedTheme === 'eye-care' || storedTheme === 'dark') {
      return storedTheme
    }
  } catch {
    // 本地存储不可用时回退到标准主题。
  }
  return 'standard'
}

export const libraryReaderTheme = ref<LibraryReaderTheme>(loadLibraryReaderTheme())
export const libraryReaderThemeClass = computed(() => `is-reader-theme-${libraryReaderTheme.value}`)

watch(libraryReaderTheme, (theme) => {
  try {
    window.localStorage.setItem(LIBRARY_READER_THEME_STORAGE_KEY, theme)
  } catch {
    // 浏览器禁用本地存储时，主题仍可在本次页面会话中使用。
  }
}, { immediate: true })
