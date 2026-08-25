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
  collectFanxiuExchangeActivity,
  getFanxiuExchangeActivityRankings,
  getFanxiuExchangeActivitySnapshot,
  planFanxiuExchangeActivityShop,
  saveFanxiuExchangeActivityPriorities,
  saveFanxiuExchangeActivityShopItemLock,
  type FanxiuExchangeActivityDetail,
  type FanxiuExchangeActivitySnapshot,
  type FanxiuExchangeActivitySummary,
  type FanxiuExchangeShopItem,
  type FanxiuExchangeRankingItem,
} from '@/api/fanxiu';
import { formatChineseCompactNumber } from '@/utils/numberFormat';

const props = withDefaults(defineProps<{
  embedded?: boolean;
  initialSnapshot?: FanxiuExchangeActivitySnapshot;
  activityType?: string;
  activityName?: string;
  comparativeRankingScope?: string;
  comparativeRankingTitle?: string;
  comparativeRankingSubjectLabel?: string;
  comparativeRankingEmptyText?: string;
}>(), {
  embedded: false,
  initialSnapshot: undefined,
  activityType: 'xutian-palace',
  activityName: '虚天殿',
  comparativeRankingScope: 'plane',
  comparativeRankingTitle: '位面排名',
  comparativeRankingSubjectLabel: '位面',
  comparativeRankingEmptyText: '尚未加载位面榜运行态数据',
});

type SortKey = 'source_order' | 'priority_order' | 'locked' | 'name' | 'token_cost'
  | 'purchase_limit' | 'row_total_tokens' | 'cumulative_tokens' | 'remaining_challenges';
type Direction = 'asc' | 'desc';

const loading = ref(false);
const collectingFromGame = ref(false);
const saving = ref(false);
const planning = ref(false);
const lockSavingIds = ref<number[]>([]);
const activities = ref<FanxiuExchangeActivitySummary[]>(props.initialSnapshot?.activities || []);
const selectedActivityId = ref(props.initialSnapshot?.selected_activity?.id || '');
const activity = ref<FanxiuExchangeActivityDetail | null>(props.initialSnapshot?.selected_activity || null);
const personalRankings = ref<FanxiuExchangeRankingItem[]>([]);
const planeRankings = ref<FanxiuExchangeRankingItem[]>([]);
const rankingPage = ref(1);
const rankingPageSize = 20;
const rankingTotal = ref(0);
const rankingLastCapturedAt = ref('');
const planeRankingLastCapturedAt = ref('');
const sort = ref<{ key: SortKey; direction: Direction }>(
  defaultExchangeShopSort(activity.value?.shop_items),
);

const {
  canEdit,
  canCollect,
  maybeAutoCollect: maybeAutoCollectFromGame,
} = useFanxiuActivityRefresh({
  activity,
  collectSilently: () => collectFromGame(false),
});
const strategyEntries = computed(() => Object.entries(activity.value?.resource_strategy || {}));
const selectedGoodsIds = computed(() => [...(activity.value?.shop_items || [])]
  .filter(item => item.priority_order != null)
  .sort((a, b) => Number(a.priority_order) - Number(b.priority_order))
  .map(item => item.goods_id));
const displayedItems = computed(() => [...(activity.value?.shop_items || [])].sort((a, b) => {
  const key = sort.value.key;
  let result = 0;
  if (key === 'name') result = a.name.localeCompare(b.name, 'zh-CN');
  else if (key === 'locked') result = Number(a.locked) - Number(b.locked);
  else {
    const left = a[key];
    const right = b[key];
    if (left == null && right == null) result = 0;
    else if (left == null) result = 1;
    else if (right == null) result = -1;
    else result = left - right;
  }
  return (sort.value.direction === 'asc' ? result : -result) || a.source_order - b.source_order;
}));

async function loadSnapshot(activityId?: string) {
  loading.value = true;
  try {
    const snapshot = await getFanxiuExchangeActivitySnapshot(props.activityType, activityId);
    activities.value = snapshot.activities;
    activity.value = snapshot.selected_activity || null;
    selectedActivityId.value = activity.value?.id || '';
    sort.value = defaultExchangeShopSort(activity.value?.shop_items);
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || `读取${props.activityName}数据失败`);
  } finally {
    loading.value = false;
  }
}

async function loadRankings() {
  if (!selectedActivityId.value) return;
  try {
    const [personal, plane] = await Promise.all([
      getFanxiuExchangeActivityRankings(props.activityType, selectedActivityId.value, rankingPage.value, rankingPageSize, 'personal'),
      getFanxiuExchangeActivityRankings(
        props.activityType,
        selectedActivityId.value,
        1,
        100,
        props.comparativeRankingScope,
      ),
    ]);
    personalRankings.value = personal.items;
    planeRankings.value = plane.items;
    rankingTotal.value = personal.total;
    rankingLastCapturedAt.value = personal.last_captured_at || '';
    planeRankingLastCapturedAt.value = plane.last_captured_at || '';
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || `读取${props.activityName}榜单失败`);
  }
}

async function collectFromGame(showFeedback = true) {
  if (!activity.value || !canCollect.value || collectingFromGame.value) return;
  collectingFromGame.value = true;
  try {
    activity.value = await collectFanxiuExchangeActivity(props.activityType, activity.value.id);
    await loadRankings();
    if (showFeedback) ElMessage.success('已从游戏更新');
  } catch (error: any) {
    if (showFeedback) {
      ElMessage.error(error?.response?.data?.detail || error?.message || '从游戏更新失败');
    }
  } finally {
    collectingFromGame.value = false;
  }
}

function setSort(key: SortKey) {
  sort.value = sort.value.key === key
    ? { key, direction: sort.value.direction === 'asc' ? 'desc' : 'asc' }
    : { key, direction: 'asc' };
}

function indicator(key: SortKey) {
  return sort.value.key === key ? (sort.value.direction === 'asc' ? '↑' : '↓') : '';
}

function isSelected(item: FanxiuExchangeShopItem) {
  return selectedGoodsIds.value.includes(item.goods_id);
}

async function setSelected(item: FanxiuExchangeShopItem, checked: boolean) {
  if (!activity.value || saving.value || !canEdit.value) return;
  const ids = selectedGoodsIds.value.filter(id => id !== item.goods_id);
  if (checked) ids.push(item.goods_id);
  saving.value = true;
  try {
    activity.value = await saveFanxiuExchangeActivityPriorities(props.activityType, activity.value.id, ids);
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '保存兑换优先级失败');
  } finally {
    saving.value = false;
  }
}

async function setLocked(item: FanxiuExchangeShopItem, locked: boolean) {
  if (!activity.value || !canEdit.value || lockSavingIds.value.includes(item.goods_id)) return;
  const previous = item.locked;
  item.locked = locked;
  lockSavingIds.value.push(item.goods_id);
  try {
    activity.value = await saveFanxiuExchangeActivityShopItemLock(
      props.activityType, activity.value.id, item.goods_id, locked,
    );
  } catch (error: any) {
    item.locked = previous;
    ElMessage.error(error?.response?.data?.detail || error?.message || '保存锁定状态失败');
  } finally {
    lockSavingIds.value = lockSavingIds.value.filter(id => id !== item.goods_id);
  }
}

async function planShop() {
  if (!activity.value || !canEdit.value || planning.value) return;
  planning.value = true;
  try {
    activity.value = await planFanxiuExchangeActivityShop(
      props.activityType,
      activity.value.id,
    );
    sort.value = defaultExchangeShopSort(activity.value.shop_items);
    ElMessage.success('已按祈愿周、折扣与限购规则生成兑换规划');
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '计算兑换规划失败');
  } finally {
    planning.value = false;
  }
}

watch(selectedActivityId, value => {
  if (value && value !== activity.value?.id) void loadSnapshot(value).then(loadRankings);
});
onMounted(async () => {
  if (props.initialSnapshot) await loadRankings();
  else await loadSnapshot().then(loadRankings);
  maybeAutoCollectFromGame();
});
</script>

<template>
  <div class="exchange-page" :class="{ 'is-embedded': props.embedded }" v-loading="loading">
    <header class="page-header">
      <h2 v-if="!props.embedded">{{ props.activityName }}</h2>
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
        <dl v-if="strategyEntries.length" class="strategy-list">
          <template v-for="([key, value]) in strategyEntries" :key="key"><dt>{{ key }}</dt><dd>{{ value }}</dd></template>
        </dl>
        <div v-else class="muted">尚未配置</div>
      </section>

      <slot name="activity-strategy" :activity="activity" />

      <section class="section-block">
        <div class="section-heading shop-heading">
          <div class="title-with-help">
            <h3>兑换宝阁</h3>
            <el-popover placement="bottom-start" :width="440" trigger="click">
              <template #reference><el-button :icon="QuestionFilled" circle text class="help" aria-label="查看兑换规则" /></template>
              <div class="rules">
                <h4>兑换规则</h4>
                <ul>
                  <li><strong>优先级：</strong>勾选后进入领取计划，数字表示理论领取顺序；未勾选的不领取。</li>
                  <li><strong>锁定：</strong>按优先级预留兑币但跳过领取，预留部分不能被后续商品占用。</li>
                  <li><strong>自动规划：</strong>按活动结束日对应的祈愿周计算；周日结束时预留下周祈愿资源，再预留一项卡邮件资源，锁定总数最多两项。</li>
                  <li><strong>活动目标：</strong>常规完成到各功法全部最低折扣轮次及其余折扣条目；有条件再完成第9层限购物品。不限量玄灵丹只作兑币溢出兜底。</li>
                  <li><strong>累计兑币：</strong>累计实际领取和锁定预留；不限购商品不计算总计及累计。</li>
                  <li><strong>还需挑战：</strong>按近期挑战速度估算达到该行累计兑币还需挑战多少次；速度尚无数据、已经满足或不限购时留空。</li>
                </ul>
              </div>
            </el-popover>
          </div>
          <div class="shop-heading-status">
            <el-button size="small" type="primary" plain :loading="planning" :disabled="!canEdit || saving" @click="planShop">自动规划</el-button>
            <span class="muted">
              {{ activity.currency_fact_fresh ? '当前兑币' : '最近兑币' }} {{ formatChineseCompactNumber(activity.current_currency) }}，活动累计 {{ formatChineseCompactNumber(activity.cumulative_currency) }}（{{ activity.currency_name }}）<template v-if="activity.currency_captured_at || activity.captured_at">，最后更新 {{ formatActivityUpdatedAt(activity.currency_captured_at || activity.captured_at) }}</template><template v-if="!activity.budget_ready">，<span class="budget-not-ready" :title="activity.budget_block_reason">预算未就绪</span></template>
            </span>
          </div>
          <div class="muted">
            本期兑换宝阁 {{ activity.shop_items.length }} 项，清单采集
            {{ activity.shop_snapshot_captured_at ? formatActivityUpdatedAt(activity.shop_snapshot_captured_at) : '时间未知' }}。
            <template v-if="activity.shop_refresh_status === 'retained'">
              商品清单已保留；本次购买进度未刷新：{{ activity.shop_refresh_reason || '运行态页面未加载' }}
            </template>
            <template v-else-if="activity.shop_refresh_status === 'updated'">商品与购买进度已刷新。</template>
          </div>
          <div v-if="activity.exchange_plan?.stage8_budget && activity.exchange_plan?.stage9_budget" class="budget-summary">
            <span>第8层{{ activity.budget_ready ? '尚需新获' : '最近理论缺口' }} {{ formatChineseCompactNumber(activity.exchange_plan.stage8_budget.required_new_currency) }}</span>
            <span>第9层{{ activity.budget_ready ? '尚需新获' : '最近理论缺口' }} {{ formatChineseCompactNumber(activity.exchange_plan.stage9_budget.required_new_currency) }}</span>
            <span v-if="activity.exchange_plan.locked_reserved_tokens">已含锁定预留 {{ formatChineseCompactNumber(activity.exchange_plan.locked_reserved_tokens) }}</span>
          </div>
        </div>
        <div class="table-shell" v-loading="saving">
          <table>
            <thead><tr>
              <th><button class="sort-button" @click="setSort('source_order')">原序 {{ indicator('source_order') }}</button></th>
              <th><button class="sort-button" @click="setSort('priority_order')">优先级 {{ indicator('priority_order') }}</button></th>
              <th><button class="sort-button" @click="setSort('locked')">锁定 {{ indicator('locked') }}</button></th>
              <th><button class="sort-button" @click="setSort('name')">名称 {{ indicator('name') }}</button></th>
              <th class="number"><button class="sort-button number-sort-button" @click="setSort('token_cost')">所需兑币 {{ indicator('token_cost') }}</button></th>
              <th class="number"><button class="sort-button number-sort-button" @click="setSort('purchase_limit')">限购数量 {{ indicator('purchase_limit') }}</button></th>
              <th class="number"><button class="sort-button number-sort-button" @click="setSort('row_total_tokens')">总计兑币 {{ indicator('row_total_tokens') }}</button></th>
              <th class="number"><button class="sort-button number-sort-button" @click="setSort('cumulative_tokens')">累计兑币 {{ indicator('cumulative_tokens') }}</button></th>
              <th class="number"><button class="sort-button number-sort-button" @click="setSort('remaining_challenges')">还需挑战 {{ indicator('remaining_challenges') }}</button></th>
            </tr></thead>
            <tbody>
              <tr v-if="!displayedItems.length"><td colspan="9" class="empty">暂无兑换数据</td></tr>
              <tr v-for="item in displayedItems" :key="item.id">
                <td>{{ item.source_order }}</td>
                <td><span class="priority"><el-checkbox :model-value="isSelected(item)" :disabled="!canEdit || saving" @change="value => setSelected(item, Boolean(value))" /><i v-if="item.priority_order != null">{{ item.priority_order }}</i></span></td>
                <td><el-checkbox :model-value="item.locked" :disabled="!canEdit || lockSavingIds.includes(item.goods_id)" @change="value => setLocked(item, Boolean(value))" /></td>
                <td>{{ item.name }} <span v-if="item.goods_num > 1" class="muted">×{{ item.goods_num }}</span></td>
                <td class="number price"><span><del v-if="item.original_price != null && item.original_price > item.token_cost">{{ formatChineseCompactNumber(item.original_price) }}</del>{{ formatChineseCompactNumber(item.token_cost) }}</span></td>
                <td class="number">{{ item.purchase_limit < 0 ? '不限' : formatChineseCompactNumber(item.purchase_limit) }}</td>
                <td class="number">{{ item.row_total_tokens == null ? '—' : formatChineseCompactNumber(item.row_total_tokens) }}</td>
                <td class="number cumulative">{{ item.cumulative_tokens == null ? '—' : formatChineseCompactNumber(item.cumulative_tokens) }}</td>
                <td class="number">{{ item.remaining_challenges == null ? '' : formatChineseCompactNumber(item.remaining_challenges) }}</td>
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
        :plane-title="props.comparativeRankingTitle"
        :plane-subject-label="props.comparativeRankingSubjectLabel"
        :plane-empty-text="props.comparativeRankingEmptyText"
        @page-change="page => { rankingPage = page; loadRankings(); }"
      />
    </template>
    <div v-else-if="!loading" class="empty">暂无{{ props.activityName }}活动数据</div>
  </div>
</template>

<style scoped>
.exchange-page{display:flex;flex-direction:column;gap:22px;padding:20px}.exchange-page.is-embedded{padding:0}.page-header,.section-heading,.title-with-help,.shop-heading-status,.budget-summary{display:flex;align-items:center;gap:16px}.shop-heading{align-items:flex-start;flex-direction:column;gap:8px}.title-with-help{gap:3px}.shop-heading-status{gap:10px}.budget-summary{flex-wrap:wrap;color:var(--el-text-color-regular);font-size:13px}.page-header h2,.section-block h3{margin:0}.section-block{display:flex;flex-direction:column;align-items:flex-start;gap:10px}.strategy-list{display:grid;grid-template-columns:max-content max-content;gap:6px 18px;margin:0}.strategy-list dt,.strategy-list dd{margin:0}.muted{color:var(--el-text-color-secondary)}.budget-not-ready{color:var(--el-color-warning)}.help{width:24px;height:24px;padding:0;color:var(--el-text-color-secondary)}.rules h4{margin:0 0 8px}.rules ul{margin:0;padding-left:20px;line-height:1.7}.table-shell{max-width:100%;overflow-x:auto}table{width:max-content;max-width:100%;border-collapse:collapse;font-size:14px}th,td{padding:9px 14px;border-bottom:1px solid var(--el-border-color-lighter);text-align:left;white-space:nowrap}th{color:var(--el-text-color-secondary);font-weight:500;background:var(--el-fill-color-light)}.sort-button{display:inline-flex;align-items:center;justify-content:flex-start;width:100%;padding:0;border:0;color:inherit;font:inherit;text-align:left;cursor:pointer;background:transparent}.number{text-align:right}.number-sort-button{justify-content:flex-end;text-align:right}.priority{display:flex;align-items:center;gap:6px}.priority i{color:var(--el-color-primary);font-style:normal}.price{padding-top:4px;padding-bottom:4px}.price span{display:inline-flex;flex-direction:column;align-items:flex-end;line-height:14px}.price del{color:var(--el-text-color-placeholder);font-size:.86em}.cumulative{font-weight:600}.empty{padding:24px;color:var(--el-text-color-secondary);text-align:center}.self{background:var(--el-color-primary-light-9)}.vacant{color:var(--el-text-color-secondary)}
</style>
