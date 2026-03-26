import { nextTick, onUnmounted, watch, type Ref } from 'vue'
import Sortable from 'sortablejs'

export interface UseSortableListOptions {
  listRef: Ref<HTMLElement | null>
  getDeps: () => readonly unknown[]
  isEnabled?: () => boolean
  handle?: string
  animation?: number
  ghostClass?: string
  onReorder: (oldIndex: number, newIndex: number) => void | Promise<void>
}

export function useSortableList(options: UseSortableListOptions) {
  let sortable: Sortable | null = null

  const destroySortable = () => {
    if (!sortable) {
      return
    }
    sortable.destroy()
    sortable = null
  }

  const initSortable = () => {
    destroySortable()
    if (!options.listRef.value) {
      return
    }
    if (!(options.isEnabled?.() ?? true)) {
      return
    }

    sortable = Sortable.create(options.listRef.value, {
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
      initSortable()
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
