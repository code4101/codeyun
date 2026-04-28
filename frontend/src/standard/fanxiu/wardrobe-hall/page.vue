<script setup lang="ts">
import {
  getFanxiuWardrobeHall,
  getFanxiuWardrobeNote,
  saveFanxiuWardrobeHall,
  saveFanxiuWardrobeNote,
} from '@/api/fanxiu';
import InventoryHallPage from '../components/InventoryHallPage.vue';

const sections = [
  { key: 'shizhuang', title: '1.1 时装' },
  { key: 'wuqi', title: '1.2 武器' },
  { key: 'huanshen', title: '1.3 环身' },
  { key: 'beishi', title: '1.4 背饰' },
  { key: 'yuqi', title: '1.5 御器' },
];
</script>

<template>
  <InventoryHallPage
    title="道具仓库 · 1 衣装阁"
    resource-label="衣装阁"
    id-prefix="wardrobe"
    draft-storage-key-prefix="fanxiu:wardrobe-hall"
    split-pane-storage-key="fanxiu:wardrobe-hall:split-pane-height"
    keepalive-path-prefix="/api/fanxiu/inventory/wardrobe-notes"
    :sections="sections"
    :load-snapshot="getFanxiuWardrobeHall"
    :save-snapshot="saveFanxiuWardrobeHall"
    :get-note="getFanxiuWardrobeNote"
    :save-note="saveFanxiuWardrobeNote"
  />
</template>
