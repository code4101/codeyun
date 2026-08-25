<script setup lang="ts">
import type { FanxiuGameRichTextSegment } from '@/api/fanxiu';

defineProps<{
  text?: string;
  segments?: FanxiuGameRichTextSegment[];
}>();

const SAFE_HEX_COLOR = /^#[0-9a-f]{3,8}$/i;

function segmentStyle(segment: FanxiuGameRichTextSegment) {
  return SAFE_HEX_COLOR.test(segment.color || '') ? { color: segment.color } : undefined;
}
</script>

<template>
  <span class="fanxiu-game-rich-text">
    <template v-if="segments?.length">
      <span
        v-for="(segment, index) in segments"
        :key="`${index}-${segment.text}`"
        :class="segment.role ? `is-${segment.role}` : undefined"
        :style="segmentStyle(segment)"
      >{{ segment.text }}</span>
    </template>
    <template v-else>{{ text }}</template>
  </span>
</template>

<style scoped>
.fanxiu-game-rich-text {
  white-space: pre-wrap;
}

.is-skill,
.is-quality {
  font-weight: 650;
}

.is-value,
.is-attribute {
  font-weight: 560;
}
</style>
