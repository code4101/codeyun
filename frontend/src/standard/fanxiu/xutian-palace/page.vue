<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { QuestionFilled } from '@element-plus/icons-vue';
import FanxiuActivityRankingSection from '@/standard/fanxiu/components/FanxiuActivityRankingSection.vue';
import FanxiuActivityToolbar from '@/standard/fanxiu/components/FanxiuActivityToolbar.vue';
import { formatActivityUpdatedAt } from '@/standard/fanxiu/components/activityStatus';
import { useFanxiuActivityRefresh } from '@/standard/fanxiu/components/useFanxiuActivityRefresh';
import {
  collectFanxiuExchangeActivity,
  getFanxiuExchangeActivityRankings,
  getFanxiuExchangeActivitySnapshot,
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

interface PlannedShopRow {
  item: FanxiuExchangeShopItem;
  priorityLevel: number | null;
  priorityId: string;
  groupRowSpan: number;
  isGroupStart: boolean;
}

const loading = ref(false);
const collectingFromGame = ref(false);
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

const {
  canCollect,
  maybeAutoCollect: maybeAutoCollectFromGame,
} = useFanxiuActivityRefresh({
  activity,
  collectSilently: () => collectFromGame(false),
});
const plannedShopRows = computed<PlannedShopRow[]>(() => {
  const items = activity.value?.shop_items || [];
  const itemByGoodsId = new Map(items.map(item => [item.goods_id, item]));
  const priorityIds = activity.value?.exchange_plan?.priority_order_ids || [];
  const groups = activity.value?.exchange_plan?.priority_group_goods_ids || {};
  const included = new Set<number>();
  const rows: PlannedShopRow[] = [];

  priorityIds.forEach((priorityId, index) => {
    const groupItems = (groups[priorityId] || [])
      .map(goodsId => itemByGoodsId.get(goodsId))
      .filter((item): item is FanxiuExchangeShopItem => Boolean(item && !included.has(item.goods_id)));
    groupItems.forEach((item, groupIndex) => {
      included.add(item.goods_id);
      rows.push({
        item,
        priorityLevel: index + 1,
        priorityId,
        groupRowSpan: groupItems.length,
        isGroupStart: groupIndex === 0,
      });
    });
  });

  // 兼容旧 schema：未进入领取档次的遗留商品统一投影到固定第 14 级。
  const notNeededItems = items.filter(item => !included.has(item.goods_id));
  notNeededItems.forEach((item, groupIndex) => {
    rows.push({
      item,
      priorityLevel: 14,
      priorityId: '不需要领',
      groupRowSpan: notNeededItems.length,
      isGroupStart: groupIndex === 0,
    });
  });
  return rows;
});

function remainingPurchaseCount(item: FanxiuExchangeShopItem) {
  if (item.purchase_limit < 0) return null;
  return Math.max(0, item.purchase_limit - item.purchased_count);
}

function formatPurchaseCount(value: number | null) {
  return value == null ? '不限' : formatChineseCompactNumber(value);
}

async function loadSnapshot(activityId?: string) {
  loading.value = true;
  try {
    const snapshot = await getFanxiuExchangeActivitySnapshot(props.activityType, activityId);
    activities.value = snapshot.activities;
    activity.value = snapshot.selected_activity || null;
    selectedActivityId.value = activity.value?.id || '';
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
        <div class="section-heading shop-heading">
          <div class="title-with-help">
            <h3>兑换宝阁</h3>
            <el-popover placement="bottom-start" :width="440" trigger="click">
              <template #reference><el-button :icon="QuestionFilled" circle text class="help" aria-label="查看兑换规则" /></template>
              <div class="rules">
                <h4>兑换规则</h4>
                <ul>
                  <li><strong>等级与 ID：</strong>按后端固定规则展示业务档次；每档只展开本期兑换宝阁实际存在的商品。</li>
                  <li><strong>优先级：</strong>等级就是后端算法给出的固定领取顺序，前端只读展示。</li>
                  <li><strong>锁定：</strong>“锁定”表示按优先级预留兑币但暂不领取，前端只读展示。</li>
                  <li><strong>跨周预留：</strong>由后端根据活动页面关闭时间与祈愿周边界自动计算。</li>
                  <li><strong>活动目标：</strong>后端按活动规则计算目标档次；不限量玄灵丹只作兑币溢出兜底。</li>
                  <li><strong>累计兑币：</strong>累计实际领取和锁定预留；不限购商品不计算总计及累计。</li>
                  <li><strong>还需挑战：</strong>按近期挑战速度估算达到该行累计兑币还需挑战多少次；速度尚无数据、已经满足或不限购时留空。</li>
                </ul>
              </div>
            </el-popover>
          </div>
          <div class="shop-heading-status">
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
        </div>
        <div class="table-shell">
          <table>
            <thead><tr>
              <th>等级</th>
              <th>ID</th>
              <th>锁定</th>
              <th>名称</th>
              <th class="number">所需{{ activity.currency_name || '代币' }}</th>
              <th class="number">限购数量</th>
              <th class="number">已购</th>
              <th class="number">剩余</th>
              <th class="number">总计{{ activity.currency_name || '代币' }}</th>
              <th class="number">累计{{ activity.currency_name || '代币' }}</th>
              <th class="number">还需挑战</th>
            </tr></thead>
            <tbody>
              <tr v-if="!plannedShopRows.length"><td colspan="11" class="empty">暂无兑换数据</td></tr>
              <tr v-for="row in plannedShopRows" :key="row.item.id">
                <td v-if="row.isGroupStart" class="priority-level" :rowspan="row.groupRowSpan">{{ row.priorityLevel ?? '—' }}</td>
                <td v-if="row.isGroupStart" class="priority-id" :rowspan="row.groupRowSpan">{{ row.priorityId }}</td>
                <td>{{ row.item.locked ? '锁定' : '—' }}</td>
                <td>{{ row.item.name }} <span v-if="row.item.goods_num > 1" class="muted">×{{ row.item.goods_num }}</span></td>
                <td class="number price"><span><del v-if="row.item.original_price != null && row.item.original_price > row.item.token_cost">{{ formatChineseCompactNumber(row.item.original_price) }}</del>{{ formatChineseCompactNumber(row.item.token_cost) }}</span></td>
                <td class="number">{{ formatPurchaseCount(row.item.purchase_limit < 0 ? null : row.item.purchase_limit) }}</td>
                <td class="number">{{ formatChineseCompactNumber(row.item.purchased_count) }}</td>
                <td class="number">{{ formatPurchaseCount(remainingPurchaseCount(row.item)) }}</td>
                <td class="number">{{ row.item.row_total_tokens == null ? '—' : formatChineseCompactNumber(row.item.row_total_tokens) }}</td>
                <td class="number cumulative">{{ row.item.cumulative_tokens == null ? '—' : formatChineseCompactNumber(row.item.cumulative_tokens) }}</td>
                <td class="number">{{ row.item.remaining_challenges == null ? '' : formatChineseCompactNumber(row.item.remaining_challenges) }}</td>
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
.exchange-page{display:flex;flex-direction:column;gap:22px;padding:20px}.exchange-page.is-embedded{padding:0}.page-header,.section-heading,.title-with-help,.shop-heading-status{display:flex;align-items:center;gap:16px}.shop-heading{align-items:flex-start;flex-direction:column;gap:8px}.title-with-help{gap:3px}.shop-heading-status{gap:10px}.page-header h2,.section-block h3{margin:0}.section-block{display:flex;flex-direction:column;align-items:flex-start;gap:10px}.muted{color:var(--el-text-color-secondary)}.budget-not-ready{color:var(--el-color-warning)}.help{width:24px;height:24px;padding:0;color:var(--el-text-color-secondary)}.rules h4{margin:0 0 8px}.rules ul{margin:0;padding-left:20px;line-height:1.7}.table-shell{max-width:100%;overflow-x:auto}table{width:max-content;max-width:100%;border-collapse:collapse;font-size:14px}th,td{padding:9px 14px;border-bottom:1px solid var(--el-border-color-lighter);text-align:left;white-space:nowrap}th{color:var(--el-text-color-secondary);font-weight:500;background:var(--el-fill-color-light)}.number{text-align:right}.priority-level,.priority-id{font-weight:600;vertical-align:top}.price{padding-top:4px;padding-bottom:4px}.price span{display:inline-flex;flex-direction:column;align-items:flex-end;line-height:14px}.price del{color:var(--el-text-color-placeholder);font-size:.86em}.cumulative{font-weight:600}.empty{padding:24px;color:var(--el-text-color-secondary);text-align:center}.self{background:var(--el-color-primary-light-9)}.vacant{color:var(--el-text-color-secondary)}
</style>
