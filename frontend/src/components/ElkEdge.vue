<script setup lang="ts">
import { BaseEdge, getBezierPath, type EdgeProps } from '@vue-flow/core';
import { computed } from 'vue';

const props = defineProps<EdgeProps>();

const path = computed(() => {
  // 1. 如果有 ELK 计算的路由信息，使用它绘制折线
  if (props.data?.elkSections && props.data.elkSections.length > 0) {
    let d = '';
    props.data.elkSections.forEach((section: any) => {
      // Move to start point
      d += `M ${section.startPoint.x} ${section.startPoint.y} `;
      
      // Draw lines through bend points
      if (section.bendPoints) {
        section.bendPoints.forEach((bend: any) => {
          d += `L ${bend.x} ${bend.y} `;
        });
      }
      
      // Draw line to end point
      d += `L ${section.endPoint.x} ${section.endPoint.y} `;
    });
    return d;
  }

  // 2. 降级方案：如果没有 ELK 数据，使用默认的贝塞尔曲线
  // 这确保了在布局计算失败或未完成时，连线仍然可见
  return getBezierPath(props)[0];
});
</script>

<template>
  <BaseEdge 
    :path="path" 
    :marker-end="markerEnd" 
    :style="style" 
  />
</template>

<style scoped>
/* 可以添加 hover 效果等 */
</style>
