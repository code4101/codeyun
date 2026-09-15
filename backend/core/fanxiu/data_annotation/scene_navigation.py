from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from pyxllib.autogui import SceneNavigator, View, image_number


def explicit_scene_jump_edges(
    tree: list[dict[str, Any]],
) -> dict[int, list[dict[str, Any]]]:
    """Build navigation edges only from explicitly declared Shape targets.

    Asset folders and image nesting are editorial structure, not navigation
    facts.  In particular, an empty ``返回``/``关闭`` target must not silently
    become an edge to a physical parent image or a same-named folder.
    """

    navigator = SceneNavigator(tree)
    edges: dict[int, list[dict[str, Any]]] = {}

    def visit(items: list[dict[str, Any]]) -> None:
        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("type") or "") == "image":
                source_id = image_number(item)
                if source_id is not None:
                    for shape_view in View(item).get_shapes(include_groups=False):
                        shape = shape_view.raw
                        target_text = navigator.jump_target_text(shape)
                        if not target_text or target_text in {"-1", "0"}:
                            continue
                        target_ids = navigator.scene_jump_target_ids(shape)
                        if not target_ids:
                            continue
                        edges.setdefault(int(source_id), []).append({
                            "source_id": int(source_id),
                            "image": item,
                            "shape": shape,
                            "target_ids": [int(scene_id) for scene_id in target_ids],
                        })
            children = item.get("children")
            if isinstance(children, list):
                visit([child for child in children if isinstance(child, dict)])

    visit(tree)
    return edges


def _landing_evidence(
    observed_counts: Mapping[int, int],
    declared_landing_ids: Iterable[int],
    alpha: float,
) -> tuple[dict[int, int], float]:
    """Return the per-landing evidence table and its diluted total.

    The frequency table is evidence, not truth.  One pseudo-observation is
    reserved for a still-unknown landing, so a single hit is at most 50% likely
    while ample evidence barely moves (315/560 → 56.1%).  Declared-but-never-
    observed landings stay at zero: an unproven destination earns no mass.
    """

    declared = {int(item) for item in declared_landing_ids}
    observed = {
        int(scene_id): max(0, int(count))
        for scene_id, count in observed_counts.items()
    }
    outcomes = declared | set(observed)
    if not outcomes:
        return {}, 0.0
    table = {scene_id: observed.get(scene_id, 0) for scene_id in outcomes}
    evidence = float(sum(observed.values())) + max(0.0, float(alpha))
    return table, evidence


def posterior_reachable_probability(
    observed_counts: Mapping[int, int],
    declared_landing_ids: Iterable[int],
    reachable_landing_ids: Iterable[int],
    *,
    alpha: float = 1.0,
    confidence_z: float = 0.0,
) -> float:
    """Estimate ``P(action eventually keeps a route to target)``."""

    probabilities = posterior_landing_probabilities(
        observed_counts,
        declared_landing_ids,
        alpha=alpha,
        confidence_z=confidence_z,
    )
    reachable = {int(item) for item in reachable_landing_ids}
    return sum(
        probability
        for scene_id, probability in probabilities.items()
        if scene_id in reachable
    )


def posterior_landing_probabilities(
    observed_counts: Mapping[int, int],
    declared_landing_ids: Iterable[int],
    *,
    alpha: float = 1.0,
    confidence_z: float = 0.0,
) -> dict[int, float]:
    """Return the diluted, optionally confidence-floored landing probabilities.

    Omitted probability mass belongs to one still-unknown landing, which is
    what keeps a single observation from looking like a proven route.  Passing
    ``confidence_z > 0`` additionally subtracts each landing's observation
    error (normal approximation), so diffuse controls — a return button that
    lands wherever the caller came from — collapse without being classified or
    special-cased by name.
    """

    table, evidence = _landing_evidence(
        observed_counts, declared_landing_ids, alpha,
    )
    if evidence <= 0:
        return {}
    probabilities = {
        scene_id: count / evidence for scene_id, count in table.items()
    }
    if confidence_z <= 0:
        return probabilities
    z = float(confidence_z)
    return {
        scene_id: max(
            0.0,
            probability
            - z * (max(0.0, probability * (1.0 - probability)) / evidence) ** 0.5,
        )
        for scene_id, probability in probabilities.items()
    }
