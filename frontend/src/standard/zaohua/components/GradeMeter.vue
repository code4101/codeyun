<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{
  rank?: number
  label: string
  textColor?: string
  title?: string
}>(), {
  rank: 0,
  textColor: '#2f3437',
  title: '',
})

const TIER_COLORS = ['#7f878c', '#2f8798', '#7449a8', '#b56b24', '#a62f46']

const visualStyle = computed(() => {
  const rank = Math.max(1, Math.min(15, Math.trunc(props.rank || 1)))
  const tier = Math.ceil(rank / 3)
  const quality = ((rank - 1) % 3) + 1
  return {
    '--grade-tier-color': TIER_COLORS[tier - 1],
    '--grade-fill-width': `${quality * 100 / 3}%`,
    '--grade-meter-text': props.textColor,
  }
})
</script>

<template>
  <span class="grade-meter" :style="visualStyle" :title="title || label">
    <span class="grade-meter-fill"></span>
    <span class="grade-meter-label">{{ label }}</span>
  </span>
</template>

<style scoped>
.grade-meter {
  position: relative;
  display: inline-flex;
  box-sizing: border-box;
  align-items: center;
  justify-content: center;
  width: 106px;
  height: 27px;
  overflow: hidden;
  border: 1px solid color-mix(in srgb, var(--grade-tier-color) 48%, #cfd4d0);
  border-radius: 4px;
  background: #fff;
  vertical-align: middle;
}

.grade-meter-fill {
  position: absolute;
  inset: 0 auto 0 0;
  width: var(--grade-fill-width);
  background: color-mix(in srgb, var(--grade-tier-color) 32%, #fff);
}

.grade-meter-label {
  position: relative;
  z-index: 2;
  overflow: hidden;
  color: var(--grade-meter-text);
  font-weight: 600;
  line-height: 1;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
