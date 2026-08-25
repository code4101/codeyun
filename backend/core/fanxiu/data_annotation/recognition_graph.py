from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class SceneGraphCandidate:
    scene_id: int
    score: float
    matched: bool
    frame_similarity: float | None = None


@dataclass(frozen=True)
class SceneGraphRecognitionResult:
    scene_id: int | None
    score: float
    status: str
    matched_candidates: tuple[SceneGraphCandidate, ...]
    unresolved_candidates: tuple[int, ...]
    best_similarity_scene_id: int | None = None
    best_similarity_score: float = 0.0


def normalize_match_edge(edge: Mapping[str, Any]) -> tuple[int, int] | None:
    """Return ``(reference, fact)`` for a directed ``match(s, x)`` edge."""

    if "s" in edge:
        source = edge.get("s")
        target = edge.get("x")
    elif "reference" in edge:
        source = edge.get("reference")
        target = edge.get("frame")
    else:
        # Legacy debug payloads used x=source frame and y=reference scene.
        source = edge.get("y")
        target = edge.get("x")
    try:
        source_id = int(str(source).lstrip("#"))
        target_id = int(str(target).lstrip("#"))
    except (TypeError, ValueError):
        return None
    if source_id == target_id:
        return None
    return source_id, target_id


def graph_nearest_scene_ids(candidate_ids: Iterable[int], match_edges: Iterable[Mapping[str, Any] | tuple[int, int]]) -> tuple[int, ...]:
    """Return the scene nodes nearest to the current fact in the match subgraph.

    ``match(s, x)`` is the directed edge ``s -> x``.  Every candidate passed to
    this function already matches the current fact.  An edge to the fact is
    redundant when that candidate reaches another matched candidate first, so
    only terminal strongly-connected components are adjacent to the fact after
    redundant edges are removed.  Their members therefore share the minimum
    graph distance to the current fact.
    """

    candidates = tuple(dict.fromkeys(int(item) for item in candidate_ids))
    candidate_set = set(candidates)
    outgoing: dict[int, set[int]] = defaultdict(set)
    for edge in match_edges:
        if isinstance(edge, tuple):
            normalized = edge
        else:
            normalized = normalize_match_edge(edge)
        if normalized is None:
            continue
        source_id, target_id = int(normalized[0]), int(normalized[1])
        if source_id in candidate_set and target_id in candidate_set:
            outgoing[source_id].add(target_id)

    index = 0
    stack: list[int] = []
    on_stack: set[int] = set()
    indices: dict[int, int] = {}
    lowlinks: dict[int, int] = {}
    components: list[tuple[int, ...]] = []

    def visit(scene_id: int) -> None:
        nonlocal index
        indices[scene_id] = index
        lowlinks[scene_id] = index
        index += 1
        stack.append(scene_id)
        on_stack.add(scene_id)
        for target_id in outgoing.get(scene_id, set()):
            if target_id not in indices:
                visit(target_id)
                lowlinks[scene_id] = min(lowlinks[scene_id], lowlinks[target_id])
            elif target_id in on_stack:
                lowlinks[scene_id] = min(lowlinks[scene_id], indices[target_id])
        if lowlinks[scene_id] != indices[scene_id]:
            return
        component: list[int] = []
        while stack:
            node = stack.pop()
            on_stack.discard(node)
            component.append(node)
            if node == scene_id:
                break
        components.append(tuple(component))

    for scene_id in candidates:
        if scene_id not in indices:
            visit(scene_id)

    component_by_scene = {
        scene_id: component_index
        for component_index, component in enumerate(components)
        for scene_id in component
    }
    terminal_components = set(range(len(components)))
    for source_id, targets in outgoing.items():
        source_component = component_by_scene[source_id]
        if any(component_by_scene[target_id] != source_component for target_id in targets):
            terminal_components.discard(source_component)
    return tuple(
        scene_id
        for scene_id in candidates
        if component_by_scene[scene_id] in terminal_components
    )


def choose_scene_from_graph(
    candidates: Iterable[SceneGraphCandidate],
    match_edges: Iterable[Mapping[str, Any] | tuple[int, int]],
) -> SceneGraphRecognitionResult:
    """Choose a scene from valid matches and graph relations.

    Similarity below a scene's match threshold is diagnostic evidence only; it
    must never be promoted into a scene result.
    """

    candidate_tuple = tuple(candidates)
    matched = tuple(item for item in candidate_tuple if item.matched)
    if len(matched) == 1:
        item = matched[0]
        return SceneGraphRecognitionResult(
            scene_id=item.scene_id,
            score=item.score,
            status="matched",
            matched_candidates=matched,
            unresolved_candidates=(),
            best_similarity_scene_id=item.scene_id,
            best_similarity_score=item.score,
        )

    if len(matched) > 1:
        # 核心设计：多个场景即使都命中 100%，也不能按分数或候选顺序选。
        # ``s -> x`` 表示场景 s 的身份条件能够匹配事实帧 x；因此它同时
        # 表达了“s 比 x 更宽泛”。例如 427 -> 428 -> 429 且
        # 427 -> 429 时，三者对当前帧都可能是 100%，但唯一终点 429
        # 才是最具体的事实。这里先压缩双向可达的强连通分量，再只保留
        # 没有指向其它分量的终端节点。若终端不唯一，必须继续消歧或返回
        # ambiguous，绝不能让遍历顺序制造一个看似确定的结果。
        nearest_ids = graph_nearest_scene_ids((item.scene_id for item in matched), match_edges)
        if len(nearest_ids) == 1:
            winner = next(item for item in matched if item.scene_id == nearest_ids[0])
            return SceneGraphRecognitionResult(
                scene_id=winner.scene_id,
                score=winner.score,
                status="graph_nearest",
                matched_candidates=matched,
                unresolved_candidates=(),
                best_similarity_scene_id=winner.scene_id,
                best_similarity_score=winner.score,
            )
        if nearest_ids:
            unresolved = tuple(int(item) for item in nearest_ids)
            pool = [item for item in matched if item.scene_id in set(unresolved)]
        else:
            unresolved = tuple(item.scene_id for item in matched)
            pool = list(matched)
        comparable = [item for item in pool if item.frame_similarity is not None]
        if not comparable:
            best = max(pool, key=lambda item: item.score)
            return SceneGraphRecognitionResult(
                scene_id=None,
                score=best.score,
                status="ambiguous",
                matched_candidates=matched,
                unresolved_candidates=unresolved,
                best_similarity_scene_id=None,
                best_similarity_score=0.0,
            )
        best = max(comparable, key=lambda item: float(item.frame_similarity or 0.0))
        same_best = [
            item for item in comparable
            if abs(float(item.frame_similarity or 0.0) - float(best.frame_similarity or 0.0)) < 1e-9
        ]
        if len(same_best) == 1:
            return SceneGraphRecognitionResult(
                scene_id=best.scene_id,
                score=best.score,
                status="similarity_tiebreak",
                matched_candidates=matched,
                unresolved_candidates=unresolved,
                best_similarity_scene_id=best.scene_id,
                best_similarity_score=float(best.frame_similarity or 0.0),
            )
        return SceneGraphRecognitionResult(
            scene_id=None,
            score=best.score,
            status="ambiguous",
            matched_candidates=matched,
            unresolved_candidates=unresolved,
            best_similarity_scene_id=best.scene_id,
            best_similarity_score=float(best.frame_similarity or 0.0),
        )

    if not candidate_tuple:
        return SceneGraphRecognitionResult(
            scene_id=None,
            score=0.0,
            status="no_candidates",
            matched_candidates=(),
            unresolved_candidates=(),
        )

    best = max(candidate_tuple, key=lambda item: item.score)
    return SceneGraphRecognitionResult(
        scene_id=None,
        score=best.score,
        status="unknown",
        matched_candidates=(),
        unresolved_candidates=(),
        best_similarity_scene_id=best.scene_id,
        best_similarity_score=best.score,
    )
