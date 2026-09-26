<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref } from 'vue'
const props = defineProps<{ modelValue: number }>()
const emit = defineEmits<{ 'update:modelValue': [level: number] }>()
const trigger = ref<HTMLButtonElement>()
const submenu = ref<HTMLElement>()
const opened = ref(false)
const position = ref({ left: '0px', top: '0px' })
let timer: ReturnType<typeof setTimeout> | undefined
function cancelClose() { clearTimeout(timer) }
async function open(focus = false) {
  cancelClose()
  const rect = trigger.value?.getBoundingClientRect()
  if (!rect) return
  opened.value = true
  await nextTick()
  const width = submenu.value?.offsetWidth ?? 156
  const height = submenu.value?.offsetHeight ?? 180
  position.value = {
    left: `${Math.max(4, rect.right + width + 4 <= innerWidth ? rect.right + 4 : rect.left - width - 4)}px`,
    top: `${Math.max(4, Math.min(rect.top, innerHeight - height - 4))}px`,
  }
  if (focus) submenu.value?.querySelector<HTMLElement>('[aria-checked="true"]')?.focus()
}
function closeLater() { timer = setTimeout(() => { opened.value = false }, 120) }
function close() { cancelClose(); opened.value = false; trigger.value?.focus() }
function select(level: number) { opened.value = false; emit('update:modelValue', level) }
function key(event: KeyboardEvent) {
  if (event.key === 'Escape' || event.key === 'ArrowLeft') { event.preventDefault(); close(); return }
  if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
    event.preventDefault()
    const buttons = [...(submenu.value?.querySelectorAll<HTMLButtonElement>('button') ?? [])]
    const index = buttons.indexOf(document.activeElement as HTMLButtonElement)
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length
    buttons[next]?.focus()
  }
}
onBeforeUnmount(cancelClose)
</script>
<template>
  <div class="outline-level-options" @mouseenter="open()" @mouseleave="closeLater" @keydown.stop>
    <button ref="trigger" type="button" class="outline-level-trigger" role="menuitem" aria-haspopup="menu" :aria-expanded="opened" @click="open(true)" @keydown.right.prevent="open(true)" @keydown.esc.prevent="close">
      大纲起始层级<span aria-hidden="true">›</span>
    </button>
    <div v-if="opened" ref="submenu" class="outline-level-submenu" role="menu" aria-label="大纲起始层级" :style="position" @mouseenter="cancelClose" @keydown.stop="key">
      <button v-for="level in [0, 1, 2, 3, 4]" :key="level" type="button" role="menuitemradio" :aria-checked="props.modelValue === level" @click="select(level)">
        <span aria-hidden="true">{{ props.modelValue === level ? '●' : '○' }}</span>{{ level ? `${level} 级` : '自动' }}
      </button>
    </div>
  </div>
</template>
<style scoped>
.outline-level-options { border-top: 1px solid var(--reader-border, #ddd); padding: 4px 0 0; }
.outline-level-submenu { position: fixed; z-index: 10001; width: 156px; padding: 4px; border: 1px solid var(--reader-border, #ddd); border-radius: 5px; background: var(--reader-surface, #fff); box-shadow: 0 4px 18px #0002; }
button { display: flex; align-items: center; gap: 10px; width: 100%; border: 0; background: transparent; padding: 7px 10px; color: var(--reader-text, #273447); text-align: left; cursor: pointer; }
.outline-level-trigger { justify-content: space-between; }
button:hover, button:focus-visible { background: var(--reader-hover, #edf2f7); }
</style>
