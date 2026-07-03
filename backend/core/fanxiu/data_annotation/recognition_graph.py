from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class SceneGraphCandidate:
    scene_id: int
    score: float
    matched: bool


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


def graph_specific_scene_ids(candidate_ids: Iterable[int], match_edges: Iterable[Mapping[str, Any] | tuple[int, int]]) -> tuple[int, ...]:
    """Return candidates that are not ancestors of another matched candidate.

    With ``match(a, b)`` represented as ``a -> b``, ``b`` is more specific than
    ``a`` for the current frame. If both ``a`` and ``b`` match the live frame,
    keep ``b`` and drop ``a``.
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

    less_specific: set[int] = set()
    for scene_id in candidates:
        queue: deque[int] = deque(outgoing.get(scene_id, ()))
        seen = {scene_id}
        while queue:
            target_id = queue.popleft()
            if target_id in seen:
                continue
            seen.add(target_id)
            if target_id in candidate_set:
                less_specific.add(scene_id)
                break
            queue.extend(outgoing.get(target_id, ()))
    return tuple(scene_id for scene_id in candidates if scene_id not in less_specific)


def choose_scene_from_graph(
    candidates: Iterable[SceneGraphCandidate],
    match_edges: Iterable[Mapping[str, Any] | tuple[int, int]],
    *,
    unknown_similarity_threshold: float = 5.0,
) -> SceneGraphRecognitionResult:
    """Choose a scene by the layer match -> graph disambiguation -> similarity flow."""

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
        specific_ids = graph_specific_scene_ids((item.scene_id for item in matched), match_edges)
        if len(specific_ids) == 1:
            winner = next(item for item in matched if item.scene_id == specific_ids[0])
            return SceneGraphRecognitionResult(
                scene_id=winner.scene_id,
                score=winner.score,
                status="graph_specific",
                matched_candidates=matched,
                unresolved_candidates=(),
                best_similarity_scene_id=winner.scene_id,
                best_similarity_score=winner.score,
            )
        if specific_ids:
            unresolved = tuple(int(item) for item in specific_ids)
            pool = [item for item in matched if item.scene_id in set(unresolved)]
        else:
            unresolved = tuple(item.scene_id for item in matched)
            pool = list(matched)
        best = max(pool, key=lambda item: item.score)
        same_best = [item for item in pool if abs(float(item.score) - float(best.score)) < 1e-9]
        if len(same_best) == 1:
            return SceneGraphRecognitionResult(
                scene_id=best.scene_id,
                score=best.score,
                status="similarity_tiebreak",
                matched_candidates=matched,
                unresolved_candidates=unresolved,
                best_similarity_scene_id=best.scene_id,
                best_similarity_score=best.score,
            )
        return SceneGraphRecognitionResult(
            scene_id=None,
            score=best.score,
            status="ambiguous",
            matched_candidates=matched,
            unresolved_candidates=unresolved,
            best_similarity_scene_id=best.scene_id,
            best_similarity_score=best.score,
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
    if float(best.score) < float(unknown_similarity_threshold):
        return SceneGraphRecognitionResult(
            scene_id=None,
            score=best.score,
            status="unknown",
            matched_candidates=(),
            unresolved_candidates=(),
            best_similarity_scene_id=best.scene_id,
            best_similarity_score=best.score,
        )
    return SceneGraphRecognitionResult(
        scene_id=best.scene_id,
        score=best.score,
        status="similarity_fallback",
        matched_candidates=(),
        unresolved_candidates=(),
        best_similarity_scene_id=best.scene_id,
        best_similarity_score=best.score,
    )
