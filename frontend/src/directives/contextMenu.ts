import type { ObjectDirective } from 'vue'
import { registerContextMenuSurface, type ContextMenuOptions } from '../utils/contextMenu.ts'

type Handler = (event: MouseEvent) => unknown
type Binding = Handler | ContextMenuOptions | false | undefined
const bindings = new WeakMap<HTMLElement, { value: Binding; dispose: () => void }>()

/**
 * v-context-menu="open" replaces @contextmenu="open" and adds touch long press.
 * .prevent/.stop/.capture preserve Vue event semantics. Value false disables it.
 * Adapter mode: v-context-menu="{ selector: '.el-table__row' }" retains the
 * component's own row-contextmenu/node-contextmenu listener and payload.
 * Mark selectable text descendants data-context-menu-native to keep native hold.
 */
export const contextMenuDirective: ObjectDirective<HTMLElement, Binding> = {
  mounted(element, binding) {
    const state = { value: binding.value, dispose: () => {} }
    bindings.set(element, state)
    const handler = (event: Event) => {
      if (typeof state.value !== 'function') return
      if (binding.modifiers.prevent) event.preventDefault()
      if (binding.modifiers.stop) event.stopPropagation()
      state.value(event as MouseEvent)
    }
    const capture = !!binding.modifiers.capture
    element.addEventListener('contextmenu', handler, capture)
    const unregister = state.value === false ? () => {} : registerContextMenuSurface(element,
      typeof state.value === 'object' ? state.value : {})
    state.dispose = () => {
      unregister()
      element.removeEventListener('contextmenu', handler, capture)
    }
  },
  updated(element, binding) {
    const state = bindings.get(element)
    if (!state) return
    if (typeof state.value === 'function' && typeof binding.value === 'function') {
      state.value = binding.value
      return
    }
    if (state.value === binding.value) return
    if (typeof state.value === 'object' && typeof binding.value === 'object'
      && state.value.selector === binding.value.selector) return
    state.dispose()
    // Rebind when enabled state or a component adapter's selector changes.
    contextMenuDirective.mounted!(element, binding, null as never, null as never)
  },
  beforeUnmount(element) {
    bindings.get(element)?.dispose()
    bindings.delete(element)
  },
}

declare module 'vue' {
  interface GlobalDirectives {
    vContextMenu: typeof contextMenuDirective
  }
}
