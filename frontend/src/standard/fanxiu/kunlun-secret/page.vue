<script setup lang="ts">

type RewardRow = {
  drawCount: number;
  bonusKeyCount: number;
  totalBonusKeyCount: number;
};

type CostRow = {
  type: '任务' | '灵石' | '代币';
  price: number;
  boughtKeys: number;
  unitPriceText: string;
  totalKeys: number;
  totalTokens: number;
};

type ExchangeRow = {
  name: string;
  jadeCost: number;
  purchaseLimit: number;
};

const BASE_FREE_KEYS_PER_EVENT = 17;

const rawRewardRows = [
  { drawCount: 20, bonusKeyCount: 4 },
  { drawCount: 40, bonusKeyCount: 4 },
  { drawCount: 60, bonusKeyCount: 4 },
  { drawCount: 80, bonusKeyCount: 4 },
  { drawCount: 100, bonusKeyCount: 5 },
  { drawCount: 140, bonusKeyCount: 5 },
  { drawCount: 180, bonusKeyCount: 5 },
  { drawCount: 220, bonusKeyCount: 5 },
  { drawCount: 260, bonusKeyCount: 10 },
  { drawCount: 300, bonusKeyCount: 10 },
  { drawCount: 340, bonusKeyCount: 5 },
  { drawCount: 380, bonusKeyCount: 5 },
  { drawCount: 420, bonusKeyCount: 5 },
  { drawCount: 460, bonusKeyCount: 5 },
  { drawCount: 500, bonusKeyCount: 5 },
  { drawCount: 540, bonusKeyCount: 5 },
  { drawCount: 580, bonusKeyCount: 5 },
];

const rewardRows: RewardRow[] = rawRewardRows.map((row, index) => ({
  ...row,
  totalBonusKeyCount: rawRewardRows
    .slice(0, index + 1)
    .reduce((sum, item) => sum + item.bonusKeyCount, 0),
}));

const maxBonusKeyCount = rewardRows.length ? Math.max(...rewardRows.map(item => item.bonusKeyCount)) : 0;

function buildRatePillStyle(rate: number, minRate: number, maxRate: number) {
  const normalized = maxRate > minRate ? (rate - minRate) / (maxRate - minRate) : 1;
  const fillPercent = maxRate > 0 ? Math.max((rate / maxRate) * 100, 24) : 0;
  const activeAlpha = 0.12 + normalized * 0.48;
  const restAlpha = 0.03 + normalized * 0.08;
  return {
    background: `linear-gradient(90deg, rgba(47,109,246,${activeAlpha.toFixed(3)}) 0%, rgba(47,109,246,${activeAlpha.toFixed(3)}) ${fillPercent.toFixed(1)}%, rgba(47,109,246,${restAlpha.toFixed(3)}) ${fillPercent.toFixed(1)}%, rgba(47,109,246,${restAlpha.toFixed(3)}) 100%)`,
    color: normalized > 0.55 ? '#123a94' : '#1d4ed8',
    fontWeight: normalized > 0.92 ? '700' : '600',
  };
}

function getGiftRateValue(row: RewardRow) {
  if (!row.drawCount) return 0;
  return row.totalBonusKeyCount / row.drawCount;
}

function formatGiftRate(row: RewardRow) {
  return String(Math.round(getGiftRateValue(row) * 100) / 100);
}

const giftRateValues = rewardRows.map(getGiftRateValue);
const maxGiftRate = giftRateValues.length ? Math.max(...giftRateValues) : 0;
const minGiftRate = giftRateValues.length ? Math.min(...giftRateValues) : 0;
const hasGiftRateSpread = maxGiftRate > minGiftRate;

function isPeakGiftRate(row: RewardRow) {
  return hasGiftRateSpread && Math.abs(getGiftRateValue(row) - maxGiftRate) < 1e-9;
}

function getGiftRateStyle(row: RewardRow) {
  return buildRatePillStyle(getGiftRateValue(row), minGiftRate, maxGiftRate);
}

const costRows: CostRow[] = [
  { type: '任务', price: 0, boughtKeys: 10, unitPriceText: '', totalKeys: 10, totalTokens: 0 },
  { type: '灵石', price: 998, boughtKeys: 5, unitPriceText: '200', totalKeys: 15, totalTokens: 0 },
  { type: '灵石', price: 488, boughtKeys: 2, unitPriceText: '244', totalKeys: 17, totalTokens: 0 },
  { type: '代币', price: 6, boughtKeys: 5, unitPriceText: '1.20', totalKeys: 22, totalTokens: 6 },
  { type: '代币', price: 18, boughtKeys: 10, unitPriceText: '1.80', totalKeys: 32, totalTokens: 24 },
  { type: '代币', price: 30, boughtKeys: 15, unitPriceText: '2.00', totalKeys: 47, totalTokens: 54 },
  { type: '代币', price: 68, boughtKeys: 20, unitPriceText: '3.40', totalKeys: 95, totalTokens: 220 },
  { type: '代币', price: 98, boughtKeys: 28, unitPriceText: '3.50', totalKeys: 75, totalTokens: 152 },
  { type: '代币', price: 128, boughtKeys: 36, unitPriceText: '3.56', totalKeys: 131, totalTokens: 348 },
  { type: '代币', price: 328, boughtKeys: 72, unitPriceText: '4.56', totalKeys: 203, totalTokens: 676 },
  { type: '代币', price: 328, boughtKeys: 72, unitPriceText: '4.56', totalKeys: 275, totalTokens: 1004 },
  { type: '代币', price: 648, boughtKeys: 140, unitPriceText: '4.63', totalKeys: 415, totalTokens: 1652 },
  { type: '代币', price: 648, boughtKeys: 140, unitPriceText: '4.63', totalKeys: 555, totalTokens: 2300 },
];

const exchangeRows: ExchangeRow[] = [
  { name: '五阶巽风灵环', jadeCost: 960, purchaseLimit: 1 },
  { name: '仙材宝匣', jadeCost: 800, purchaseLimit: 1 },
  { name: '仙侣神通修为宝匣', jadeCost: 100, purchaseLimit: 5 },
  { name: '秘传法宝自选匣', jadeCost: 300, purchaseLimit: 5 },
  { name: '非遗悟境自选匣', jadeCost: 300, purchaseLimit: 3 },
  { name: '三彩瓶', jadeCost: 100, purchaseLimit: 10 },
  { name: '魔道·四倍功勋符', jadeCost: 30, purchaseLimit: 30 },
  { name: '云梦·四倍积分令', jadeCost: 20, purchaseLimit: 20 },
  { name: '云梦·狙击牌', jadeCost: 10, purchaseLimit: 5 },
  { name: '兽渊·探查符', jadeCost: 10, purchaseLimit: 30 },
  { name: '虚天·四倍积分符', jadeCost: 20, purchaseLimit: 15 },
  { name: '虚天·狙击令', jadeCost: 30, purchaseLimit: 5 },
  { name: '弈技·四倍棋符', jadeCost: 10, purchaseLimit: 30 },
  { name: '弈技·埋伏', jadeCost: 20, purchaseLimit: 5 },
];

function getCostRowClassName({ row }: { row: CostRow }) {
  if (row.type === '任务') return 'cost-row--task';
  if (row.type === '灵石') return 'cost-row--stone';
  return 'cost-row--token';
}

function formatSpeed(row: CostRow) {
  return `${Math.floor((row.totalKeys / BASE_FREE_KEYS_PER_EVENT) * 100)}%`;
}
</script>

<template>
  <div class="kunlun-page">
    <div class="page-header">
      <h2 class="page-title">活动列表 · 昆仑秘藏</h2>
    </div>

    <el-card class="data-card" shadow="never">
      <template #header>
        <div class="card-header">
          <span>攻略</span>
        </div>
      </template>

      <ol class="strategy-list">
        <li>累计有20抽的时候可以准备抽，尝试抽1个奖。</li>
        <li>如果&lt;=15抽中，直接停止。</li>
        <li>&gt;=16抽中，则可以拉满到20抽，能再拿4抽不亏，领到的4抽不用。</li>
        <li>再倒霉20抽以上才中，可以同理依次类推考虑是否拉到40抽。</li>
      </ol>
    </el-card>

    <el-card class="data-card" shadow="never">
      <template #header>
        <div class="card-header">
          <span>购钥成本表</span>
        </div>
      </template>

      <el-table
        :data="costRows"
        border
        size="small"
        table-layout="auto"
        class="cost-table"
        :fit="false"
        :row-class-name="getCostRowClassName"
      >
        <el-table-column prop="type" label="类型" />
        <el-table-column prop="price" label="价格" align="right" />
        <el-table-column prop="boughtKeys" label="购买密钥" align="right" />
        <el-table-column prop="unitPriceText" label="单价" align="right" />
        <el-table-column prop="totalKeys" label="总计密钥" align="right" />
        <el-table-column prop="totalTokens" label="总计代币" align="right" />
        <el-table-column label="速度" align="right">
          <template #default="{ row }">
            {{ formatSpeed(row) }}
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card class="data-card" shadow="never">
      <template #header>
        <div class="card-header">
          <span>累抽赠送表</span>
        </div>
      </template>

      <el-table
        :data="rewardRows"
        border
        stripe
        size="small"
        table-layout="auto"
        class="reward-table"
        :fit="false"
      >
        <el-table-column prop="drawCount" label="累抽密钥" align="center" />
        <el-table-column label="额外赠送密钥" align="center">
          <template #default="{ row }">
            <span class="bonus-pill" :class="{ 'is-peak': row.bonusKeyCount === maxBonusKeyCount }">
              +{{ row.bonusKeyCount }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="totalBonusKeyCount" label="累计赠送密钥" align="center" />
        <el-table-column label="赠送率" align="center">
          <template #default="{ row }">
            <span
              class="rate-pill"
              :class="{ 'is-peak': isPeakGiftRate(row) }"
              :style="getGiftRateStyle(row)"
            >
              {{ formatGiftRate(row) }}
            </span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card class="data-card" shadow="never">
      <template #header>
        <div class="card-header">
          <span>兑换宝阁</span>
          <span class="card-note">每抽可得 5 个昆仑古玉</span>
        </div>
      </template>

      <el-table
        :data="exchangeRows"
        border
        size="small"
        table-layout="auto"
        class="exchange-table"
        :fit="false"
      >
        <el-table-column prop="name" label="名称" />
        <el-table-column prop="jadeCost" label="昆仑古玉" align="right" />
        <el-table-column prop="purchaseLimit" label="限购" align="right" />
      </el-table>
    </el-card>
  </div>
</template>

<style scoped>
.kunlun-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-height: 100%;
  padding: 20px;
  background: #f5f7fa;
  overflow: auto;
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
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}

.card-note {
  color: #94a3b8;
  font-size: 13px;
  line-height: 1.4;
  white-space: nowrap;
}

.strategy-list {
  margin: 0;
  padding-left: 22px;
  color: #334155;
  font-size: 14px;
  line-height: 1.8;
}

.strategy-list li + li {
  margin-top: 4px;
}

.reward-table :deep(.el-table__cell) {
  padding-top: 8px;
  padding-bottom: 8px;
}

.exchange-table :deep(.el-table__cell) {
  padding-top: 8px;
  padding-bottom: 8px;
}

.cost-table :deep(.el-table__header-wrapper th.el-table__cell) {
  background: #d9eafc;
  color: #334155;
}

.cost-table :deep(.el-table__cell) {
  padding-top: 8px;
  padding-bottom: 8px;
}

.cost-table :deep(.cost-row--task td.el-table__cell) {
  background: #eef7df;
}

.cost-table :deep(.cost-row--stone td.el-table__cell) {
  background: #fff1c6;
}

.cost-table :deep(.cost-row--token td.el-table__cell) {
  background: #fbe3d2;
}

.bonus-pill {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 42px;
  padding: 2px 10px;
  border-radius: 999px;
  background: #eff6ff;
  color: #1d4ed8;
  font-weight: 600;
}

.bonus-pill.is-peak {
  background: #dbeafe;
  color: #1e3a8a;
}

.rate-pill {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 62px;
  padding: 3px 10px;
  border-radius: 999px;
  box-shadow: inset 0 0 0 1px rgba(47, 109, 246, 0.08);
  transition: background 0.2s ease, box-shadow 0.2s ease, color 0.2s ease;
}

.rate-pill.is-peak {
  box-shadow: inset 0 0 0 1px rgba(29, 78, 216, 0.45), 0 0 0 2px rgba(191, 219, 254, 0.7);
}

@media (max-width: 900px) {
  .kunlun-page {
    padding: 16px;
  }
}
</style>
