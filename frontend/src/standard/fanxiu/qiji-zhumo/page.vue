<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { QuestionFilled, RefreshLeft } from '@element-plus/icons-vue';

type SpeedMode = 'linear' | 'compound';

type Allocation = {
  attackLevels: number;
  speedLevels: number;
  attackValue: number;
  speedMultiplier: number;
  dps: number;
  relativeDps: number;
};

const STORAGE_KEY = 'codeyun_fanxiu_qiji_zhumo_v1';
const LEGACY_STORAGE_KEY = 'codeyun_fanxiu_stat_optimizer_v1';
const CRIT_POINT_COST = 10;

const DEFAULT_INCREMENTS = [
  8, 9, 9, 10, 11, 12, 13, 14, 15, 16, 17, 19, 20, 22, 24, 26, 28,
  30, 32, 35, 38, 41, 44, 47, 51, 55, 60, 64, 70, 70, 70, 70, 70, 70,
];

const DEFAULT_SETTINGS = {
  jiangshenTokenCount: 235,
  baseAttack: 100,
  speedPercent: 10,
  speedMode: 'linear' as SpeedMode,
  topCount: 10,
  capIncrement: 70,
  incrementText: DEFAULT_INCREMENTS.join(', '),
};

const jiangshenTokenCount = ref(DEFAULT_SETTINGS.jiangshenTokenCount);
const baseAttack = ref(DEFAULT_SETTINGS.baseAttack);
const speedPercent = ref(DEFAULT_SETTINGS.speedPercent);
const speedMode = ref<SpeedMode>(DEFAULT_SETTINGS.speedMode);
const topCount = ref(DEFAULT_SETTINGS.topCount);
const capIncrement = ref(DEFAULT_SETTINGS.capIncrement);
const incrementText = ref(DEFAULT_SETTINGS.incrementText);

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function normalizeNumber(value: unknown, fallback: number, min = 0, max = 1_000_000) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return clamp(parsed, min, max);
}

function normalizeInteger(value: unknown, fallback: number, min = 0, max = 10_000) {
  return Math.round(normalizeNumber(value, fallback, min, max));
}

function normalizeSpeedMode(value: unknown): SpeedMode {
  return value === 'compound' ? 'compound' : 'linear';
}

function parseIncrementInput(raw: string) {
  const tokens = raw
    .split(/[,，\s]+/)
    .map((item) => item.trim())
    .filter(Boolean);
  const values: number[] = [];
  const invalidTokens: string[] = [];

  for (const token of tokens) {
    const parsed = Number(token);
    if (!Number.isFinite(parsed) || parsed < 0) {
      invalidTokens.push(token);
    } else {
      values.push(parsed);
    }
  }

  return { values, invalidTokens };
}

function getAttackValue(
  initialAttack: number,
  attackLevels: number,
  increments: number[],
  overflowIncrement: number,
) {
  const listedLevels = Math.min(attackLevels, increments.length);
  const listedAttack = increments
    .slice(0, listedLevels)
    .reduce((sum, value) => sum + value, initialAttack);
  const overflowLevels = Math.max(0, attackLevels - increments.length);
  return listedAttack + overflowLevels * overflowIncrement;
}

function getSpeedMultiplier(speedLevels: number, speedGain: number, mode: SpeedMode) {
  if (mode === 'compound') {
    return (1 + speedGain) ** speedLevels;
  }
  return 1 + speedLevels * speedGain;
}

function formatNumber(value: number, digits = 2) {
  if (!Number.isFinite(value)) return '-';
  if (Number.isInteger(value)) return String(value);
  return value
    .toFixed(digits)
    .replace(/(\.\d*?)0+$/, '$1')
    .replace(/\.$/, '');
}

function formatMultiplier(value: number) {
  return `${formatNumber(value, 4)}x`;
}

function formatPercent(value: number, digits = 2) {
  return `${formatNumber(value * 100, digits)}%`;
}

const parsedIncrementResult = computed(() => parseIncrementInput(incrementText.value));
const parsedIncrements = computed(() => parsedIncrementResult.value.values);
const incrementParseError = computed(() => {
  const invalid = parsedIncrementResult.value.invalidTokens;
  if (!invalid.length) return '';
  return `无法解析：${invalid.slice(0, 6).join('、')}${invalid.length > 6 ? '...' : ''}`;
});

const effectiveJiangshenTokenCount = computed(() =>
  normalizeInteger(jiangshenTokenCount.value, DEFAULT_SETTINGS.jiangshenTokenCount, 0, 100_000),
);
const effectiveTotalPoints = computed(() => Math.floor(effectiveJiangshenTokenCount.value / 5));
const leftoverJiangshenTokenCount = computed(() => effectiveJiangshenTokenCount.value % 5);
const effectiveCritPointCount = computed(() => Math.min(effectiveTotalPoints.value, CRIT_POINT_COST));
const allocatablePointCount = computed(() => Math.max(0, effectiveTotalPoints.value - CRIT_POINT_COST));
const effectiveBaseAttack = computed(() => normalizeNumber(baseAttack.value, DEFAULT_SETTINGS.baseAttack, 0));
const effectiveSpeedGain = computed(() => normalizeNumber(speedPercent.value, DEFAULT_SETTINGS.speedPercent, 0, 10_000) / 100);
const effectiveCapIncrement = computed(() => normalizeNumber(capIncrement.value, DEFAULT_SETTINGS.capIncrement, 0));
const effectiveTopCount = computed(() => normalizeInteger(topCount.value, DEFAULT_SETTINGS.topCount, 1, 100));

const allocationRows = computed<Allocation[]>(() => {
  if (incrementParseError.value) return [];

  const rows: Omit<Allocation, 'relativeDps'>[] = [];
  const pointCount = allocatablePointCount.value;
  const increments = parsedIncrements.value;

  for (let attackLevels = 0; attackLevels <= pointCount; attackLevels += 1) {
    const speedLevels = pointCount - attackLevels;
    const attackValue = getAttackValue(
      effectiveBaseAttack.value,
      attackLevels,
      increments,
      effectiveCapIncrement.value,
    );
    const speedMultiplier = getSpeedMultiplier(speedLevels, effectiveSpeedGain.value, speedMode.value);
    rows.push({
      attackLevels,
      speedLevels,
      attackValue,
      speedMultiplier,
      dps: attackValue * speedMultiplier,
    });
  }

  rows.sort((left, right) => right.dps - left.dps);
  const bestDps = rows[0]?.dps ?? 0;
  return rows.map((row) => ({
    ...row,
    relativeDps: bestDps > 0 ? row.dps / bestDps : 0,
  }));
});

const bestAllocation = computed(() => allocationRows.value[0] ?? null);
const topAllocations = computed(() => allocationRows.value.slice(0, effectiveTopCount.value));
const incrementCountText = computed(() => `已解析 ${parsedIncrements.value.length} 级`);
const statPointRows = computed(() => {
  const best = bestAllocation.value;
  return [
    {
      key: 'attack',
      label: '攻击',
      points: best?.attackLevels ?? 0,
      effect: best ? `最终 ${formatNumber(best.attackValue, 2)}` : '-',
    },
    {
      key: 'spirit',
      label: '仙力',
      points: 0,
      effect: '未启用',
    },
    {
      key: 'speed',
      label: '攻速',
      points: best?.speedLevels ?? 0,
      effect: best ? formatMultiplier(best.speedMultiplier) : '-',
    },
    {
      key: 'crit',
      label: '暴击',
      points: effectiveCritPointCount.value,
      effect: '已预留',
    },
  ];
});

function canUseLocalStorage() {
  return typeof window !== 'undefined' && typeof window.localStorage !== 'undefined';
}

function saveSettings() {
  if (!canUseLocalStorage()) return;
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify({
    jiangshenTokenCount: jiangshenTokenCount.value,
    baseAttack: baseAttack.value,
    speedPercent: speedPercent.value,
    speedMode: speedMode.value,
    topCount: topCount.value,
    capIncrement: capIncrement.value,
    incrementText: incrementText.value,
  }));
}

function loadSettings() {
  if (!canUseLocalStorage()) return false;
  const raw = window.localStorage.getItem(STORAGE_KEY) ?? window.localStorage.getItem(LEGACY_STORAGE_KEY);
  if (!raw) return false;

  try {
    const data = JSON.parse(raw) as Partial<typeof DEFAULT_SETTINGS> & { totalPoints?: unknown };
    jiangshenTokenCount.value = data.jiangshenTokenCount === undefined
      ? normalizeInteger(data.totalPoints, Math.floor(DEFAULT_SETTINGS.jiangshenTokenCount / 5), 0, 10_000) * 5
      : normalizeInteger(data.jiangshenTokenCount, DEFAULT_SETTINGS.jiangshenTokenCount, 0, 100_000);
    baseAttack.value = normalizeNumber(data.baseAttack, DEFAULT_SETTINGS.baseAttack, 0);
    speedPercent.value = normalizeNumber(data.speedPercent, DEFAULT_SETTINGS.speedPercent, 0);
    speedMode.value = normalizeSpeedMode(data.speedMode);
    topCount.value = normalizeInteger(data.topCount, DEFAULT_SETTINGS.topCount, 1, 100);
    capIncrement.value = normalizeNumber(data.capIncrement, DEFAULT_SETTINGS.capIncrement, 0);
    incrementText.value = typeof data.incrementText === 'string'
      ? data.incrementText
      : DEFAULT_SETTINGS.incrementText;
    return true;
  } catch (error) {
    console.error('Failed to load fanxiu qiji zhumo settings', error);
    return false;
  }
}

function resetToDefault() {
  jiangshenTokenCount.value = DEFAULT_SETTINGS.jiangshenTokenCount;
  baseAttack.value = DEFAULT_SETTINGS.baseAttack;
  speedPercent.value = DEFAULT_SETTINGS.speedPercent;
  speedMode.value = DEFAULT_SETTINGS.speedMode;
  topCount.value = DEFAULT_SETTINGS.topCount;
  capIncrement.value = DEFAULT_SETTINGS.capIncrement;
  incrementText.value = DEFAULT_SETTINGS.incrementText;
  saveSettings();
  ElMessage.success('已恢复默认参数');
}

watch(
  [jiangshenTokenCount, baseAttack, speedPercent, speedMode, topCount, capIncrement, incrementText],
  saveSettings,
);

onMounted(() => {
  loadSettings();
});
</script>

<template>
  <div class="qiji-zhumo-page">
    <div class="page-header">
      <div>
        <h2 class="page-title">活动列表 · 奇技诛魔</h2>
        <p class="page-subtitle">扣除暴击后的攻击/攻速 DPS 计算</p>
      </div>
      <el-button :icon="RefreshLeft" @click="resetToDefault">恢复默认</el-button>
    </div>

    <div class="workspace-grid">
      <el-card class="panel-card settings-panel" shadow="never">
        <template #header>
          <div class="card-header">
            <span>资源</span>
            <el-popover placement="bottom-start" width="340" trigger="click">
              <template #reference>
                <el-button
                  text
                  circle
                  :icon="QuestionFilled"
                  aria-label="查看计算说明"
                />
              </template>
              <div class="optimizer-help">
                <p>DPS = 最终攻击力 x 攻速倍率。</p>
                <p>降神令每 5 个折算 1 次可升级次数，计算时只使用可完整升级的次数。</p>
                <p>暴击按固定 10 点预留；当前不纳入仙力，剩余点数只在攻击和攻速之间搜索。</p>
                <p>攻击点数按增量列表逐级累加；列表用完后，每级使用“列表后增量”。</p>
                <p>攻速线性模式为 1 + n x r，复利模式为 (1 + r)^n。</p>
              </div>
            </el-popover>
          </div>
        </template>

        <div class="settings-grid">
          <label class="setting-item">
            <span class="setting-label-line">
              <span>降神令数量</span>
              <span class="computed-value">
                可升级 {{ effectiveTotalPoints }} 次<span v-if="leftoverJiangshenTokenCount">，余 {{ leftoverJiangshenTokenCount }}</span>
              </span>
            </span>
            <el-input-number
              v-model="jiangshenTokenCount"
              :min="0"
              :max="100000"
              :step="5"
              controls-position="right"
            />
            <span class="setting-note">
              暴击固定 {{ CRIT_POINT_COST }} 点，攻击/攻速可分配 {{ allocatablePointCount }} 点<span v-if="leftoverJiangshenTokenCount">；降神令余 {{ leftoverJiangshenTokenCount }}</span>
            </span>
          </label>
          <label class="setting-item">
            <span>显示前 N 名</span>
            <el-input-number
              v-model="topCount"
              :min="1"
              :max="100"
              :step="1"
              step-strictly
              controls-position="right"
            />
          </label>
        </div>

      </el-card>

      <div class="result-column">
        <el-card class="panel-card" shadow="never">
          <template #header>
            <div class="card-header">
              <span>最优分配</span>
              <span class="header-meta">暴击占用 {{ effectiveCritPointCount }} 点</span>
            </div>
          </template>

          <el-alert
            v-if="incrementParseError"
            :title="incrementParseError"
            type="error"
            :closable="false"
            show-icon
          />

          <template v-if="!incrementParseError && bestAllocation">
            <div class="metric-grid">
              <div class="metric-item highlight">
                <span class="metric-label">有效 DPS</span>
                <strong>{{ formatNumber(bestAllocation.dps, 3) }}</strong>
              </div>
            </div>
          </template>

          <div class="stat-table-scroll">
            <table class="stat-point-table" aria-label="属性配置与等级分配点数">
              <thead>
                <tr>
                  <th scope="col">属性</th>
                  <th scope="col" class="point-column">等级分配点数</th>
                  <th scope="col" class="effect-column">当前效果</th>
                  <th scope="col" class="config-column">基础与配置</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in statPointRows" :key="row.key">
                  <td>{{ row.label }}</td>
                  <td class="point-column"><strong>{{ row.points }}</strong></td>
                  <td class="effect-column">
                    <span :class="['effect-value', { muted: row.key === 'spirit' }]">{{ row.effect }}</span>
                  </td>
                  <td class="config-column">
                    <div v-if="row.key === 'attack'" class="config-stack">
                      <label class="inline-number-control">
                        <span>基础攻击</span>
                        <el-input-number
                          v-model="baseAttack"
                          :min="0"
                          :step="1"
                          controls-position="right"
                        />
                      </label>
                      <div class="attack-increment-config">
                        <div class="attack-increment-header">
                          <span>每级增幅</span>
                          <span :class="['increment-status', { error: Boolean(incrementParseError) }]">
                            {{ incrementParseError || incrementCountText }}
                          </span>
                        </div>
                        <el-input
                          v-model="incrementText"
                          type="textarea"
                          :autosize="{ minRows: 3, maxRows: 7 }"
                          spellcheck="false"
                        />
                      </div>
                      <label class="inline-number-control">
                        <span>列表后增量</span>
                        <el-input-number
                          v-model="capIncrement"
                          :min="0"
                          :step="1"
                          :precision="2"
                          controls-position="right"
                        />
                      </label>
                    </div>
                    <div v-else-if="row.key === 'speed'" class="config-stack">
                      <label class="inline-number-control">
                        <span>每级攻速(%)</span>
                        <el-input-number
                          v-model="speedPercent"
                          :min="0"
                          :step="1"
                          :precision="2"
                          controls-position="right"
                        />
                      </label>
                      <div class="speed-mode-config">
                        <span>攻速模型</span>
                        <el-radio-group v-model="speedMode">
                          <el-radio-button value="linear">线性</el-radio-button>
                          <el-radio-button value="compound">复利</el-radio-button>
                        </el-radio-group>
                      </div>
                    </div>
                    <span v-else-if="row.key === 'crit'" class="config-note">
                      固定消耗 {{ CRIT_POINT_COST }} 点
                    </span>
                    <span v-else class="config-note">当前不走仙力体系</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </el-card>

        <el-card class="panel-card" shadow="never">
          <template #header>
            <div class="card-header">
              <span>候选分配</span>
              <span class="header-meta">剩余 {{ allocatablePointCount }} 点</span>
            </div>
          </template>

          <el-table
            :data="topAllocations"
            border
            size="small"
            table-layout="auto"
            :fit="false"
            class="allocation-table"
          >
            <el-table-column type="index" label="#" width="58" align="center" />
            <el-table-column label="攻击点" width="88" align="right">
              <template #default="{ row }">
                {{ row.attackLevels }}
              </template>
            </el-table-column>
            <el-table-column label="攻速点" width="88" align="right">
              <template #default="{ row }">
                {{ row.speedLevels }}
              </template>
            </el-table-column>
            <el-table-column label="最终攻击" width="110" align="right">
              <template #default="{ row }">
                {{ formatNumber(row.attackValue, 2) }}
              </template>
            </el-table-column>
            <el-table-column label="攻速倍率" width="110" align="right">
              <template #default="{ row }">
                {{ formatMultiplier(row.speedMultiplier) }}
              </template>
            </el-table-column>
            <el-table-column label="DPS" width="130" align="right">
              <template #default="{ row }">
                {{ formatNumber(row.dps, 3) }}
              </template>
            </el-table-column>
            <el-table-column label="相对最优" min-width="160">
              <template #default="{ row }">
                <div class="relative-cell">
                  <div class="relative-bar">
                    <span :style="{ width: formatPercent(row.relativeDps, 4) }" />
                  </div>
                  <span class="relative-text">{{ formatPercent(row.relativeDps, 2) }}</span>
                </div>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </div>
    </div>
  </div>
</template>

<style scoped>
.qiji-zhumo-page {
  padding: 24px;
  color: #1f2d3d;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.page-title {
  margin: 0;
  font-size: 24px;
  font-weight: 700;
}

.page-subtitle {
  margin: 8px 0 0;
  color: #667085;
}

.workspace-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}

.result-column {
  display: grid;
  gap: 16px;
}

.panel-card {
  border-radius: 6px;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  font-weight: 600;
}

.header-meta {
  color: #667085;
  font-size: 12px;
  font-weight: 400;
  white-space: nowrap;
}

.optimizer-help {
  color: #475467;
  line-height: 1.65;
}

.optimizer-help p {
  margin: 0 0 8px;
}

.optimizer-help p:last-child {
  margin-bottom: 0;
}

.settings-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 14px;
  width: min(100%, 360px);
}

.setting-item {
  display: grid;
  gap: 8px;
  color: #667085;
  font-size: 13px;
}

.setting-label-line {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.computed-value {
  color: #475467;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.setting-note {
  color: #667085;
  font-size: 12px;
  line-height: 1.5;
}

.setting-item :deep(.el-input-number),
.setting-item :deep(.el-radio-group) {
  width: 100%;
}

.setting-item :deep(.el-radio-button) {
  flex: 1;
}

.setting-item :deep(.el-radio-button__inner) {
  width: 100%;
}

.increment-status {
  color: #667085;
  font-size: 12px;
}

.increment-status.error {
  color: #c03535;
}

.metric-grid {
  display: grid;
  grid-template-columns: minmax(180px, 260px);
  gap: 10px;
}

.metric-item {
  display: grid;
  gap: 5px;
  padding: 12px;
  border: 1px solid #e7ebf2;
  border-radius: 6px;
  background: #fafbfc;
}

.metric-item.highlight {
  border-color: #b9c9f5;
  background: #f4f7ff;
}

.metric-label {
  color: #667085;
  font-size: 12px;
}

.metric-item strong {
  color: #101828;
  font-size: 18px;
}

.stat-table-scroll {
  margin-top: 16px;
  overflow-x: auto;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
}

.stat-point-table {
  width: 100%;
  min-width: 560px;
  overflow: hidden;
  border-spacing: 0;
  color: #344054;
  table-layout: fixed;
}

.stat-point-table th {
  background: #f7f8fa;
  color: #667085;
  font-size: 12px;
  font-weight: 600;
}

.stat-point-table th,
.stat-point-table td {
  padding: 9px 12px;
  border-bottom: 1px solid #eef1f6;
  text-align: left;
  vertical-align: top;
  white-space: nowrap;
}

.stat-point-table tr:last-child td {
  border-bottom: 0;
}

.stat-point-table th:first-child,
.stat-point-table td:first-child {
  width: 72px;
}

.stat-point-table .point-column {
  width: 112px;
  text-align: right;
}

.stat-point-table .effect-column {
  width: 110px;
  text-align: left;
}

.stat-point-table .config-column {
  width: auto;
  min-width: 280px;
  text-align: left;
  white-space: normal;
}

.stat-point-table strong {
  color: #101828;
  font-variant-numeric: tabular-nums;
}

.effect-value {
  color: #101828;
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}

.effect-value.muted {
  color: #98a2b3;
  font-weight: 400;
}

.config-stack {
  display: grid;
  gap: 10px;
}

.inline-number-control,
.speed-mode-config {
  display: grid;
  grid-template-columns: 78px minmax(0, 1fr);
  gap: 10px;
  align-items: center;
  color: #667085;
  font-size: 12px;
}

.inline-number-control :deep(.el-input-number) {
  width: 100%;
}

.speed-mode-config :deep(.el-radio-group) {
  width: 100%;
}

.speed-mode-config :deep(.el-radio-button) {
  flex: 1;
}

.speed-mode-config :deep(.el-radio-button__inner) {
  width: 100%;
}

.attack-increment-config {
  display: grid;
  gap: 6px;
}

.attack-increment-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  color: #667085;
  font-size: 12px;
}

.attack-increment-config :deep(.el-textarea__inner) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
}

.config-note {
  color: #667085;
  font-size: 12px;
}

.allocation-table {
  width: 100%;
}

.relative-cell {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 140px;
}

.relative-bar {
  position: relative;
  width: 90px;
  height: 8px;
  overflow: hidden;
  border-radius: 999px;
  background: #edf0f5;
}

.relative-bar span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: #f59e0b;
}

.relative-text {
  color: #475467;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

@media (max-width: 1080px) {
  .workspace-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 640px) {
  .qiji-zhumo-page {
    padding: 16px;
  }

  .page-header,
  .settings-grid,
  .metric-grid {
    grid-template-columns: 1fr;
  }

  .page-header {
    display: grid;
  }
}
</style>
