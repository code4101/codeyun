/**
 * Explicitly registered context surfaces share one touch recognizer per document.
 * A long press dispatches contextmenu on the original target, preserving capture,
 * bubbling, tree row resolution and spreadsheet selection in existing adapters.
 * It does not simulate mousedown/up or grant browser user-activation privileges.
 */
export interface ContextMenuOptions {
  /** Restrict a component adapter to its actual rows/cells. */
  selector?: string
}

export const CONTEXT_MENU_HOLD_MS = 500
export const CONTEXT_MENU_MOVE_PX = 10
const SURFACE = 'data-context-menu-surface'
const NATIVE = 'input,textarea,select,[contenteditable]:not([contenteditable="false"]),[data-context-menu-native]'

interface Press {
  id: number
  target: Element
  owner: Element
  x: number
  y: number
  timer?: ReturnType<typeof setTimeout>
  fired: boolean
}

/** Public registration API for Vue and third-party control adapters. */
export function registerContextMenuSurface(element: HTMLElement, options: ContextMenuOptions = {}): () => void {
  const doc = element.ownerDocument
  let manager = managers.get(doc)
  if (!manager) {
    manager = createManager(doc)
    managers.set(doc, manager)
  }
  return manager.register(element, options)
}

const managers = new WeakMap<Document, ReturnType<typeof createManager>>()

function createManager(doc: Document) {
  const win = doc.defaultView!
  const surfaces = new Map<Element, ContextMenuOptions>()
  const touches = new Set<number>()
  let press: Press | undefined
  let recent: { target: Element; x: number; y: number; expires: number } | undefined
  let emitting = false
  let cleanupTimer: ReturnType<typeof setTimeout> | undefined
  const listeners: Array<() => void> = []

  function listen(target: EventTarget, name: string, handler: (event: any) => void, passive = true) {
    target.addEventListener(name, handler, { capture: true, passive })
    listeners.push(() => target.removeEventListener(name, handler, true))
  }
  function cancel() {
    if (press?.timer !== undefined) clearTimeout(press.timer)
    if (press?.fired && recent) recent.expires = Date.now() + 800
    press = undefined
  }
  function disposeIfUnused() {
    if (surfaces.size || touches.size) return
    if (cleanupTimer !== undefined) clearTimeout(cleanupTimer)
    if (recent && recent.expires > Date.now()) {
      cleanupTimer = setTimeout(disposeIfUnused, recent.expires - Date.now() + 1)
      return
    }
    listeners.forEach(remove => remove())
    managers.delete(doc)
  }
  function reset() { cancel(); recent = undefined; touches.clear(); disposeIfUnused() }
  function targetOf(event: Event): Element | undefined {
    return event.composedPath().find((node): node is Element => node instanceof win.Element)
  }
  function ownerOf(target: Element): Element | undefined {
    if (target.closest(NATIVE) || target.closest(':disabled,[aria-disabled="true"]')) return
    for (let node: Element | null = target; node; node = node.parentElement) {
      const options = surfaces.get(node)
      if (!options) continue
      // An explicitly narrower child surface owns its region, including exclusions.
      const match = options.selector ? target.closest(options.selector) : target
      return match && node.contains(match) ? node : undefined
    }
  }
  function down(event: PointerEvent) {
    if (event.pointerType !== 'touch') { reset(); return }
    touches.add(event.pointerId)
    cancel()
    recent = undefined
    if (touches.size !== 1 || !event.isPrimary) return
    const target = targetOf(event)
    const owner = target && ownerOf(target)
    if (!target || !owner) return
    const current: Press = { id: event.pointerId, target, owner, x: event.clientX, y: event.clientY, fired: false }
    press = current
    current.timer = setTimeout(() => {
      current.timer = undefined
      if (press !== current || !target.isConnected || ownerOf(target) !== owner) return cancel()
      current.fired = true
      recent = { target, x: current.x, y: current.y, expires: Infinity }
      emitting = true
      try {
        target.dispatchEvent(new win.MouseEvent('contextmenu', {
          bubbles: true, cancelable: true, composed: true, view: win,
          button: 2, buttons: 2, clientX: current.x, clientY: current.y,
        }))
      } finally { emitting = false }
    }, CONTEXT_MENU_HOLD_MS)
  }
  function move(event: PointerEvent) {
    if (press?.id !== event.pointerId || press.fired) return
    if (Math.hypot(event.clientX - press.x, event.clientY - press.y) > CONTEXT_MENU_MOVE_PX) cancel()
  }
  function up(event: PointerEvent) {
    touches.delete(event.pointerId)
    if (press?.id === event.pointerId) cancel()
    disposeIfUnused()
  }
  function matchesRecent(event: MouseEvent) {
    if (!recent || Date.now() > recent.expires) return false
    const target = targetOf(event)
    return target === recent.target || !!(target && recent.target.contains(target))
      || Math.hypot(event.clientX - recent.x, event.clientY - recent.y) <= CONTEXT_MENU_MOVE_PX
  }
  function context(event: MouseEvent) {
    if (emitting) return
    if (matchesRecent(event)) { event.preventDefault(); event.stopImmediatePropagation(); return }
    // Some engines emit a native contextmenu before our timer. Let it open once.
    if (press && !press.fired && ownerOf(press.target)) {
      if (press.timer !== undefined) clearTimeout(press.timer)
      press.fired = true
      recent = { target: press.target, x: press.x, y: press.y, expires: Infinity }
    }
  }
  function click(event: MouseEvent) {
    if (event.detail === 0 || !matchesRecent(event)) return
    event.preventDefault()
    event.stopImmediatePropagation()
  }
  listen(doc, 'pointerdown', down)
  listen(doc, 'pointermove', move)
  listen(doc, 'pointerup', up)
  listen(doc, 'pointercancel', up)
  listen(doc, 'contextmenu', context, false)
  listen(doc, 'click', click, false)
  listen(doc, 'touchend', (event: TouchEvent) => {
    // Cancel only the completed long press's compatibility click, never a scroll.
    if (recent && Date.now() <= recent.expires && event.cancelable) event.preventDefault()
  }, false)
  listen(doc, 'scroll', cancel)
  listen(doc, 'dragstart', cancel)
  listen(win, 'blur', reset)
  listen(doc, 'visibilitychange', reset)

  return {
    register(element: HTMLElement, options: ContextMenuOptions) {
      let disposed = false
      const oldAttribute = element.getAttribute(SURFACE)
      surfaces.set(element, options)
      element.setAttribute(SURFACE, '')
      return () => {
        if (disposed) return
        disposed = true
        surfaces.delete(element)
        if (oldAttribute === null) element.removeAttribute(SURFACE)
        else element.setAttribute(SURFACE, oldAttribute)
        if (press?.owner === element) cancel()
        disposeIfUnused()
      }
    },
  }
}
