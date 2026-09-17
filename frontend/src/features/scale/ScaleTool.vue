<template>
  <div class="scale-tool">
    <h1>{{ system.title }}</h1>
    <ScaleBoard :system="system" />
  </div>
</template>

<script setup lang="ts">
import type { ScaleSystem } from '@/utils/scale'

import ScaleBoard from '@/features/scale/ScaleBoard.vue'

defineProps<{ system: ScaleSystem }>()
</script>

<style scoped>
.scale-tool {
  padding: 24px 20px 48px;
  color: inherit;
  line-height: 1.6;
}

h1 {
  margin: 0 0 20px;
  font-size: 20px;
  font-weight: 600;
}
</style>
