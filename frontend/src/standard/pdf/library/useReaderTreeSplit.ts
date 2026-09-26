import { ref, watch } from 'vue'

export function useReaderTreeSplit(bookKey: () => string) {
  const outlineLevel = ref(0)
  watch(bookKey, key => {
    try {
      const saved = Number(localStorage.getItem(`codeyun.reader.outline-level.${key}`))
      outlineLevel.value = [1, 2, 3, 4].includes(saved) ? saved : 0
    } catch { outlineLevel.value = 0 }
  }, { immediate: true })
  function setOutlineLevel(level: number) {
    outlineLevel.value = level
    try { localStorage.setItem(`codeyun.reader.outline-level.${bookKey()}`, String(level)) } catch { /* Session-only setting. */ }
  }
  return { outlineLevel, setOutlineLevel }
}
