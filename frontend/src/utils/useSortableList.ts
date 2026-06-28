import { nextTick, onUnmounted, watch, type Ref } from 'vue'
import type Sortable from 'sortablejs'

export interface UseSortableListOptions {
  listRef: Ref<HTMLElement | null>
  getDeps: () => readonly unknown[]
  isEnabled?: () => boolean
  handle?: string
  animation?: number
  ghostClass?: string
  onReorder: (oldIndex: number, newIndex: number) => void | Promise<void>
}

let sortableModulePromise: Promise<typeof import('sortablejs')> | null = null

const loadSortableModule = () => {
  sortableModulePromise ||= import('sortablejs')
  return sortableModulePromise
}

export function useSortableList(options: UseSortableListOptions) {
  let sortable: Sortable | null = null
  let initNonce = 0

  const disposeSortable = () => {
    if (!sortable) {
      return
    }
    sortable.destroy()
    sortable = null
  }

  const destroySortable = () => {
    initNonce += 1
    disposeSortable()
  }

  const initSortable = async () => {
    const nonce = ++initNonce
    disposeSortable()
    if (!options.listRef.value) {
      return
    }
    if (!(options.isEnabled?.() ?? true)) {
      return
    }

    const { default: SortableCtor } = await loadSortableModule()
    if (nonce !== initNonce || !options.listRef.value || !(options.isEnabled?.() ?? true)) {
      return
    }

    sortable = SortableCtor.create(options.listRef.value, {
      handle: options.handle ?? '.sortable-order-handle',
      animation: options.animation ?? 150,
      ghostClass: options.ghostClass,
      onEnd: ({ oldIndex, newIndex }) => {
        if (oldIndex == null || newIndex == null || oldIndex === newIndex) {
          return
        }
        void options.onReorder(oldIndex, newIndex)
      },
    })
  }

  watch(
    () => options.getDeps(),
    async () => {
      await nextTick()
      void initSortable()
    },
    { immediate: true },
  )

  onUnmounted(() => {
    destroySortable()
  })

  return {
    initSortable,
    destroySortable,
  }
}
