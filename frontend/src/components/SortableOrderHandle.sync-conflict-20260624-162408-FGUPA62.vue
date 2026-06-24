<template>
  <button
    type="button"
    class="sortable-order-handle"
    :class="[`size-${size}`, { disabled }]"
    :disabled="disabled"
    :title="resolvedTitle"
    :aria-label="resolvedAriaLabel"
  >
    {{ orderLabel }}
  </button>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{
  index: number
  total: number
  disabled?: boolean
  size?: 'xs' | 'sm' | 'md'
  title?: string
  ariaLabel?: string
}>(), {
  disabled: false,
  size: 'md',
  title: '',
  ariaLabel: '',
})

const orderLabel = computed(() => {
  const order = String(props.index + 1)
  if (props.total < 10) {
    return order
  }
  const width = Math.max(2, String(props.total).length)
  return order.padStart(width, '0')
})

const resolvedTitle = computed(() => (
  props.title || `拖拽调整顺序（当前第 ${orderLabel.value} 项）`
))

const resolvedAriaLabel = computed(() => (
  props.ariaLabel || `拖拽调整顺序，当前第 ${orderLabel.value} 项`
))
</script>

<style scoped>
.sortable-order-handle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 24px;
  height: 24px;
  padding: 0 6px;
  border: none;
  border-radius: 8px;
  background: rgba(226, 232, 240, 0.92);
  color: #475569;
  cursor: grab;
  flex: 0 0 auto;
  font-size: 12px;
  font-weight: 700;
  line-height: 1;
  font-variant-numeric: tabular-nums;
  transition: background-color 0.15s ease, color 0.15s ease, transform 0.15s ease;
}

.sortable-order-handle:hover:not(:disabled) {
  background: rgba(191, 219, 254, 0.96);
  color: #1d4ed8;
}

.sortable-order-handle:active:not(:disabled) {
  cursor: grabbing;
  transform: scale(0.98);
}

.sortable-order-handle.disabled,
.sortable-order-handle:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.sortable-order-handle.size-xs {
  min-width: 20px;
  height: 20px;
  padding: 0 4px;
  border-radius: 6px;
  font-size: 11px;
}

.sortable-order-handle.size-sm {
  min-width: 22px;
  height: 22px;
  padding: 0 5px;
  border-radius: 7px;
  font-size: 11px;
}

.sortable-order-handle.size-md {
  min-width: 28px;
  height: 28px;
  padding: 0 8px;
  border-radius: 10px;
  font-size: 12px;
}
</style>
