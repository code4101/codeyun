<script setup lang="ts">
type DivineResourceSeed = {
  name: string;
  activity: string;
  unitStoneValue: number;
};

type DivineResourceRow = DivineResourceSeed & {
  dailyOutputCount: number;
  weeklyOutputCount: number;
};

type DivineChestOptionSeed = {
  name: string;
  optionCount: number;
};

type DivineChestOptionRow = DivineResourceRow & {
  optionCount: number;
  totalStoneValue: number;
  relativeValuePercent: number;
};

type BaiyeOptionSeed = {
  optionName: string;
  rewardSummary: string;
  totalStoneValue: number;
};

type BaiyeOptionRow = BaiyeOptionSeed & {
  relativeValuePercent: number;
};

const DAILY_OUTPUT_STONE_VALUE = 57.6;

const divineResourceSeeds: DivineResourceSeed[] = [
  { name: '天雷竹', activity: '魔道入侵', unitStoneValue: 0.6 },
  { name: '玄玉葫芦', activity: '云梦试剑', unitStoneValue: 0.4 },
  { name: '星海火树', activity: '兽渊探秘', unitStoneValue: 0.3 },
  { name: '堕天松', activity: '虚天殿', unitStoneValue: 0.2 },
  { name: '灵眼神树', activity: '天地弈局', unitStoneValue: 0.15 },
];

function calculateDailyOutputCount(unitStoneValue: number) {
  return Math.round(DAILY_OUTPUT_STONE_VALUE / unitStoneValue);
}

const divineResourceRows: DivineResourceRow[] = divineResourceSeeds.map((item) => {
  const dailyOutputCount = calculateDailyOutputCount(item.unitStoneValue);
  return {
    ...item,
    dailyOutputCount,
    weeklyOutputCount: dailyOutputCount * 7,
  };
});

const divineResourceRowByName = Object.fromEntries(
  divineResourceRows.map((item) => [item.name, item]),
) as Record<string, DivineResourceRow>;

const divineChestOptionSeeds: DivineChestOptionSeed[] = [
  { name: '天雷竹', optionCount: 6700 },
  { name: '堕天松', optionCount: 20000 },
  { name: '星海火树', optionCount: 13300 },
  { name: '玄玉葫芦', optionCount: 10000 },
  { name: '灵眼神树', optionCount: 26700 },
];

const divineChestOptionBaseRows = divineChestOptionSeeds.map((item) => {
  const resource = divineResourceRowByName[item.name];
  const totalStoneValue = item.optionCount * resource.unitStoneValue;
  return {
    ...resource,
    optionCount: item.optionCount,
    totalStoneValue,
  };
});

const lowestDivineChestValue = divineChestOptionBaseRows.length
  ? Math.min(...divineChestOptionBaseRows.map((item) => item.totalStoneValue))
  : 0;

const divineChestOptionRows: DivineChestOptionRow[] = divineChestOptionBaseRows.map((item) => ({
  ...item,
  relativeValuePercent: Math.round((item.totalStoneValue / lowestDivineChestValue) * 100),
}));

const divineChestValueStats = {
  min: lowestDivineChestValue,
  max: divineChestOptionBaseRows.length
    ? Math.max(...divineChestOptionBaseRows.map((item) => item.totalStoneValue))
    : 0,
};

const baiyeOptionSeeds: BaiyeOptionSeed[] = [
  {
    optionName: '巅峰',
    rewardSummary: '淬体精魄20 + 珍品饲灵丸20 + 炼丹灵草匣',
    totalStoneValue: 600,
  },
  {
    optionName: '魔道',
    rewardSummary: '天雷竹 1100',
    totalStoneValue: 1100 * divineResourceRowByName['天雷竹'].unitStoneValue,
  },
  {
    optionName: '万剑',
    rewardSummary: '玄玉葫芦 1500',
    totalStoneValue: 1500 * divineResourceRowByName['玄玉葫芦'].unitStoneValue,
  },
  {
    optionName: '潮汐',
    rewardSummary: '星海火树 2000',
    totalStoneValue: 2000 * divineResourceRowByName['星海火树'].unitStoneValue,
  },
  {
    optionName: '幻虚',
    rewardSummary: '堕天松 3000',
    totalStoneValue: 3000 * divineResourceRowByName['堕天松'].unitStoneValue,
  },
  {
    optionName: '仙弈',
    rewardSummary: '灵眼神树 4000',
    totalStoneValue: 4000 * divineResourceRowByName['灵眼神树'].unitStoneValue,
  },
];

const lowestBaiyeValue = baiyeOptionSeeds.length
  ? Math.min(...baiyeOptionSeeds.map((item) => item.totalStoneValue))
  : 0;

const baiyeOptionRows: BaiyeOptionRow[] = baiyeOptionSeeds.map((item) => ({
  ...item,
  relativeValuePercent: Math.round((item.totalStoneValue / lowestBaiyeValue) * 100),
}));

const baiyeValueStats = {
  min: lowestBaiyeValue,
  max: baiyeOptionSeeds.length
    ? Math.max(...baiyeOptionSeeds.map((item) => item.totalStoneValue))
    : 0,
};

function formatNumber(value: number, digits = 2) {
  if (Number.isInteger(value)) {
    return String(value);
  }
  return value.toFixed(digits).replace(/\.?0+$/, '');
}

function buildMultiplierPillStyle(value: number, minValue: number, maxValue: number) {
  const hasSpread = maxValue > minValue;
  const normalized = hasSpread ? (value - minValue) / (maxValue - minValue) : 1;
  const fillPercent = hasSpread ? 26 + normalized * 74 : 100;
  const activeAlpha = 0.16 + normalized * 0.4;
  const restAlpha = 0.05 + normalized * 0.06;

  return {
    background: `linear-gradient(90deg, rgba(47,109,246,${activeAlpha.toFixed(3)}) 0%, rgba(47,109,246,${activeAlpha.toFixed(3)}) ${fillPercent.toFixed(1)}%, rgba(47,109,246,${restAlpha.toFixed(3)}) ${fillPercent.toFixed(1)}%, rgba(47,109,246,${restAlpha.toFixed(3)}) 100%)`,
    color: normalized > 0.55 ? '#123a94' : '#1d4ed8',
    fontWeight: normalized > 0.92 ? '700' : '600',
  };
}

function getDivineChestMultiplierStyle(row: DivineChestOptionRow) {
  return buildMultiplierPillStyle(
    row.totalStoneValue,
    divineChestValueStats.min,
    divineChestValueStats.max,
  );
}

function getBaiyeMultiplierStyle(row: BaiyeOptionRow) {
  return buildMultiplierPillStyle(
    row.totalStoneValue,
    baiyeValueStats.min,
    baiyeValueStats.max,
  );
}
</script>

<template>
  <div class="divine-resource-page">
    <div class="page-header">
      <h2 class="page-title">活动列表 · 神物资源</h2>
    </div>

    <el-card class="data-card" shadow="never">
      <template #header>
        <div class="card-header">
          <span>神物基础折算</span>
        </div>
      </template>

      <div class="summary-strip">
        <div class="summary-item">
          <span class="summary-label">统一日产值</span>
          <strong class="summary-value">{{ DAILY_OUTPUT_STONE_VALUE }} 灵石</strong>
        </div>
        <div class="summary-item">
          <span class="summary-label">小绿瓶</span>
          <strong class="summary-value">1 次 = 1 天自然产出</strong>
        </div>
      </div>

      <div class="table-wrap">
        <el-table
          :data="divineResourceRows"
          border
          size="small"
          table-layout="auto"
          :fit="false"
          class="resource-table"
        >
          <el-table-column type="index" label="编号" width="72" align="center" />
          <el-table-column prop="name" label="神物" />
          <el-table-column prop="activity" label="对应活动" />
          <el-table-column prop="unitStoneValue" label="单件价值(灵石)" align="right" />
          <el-table-column prop="dailyOutputCount" label="日产数量" align="right" />
          <el-table-column prop="weeklyOutputCount" label="周产数量" align="right" />
        </el-table>
      </div>
    </el-card>

    <el-card class="data-card" shadow="never">
      <template #header>
        <div class="card-header">
          <span>月光神物宝匣</span>
        </div>
      </template>

      <div class="table-wrap">
        <el-table
          :data="divineChestOptionRows"
          border
          size="small"
          table-layout="auto"
          :fit="false"
          class="resource-table"
        >
          <el-table-column type="index" label="编号" width="72" align="center" />
          <el-table-column prop="name" label="神物" />
          <el-table-column prop="optionCount" label="宝匣数量" align="right" />
          <el-table-column prop="unitStoneValue" label="单件价值(灵石)" align="right" />
          <el-table-column label="总价值(灵石)" align="right">
            <template #default="{ row }">
              {{ formatNumber(row.totalStoneValue) }}
            </template>
          </el-table-column>
          <el-table-column label="倍率" align="right">
            <template #default="{ row }">
              <span class="multiplier-pill" :style="getDivineChestMultiplierStyle(row)">
                {{ row.relativeValuePercent }}%
              </span>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </el-card>

    <el-card class="data-card" shadow="never">
      <template #header>
        <div class="card-header">
          <span>拜谒日常</span>
        </div>
      </template>

      <div class="table-wrap">
        <el-table
          :data="baiyeOptionRows"
          border
          size="small"
          table-layout="auto"
          :fit="false"
          class="resource-table"
        >
          <el-table-column type="index" label="编号" width="72" align="center" />
          <el-table-column prop="optionName" label="选项" />
          <el-table-column prop="rewardSummary" label="奖励内容" />
          <el-table-column label="总价值(灵石)" align="right">
            <template #default="{ row }">
              {{ formatNumber(row.totalStoneValue) }}
            </template>
          </el-table-column>
          <el-table-column label="倍率" align="right">
            <template #default="{ row }">
              <span class="multiplier-pill" :style="getBaiyeMultiplierStyle(row)">
                {{ row.relativeValuePercent }}%
              </span>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.divine-resource-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-height: 100%;
  padding: 20px;
  background: #f5f7fa;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.page-title {
  margin: 0;
  color: #111827;
  font-size: 24px;
  font-weight: 600;
  line-height: 1.3;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.summary-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 16px;
}

.summary-item {
  display: inline-flex;
  align-items: baseline;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 12px;
  background: #f8fafc;
  color: #334155;
}

.summary-label {
  font-size: 13px;
  color: #64748b;
}

.summary-value {
  font-size: 14px;
  font-weight: 600;
  color: #0f172a;
}

.table-wrap {
  width: 100%;
  overflow-x: auto;
}

.resource-table {
  width: max-content;
  min-width: fit-content;
}

.resource-table :deep(.el-table__cell) {
  padding-top: 8px;
  padding-bottom: 8px;
}

.resource-table :deep(.cell) {
  white-space: nowrap;
  word-break: keep-all;
}

.multiplier-pill {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 72px;
  padding: 4px 12px;
  border-radius: 999px;
  box-shadow: inset 0 0 0 1px rgba(47, 109, 246, 0.08);
  transition: background 0.2s ease, box-shadow 0.2s ease, color 0.2s ease;
}

@media (max-width: 900px) {
  .divine-resource-page {
    padding: 16px;
  }

  .summary-strip {
    flex-direction: column;
  }
}
</style>
