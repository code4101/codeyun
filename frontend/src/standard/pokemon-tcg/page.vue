<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { Search } from '@element-plus/icons-vue'

import {
  fetchPokemonTcgCards,
  fetchPokemonTcgMeta,
  pokemonTcgImageUrl,
  type PokemonTcgCard,
  type PokemonTcgMeta,
} from '@/api/pokemonTcg'
import { useResizablePane } from '@/utils/useResizablePane'

const SET_OPTIONS = [
  { label: '全部卡包', value: '' },
  { label: 'Base Set', value: 'base-set' },
  { label: 'Jungle', value: 'jungle' },
  { label: 'Fossil', value: 'fossil' },
  { label: 'Team Rocket', value: 'team-rocket' },
]

const TYPE_OPTIONS = [
  { label: '全部属性', value: '' },
  { label: '草', value: '{ G }' },
  { label: '火', value: '{ R }' },
  { label: '水', value: '{ W }' },
  { label: '雷', value: '{ L }' },
  { label: '超', value: '{ P }' },
  { label: '斗', value: '{ F }' },
  { label: '无', value: '{ C }' },
  { label: '无属性', value: 'none' },
]

const loading = ref(false)
const meta = ref<PokemonTcgMeta | null>(null)
const cards = ref<PokemonTcgCard[]>([])
const selected = ref<PokemonTcgCard | null>(null)
const q = ref('')
const setSlug = ref('')
const typeKey = ref('')
const page = ref(1)
const pageSize = ref(60)
const total = ref(0)

const {
  paneHeight: cardPaneHeight,
  isResizing,
  startResizing,
} = useResizablePane({
  initialHeight: 520,
  getAdaptiveHeight: () => {
    const availableHeight = Math.max(520, window.innerHeight - 230)
    return Math.floor(availableHeight * 0.58)
  },
  getResizeBounds: () => {
    const availableHeight = Math.max(520, window.innerHeight - 230)
    return {
      min: 260,
      max: Math.max(320, availableHeight - 220),
    }
  },
  storageKey: 'pokemon-tcg:catalog-card-pane-height',
})
const cardPaneStyle = computed(() => ({ height: `${cardPaneHeight.value}px` }))

const progressText = computed(() => {
  const progress = meta.value?.progress || {}
  const status = String(progress.status || '')
  const done = Number(progress.done_count || 0)
  const target = Number(progress.target_count || 0)
  if (!status) return '未生成快照'
  if (target > 0) return `${status} ${done}/${target}`
  return status
})

const cardTitle = (card: PokemonTcgCard) => card.zh?.display_title || card.display_title
const cardName = (card: PokemonTcgCard) => card.zh?.official_name || card.official_name
const cardSetName = (card: PokemonTcgCard) => card.zh?.set_name || card.set_name
const cardTypeText = (card: PokemonTcgCard) => card.zh?.color || card.color || '无属性'
const cardBasicText = (card: PokemonTcgCard) => {
  const name = card.zh?.official_name || card.official_name
  const hp = card.zh?.hp_text || `${card.hp} HP`
  const color = cardTypeText(card)
  return [name, hp, color].filter(Boolean).join(' · ')
}

const typeOptions = computed(() => TYPE_OPTIONS.map((option) => {
  if (!option.value) return option
  const count = meta.value?.type_counts?.[option.value]?.count
  return count == null ? option : { ...option, label: `${option.label} ${count}` }
}))

const loadMeta = async () => {
  meta.value = await fetchPokemonTcgMeta()
}

const loadCards = async () => {
  loading.value = true
  try {
    const response = await fetchPokemonTcgCards({
      q: q.value.trim(),
      set: setSlug.value,
      type: typeKey.value,
      page: page.value,
      page_size: pageSize.value,
    })
    cards.value = response.items
    total.value = response.total
    if (!selected.value || !cards.value.some((card) => card.source_card_slug === selected.value?.source_card_slug)) {
      selected.value = cards.value[0] || null
    }
  } finally {
    loading.value = false
  }
}

const reload = async () => {
  await Promise.all([loadMeta(), loadCards()])
}

watch([q, setSlug, typeKey, pageSize], () => {
  page.value = 1
  void loadCards()
})

watch(page, () => {
  void loadCards()
})

onMounted(() => {
  void reload()
})
</script>

<template>
  <main class="pokemon-tcg-page">
    <header class="page-head">
      <div>
        <h1>宝可梦 TCG 旧卡图鉴</h1>
        <p>Base / Jungle / Fossil / Team Rocket 官方母版数据缓存。</p>
      </div>
      <div class="status-pill">{{ progressText }}</div>
    </header>

    <section class="toolbar">
      <el-input
        v-model="q"
        class="search-input"
        clearable
        :prefix-icon="Search"
        placeholder="搜索名称、编号、招式"
      />
      <el-select v-model="setSlug" class="set-select" placeholder="卡包">
        <el-option
          v-for="option in SET_OPTIONS"
          :key="option.value"
          :label="option.label"
          :value="option.value"
        />
      </el-select>
      <el-select v-model="typeKey" class="type-select" placeholder="属性">
        <el-option
          v-for="option in typeOptions"
          :key="option.value"
          :label="option.label"
          :value="option.value"
        />
      </el-select>
    </section>

    <section class="browser" :class="{ resizing: isResizing }">
      <div class="card-pane" :style="cardPaneStyle">
        <div class="card-list" v-loading="loading">
          <button
            v-for="card in cards"
            :key="card.source_card_slug"
            class="card-tile"
            :class="{ active: selected?.source_card_slug === card.source_card_slug }"
            type="button"
            @click="selected = card"
          >
            <img :src="pokemonTcgImageUrl(card)" :alt="cardTitle(card)" loading="lazy">
            <span>{{ cardName(card) }}</span>
            <em>{{ card.official_id || `${card.set_name} #${card.official_number}` }}</em>
          </button>
        </div>
      </div>

      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        class="pagination"
        layout="prev, pager, next, sizes, total"
        :page-sizes="[30, 60, 120]"
        :total="total"
      />

      <div
        class="catalog-resizer"
        role="separator"
        aria-orientation="horizontal"
        title="拖动调整卡墙和详情的比例"
        @mousedown="startResizing"
      >
        <span></span>
      </div>

      <aside class="detail" v-if="selected">
        <div class="detail-content">
          <img class="detail-image" :src="pokemonTcgImageUrl(selected)" :alt="cardTitle(selected)">
          <div class="detail-body">
            <h2>{{ cardTitle(selected) }}</h2>
            <dl>
              <dt>卡包</dt><dd>{{ cardSetName(selected) }} #{{ selected.official_number }}/{{ selected.official_total }}</dd>
              <dt>基本</dt><dd>{{ cardBasicText(selected) }}</dd>
              <dt>属性</dt><dd>{{ cardTypeText(selected) }}</dd>
              <dt>阶段</dt><dd>{{ selected.zh?.stage || selected.stage || '—' }}</dd>
              <dt>招式</dt><dd>{{ selected.zh?.attacks_text || selected.attacks_text || '—' }}</dd>
              <dt>弱点 / 抵抗 / 撤退</dt>
              <dd>{{ selected.zh?.weakness_text || selected.weakness_text || '—' }} / {{ selected.zh?.resistance_text || selected.resistance_text || '—' }} / {{ selected.zh?.retreat_cost ?? selected.retreat_cost ?? '—' }}</dd>
              <dt>稀有度 / 日期</dt><dd>{{ selected.zh?.rarity || selected.rarity || '—' }} · {{ selected.zh?.release_date_text || selected.release_date_text || '—' }}</dd>
              <dt>插画</dt><dd>{{ selected.zh?.illustrator_text || selected.illustrator_text || '—' }}</dd>
              <dt>图鉴文本</dt><dd>{{ selected.zh?.flavor_text || selected.flavor_text || '—' }}</dd>
            </dl>
            <a :href="selected.source_url" target="_blank" rel="noreferrer">PkmnCards</a>
          </div>
        </div>
      </aside>
    </section>
  </main>
</template>

<style scoped>
.pokemon-tcg-page {
  padding: 20px;
  color: #202326;
}

.page-head,
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  max-width: 1360px;
  margin: 0 auto 14px;
}

.page-head h1 {
  margin: 0;
  font-size: 24px;
  letter-spacing: 0;
}

.page-head p {
  margin: 4px 0 0;
  color: #687078;
}

.status-pill {
  padding: 4px 10px;
  border-radius: 999px;
  background: #dff0ed;
  color: #105660;
  font-weight: 700;
  white-space: nowrap;
}

.toolbar {
  justify-content: flex-start;
}

.search-input {
  width: 320px;
}

.set-select {
  width: 180px;
}

.type-select {
  width: 150px;
}

.browser {
  max-width: 1360px;
  margin: 0 auto;
}

.card-pane {
  overflow: auto;
  padding-right: 2px;
}

.card-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 10px;
}

.card-tile {
  border: 1px solid #d8d0c1;
  border-radius: 8px;
  background: #fffdf8;
  padding: 8px;
  text-align: left;
  cursor: pointer;
}

.card-tile.active {
  border-color: #17636c;
  box-shadow: 0 0 0 2px rgba(23, 99, 108, .15);
}

.card-tile img {
  display: block;
  width: 100%;
  aspect-ratio: 600 / 825;
  object-fit: contain;
  background: #e9e2d6;
}

.card-tile span,
.card-tile em {
  display: block;
  margin-top: 6px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.card-tile span {
  font-weight: 700;
}

.card-tile em {
  color: #687078;
  font-style: normal;
  font-size: 12px;
}

.detail {
  border: 1px solid #d8d0c1;
  border-radius: 8px;
  background: #fffdf8;
  overflow: hidden;
}

.detail-content {
  display: grid;
  grid-template-columns: 320px minmax(0, 1fr);
  align-items: start;
}

.detail-image {
  display: block;
  width: 100%;
  max-height: 460px;
  object-fit: contain;
  background: #e9e2d6;
}

.detail-body {
  padding: 14px;
}

.detail h2 {
  margin: 0 0 10px;
  font-size: 18px;
}

.detail dl {
  margin: 0;
}

.detail dt {
  margin-top: 9px;
  font-weight: 700;
}

.detail dd {
  margin: 3px 0 0;
  color: #34393d;
  line-height: 1.45;
  white-space: pre-line;
}

.pagination {
  margin: 12px 0 0;
}

.catalog-resizer {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 18px;
  cursor: row-resize;
}

.catalog-resizer span {
  width: 48px;
  height: 3px;
  border-radius: 999px;
  background: #d8d0c1;
}

.catalog-resizer:hover span,
.browser.resizing .catalog-resizer span {
  background: #17636c;
}

@media (max-width: 1180px) {
  .card-list {
    grid-template-columns: repeat(auto-fill, minmax(132px, 1fr));
  }

  .detail-content {
    grid-template-columns: 280px minmax(0, 1fr);
  }

  .detail-image {
    max-height: 420px;
  }
}

@media (max-width: 900px) {
  .page-head,
  .toolbar {
    flex-wrap: wrap;
  }

  .detail-body {
    padding: 12px;
  }

  .detail-content {
    grid-template-columns: 240px minmax(0, 1fr);
  }
}

@media (max-width: 760px) {
  .detail-content {
    grid-template-columns: 1fr;
  }

  .card-list {
    grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  }
}
</style>
