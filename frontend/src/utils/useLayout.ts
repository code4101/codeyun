import ELK, { type ElkNode, type ElkPrimitiveEdge } from 'elkjs/lib/elk.bundled.js';
import { type Node, type Edge, Position } from '@vue-flow/core';

// Initialize ELK
const elk = new ELK();

// Handle Mapping
// CustomNode.vue defined handles:
// Top: t-t (target), t-s (source)
// Bottom: b-t (target), b-s (source)
// Left: l-t (target), l-s (source)
// Right: r-t (target), r-s (source)

const PORT_MAPPING: Record<string, string> = {
    't-t': 'NORTH', 't-s': 'NORTH',
    'b-t': 'SOUTH', 'b-s': 'SOUTH',
    'l-t': 'WEST', 'l-s': 'WEST',
    'r-t': 'EAST', 'r-s': 'EAST',
};

// Default node dimensions (should match CSS)
const NODE_WIDTH = 150;
const NODE_HEIGHT = 50;

/**
 * Calculate dimensions based on weight (area scaling)
 * @param weight default 100
 */
function getNodeDimensions(weight: number = 100) {
    // Area = Width * Height
    // New Area = Area * (weight / 100)
    // Scale Factor = sqrt(weight / 100)
    
    // Ensure minimum weight to avoid tiny nodes
    const safeWeight = Math.max(10, weight);
    const scale = Math.sqrt(safeWeight / 100);
    
    return {
        width: Math.round(NODE_WIDTH * scale),
        height: Math.round(NODE_HEIGHT * scale)
    };
}

/**
 * Use ELK to layout the graph
 * @param nodes Vue Flow Nodes
 * @param edges Vue Flow Edges
 * @returns Object containing laid out nodes and edges
 */
export const useLayout = async (nodes: Node[], edges: Edge[]) => {
    
    // 0. Pre-sort nodes by creation time
    // This gives ELK a hint for the "model order"
    const sortedNodes = [...nodes].sort((a, b) => {
        const timeA = a.data?.created_at || 0;
        const timeB = b.data?.created_at || 0;
        return timeA - timeB;
    });

    // 1. Transform to ELK Graph Structure
    const elkNodes: ElkNode[] = sortedNodes.map((node) => {
        const dimensions = getNodeDimensions(node.data?.weight);
        return {
            id: node.id,
            width: dimensions.width,
            height: dimensions.height,
            // Define ports for ELK to know where connections can happen
            ports: [
                { id: `${node.id}-p-north`, properties: { 'port.side': 'NORTH' } },
                { id: `${node.id}-p-south`, properties: { 'port.side': 'SOUTH' } },
                { id: `${node.id}-p-west`, properties: { 'port.side': 'WEST' } },
                { id: `${node.id}-p-east`, properties: { 'port.side': 'EAST' } },
            ],
            layoutOptions: {
                'portConstraints': 'FIXED_SIDE' // Force ports to stay on their assigned sides
            }
        };
    });

    const elkEdges: ElkPrimitiveEdge[] = edges.map((edge) => {
        return {
            id: edge.id,
            source: edge.source,
            target: edge.target,
        };
    });

    const graph: ElkNode = {
        id: 'root',
        layoutOptions: {
            'elk.algorithm': 'layered',
            'elk.direction': 'DOWN',
            'elk.spacing.nodeNode': '80', // Vertical spacing
            'elk.layered.spacing.nodeNodeBetweenLayers': '100', // Horizontal spacing
            'elk.edgeRouting': 'ORTHOGONAL', // Orthogonal lines
            'elk.layered.nodePlacement.strategy': 'BRANDES_KOEPF',
            // Allow edges to route around nodes to avoid crossing
            'elk.layered.crossingMinimization.strategy': 'LAYER_SWEEP',
            
            // --- Model Order Preference ---
            // In case of equal crossing counts, respect the order of nodes in the array
            'elk.layered.crossingMinimization.semiInteractive': 'true',
            'elk.layered.considerModelOrder.strategy': 'PREFER_NODES',
        },
        children: elkNodes,
        edges: elkEdges,
    };

    // 2. Run Layout
    try {
        const layoutedGraph = await elk.layout(graph);
        
        // 3. Apply positions back to Vue Flow Nodes
        const layoutedNodes = nodes.map((node) => {
            const elkNode = layoutedGraph.children?.find((n) => n.id === node.id);
            if (elkNode) {
                return {
                    ...node,
                    position: {
                        x: elkNode.x || 0,
                        y: elkNode.y || 0,
                    },
                };
            }
            return node;
        });
        
        // 4. Extract edge routing from ELK (Scheme A)
        // If ELK returns edge routing, use it. Otherwise fall back to smart connections.
        let layoutedEdges: Edge[] = [];

        // Check if ELK returned routed edges
        // Note: elkjs types might not fully reflect the runtime object, so we cast to any for safety accessing 'sections'
        const elkEdgesResult = layoutedGraph.edges as any[];

        if (elkEdgesResult && elkEdgesResult.length > 0) {
             layoutedEdges = edges.map((edge) => {
                const elkEdge = elkEdgesResult.find((e) => e.id === edge.id);
                
                if (elkEdge && elkEdge.sections && elkEdge.sections.length > 0) {
                    // ELK successfully routed this edge
                    const section = elkEdge.sections[0]; // Usually one section for simple edges
                    
                    // Helper to map ELK ports back to Vue Flow handles
                    const getHandleFromPort = (portId: string | undefined, type: 'source' | 'target') => {
                        if (!portId) return undefined;
                        // North -> t, South -> b, West -> l, East -> r
                        if (portId.includes('-p-north')) return type === 'source' ? 't-s' : 't-t';
                        if (portId.includes('-p-south')) return type === 'source' ? 'b-s' : 'b-t';
                        if (portId.includes('-p-west')) return type === 'source' ? 'l-s' : 'l-t';
                        if (portId.includes('-p-east')) return type === 'source' ? 'r-s' : 'r-t';
                        return undefined;
                    };

                    const sourceHandle = getHandleFromPort(section.startPoint?.incomingShape, 'source') || getHandleFromPort(elkEdge.sourcePort, 'source'); 
                    // Note: elkjs structure varies. Usually ports are on the node, but edge references them.
                    // Actually, ELK returns `sourcePort` and `targetPort` on the edge object if ports were used.
                    // But wait, the `sections` startPoint/endPoint might NOT have port info directly in all versions.
                    // Let's rely on generic heuristic or check if we can get it from the edge object.
                    
                    // Let's try to parse from the edge object first if available
                    // The section start/end points represent the absolute coordinates.
                    
                    // Correction: We defined ports on nodes. ELK should return which port was used.
                    // In elkjs, edge object often has `sourcePort` and `targetPort` properties matching the port IDs we defined.
                    
                    const srcPortId = elkEdge.sourcePort || (section.startPoint ? section.startPoint.port : undefined);
                    const tgtPortId = elkEdge.targetPort || (section.endPoint ? section.endPoint.port : undefined);

                    return {
                        ...edge,
                        // Update handles based on ELK's choice
                        sourceHandle: getHandleFromPort(srcPortId, 'source') || edge.sourceHandle,
                        targetHandle: getHandleFromPort(tgtPortId, 'target') || edge.targetHandle,
                        
                        // Pass routing points to custom edge component
                        data: {
                            ...edge.data,
                            elkSections: elkEdge.sections
                        }
                    };
                }
                
                return edge;
            });
        } else {
            // Fallback to old logic if no edges returned (should not happen if graph has edges)
            layoutedEdges = optimizeSmartConnections(layoutedNodes, edges);
        }

        return { nodes: layoutedNodes, edges: layoutedEdges };
        
    } catch (error) {
        console.error('ELK Layout Failed:', error);
        return { nodes, edges }; // Fallback to original
    }
};

/**
 * Helper to calculate handle position based on node position and side
 */
function getHandlePosition(node: Node, side: string) {
    const { x, y } = node.position;
    // Recalculate dimensions for handle positioning
    const { width, height } = getNodeDimensions(node.data?.weight);
    
    switch (side) {
        case 't': return { x: x + width / 2, y: y };      // Top
        case 'b': return { x: x + width / 2, y: y + height };  // Bottom
        case 'l': return { x: x, y: y + height / 2 };      // Left
        case 'r': return { x: x + width, y: y + height / 2 };  // Right
        default: return { x: x + width / 2, y: y };
    }
}

/**
 * Helper to calculate distance between two points
 */
function getDistance(p1: { x: number, y: number }, p2: { x: number, y: number }) {
    return Math.sqrt(Math.pow(p1.x - p2.x, 2) + Math.pow(p1.y - p2.y, 2));
}

/**
 * Optimizes edge connections by finding the shortest path handles
 * For bidirectional/multiple edges, it picks the next best shortest path
 */
function optimizeSmartConnections(nodes: Node[], edges: Edge[]): Edge[] {
    const nodeMap = new Map(nodes.map(n => [n.id, n]));
    const processedEdges: Edge[] = [];
    
    // Group edges by node pair (sorted ids to handle bidirectional)
    const pairMap = new Map<string, Edge[]>();
    
    edges.forEach(edge => {
        // Create a unique key for the pair of nodes (order independent)
        const pairId = [edge.source, edge.target].sort().join('__');
        if (!pairMap.has(pairId)) pairMap.set(pairId, []);
        pairMap.get(pairId)!.push(edge);
    });

    const sides = ['t', 'b', 'l', 'r'];

    pairMap.forEach((groupEdges, pairId) => {
        const [idA, idB] = pairId.split('__');
        const nodeA = nodeMap.get(idA);
        const nodeB = nodeMap.get(idB);

        // If nodes are missing, keep edges as is
        if (!nodeA || !nodeB) {
            processedEdges.push(...groupEdges);
            return;
        }

        // Calculate all 16 distances between sides of A and B
        const combinations = [];
        for (const sideA of sides) {
            for (const sideB of sides) {
                const posA = getHandlePosition(nodeA, sideA);
                const posB = getHandlePosition(nodeB, sideB);
                const dist = getDistance(posA, posB);
                combinations.push({ sideA, sideB, dist });
            }
        }

        // Sort by distance (shortest first)
        combinations.sort((a, b) => a.dist - b.dist);

        // Assign handles to edges
        groupEdges.forEach((edge, index) => {
            // Use the i-th best combination
            // If we have more edges than combinations (unlikely), wrap around
            const combo = combinations[index % combinations.length];
            
            // Determine which node is source/target in this specific edge
            if (edge.source === idA) {
                // A is Source, B is Target
                // Source Handle uses '-s', Target Handle uses '-t'
                edge.sourceHandle = `${combo.sideA}-s`;
                edge.targetHandle = `${combo.sideB}-t`;
            } else {
                // B is Source, A is Target
                edge.sourceHandle = `${combo.sideB}-s`;
                edge.targetHandle = `${combo.sideA}-t`;
            }
            
            processedEdges.push(edge);
        });
    });
    
    // Return edges that were not part of any group? 
    // No, pairMap covers all edges. But let's verify order if needed. 
    // The loop above iterates pairMap. To preserve original order, we might need a map lookup.
    // But Vue Flow usually doesn't care about edge order in the array.
    
    return processedEdges;
}
