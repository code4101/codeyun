<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { QuestionFilled, RefreshLeft } from '@element-plus/icons-vue';

type PresetKey = 'late-ramp' | 'uniform' | 'must-pity' | 'early-ramp';

type ProbabilityRow = {
  draw: number;
  cumulative: number;
  exact: number;
  rangeStart: number;
  rangeEnd: number;
};

const STORAGE_KEY = 'codeyun_fanxiu_lottery_model_v1';
const DEFAULT_GUARANTEE_COUNT = 8;
const MIN_GUARANTEE_COUNT = 2;
const MAX_GUARANTEE_COUNT = 50;

const guaranteeCount = ref(DEFAULT_GUARANTEE_COUNT);
const targetHitCount = ref(1);
const cutPositions = ref<number[]>([]);

const presetLabels: Record<PresetKey, string> = {
  'late-ramp': '软保底',
  uniform: '均匀',
  'must-pity': '纯保底',
  'early-ramp': '前置',
};

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function normalizeGuaranteeCount(value: unknown) {
  const parsed = Math.round(Number(value));
  if (!Number.isFinite(parsed)) return DEFAULT_GUARANTEE_COUNT;
  return clamp(parsed, MIN_GUARANTEE_COUNT, MAX_GUARANTEE_COUNT);
}

function normalizeTargetHitCount(value: unknown) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return 1;
  return Math.max(0, parsed);
}

function normalizeProbability(value: unknown) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return 0;
  return clamp(parsed, 0, 1);
}

function roundProbability(value: number) {
  return Math.round(normalizeProbability(value) * 10000) / 10000;
}

function createUniformCuts(count: number) {
  return Array.from({ length: count - 1 }, (_, index) => roundProbability((index + 1) / count));
}

function createWeightedCuts(count: number, getWeight: (draw: number, count: number) => number) {
  const weights = Array.from({ length: count }, (_, index) => Math.max(0, getWeight(index + 1, count)));
  const total = weights.reduce((sum, weight) => sum + weight, 0) || 1;
  let cumulative = 0;
  return weights.slice(0, -1).map((weight) => {
    cumulative += weight / total;
    return roundProbability(cumulative);
  });
}

function createSoftSegment(startValue: number, endValue: number, stepCount: number) {
  if (stepCount <= 0) return [];

  const distance = endValue - startValue;
  const totalWeight = (stepCount * (stepCount + 1)) / 2;
  let cumulativeWeight = 0;

  return Array.from({ length: stepCount }, (_, index) => {
    cumulativeWeight += index + 1;
    return roundProbability(startValue + distance * (cumulativeWeight / totalWeight));
  });
}

function createAnchoredSoftCuts(count: number, anchorDraw: number, anchorValue: number) {
  const safeDraw = clamp(Math.round(anchorDraw), 1, count - 1);
  const safeValue = roundProbability(anchorValue);
  const beforeAndAnchor = createSoftSegment(0, safeValue, safeDraw);
  const after = createSoftSegment(safeValue, 1, count - safeDraw);
  return [...beforeAndAnchor, ...after].slice(0, count - 1);
}

function getPresetCuts(key: PresetKey, count: number) {
  if (key === 'must-pity') {
    return Array.from({ length: count - 1 }, () => 0);
  }
  if (key === 'uniform') {
    return createUniformCuts(count);
  }
  if (key === 'early-ramp') {
    return createWeightedCuts(count, (draw, total) => total - draw + 1);
  }
  return createWeightedCuts(count, (draw) => draw);
}

function fitCutCount() {
  const needed = guaranteeCount.value - 1;
  const current = [...cutPositions.value]
    .map(normalizeProbability)
    .sort((a, b) => a - b)
    .slice(0, needed);
  const fallback = createUniformCuts(guaranteeCount.value);

  while (current.length < needed) {
    current.push(fallback[current.length] ?? 0);
  }

  cutPositions.value = current.map(roundProbability);
}

function applyPreset(key: PresetKey, notify = true) {
  cutPositions.value = getPresetCuts(key, guaranteeCount.value);
  if (notify) {
    ElMessage.success(`已套用${presetLabels[key]}模型`);
  }
}

function handleGuaranteeChange(value: number | undefined) {
  guaranteeCount.value = normalizeGuaranteeCount(value);
  fitCutCount();
}

function handleTargetHitChange(value: number | undefined) {
  targetHitCount.value = normalizeTargetHitCount(value);
}

function handleCumulativePercentChange(draw: number, value: number | undefined) {
  if (draw >= guaranteeCount.value) return;
  cutPositions.value = createAnchoredSoftCuts(
    guaranteeCount.value,
    draw,
    normalizeProbability((value ?? 0) / 100),
  );
}

const sortedCuts = computed(() => [...cutPositions.value].map(normalizeProbability).sort((a, b) => a - b));

const probabilityRows = computed<ProbabilityRow[]>(() => {
  let previous = 0;
  return [...sortedCuts.value, 1].map((cumulative, index) => {
    const row = {
      draw: index + 1,
      cumulative,
      exact: Math.max(0, cumulative - previous),
      rangeStart: previous,
      rangeEnd: cumulative,
    };
    previous = cumulative;
    return row;
  });
});

const expectedDraws = computed(() =>
  probabilityRows.value.reduce((sum, row) => sum + row.draw * row.exact, 0),
);

const expectedSavedDraws = computed(() => guaranteeCount.value - expectedDraws.value);
const hitRatePerDraw = computed(() => (expectedDraws.value > 0 ? 1 / expectedDraws.value : 0));
const expectedTotalDraws = computed(() => expectedDraws.value * targetHitCount.value);

const trackMarkers = computed(() =>
  sortedCuts.value.map((value, index) => ({
    index,
    value: normalizeProbability(value),
  })),
);

function formatNumber(value: number, digits = 2) {
  return value
    .toFixed(digits)
    .replace(/(\.\d*?)0+$/, '$1')
    .replace(/\.$/, '');
}

function formatPercent(value: number) {
  return `${formatNumber(normalizeProbability(value) * 100, 2)}%`;
}

function getSegmentStyle(row: ProbabilityRow, index: number) {
  const hue = 204 + (index * 23) % 120;
  const width = row.exact > 0 ? Math.max(row.exact * 100, 0.8) : 0;
  return {
    width: `${width}%`,
    backgroundColor: `hsl(${hue} 72% 58%)`,
  };
}

function saveSettings() {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify({
    guaranteeCount: guaranteeCount.value,
    targetHitCount: targetHitCount.value,
    cutPositions: cutPositions.value,
  }));
}

function loadSettings() {
  if (typeof window === 'undefined') return false;
  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (!raw) return false;

  try {
    const data = JSON.parse(raw) as {
      guaranteeCount?: unknown;
      targetHitCount?: unknown;
      cutPositions?: unknown;
    };
    guaranteeCount.value = normalizeGuaranteeCount(data.guaranteeCount);
    targetHitCount.value = normalizeTargetHitCount(data.targetHitCount);
    cutPositions.value = Array.isArray(data.cutPositions)
      ? data.cutPositions.map(normalizeProbability)
      : [];
    fitCutCount();
    return true;
  } catch (error) {
    console.error('Failed to load lottery model settings', error);
    return false;
  }
}

watch([guaranteeCount, targetHitCount, cutPositions], saveSettings, { deep: true });

onMounted(() => {
  if (!loadSettings()) {
    applyPreset('late-ramp', false);
  }
  fitCutCount();
});
</script>

<template>
  <div class="lottery-model-page">
    <div class="page-header">
      <div>
        <h2 class="page-title">抽奖期望模型</h2>
        <p class="page-subtitle">匿名切片保底分布测试工具</p>
      </div>
      <el-button :icon="RefreshLeft" @click="applyPreset('late-ramp')">恢复默认</el-button>
    </div>

    <div class="workspace-grid">
      <el-card class="panel-card" shadow="never">
        <template #header>
          <div class="card-header">
            <div class="card-title-line">
              <span>切片模型</span>
              <el-tag size="small" effect="plain">计算时自动排序</el-tag>
            </div>
            <el-popover placement="bottom-start" width="340" trigger="click">
              <template #reference>
                <el-button
                  text
                  circle
                  :icon="QuestionFilled"
                  aria-label="查看模型说明"
                />
              </template>
              <div class="model-help">
                <p>表格填写的是 P(T&lt;=k) 的累计位置，最后一抽固定为 100%。</p>
                <p>修改任意中间抽次后，会把该抽次当成锚点，并按软保底曲线重排前后区间。</p>
                <p>例如第 5 抽设为 10%，前 4 抽会自动落在 0% 到 10% 之间。</p>
              </div>
            </el-popover>
          </div>
        </template>

        <div class="settings-row">
          <label class="setting-item">
            <span>保底次数</span>
            <el-input-number
              :model-value="guaranteeCount"
              :min="MIN_GUARANTEE_COUNT"
              :max="MAX_GUARANTEE_COUNT"
              :step="1"
              step-strictly
              controls-position="right"
              @change="handleGuaranteeChange"
            />
          </label>
          <label class="setting-item">
            <span>目标中奖次数</span>
            <el-input-number
              :model-value="targetHitCount"
              :min="0"
              :step="1"
              controls-position="right"
              @change="handleTargetHitChange"
            />
          </label>
        </div>

        <div class="preset-row">
          <el-button-group>
            <el-button size="small" @click="applyPreset('late-ramp')">软保底</el-button>
            <el-button size="small" @click="applyPreset('uniform')">均匀</el-button>
            <el-button size="small" @click="applyPreset('must-pity')">纯保底</el-button>
            <el-button size="small" @click="applyPreset('early-ramp')">前置</el-button>
          </el-button-group>
        </div>

        <el-table
          :data="probabilityRows"
          border
          size="small"
          table-layout="auto"
          :fit="false"
          class="cumulative-table"
        >
          <el-table-column label="抽次" width="90" align="right">
            <template #default="{ row }">
              第{{ row.draw }}抽
            </template>
          </el-table-column>
          <el-table-column label="切片位置(%)" width="170" align="right">
            <template #default="{ row }">
              <el-input-number
                v-if="row.draw < guaranteeCount"
                :model-value="roundProbability(row.cumulative) * 100"
                :min="0"
                :max="100"
                :step="0.01"
                :precision="2"
                :controls="false"
                size="small"
                class="position-input"
                @change="(value: number | undefined) => handleCumulativePercentChange(row.draw, value)"
              />
              <span v-else class="fixed-position">100.00</span>
            </template>
          </el-table-column>
        </el-table>

        <div class="track-shell">
          <div class="cut-track">
            <div class="track-line" />
            <div
              v-for="marker in trackMarkers"
              :key="marker.index"
              class="cut-marker"
              :style="{
                left: `${marker.value * 100}%`,
                zIndex: marker.index + 1,
              }"
              :aria-label="`切片位置 ${formatPercent(marker.value)}`"
              :title="formatPercent(marker.value)"
            >
              <span />
            </div>
            <div class="track-axis">
              <span>0%</span>
              <span>100%</span>
            </div>
          </div>
        </div>

        <div class="formula-line">
          E[T] = {{ guaranteeCount }} - Σ累计切片 = {{ formatNumber(expectedDraws, 3) }} 抽
        </div>
      </el-card>

      <div class="result-column">
        <el-card class="panel-card" shadow="never">
          <template #header>
            <div class="card-header">
              <span>结果</span>
            </div>
          </template>

          <div class="metric-grid">
            <div class="metric-item">
              <span class="metric-label">单次中奖期望</span>
              <strong>{{ formatNumber(expectedDraws, 3) }} 抽</strong>
            </div>
            <div class="metric-item">
              <span class="metric-label">平均每抽中奖份额</span>
              <strong>{{ formatPercent(hitRatePerDraw) }}</strong>
            </div>
            <div class="metric-item">
              <span class="metric-label">相对纯保底节省</span>
              <strong>{{ formatNumber(expectedSavedDraws, 3) }} 抽</strong>
            </div>
            <div class="metric-item">
              <span class="metric-label">目标期望抽数</span>
              <strong>{{ formatNumber(expectedTotalDraws, 3) }} 抽</strong>
            </div>
          </div>

          <div class="probability-strip" aria-label="正好中奖概率分布">
            <div
              v-for="(row, index) in probabilityRows"
              :key="row.draw"
              class="probability-segment"
              :style="getSegmentStyle(row, index)"
              :title="`第${row.draw}抽：${formatPercent(row.exact)}`"
            />
          </div>
        </el-card>

        <el-card class="panel-card" shadow="never">
          <template #header>
            <div class="card-header">
              <span>分布表</span>
            </div>
          </template>

          <el-table
            :data="probabilityRows"
            border
            size="small"
            table-layout="auto"
            :fit="false"
            class="distribution-table"
          >
            <el-table-column label="抽次" width="90" align="right">
              <template #default="{ row }">
                第{{ row.draw }}抽
              </template>
            </el-table-column>
            <el-table-column label="累计中奖" width="120" align="right">
              <template #default="{ row }">
                {{ formatPercent(row.cumulative) }}
              </template>
            </el-table-column>
            <el-table-column label="正好中奖" width="120" align="right">
              <template #default="{ row }">
                <span class="exact-probability">{{ formatPercent(row.exact) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="线段" min-width="170">
              <template #default="{ row }">
                <span class="range-text">{{ formatPercent(row.rangeStart) }} - {{ formatPercent(row.rangeEnd) }}</span>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </div>
    </div>
  </div>
</template>

<style scoped>
.lottery-model-page {
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
  grid-template-columns: minmax(360px, 520px) minmax(0, 1fr);
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

.card-header,
.card-title-line {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.card-title-line {
  justify-content: flex-start;
  font-weight: 600;
}

.model-help {
  color: #475467;
  line-height: 1.65;
}

.model-help p {
  margin: 0 0 8px;
}

.model-help p:last-child {
  margin-bottom: 0;
}

.settings-row {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
  margin-bottom: 14px;
}

.setting-item {
  display: grid;
  gap: 8px;
  font-size: 13px;
  color: #667085;
}

.setting-item :deep(.el-input-number) {
  width: 100%;
}

.preset-row {
  margin-bottom: 14px;
}

.cumulative-table {
  width: 100%;
  margin-bottom: 12px;
}

.position-input {
  width: 120px;
}

.fixed-position {
  display: inline-block;
  width: 120px;
  padding-right: 11px;
  color: #667085;
  font-variant-numeric: tabular-nums;
}

.track-shell {
  padding: 8px 10px 4px;
}

.cut-track {
  position: relative;
  height: 78px;
  user-select: none;
}

.track-line {
  position: absolute;
  top: 30px;
  right: 0;
  left: 0;
  height: 8px;
  border-radius: 999px;
  background: linear-gradient(90deg, #e5e7eb 0%, #b8c0cc 100%);
}

.cut-marker {
  position: absolute;
  top: 18px;
  width: 18px;
  height: 32px;
  padding: 0;
  border: 1px solid #2563eb;
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 3px 10px rgba(37, 99, 235, 0.22);
  transform: translateX(-50%);
}

.cut-marker span {
  display: block;
  width: 4px;
  height: 18px;
  margin: 6px auto;
  border-radius: 999px;
  background: #2563eb;
}

.track-axis {
  position: absolute;
  right: 0;
  bottom: 2px;
  left: 0;
  display: flex;
  justify-content: space-between;
  color: #667085;
  font-size: 12px;
}

.formula-line {
  margin-top: 10px;
  padding-top: 12px;
  border-top: 1px solid #edf0f5;
  color: #475467;
  font-size: 13px;
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 16px;
}

.metric-item {
  display: grid;
  gap: 5px;
  padding: 12px;
  border: 1px solid #e7ebf2;
  border-radius: 6px;
  background: #fafbfc;
}

.metric-label {
  color: #667085;
  font-size: 12px;
}

.metric-item strong {
  color: #101828;
  font-size: 18px;
}

.probability-strip {
  display: flex;
  width: 100%;
  height: 34px;
  overflow: hidden;
  border: 1px solid #d9dee8;
  border-radius: 6px;
  background: #f2f4f7;
}

.probability-segment {
  flex: 0 0 auto;
  min-width: 0;
  opacity: 0.88;
}

.probability-segment + .probability-segment {
  border-left: 1px solid rgba(255, 255, 255, 0.8);
}

.distribution-table {
  width: 100%;
}

.exact-probability {
  font-weight: 600;
  color: #175cd3;
}

.range-text {
  color: #667085;
  white-space: nowrap;
}

@media (max-width: 980px) {
  .lottery-model-page {
    padding: 16px;
  }

  .workspace-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 640px) {
  .page-header,
  .settings-row,
  .metric-grid {
    grid-template-columns: 1fr;
  }

  .page-header {
    display: grid;
  }

  .preset-row :deep(.el-button-group) {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    width: 100%;
  }

  .preset-row :deep(.el-button) {
    margin-left: 0;
  }
}
</style>
