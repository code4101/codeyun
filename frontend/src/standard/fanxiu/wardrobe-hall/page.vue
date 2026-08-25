<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { ElMessage } from 'element-plus';
import {
  collectFanxiuWardrobeHall,
  getFanxiuWardrobeHall,
  type FanxiuWardrobeHallSnapshot,
  type FanxiuWardrobeItem,
} from '@/api/fanxiu';
import { useResizablePane } from '@/utils/useResizablePane';
import FanxiuActivityUpdateButton from '../components/FanxiuActivityUpdateButton.vue';
import FanxiuRenderedText from '../FanxiuRenderedText.vue';
import { shouldAutoCollectInventorySnapshot } from '../components/inventorySnapshotStatus';

const snapshot = ref<FanxiuWardrobeHallSnapshot | null>(null);
const loading = ref(false);
const collecting = ref(false);
const autoCollectAttempted = ref(false);
const keyword = ref('');
const category = ref('');
const selectedId = ref('');

const sections = [
  { key: 'shizhuang', label: '时装' },
  { key: 'wuqi', label: '武器' },
  { key: 'huanshen', label: '环身' },
  { key: 'beishi', label: '背饰' },
  { key: 'yuqi', label: '御器' },
] as const;

const allItems = computed<FanxiuWardrobeItem[]>(() => {
  if (!snapshot.value) return [];
  return sections.flatMap(section => snapshot.value?.[section.key] || []);
});

const filteredItems = computed(() => {
  const query = keyword.value.trim().toLocaleLowerCase();
  return allItems.value
    .filter(item => !category.value || item.category === category.value)
    .filter(item => !query || [
      item.name,
      item.category,
      item.catalog_description,
      item.catalog_effect_description,
      item.main_use,
      item.acquisition,
    ].some(value => String(value || '').toLocaleLowerCase().includes(query)))
    .sort((left, right) => (
      Number(right.owned) - Number(left.owned)
      || Number(right.quality || 0) - Number(left.quality || 0)
      || Number(right.rank || 0) - Number(left.rank || 0)
      || left.name.localeCompare(right.name, 'zh-CN')
    ));
});

const selectedItem = computed(() => (
  allItems.value.find(item => item.id === selectedId.value) || filteredItems.value[0] || null
));

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
  storageKey: 'fanxiu:wardrobe-hall:list-pane-height:v2',
});

function selectFirstAvailable() {
  if (!filteredItems.value.some(item => item.id === selectedId.value)) {
    selectedId.value = filteredItems.value[0]?.id || '';
  }
}

async function loadSnapshot() {
  loading.value = true;
  try {
    snapshot.value = await getFanxiuWardrobeHall();
    selectFirstAvailable();
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '读取衣装数据失败');
  } finally {
    loading.value = false;
  }
  maybeAutoCollectFromGame();
}

async function collectFromGame(options: { automatic?: boolean } = {}) {
  if (collecting.value) return;
  collecting.value = true;
  try {
    snapshot.value = await collectFanxiuWardrobeHall();
    selectFirstAvailable();
    if (!options.automatic) {
      ElMessage.success(`已从游戏更新 ${snapshot.value.runtime_item_count} 件衣装，其中已拥有 ${snapshot.value.runtime_owned_count} 件`);
    }
  } catch (error: any) {
    if (!options.automatic) {
      ElMessage.error(error?.response?.data?.detail || error?.message || '从游戏更新衣装失败');
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

function rowClassName({ row }: { row: FanxiuWardrobeItem }) {
  return [
    row.id === selectedItem.value?.id ? 'is-current-item' : '',
    !row.owned ? 'is-unowned-item' : '',
  ].filter(Boolean).join(' ');
}

function qualityColor(item: FanxiuWardrobeItem) {
  const color = String(item.catalog_quality_color || '').replace(/^#/, '');
  return /^[0-9a-f]{6}$/i.test(color) ? `#${color}` : 'var(--el-text-color-regular)';
}

onMounted(loadSnapshot);
</script>

<template>
  <section class="wardrobe-page" v-loading="loading">
    <header class="page-toolbar">
      <div class="heading">
        <h1>衣装阁</h1>
        <span class="count">{{ allItems.length }} 件 / 已拥有 {{ snapshot?.runtime_owned_count || 0 }} 件</span>
        <span class="updated">数据：{{ updatedAtLabel }}</span>
      </div>
      <div class="toolbar-actions">
        <el-select v-model="category" clearable size="small" placeholder="全部分类" class="category-select" @change="selectFirstAvailable">
          <el-option v-for="section in sections" :key="section.key" :label="section.label" :value="section.label" />
        </el-select>
        <el-input v-model="keyword" clearable size="small" placeholder="搜索名称或效果" class="search-input" @input="selectFirstAvailable" />
        <FanxiuActivityUpdateButton :visible="true" :loading="collecting" :disabled="loading" @collect="collectFromGame" />
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
        <el-table-column prop="name" label="衣装" width="190" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="item-name" :style="{ '--quality-color': qualityColor(row) }">{{ row.name }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="category" label="分类" width="72" />
        <el-table-column label="状态" width="72">
          <template #default="{ row }">{{ row.owned ? '已拥有' : '未拥有' }}</template>
        </el-table-column>
        <el-table-column label="阶数" width="88" align="right">
          <template #default="{ row }">{{ row.rank }} / {{ row.max_level || '—' }}</template>
        </el-table-column>
      </el-table>
      <el-empty v-else description="数据库中还没有衣装数据，请从游戏更新" :image-size="72" />
    </div>

    <div class="resize-handle" title="拖动调整列表高度" @mousedown.prevent="startResizing"><span /></div>

    <main class="detail-pane">
      <template v-if="selectedItem">
        <div class="detail-heading">
          <h2>{{ selectedItem.name }}</h2>
          <span v-if="selectedItem.catalog_quality_name" :style="{ color: qualityColor(selectedItem) }">{{ selectedItem.catalog_quality_name }}</span>
          <span>{{ selectedItem.category }} · {{ selectedItem.owned ? `${selectedItem.rank}阶` : '未拥有' }}</span>
        </div>
        <section class="runtime-facts">
          <span>Fashion ID {{ selectedItem.fashion_id }}</span>
          <span>道具 ID {{ selectedItem.item_id || '—' }}</span>
          <span>最高 {{ selectedItem.max_level || '—' }} 阶</span>
          <span v-if="selectedItem.dress">当前穿戴</span>
        </section>
        <section v-if="selectedItem.catalog_description" class="effect-section">
          <h3>完整效果</h3>
          <FanxiuRenderedText
            :value="selectedItem.catalog_description"
            tone="light"
            compact
            preserve-colors
            :enable-links="false"
          />
        </section>
        <section v-if="selectedItem.catalog_effect_description" class="effect-section">
          <h3>使用效果</h3>
          <FanxiuRenderedText
            :value="selectedItem.catalog_effect_description"
            tone="light"
            compact
            preserve-colors
            :enable-links="false"
          />
        </section>
        <section v-if="selectedItem.main_use || selectedItem.acquisition" class="effect-section">
          <h3>已有说明</h3>
          <FanxiuRenderedText
            v-if="selectedItem.main_use"
            :value="selectedItem.main_use"
            tone="light"
            compact
            :enable-links="false"
          />
          <div v-if="selectedItem.acquisition" class="acquisition-row">
            <span>获取：</span>
            <FanxiuRenderedText
              :value="selectedItem.acquisition"
              tone="light"
              compact
              :enable-links="false"
            />
          </div>
        </section>
      </template>
      <el-empty v-else description="选择一件衣装查看效果" :image-size="72" />
    </main>
  </section>
</template>

<style scoped>
.wardrobe-page { height: 100%; min-height: 0; display: flex; flex-direction: column; overflow: hidden; color: var(--el-text-color-primary); }
.page-toolbar { flex: none; min-height: 54px; display: flex; align-items: center; justify-content: space-between; gap: 18px; padding: 8px 14px; border-bottom: 1px solid var(--el-border-color-light); }
.heading, .toolbar-actions, .detail-heading, .runtime-facts { display: flex; align-items: center; }
.heading { min-width: 0; gap: 10px; }
h1, h2, h3, p { margin: 0; }
h1 { font-size: 18px; font-weight: 650; white-space: nowrap; }
h2 { font-size: 20px; font-weight: 650; }
.count, .updated, .detail-heading span, .runtime-facts { color: var(--el-text-color-secondary); font-size: 12px; }
.toolbar-actions { gap: 8px; }
.category-select { width: 110px; }
.search-input { width: 190px; }
.list-pane { flex: none; min-height: 0; padding: 8px 14px 0; }
.compact-table { width: max-content; min-width: fit-content; }
.item-name { display: inline-block; max-width: 100%; padding: 2px 7px 2px 8px; overflow: hidden; color: var(--quality-color); background: color-mix(in srgb, var(--quality-color) 9%, transparent); border-radius: 2px; box-shadow: inset 2px 0 var(--quality-color); font-weight: 600; line-height: 1.35; text-overflow: ellipsis; white-space: nowrap; }
.list-pane :deep(.el-table__row) { cursor: pointer; }
.list-pane :deep(.is-current-item td.el-table__cell) { background: var(--el-color-primary-light-9) !important; }
.list-pane :deep(.is-unowned-item:not(.is-current-item) td.el-table__cell) { color: var(--el-text-color-secondary); }
.resize-handle { flex: none; height: 10px; display: grid; place-items: center; cursor: row-resize; }
.resize-handle span { width: 44px; height: 3px; border-radius: 2px; background: var(--el-border-color); }
.detail-pane { flex: 1; min-height: 0; overflow: auto; padding: 12px 18px 28px; border-top: 1px solid var(--el-border-color-light); }
.detail-heading { align-items: baseline; flex-wrap: wrap; gap: 9px; margin-bottom: 8px; }
.runtime-facts { flex-wrap: wrap; gap: 12px; padding-bottom: 10px; border-bottom: 1px solid var(--el-border-color-lighter); }
.effect-section { padding: 14px 0; border-bottom: 1px solid var(--el-border-color-lighter); }
.effect-section h3 { margin-bottom: 7px; color: #6f4b16; font-size: 14px; }
.effect-section :deep(.fanxiu-rendered-text) { max-width: 980px; }
.acquisition-row { display: flex; align-items: baseline; max-width: 980px; }
.acquisition-row > span { flex: none; }
@media (max-width: 900px) { .page-toolbar, .toolbar-actions { align-items: stretch; } .page-toolbar { flex-direction: column; } .toolbar-actions { width: 100%; } .search-input { flex: 1; width: auto; } }
</style>
