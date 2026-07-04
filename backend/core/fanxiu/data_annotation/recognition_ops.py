from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Mapping

from backend.core.fanxiu.data_annotation.recognition_graph import normalize_match_edge


RECOGNITION_OPS_CATEGORIES: tuple[dict[str, str], ...] = (
    {"id": "mutual_match", "label": "互相匹配"},
    {"id": "multi_parent", "label": "多上游匹配"},
    {"id": "cycle_group", "label": "循环匹配组"},
)


def _scene_number(image: Mapping[str, Any]) -> int | None:
    for key in ("scene_id", "number", "image_id"):
        value = image.get(key)
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            pass
    filename = str(image.get("filename") or "")
    stem = filename.split(".", 1)[0]
    try:
        return int(stem)
    except (TypeError, ValueError):
        return None


def _scene_label(scene_id: int, images: Mapping[int, Mapping[str, Any]]) -> str:
    image = images.get(int(scene_id)) or {}
    title = str(image.get("title") or image.get("filename") or "").strip()
    return f"#{int(scene_id)} {title}".strip()


def _normalize_images(images: Mapping[Any, Any]) -> dict[int, Mapping[str, Any]]:
    result: dict[int, Mapping[str, Any]] = {}
    for key, image in images.items():
        if not isinstance(image, Mapping):
            continue
        try:
            scene_id = int(key)
        except (TypeError, ValueError):
            scene_id = _scene_number(image) or 0
        if scene_id > 0:
            result[int(scene_id)] = image
    return result


def _edge_payload(edge: Mapping[str, Any], source_id: int, target_id: int) -> dict[str, Any]:
    return {
        "source_id": int(source_id),
        "target_id": int(target_id),
        "score": edge.get("score"),
        "threshold": edge.get("threshold"),
        "matched": bool(edge.get("matched", True)),
    }


def _matrix_edges(matrix: Mapping[str, Any]) -> tuple[list[dict[str, Any]], int]:
    edges: list[dict[str, Any]] = []
    self_loop_count = 0
    raw_matches = matrix.get("matches") if isinstance(matrix.get("matches"), list) else []
    for item in raw_matches:
        if not isinstance(item, Mapping):
            continue
        normalized = normalize_match_edge(item)
        if normalized is None:
            try:
                source = int(str(item.get("s")).lstrip("#"))
                target = int(str(item.get("x")).lstrip("#"))
            except (TypeError, ValueError):
                continue
            if source == target:
                self_loop_count += 1
            continue
        source_id, target_id = normalized
        edges.append(_edge_payload(item, source_id, target_id))
    return edges, self_loop_count


def _strongly_connected_components(nodes: Iterable[int], outgoing: Mapping[int, set[int]]) -> list[list[int]]:
    index = 0
    stack: list[int] = []
    on_stack: set[int] = set()
    indices: dict[int, int] = {}
    lowlinks: dict[int, int] = {}
    result: list[list[int]] = []

    def visit(node: int) -> None:
        nonlocal index
        indices[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)

        for target in outgoing.get(node, set()):
            if target not in indices:
                visit(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[target])

        if lowlinks[node] != indices[node]:
            return
        component: list[int] = []
        while stack:
            target = stack.pop()
            on_stack.discard(target)
            component.append(target)
            if target == node:
                break
        result.append(sorted(component))

    for node in sorted(set(int(item) for item in nodes)):
        if node not in indices:
            visit(node)
    return result


def build_recognition_ops_report(matrix: Mapping[str, Any], images: Mapping[Any, Any]) -> dict[str, Any]:
    normalized_images = _normalize_images(images)
    scene_ids = [
        int(item)
        for item in matrix.get("scene_ids", [])
        if isinstance(item, int) or (isinstance(item, str) and item.isdigit())
    ]
    if not scene_ids:
        scene_ids = sorted(normalized_images)

    edges, self_loop_count = _matrix_edges(matrix)
    edge_set = {(int(edge["source_id"]), int(edge["target_id"])) for edge in edges}
    outgoing: dict[int, set[int]] = defaultdict(set)
    incoming: dict[int, set[int]] = defaultdict(set)
    edge_by_pair: dict[tuple[int, int], dict[str, Any]] = {}
    for edge in edges:
        source_id = int(edge["source_id"])
        target_id = int(edge["target_id"])
        outgoing[source_id].add(target_id)
        incoming[target_id].add(source_id)
        edge_by_pair[(source_id, target_id)] = edge

    issues: list[dict[str, Any]] = []

    for source_id, target_id in sorted(edge_set):
        if source_id > target_id or (target_id, source_id) not in edge_set:
            continue
        issue_edges = [edge_by_pair[(source_id, target_id)], edge_by_pair[(target_id, source_id)]]
        issues.append(
            {
                "id": f"mutual:{source_id}:{target_id}",
                "category": "mutual_match",
                "severity": "warning",
                "label": f"{_scene_label(source_id, normalized_images)} <-> {_scene_label(target_id, normalized_images)}",
                "node_ids": [source_id, target_id],
                "edges": issue_edges,
            }
        )

    for target_id in sorted(incoming):
        sources = sorted(incoming[target_id])
        if len(sources) <= 1:
            continue
        issue_edges = [edge_by_pair[(source_id, target_id)] for source_id in sources if (source_id, target_id) in edge_by_pair]
        issues.append(
            {
                "id": f"multi-parent:{target_id}",
                "category": "multi_parent",
                "severity": "warning",
                "label": f"{_scene_label(target_id, normalized_images)} <- {len(sources)}",
                "node_ids": [target_id, *sources],
                "edges": issue_edges,
            }
        )

    for component in _strongly_connected_components(scene_ids, outgoing):
        if len(component) <= 2:
            continue
        component_set = set(component)
        issue_edges = [
            edge
            for edge in edges
            if int(edge["source_id"]) in component_set and int(edge["target_id"]) in component_set
        ]
        issues.append(
            {
                "id": f"cycle:{'-'.join(str(item) for item in component)}",
                "category": "cycle_group",
                "severity": "error",
                "label": " -> ".join(_scene_label(item, normalized_images) for item in component),
                "node_ids": component,
                "edges": issue_edges,
            }
        )

    category_counts = {item["id"]: 0 for item in RECOGNITION_OPS_CATEGORIES}
    for issue in issues:
        category = str(issue.get("category") or "")
        category_counts[category] = category_counts.get(category, 0) + 1

    return {
        "matrix": {
            "cache_key": matrix.get("cache_key"),
            "cache_path": matrix.get("cache_path"),
            "cache_hit": bool(matrix.get("cache_hit")),
            "cache_missing": bool(matrix.get("cache_missing")),
            "cache_partial": bool(matrix.get("cache_partial")),
            "cache_stale": bool(matrix.get("cache_stale")),
            "score_mode": matrix.get("score_mode"),
            "layer": matrix.get("layer"),
            "threshold": matrix.get("threshold"),
            "updated_at": matrix.get("updated_at"),
            "node_count": len(scene_ids),
            "expected_node_count": matrix.get("expected_node_count"),
            "covered_node_count": len(scene_ids),
            "skipped_node_ids": matrix.get("skipped_node_ids") if isinstance(matrix.get("skipped_node_ids"), list) else [],
            "edge_count": len(edges),
            "ignored_self_loop_count": self_loop_count,
        },
        "categories": [
            {**category, "count": int(category_counts.get(category["id"], 0))}
            for category in RECOGNITION_OPS_CATEGORIES
        ],
        "edges": edges,
        "issues": issues,
        "summary": {
            "node_count": len(scene_ids),
            "edge_count": len(edges),
            "issue_count": len(issues),
            "category_counts": category_counts,
        },
    }
