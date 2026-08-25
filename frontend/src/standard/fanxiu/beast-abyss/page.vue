<script setup lang="ts">
import XutianPalacePage from '@/standard/fanxiu/xutian-palace/page.vue'
import type { FanxiuExchangeActivitySnapshot } from '@/api/fanxiu'

withDefaults(defineProps<{
  embedded?: boolean
  initialSnapshot?: FanxiuExchangeActivitySnapshot
}>(), {
  embedded: false,
  initialSnapshot: undefined,
})
</script>

<template>
  <XutianPalacePage
    activity-type="beast-abyss"
    activity-name="兽渊探秘"
    comparative-ranking-scope="team"
    comparative-ranking-title="团队排名"
    comparative-ranking-subject-label="团队"
    comparative-ranking-empty-text="尚未加载团队榜运行态数据"
    :embedded="embedded"
    :initial-snapshot="initialSnapshot"
  >
    <template #activity-type-control><slot name="activity-type-control" /></template>
  </XutianPalacePage>
</template>
