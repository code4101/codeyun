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
    activity-type="magic-invasion"
    activity-name="魔道入侵"
    :embedded="embedded"
    :initial-snapshot="initialSnapshot"
  >
    <template #activity-type-control><slot name="activity-type-control" /></template>
    <template #activity-strategy>
      <section class="magic-strategy">
        <h3>自动除魔</h3>
        <p>以目标功勋为停止条件；关闭长老以下敌人，开启长老及以上敌人与对应道具，从最高等级开始循环挑战。</p>
      </section>
    </template>
  </XutianPalacePage>
</template>

<style scoped>
.magic-strategy{display:flex;flex-direction:column;align-items:flex-start;gap:10px}.magic-strategy h3,.magic-strategy p{margin:0}
</style>
