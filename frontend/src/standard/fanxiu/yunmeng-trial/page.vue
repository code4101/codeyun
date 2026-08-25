<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { QuestionFilled } from '@element-plus/icons-vue';
import FanxiuActivityRankingSection from '@/standard/fanxiu/components/FanxiuActivityRankingSection.vue';
import FanxiuActivityToolbar from '@/standard/fanxiu/components/FanxiuActivityToolbar.vue';
import { formatActivityUpdatedAt } from '@/standard/fanxiu/components/activityStatus';
import { defaultExchangeShopSort } from '@/standard/fanxiu/components/exchangeShopSort';
import { useFanxiuActivityRefresh } from '@/standard/fanxiu/components/useFanxiuActivityRefresh';

import {
  collectFanxiuYunmengTrialMeasurement,
  getFanxiuYunmengTrialRankings,
  getFanxiuYunmengTrialSnapshot,
  saveFanxiuYunmengTrialPriorities,
  saveFanxiuYunmengTrialShopItemLock,
  type FanxiuYunmengTrialActivityDetail,
  type FanxiuYunmengTrialActivitySummary,
  type FanxiuYunmengTrialRankingItem,
  type FanxiuYunmengTrialShopItem,
  type FanxiuYunmengTrialSnapshot,
} from '@/api/fanxiu';
import { formatChineseCompactNumber } from '@/utils/numberFormat';

const props = withDefaults(defineProps<{
  embedded?: boolean;
  initialSnapshot?: FanxiuYunmengTrialSnapshot;
}>(), {
  embedded: false,
  initialSnapshot: undefined,
});

const loading = ref(false);
const collectingFromGame = ref(false);
const prioritySaving = ref(false);
const lockSavingGoodsIds = ref<number[]>([]);
const rankingLoading = ref(false);
const activities = ref<FanxiuYunmengTrialActivitySummary[]>(props.initialSnapshot?.activities || []);
const selectedActivityId = ref(props.initialSnapshot?.selected_activity?.id || '');
const activity = ref<FanxiuYunmengTrialActivityDetail | null>(props.initialSnapshot?.selected_activity || null);
const personalRankings = ref<FanxiuYunmengTrialRankingItem[]>([]);
const planeRankings = ref<FanxiuYunmengTrialRankingItem[]>([]);
const rankingPage = ref(1);
const rankingPageSize = ref(20);
const rankingTotal = ref(0);
const rankingLastCapturedAt = ref('');
const planeRankingLastCapturedAt = ref('');

type ShopSortKey = 'source_order' | 'priority_order' | 'locked' | 'name'
  | 'token_cost' | 'purchase_limit' | 'row_total_tokens' | 'cumulative_tokens'
  | 'remaining_challenges';
type SortDirection = 'asc' | 'desc';

const shopSort = ref<{ key: ShopSortKey; direction: SortDirection }>({
  key: 'source_order',
  direction: 'asc',
});
const shopOrderGoodsIds = ref<number[]>([]);
const shopSortStoragePrefix = 'codeyun:fanxiu:yunmeng-trial:shop-sort:';

const {
  canEdit,
  canCollect,
  maybeAutoCollect: maybeAutoCollectFromGame,
} = useFanxiuActivityRefresh({
  activity,
  collectSilently: () => collectFromGame(false),
});

const selectedGoodsIds = computed(() => (
  [...(activity.value?.shop_items || [])]
    .filter(item => item.priority_order != null)
    .sort((left, right) => Number(left.priority_order) - Number(right.priority_order))
    .map(item => item.goods_id)
));

const resourceStrategyEntries = computed(() => (
  Object.entries(activity.value?.resource_strategy || {})
));

const displayedShopItems = computed(() => {
  const items = activity.value?.shop_items || [];
  const byGoodsId = new Map(items.map(item => [item.goods_id, item]));
  const ordered = shopOrderGoodsIds.value
    .map(goodsId => byGoodsId.get(goodsId))
    .filter((item): item is FanxiuYunmengTrialShopItem => Boolean(item));
  const knownIds = new Set(shopOrderGoodsIds.value);
  return ordered.concat(items.filter(item => !knownIds.has(item.goods_id)));
});

function compareShopItems(
  left: FanxiuYunmengTrialShopItem,
  right: FanxiuYunmengTrialShopItem,
  key: ShopSortKey,
  direction: SortDirection,
) {
  let compared = 0;
  if (key === 'name') {
    compared = left.name.localeCompare(right.name, 'zh-CN');
  } else if (key === 'locked') {
    compared = Number(left.locked) - Number(right.locked);
  } else {
    const leftValue = left[key];
    const rightValue = right[key];
    if (leftValue == null && rightValue == null) return 0;
    if (leftValue == null) return 1;
    if (rightValue == null) return -1;
    compared = leftValue - rightValue;
  }
  return direction === 'asc' ? compared : -compared;
}

function refreshShopOrder() {
  const items = [...(activity.value?.shop_items || [])];
  const { key, direction } = shopSort.value;
  items.sort((left, right) => {
    const compared = compareShopItems(left, right, key, direction);
    if (compared !== 0) return compared;
    return left.source_order - right.source_order;
  });
  shopOrderGoodsIds.value = items.map(item => item.goods_id);
}

function restoreShopSort(activityId: string) {
  const fallback = defaultExchangeShopSort(activity.value?.shop_items);
  try {
    const saved = JSON.parse(localStorage.getItem(`${shopSortStoragePrefix}${activityId}`) || 'null');
    const validKeys: ShopSortKey[] = [
      'source_order', 'priority_order', 'locked', 'name', 'token_cost',
      'purchase_limit', 'row_total_tokens', 'cumulative_tokens', 'remaining_challenges',
    ];
    shopSort.value = validKeys.includes(saved?.key) && ['asc', 'desc'].includes(saved?.direction)
      ? saved
      : fallback;
  } catch {
    shopSort.value = fallback;
  }
}

function setShopSort(key: ShopSortKey) {
  shopSort.value = shopSort.value.key === key
    ? { key, direction: shopSort.value.direction === 'asc' ? 'desc' : 'asc' }
    : { key, direction: 'asc' };
  if (activity.value) {
    localStorage.setItem(
      `${shopSortStoragePrefix}${activity.value.id}`,
      JSON.stringify(shopSort.value),
    );
  }
  refreshShopOrder();
}

function sortIndicator(key: ShopSortKey) {
  if (shopSort.value.key !== key) return '';
  return shopSort.value.direction === 'asc' ? '↑' : '↓';
}

function ariaSort(key: ShopSortKey) {
  if (shopSort.value.key !== key) return 'none';
  return shopSort.value.direction === 'asc' ? 'ascending' : 'descending';
}

function isSelected(item: FanxiuYunmengTrialShopItem) {
  return selectedGoodsIds.value.includes(item.goods_id);
}

async function loadSnapshot(activityId?: string, showLoading = true) {
  if (showLoading) loading.value = true;
  try {
    const snapshot = await getFanxiuYunmengTrialSnapshot(activityId);
    activities.value = snapshot.activities;
    activity.value = snapshot.selected_activity || null;
    selectedActivityId.value = activity.value?.id || '';
    if (activity.value) {
      restoreShopSort(activity.value.id);
      refreshShopOrder();
    } else {
      shopOrderGoodsIds.value = [];
    }
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '读取云梦试剑数据失败');
  } finally {
    if (showLoading) loading.value = false;
  }
}

async function loadRankings() {
  if (!selectedActivityId.value) {
    personalRankings.value = [];
    planeRankings.value = [];
    rankingTotal.value = 0;
    rankingLastCapturedAt.value = '';
    planeRankingLastCapturedAt.value = '';
    return;
  }
  rankingLoading.value = true;
  try {
    const [personal, plane] = await Promise.all([
      getFanxiuYunmengTrialRankings(
        selectedActivityId.value,
        rankingPage.value,
        rankingPageSize.value,
        'personal',
      ),
      getFanxiuYunmengTrialRankings(selectedActivityId.value, 1, 100, 'plane'),
    ]);
    personalRankings.value = personal.items;
    rankingTotal.value = personal.total;
    rankingLastCapturedAt.value = personal.last_captured_at || '';
    planeRankings.value = plane.items;
    planeRankingLastCapturedAt.value = plane.last_captured_at || '';
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '读取榜单失败');
  } finally {
    rankingLoading.value = false;
  }
}

async function changeActivity(activityId: string) {
  rankingPage.value = 1;
  await loadSnapshot(activityId);
  await loadRankings();
}

async function collectFromGame(showFeedback = true) {
  if (!activity.value || !canCollect.value || collectingFromGame.value) return;
  const activityId = activity.value.id;
  collectingFromGame.value = true;
  try {
    const result = await collectFanxiuYunmengTrialMeasurement(
      activityId,
      undefined,
      '页面手动更新最近批次',
    );
    await Promise.all([
      loadSnapshot(activityId, false),
      loadRankings(),
    ]);
    const count = result.measurement.challenge_count_delta;
    const currencyDelta = result.exchange_currency_delta;
    const speed = result.average_exchange_currency_per_challenge;
    const details = [
      count ? `${formatChineseCompactNumber(count)} 次` : '',
      currencyDelta != null ? `兑币 +${formatChineseCompactNumber(currencyDelta)}` : '',
      speed != null ? `速度 ${formatChineseCompactNumber(speed)}/次` : '',
    ].filter(Boolean).join('，');
    if (showFeedback) ElMessage.success(details ? `已从游戏更新：${details}` : '已从游戏更新');
  } catch (error: any) {
    const detail = error?.response?.data?.detail || error?.message || '从游戏更新失败';
    if (String(detail).includes('已经采集')) {
      await Promise.all([
        loadSnapshot(activityId, false),
        loadRankings(),
      ]);
      if (showFeedback) ElMessage.info('当前最近批次已经是最新数据');
    } else if (showFeedback) {
      ElMessage.error(detail);
    }
  } finally {
    collectingFromGame.value = false;
  }
}

async function setSelected(item: FanxiuYunmengTrialShopItem, checked: boolean) {
  if (!activity.value || !canEdit.value || prioritySaving.value) return;
  const next = selectedGoodsIds.value.filter(goodsId => goodsId !== item.goods_id);
  if (checked) next.push(item.goods_id);
  prioritySaving.value = true;
  try {
    activity.value = await saveFanxiuYunmengTrialPriorities(activity.value.id, next);
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '保存兑换优先级失败');
  } finally {
    prioritySaving.value = false;
  }
}

function isLockSaving(item: FanxiuYunmengTrialShopItem) {
  return lockSavingGoodsIds.value.includes(item.goods_id);
}

async function setLocked(item: FanxiuYunmengTrialShopItem, locked: boolean) {
  if (!activity.value || !canEdit.value || isLockSaving(item)) return;
  const previous = item.locked;
  item.locked = locked;
  lockSavingGoodsIds.value = [...lockSavingGoodsIds.value, item.goods_id];
  try {
    activity.value = await saveFanxiuYunmengTrialShopItemLock(
      activity.value.id,
      item.goods_id,
      locked,
    );
  } catch (error: any) {
    item.locked = previous;
    ElMessage.error(error?.response?.data?.detail || error?.message || '保存锁定状态失败');
  } finally {
    lockSavingGoodsIds.value = lockSavingGoodsIds.value.filter(id => id !== item.goods_id);
  }
}

function changeRankingPage(page: number) {
  rankingPage.value = page;
  void loadRankings();
}

watch(selectedActivityId, value => {
  if (value && value !== activity.value?.id) void changeActivity(value);
});

onMounted(async () => {
  if (activity.value) {
    restoreShopSort(activity.value.id);
    refreshShopOrder();
  } else {
    await loadSnapshot();
  }
  await loadRankings();
  maybeAutoCollectFromGame();
});
</script>

<template>
  <div class="yunmeng-page" :class="{ 'is-embedded': props.embedded }" v-loading="loading">
    <header class="page-header">
      <h2 v-if="!props.embedded">云梦试剑</h2>
      <FanxiuActivityToolbar
        v-model="selectedActivityId"
        :activities="activities"
        :can-collect="canCollect"
        :collect-loading="collectingFromGame"
        :collect-disabled="loading"
        @collect="collectFromGame()"
      >
        <slot name="activity-type-control" />
      </FanxiuActivityToolbar>
    </header>

    <template v-if="activity">
      <section class="section-block">
        <h3>资源策略</h3>
        <dl v-if="resourceStrategyEntries.length" class="strategy-list">
          <template v-for="([key, value]) in resourceStrategyEntries" :key="key">
            <dt>{{ key }}</dt>
            <dd>{{ value }}</dd>
          </template>
        </dl>
        <div v-else class="empty-line">尚未配置</div>
      </section>

      <section class="section-block">
        <div class="section-heading shop-heading">
          <div class="section-title-with-help">
            <h3>兑换宝阁</h3>
            <el-popover placement="bottom-start" :width="440" trigger="click">
              <template #reference>
                <el-button
                  :icon="QuestionFilled"
                  circle
                  text
                  class="shop-rules-help-button"
                  title="查看兑换宝阁规则"
                  aria-label="查看兑换宝阁规则"
                />
              </template>
              <div class="shop-rules-doc">
                <h4>兑换规则</h4>
                <ul>
                  <li><strong>优先级：</strong>勾选后进入领取计划，数字表示理论领取顺序；未勾选的商品不领取。</li>
                  <li><strong>锁定：</strong>仍按优先级预留该商品所需的兑币，但实际领取时跳过它，继续处理后面的商品。预留兑币不能被后续商品占用。</li>
                  <li><strong>累计兑币：</strong>同时累计实际领取开销与锁定预留，因此锁定商品仍会推进累计兑币。</li>
                  <li><strong>还需挑战：</strong>按最近一段挑战的平均兑币收益，估算达到该行累计兑币还需挑战多少次；已经满足或限购不限时留空。</li>
                  <li><strong>无限商品：</strong>放在优先级末尾，用扣除前序领取和锁定预留后的全部剩余兑币兑换。</li>
                </ul>
                <div class="shop-rules-example">
                  <strong>示例</strong>
                  <p>现有 3 万兑币，A 需 1 万、B 需 1 万且锁定、C 需 2 万。领取 A 后剩 2 万；B 不领取但预留 1 万；因此 C 只有 1 万可用，不能消耗完整的 2 万。</p>
                </div>
              </div>
            </el-popover>
          </div>
          <div class="shop-heading-status">
            <div class="shop-metrics">
              <span class="currency-line">
                当前兑币 {{ formatChineseCompactNumber(activity.current_currency) }}，活动累计 {{ formatChineseCompactNumber(activity.cumulative_currency) }}<template v-if="activity.captured_at">，最后更新 {{ formatActivityUpdatedAt(activity.captured_at) }}</template>
              </span>
              <span v-if="activity.yield_rate" class="yield-rate-line">
                平均每百次：积分 {{ formatChineseCompactNumber(activity.yield_rate.average_score_per_100) }}，兑币 {{ formatChineseCompactNumber(activity.yield_rate.average_exchange_currency_per_100) }}（近 {{ formatChineseCompactNumber(activity.yield_rate.sample_challenges) }} 次）
              </span>
            </div>
          </div>
        </div>

        <div class="table-shell" v-loading="prioritySaving">
          <table class="exchange-table">
            <thead>
              <tr>
                <th :aria-sort="ariaSort('source_order')">
                  <button type="button" class="sort-button" @click="setShopSort('source_order')">原序 <span>{{ sortIndicator('source_order') }}</span></button>
                </th>
                <th :aria-sort="ariaSort('priority_order')">
                  <button type="button" class="sort-button" @click="setShopSort('priority_order')">优先级 <span>{{ sortIndicator('priority_order') }}</span></button>
                </th>
                <th :aria-sort="ariaSort('locked')">
                  <button type="button" class="sort-button" @click="setShopSort('locked')">锁定 <span>{{ sortIndicator('locked') }}</span></button>
                </th>
                <th :aria-sort="ariaSort('name')">
                  <button type="button" class="sort-button" @click="setShopSort('name')">名称 <span>{{ sortIndicator('name') }}</span></button>
                </th>
                <th class="number-cell" :aria-sort="ariaSort('token_cost')">
                  <button type="button" class="sort-button number-sort-button" @click="setShopSort('token_cost')">所需兑币 <span>{{ sortIndicator('token_cost') }}</span></button>
                </th>
                <th class="number-cell" :aria-sort="ariaSort('purchase_limit')">
                  <button type="button" class="sort-button number-sort-button" @click="setShopSort('purchase_limit')">限购数量 <span>{{ sortIndicator('purchase_limit') }}</span></button>
                </th>
                <th class="number-cell" :aria-sort="ariaSort('row_total_tokens')">
                  <button type="button" class="sort-button number-sort-button" @click="setShopSort('row_total_tokens')">总计兑币 <span>{{ sortIndicator('row_total_tokens') }}</span></button>
                </th>
                <th class="number-cell" :aria-sort="ariaSort('cumulative_tokens')">
                  <button type="button" class="sort-button number-sort-button" @click="setShopSort('cumulative_tokens')">累计兑币 <span>{{ sortIndicator('cumulative_tokens') }}</span></button>
                </th>
                <th class="number-cell" :aria-sort="ariaSort('remaining_challenges')">
                  <button type="button" class="sort-button number-sort-button" @click="setShopSort('remaining_challenges')">还需挑战 <span>{{ sortIndicator('remaining_challenges') }}</span></button>
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="!activity.shop_items.length">
                <td colspan="9" class="empty-cell">暂无兑换数据</td>
              </tr>
              <tr v-for="item in displayedShopItems" :key="item.id">
                <td>{{ item.source_order }}</td>
                <td>
                  <div class="priority-cell">
                    <el-checkbox
                      :model-value="isSelected(item)"
                      :disabled="!canEdit || prioritySaving"
                      @change="value => setSelected(item, Boolean(value))"
                    />
                    <span v-if="item.priority_order != null" class="priority-order">{{ item.priority_order }}</span>
                  </div>
                </td>
                <td>
                  <el-checkbox
                    :model-value="item.locked"
                    :disabled="!canEdit || isLockSaving(item)"
                    @change="value => setLocked(item, Boolean(value))"
                  />
                </td>
                <td>
                  {{ item.name }}
                  <span v-if="item.goods_num > 1" class="muted">×{{ item.goods_num }}</span>
                </td>
                <td class="number-cell price-cell">
                  <span class="token-price">
                    <del
                      v-if="item.original_price != null && item.original_price > item.token_cost"
                      class="original-price"
                    >{{ formatChineseCompactNumber(item.original_price) }}</del>
                    <span class="actual-price">{{ formatChineseCompactNumber(item.token_cost) }}</span>
                  </span>
                </td>
                <td class="number-cell">{{ item.purchase_limit < 0 ? '不限' : formatChineseCompactNumber(item.purchase_limit) }}</td>
                <td class="number-cell">{{ item.row_total_tokens == null ? '—' : formatChineseCompactNumber(item.row_total_tokens) }}</td>
                <td class="number-cell cumulative-cell">
                  {{ item.cumulative_tokens == null ? '—' : formatChineseCompactNumber(item.cumulative_tokens) }}
                </td>
                <td class="number-cell">
                  {{ item.remaining_challenges == null ? '' : formatChineseCompactNumber(item.remaining_challenges) }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <FanxiuActivityRankingSection
        :personal-rows="personalRankings"
        :plane-rows="planeRankings"
        score-label="积分"
        score-per-reward-label="丹均积分"
        :personal-total="rankingTotal"
        :page="rankingPage"
        :page-size="rankingPageSize"
        :personal-last-captured-at="rankingLastCapturedAt"
        :plane-last-captured-at="planeRankingLastCapturedAt"
        :loading="rankingLoading"
        @page-change="changeRankingPage"
      />
    </template>

    <div v-else-if="!loading" class="page-empty">暂无云梦试剑活动数据</div>
  </div>
</template>

<style scoped>
.yunmeng-page {
  display: flex;
  flex-direction: column;
  gap: 22px;
  padding: 20px;
}

.yunmeng-page.is-embedded {
  padding: 0;
}

.page-header,
.section-heading {
  display: flex;
  align-items: center;
  gap: 16px;
}

.page-header h2,
.section-block h3 {
  margin: 0;
}

.section-title-with-help {
  display: flex;
  align-items: center;
  gap: 3px;
}

.shop-heading {
  align-items: flex-start;
  flex-direction: column;
  gap: 8px;
}

.shop-heading-status {
  display: flex;
  align-items: center;
  gap: 10px;
}

.shop-metrics {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.yield-rate-line {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.section-heading-note {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.shop-rules-help-button {
  width: 24px;
  height: 24px;
  min-height: 24px;
  padding: 0;
  color: var(--el-text-color-secondary);
}

.shop-rules-doc h4 {
  margin: 0 0 8px;
}

.shop-rules-doc ul {
  margin: 0;
  padding-left: 20px;
  line-height: 1.6;
}

.shop-rules-doc li + li {
  margin-top: 5px;
}

.shop-rules-example {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.shop-rules-example p {
  margin: 4px 0 0;
  color: var(--el-text-color-regular);
  line-height: 1.6;
}

.section-block {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 10px;
}

.currency-line,
.empty-line,
.muted {
  color: var(--el-text-color-secondary);
}

.strategy-list {
  display: grid;
  grid-template-columns: max-content max-content;
  gap: 6px 18px;
  margin: 0;
}

.strategy-list dt,
.strategy-list dd {
  margin: 0;
}

.table-shell {
  max-width: 100%;
  overflow-x: auto;
}

table {
  width: max-content;
  max-width: 100%;
  border-collapse: collapse;
  font-size: 14px;
}

th,
td {
  padding: 9px 14px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  text-align: left;
  white-space: nowrap;
}

th {
  color: var(--el-text-color-secondary);
  font-weight: 500;
  background: var(--el-fill-color-light);
}

.sort-button {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-width: 100%;
  padding: 0;
  border: 0;
  color: inherit;
  font: inherit;
  white-space: nowrap;
  cursor: pointer;
  background: transparent;
}

.sort-button span {
  width: 1em;
  color: var(--el-color-primary);
  text-align: center;
}

.number-sort-button {
  justify-content: flex-end;
}

.sort-button:hover,
.sort-button:focus-visible {
  color: var(--el-text-color-primary);
}

.number-cell {
  text-align: right;
}

.price-cell {
  padding-top: 4px;
  padding-bottom: 4px;
}

.token-price {
  display: inline-flex;
  flex-direction: column;
  align-items: flex-end;
  justify-content: flex-end;
  gap: 0;
  line-height: 14px;
  font-variant-numeric: tabular-nums;
}

.original-price {
  color: var(--el-text-color-placeholder);
  font-size: 0.86em;
  text-decoration-thickness: 1px;
}

.priority-cell {
  display: flex;
  align-items: center;
  gap: 6px;
}

.priority-order {
  min-width: 1.5em;
  color: var(--el-color-primary);
  font-variant-numeric: tabular-nums;
}

.cumulative-cell {
  font-weight: 600;
}

.empty-cell,
.page-empty {
  padding: 24px;
  color: var(--el-text-color-secondary);
  text-align: center;
}

.is-self td {
  background: var(--el-color-primary-light-9);
}

.ranking-pagination {
  margin-top: 12px;
}

.subsection-heading {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin: 20px 0 8px;
}

.subsection-heading h4 {
  margin: 0;
}

@media (max-width: 720px) {
  .page-header,
  .section-heading {
    align-items: flex-start;
    flex-direction: column;
  }
  .shop-heading-status {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
