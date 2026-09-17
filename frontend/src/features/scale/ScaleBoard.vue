<template>
  <div class="scale-board">
    <section class="board">
      <div class="fraction">
        <div class="slot">
          <span class="amount" :title="amountTitle">{{ amountText }}</span>
          <select v-model="unitId">
            <option v-for="unit in unitChoices" :key="unit.name" :value="unit.name">{{ unit.label }}</option>
          </select>
        </div>
        <div class="bar" />
        <select :value="chosen.b" @change="pin('b', $event)">
          <option v-if="!chosen.b" value="" disabled>选一个参照物</option>
          <option v-for="option in bridgeChoices" :key="option.name" :value="option.name">{{ option.label }}</option>
        </select>
      </div>

      <div class="equals">=</div>

      <div class="groups">
        <template v-for="(group, index) in displayGroups" :key="`${index}-${group.c.name}-${group.power}`">
          <span v-if="index > 0" class="times">×</span>
          <div class="fraction">
            <select v-if="!chain" :value="chosen.c" @change="pin('c', $event)">
              <option v-if="!chosen.c" value="" disabled>选一个参照物</option>
              <option v-for="option in visibleChoices" :key="option.name" :value="option.name">{{ option.label }}</option>
            </select>
            <span v-else class="cell">{{ label(group.c) }}</span>
            <div class="bar" />
            <select v-if="!chain" :value="chosen.d" @change="pin('d', $event)">
              <option v-if="!chosen.d" value="" disabled>选一个参照物</option>
              <option v-for="option in visibleChoices" :key="option.name" :value="option.name">{{ option.label }}</option>
            </select>
            <span v-else class="cell">{{ label(group.d) }}</span>
          </div>
          <span v-if="group.power > 1" class="power">{{ toSuperscript(group.power) }}</span>
        </template>
      </div>
    </section>

    <p v-if="groups.length === 0" class="empty">
      {{ hasManual ? '这个组合对不上，换个参照物试试' : '这个尺度上找不到能对上的日常实物' }}
    </p>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'

import {
  bridgeOptions,
  everydayAnchors,
  findChain,
  findProportions,
  findUnit,
  formatSignificant,
  labelOf,
  resolveInput,
  toSuperscript,
  unitOptions,
  unitsByScale,
  type ChainGroup,
  type NamedValue,
  type ScaleSystem,
} from '@/utils/scale'

const props = defineProps<{ system: ScaleSystem }>()

/** a 就是「1 个所选单位」；左边那个数是自动微调出来的。 */
const unitId = ref(props.system.units[0]?.id ?? '')
const manual = reactive<{ b: string | null; c: string | null; d: string | null }>({ b: null, c: null, d: null })

const unitChoices = computed(() => unitOptions(props.system))
const bridgeChoices = computed(() => bridgeOptions(props.system))
const visibleChoices = computed(() => {
  const system = props.system
  return [...everydayAnchors(system), ...system.rulers.filter((ruler) => ruler.value >= system.visible.min)]
    .sort((left, right) => left.value - right.value)
    .map((item) => ({ name: item.name, label: labelOf(system, item) }))
})

/** 默认单位取系统指定的那一档，避免一上来就是最小单位。 */
watch(() => props.system, (system) => {
  const sorted = unitsByScale(system)
  const preferred = system.defaultUnit ?? sorted.find((unit) => unit.symbol === 'm')?.id
  unitId.value = preferred ?? sorted[Math.floor(sorted.length / 2)]?.id ?? ''
  manual.b = null
  manual.c = null
  manual.d = null
}, { immediate: true })

const label = (item: NamedValue) => labelOf(props.system, item)

const target = computed(() => findUnit(props.system, unitId.value)?.factor ?? null)

const resolve = (name: string | null) => (name ? resolveInput(props.system, name) : null)

const pick = computed(() => {
  if (target.value === null) return null
  const fix = { b: resolve(manual.b) ?? undefined, c: resolve(manual.c) ?? undefined, d: resolve(manual.d) ?? undefined }
  return findProportions(props.system, target.value, { limit: 1, fix })[0] ?? null
})

/** b 一旦手动钉住，一组分式常常装不下整段差距，这时右边自动拆成几组相乘。 */
const chain = computed(() => {
  if (!manual.b || target.value === null) return null
  const b = resolve(manual.b)
  if (!b) return null
  return findChain(props.system, target.value, { b })
})

const groups = computed<ChainGroup[]>(() => {
  if (chain.value) return chain.value.groups
  const item = pick.value
  if (!item) return []
  return [{ c: item.terms.c, d: item.terms.d, ratio: item.terms.c.value / item.terms.d.value }]
})

const hasManual = computed(() => Boolean(manual.b || manual.c || manual.d))

/** 共用一个分母时分子往往也一样，同一组分式连着出现就写成一个幂。 */
const displayGroups = computed(() => {
  const list: { c: NamedValue; d: NamedValue; power: number }[] = []
  for (const group of groups.value) {
    const last = list[list.length - 1]
    if (last && last.c.name === group.c.name && last.d.name === group.d.name) last.power += 1
    else list.push({ c: group.c, d: group.d, power: 1 })
  }
  return list
})

const calibrated = computed(() => (chain.value ? chain.value.factor : pick.value?.factor ?? null))
const amountText = computed(() => (calibrated.value === null ? '1' : formatSignificant(calibrated.value, 3)))

const chosen = computed(() => ({
  b: manual.b ?? pick.value?.terms.b.name ?? '',
  c: manual.c ?? pick.value?.terms.c.name ?? '',
  d: manual.d ?? pick.value?.terms.d.name ?? '',
}))

const amountTitle = computed(() => {
  if (groups.value.length === 0) return ''
  const unit = findUnit(props.system, unitId.value)?.name ?? ''
  return `自动微调值：${amountText.value} ${unit} 之于 ${chosen.value.b}，正好等于右边 ${groups.value.length} 组相乘`
})

function pin(slot: 'b' | 'c' | 'd', event: Event) {
  manual[slot] = (event.target as HTMLSelectElement).value || null
}

watch(target, () => {
  manual.b = null
  manual.c = null
  manual.d = null
})
</script>

<style scoped>
.board {
  display: inline-flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}

.groups {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.times {
  font-size: 16px;
  opacity: 0.5;
}

.power {
  align-self: center;
  font-size: 15px;
  color: #3b82f6;
}

.fraction {
  display: inline-flex;
  flex-direction: column;
  gap: 4px;
}

.slot {
  display: flex;
  gap: 8px;
}

select,
.cell,
.amount {
  field-sizing: content;
  min-width: 8ch;
  max-width: 34ch;
  padding: 7px 9px;
  font: inherit;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  color: inherit;
  background: transparent;
  border: 1px solid color-mix(in srgb, currentColor 22%, transparent);
  border-radius: 6px;
}

.cell {
  border-color: transparent;
  white-space: nowrap;
}

/* 这个数不让手改：它是最后一步自动微调出来的 */
.amount {
  min-width: 4ch;
  color: #3b82f6;
  border-style: dashed;
}

select:focus {
  outline: 2px solid color-mix(in srgb, currentColor 40%, transparent);
  outline-offset: 1px;
}

.bar {
  height: 1px;
  background: color-mix(in srgb, currentColor 45%, transparent);
}

.equals {
  font-size: 20px;
  opacity: 0.6;
}

.empty {
  margin: 12px 0 0;
  font-size: 13px;
  color: color-mix(in srgb, currentColor 55%, transparent);
}
</style>
