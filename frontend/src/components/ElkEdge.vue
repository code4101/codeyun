<script setup lang="ts">
import { BaseEdge, EdgeLabelRenderer, getSmoothStepPath, type EdgeProps } from '@vue-flow/core';
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

const edgeLabel = computed(() => String(props.label ?? '').trim());

const routePoints = computed<Point[] | null>(() => {
  const rawPoints = Array.isArray(props.data?.routePoints) && props.data.routePoints.length > 1
    ? props.data.routePoints
    : Array.isArray(props.data?.elkSections) && props.data.elkSections.length > 0
      ? flattenElkSections(props.data.elkSections)
      : null;

  if (!rawPoints || rawPoints.length <= 1) return null;
  return compressOrthogonalPoints(rawPoints);
});

const smoothStepPath = computed(() => getSmoothStepPath({
  sourceX: props.sourceX,
  sourceY: props.sourceY,
  sourcePosition: props.sourcePosition,
  targetX: props.targetX,
  targetY: props.targetY,
  targetPosition: props.targetPosition,
  borderRadius: 0
}));

const midpointOfPolyline = (points: Point[]) => {
  let totalLength = 0;
  const segments = points.slice(0, -1).map((point, index) => {
    const next = points[index + 1];
    const length = Math.hypot(next.x - point.x, next.y - point.y);
    totalLength += length;
    return { point, next, length };
  });

  let walked = 0;
  const targetLength = totalLength / 2;
  for (const segment of segments) {
    if (walked + segment.length >= targetLength) {
      const ratio = segment.length > 0 ? (targetLength - walked) / segment.length : 0;
      return {
        x: segment.point.x + (segment.next.x - segment.point.x) * ratio,
        y: segment.point.y + (segment.next.y - segment.point.y) * ratio,
      };
    }
    walked += segment.length;
  }
  return points[Math.floor(points.length / 2)] ?? { x: props.sourceX, y: props.sourceY };
};

const path = computed(() => {
  if (routePoints.value && routePoints.value.length > 1) {
    return pointsToPath(routePoints.value);
  }
  return smoothStepPath.value[0];
});

const labelPosition = computed(() => {
  if (routePoints.value && routePoints.value.length > 1) {
    return midpointOfPolyline(routePoints.value);
  }
  return { x: smoothStepPath.value[1], y: smoothStepPath.value[2] };
});
</script>

<template>
  <BaseEdge 
    :path="path" 
    :marker-end="markerEnd" 
    :style="style" 
  />
  <EdgeLabelRenderer v-if="edgeLabel">
    <div
      class="elk-edge-label"
      :style="{
        transform: `translate(-50%, -50%) translate(${labelPosition.x}px, ${labelPosition.y}px)`,
      }"
    >
      {{ edgeLabel }}
    </div>
  </EdgeLabelRenderer>
</template>

<style scoped>
.elk-edge-label {
  position: absolute;
  padding: 1px 5px;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.92);
  color: #303133;
  font-size: 11px;
  line-height: 16px;
  pointer-events: none;
  transform-origin: center;
  white-space: nowrap;
}
</style>
