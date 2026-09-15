<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import { ArrowLeft, ArrowRight } from '@element-plus/icons-vue'

/**
 * 栏分割线上的折叠手柄。
 *
 * 为什么不做成标题栏开关：折叠是「栏」自己的属性，入口就应该长在它的分割线上，
 * 平时收起不占视觉重量，鼠标移到分割线（或键盘 Tab 聚焦）时才出现。
 * 手柄所在的窄带永远在正文滚动视口之外，所以不会挡住正文滚动条。
 * 热区向分割线两侧都留容错，离开时延迟收起，避免鼠标擦边时按钮闪烁。
 */
defineProps<{
  side: 'left' | 'right'
  collapsed: boolean
  /** 栏名，用于生成「折叠目录 / 展开大纲」这类无障碍文案 */
  label: string
}>()

const emit = defineEmits<{ toggle: [] }>()

/** 手柄按钮沿分割线的长度（与 .reader-column-handle-button 的 46px 一致）。 */
const HANDLE_BUTTON_LENGTH = 46
/** 离开分割线后延迟收起的毫秒数：容错鼠标的抖动与擦边。 */
const HANDLE_HIDE_DELAY = 150

const stripRef = ref<HTMLElement | null>(null)
/** 手柄相对分割线中点的偏移：鼠标在线段哪个高度/横向位置出现，按钮就跟到哪。 */
const handleShift = ref(0)
const handleVisible = ref(false)
let hideTimer: ReturnType<typeof setTimeout> | null = null

function showHandle() {
  if (hideTimer) {
    clearTimeout(hideTimer)
    hideTimer = null
  }
  handleVisible.value = true
}

function scheduleHideHandle() {
  if (hideTimer) clearTimeout(hideTimer)
  hideTimer = setTimeout(() => {
    hideTimer = null
    handleVisible.value = false
  }, HANDLE_HIDE_DELAY)
}

onBeforeUnmount(() => {
  if (hideTimer) clearTimeout(hideTimer)
})

/**
 * 整条分割线都是热区：竖向分割线只看 x 是否靠近、不看 y，横向分割线只看 y。
 * 按钮落点取鼠标在线段上的投影位置并夹在线段内，点下去折叠的就是当前这条栏。
 */
function trackPointer(event: PointerEvent) {
  showHandle()
  const strip = stripRef.value
  if (!strip) return
  const bounds = strip.getBoundingClientRect()
  const vertical = bounds.height >= bounds.width
  const axisLength = vertical ? bounds.height : bounds.width
  const pointer = vertical ? event.clientY - bounds.top : event.clientX - bounds.left
  const limit = Math.max(0, (axisLength - HANDLE_BUTTON_LENGTH) / 2)
  handleShift.value = Math.max(-limit, Math.min(limit, pointer - axisLength / 2))
}

function resetHandleShift() {
  handleShift.value = 0
}
</script>

<template>
  <div
    ref="stripRef"
    class="reader-column-handle"
    :class="[side === 'left' ? 'is-left' : 'is-right', { 'is-visible': handleVisible }]"
    @pointerenter="trackPointer"
    @pointermove="trackPointer"
    @pointerleave="scheduleHideHandle"
    @focusin="showHandle"
    @focusout="scheduleHideHandle"
  >
    <button
      type="button"
      class="reader-column-handle-button"
      :style="{ '--reader-handle-shift': `${handleShift}px` }"
      :aria-expanded="!collapsed"
      :aria-label="`${collapsed ? '展开' : '折叠'}${label}`"
      :title="`${collapsed ? '展开' : '折叠'}${label}`"
      @focus="resetHandleShift"
      @click="emit('toggle')"
    >
      <el-icon>
        <ArrowRight v-if="collapsed === (side === 'left')" />
        <ArrowLeft v-else />
      </el-icon>
    </button>
  </div>
</template>

<style scoped>
/* 手柄热区横跨分割线两侧：向侧栏侧最多压进它的内边距（12px），向正文侧用满
   正文让出的窄带；两侧都停在滚动条之外，所以既好悬停，也不会挡住滚动条。 */
.reader-column-handle {
  position: absolute;
  top: 0;
  bottom: 0;
  z-index: 3;
  box-sizing: border-box;
  display: flex;
  width: calc(var(--reader-handle-reach, 12px) + var(--reader-gutter, 20px));
  align-items: center;
}

.reader-column-handle.is-left {
  left: calc(var(--reader-handle-reach, 12px) * -1);
  padding-left: var(--reader-handle-reach, 12px);
  justify-content: flex-start;
}

.reader-column-handle.is-right {
  right: calc(var(--reader-handle-reach, 12px) * -1);
  padding-right: var(--reader-handle-reach, 12px);
  justify-content: flex-end;
}

.reader-column-handle-button {
  display: inline-flex;
  width: var(--reader-handle-size, 14px);
  height: 46px;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--reader-border, #e4e9ef);
  border-radius: 7px;
  background: var(--reader-surface, #fff);
  padding: 0;
  color: var(--reader-muted, #657286);
  font-size: 12px;
  cursor: pointer;
  opacity: 0;
  /* 默认停在分割线中点，随鼠标在线段上的位置移动。 */
  transform: translateY(var(--reader-handle-shift, 0px));
  transition: opacity 120ms ease, transform 90ms ease-out;
}

.reader-column-handle:hover .reader-column-handle-button,
.reader-column-handle.is-visible .reader-column-handle-button,
.reader-column-handle:focus-within .reader-column-handle-button {
  opacity: 1;
}

.reader-column-handle-button:hover {
  border-color: var(--reader-active-text, #1f5fbe);
  color: var(--reader-active-text, #1f5fbe);
}

/* 窄屏三栏改为上下行布局，分割线变成水平线：手柄跟着横过来，箭头指向上下。 */
@media (max-width: 980px) {
  .reader-column-handle.is-left,
  .reader-column-handle.is-right {
    left: 0;
    right: 0;
    width: auto;
    height: calc(var(--reader-handle-reach, 12px) + var(--reader-gutter, 20px));
    padding-right: 0;
    padding-left: 0;
    justify-content: center;
  }

  .reader-column-handle.is-left {
    top: calc((var(--reader-handle-reach, 12px) + var(--reader-gutter, 20px)) / -2);
    bottom: auto;
  }

  .reader-column-handle.is-right {
    top: auto;
    bottom: calc((var(--reader-handle-reach, 12px) + var(--reader-gutter, 20px)) / -2);
  }

  .reader-column-handle-button {
    /* 按钮仍是 14×46 的竖长胶囊，旋转 90° 后视觉上变成横长，箭头指向上下。 */
    transform: translateX(var(--reader-handle-shift, 0px)) rotate(90deg);
  }
}
</style>
