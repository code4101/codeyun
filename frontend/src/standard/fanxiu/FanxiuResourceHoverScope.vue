<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { Close, Lock } from '@element-plus/icons-vue'

import {
  cleanFanxiuDisplayText,
  cleanFanxiuPreview,
  parseFanxiuEffectRows,
  parseFanxiuRewardRows,
  type FanxiuEffectRow,
  type FanxiuRewardRow,
  type FanxiuResourceType,
} from './resourceRenderer'
import FanxiuRenderedText from './FanxiuRenderedText.vue'

const WIKI_LINK_HOVER_MARGIN = 16
const WIKI_LINK_HOVER_OFFSET = 12
const WIKI_LINK_HOVER_WIDTH = 520
const WIKI_LINK_HOVER_MAX_HEIGHT = 360

const resourceHover = ref<{
  visible: boolean
  x: number
  y: number
  tab: FanxiuResourceType
  title: string
  preview: string
  effectTextPreview: string
  effectRows: FanxiuEffectRow[]
  rewardRows: FanxiuRewardRow[]
  id: string
  alias: string
  pinned: boolean
} | null>(null)

function normalizeResourceType(value: unknown): FanxiuResourceType | null {
  const text = String(value ?? '').trim()
  return text === 'gongfa' || text === 'item' || text === 'lingjie' ? text : null
}

function getResourceTypeLabel(tab: FanxiuResourceType) {
  if (tab === 'gongfa') return '功法'
  if (tab === 'item') return '道具'
  return '灵界词条'
}

function decodeResourcePreview(value: unknown) {
  const text = String(value ?? '')
  if (!text) return ''
  try {
    return cleanFanxiuDisplayText(decodeURIComponent(text))
  } catch {
    return cleanFanxiuDisplayText(text)
  }
}

function updateResourceHover(event: MouseEvent) {
  if (resourceHover.value?.pinned) return
  if (event.target instanceof Element && event.target.closest('.fanxiu-resource-preview')) return
  const target = event.target instanceof Element
    ? event.target.closest<HTMLAnchorElement>('a[data-fanxiu-resource-link="1"]')
    : null
  if (!target) {
    resourceHover.value = null
    return
  }
  const tab = normalizeResourceType(target.dataset.wikiTab)
  const id = cleanFanxiuPreview(target.dataset.wikiId)
  if (!tab || !id) {
    resourceHover.value = null
    return
  }
  if (resourceHover.value?.visible && resourceHover.value.tab === tab && resourceHover.value.id === id) {
    return
  }
  const viewportWidth = window.innerWidth || document.documentElement.clientWidth || WIKI_LINK_HOVER_WIDTH
  const viewportHeight = window.innerHeight || document.documentElement.clientHeight || WIKI_LINK_HOVER_MAX_HEIGHT
  const hoverWidth = Math.min(WIKI_LINK_HOVER_WIDTH, Math.max(220, viewportWidth - WIKI_LINK_HOVER_MARGIN * 2))
  const hoverHeight = Math.min(WIKI_LINK_HOVER_MAX_HEIGHT, Math.max(160, viewportHeight - WIKI_LINK_HOVER_MARGIN * 2))
  const preferredRight = event.clientX + WIKI_LINK_HOVER_OFFSET
  const preferredBottom = event.clientY + WIKI_LINK_HOVER_OFFSET
  const preferredLeft = event.clientX - hoverWidth - WIKI_LINK_HOVER_OFFSET
  const preferredTop = event.clientY - hoverHeight - WIKI_LINK_HOVER_OFFSET
  const x = Math.min(
    Math.max(preferredRight + hoverWidth <= viewportWidth - WIKI_LINK_HOVER_MARGIN ? preferredRight : preferredLeft, WIKI_LINK_HOVER_MARGIN),
    Math.max(WIKI_LINK_HOVER_MARGIN, viewportWidth - hoverWidth - WIKI_LINK_HOVER_MARGIN),
  )
  const y = Math.min(
    Math.max(preferredBottom + hoverHeight <= viewportHeight - WIKI_LINK_HOVER_MARGIN ? preferredBottom : preferredTop, WIKI_LINK_HOVER_MARGIN),
    Math.max(WIKI_LINK_HOVER_MARGIN, viewportHeight - hoverHeight - WIKI_LINK_HOVER_MARGIN),
  )
  resourceHover.value = {
    visible: true,
    x,
    y,
    tab,
    id,
    title: cleanFanxiuPreview(target.dataset.wikiTitle || target.textContent),
    preview: decodeResourcePreview(target.dataset.wikiPreview),
    effectTextPreview: decodeResourcePreview(target.dataset.wikiEffectTextPreview),
    effectRows: parseFanxiuEffectRows(target.dataset.wikiEffectPreview),
    rewardRows: parseFanxiuRewardRows(target.dataset.wikiRewardPreview),
    alias: cleanFanxiuPreview(target.dataset.wikiAlias || target.textContent),
    pinned: false,
  }
}

function clearResourceHover(event: MouseEvent) {
  if (resourceHover.value?.pinned) return
  const related = event.relatedTarget
  if (related instanceof Element && (related.closest('a[data-fanxiu-resource-link="1"]') || related.closest('.fanxiu-resource-preview'))) return
  resourceHover.value = null
}

function toggleResourceHoverPinned() {
  if (!resourceHover.value) return
  resourceHover.value = {
    ...resourceHover.value,
    pinned: !resourceHover.value.pinned,
  }
}

function pinResourceHover(event: MouseEvent) {
  if (!resourceHover.value || resourceHover.value.pinned) return
  if (event.target instanceof Element && event.target.closest('.fanxiu-resource-preview-action')) return
  resourceHover.value = {
    ...resourceHover.value,
    pinned: true,
  }
}

function closeResourceHover() {
  resourceHover.value = null
}

function handleDocumentPointerDown(event: MouseEvent) {
  if (!resourceHover.value?.pinned) return
  if (event.target instanceof Element && event.target.closest('.fanxiu-resource-preview')) return
  resourceHover.value = null
}

function handleDocumentKeydown(event: KeyboardEvent) {
  if (event.key !== 'Escape' || !resourceHover.value?.pinned) return
  resourceHover.value = null
}

function ensureDocumentListeners() {
  document.addEventListener('mousedown', handleDocumentPointerDown, true)
  document.addEventListener('keydown', handleDocumentKeydown, true)
}

onMounted(ensureDocumentListeners)

onBeforeUnmount(() => {
  document.removeEventListener('mousedown', handleDocumentPointerDown, true)
  document.removeEventListener('keydown', handleDocumentKeydown, true)
})
</script>

<template>
  <div
    class="fanxiu-resource-hover-scope"
    @mouseover="updateResourceHover"
    @mouseout="clearResourceHover"
  >
    <slot />
    <div
      v-if="resourceHover?.visible"
      class="fanxiu-resource-preview"
      :class="{ 'is-pinned': resourceHover.pinned }"
      :style="{ left: `${resourceHover.x}px`, top: `${resourceHover.y}px` }"
      :title="resourceHover.pinned ? '' : '点击固定悬浮窗'"
      @mousedown.stop
      @click.stop="pinResourceHover"
    >
      <div class="fanxiu-resource-preview-head">
        <div class="fanxiu-resource-preview-title">
          <span>{{ getResourceTypeLabel(resourceHover.tab) }}</span>
          <strong>{{ resourceHover.title }}</strong>
        </div>
        <div class="fanxiu-resource-preview-actions">
          <button
            type="button"
            class="fanxiu-resource-preview-action"
            :class="{ active: resourceHover.pinned }"
            :title="resourceHover.pinned ? '取消固定' : '固定悬浮窗'"
            :aria-label="resourceHover.pinned ? '取消固定' : '固定悬浮窗'"
            @click.stop.prevent="toggleResourceHoverPinned"
          >
            <el-icon><Lock /></el-icon>
          </button>
          <button
            v-if="resourceHover.pinned"
            type="button"
            class="fanxiu-resource-preview-action"
            title="关闭"
            aria-label="关闭"
            @click.stop.prevent="closeResourceHover"
          >
            <el-icon><Close /></el-icon>
          </button>
        </div>
      </div>
      <div v-if="resourceHover.preview" class="fanxiu-resource-preview-text-list">
        <div class="fanxiu-resource-preview-text-row">
          <span>说明</span>
          <FanxiuRenderedText
            :value="resourceHover.preview"
            tone="dark"
            compact
            :enable-links="false"
          />
        </div>
      </div>
      <div v-if="resourceHover.effectTextPreview" class="fanxiu-resource-preview-effect-summary">
        <span>效果</span>
        <FanxiuRenderedText
          class="fanxiu-resource-preview-effect-text"
          :value="resourceHover.effectTextPreview"
          tone="dark"
          compact
          :enable-links="false"
        />
      </div>
      <ul v-if="resourceHover.effectRows.length" class="fanxiu-resource-preview-effect-list">
        <li v-for="effect in resourceHover.effectRows" :key="effect.key">
          <span>{{ effect.name }}</span>
          <strong>{{ effect.value }}</strong>
        </li>
      </ul>
      <ul v-if="resourceHover.rewardRows.length" class="fanxiu-resource-preview-reward-list">
        <li v-for="reward in resourceHover.rewardRows" :key="`${reward.id}-${reward.count}`">
          <span>{{ reward.name || reward.id }}</span>
          <strong v-if="reward.count">x{{ reward.count }}</strong>
        </li>
      </ul>
      <small v-if="!resourceHover.preview && !resourceHover.effectTextPreview && !resourceHover.effectRows.length && !resourceHover.rewardRows.length">资源链接</small>
    </div>
  </div>
</template>

<style scoped>
.fanxiu-resource-hover-scope {
  display: contents;
}

.fanxiu-resource-preview {
  position: fixed;
  z-index: 3000;
  display: grid;
  grid-template-rows: auto minmax(0, auto) auto minmax(0, auto) minmax(0, auto) auto;
  gap: 8px;
  width: min(520px, calc(100vw - 32px));
  max-height: min(360px, calc(100vh - 32px));
  overflow: hidden;
  padding: 12px 14px;
  border: 1px solid rgba(68, 214, 223, 0.38);
  border-radius: 6px;
  color: rgba(247, 240, 223, 0.92);
  background: rgba(47, 49, 58, 0.96);
  box-shadow: 0 8px 22px rgba(15, 23, 42, 0.22);
  pointer-events: auto;
}

.fanxiu-resource-preview-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  min-width: 0;
}

.fanxiu-resource-preview-title {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}

.fanxiu-resource-preview-title span {
  flex: 0 0 auto;
  color: #44d6df;
  font-size: 11px;
  line-height: 1.2;
}

.fanxiu-resource-preview-title strong {
  min-width: 0;
  overflow-wrap: anywhere;
  color: #ffd45f;
  font-size: 13px;
  line-height: 1.35;
}

.fanxiu-resource-preview-actions {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 4px;
}

.fanxiu-resource-preview-action {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  padding: 0;
  border: 0;
  border-radius: 4px;
  color: rgba(247, 240, 223, 0.72);
  background: transparent;
  cursor: pointer;
}

.fanxiu-resource-preview-action:hover,
.fanxiu-resource-preview-action.active {
  color: #44d6df;
  background: rgba(68, 214, 223, 0.13);
}

.fanxiu-resource-preview.is-pinned {
  border-color: rgba(255, 212, 95, 0.6);
  box-shadow: 0 10px 26px rgba(15, 23, 42, 0.28);
}

.fanxiu-resource-preview-text-list {
  display: grid;
  gap: 6px;
  min-height: 0;
  max-height: 120px;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding-right: 6px;
}

.fanxiu-resource-preview-text-row {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin: 0;
  min-width: 0;
}

.fanxiu-resource-preview-text-list span,
.fanxiu-resource-preview-effect-summary span {
  margin-right: 6px;
  color: #44d6df;
  font-size: 11px;
  font-weight: 800;
}

.fanxiu-resource-preview-effect-summary {
  display: flex;
  align-items: baseline;
  gap: 6px;
  min-height: 0;
  max-height: 92px;
  overflow-y: auto;
  min-width: 0;
  color: rgba(247, 240, 223, 0.9);
  font-size: 13px;
  line-height: 1.5;
}

.fanxiu-resource-preview-text-row :deep(.fanxiu-rendered-text),
.fanxiu-resource-preview-effect-text {
  min-width: 0;
  overflow-wrap: anywhere;
  color: rgba(247, 240, 223, 0.9);
  font-size: 13px;
  line-height: 1.5;
}

.fanxiu-resource-preview-effect-list,
.fanxiu-resource-preview-reward-list {
  display: grid;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  margin: 0;
  padding: 0;
  list-style: none;
  color: rgba(247, 240, 223, 0.9);
  font-size: 13px;
  line-height: 1.45;
}

.fanxiu-resource-preview-effect-list {
  gap: 2px;
  max-height: 110px;
}

.fanxiu-resource-preview-reward-list {
  gap: 4px;
  max-height: 150px;
}

.fanxiu-resource-preview-effect-list li,
.fanxiu-resource-preview-reward-list li {
  display: flex;
  align-items: baseline;
  min-width: 0;
}

.fanxiu-resource-preview-effect-list li {
  gap: 0;
}

.fanxiu-resource-preview-reward-list li {
  gap: 6px;
}

.fanxiu-resource-preview-effect-list span {
  color: #ff8b88;
  font-weight: 700;
}

.fanxiu-resource-preview-effect-list strong,
.fanxiu-resource-preview-reward-list strong {
  color: #b9f08f;
  font-weight: 800;
}

.fanxiu-resource-preview-reward-list span {
  min-width: 0;
  color: #ffd45f;
  font-weight: 750;
  overflow-wrap: anywhere;
}

.fanxiu-resource-preview small {
  color: rgba(247, 240, 223, 0.58);
  font-size: 11px;
  line-height: 1.2;
}
</style>
