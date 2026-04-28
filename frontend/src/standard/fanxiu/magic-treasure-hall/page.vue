<script setup lang="ts">
import {
  type FanxiuInventoryItem,
  type FanxiuInventorySectionSnapshot,
  type FanxiuMagicTreasureCategory,
  type FanxiuMagicTreasureHallSnapshot,
  getFanxiuMagicTreasureHall,
  getFanxiuMagicTreasureNote,
  importFanxiuMagicTreasureFromOcr,
  saveFanxiuMagicTreasureHall,
  saveFanxiuMagicTreasureNote,
} from '@/api/fanxiu';
import InventoryHallPage from '../components/InventoryHallPage.vue';

const MERGED_SECTION_KEY = 'magic_treasure';
const MAGIC_TREASURE_CATEGORY_OPTIONS: FanxiuMagicTreasureCategory[] = ['法宝', '先天古宝', '后天古宝'];
const CATEGORY_TO_SECTION_KEY: Record<FanxiuMagicTreasureCategory, keyof FanxiuMagicTreasureHallSnapshot> = {
  法宝: 'fabao',
  先天古宝: 'xiantiangubao',
  后天古宝: 'houtiangubao',
};
const SECTION_KEY_TO_CATEGORY: Record<keyof FanxiuMagicTreasureHallSnapshot, FanxiuMagicTreasureCategory> = {
  fabao: '法宝',
  xiantiangubao: '先天古宝',
  houtiangubao: '后天古宝',
};
const CATEGORY_SELECTION_LABEL_MAP: Record<FanxiuMagicTreasureCategory, string> = {
  法宝: '法宝',
  先天古宝: '先天',
  后天古宝: '后天',
};

const sections = [
  { key: MERGED_SECTION_KEY, title: '法宝' },
];

function normalizeMagicTreasureCategory(value: unknown): FanxiuMagicTreasureCategory {
  const normalized = String(value || '').trim() as FanxiuMagicTreasureCategory;
  return MAGIC_TREASURE_CATEGORY_OPTIONS.includes(normalized) ? normalized : '法宝';
}

function attachCategory(
  items: FanxiuInventoryItem[] | undefined,
  category: FanxiuMagicTreasureCategory,
): FanxiuInventoryItem[] {
  return (items || []).map(item => ({
    ...item,
    category,
  }));
}

function mergeMagicTreasureSnapshot(
  snapshot: FanxiuMagicTreasureHallSnapshot,
): FanxiuInventorySectionSnapshot {
  return {
    [MERGED_SECTION_KEY]: [
      ...attachCategory(snapshot.fabao, '法宝'),
      ...attachCategory(snapshot.xiantiangubao, '先天古宝'),
      ...attachCategory(snapshot.houtiangubao, '后天古宝'),
    ],
  };
}

function splitMagicTreasureSnapshot(
  snapshot: FanxiuInventorySectionSnapshot,
): FanxiuMagicTreasureHallSnapshot {
  const mergedItems = snapshot[MERGED_SECTION_KEY] || [];
  const payload: FanxiuMagicTreasureHallSnapshot = {
    fabao: [],
    xiantiangubao: [],
    houtiangubao: [],
  };

  for (const item of mergedItems) {
    const category = normalizeMagicTreasureCategory(item.category);
    const sectionKey = CATEGORY_TO_SECTION_KEY[category];
    payload[sectionKey].push({
      ...item,
      category,
    });
  }

  return payload;
}

async function loadMergedMagicTreasureHall() {
  return mergeMagicTreasureSnapshot(await getFanxiuMagicTreasureHall());
}

async function saveMergedMagicTreasureHall(payload: FanxiuInventorySectionSnapshot) {
  const saved = await saveFanxiuMagicTreasureHall(splitMagicTreasureSnapshot(payload));
  return mergeMagicTreasureSnapshot(saved);
}

async function importImage(_sectionKey: string, image: File) {
  const response = await importFanxiuMagicTreasureFromOcr(CATEGORY_TO_SECTION_KEY['法宝'], image);
  return {
    ...response.item,
    category: SECTION_KEY_TO_CATEGORY.fabao,
  };
}
</script>

<template>
  <InventoryHallPage
    title="道具仓库 · 3 法宝"
    resource-label="法宝仓库"
    id-prefix="magic-treasure"
    draft-storage-key-prefix="fanxiu:magic-treasure-hall:merged"
    split-pane-storage-key="fanxiu:magic-treasure-hall:split-pane-height:v2"
    keepalive-path-prefix="/api/fanxiu/inventory/magic-treasure-notes"
    sort-mode="quality_rank_desc"
    category-column-label="分类"
    :category-column-width="82"
    :category-selection-label-map="CATEGORY_SELECTION_LABEL_MAP"
    :name-column-min-width="156"
    :type-column-width="82"
    :table-internal-scroll="true"
    :show-view-filters="true"
    :category-options="MAGIC_TREASURE_CATEGORY_OPTIONS"
    :sections="sections"
    :load-snapshot="loadMergedMagicTreasureHall"
    :save-snapshot="saveMergedMagicTreasureHall"
    :get-note="getFanxiuMagicTreasureNote"
    :save-note="saveFanxiuMagicTreasureNote"
    :import-image="importImage"
  />
</template>
