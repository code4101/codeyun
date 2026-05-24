<script setup lang="ts">
import { computed } from 'vue'

import {
  renderFanxiuPlainRichText,
  renderFanxiuRichText,
  type FanxiuResourceLinkTarget,
} from './resourceRenderer'

const props = withDefaults(defineProps<{
  value?: unknown
  linkTargetGroups?: Map<string, FanxiuResourceLinkTarget[]>
  tone?: 'light' | 'dark'
  compact?: boolean
  enableLinks?: boolean
}>(), {
  value: '',
  tone: 'dark',
  compact: false,
  enableLinks: true,
})

const renderedHtml = computed(() => {
  if (!props.enableLinks) return renderFanxiuPlainRichText(props.value)
  return renderFanxiuRichText(props.value, props.linkTargetGroups)
})
</script>

<template>
  <div
    class="fanxiu-rendered-text"
    :class="[`tone-${tone}`, { compact }]"
    v-html="renderedHtml"
  />
</template>

<style scoped>
.fanxiu-rendered-text {
  --wiki-term-color: #ffd45f;
  --wiki-number-color: #b9f08f;
  --wiki-variable-color: #44d6df;
  color: #f7f0df;
  font-size: 17px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
}

.fanxiu-rendered-text.tone-light {
  --wiki-term-color: #b16a00;
  --wiki-number-color: #2f8f1d;
  --wiki-variable-color: #007f86;
  color: #554733;
}

.fanxiu-rendered-text.compact {
  font-size: 15px;
  line-height: 1.5;
}

.fanxiu-rendered-text :deep(.fanxiu-rich-term) {
  color: var(--wiki-term-color);
  font-weight: 800;
}

.fanxiu-rendered-text :deep(.fanxiu-rich-number) {
  color: var(--wiki-number-color);
  font-weight: 800;
}

.fanxiu-rendered-text :deep(.fanxiu-rich-variable) {
  color: var(--wiki-variable-color);
  font-weight: 800;
}

.fanxiu-rendered-text :deep(.fanxiu-resource-link) {
  color: inherit;
  font-weight: 800;
  text-decoration: underline;
  text-decoration-color: rgba(15, 140, 152, 0.5);
  text-decoration-thickness: 1px;
  text-underline-offset: 3px;
}

.fanxiu-rendered-text :deep(.fanxiu-resource-link:hover) {
  color: #0f8c98;
  text-decoration-color: currentColor;
}
</style>
