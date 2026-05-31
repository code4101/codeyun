<script setup lang="ts">
import { computed } from 'vue'

import { getFanxiuResourceIconUrl } from '@/api/fanxiu'
import {
  buildFanxiuResourceHref,
  cleanFanxiuDisplayText,
  cleanFanxiuPreview,
  encodeFanxiuDataText,
} from './resourceRenderer'

type LinkedItem = {
  id?: string | number | null
  name?: string | null
  count?: string | number | null
  icon?: string | null
  small_icon?: string | null
  description?: string | null
}

const props = withDefaults(defineProps<{
  item: LinkedItem
  compact?: boolean
  muted?: boolean
  plainCount?: boolean
  disableHover?: boolean
}>(), {
  compact: false,
  muted: false,
  plainCount: false,
  disableHover: false,
})

const itemId = computed(() => String(props.item?.id ?? '').trim())
const itemName = computed(() => cleanFanxiuPreview(props.item?.name || props.item?.id || '道具'))
const itemCount = computed(() => {
  const count = props.item?.count
  if (count === null || count === undefined || count === '') return ''
  return props.plainCount ? ` ${count}` : ` x${count}`
})
const itemText = computed(() => `${itemName.value}${itemCount.value}`)
const itemDescription = computed(() => cleanFanxiuDisplayText(props.item?.description))
const itemHref = computed(() => itemId.value ? buildFanxiuResourceHref('item', itemId.value) : undefined)
const itemIconUrl = computed(() => getFanxiuResourceIconUrl(props.item?.icon || props.item?.small_icon))
const itemPreview = computed(() => encodeFanxiuDataText(itemDescription.value))

function hideBrokenIcon(event: Event) {
  if (event.target instanceof HTMLImageElement) {
    event.target.style.display = 'none'
  }
}
</script>

<template>
  <a
    v-if="itemHref"
    class="linked-item clickable"
    :class="{ compact, muted }"
    :href="itemHref"
    :data-fanxiu-resource-link="disableHover ? undefined : '1'"
    :data-wiki-resource-link="disableHover ? undefined : '1'"
    data-wiki-tab="item"
    :data-wiki-id="itemId"
    :data-wiki-title="itemName"
    :data-wiki-preview="itemPreview"
    data-wiki-effect-text-preview=""
    data-wiki-effect-preview=""
    data-wiki-reward-preview=""
    :data-wiki-alias="itemName"
  >
    <span class="linked-item-icon">
      <img
        v-if="itemIconUrl"
        :src="itemIconUrl"
        :alt="itemName"
        loading="lazy"
        @error="hideBrokenIcon"
      >
    </span>
    <span>{{ itemText }}</span>
  </a>
  <span
    v-else
    class="linked-item"
    :class="{ compact, muted }"
  >
    <span class="linked-item-icon">
      <img
        v-if="itemIconUrl"
        :src="itemIconUrl"
        :alt="itemName"
        loading="lazy"
        @error="hideBrokenIcon"
      >
    </span>
    <span>{{ itemText }}</span>
  </span>
</template>

<style scoped>
.linked-item {
  min-height: 34px;
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 3px 9px 3px 4px;
  color: #6f4d17;
  font: inherit;
  font-size: 13px;
  font-weight: 650;
  text-align: left;
  text-decoration: none;
  background: rgba(255, 251, 236, 0.82);
  border: 1px solid rgba(191, 151, 70, 0.48);
  cursor: default;
}

.linked-item.clickable {
  cursor: pointer;
}

.linked-item.clickable:hover {
  color: #4d340e;
  background: rgba(255, 246, 205, 0.96);
  border-color: rgba(194, 130, 24, 0.78);
}

.linked-item.muted {
  opacity: 0.72;
}

.linked-item.compact {
  min-height: 28px;
  color: #ead7a5;
  background: rgba(255, 248, 220, 0.08);
  border-color: rgba(255, 212, 95, 0.34);
}

.linked-item-icon {
  width: 28px;
  height: 28px;
  flex: 0 0 auto;
  display: inline-grid;
  place-items: center;
  overflow: hidden;
  background:
    radial-gradient(circle at 35% 25%, rgba(255, 255, 255, 0.74), transparent 22%),
    linear-gradient(135deg, #284d8d, #20b6cc 52%, #efe9ac);
  border: 1px solid rgba(244, 230, 170, 0.86);
}

.linked-item-icon img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
</style>
