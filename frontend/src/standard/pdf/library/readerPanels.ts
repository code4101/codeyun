import { ref, watch } from 'vue'

/**
 * 图书馆阅读器三栏（目录 / 正文 / 大纲）中左右两栏的显隐状态。
 *
 * 为什么是模块级状态而不是组件内部状态：
 * - 「要不要看侧栏」是纯阅读偏好，与具体是哪本书无关，因此电子书阅读器与
 *   Skill 手册阅读器共用同一份状态，一处切换另一处同样生效。
 * - 主题（readerTheme）只决定配色，这里只决定哪些栏参与网格布局，二者正交。
 */
export const READER_PANEL_STORAGE_KEY = 'codeyun.library.reader-panels.v1'

interface ReaderPanelVisibility {
  /** 左栏：目录 / 全文搜索 */
  toc: boolean
  /** 右栏：本章大纲 */
  outline: boolean
}

function loadReaderPanelVisibility(): ReaderPanelVisibility {
  const fallback: ReaderPanelVisibility = { toc: true, outline: true }
  if (typeof window === 'undefined') return fallback
  try {
    const stored = JSON.parse(
      window.localStorage.getItem(READER_PANEL_STORAGE_KEY) || 'null',
    ) as Partial<ReaderPanelVisibility> | null
    // 只有显式存过的 false 才算隐藏，缺字段或历史格式都回落到「显示」。
    return { toc: stored?.toc !== false, outline: stored?.outline !== false }
  } catch {
    return fallback
  }
}

const storedPanelVisibility = loadReaderPanelVisibility()
export const readerTocVisible = ref(storedPanelVisibility.toc)
export const readerOutlineVisible = ref(storedPanelVisibility.outline)

watch([readerTocVisible, readerOutlineVisible], ([toc, outline]) => {
  try {
    window.localStorage.setItem(READER_PANEL_STORAGE_KEY, JSON.stringify({ toc, outline }))
  } catch {
    // 浏览器禁用本地存储时，本次会话内仍可正常切换。
  }
})

export function toggleReaderPanel(panel: keyof ReaderPanelVisibility) {
  if (panel === 'toc') readerTocVisible.value = !readerTocVisible.value
  else readerOutlineVisible.value = !readerOutlineVisible.value
}
