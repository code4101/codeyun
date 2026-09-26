<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { formatChineseCompactNumber } from '@/utils/numberFormat'
import { countryDetails, countryDataSources } from './countryData'
import { encyclopediaLinks, loadCountrySummary, type CountrySummary } from './countryEncyclopedia'

const props = defineProps<{ country: string }>()
const info = computed(() => countryDetails[props.country])
const flagFailed = ref(false)
const summary = ref<CountrySummary | null>(null)
const summaryState = ref<'loading' | 'ready' | 'unavailable'>('loading')
const links = computed(() => encyclopediaLinks(props.country))
watch(() => props.country, async (country, _, onCleanup) => {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), 8000)
  let active = true
  onCleanup(() => { active = false; controller.abort(); window.clearTimeout(timer) })
  summary.value = null
  summaryState.value = 'loading'
  try {
    const result = await loadCountrySummary(country, controller.signal)
    if (active) { summary.value = result; summaryState.value = result ? 'ready' : 'unavailable' }
  } catch {
    if (active) summaryState.value = 'unavailable'
  } finally {
    window.clearTimeout(timer)
  }
}, { immediate: true })
watch(() => props.country, () => { flagFailed.value = false })
const languageNames = new Intl.DisplayNames(['zh-CN'], { type: 'language' })
const currencyNames = new Intl.DisplayNames(['zh-CN'], { type: 'currency' })
const regions: Record<string, string> = {
  Africa: '非洲', Asia: '亚洲', Europe: '欧洲', Oceania: '大洋洲', Antarctica: '南极洲',
  'North America': '北美洲', 'South America': '南美洲', 'Seven seas (open ocean)': '大洋岛屿',
  'Northern Africa': '北非', 'Western Africa': '西非', 'Middle Africa': '中非', 'Eastern Africa': '东非', 'Southern Africa': '南非',
  'Eastern Asia': '东亚', 'South-Eastern Asia': '东南亚', 'Southern Asia': '南亚', 'Central Asia': '中亚', 'Western Asia': '西亚',
  'Northern Europe': '北欧', 'Western Europe': '西欧', 'Southern Europe': '南欧', 'Eastern Europe': '东欧',
  'Northern America': '北美', 'Central America': '中美洲', Caribbean: '加勒比地区',
  'Australia and New Zealand': '澳大利亚与新西兰', Melanesia: '美拉尼西亚', Polynesia: '波利尼西亚', Micronesia: '密克罗尼西亚',
}
const location = computed(() => [...new Set([info.value?.continent, info.value?.region].filter(Boolean))]
  .map(value => regions[value!] ?? value).join(' · '))
const languages = computed(() => Object.entries(info.value?.languages ?? {})
  .map(([code, name]) => languageNames.of(code) || name).join('、'))
const currencies = computed(() => info.value?.currencies.map(code => `${currencyNames.of(code)}（${code}）`).join('、'))
</script>

<template>
  <article class="country-info" aria-label="国家与地区介绍">
    <header>
      <img v-if="info?.code && info.code !== 'AQ' && !flagFailed" :key="info.code"
        :src="`https://flagcdn.com/w160/${info.code.toLowerCase()}.png`" :alt="`${country}旗帜`"
        width="72" height="48" @error="flagFailed = true" />
      <div><h2>{{ country }}</h2><p class="english">{{ info?.englishName }}</p></div>
    </header>
    <p v-if="flagFailed" class="muted">旗帜暂时无法加载</p>
    <template v-if="info">
      <p class="summary">{{ info.officialName || country }}位于{{ location }}<template v-if="info.landlocked !== null">，{{ info.landlocked ? '地处内陆' : '拥有海岸线' }}</template>。</p>
      <section class="encyclopedia" aria-label="百科简介" aria-live="polite" :aria-busy="summaryState === 'loading'">
        <p v-if="summary" class="extract">{{ summary.text }}</p>
        <p v-else class="muted">{{ summaryState === 'loading' ? '正在加载百科简介…' : '百科简介暂不可用，可通过下方链接查看。' }}</p>
        <div class="encyclopedia-links"><a :href="links.wikipedia" target="_blank" rel="noopener noreferrer">维基百科 ↗</a><a :href="links.baidu" target="_blank" rel="noopener noreferrer">百度百科 ↗</a></div>
        <p v-if="summary" class="muted">简介摘自维基百科（<a href="https://creativecommons.org/licenses/by-sa/4.0/" target="_blank" rel="noopener noreferrer">CC BY-SA</a>）。</p>
      </section>
      <dl>
        <div><dt>首都 / 行政中心</dt><dd>{{ info.capital.join('、') || '暂无资料 / 不适用' }}</dd></div>
        <div><dt>面积</dt><dd>{{ info.area !== null ? `${formatChineseCompactNumber(info.area)} 平方公里` : '暂无资料' }}</dd></div>
        <div><dt>人口<small v-if="info.population !== null">（{{ info.populationYear }} 年估计）</small></dt><dd>{{ info.population !== null ? `约 ${formatChineseCompactNumber(info.population)} 人` : '暂无常住人口统计' }}</dd></div>
        <div><dt>语言</dt><dd>{{ languages || '暂无资料 / 不适用' }}</dd></div>
        <div><dt>货币</dt><dd>{{ currencies || '暂无资料 / 不适用' }}</dd></div>
      </dl>
      <footer>资料：<a :href="countryDataSources.geography" target="_blank" rel="noopener noreferrer">Natural Earth</a> · <a :href="countryDataSources.facts" target="_blank" rel="noopener noreferrer">World Countries</a><br>人口为历史估计值；资料快照：2026-09-26。</footer>
    </template>
    <p v-else class="muted">暂无该地区的介绍资料。</p>
  </article>
</template>

<style scoped>
.country-info { margin-top: 20px; width: 100%; min-width: 0; box-sizing: border-box; padding: 24px; border: 1px solid #dbe4ee; border-radius: 14px; background: #fff; color: #263e5a; overflow-wrap: anywhere; }
header { display: flex; align-items: center; gap: 14px; padding-right: 16px; }
img { object-fit: contain; flex-shrink: 0; filter: drop-shadow(0 1px 2px #0002); }
h2 { margin: 0; font-size: 20px; }
.english { margin: 4px 0 0; color: #7b8998; font-size: 12px; }
.summary { margin: 16px 0; font-size: 13px; line-height: 1.8; }
.encyclopedia { margin-bottom: 16px; }
.extract { font-size: 13px; line-height: 1.8; white-space: pre-line; margin: 0 0 10px; }
.encyclopedia-links { display: flex; gap: 16px; font-size: 12px; }
dl { margin: 0; display: grid; grid-template-columns: repeat(auto-fit, minmax(min(220px, 100%), 1fr)); gap: 0 24px; }
dl > div { padding: 9px 0; border-top: 1px solid #edf1f5; }
dt { font-size: 12px; color: #748399; }
dd { margin: 5px 0 0; font-size: 14px; overflow-wrap: anywhere; }
small { font-size: inherit; }
footer, .muted { margin-top: 14px; font-size: 11px; line-height: 1.7; color: #8290a2; }
a { color: #467eae; }
@media (max-width: 640px) { .country-info { padding: 16px; } }
</style>
