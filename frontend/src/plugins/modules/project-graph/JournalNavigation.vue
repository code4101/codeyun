<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import type { GraphDocument } from './storage'
import { dayLabel, journalMonth, localDay, shiftMonth } from './journal'

const props = defineProps<{ documents: GraphDocument[]; selected: string; selectedDays?: string[]; disabled: boolean }>()
const emit = defineEmits<{ open: [day: string, additive?: boolean]; remove: [document: GraphDocument] }>()
const month = ref(props.selected.slice(0, 7))
watch(() => props.selected, day => { month.value = day.slice(0, 7) })
const days = computed(() => new Map(props.documents.filter(doc => doc.journalDate).map(doc => [doc.journalDate!, doc])))
const context = ref<{ doc: GraphDocument; x: number; y: number }>()
const deleteButton = ref<HTMLButtonElement>()
let origin: HTMLElement | undefined
function closeMenu() { context.value = undefined; origin?.focus() }
async function showMenu(event: MouseEvent, day: string) {
  const doc = days.value.get(day)
  if (!doc || props.disabled) { context.value = undefined; return }
  origin = event.currentTarget as HTMLElement
  const rect = origin.getBoundingClientRect()
  context.value = { doc, x: Math.max(8, Math.min(event.clientX || rect.left, window.innerWidth - 190)), y: Math.max(8, Math.min(event.clientY || rect.bottom, window.innerHeight - 52)) }
  await nextTick(); deleteButton.value?.focus()
}
function remove() { const doc = context.value?.doc; closeMenu(); if (doc) emit('remove', doc) }
function selectMonth(event: Event) {
  const input = event.target as HTMLInputElement
  if (input.value && input.validity.valid) month.value = input.value
}
</script>

<template>
  <section class="journal-navigation" aria-label="每日记录">
    <div class="journal-heading"><button :disabled="disabled" @click="emit('open', localDay())">今天</button></div>
    <div class="journal-date-picker">
      <button aria-label="上个月" :disabled="disabled || month === '0001-01'" @click="month = shiftMonth(month, -1)">‹</button>
      <input type="month" aria-label="选择月份" :value="month" min="0001-01" max="9999-12" :disabled="disabled" @change="selectMonth">
      <button aria-label="下个月" :disabled="disabled || month === '9999-12'" @click="month = shiftMonth(month, 1)">›</button>
    </div>
    <div class="journal-month">
      <small v-for="label in ['一', '二', '三', '四', '五', '六', '日']" :key="label">{{ label }}</small>
        <button v-for="day in journalMonth(month)" :key="day" :class="{ 'adjacent-month': !day.startsWith(month) }" :disabled="disabled" :aria-label="dayLabel(day)" :aria-pressed="(selectedDays ?? [selected]).includes(day)" :title="`${day}${days.has(day) ? ' · 已有记录' : ''}`" :data-day="day" @click="emit('open', day, $event.ctrlKey || $event.metaKey)" @contextmenu.prevent.stop="showMenu($event, day)">
          <span>{{ Number(day.slice(-2)) }}</span><i :class="{ recorded: days.has(day) }" />
        </button>
    </div>
    <template v-if="context">
      <div class="menu-dismiss" @pointerdown="closeMenu" @contextmenu.prevent="closeMenu" />
      <div class="day-menu" role="menu" :style="{ left: `${context.x}px`, top: `${context.y}px` }" @keydown.esc.prevent="closeMenu" @keydown.tab="closeMenu">
        <button ref="deleteButton" role="menuitem" :disabled="disabled" @click="remove">删除当天记录</button>
      </div>
    </template>
  </section>
</template>

<style scoped>
.journal-navigation{display:flex;flex-direction:column;min-height:0;padding:12px;box-sizing:border-box;gap:14px;color:var(--reader-text)}
button,input{font:inherit;color:inherit;min-width:0;border:1px solid var(--reader-border);border-radius:6px;background:var(--reader-content);padding:6px;box-sizing:border-box}
button{cursor:pointer}button:hover{background:var(--reader-hover)}button:disabled{opacity:.5;cursor:default}.journal-heading{display:flex;justify-content:flex-end}.journal-heading button{font-size:12px;padding:5px 12px}
.journal-date-picker{display:flex;gap:6px}.journal-date-picker input{flex:1;width:0;color-scheme:light dark}.journal-date-picker button{width:27px}
.journal-month{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:4px 2px}.journal-month>small{text-align:center;padding:4px 0 8px;font-size:11px;color:var(--reader-muted)}.journal-month button{display:flex;flex-direction:column;align-items:center;gap:5px;border-color:transparent;padding:8px 0}.journal-month i{width:4px;height:4px;border-radius:50%;background:transparent}.journal-month i.recorded{background:var(--reader-active-text,#609ef8)}
button[aria-pressed=true]{background:var(--reader-hover);color:var(--reader-active-text,#609ef8);border-color:var(--reader-border)}
.journal-month button.adjacent-month:not([aria-pressed=true]){color:var(--reader-muted);background:transparent}
.menu-dismiss{position:fixed;inset:0;z-index:30}.day-menu{position:fixed;z-index:31;width:180px;padding:4px;background:var(--reader-panel);border:1px solid var(--reader-border);border-radius:6px;box-shadow:0 8px 24px #0003}.day-menu button{width:100%;border:0;text-align:left;color:#e77979;background:transparent}
</style>
