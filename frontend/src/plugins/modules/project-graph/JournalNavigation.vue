<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { GraphDocument } from './storage'
import { dayLabel, journalWeek, localDay, shiftDay } from './journal'

const props = defineProps<{ documents: GraphDocument[]; selected: string; disabled: boolean }>()
const emit = defineEmits<{ open: [day: string] }>()
const search = ref('')
const weekAnchor = ref(props.selected)
watch(() => props.selected, day => { weekAnchor.value = day })
const days = computed(() => new Set(props.documents.map(doc => doc.journalDate).filter(Boolean)))
const groups = computed(() => {
  const result = new Map<string, GraphDocument[]>()
  for (const doc of props.documents.filter(doc => doc.journalDate && `${doc.journalDate} ${doc.title}`.includes(search.value.trim()))
    .sort((a, b) => b.journalDate!.localeCompare(a.journalDate!))) {
    const month = doc.journalDate!.slice(0, 7)
    if (!result.has(month)) result.set(month, [])
    result.get(month)!.push(doc)
  }
  return [...result].map(([month, documents]) => ({ month, documents }))
})
function selectDate(event: Event) {
  const input = event.target as HTMLInputElement
  if (input.value && input.validity.valid) emit('open', input.value)
}
</script>

<template>
  <section class="journal-navigation" aria-label="每日记录">
    <div class="journal-heading"><strong>每日记录</strong><button :disabled="disabled" @click="emit('open', localDay())">今天</button></div>
    <div class="journal-date-picker">
      <button aria-label="上一周" :disabled="disabled" @click="weekAnchor = shiftDay(weekAnchor, -7)">‹</button>
      <input type="date" aria-label="选择记录日期" :value="weekAnchor" min="0001-01-01" max="9999-12-31" :disabled="disabled" @change="selectDate">
      <button aria-label="下一周" :disabled="disabled" @click="weekAnchor = shiftDay(weekAnchor, 7)">›</button>
    </div>
    <div class="journal-week">
      <button v-for="(day, index) in journalWeek(weekAnchor)" :key="day" :disabled="disabled" :aria-label="dayLabel(day)" :aria-pressed="day === selected" :title="`${day}${days.has(day) ? ' · 已有记录' : ''}`" @click="emit('open', day)">
        <small>{{ ['一', '二', '三', '四', '五', '六', '日'][index] }}</small><span>{{ Number(day.slice(-2)) }}</span><i :class="{ recorded: days.has(day) }" />
      </button>
    </div>
    <input v-model="search" class="journal-search" type="search" aria-label="搜索每日记录" placeholder="搜索日期或标题">
    <div class="journal-history">
      <section v-for="group in groups" :key="group.month">
        <h3>{{ group.month.replace('-', ' 年 ') }} 月</h3>
        <button v-for="doc in group.documents" :key="doc.id" :disabled="disabled" :aria-current="doc.journalDate === selected ? 'date' : undefined" @click="emit('open', doc.journalDate!)">
          <span>{{ dayLabel(doc.journalDate!) }}</span><small v-if="doc.title !== doc.journalDate">{{ doc.title }}</small>
        </button>
      </section>
      <p v-if="!groups.length">{{ search ? '没有匹配的记录' : '选择日期，开始画下当天的想法。' }}</p>
    </div>
  </section>
</template>

<style scoped>
.journal-navigation{display:flex;flex-direction:column;height:100%;min-height:0;padding:12px;box-sizing:border-box;gap:14px;color:var(--reader-text)}
button,input{font:inherit;color:inherit;min-width:0;border:1px solid var(--reader-border);border-radius:6px;background:var(--reader-content);padding:6px;box-sizing:border-box}
button{cursor:pointer}button:hover{background:var(--reader-hover)}button:disabled{opacity:.5;cursor:default}.journal-heading{display:flex;align-items:center;justify-content:space-between}.journal-heading button{font-size:12px;padding:5px 12px}
.journal-date-picker{display:flex;gap:6px}.journal-date-picker input{flex:1;width:0;color-scheme:light dark}.journal-date-picker button{width:27px}
.journal-week{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:2px}.journal-week button{display:flex;flex-direction:column;align-items:center;gap:7px;border-color:transparent;padding:7px 0}.journal-week small{font-size:11px;color:var(--reader-muted)}.journal-week i{width:4px;height:4px;border-radius:50%;background:transparent}.journal-week i.recorded{background:var(--reader-active-text,#609ef8)}
button[aria-pressed=true],button[aria-current=date]{background:var(--reader-active-bg,var(--reader-hover));color:var(--reader-active-text,#609ef8);border-color:var(--reader-border)}
.journal-search{width:100%;font-size:12px;padding:8px}.journal-history{overflow:auto;flex:1;min-height:0}.journal-history h3{font-size:11px;color:var(--reader-muted);font-weight:500;margin:12px 0 6px}.journal-history button{display:flex;flex-direction:column;gap:5px;text-align:left;width:100%;border-color:transparent;background:transparent;padding:10px 8px}.journal-history button[aria-current=date]{background:var(--reader-hover)}.journal-history small{color:var(--reader-muted);white-space:nowrap;text-overflow:ellipsis;overflow:hidden;max-width:100%}.journal-history p{font-size:12px;color:var(--reader-muted);line-height:1.8}
</style>
