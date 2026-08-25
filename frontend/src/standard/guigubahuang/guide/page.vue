<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { Search } from '@element-plus/icons-vue'
import {
  fetchGuigubahuangGuide,
  type FieldModifier,
  type GuigubahuangGuide,
  type WudaoAttribute,
  type XianCiShrine,
} from '@/api/guigubahuang'

type GuideTab = 'wudao' | 'xian-ci'

const TAB_STORAGE_KEY = 'guigubahuang:guide:tab'
const SOULS_STORAGE_KEY = 'guigubahuang:guide:souls'
const guide = ref<GuigubahuangGuide | null>(null)
const loading = ref(false)
const errorText = ref('')
const activeTab = ref<GuideTab>('wudao')
const selectedAttributeId = ref('fire')
const selectedShrineId = ref('nili')
const shrineQuery = ref('')
const shrineRegion = ref('全部地区')
const shrineKind = ref('全部类型')
const souls = ref<string[][]>([
  ['火', '土', '木'],
  ['火', '土', '木'],
  ['土', '火', '木'],
])

const attributes = computed(() => guide.value?.wudao.attributes ?? [])
const attributeNames = computed(() => attributes.value.map((item) => item.name))
const selectedAttribute = computed(() => (
  attributes.value.find((item) => item.id === selectedAttributeId.value) ?? attributes.value[0] ?? null
))
const regions = computed(() => [
  '全部地区',
  ...Array.from(new Set((guide.value?.xian_ci.shrines ?? []).map((item) => item.region))),
])
const shrineKinds = computed(() => [
  '全部类型',
  ...Array.from(new Set((guide.value?.xian_ci.shrines ?? []).map((item) => item.kind))),
])
const filteredShrines = computed(() => {
  const query = shrineQuery.value.trim().toLowerCase()
  return (guide.value?.xian_ci.shrines ?? []).filter((shrine) => {
    if (shrineRegion.value !== '全部地区' && shrine.region !== shrineRegion.value) return false
    if (shrineKind.value !== '全部类型' && shrine.kind !== shrineKind.value) return false
    if (!query) return true
    return [
      shrine.name,
      shrine.region,
      shrine.kind,
      shrine.unlock,
      ...shrine.immortals,
      ...shrine.rewards.flatMap((reward) => [reward.name, reward.effect]),
    ].some((value) => value.toLowerCase().includes(query))
  })
})
const selectedShrine = computed(() => (
  filteredShrines.value.find((item) => item.id === selectedShrineId.value)
  ?? filteredShrines.value[0]
  ?? null
))

const attributeCounts = computed(() => {
  const counts = new Map<string, number>()
  souls.value
    .flatMap((soul) => soul.slice(1))
    .forEach((name) => counts.set(name, (counts.get(name) ?? 0) + 1))
  return counts
})
const primaryCounts = computed(() => {
  const counts = new Map<string, number>()
  souls.value.forEach((soul) => {
    const primary = soul[0]
    if (primary) counts.set(primary, (counts.get(primary) ?? 0) + 1)
  })
  return counts
})
const activeDomains = computed(() => Array.from(primaryCounts.value.entries())
  .sort((left, right) => right[1] - left[1])
  .map(([name, level]) => ({ name, level })))
const previewField = computed(() => {
  const base = guide.value?.wudao.base_field
  if (!base) return null
  const result = {
    dp_max: base.dp_max,
    charge_seconds: base.charge_seconds,
    dp_cost: base.dp_cost,
    duration_seconds: base.duration_seconds,
    cooldown_seconds: base.cooldown_seconds,
    range: base.range,
  }
  for (const attribute of attributes.value) {
    const count = attributeCounts.value.get(attribute.name) ?? 0
    if (!count) continue
    for (const key of Object.keys(attribute.modifier) as Array<keyof FieldModifier>) {
      const value = attribute.modifier[key]
      if (typeof value === 'number') result[key] += value * count
    }
  }
  return result
})

function formatSigned(value: number, unit = '') {
  return `${value > 0 ? '+' : ''}${value}${unit}`
}

function modifierText(item: WudaoAttribute) {
  const labels: Record<keyof FieldModifier, [string, string]> = {
    dp_max: ['道力上限', ''],
    charge_seconds: ['蓄力', '秒'],
    dp_cost: ['消耗', ''],
    duration_seconds: ['持续', '秒'],
    cooldown_seconds: ['冷却', '秒'],
    range: ['范围', ''],
  }
  return (Object.entries(item.modifier) as Array<[keyof FieldModifier, number]>)
    .map(([key, value]) => `${labels[key][0]}${formatSigned(value, labels[key][1])}`)
    .join(' · ')
}

function chooseAttribute(item: WudaoAttribute) {
  selectedAttributeId.value = item.id
}

function chooseShrine(item: XianCiShrine) {
  selectedShrineId.value = item.id
}

function normalizeSoulRow(row: string[]) {
  const next: string[] = []
  for (const value of row) {
    if (value && !next.includes(value)) next.push(value)
  }
  for (const name of attributeNames.value) {
    if (next.length >= 3) break
    if (!next.includes(name)) next.push(name)
  }
  return next.slice(0, 3)
}

function updateSoul(rowIndex: number, slotIndex: number, value: string) {
  const row = [...souls.value[rowIndex]]
  const duplicateIndex = row.findIndex((name, index) => name === value && index !== slotIndex)
  if (duplicateIndex >= 0) {
    row[duplicateIndex] = row[slotIndex]
  }
  row[slotIndex] = value
  souls.value[rowIndex] = normalizeSoulRow(row)
  souls.value = [...souls.value]
}

async function loadGuide() {
  loading.value = true
  errorText.value = ''
  try {
    guide.value = await fetchGuigubahuangGuide()
    souls.value = souls.value.map(normalizeSoulRow)
  } catch (error) {
    errorText.value = error instanceof Error ? error.message : '读取攻略数据失败'
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  const storedTab = window.localStorage.getItem(TAB_STORAGE_KEY)
  if (storedTab === 'wudao' || storedTab === 'xian-ci') activeTab.value = storedTab
  try {
    const storedSouls = JSON.parse(window.localStorage.getItem(SOULS_STORAGE_KEY) ?? 'null')
    if (Array.isArray(storedSouls) && storedSouls.length === 3) souls.value = storedSouls
  } catch {
    window.localStorage.removeItem(SOULS_STORAGE_KEY)
  }
  void loadGuide()
})

watch(activeTab, (value) => window.localStorage.setItem(TAB_STORAGE_KEY, value))
watch(souls, (value) => window.localStorage.setItem(SOULS_STORAGE_KEY, JSON.stringify(value)), { deep: true })
watch(filteredShrines, (items) => {
  if (items.length && !items.some((item) => item.id === selectedShrineId.value)) {
    selectedShrineId.value = items[0].id
  }
})
</script>

<template>
  <main class="guide-page" v-loading="loading">
    <header class="page-head">
      <div>
        <h1>鬼谷八荒 · 修炼攻略</h1>
        <p v-if="guide">本机 Build {{ guide.source.build_id }} 只读配置整理 · {{ guide.source.verified_at }}</p>
      </div>
      <el-segmented v-model="activeTab" :options="[{ label: '悟道', value: 'wudao' }, { label: '仙祠', value: 'xian-ci' }]" />
    </header>

    <el-alert v-if="errorText" :title="errorText" type="error" :closable="false" />

    <template v-if="guide && activeTab === 'wudao'">
      <section class="rule-strip">
        <span v-for="rule in guide.wudao.rules" :key="rule">{{ rule }}</span>
      </section>

      <section class="planner-section">
        <h2>道魂组合</h2>
        <div class="soul-rows">
          <div v-for="(soul, rowIndex) in souls" :key="rowIndex" class="soul-row">
            <strong>道魂 {{ rowIndex + 1 }}</strong>
            <el-select
              v-for="(value, slotIndex) in soul"
              :key="slotIndex"
              :model-value="value"
              :aria-label="`道魂${rowIndex + 1}${slotIndex === 0 ? '主属性' : `副属性${slotIndex}`}`"
              @update:model-value="updateSoul(rowIndex, slotIndex, $event)"
            >
              <el-option
                v-for="name in attributeNames"
                :key="name"
                :label="`${slotIndex === 0 ? '主' : '副'} · ${name}`"
                :value="name"
              />
            </el-select>
          </div>
        </div>
        <div v-if="previewField" class="preview-line">
          <span class="domain-result">
            <b v-for="domain in activeDomains" :key="domain.name">{{ domain.name }}{{ ['零', '一', '二', '三'][domain.level] }}重</b>
          </span>
          <span>道力 {{ previewField.dp_max }}</span>
          <span>蓄力 {{ previewField.charge_seconds }}秒</span>
          <span>消耗 {{ previewField.dp_cost }}</span>
          <span>持续 {{ previewField.duration_seconds }}秒</span>
          <span>冷却 {{ previewField.cooldown_seconds }}秒</span>
          <span>范围 {{ previewField.range }}</span>
        </div>
      </section>

      <section class="atlas-section">
        <h2>属性与领域效果</h2>
        <div class="attribute-layout">
          <div class="table-scroll">
            <table class="attribute-table">
              <thead><tr><th>属性</th><th>领域参数</th></tr></thead>
              <tbody>
                <tr
                  v-for="item in attributes"
                  :key="item.id"
                  :class="{ selected: selectedAttribute?.id === item.id }"
                  @click="chooseAttribute(item)"
                >
                  <td><span class="attribute-dot" :style="{ background: item.color }" />{{ item.name }}</td>
                  <td>{{ modifierText(item) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-if="selectedAttribute" class="domain-detail">
            <h3><span class="attribute-dot" :style="{ background: selectedAttribute.color }" />{{ selectedAttribute.name }}领域</h3>
            <ol>
              <li v-for="(effect, index) in selectedAttribute.levels" :key="effect">
                <b>{{ ['一重', '二重', '三重'][index] }}</b>
                <span>{{ effect }}</span>
              </li>
            </ol>
          </div>
        </div>
      </section>
    </template>

    <template v-else-if="guide">
      <section class="rule-strip">
        <span v-for="rule in guide.xian_ci.rules" :key="rule">{{ rule }}</span>
      </section>

      <section class="shrine-section">
        <div class="filter-row">
          <el-input v-model="shrineQuery" class="search-input" clearable placeholder="搜索仙祠、仙人、仙法或效果" :prefix-icon="Search" />
          <el-select v-model="shrineRegion" aria-label="地区">
            <el-option v-for="region in regions" :key="region" :label="region" :value="region" />
          </el-select>
          <el-select v-model="shrineKind" aria-label="仙法类型">
            <el-option v-for="kind in shrineKinds" :key="kind" :label="kind" :value="kind" />
          </el-select>
        </div>

        <div class="shrine-layout">
          <div class="table-scroll shrine-list">
            <table>
              <thead><tr><th>仙祠</th><th>地区</th><th>仙人</th><th>类型</th></tr></thead>
              <tbody>
                <tr
                  v-for="item in filteredShrines"
                  :key="item.id"
                  :class="{ selected: selectedShrine?.id === item.id }"
                  @click="chooseShrine(item)"
                >
                  <td>{{ item.name }}</td>
                  <td>{{ item.region }}</td>
                  <td>{{ item.immortals.join('、') }}</td>
                  <td>{{ item.kind }}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <article v-if="selectedShrine" class="shrine-detail">
            <header>
              <h2>{{ selectedShrine.name }}</h2>
              <span>{{ selectedShrine.region }} · {{ selectedShrine.immortals.join('、') }}</span>
            </header>
            <dl>
              <dt>认可流程</dt>
              <dd>{{ selectedShrine.unlock }}</dd>
            </dl>
            <table class="reward-table">
              <thead><tr><th>仙法</th><th>本源仙力</th><th>效果</th></tr></thead>
              <tbody>
                <tr v-for="reward in selectedShrine.rewards" :key="reward.name">
                  <td>{{ reward.name }}</td>
                  <td>{{ reward.cost ?? '—' }}</td>
                  <td>{{ reward.effect }}</td>
                </tr>
              </tbody>
            </table>
          </article>
          <el-empty v-else description="没有符合条件的仙祠" :image-size="64" />
        </div>
      </section>
    </template>
  </main>
</template>

<style scoped>
.guide-page {
  box-sizing: border-box;
  height: 100%;
  min-height: 0;
  padding: 16px 20px 24px;
  overflow: auto;
  color: #2c3135;
  background: #f6f7f5;
}

.page-head,
.filter-row,
.preview-line,
.soul-row,
.shrine-detail header {
  display: flex;
  align-items: center;
}

.page-head {
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
}

h1,
h2,
h3,
p {
  margin: 0;
}

h1 { font-size: 22px; }
h2 { font-size: 16px; }
h3 { font-size: 17px; }

.page-head p,
.shrine-detail header span {
  margin-top: 4px;
  color: #71787c;
  font-size: 13px;
}

.rule-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 18px;
  margin-bottom: 14px;
  color: #626a6f;
  font-size: 13px;
  line-height: 1.65;
}

.rule-strip span::before {
  content: '·';
  margin-right: 6px;
  color: #9aa19e;
}

.planner-section,
.atlas-section,
.shrine-section {
  margin-top: 14px;
}

.planner-section h2,
.atlas-section h2 {
  margin-bottom: 9px;
}

.soul-rows {
  display: flex;
  flex-wrap: wrap;
  gap: 7px 18px;
}

.soul-row { gap: 6px; }
.soul-row strong { width: 54px; font-size: 13px; }
.soul-row .el-select { width: 98px; }

.preview-line {
  flex-wrap: wrap;
  gap: 5px 16px;
  margin-top: 10px;
  min-height: 34px;
  padding: 4px 10px;
  border-left: 3px solid #c96044;
  background: #fff;
  color: #535b60;
  font-size: 13px;
}

.domain-result { display: inline-flex; gap: 8px; }
.domain-result b { color: #a64331; }

.attribute-layout,
.shrine-layout {
  display: grid;
  grid-template-columns: max-content minmax(320px, 1fr);
  align-items: start;
  gap: 16px;
}

.table-scroll {
  max-width: 100%;
  overflow: auto;
}

table {
  width: max-content;
  max-width: 100%;
  border-collapse: collapse;
  background: #fff;
  font-size: 13px;
}

th,
td {
  padding: 8px 12px;
  border-bottom: 1px solid #e5e8e5;
  text-align: left;
  vertical-align: top;
}

th {
  color: #596166;
  background: #edf0ed;
  font-weight: 600;
  white-space: nowrap;
}

tbody tr { cursor: pointer; }
tbody tr:hover,
tbody tr.selected { background: #f3f7f1; }
tbody tr.selected { box-shadow: inset 3px 0 #618b55; }

.attribute-table td:first-child { font-weight: 650; white-space: nowrap; }
.attribute-table td:nth-child(2) { white-space: nowrap; }
.attribute-dot {
  display: inline-block;
  width: 9px;
  height: 9px;
  margin-right: 7px;
  border-radius: 50%;
  vertical-align: 1px;
}

.domain-detail,
.shrine-detail {
  min-width: 0;
  padding: 13px 15px;
  border: 1px solid #e0e4df;
  background: #fff;
}

.domain-detail ol {
  display: grid;
  gap: 9px;
  margin: 12px 0 0;
  padding: 0;
  list-style: none;
}

.domain-detail li {
  display: grid;
  grid-template-columns: 42px minmax(0, 1fr);
  gap: 8px;
  line-height: 1.65;
}

.domain-detail li b { color: #666d71; }
.filter-row { gap: 8px; margin-bottom: 10px; }
.filter-row .search-input { width: 310px; }
.filter-row .el-select { width: 132px; }
.shrine-list { max-height: 415px; }
.shrine-list table { min-width: 540px; }
.shrine-list td { white-space: nowrap; }

.shrine-detail header {
  justify-content: space-between;
  gap: 14px;
}

.shrine-detail dl {
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr);
  gap: 8px;
  margin: 13px 0;
  line-height: 1.65;
}

.shrine-detail dt { color: #697176; font-weight: 600; }
.shrine-detail dd { margin: 0; }
.reward-table { width: 100%; }
.reward-table td:first-child { min-width: 120px; font-weight: 650; }
.reward-table td:nth-child(2) { text-align: center; white-space: nowrap; }
.reward-table td:last-child { line-height: 1.65; }

@media (max-width: 960px) {
  .attribute-layout,
  .shrine-layout { grid-template-columns: minmax(0, 1fr); }
  .attribute-table,
  .shrine-list table { width: 100%; }
  .shrine-list { max-height: 330px; }
}

@media (max-width: 720px) {
  .guide-page { padding: 13px; }
  .page-head { align-items: flex-start; flex-direction: column; }
  .soul-rows { display: grid; }
  .filter-row { align-items: stretch; flex-wrap: wrap; }
  .filter-row .search-input { width: 100%; }
  .filter-row .el-select { flex: 1; min-width: 125px; }
}
</style>
