<template>
  <div class="units">
    <h1>单位比喻</h1>

    <p class="intro">「1 纳米」到底有多小？「1 吨」到底有多重？这种问题，光看字面是答不上来的。</p>
    <p class="intro">
      所以这里换个法子：拿你熟悉的东西去比。
      <b>一扇门是 1 毫米的两千倍，就跟一个泳池是一枚硬币的两千倍一样。</b>
    </p>
    <p class="intro">每个参照物后面都标着它实际多大，不顺眼就换一个，别的格子会跟着自动算好。</p>

    <section v-for="system in SYSTEMS" :key="system.id" class="dimension">
      <h2>{{ system.title }}</h2>
      <ScaleBoard :system="system" />
    </section>
  </div>
</template>

<script setup lang="ts">
import ScaleBoard from '@/features/scale/ScaleBoard.vue'

import { areaSystem } from '@/utils/dimensions/area'
import { lengthSystem } from '@/utils/dimensions/length'
import { massSystem } from '@/utils/dimensions/mass'
import { speedSystem } from '@/utils/dimensions/speed'
import { timeSystem } from '@/utils/dimensions/time'
import { volumeSystem } from '@/utils/dimensions/volume'

const SYSTEMS = [lengthSystem, timeSystem, massSystem, areaSystem, volumeSystem, speedSystem]
</script>

<style scoped>
.units {
  padding: 24px 28px 48px;
  color: inherit;
  line-height: 1.6;
}

h1 {
  margin: 0 0 12px;
  font-size: 20px;
  font-weight: 600;
}

.intro {
  margin: 0 0 8px;
  font-size: 13px;
  line-height: 2;
  color: color-mix(in srgb, currentColor 62%, transparent);
}

.intro:last-of-type {
  margin-bottom: 4px;
}

.intro b {
  font-weight: 600;
  color: inherit;
}


.dimension {
  padding: 20px 0;
  border-top: 1px solid color-mix(in srgb, currentColor 12%, transparent);
}

.dimension:first-of-type {
  border-top: none;
}

h2 {
  margin: 0 0 12px;
  font-size: 15px;
  font-weight: 600;
  opacity: 0.75;
}
</style>
