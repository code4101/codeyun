<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { ElMessage } from 'element-plus';
import {
  collectFanxiuMagicTreasureHall,
  getFanxiuMagicTreasureHall,
  type FanxiuMagicTreasureHallSnapshot,
  type FanxiuMagicTreasureItem,
} from '@/api/fanxiu';
import { useResizablePane } from '@/utils/useResizablePane';
import FanxiuActivityUpdateButton from '../components/FanxiuActivityUpdateButton.vue';
import FanxiuGameRichText from '../components/FanxiuGameRichText.vue';
import { shouldAutoCollectInventorySnapshot } from '../components/inventorySnapshotStatus';

const snapshot = ref<FanxiuMagicTreasureHallSnapshot | null>(null);
const loading = ref(false);
const collecting = ref(false);
const autoCollectAttempted = ref(false);
const keyword = ref('');
const selectedId = ref('');

const allItems = computed<FanxiuMagicTreasureItem[]>(() => {
  if (!snapshot.value) return [];
  return [
    ...snapshot.value.fabao,
    ...snapshot.value.xiantiangubao,
    ...snapshot.value.houtiangubao,
  ];
});

const filteredItems = computed(() => {
  const query = keyword.value.trim().toLocaleLowerCase();
  return allItems.value
    .filter(item => {
      if (!query) return true;
      return [item.name, item.type, item.original_effect, item.shenlian_effect]
        .some(value => String(value || '').toLocaleLowerCase().includes(query));
    })
    .sort((left, right) => (
      Number(right.owned) - Number(left.owned)
      || Number(right.catalog_quality ?? right.quality ?? 0) - Number(left.catalog_quality ?? left.quality ?? 0)
      || Number(right.rank || 0) - Number(left.rank || 0)
      || Number(right.wujing_level || 0) - Number(left.wujing_level || 0)
      || left.name.localeCompare(right.name, 'zh-CN')
    ));
});

const selectedItem = computed(() => (
  allItems.value.find(item => item.id === selectedId.value) || filteredItems.value[0] || null
));

const ROUTINE_UPGRADE_ATTRIBUTE_NAMES = [
  '攻击',
  '灵力',
  '气血',
  '气血上限',
  '守御',
  '灵力恢复',
  '功法增伤',
  '功法减伤',
  '攻击加成',
  '灵力加成',
  '气血加成',
  '攻击资质',
  '灵力资质',
  '气血资质',
].join('|');
const ROUTINE_UPGRADE_NUMBER_PATTERN = String.raw`(?:\d[\d,]*(?:\.\d+)?|\.\d+)(?:万|亿)?%?`;
const ROUTINE_UPGRADE_EFFECT_RE = new RegExp(
  String.raw`^(?:${ROUTINE_UPGRADE_ATTRIBUTE_NAMES})\s*\+\s*${ROUTINE_UPGRADE_NUMBER_PATTERN}$`,
);
const ROUTINE_PERMANENT_EFFECT_RE = new RegExp(
  String.raw`^角色永久增加(?:攻击|灵力|气血|气血上限)加成\s*${ROUTINE_UPGRADE_NUMBER_PATTERN}$`,
);

function isRoutineUpgradeEffect(description: string) {
  const text = String(description || '').trim();
  return ROUTINE_UPGRADE_EFFECT_RE.test(text) || ROUTINE_PERMANENT_EFFECT_RE.test(text);
}

const visibleUpgradeEffects = computed(() => {
  const item = selectedItem.value;
  if (!item) return [];
  return item.upgrade_effects.filter(effect => (
    effect.stage === item.rank
    || !isRoutineUpgradeEffect(effect.description)
    || Math.abs(effect.stage - item.rank) <= 5
  ));
});

const updatedAtLabel = computed(() => {
  const timestamp = Number(snapshot.value?.runtime_updated_at || 0);
  if (!timestamp) return snapshot.value?.runtime_error || '尚未从游戏实时更新';
  return new Date(timestamp * 1000).toLocaleString('zh-CN', { hour12: false });
});

function calculateListPaneBounds() {
  const available = Math.max(520, window.innerHeight - 170);
  return {
    adaptiveHeight: Math.max(250, Math.min(430, Math.floor(available * 0.48))),
    maxHeight: Math.max(300, available - 260),
  };
}

const { paneHeight: listPaneHeight, startResizing } = useResizablePane({
  initialHeight: 360,
  getAdaptiveHeight: () => calculateListPaneBounds().adaptiveHeight,
  getResizeBounds: () => ({ min: 220, max: calculateListPaneBounds().maxHeight }),
  storageKey: 'fanxiu:magic-treasure-hall:list-pane-height:v3',
});

function selectFirstAvailable() {
  if (!filteredItems.value.some(item => item.id === selectedId.value)) {
    selectedId.value = filteredItems.value[0]?.id || '';
  }
}

function hasShenlianProgression(item: FanxiuMagicTreasureItem) {
  return item.shenlian_gradients.some(gradient => gradient.level > 0);
}

const visibleShenlianGradients = computed(() => (
  selectedItem.value?.shenlian_gradients.filter(gradient => gradient.level > 0) || []
));

function shenlianMixedFraction(item: FanxiuMagicTreasureItem) {
  const level = Math.max(0, Math.trunc(Number(item.wujing_level) || 0));
  return {
    whole: Math.floor(level / 9),
    numerator: level % 9,
    denominator: 9,
  };
}

function shenlianMixedFractionLabel(item: FanxiuMagicTreasureItem) {
  const { whole, numerator, denominator } = shenlianMixedFraction(item);
  return whole ? `${whole} ${numerator}／${denominator}` : `${numerator}／${denominator}`;
}

async function loadSnapshot() {
  loading.value = true;
  try {
    snapshot.value = await getFanxiuMagicTreasureHall();
    selectFirstAvailable();
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '读取法宝数据失败');
  } finally {
    loading.value = false;
  }
  maybeAutoCollectFromGame();
}

async function collectFromGame(options: { automatic?: boolean } = {}) {
  if (collecting.value) return;
  collecting.value = true;
  try {
    snapshot.value = await collectFanxiuMagicTreasureHall();
    selectFirstAvailable();
    if (!options.automatic) {
      ElMessage.success(`已从游戏更新 ${snapshot.value.runtime_item_count} 件法宝`);
    }
  } catch (error: any) {
    if (!options.automatic) {
      ElMessage.error(error?.response?.data?.detail || error?.message || '从游戏更新法宝失败');
    }
  } finally {
    collecting.value = false;
  }
}

function maybeAutoCollectFromGame() {
  if (autoCollectAttempted.value || !snapshot.value) return false;
  autoCollectAttempted.value = true;
  if (!shouldAutoCollectInventorySnapshot(snapshot.value.runtime_updated_at)) return false;
  void collectFromGame({ automatic: true });
  return true;
}

function rowClassName({ row }: { row: FanxiuMagicTreasureItem }) {
  return [
    row.id === selectedItem.value?.id ? 'is-current-treasure' : '',
    !row.owned ? 'is-unowned-treasure' : '',
  ].filter(Boolean).join(' ');
}

function catalogHref(item: FanxiuMagicTreasureItem) {
  if (!item.catalog_item_id) return '';
  const query = new URLSearchParams({ tab: 'item', id: String(item.catalog_item_id) });
  return `/standalone/fanxiu/wiki?${query.toString()}`;
}

function catalogQualityColor(item: FanxiuMagicTreasureItem) {
  const color = String(item.catalog_quality_color || '').replace(/^#/, '');
  return /^[0-9a-f]{6}$/i.test(color) ? `#${color}` : 'var(--el-text-color-regular)';
}

function catalogQualityStyle(item: FanxiuMagicTreasureItem) {
  return { '--treasure-quality-color': catalogQualityColor(item) };
}

onMounted(loadSnapshot);
</script>

<template>
  <section class="magic-treasure-page" v-loading="loading">
    <header class="page-toolbar">
      <div class="heading">
        <h1>法宝殿</h1>
        <span class="count">{{ allItems.length }} 件</span>
        <span class="updated">数据：{{ updatedAtLabel }}</span>
      </div>
      <div class="toolbar-actions">
        <el-input
          v-model="keyword"
          class="search-input"
          clearable
          size="small"
          placeholder="搜索名称或效果"
        />
        <FanxiuActivityUpdateButton
          :visible="true"
          :loading="collecting"
          :disabled="loading"
          @collect="collectFromGame"
        />
      </div>
    </header>

    <div class="list-pane" :style="{ height: `${listPaneHeight}px` }">
      <el-table
        v-if="filteredItems.length"
        :data="filteredItems"
        height="100%"
        size="small"
        table-layout="auto"
        scrollbar-always-on
        class="compact-table"
        row-key="id"
        :fit="false"
        :row-class-name="rowClassName"
        @row-click="selectedId = $event.id"
      >
        <el-table-column type="index" label="编号" width="56" align="right" />
        <el-table-column prop="name" label="法宝" width="170" show-overflow-tooltip>
          <template #default="{ row }">
            <span
              class="treasure-name"
              :style="catalogQualityStyle(row)"
              :title="row.catalog_quality_name || row.name"
            >{{ row.name }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="category" label="分类" width="92" />
        <el-table-column prop="type" label="定位" width="72" />
        <el-table-column label="阶数" width="72" align="right">
          <template #default="{ row }">{{ row.rank }} 阶</template>
        </el-table-column>
        <el-table-column label="神炼" width="72" align="center">
          <template #default="{ row }">
            <span
              v-if="hasShenlianProgression(row)"
              class="shenlian-mixed-number"
              :aria-label="`神炼 ${shenlianMixedFractionLabel(row)}`"
              :title="`神炼节点等级 ${row.wujing_level}`"
            >
              <span v-if="shenlianMixedFraction(row).whole" class="mixed-number-whole">
                {{ shenlianMixedFraction(row).whole }}
              </span>
              <span class="stacked-fraction" aria-hidden="true">
                <span class="fraction-numerator">{{ shenlianMixedFraction(row).numerator }}</span>
                <span class="fraction-denominator">{{ shenlianMixedFraction(row).denominator }}</span>
              </span>
            </span>
            <span v-else>无</span>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-else description="数据库中还没有法宝数据，请从游戏更新" :image-size="72" />
    </div>

    <div class="resize-handle" title="拖动调整列表高度" @mousedown.prevent="startResizing">
      <span />
    </div>

    <main class="detail-pane">
      <template v-if="selectedItem">
        <div class="detail-heading">
          <h2>{{ selectedItem.name }}</h2>
          <span
            v-if="selectedItem.catalog_quality_name"
            class="catalog-quality"
            :style="{ color: catalogQualityColor(selectedItem) }"
          >{{ selectedItem.catalog_quality_name }}</span>
          <a
            v-if="catalogHref(selectedItem)"
            class="catalog-link"
            :href="catalogHref(selectedItem)"
            target="_blank"
            rel="noopener"
            @click.stop
          >查看完整图鉴</a>
        </div>

        <section
          v-if="selectedItem.catalog_description"
          class="catalog-summary"
        >
          <p>{{ selectedItem.catalog_description }}</p>
        </section>

        <section class="progression-section">
          <div class="progression-title">
            <h3>升阶效果</h3>
            <span>{{ selectedItem.rank }}阶</span>
          </div>
          <ol
            v-if="visibleUpgradeEffects.length"
            class="progression-list upgrade-effect-list"
          >
            <li
              v-for="effect in visibleUpgradeEffects"
              :key="effect.stage"
              :class="{
                'is-active': effect.stage <= selectedItem.rank,
                'is-current': effect.stage === selectedItem.rank,
              }"
            >
              <strong class="progression-level">{{ effect.stage }} 阶</strong>
              <p>
                <FanxiuGameRichText :text="effect.description" :segments="effect.segments" />
              </p>
            </li>
          </ol>
          <el-empty v-else description="该法宝暂未读取到升阶效果" :image-size="64" />
        </section>

        <section v-if="hasShenlianProgression(selectedItem)" class="progression-section">
          <div class="progression-title">
            <h3>神炼效果</h3>
            <span>{{ selectedItem.wujing_level }}炼</span>
          </div>
          <ol
            v-if="visibleShenlianGradients.length"
            class="progression-list shenlian-list"
          >
            <li
              v-for="gradient in visibleShenlianGradients"
              :key="`${gradient.pin}-${gradient.level}`"
              :class="{
                'is-active': gradient.active,
                'is-current': gradient.current,
              }"
            >
              <strong class="progression-level">{{ gradient.pin_label || `${gradient.pin}炼` }}</strong>
              <div class="progression-copy">
                <span v-if="gradient.skill_name" class="skill-name">{{ gradient.skill_name }}</span>
                <p v-if="gradient.summary_description">
                  <FanxiuGameRichText
                    :text="gradient.summary_description"
                    :segments="gradient.summary_segments"
                  />
                </p>
                <p v-if="gradient.effect_description">
                  <FanxiuGameRichText
                    :text="gradient.effect_description"
                    :segments="gradient.effect_segments"
                  />
                </p>
                <p v-if="!gradient.summary_description && !gradient.effect_description" class="muted">
                  {{ gradient.unlock_label || '暂无效果描述' }}
                </p>
              </div>
              <span class="node-level">Lv.{{ gradient.level }}</span>
            </li>
          </ol>
          <el-empty v-else description="该法宝暂未读取到神炼梯度" :image-size="64" />
        </section>
      </template>
      <el-empty v-else description="选择一件法宝查看效果" :image-size="72" />
    </main>
  </section>
</template>

<style scoped>
.magic-treasure-page {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  color: var(--el-text-color-primary);
}

.page-toolbar {
  flex: none;
  min-height: 54px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding: 8px 14px;
  border-bottom: 1px solid var(--el-border-color-light);
}

.heading,
.toolbar-actions,
.detail-heading {
  display: flex;
  align-items: center;
}

.heading {
  min-width: 0;
  gap: 10px;
}

h1,
h2,
h3,
p {
  margin: 0;
}

h1 {
  font-size: 18px;
  font-weight: 650;
  white-space: nowrap;
}

.count,
.updated,
.muted,
.schedule,
.gradient-level {
  color: var(--el-text-color-secondary);
}

.count,
.updated {
  font-size: 12px;
  white-space: nowrap;
}

.toolbar-actions {
  gap: 8px;
}

.search-input {
  width: 190px;
}

.list-pane {
  flex: none;
  min-height: 0;
  padding: 8px 14px 0;
}

.compact-table {
  width: max-content;
  min-width: fit-content;
}

.treasure-name {
  display: inline-block;
  max-width: 100%;
  padding: 2px 7px 2px 8px;
  overflow: hidden;
  color: var(--treasure-quality-color);
  background: color-mix(in srgb, var(--treasure-quality-color) 9%, transparent);
  border-radius: 2px;
  box-shadow: inset 2px 0 var(--treasure-quality-color);
  font-weight: 600;
  line-height: 1.35;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.list-pane :deep(.el-table__row) {
  cursor: pointer;
}

.list-pane :deep(.is-current-treasure td.el-table__cell) {
  background: var(--el-color-primary-light-9) !important;
}

.list-pane :deep(.is-unowned-treasure:not(.is-current-treasure) td.el-table__cell) {
  color: var(--el-text-color-secondary);
}

.shenlian-mixed-number {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-variant-numeric: tabular-nums;
  line-height: 1;
}

.mixed-number-whole {
  font-size: 13px;
}

.stacked-fraction {
  display: inline-grid;
  min-width: 15px;
  color: currentColor;
  font-size: 10px;
  line-height: 1;
  text-align: center;
}

.fraction-numerator {
  padding: 0 2px 1px;
  border-bottom: 1px solid currentColor;
}

.fraction-denominator {
  padding: 1px 2px 0;
}

.resize-handle {
  flex: none;
  height: 10px;
  display: grid;
  place-items: center;
  cursor: row-resize;
}

.resize-handle span {
  width: 44px;
  height: 3px;
  border-radius: 2px;
  background: var(--el-border-color);
}

.detail-pane {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 12px 18px 28px;
  border-top: 1px solid var(--el-border-color-light);
}

.detail-heading {
  align-items: baseline;
  flex-wrap: wrap;
  gap: 9px;
  margin-bottom: 8px;
}

.catalog-summary {
  margin: 0 0 14px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  font-size: 13px;
}

.catalog-link {
  color: var(--el-color-primary);
  font-size: 12px;
  font-weight: 400;
  text-decoration: none;
}

.catalog-summary p {
  max-width: 920px;
  color: var(--el-text-color-regular);
  line-height: 1.65;
  white-space: pre-wrap;
}

.catalog-quality {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  font-weight: 400;
}

.detail-heading h2 {
  font-size: 20px;
  font-weight: 650;
}

.progression-section {
  padding: 14px 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.progression-title {
  display: flex;
  align-items: baseline;
  gap: 9px;
  margin-bottom: 7px;
}

.progression-title h3 {
  font-size: 14px;
  color: #6f4b16;
}

.progression-title span {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.progression-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.progression-list li {
  display: grid;
  grid-template-columns: 62px minmax(0, 1fr) 54px;
  gap: 9px;
  align-items: start;
  padding: 7px 8px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  opacity: 0.48;
}

.progression-list li.is-active {
  background: color-mix(in srgb, #864c00 3%, transparent);
  opacity: 1;
}

.progression-list li.is-current {
  background: var(--el-color-warning-light-9);
  box-shadow: inset 3px 0 var(--el-color-warning);
  opacity: 1;
}

.node-level {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  white-space: nowrap;
}

.progression-level {
  color: #864c00;
  font-size: 12px;
  white-space: nowrap;
}

.upgrade-effect-list li {
  grid-template-columns: 62px minmax(0, 1fr);
}

.progression-list p {
  line-height: 1.65;
  white-space: pre-wrap;
}

.progression-copy p + p {
  margin-top: 3px;
}

.skill-name {
  display: inline-block;
  margin-bottom: 2px;
  color: var(--el-color-warning-dark-2);
  font-size: 13px;
}

.node-level {
  text-align: right;
}

@media (max-width: 900px) {
  .page-toolbar,
  .toolbar-actions {
    align-items: stretch;
  }

  .page-toolbar {
    flex-direction: column;
  }

  .toolbar-actions {
    width: 100%;
  }

  .search-input {
    flex: 1;
    width: auto;
  }

  .progression-list li,
  .upgrade-effect-list li {
    grid-template-columns: 54px minmax(0, 1fr);
  }

  .node-level {
    display: none;
  }
}
</style>
