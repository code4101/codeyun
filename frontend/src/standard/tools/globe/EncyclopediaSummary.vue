<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { encyclopediaLinks, loadCountrySummary, type CountrySummary } from './countryEncyclopedia'
const props = defineProps<{ title: string }>()
const summary = ref<CountrySummary | null>(null)
const summaryState = ref<'loading' | 'ready' | 'unavailable'>('loading')
const links = computed(() => encyclopediaLinks(props.title))
watch(() => props.title, async (country, _, onCleanup) => {
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
</script>

<template>
      <section class="encyclopedia" aria-label="百科简介" aria-live="polite" :aria-busy="summaryState === 'loading'">
        <p v-if="summary" class="extract">{{ summary.text }}</p>
        <p v-else class="muted">{{ summaryState === 'loading' ? '正在加载百科简介…' : '百科简介暂不可用，可通过下方链接查看。' }}</p>
        <div class="encyclopedia-links"><a :href="links.wikipedia" target="_blank" rel="noopener noreferrer">维基百科 ↗</a><a :href="links.baidu" target="_blank" rel="noopener noreferrer">百度百科 ↗</a></div>
        <p v-if="summary" class="muted">简介摘自维基百科（<a href="https://creativecommons.org/licenses/by-sa/4.0/" target="_blank" rel="noopener noreferrer">CC BY-SA</a>）。</p>
      </section>
</template>

<style scoped>
.encyclopedia { margin: 16px 0; }
.extract { font-size: 13px; line-height: 1.8; white-space: pre-line; margin: 0 0 10px; color: #263e5a; }
.encyclopedia-links { display: flex; gap: 16px; font-size: 12px; }
.muted { margin-top: 14px; font-size: 11px; line-height: 1.7; color: #8290a2; }
a { color: #467eae; }
</style>
