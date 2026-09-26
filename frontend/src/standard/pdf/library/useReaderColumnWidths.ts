import { computed, onBeforeUnmount, onMounted, ref, type Ref } from 'vue'

export type ReaderSide = 'toc' | 'outline'
/** Persist preferred widths; temporary window constraints never overwrite them. */
export function useReaderColumnWidths(
  panes: Ref<HTMLElement | undefined>,
  options: () => { toc: number; outline: number; tocVisible: boolean; outlineVisible: boolean; resizeToc: boolean },
  changed: () => void,
) {
  const preferred = ref({ toc: options().toc, outline: options().outline })
  const available = ref(1200)
  const dragging = ref<ReaderSide | null>(null)
  try {
    const saved = JSON.parse(localStorage.getItem('codeyun.reader.column-widths') || '{}')
    for (const side of ['toc', 'outline'] as const) if (Number.isFinite(saved[side]) && saved[side] >= 160) preferred.value[side] = saved[side]
  } catch { /* Defaults also work without storage. */ }
  const actual = computed(() => {
    const o = options()
    const toc = o.tocVisible ? (o.resizeToc ? preferred.value.toc : o.toc) : 0
    const outline = o.outlineVisible ? preferred.value.outline : 0
    const scale = Math.min(1, Math.max(0, available.value - 320) / Math.max(1, toc + outline))
    return { toc: Math.round(toc * scale), outline: Math.round(outline * scale) }
  })
  const limit = (side: ReaderSide) => Math.max(160, available.value - 320 - actual.value[side === 'toc' ? 'outline' : 'toc'])
  function set(side: ReaderSide, value: number) { preferred.value = { ...preferred.value, [side]: Math.round(Math.max(160, Math.min(limit(side), value))) } }
  function save() {
    try { localStorage.setItem('codeyun.reader.column-widths', JSON.stringify(preferred.value)) } catch { /* Session-only preference. */ }
    changed()
  }
  let drag: { side: ReaderSide; x: number; width: number; pointer: number; target: HTMLElement } | null = null
  let observer: ResizeObserver | undefined
  function start(event: PointerEvent, side: ReaderSide) {
    if (event.button !== 0) return
    event.preventDefault()
    const target = event.currentTarget as HTMLElement
    target.setPointerCapture(event.pointerId)
    drag = { side, x: event.clientX, width: actual.value[side], pointer: event.pointerId, target }
    dragging.value = side
  }
  function move(event: PointerEvent) {
    if (drag && event.pointerId === drag.pointer) set(drag.side, drag.width + (event.clientX - drag.x) * (drag.side === 'toc' ? 1 : -1))
  }
  function finish() {
    const previous = drag
    drag = null; dragging.value = null
    if (previous?.target.hasPointerCapture(previous.pointer)) previous.target.releasePointerCapture(previous.pointer)
    if (previous) save()
  }
  function reset(side: ReaderSide) { set(side, options()[side]); save() }
  function key(event: KeyboardEvent, side: ReaderSide) {
    if (!['ArrowLeft', 'ArrowRight', 'Home'].includes(event.key)) return
    event.preventDefault()
    if (event.key === 'Home') reset(side)
    else { set(side, actual.value[side] + (event.key === 'ArrowRight' ? 20 : -20) * (side === 'toc' ? 1 : -1)); save() }
  }
  onMounted(() => {
    if (!panes.value) return
    observer = new ResizeObserver(([entry]) => { if (entry) available.value = entry.contentRect.width })
    observer.observe(panes.value)
  })
  onBeforeUnmount(() => { finish(); observer?.disconnect() })
  return { actual, dragging, limit, start, move, finish, reset, key }
}
