import { ref } from 'vue'

export const noteTypePaletteItemsState = ref<Record<string, unknown>>({})
export const noteTypePaletteLoadedState = ref(false)

let noteTypePaletteLoadPromiseState: Promise<unknown[]> | null = null

export const getNoteTypePaletteLoadPromiseState = () => noteTypePaletteLoadPromiseState

export const setNoteTypePaletteLoadPromiseState = (value: Promise<unknown[]> | null) => {
  noteTypePaletteLoadPromiseState = value
}

export const resetNoteTypePaletteState = () => {
  noteTypePaletteItemsState.value = {}
  noteTypePaletteLoadedState.value = false
  noteTypePaletteLoadPromiseState = null
}
