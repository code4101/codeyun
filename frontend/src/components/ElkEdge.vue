<script setup lang="ts">
import { BaseEdge, getSmoothStepPath, type EdgeProps } from '@vue-flow/core';
import { computed, toRefs } from 'vue';

const props = defineProps<EdgeProps>();
const { markerEnd, style } = toRefs(props);

type Point = { x: number; y: number };

const isSamePoint = (a: Point | undefined, b: Point | undefined) => {
  if (!a || !b) return false;
  return Math.abs(a.x - b.x) < 0.5 && Math.abs(a.y - b.y) < 0.5;
};

const pushPoint = (points: Point[], point: Point) => {
  const last = points[points.length - 1];
  if (!last || !isSamePoint(last, point)) {
    points.push(point);
  }
};

const compressOrthogonalPoints = (points: Point[]) => {
  if (points.length <= 2) return points;

  const result: Point[] = [];
  points.forEach(point => {
    pushPoint(result, point);
    while (result.length >= 3) {
      const a = result[result.length - 3];
      const b = result[result.length - 2];
      const c = result[result.length - 1];
      const sameX = Math.abs(a.x - b.x) < 0.5 && Math.abs(b.x - c.x) < 0.5;
      const sameY = Math.abs(a.y - b.y) < 0.5 && Math.abs(b.y - c.y) < 0.5;
      if (!sameX && !sameY) break;
      result.splice(result.length - 2, 1);
    }
  });
  return result;
};

const flattenElkSections = (sections: any[]): Point[] => {
  const points: Point[] = [];
  sections.forEach((section: any) => {
    if (section.startPoint) {
      pushPoint(points, { x: section.startPoint.x, y: section.startPoint.y });
    }
    if (Array.isArray(section.bendPoints)) {
      section.bendPoints.forEach((bend: any) => {
        pushPoint(points, { x: bend.x, y: bend.y });
      });
    }
    if (section.endPoint) {
      pushPoint(points, { x: section.endPoint.x, y: section.endPoint.y });
    }
  });
  return points;
};

const pointsToPath = (points: Point[]) => {
  if (points.length === 0) return '';
  const [first, ...rest] = points;
  return `M ${first.x} ${first.y} ${rest.map(point => `L ${point.x} ${point.y}`).join(' ')}`.trim();
};

const path = computed(() => {
  const routePoints = Array.isArray(props.data?.routePoints) && props.data.routePoints.length > 1
    ? props.data.routePoints
    : Array.isArray(props.data?.elkSections) && props.data.elkSections.length > 0
      ? flattenElkSections(props.data.elkSections)
      : null;

  if (routePoints && routePoints.length > 1) {
    return pointsToPath(compressOrthogonalPoints(routePoints));
  }

  return getSmoothStepPath({
    sourceX: props.sourceX,
    sourceY: props.sourceY,
    sourcePosition: props.sourcePosition,
    targetX: props.targetX,
    targetY: props.targetY,
    targetPosition: props.targetPosition,
    borderRadius: 0
  })[0];
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
