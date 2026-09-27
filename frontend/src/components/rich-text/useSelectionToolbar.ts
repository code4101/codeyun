import { nextTick, onBeforeUnmount, onMounted, ref, watch, type Ref } from 'vue'
import { selectionToolbarPosition } from './selectionToolbarPosition.ts'

/** Selection UI is independent of contextmenu: mouse, keyboard and native touch
 * handles all update Selection. Never prevent touch selection or synthesize it. */
export function useSelectionToolbar(
  root: Ref<HTMLElement | null>, toolbar: Ref<HTMLElement | null>,
  visible: () => boolean, refresh: () => unknown,
) {
  const style = ref({ left: '0px', top: '0px', visibility: 'hidden' as 'hidden' | 'visible' })
  let timer: ReturnType<typeof setTimeout> | undefined
  let disposed = false
  let touchUntil = 0
  const removeListeners: Array<() => void> = []

  function position() {
    const element = root.value
    const menu = toolbar.value
    const selection = window.getSelection()
    if (!element || !menu || !visible() || !selection?.rangeCount || selection.isCollapsed) {
      style.value.visibility = 'hidden'
      return
    }
    const range = selection.getRangeAt(0)
    if (!element.contains(range.commonAncestorContainer)) { style.value.visibility = 'hidden'; return }
    const readerStyle = getComputedStyle(element)
    for (const name of ['--reader-border', '--reader-surface', '--reader-text', '--reader-hover', '--reader-active-text']) {
      menu.style.setProperty(name, readerStyle.getPropertyValue(name))
    }
    const vv = window.visualViewport
    const viewport = {
      left: vv?.offsetLeft ?? 0, top: vv?.offsetTop ?? 0,
      right: (vv?.offsetLeft ?? 0) + (vv?.width ?? window.innerWidth),
      bottom: (vv?.offsetTop ?? 0) + (vv?.height ?? window.innerHeight),
    }
    // A page can be scrolled, column-clipped or docked in a narrow pane. Only
    // anchor to the portion of text that the reader can actually see.
    for (let node: HTMLElement | null = element; node; node = node.parentElement) {
      const css = getComputedStyle(node)
      const bounds = node.getBoundingClientRect()
      if (/(auto|scroll|hidden|clip)/.test(css.overflowX)) {
        viewport.left = Math.max(viewport.left, bounds.left)
        viewport.right = Math.min(viewport.right, bounds.right)
      }
      if (/(auto|scroll|hidden|clip)/.test(css.overflowY)) {
        viewport.top = Math.max(viewport.top, bounds.top)
        viewport.bottom = Math.min(viewport.bottom, bounds.bottom)
      }
    }
    const backwards = selection.focusNode === range.startContainer && selection.focusOffset === range.startOffset
    const point = selectionToolbarPosition(Array.from(range.getClientRects()), viewport, menu.getBoundingClientRect(), backwards)
    style.value = point
      ? { left: `${point.left}px`, top: `${point.top}px`, visibility: 'visible' }
      : { ...style.value, visibility: 'hidden' }
  }

  async function update() {
    if (disposed) return
    refresh()
    await nextTick()
    if (!disposed) position()
  }
  function schedule(delay = 0) {
    clearTimeout(timer)
    timer = setTimeout(() => { void update() }, delay)
  }
  function completed(event: Event) {
    if (event.target instanceof Node && toolbar.value?.contains(event.target)) return
    schedule()
  }
  function listen(target: EventTarget, name: string, handler: (event: any) => void, capture = false) {
    target.addEventListener(name, handler, { capture, passive: true })
    removeListeners.push(() => target.removeEventListener(name, handler, capture))
  }
  onMounted(() => {
    listen(document, 'selectionchange', () => schedule(100))
    listen(document, 'pointerdown', (event: PointerEvent) => {
      if (event.pointerType === 'touch') touchUntil = Date.now() + 1000
    }, true)
    listen(document, 'touchstart', () => { touchUntil = Date.now() + 1000 }, true)
    listen(document, 'touchend', event => { touchUntil = Date.now() + 1000; completed(event) })
    listen(document, 'pointerup', completed)
    listen(document, 'keyup', completed)
    listen(document, 'scroll', position, true)
    listen(window, 'resize', position)
    if (window.visualViewport) {
      listen(window.visualViewport, 'resize', position)
      listen(window.visualViewport, 'scroll', position)
    }
  })
  watch(visible, () => { void nextTick(() => { if (!disposed) position() }) })
  onBeforeUnmount(() => {
    disposed = true
    clearTimeout(timer)
    removeListeners.forEach(remove => remove())
  })
  return {
    style,
    // OCR's desktop drag recognizer must not clear a native touch selection
    // when Safari sends compatibility mouse events after releasing a finger.
    isTouchMouseEvent: () => Date.now() < touchUntil,
  }
}
