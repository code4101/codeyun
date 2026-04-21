<script setup lang="ts">
import originalRuleImageSrc from './originalRuleImage';
interface PromotionRule {
  qualifyingPayment: number;
  couponValue: number;
  minimumRecharge: number;
  limit: number;
}

interface MonthlyCoupon {
  faceValue: number;
  reduction: number;
  monthlyLimit: number;
}

interface RawPlan {
  actionLabel: string;
  singleActualPayment: number;
  singleFaceValue: number;
  singleRatio: number;
  limit: number;
}

interface PurchasePlan {
  priority: number;
  actionLabel: string;
  singleActualPayment: number;
  singleFaceValue: number;
  singleRatio: number;
  singlePercentText: string;
  limit: number;
  cumulativeActualPayment: number;
  cumulativeFaceValue: number;
  cumulativeRatio: number;
  cumulativePercentText: string;
}

const rechargeOptions = [6, 30, 68, 98, 128, 198, 328, 488, 648, 998, 1998];

const promotionRules: PromotionRule[] = [
  { qualifyingPayment: 30, couponValue: 28, minimumRecharge: 98, limit: 2 },
  { qualifyingPayment: 98, couponValue: 88, minimumRecharge: 198, limit: 2 },
  { qualifyingPayment: 198, couponValue: 168, minimumRecharge: 328, limit: 2 },
  { qualifyingPayment: 328, couponValue: 248, minimumRecharge: 648, limit: 2 },
  { qualifyingPayment: 648, couponValue: 368, minimumRecharge: 648, limit: 2 },
];

const monthlyCoupons: MonthlyCoupon[] = [
  { faceValue: 68, reduction: 15, monthlyLimit: 4 },
  { faceValue: 328, reduction: 68, monthlyLimit: 3 },
  { faceValue: 30, reduction: 6, monthlyLimit: 2 },
  { faceValue: 648, reduction: 128, monthlyLimit: 3 },
  { faceValue: 128, reduction: 25, monthlyLimit: 2 },
  { faceValue: 198, reduction: 38, monthlyLimit: 4 },
];

const toPercentText = (ratio: number) => `${(ratio * 100).toFixed(2)}%`;

const getBestRechargeFaceValue = (minimumRecharge: number) => {
  const faceValue = rechargeOptions.find((value) => value >= minimumRecharge);
  if (faceValue === undefined) {
    throw new Error(`No recharge option satisfies minimum ${minimumRecharge}`);
  }
  return faceValue;
};

const buildPurchasePlans = (rawPlans: RawPlan[]): PurchasePlan[] => {
  const sortedPlans = [...rawPlans].sort((left, right) => left.singleRatio - right.singleRatio);
  const builtPlans: PurchasePlan[] = [];
  let runningActualPayment = 0;
  let runningFaceValue = 0;

  for (const [index, plan] of sortedPlans.entries()) {
    runningActualPayment += plan.singleActualPayment * plan.limit;
    runningFaceValue += plan.singleFaceValue * plan.limit;

    const cumulativeRatio = runningActualPayment / runningFaceValue;

    builtPlans.push({
      priority: index + 1,
      actionLabel: plan.actionLabel,
      singleActualPayment: plan.singleActualPayment,
      singleFaceValue: plan.singleFaceValue,
      singleRatio: plan.singleRatio,
      singlePercentText: toPercentText(plan.singleRatio),
      limit: plan.limit,
      cumulativeActualPayment: runningActualPayment,
      cumulativeFaceValue: runningFaceValue,
      cumulativeRatio,
      cumulativePercentText: toPercentText(cumulativeRatio),
    });
  }

  return builtPlans;
};

const eventRawPlans: RawPlan[] = promotionRules.map((rule) => {
  const couponRechargeFaceValue = getBestRechargeFaceValue(rule.minimumRecharge);
  const couponRechargeActualPayment = couponRechargeFaceValue - rule.couponValue;
  const singleActualPayment = rule.qualifyingPayment + couponRechargeActualPayment;
  const singleFaceValue = rule.qualifyingPayment + couponRechargeFaceValue;
  const singleRatio = singleActualPayment / singleFaceValue;

  return {
    actionLabel: `先充 ${rule.qualifyingPayment} 拿 ${rule.couponValue} 券，再用券充 ${couponRechargeFaceValue}`,
    limit: rule.limit,
    singleActualPayment,
    singleFaceValue,
    singleRatio,
  };
});

const monthlyRawPlans: RawPlan[] = monthlyCoupons.map((coupon) => {
  const singleActualPayment = coupon.faceValue - coupon.reduction;
  const singleFaceValue = coupon.faceValue;
  const singleRatio = singleActualPayment / singleFaceValue;

  return {
    actionLabel: `充 ${coupon.faceValue} 减 ${coupon.reduction}`,
    limit: coupon.monthlyLimit,
    singleActualPayment,
    singleFaceValue,
    singleRatio,
  };
});

const eventPlans = buildPurchasePlans(eventRawPlans);
const monthlyPlans = buildPurchasePlans(monthlyRawPlans);
const rechargeOptionsText = rechargeOptions.join(' / ');
</script>

<template>
  <div class="discount-page">
    <div class="page-shell">
      <div class="page-header">
        <p class="page-kicker">凡修手游</p>
        <h2 class="page-title">凡修优惠券</h2>
        <p class="page-subtitle">
          可充值档位：{{ rechargeOptionsText }}
        </p>
      </div>

      <el-card class="table-card" shadow="never">
        <template #header>
          <div class="section-title">每月优惠</div>
        </template>
        <div class="table-scroll">
          <el-table :data="monthlyPlans" border size="small" class="compact-table">
            <el-table-column label="优先级" width="78" align="center">
              <template #default="scope">
                <span class="priority-badge">{{ scope.row.priority }}</span>
              </template>
            </el-table-column>

            <el-table-column label="建议顺序" min-width="240">
              <template #default="scope">
                <div class="action-cell">{{ scope.row.actionLabel }}</div>
              </template>
            </el-table-column>

            <el-table-column min-width="118" align="center">
              <template #header>
                <div class="stack-header">
                  <span>单次</span>
                  <span>实付 / 面额</span>
                </div>
              </template>
              <template #default="scope">
                <div class="pair-cell">
                  <strong>{{ scope.row.singleActualPayment }}</strong>
                  <span>/ {{ scope.row.singleFaceValue }}</span>
                </div>
              </template>
            </el-table-column>

            <el-table-column label="单次折扣率" min-width="110" align="center">
              <template #default="scope">
                <strong>{{ scope.row.singlePercentText }}</strong>
              </template>
            </el-table-column>

            <el-table-column label="次数" min-width="72" align="center">
              <template #default="scope">
                {{ scope.row.limit }} 次
              </template>
            </el-table-column>

            <el-table-column min-width="126" align="center">
              <template #header>
                <div class="stack-header">
                  <span>累计</span>
                  <span>实付 / 面额</span>
                </div>
              </template>
              <template #default="scope">
                <div class="pair-cell">
                  <strong>{{ scope.row.cumulativeActualPayment }}</strong>
                  <span>/ {{ scope.row.cumulativeFaceValue }}</span>
                </div>
              </template>
            </el-table-column>

            <el-table-column label="累计折扣率" min-width="110" align="center">
              <template #default="scope">
                <strong>{{ scope.row.cumulativePercentText }}</strong>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-card>

      <el-card class="table-card section-card" shadow="never">
        <template #header>
          <div class="section-title">活动券</div>
        </template>
        <div class="table-scroll">
          <el-table :data="eventPlans" border size="small" class="compact-table">
            <el-table-column label="优先级" width="78" align="center">
              <template #default="scope">
                <span class="priority-badge">{{ scope.row.priority }}</span>
              </template>
            </el-table-column>

            <el-table-column label="建议顺序" min-width="240">
              <template #default="scope">
                <div class="action-cell">{{ scope.row.actionLabel }}</div>
              </template>
            </el-table-column>

            <el-table-column min-width="118" align="center">
              <template #header>
                <div class="stack-header">
                  <span>单次</span>
                  <span>实付 / 面额</span>
                </div>
              </template>
              <template #default="scope">
                <div class="pair-cell">
                  <strong>{{ scope.row.singleActualPayment }}</strong>
                  <span>/ {{ scope.row.singleFaceValue }}</span>
                </div>
              </template>
            </el-table-column>

            <el-table-column label="单次折扣率" min-width="110" align="center">
              <template #default="scope">
                <strong>{{ scope.row.singlePercentText }}</strong>
              </template>
            </el-table-column>

            <el-table-column label="次数" min-width="72" align="center">
              <template #default="scope">
                {{ scope.row.limit }} 次
              </template>
            </el-table-column>

            <el-table-column min-width="126" align="center">
              <template #header>
                <div class="stack-header">
                  <span>累计</span>
                  <span>实付 / 面额</span>
                </div>
              </template>
              <template #default="scope">
                <div class="pair-cell">
                  <strong>{{ scope.row.cumulativeActualPayment }}</strong>
                  <span>/ {{ scope.row.cumulativeFaceValue }}</span>
                </div>
              </template>
            </el-table-column>

            <el-table-column label="累计折扣率" min-width="110" align="center">
              <template #default="scope">
                <strong>{{ scope.row.cumulativePercentText }}</strong>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-card>

      <el-card class="source-card" shadow="never">
        <template #header>
          <div class="source-title">原始规则图</div>
        </template>
        <img class="source-image" :src="originalRuleImageSrc" alt="凡修优惠原始规则图" />
      </el-card>
    </div>
  </div>
</template>

<style scoped>
.discount-page {
  min-height: 100%;
  padding: 24px;
  background:
    radial-gradient(circle at top right, rgba(214, 164, 56, 0.16), transparent 24%),
    linear-gradient(180deg, #fffaf1 0, #f7f9fc 220px, #ffffff 100%);
}

.page-shell {
  max-width: 1500px;
}

.page-header {
  margin-bottom: 16px;
}

.page-kicker {
  margin: 0 0 8px;
  font-size: 13px;
  letter-spacing: 0.08em;
  color: #a26d10;
  text-transform: uppercase;
}

.page-title {
  margin: 0;
  font-size: 32px;
  color: #3f2a08;
}

.page-subtitle {
  margin: 10px 0 0;
  color: #6b5831;
  line-height: 1.6;
}

.table-card {
  border-radius: 22px;
  border: 1px solid #ecdcb8;
  background: rgba(255, 255, 255, 0.92);
}

.section-card {
  margin-top: 18px;
}

.table-scroll {
  overflow-x: auto;
}

.section-title {
  font-size: 18px;
  font-weight: 600;
  color: #47300d;
}

.stack-header {
  display: flex;
  flex-direction: column;
  align-items: center;
  line-height: 1.25;
}

.action-cell {
  line-height: 1.45;
  word-break: break-word;
}

.pair-cell {
  white-space: nowrap;
}

.pair-cell strong {
  font-weight: 700;
}

.compact-table :deep(.el-table__header-wrapper th.el-table__cell) {
  padding: 8px 0;
}

.compact-table :deep(.el-table__body-wrapper td.el-table__cell) {
  padding: 9px 0;
}

.compact-table :deep(.cell) {
  padding: 0 6px;
  font-size: 13px;
}

.priority-badge {
  display: inline-flex;
  width: 28px;
  height: 28px;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
  background: linear-gradient(135deg, #c89124 0, #e7bc5f 100%);
  color: #ffffff;
  font-weight: 700;
}

.source-card {
  margin-top: 18px;
  border-radius: 22px;
  border: 1px solid #ecdcb8;
  background: rgba(255, 255, 255, 0.94);
}

.source-title {
  font-size: 18px;
  font-weight: 600;
  color: #47300d;
}

.source-image {
  display: block;
  width: 59.16%;
  max-width: 450px;
  margin: 0;
  border-radius: 14px;
  border: 1px solid #ead9b2;
  background: #fffaf1;
}

@media (max-width: 768px) {
  .discount-page {
    padding: 16px;
  }

  .page-title {
    font-size: 28px;
  }

  .compact-table :deep(.cell) {
    padding: 0 4px;
    font-size: 12px;
  }

  .priority-badge {
    width: 26px;
    height: 26px;
  }
}
</style>
