<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowLeft } from '@element-plus/icons-vue'

import {
  getFanxiuActivityCard,
  getFanxiuDigitDoorCharacterCard,
  getFanxiuDoupoTDPartnerCard,
  getFanxiuGongfaCard,
  getFanxiuItemCard,
  getFanxiuLingjieFeatureCard,
  getFanxiuResourceIconUrl,
  getFanxiuWikiLinkTargets,
  searchFanxiuActivityCards,
  type FanxiuActivityCard,
  type FanxiuActivitySearchItem,
  type FanxiuDigitDoorCharacterCard,
  type FanxiuDoupoTDPartnerCard,
  type FanxiuGongfaLinkedItem,
  type FanxiuGongfaCard,
  type FanxiuItemCard,
  type FanxiuLingjieFeatureCard,
} from '@/api/fanxiu'
import {
  buildFanxiuLinkTargetGroups,
  buildFanxiuResourceHref,
  normalizeFanxiuRichText,
  parseFanxiuEffectRows,
  type FanxiuResourceLinkTarget,
} from '../resourceRenderer'
import FanxiuResourceHoverScope from '../FanxiuResourceHoverScope.vue'
import FanxiuRenderedText from '../FanxiuRenderedText.vue'

type FanxiuResourceType = 'gongfa' | 'item' | 'lingjie' | 'activity' | 'digitdoor' | 'doupotd'
type FanxiuResourceCard =
  | FanxiuGongfaCard
  | FanxiuItemCard
  | FanxiuLingjieFeatureCard
  | FanxiuActivityCard
  | FanxiuActivitySearchItem
  | FanxiuDigitDoorCharacterCard
  | FanxiuDoupoTDPartnerCard

const route = useRoute()
const loading = ref(false)
const card = ref<FanxiuResourceCard | null>(null)
const errorText = ref('')
const linkTargets = ref<FanxiuResourceLinkTarget[]>([])
let loadSeq = 0

const resourceType = computed<FanxiuResourceType | null>(() => {
  const value = String(route.params.resourceType ?? '').trim()
  return value === 'gongfa'
    || value === 'item'
    || value === 'lingjie'
    || value === 'activity'
    || value === 'digitdoor'
    || value === 'doupotd'
    ? value
    : null
})

const resourceId = computed(() => String(route.params.resourceId ?? '').trim())

const resourceTypeLabel = computed(() => {
  if (resourceType.value === 'gongfa') return '功法'
  if (resourceType.value === 'item') return '道具'
  if (resourceType.value === 'lingjie') return '灵界词条'
  if (resourceType.value === 'activity') return '活动'
  if (resourceType.value === 'digitdoor') return '数字门角色'
  if (resourceType.value === 'doupotd') return '斗破角色'
  return '凡修资源'
})

const resourceName = computed(() => String(card.value?.name || resourceId.value || '凡修资源'))
const resourceIconUrl = computed(() => getFanxiuResourceIconUrl((card.value as { icon?: string } | null)?.icon))
const wikiBackHref = computed(() => {
  const tab = resourceType.value || 'item'
  const query = new URLSearchParams()
  query.set('tab', tab)
  if (resourceId.value) query.set('id', resourceId.value)
  return `/standalone/fanxiu/wiki?${query.toString()}`
})

const resourceDescription = computed(() => {
  const current = card.value
  if (!current) return ''
  if ('description' in current && current.description) return current.description
  if ('description_preview' in current && current.description_preview) return current.description_preview
  if ('skill_description_rich' in current && current.skill_description_rich) return current.skill_description_rich
  if ('skill_description' in current && current.skill_description) return current.skill_description
  if ('skill_description_plain' in current && current.skill_description_plain) return current.skill_description_plain
  if ('positioning' in current && current.positioning) return current.positioning
  if ('skills' in current) {
    const skill = current.skills?.find(item => item.describe || item.effect_describe || item.additional_describe)
    return String(skill?.describe || skill?.effect_describe || skill?.additional_describe || '')
  }
  return ''
})

const resourceEffectDescription = computed(() => {
  const current = card.value
  if (!current || !('effect_description' in current)) return ''
  const effectDescription = normalizeFanxiuRichText(current.effect_description || '').trim()
  const description = normalizeFanxiuRichText(resourceDescription.value).trim()
  return effectDescription && effectDescription !== description ? effectDescription : ''
})

const itemShowEffects = computed(() => {
  const current = card.value
  if (!current || !('show_effect' in current)) return []
  return parseItemShowEffect(current.show_effect)
})

const itemRewardRows = computed<FanxiuGongfaLinkedItem[]>(() => {
  const current = card.value
  if (!current || !('optional_gift_rewards' in current)) return []
  return current.optional_gift_rewards ?? []
})

const sourceJson = computed(() => JSON.stringify(card.value ?? {}, null, 2))
const linkTargetGroups = computed(() => buildFanxiuLinkTargetGroups(linkTargets.value))
const resourceMetaRows = computed(() => {
  const current = card.value as Record<string, unknown> | null
  if (!current) return []
  const iconSourceTable = String(current.icon_source_table ?? '').trim()
  const iconSourceField = String(current.icon_source_field ?? '').trim()
  const iconSource = iconSourceTable && iconSourceField ? `${iconSourceTable}.${iconSourceField}` : iconSourceTable || iconSourceField
  const iconReuseCount = Number(current.icon_reuse_count ?? 0)
  const iconQuality = iconReuseCount > 1
    ? String(current.icon_quality_note ?? '').trim() || `主图标共用 ${iconReuseCount} 项`
    : ''
  const smallIconReuseCount = Number(current.small_icon_reuse_count ?? 0)
  const smallIconQuality = smallIconReuseCount > 1
    ? String(current.small_icon_quality_note ?? '').trim() || `小图标共用 ${smallIconReuseCount} 项`
    : ''
  const rows = [
    ['品质', current.quality_label || current.quality_name || current.quality],
    ['定位', current.positioning],
    ['技能', current.skill_name],
    ['时间', current.time_kind_name],
    ['来源', current.source_table],
    ['图标来源', iconSource],
    ['图标复用', iconQuality],
    ['小图标复用', smallIconQuality],
    ['状态', current.presence_status],
  ]
  return rows
    .map(([label, value]) => ({ label: String(label), value: String(value ?? '').trim() }))
    .filter(row => row.value && row.value !== 'null' && row.value !== 'undefined')
})

function parseItemShowEffect(value: unknown) {
  return parseFanxiuEffectRows(value)
}

function getLinkedItemId(item: FanxiuGongfaLinkedItem) {
  return String(item.id ?? '').trim()
}

function getLinkedItemHref(item: FanxiuGongfaLinkedItem) {
  const id = getLinkedItemId(item)
  return id ? buildFanxiuResourceHref('item', id) : ''
}

function getLinkedItemText(item: FanxiuGongfaLinkedItem) {
  const name = String(item.name || item.id || '').trim()
  const count = String(item.count ?? '').trim()
  return count ? `${name} x${count}` : name
}

function resourceLinkTextsForCard(current: FanxiuResourceCard | null) {
  if (!current) return []
  const texts = [
    resourceDescription.value,
    resourceEffectDescription.value,
  ]
  if ('show_effect' in current) texts.push(String(current.show_effect || ''))
  if ('optional_gift_rewards' in current) {
    for (const item of current.optional_gift_rewards ?? []) {
      texts.push(String(item.name || ''), String(item.description || ''))
    }
  }
  if ('skills' in current) {
    for (const skill of current.skills ?? []) {
      texts.push(String(skill?.describe || ''), String(skill?.effect_describe || ''), String(skill?.additional_describe || ''))
    }
  }
  return texts.map(text => text.trim()).filter(Boolean)
}

async function loadResource() {
  const type = resourceType.value
  const id = resourceId.value
  const seq = ++loadSeq
  if (!type || !id) {
    card.value = null
    errorText.value = '资源链接无效'
    return
  }
  loading.value = true
  errorText.value = ''
  linkTargets.value = []
  try {
    if (type === 'gongfa') {
      card.value = (await getFanxiuGongfaCard(id)).card
    } else if (type === 'item') {
      card.value = (await getFanxiuItemCard(id)).card
    } else if (type === 'lingjie') {
      card.value = await getFanxiuLingjieFeatureCard(id)
    } else if (type === 'activity') {
      const response = await searchFanxiuActivityCards({ query: id, limit: 1, include_facets: false })
      const matched = response.items.find(item => String(item.id) === id) ?? response.items[0]
      if (!matched) throw new Error(`没有找到活动：${id}`)
      card.value = matched
    } else if (type === 'digitdoor') {
      card.value = (await getFanxiuDigitDoorCharacterCard(id)).card
    } else {
      card.value = (await getFanxiuDoupoTDPartnerCard(id)).card
    }
    if (seq === loadSeq) {
      void loadResourceLinkTargets(seq)
    }
  } catch (error: any) {
    if (seq !== loadSeq) return
    card.value = null
    errorText.value = error?.response?.data?.detail || error?.message || '读取凡修资源失败'
    ElMessage.error(errorText.value)
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

async function loadResourceLinkTargets(seq = loadSeq) {
  const texts = resourceLinkTextsForCard(card.value)
  if (!texts.length) {
    if (seq === loadSeq) linkTargets.value = []
    return
  }
  try {
    const response = await getFanxiuWikiLinkTargets({ texts, limit: 120 })
    if (seq === loadSeq) {
      linkTargets.value = response.items
    }
  } catch {
    if (seq === loadSeq) {
      linkTargets.value = []
    }
  }
}

watch(() => [route.params.resourceType, route.params.resourceId], loadResource)
watch(() => card.value?.name, (name) => {
  const title = String(name || '').trim()
  if (title) {
    document.title = `${title} - CodeYun`
  }
})

onMounted(() => {
  void loadResource()
})
</script>

<template>
  <FanxiuResourceHoverScope>
  <main class="fanxiu-resource-page" v-loading="loading">
    <a class="resource-back-link" :href="wikiBackHref" title="返回凡修图鉴" aria-label="返回凡修图鉴">
      <el-icon><ArrowLeft /></el-icon>
    </a>
    <section v-if="card" class="resource-panel">
      <header class="detail-head">
        <span class="object-icon">
          <span class="icon-fallback">{{ resourceName.slice(0, 1) }}</span>
          <img v-if="resourceIconUrl" :src="resourceIconUrl" :alt="resourceName" loading="lazy">
        </span>
        <div class="detail-title">
          <h3>{{ resourceName }}</h3>
          <div class="detail-meta">
            <span>ID {{ resourceId }}</span>
            <span>{{ resourceTypeLabel }}</span>
          </div>
        </div>
      </header>

      <section v-if="resourceDescription" class="object-section intro-section">
        <h4>简介</h4>
        <FanxiuRenderedText
          :value="resourceDescription"
          :link-target-groups="linkTargetGroups"
          tone="light"
        />
      </section>

      <section v-if="resourceMetaRows.length" class="object-section meta-section">
        <h4>字段</h4>
        <ul class="effect-list">
          <li v-for="row in resourceMetaRows" :key="row.label">
            <span>{{ row.label }}</span>
            <strong>{{ row.value }}</strong>
          </li>
        </ul>
      </section>

      <section v-if="resourceEffectDescription || itemShowEffects.length || itemRewardRows.length" class="object-section">
        <h4>效果</h4>
        <FanxiuRenderedText
          v-if="resourceEffectDescription"
          class="game-rich-text"
          :value="resourceEffectDescription"
          :link-target-groups="linkTargetGroups"
          tone="dark"
        />
        <ul v-if="itemShowEffects.length" class="effect-list">
          <li v-for="effect in itemShowEffects" :key="effect.key">
            <span>{{ effect.name }}</span>
            <strong>{{ effect.value }}</strong>
          </li>
        </ul>
        <div v-if="itemRewardRows.length" class="linked-item-strip detail-items optional-gift-items">
          <a
            v-for="item in itemRewardRows"
            :key="`${item.id}-${item.count}`"
            class="linked-item clickable"
            :href="getLinkedItemHref(item)"
          >
            <span class="linked-item-icon">
              <img v-if="getFanxiuResourceIconUrl(item.icon)" :src="getFanxiuResourceIconUrl(item.icon)" :alt="String(item.name || item.id)" loading="lazy">
            </span>
            <span>{{ getLinkedItemText(item) }}</span>
          </a>
        </div>
      </section>

      <details class="resource-source">
        <summary>原始资源</summary>
        <pre>{{ sourceJson }}</pre>
      </details>
    </section>

    <section v-else class="resource-empty">
      <h1>{{ errorText || '未找到凡修资源' }}</h1>
      <p>{{ resourceTypeLabel }} · ID {{ resourceId || '-' }}</p>
    </section>
  </main>
  </FanxiuResourceHoverScope>
</template>

<style scoped>
.fanxiu-resource-page {
  position: relative;
  min-height: 100%;
  padding: 24px;
  box-sizing: border-box;
  color: #172033;
  background: #f7f1dc;
}

.resource-panel {
  width: min(1080px, 100%);
  margin: 0 auto;
  display: grid;
  gap: 14px;
}

.resource-back-link {
  position: absolute;
  top: 24px;
  left: 24px;
  width: 28px;
  height: 28px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #7c5b28;
  text-decoration: none;
  border: 1px solid rgba(165, 132, 69, 0.34);
  background: rgba(255, 252, 242, 0.74);
}

.resource-back-link:hover {
  color: #17bfc8;
  border-color: rgba(23, 191, 200, 0.48);
}

.resource-back-link .el-icon {
  width: 16px;
  height: 16px;
}

.detail-head {
  display: flex;
  align-items: center;
  gap: 16px;
  margin: 0 auto;
  width: min(100%, 1080px);
}

.object-icon {
  position: relative;
  width: 86px;
  height: 86px;
  flex: 0 0 auto;
  display: grid;
  place-items: center;
  overflow: hidden;
  color: #8a6728;
  font-size: 34px;
  font-weight: 800;
  background:
    radial-gradient(circle at 35% 25%, rgba(255, 255, 255, 0.74), transparent 22%),
    linear-gradient(135deg, #284d8d, #20b6cc 52%, #efe9ac);
  box-shadow: 0 2px 12px rgba(24, 55, 98, 0.28), inset 0 0 22px rgba(255, 255, 255, 0.42);
}

.object-icon img {
  position: relative;
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.icon-fallback {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
}

.detail-title {
  min-width: 0;
  display: grid;
  gap: 7px;
}

.detail-title h3 {
  margin: 0;
  color: #17bfc8;
  font-size: 34px;
  line-height: 1.15;
  font-weight: 800;
  text-shadow: 0 1px 0 rgba(255, 255, 255, 0.78);
}

.detail-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 12px;
  color: #8a7b61;
  font-size: 13px;
}

.object-section {
  --wiki-term-color: #ffd45f;
  --wiki-number-color: #b9f08f;
  --wiki-variable-color: #44d6df;
  width: min(100%, 1080px);
  margin: 0 auto;
  padding: 18px 26px 22px;
  box-sizing: border-box;
  color: #f7f0df;
  background: rgba(55, 56, 64, 0.95);
  border: 2px solid rgba(211, 190, 132, 0.95);
  box-shadow: 0 14px 34px rgba(50, 36, 18, 0.24);
}

.intro-section {
  --wiki-term-color: #b16a00;
  --wiki-number-color: #2f8f1d;
  --wiki-variable-color: #007f86;
  color: #554733;
  background: rgba(255, 252, 242, 0.74);
  border-color: rgba(193, 164, 92, 0.48);
  box-shadow: none;
}

.object-section h4 {
  width: max-content;
  min-width: 148px;
  margin: 0 0 12px;
  padding-bottom: 5px;
  color: #efe2ad;
  font-size: 20px;
  font-weight: 760;
  border-bottom: 2px solid rgba(214, 196, 136, 0.56);
}

.intro-section h4 {
  color: #8a6b33;
  border-bottom-color: rgba(138, 107, 51, 0.36);
}

.game-rich-text {
  padding-top: 12px;
}

.effect-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 10px 0 0;
  padding: 0;
  list-style: none;
}

.effect-list li {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 8px;
  border-radius: 6px;
  background: #fff8e6;
  box-shadow: inset 0 0 0 1px rgba(138, 103, 40, 0.18);
  font-size: 13px;
}

.effect-list span {
  color: #344054;
}

.effect-list strong {
  color: #147d4f;
}

.linked-item-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.detail-items {
  width: min(100%, 1080px);
  margin: 10px auto 0;
}

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
  cursor: pointer;
}

.linked-item:hover {
  color: #4d340e;
  background: rgba(255, 246, 205, 0.96);
  border-color: rgba(194, 130, 24, 0.78);
}

.linked-item-icon {
  width: 28px;
  height: 28px;
  flex: 0 0 auto;
  display: inline-grid;
  place-items: center;
  overflow: hidden;
  border: 1px solid rgba(244, 230, 170, 0.86);
  background:
    radial-gradient(circle at 35% 25%, rgba(255, 255, 255, 0.74), transparent 22%),
    linear-gradient(135deg, #284d8d, #20b6cc 52%, #efe9ac);
}

.linked-item-icon img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.resource-source {
  width: min(100%, 1080px);
  margin: 0 auto;
  background: rgba(255, 252, 242, 0.74);
  border: 1px solid rgba(193, 164, 92, 0.48);
  padding: 14px 16px;
  box-sizing: border-box;
}

.resource-source summary {
  width: max-content;
  cursor: pointer;
  color: #8a6728;
  font-size: 13px;
}

.resource-source pre {
  margin: 12px 0 0;
  overflow: auto;
  max-height: 56vh;
  font-size: 12px;
  line-height: 1.5;
}

.resource-empty {
  width: min(560px, 100%);
  margin: 16vh auto 0;
  text-align: center;
}

.resource-empty h1 {
  margin: 0 0 8px;
  font-size: 22px;
}

.resource-empty p {
  margin: 0;
  color: #667085;
}
</style>
