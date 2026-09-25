/** Scene graph positioning. No page state, selections or navigation side effects. */
import type { ELK, ElkNode } from 'elkjs/lib/elk-api';

export const SCENE_GRAPH_NODE_WIDTH = 156;
export const SCENE_GRAPH_NODE_HEIGHT = 42;
let enginePromise: Promise<ELK> | undefined;
const getLayoutEngine = () => {
  if (!enginePromise) {
    enginePromise = import('elkjs/lib/elk.bundled.js')
      .then(({ default: Engine }) => new Engine())
      .catch((error) => { enginePromise = undefined; throw error; });
  }
  return enginePromise;
};

export const buildFallbackSceneGraphNodes = <T extends {
  position: { x: number; y: number };
  data?: { depth?: number; label?: string };
}>(baseNodes: readonly T[]): T[] => {
  const columns = new Map<number, T[]>();
  for (const node of baseNodes) {
    const depth = Number(node.data?.['depth'] ?? 0);
    const list = columns.get(depth) ?? [];
    list.push(node);
    columns.set(depth, list);
  }
  const maxDepth = Math.max(...[...columns.keys()], 0);
  return [...columns.entries()]
    .sort((left, right) => right[0] - left[0])
    .flatMap(([depth, columnNodes]) => (
      [...columnNodes]
        .sort((left, right) => String(left.data?.label ?? '').localeCompare(String(right.data?.label ?? ''), 'zh-Hans-CN'))
        .map((node, index) => ({
          ...node,
          position: {
            x: (maxDepth - depth) * 196 + 24,
            y: 88 + (index - (columnNodes.length - 1) / 2) * 62,
          },
        }))
    ));
};

/** Compute positions and routed sections while leaving caller-owned graph data intact. */
export async function layoutSceneGraph(
  baseNodes: readonly { id: string }[],
  baseEdges: readonly { id: string; source: string; target: string }[],
) {
  const elk = await getLayoutEngine();
  const graph: ElkNode = await elk.layout({
    id: 'scene-relation-graph',
    layoutOptions: {
      'elk.algorithm': 'layered',
      'elk.direction': 'RIGHT',
      'elk.edgeRouting': 'ORTHOGONAL',
      'elk.layered.spacing.nodeNodeBetweenLayers': '72',
      'elk.spacing.nodeNode': '36',
      'elk.layered.spacing.edgeNodeBetweenLayers': '28',
      'elk.layered.spacing.edgeEdgeBetweenLayers': '16',
      'elk.layered.crossingMinimization.strategy': 'LAYER_SWEEP',
      'elk.layered.cycleBreaking.strategy': 'GREEDY',
      'elk.layered.nodePlacement.strategy': 'BRANDES_KOEPF',
    },
    children: baseNodes.map((node) => ({
      id: node.id,
      width: SCENE_GRAPH_NODE_WIDTH,
      height: SCENE_GRAPH_NODE_HEIGHT,
    })),
    edges: baseEdges.map((edge) => ({
      id: edge.id,
      sources: [edge.source],
      targets: [edge.target],
    })),
  });
  const positionById = new Map((graph.children ?? []).map((node) => [
    node.id,
    { x: Number(node.x ?? 0), y: Number(node.y ?? 0) },
  ]));
  const sectionsByEdgeId = new Map((graph.edges ?? []).map((edge) => [
    edge.id,
    Array.isArray(edge.sections) ? edge.sections : [],
  ]));
  return { positionById, sectionsByEdgeId };
}
