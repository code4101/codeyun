import { Position, type Edge, type Node } from '@vue-flow/core';
import { getNoteWeightScaleFactor } from '@/utils/noteWeight';

type Point = { x: number; y: number };
type HandleSide = 't' | 'b' | 'l' | 'r';
export interface OrthogonalSegment {
  start: Point;
  end: Point;
}

interface Rect {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

interface RouteOptions {
  obstaclePadding?: number;
  portOffset?: number;
  bendPenalty?: number;
  hintPoints?: Point[];
  occupiedSegments?: OrthogonalSegment[];
  crossingPenalty?: number;
  overlapPenalty?: number;
  includeOccupiedCoordinates?: boolean;
}

const NODE_WIDTH = 150;
const NODE_HEIGHT = 50;
const DEFAULT_OBSTACLE_PADDING = 18;
const DEFAULT_PORT_OFFSET = 26;
const DEFAULT_BEND_PENALTY = 120;
const DEFAULT_CROSSING_PENALTY = 520;
const DEFAULT_OVERLAP_PENALTY = 60;
const EPSILON = 0.5;

const isFiniteNumber = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);

const roundKey = (value: number) => Math.round(value * 100) / 100;
const pointKey = (point: Point) => `${roundKey(point.x)}:${roundKey(point.y)}`;

const samePoint = (a: Point | undefined, b: Point | undefined) => {
  if (!a || !b) return false;
  return Math.abs(a.x - b.x) < EPSILON && Math.abs(a.y - b.y) < EPSILON;
};

const pushUniquePoint = (points: Point[], point: Point) => {
  const last = points[points.length - 1];
  if (!last || !samePoint(last, point)) {
    points.push(point);
  }
};

const compressPoints = (points: Point[]) => {
  if (points.length <= 2) return points;

  const result: Point[] = [];
  points.forEach(point => {
    pushUniquePoint(result, point);
    while (result.length >= 3) {
      const a = result[result.length - 3];
      const b = result[result.length - 2];
      const c = result[result.length - 1];
      const sameX = Math.abs(a.x - b.x) < EPSILON && Math.abs(b.x - c.x) < EPSILON;
      const sameY = Math.abs(a.y - b.y) < EPSILON && Math.abs(b.y - c.y) < EPSILON;
      if (!sameX && !sameY) break;
      result.splice(result.length - 2, 1);
    }
  });
  return result;
};

export const buildOrthogonalSegments = (points: Point[]): OrthogonalSegment[] => {
  const compactPoints = compressPoints(points);
  const segments: OrthogonalSegment[] = [];

  for (let index = 0; index < compactPoints.length - 1; index += 1) {
    const start = compactPoints[index];
    const end = compactPoints[index + 1];
    if (samePoint(start, end)) continue;
    segments.push({ start, end });
  }

  return segments;
};

const getNodeDimensions = (weight: number = 0, nodeType?: string | null) => {
  const scale = getNoteWeightScaleFactor(weight, nodeType);

  return {
    width: Math.round(NODE_WIDTH * scale),
    height: Math.round(NODE_HEIGHT * scale),
  };
};

const getSideFromHandle = (
  handleId: string | null | undefined,
  fallbackPosition: Position | undefined,
  kind: 'source' | 'target'
): HandleSide => {
  if (handleId?.startsWith('t-')) return 't';
  if (handleId?.startsWith('b-')) return 'b';
  if (handleId?.startsWith('l-')) return 'l';
  if (handleId?.startsWith('r-')) return 'r';

  if (fallbackPosition === Position.Top) return 't';
  if (fallbackPosition === Position.Bottom) return 'b';
  if (fallbackPosition === Position.Left) return 'l';
  if (fallbackPosition === Position.Right) return 'r';

  return kind === 'source' ? 'b' : 't';
};

const getHandleAnchorPoint = (node: Node, side: HandleSide): Point => {
  const { width, height } = getNodeDimensions(node.data?.weight, node.data?.node_type);
  const x = Number(node.position?.x) || 0;
  const y = Number(node.position?.y) || 0;

  switch (side) {
    case 't':
      return { x: x + width / 2, y };
    case 'b':
      return { x: x + width / 2, y: y + height };
    case 'l':
      return { x, y: y + height / 2 };
    case 'r':
      return { x: x + width, y: y + height / 2 };
    default:
      return { x: x + width / 2, y: y + height / 2 };
  }
};

const offsetPoint = (point: Point, side: HandleSide, distance: number): Point => {
  switch (side) {
    case 't':
      return { x: point.x, y: point.y - distance };
    case 'b':
      return { x: point.x, y: point.y + distance };
    case 'l':
      return { x: point.x - distance, y: point.y };
    case 'r':
      return { x: point.x + distance, y: point.y };
    default:
      return point;
  }
};

const buildObstacle = (node: Node, padding: number): Rect => {
  const { width, height } = getNodeDimensions(node.data?.weight, node.data?.node_type);
  const x = Number(node.position?.x) || 0;
  const y = Number(node.position?.y) || 0;

  return {
    left: x - padding,
    right: x + width + padding,
    top: y - padding,
    bottom: y + height + padding,
  };
};

const pointInsideObstacle = (point: Point, obstacle: Rect) =>
  point.x > obstacle.left + EPSILON &&
  point.x < obstacle.right - EPSILON &&
  point.y > obstacle.top + EPSILON &&
  point.y < obstacle.bottom - EPSILON;

const segmentCrossesObstacle = (start: Point, end: Point, obstacle: Rect) => {
  if (Math.abs(start.x - end.x) < EPSILON) {
    const x = start.x;
    if (x <= obstacle.left + EPSILON || x >= obstacle.right - EPSILON) {
      return false;
    }
    const minY = Math.min(start.y, end.y);
    const maxY = Math.max(start.y, end.y);
    return maxY > obstacle.top + EPSILON && minY < obstacle.bottom - EPSILON;
  }

  if (Math.abs(start.y - end.y) < EPSILON) {
    const y = start.y;
    if (y <= obstacle.top + EPSILON || y >= obstacle.bottom - EPSILON) {
      return false;
    }
    const minX = Math.min(start.x, end.x);
    const maxX = Math.max(start.x, end.x);
    return maxX > obstacle.left + EPSILON && minX < obstacle.right - EPSILON;
  }

  return true;
};

const uniqueSorted = (values: number[]) =>
  Array.from(new Set(values.filter(isFiniteNumber).map(roundKey))).sort((a, b) => a - b);

type Direction = 'up' | 'down' | 'left' | 'right' | 'none';

interface Neighbor {
  index: number;
  direction: Exclude<Direction, 'none'>;
  cost: number;
}

const getDirection = (from: Point, to: Point): Exclude<Direction, 'none'> => {
  if (Math.abs(from.x - to.x) < EPSILON) {
    return to.y >= from.y ? 'down' : 'up';
  }
  return to.x >= from.x ? 'right' : 'left';
};

const heuristic = (from: Point, to: Point) => Math.abs(from.x - to.x) + Math.abs(from.y - to.y);

const isVerticalSegment = (segment: OrthogonalSegment) => Math.abs(segment.start.x - segment.end.x) < EPSILON;
const isHorizontalSegment = (segment: OrthogonalSegment) => Math.abs(segment.start.y - segment.end.y) < EPSILON;

const rangeContainsInterior = (value: number, start: number, end: number) =>
  value > Math.min(start, end) + EPSILON && value < Math.max(start, end) - EPSILON;

const getOverlapLength = (startA: number, endA: number, startB: number, endB: number) =>
  Math.max(0, Math.min(Math.max(startA, endA), Math.max(startB, endB)) - Math.max(Math.min(startA, endA), Math.min(startB, endB)));

const getSegmentPenalty = (
  segment: OrthogonalSegment,
  occupiedSegments: OrthogonalSegment[],
  crossingPenalty: number,
  overlapPenalty: number
) => {
  let penalty = 0;

  occupiedSegments.forEach(occupiedSegment => {
    const segmentVertical = isVerticalSegment(segment);
    const occupiedVertical = isVerticalSegment(occupiedSegment);

    if (segmentVertical !== occupiedVertical) {
      const verticalSegment = segmentVertical ? segment : occupiedSegment;
      const horizontalSegment = segmentVertical ? occupiedSegment : segment;
      if (
        rangeContainsInterior(verticalSegment.start.x, horizontalSegment.start.x, horizontalSegment.end.x) &&
        rangeContainsInterior(horizontalSegment.start.y, verticalSegment.start.y, verticalSegment.end.y)
      ) {
        penalty += crossingPenalty;
      }
      return;
    }

    if (segmentVertical && occupiedVertical && Math.abs(segment.start.x - occupiedSegment.start.x) < EPSILON) {
      const overlapLength = getOverlapLength(segment.start.y, segment.end.y, occupiedSegment.start.y, occupiedSegment.end.y);
      if (overlapLength > EPSILON) {
        penalty += overlapPenalty;
      }
      return;
    }

    if (
      isHorizontalSegment(segment) &&
      isHorizontalSegment(occupiedSegment) &&
      Math.abs(segment.start.y - occupiedSegment.start.y) < EPSILON
    ) {
      const overlapLength = getOverlapLength(segment.start.x, segment.end.x, occupiedSegment.start.x, occupiedSegment.end.x);
      if (overlapLength > EPSILON) {
        penalty += overlapPenalty;
      }
    }
  });

  return penalty;
};

export const routeOrthogonalEdge = (
  nodeLookup: Map<string, Node>,
  edge: Edge,
  options: RouteOptions = {}
): Point[] | null => {
  const sourceNode = nodeLookup.get(String(edge.source));
  const targetNode = nodeLookup.get(String(edge.target));

  if (!sourceNode || !targetNode) {
    return null;
  }

  const obstaclePadding = options.obstaclePadding ?? DEFAULT_OBSTACLE_PADDING;
  const portOffset = options.portOffset ?? DEFAULT_PORT_OFFSET;
  const bendPenalty = options.bendPenalty ?? DEFAULT_BEND_PENALTY;
  const crossingPenalty = options.crossingPenalty ?? DEFAULT_CROSSING_PENALTY;
  const overlapPenalty = options.overlapPenalty ?? DEFAULT_OVERLAP_PENALTY;
  const includeOccupiedCoordinates = options.includeOccupiedCoordinates ?? true;

  const sourceSide = getSideFromHandle(edge.sourceHandle, sourceNode.sourcePosition, 'source');
  const targetSide = getSideFromHandle(edge.targetHandle, targetNode.targetPosition, 'target');
  const sourceAnchor = getHandleAnchorPoint(sourceNode, sourceSide);
  const targetAnchor = getHandleAnchorPoint(targetNode, targetSide);
  const sourceExit = offsetPoint(sourceAnchor, sourceSide, portOffset);
  const targetEntry = offsetPoint(targetAnchor, targetSide, portOffset);

  const obstacles = Array.from(nodeLookup.values()).map(node => buildObstacle(node, obstaclePadding));
  const hintPoints = options.hintPoints ?? [];
  const occupiedSegments = options.occupiedSegments ?? [];

  const candidateXs = uniqueSorted([
    sourceAnchor.x,
    sourceExit.x,
    targetEntry.x,
    targetAnchor.x,
    (sourceExit.x + targetEntry.x) / 2,
    ...obstacles.flatMap(obstacle => [obstacle.left, obstacle.right]),
    ...hintPoints.map(point => point.x),
    ...(includeOccupiedCoordinates ? occupiedSegments.flatMap(segment => [segment.start.x, segment.end.x]) : []),
  ]);
  const candidateYs = uniqueSorted([
    sourceAnchor.y,
    sourceExit.y,
    targetEntry.y,
    targetAnchor.y,
    (sourceExit.y + targetEntry.y) / 2,
    ...obstacles.flatMap(obstacle => [obstacle.top, obstacle.bottom]),
    ...hintPoints.map(point => point.y),
    ...(includeOccupiedCoordinates ? occupiedSegments.flatMap(segment => [segment.start.y, segment.end.y]) : []),
  ]);

  const points: Point[] = [];
  const pointIndexMap = new Map<string, number>();

  candidateXs.forEach(x => {
    candidateYs.forEach(y => {
      const point = { x, y };
      if (obstacles.some(obstacle => pointInsideObstacle(point, obstacle))) {
        return;
      }
      const key = pointKey(point);
      pointIndexMap.set(key, points.length);
      points.push(point);
    });
  });

  const startIndex = pointIndexMap.get(pointKey(sourceExit));
  const endIndex = pointIndexMap.get(pointKey(targetEntry));

  if (startIndex === undefined || endIndex === undefined) {
    return compressPoints([sourceAnchor, sourceExit, targetEntry, targetAnchor]);
  }

  const neighbors = new Map<number, Neighbor[]>();
  const addNeighbor = (fromIndex: number, toIndex: number) => {
    const from = points[fromIndex];
    const to = points[toIndex];
    if (obstacles.some(obstacle => segmentCrossesObstacle(from, to, obstacle))) {
      return;
    }
    const list = neighbors.get(fromIndex) ?? [];
    const segment = { start: from, end: to };
    list.push({
      index: toIndex,
      direction: getDirection(from, to),
      cost: heuristic(from, to) + getSegmentPenalty(segment, occupiedSegments, crossingPenalty, overlapPenalty),
    });
    neighbors.set(fromIndex, list);
  };

  candidateYs.forEach(y => {
    const row = candidateXs
      .map(x => pointIndexMap.get(pointKey({ x, y })))
      .filter((index): index is number => index !== undefined);
    for (let index = 0; index < row.length - 1; index += 1) {
      addNeighbor(row[index], row[index + 1]);
      addNeighbor(row[index + 1], row[index]);
    }
  });

  candidateXs.forEach(x => {
    const column = candidateYs
      .map(y => pointIndexMap.get(pointKey({ x, y })))
      .filter((index): index is number => index !== undefined);
    for (let index = 0; index < column.length - 1; index += 1) {
      addNeighbor(column[index], column[index + 1]);
      addNeighbor(column[index + 1], column[index]);
    }
  });

  const queue: Array<{ key: string; estimate: number }> = [];
  const dist = new Map<string, number>();
  const prev = new Map<string, { key: string | null; pointIndex: number; direction: Direction }>();
  const endStates: string[] = [];
  const pushState = (pointIndex: number, direction: Direction, previousKey: string | null, score: number) => {
    const key = `${pointIndex}:${direction}`;
    const current = dist.get(key);
    if (current !== undefined && current <= score) {
      return;
    }
    dist.set(key, score);
    prev.set(key, { key: previousKey, pointIndex, direction });
    queue.push({
      key,
      estimate: score + heuristic(points[pointIndex], points[endIndex]),
    });
  };

  pushState(startIndex, getDirection(sourceAnchor, sourceExit), null, 0);

  while (queue.length > 0) {
    queue.sort((a, b) => a.estimate - b.estimate);
    const current = queue.shift();
    if (!current) break;

    const currentScore = dist.get(current.key);
    if (currentScore === undefined) continue;

    const [pointIndexText, directionText] = current.key.split(':');
    const pointIndex = Number(pointIndexText);
    const direction = (directionText as Direction) ?? 'none';

    if (pointIndex === endIndex) {
      endStates.push(current.key);
      break;
    }

    const nextNeighbors = neighbors.get(pointIndex) ?? [];
    nextNeighbors.forEach(neighbor => {
      const bendCost = direction !== 'none' && direction !== neighbor.direction ? bendPenalty : 0;
      pushState(neighbor.index, neighbor.direction, current.key, currentScore + neighbor.cost + bendCost);
    });
  }

  const bestEndKey = endStates[0];
  if (!bestEndKey) {
    return compressPoints([sourceAnchor, sourceExit, targetEntry, targetAnchor]);
  }

  const routedPoints: Point[] = [];
  let cursor: string | null = bestEndKey;
  while (cursor) {
    const state = prev.get(cursor);
    if (!state) break;
    routedPoints.push(points[state.pointIndex]);
    cursor = state.key;
  }
  routedPoints.reverse();

  return compressPoints([
    sourceAnchor,
    sourceExit,
    ...routedPoints.slice(1, -1),
    targetEntry,
    targetAnchor,
  ]);
};
