<script setup lang="ts">
import {
  getFanxiuSpiritBeastHall,
  getFanxiuSpiritBeastNote,
  saveFanxiuSpiritBeastHall,
  saveFanxiuSpiritBeastNote,
} from '@/api/fanxiu';
import InventoryHallPage from '../components/InventoryHallPage.vue';

const sections = [
  { key: 'lingshou', title: '2.1 灵兽' },
  { key: 'shengshou', title: '2.2 圣兽' },
];
</script>

<template>
  <InventoryHallPage
    title="道具仓库 · 2 灵兽"
    resource-label="灵兽仓库"
    id-prefix="spirit-beast"
    draft-storage-key-prefix="fanxiu:spirit-beast-hall"
    split-pane-storage-key="fanxiu:spirit-beast-hall:split-pane-height"
    keepalive-path-prefix="/api/fanxiu/inventory/spirit-beast-notes"
    :sections="sections"
    :load-snapshot="getFanxiuSpiritBeastHall"
    :save-snapshot="saveFanxiuSpiritBeastHall"
    :get-note="getFanxiuSpiritBeastNote"
    :save-note="saveFanxiuSpiritBeastNote"
  />
</template>
