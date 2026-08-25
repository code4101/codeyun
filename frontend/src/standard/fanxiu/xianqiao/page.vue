<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import {
  getFanxiuXianqiaoMechanics,
  type FanxiuXianqiaoMechanics,
} from '@/api/fanxiu'
import { formatChineseCompactNumber } from '@/utils/numberFormat'

const SYSTEM_STORAGE_KEY = 'fanxiu:xianqiao:selected-system'
const mechanics = ref<FanxiuXianqiaoMechanics | null>(null)
const loading = ref(true)
const errorText = ref('')
const selectedSystemId = ref(1)

const selectedSystem = computed(() => (
  mechanics.value?.systems.find(item => item.id === selectedSystemId.value)
  ?? mechanics.value?.systems[0]
  ?? null
))
const weeklyBuffs = computed(() => mechanics.value?.trial.buffs.filter(item => item.kind === '周天增益') ?? [])
const difficultyBuffs = computed(() => mechanics.value?.trial.buffs.filter(item => item.kind === '难度选项') ?? [])

const levelList = (values: number[]) => values.length ? values.map(value => `${value}级`).join('、') : '—'
const businessText = (value: string) => value.replaceAll('拾层', '十层').replaceAll('|', '；')
const systemShortName = (systemId: number) => (
  mechanics.value?.systems.find(item => item.id === systemId)?.name.split('·')[0]
  ?? `体系${systemId}`
)

const loadMechanics = async () => {
  loading.value = true
  errorText.value = ''
  try {
    mechanics.value = await getFanxiuXianqiaoMechanics()
    const storedSystem = Number(window.localStorage.getItem(SYSTEM_STORAGE_KEY))
    if (mechanics.value.systems.some(item => item.id === storedSystem)) {
      selectedSystemId.value = storedSystem
    }
  } catch (error: any) {
    errorText.value = error?.response?.data?.detail || error?.message || '仙窍系统数据读取失败'
  } finally {
    loading.value = false
  }
}

watch(selectedSystemId, (value) => {
  window.localStorage.setItem(SYSTEM_STORAGE_KEY, String(value))
})

onMounted(loadMechanics)
</script>

<template>
  <main class="xianqiao-page">
    <header class="page-heading">
      <div>
        <h2>仙窍系统</h2>
      </div>
    </header>

    <div v-if="loading" class="state-panel" aria-live="polite">正在整理仙窍系统…</div>
    <div v-else-if="errorText" class="state-panel state-panel--error">
      <span>{{ errorText }}</span>
      <button type="button" @click="loadMechanics">重新读取机制</button>
    </div>

    <template v-else-if="mechanics && selectedSystem">
      <section class="realm-overview">
        <h3>境界、体系与六个部位</h3>
        <p>数值为各部位修满五重所需的累计溢出修为。</p>
        <div class="realm-table-scroll">
          <table>
            <thead>
              <tr>
                <th>境界瓶颈</th>
                <th>仙窍体系</th>
                <th v-for="part in mechanics.systems[0]?.parts ?? []" :key="`part-heading-${part.id}`">
                  {{ part.name }}
                </th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="system in mechanics.systems"
                :key="`realm-${system.id}`"
                :class="{ active: system.id === selectedSystemId }"
              >
                <td>{{ system.name.split('·')[1] }}十层圆满</td>
                <td>
                  <button type="button" @click="selectedSystemId = system.id">
                    {{ system.name.split('·')[0] }}
                  </button>
                </td>
                <td v-for="part in system.parts" :key="`${system.id}-${part.id}`">
                  {{ formatChineseCompactNumber(part.total_exp) }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="element-section">
        <div class="section-heading">
          <div>
            <h3>{{ selectedSystem.name.split('·')[0] }}五行周天</h3>
          </div>
        </div>
        <div class="element-table-scroll">
          <table class="element-table">
            <colgroup>
              <col class="element-name-col">
              <col class="element-summary-col">
              <col v-for="level in selectedSystem.elements[0]?.levels ?? []" :key="`level-col-${level.level}`" class="element-level-col">
            </colgroup>
            <thead>
              <tr>
                <th>元素</th>
                <th>主要作用</th>
                <th v-for="level in selectedSystem.elements[0]?.levels ?? []" :key="`level-heading-${level.level}`">
                  {{ level.required_count }}个 · {{ level.level }}级
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="element in selectedSystem.elements" :key="element.id">
                <td><b>{{ element.name }}</b></td>
                <td>{{ element.purpose }}</td>
                <td v-for="level in element.levels" :key="level.level">
                  {{ businessText(level.effect) }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="element-note">只统计当前仙窍体系已穿戴仙纹上的元素；五种元素分别累计，互不合并。单系达到3/6/9/12个时，依次激活1/2/3/4级效果。</p>
        <p class="element-note"><b>毕业配置：</b>六个部位各装备满强化仙品仙纹，共6个部位 × 6个元素槽 = 36槽。目标配置为12金、12水、12火，三系均激活4级周天；土、木完全不需要。</p>
      </section>

      <section class="progression-section">
        <h3>养成顺序</h3>
        <ol>
          <li><b>前期集中元素：</b>仙品尚未成套时，优先把同一种元素凑到更高档位；6个同元素优于3个 + 3个，9个同元素优于6个 + 3个。</li>
          <li><b>清理过渡仙纹：</b>绝品及以下可直接分解；仙品只保留不含木、土，并能为金、水、火毕业配置提供有效元素槽的仙纹。</li>
          <li><b>强化控制投入：</b>先强化确定会长期使用的仙品；冲最后一个元素槽成本很高，只投入毕业候选。分解仅返还80%已投入强化经验。</li>
        </ol>
      </section>

      <section class="trial-section">
        <div class="section-heading">
          <div>
            <h3>仙窍试炼</h3>
          </div>
        </div>
        <div class="trial-table-scroll">
          <table class="trial-table">
            <thead>
              <tr>
                <th>仙窍体系</th>
                <th>试炼类型</th>
                <th>试炼目标</th>
                <th>开启条件</th>
                <th>难度与结算</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="mode in mechanics.trial.modes" :key="mode.id">
                <td><b>{{ systemShortName(mode.system_id) }}</b></td>
                <td>{{ mode.group }}</td>
                <td>{{ mode.enemy }}</td>
                <td>{{ businessText(mode.unlock_text) }}</td>
                <td v-if="mode.reward_tier_count">
                  难度{{ mode.difficulty_min }}–{{ mode.difficulty_max }}，共{{ mode.reward_tier_count }}档奖励
                </td>
                <td v-else>按通关时间和活动任务结算</td>
              </tr>
            </tbody>
          </table>
        </div>
        <ul class="trial-notes">
          <li><b>奖励次数：</b>每日有{{ mechanics.trial.daily_reward_times }}次奖励次数。</li>
          <li><b>周天增益：</b>{{ weeklyBuffs.map(item => item.description.replace('【%s】', '')).join('、') }}；消耗五行周天带来的增益点。</li>
          <li><b>难度换奖励：</b>可指定掉落元素，并从{{ difficultyBuffs.length - 1 }}类怪物强化中逐级加码；总点数决定常规试炼奖励档。</li>
        </ul>
      </section>

      <section class="quality-section">
        <div class="section-heading">
          <div>
            <h3>仙纹品质与强化</h3>
            <p>高品质不只多槽位，强化上限和副属性次数也会增加；分解时返还已投入经验的80%，再加仙纹自身经验。</p>
          </div>
        </div>
        <div class="table-scroll">
          <table>
            <thead>
              <tr>
                <th>品质</th>
                <th>满级</th>
                <th>元素槽</th>
                <th>扩槽等级</th>
                <th>副属性</th>
                <th>满级经验</th>
                <th>自身经验</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="quality in mechanics.qualities" :key="quality.quality">
                <td><span :class="`quality quality--${quality.quality}`">{{ quality.name }}</span></td>
                <td>{{ quality.max_level }}级</td>
                <td>{{ quality.initial_element_slots }} → {{ quality.element_slots }}</td>
                <td>{{ levelList(quality.element_unlock_levels) }}</td>
                <td>{{ quality.initial_side_attributes }}条起始；{{ levelList(quality.side_attribute_unlock_levels) }}新增</td>
                <td>{{ formatChineseCompactNumber(quality.total_upgrade_exp) }}</td>
                <td>{{ formatChineseCompactNumber(quality.base_feed_exp) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

    </template>
  </main>
</template>

<style scoped>
.xianqiao-page {
  --ink: #23352f;
  --muted: #66736e;
  --line: #d9dfdb;
  --soft: #f5f7f5;
  --accent: #2f6f5c;
  box-sizing: border-box;
  width: 100%;
  min-width: 0;
  max-width: 100%;
  min-height: 100%;
  overflow-x: hidden;
  padding: 24px clamp(18px, 3vw, 42px) 48px;
  color: var(--ink);
}

.page-heading {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 24px;
}

h2, h3, h4, p { margin: 0; }
h2 { font-size: 28px; line-height: 1.2; letter-spacing: -0.02em; }
h3 { font-size: 19px; line-height: 1.35; }
h4 { font-size: 16px; }
.page-heading p, .section-heading p { margin-top: 7px; color: var(--muted); line-height: 1.65; }
.page-heading p { max-width: 72ch; }

.state-panel {
  margin-top: 24px;
  padding: 28px 0;
  color: var(--muted);
}
.state-panel--error { display: flex; align-items: center; gap: 16px; color: #9b3d32; }
.state-panel button { border: 1px solid currentColor; background: transparent; color: inherit; padding: 7px 12px; cursor: pointer; }

.realm-overview { margin-top: 26px; }
.realm-overview > p { margin-top: 6px; color: var(--muted); line-height: 1.6; }
.realm-table-scroll { margin-top: 10px; overflow-x: auto; }
.realm-overview table { min-width: 900px; }
.realm-overview th, .realm-overview td { padding-top: 9px; padding-bottom: 9px; }
.realm-overview th:nth-child(n + 3), .realm-overview td:nth-child(n + 3) { text-align: right; }
.realm-overview tbody tr.active { background: #f1f5f3; }
.realm-overview td button {
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--ink);
  font: inherit;
  font-weight: 700;
  cursor: pointer;
}
.realm-overview td button:hover { color: var(--accent); text-decoration: underline; text-underline-offset: 3px; }

section { margin-top: 34px; }
.section-heading { display: flex; align-items: start; justify-content: space-between; gap: 24px; }

.table-scroll { margin-top: 16px; overflow-x: auto; }
table { width: max-content; min-width: 780px; border-collapse: collapse; font-size: 14px; }
th, td { padding: 11px 24px 11px 0; border-bottom: 1px solid var(--line); text-align: left; white-space: nowrap; }
th { color: var(--muted); font-size: 12px; font-weight: 600; }
.quality { font-weight: 700; }
.quality--3 { color: #4777ad; }
.quality--4 { color: #8855a8; }
.quality--5 { color: #a87513; }
.quality--6 { color: #bf4e34; }

.element-table-scroll { margin-top: 14px; overflow-x: auto; }
.element-table { min-width: 980px; table-layout: fixed; }
.element-table .element-name-col { width: 58px; }
.element-table .element-summary-col { width: 154px; }
.element-table .element-level-col { width: 192px; }
.element-table th, .element-table td { padding-right: 20px; vertical-align: top; white-space: normal; }
.element-table td { color: #4f5e59; line-height: 1.6; }
.element-table td:first-child { color: var(--ink); }
.element-note { margin-top: 10px; color: var(--muted); font-size: 13px; line-height: 1.6; }
.element-note b { color: var(--ink); }

.progression-section ol { margin: 10px 0 0; padding-left: 22px; }
.progression-section li { padding: 3px 0; line-height: 1.65; }

.trial-table-scroll { margin-top: 14px; overflow-x: auto; }
.trial-table { min-width: 880px; }
.trial-table th, .trial-table td { vertical-align: top; }
.trial-table td:nth-child(4), .trial-table td:nth-child(5) { white-space: normal; line-height: 1.55; }
.trial-notes { margin: 10px 0 0; padding-left: 20px; color: var(--muted); }
.trial-notes li { padding: 2px 0; line-height: 1.6; }
.trial-notes b { color: var(--ink); }

button:focus-visible { outline: 2px solid #2d7a63; outline-offset: 2px; }

@media (max-width: 640px) {
  .xianqiao-page { padding: 18px 14px 36px; }
  .page-heading { align-items: start; }
}
</style>
