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


def posterior_reachable_probability(
    observed_counts: Mapping[int, int],
    declared_landing_ids: Iterable[int],
    reachable_landing_ids: Iterable[int],
    *,
    alpha: float = 1.0,
) -> float:
    """Estimate ``P(action eventually keeps a route to target)``.

    A symmetric Dirichlet prior gives every declared/observed landing and one
    still-unknown landing the same initial mass.  The unknown bucket prevents
    an action with no observations from being treated as certainly reliable.
    """

    prior = max(1e-9, float(alpha))
    declared = {int(item) for item in declared_landing_ids}
    observed = {
        int(scene_id): max(0, int(count))
        for scene_id, count in observed_counts.items()
    }
    outcomes = declared | set(observed)
    reachable = {int(item) for item in reachable_landing_ids} & outcomes
    outcome_count_with_unknown = max(1, len(outcomes)) + 1
    observed_total = sum(observed.values())
    reachable_observed = sum(observed.get(scene_id, 0) for scene_id in reachable)
    reachable_prior = prior * len(reachable)
    denominator = observed_total + prior * outcome_count_with_unknown
    return (reachable_observed + reachable_prior) / denominator


def posterior_landing_probabilities(
    observed_counts: Mapping[int, int],
    declared_landing_ids: Iterable[int],
    *,
    alpha: float = 1.0,
) -> dict[int, float]:
    """Return the posterior probability of each known action landing.

    The omitted probability mass belongs to one still-unknown landing.  This
    keeps an unobserved declaration from looking deterministic while allowing
    callers to multiply probabilities along an actual navigation path.
    """

    prior = max(1e-9, float(alpha))
    declared = {int(item) for item in declared_landing_ids}
    observed = {
        int(scene_id): max(0, int(count))
        for scene_id, count in observed_counts.items()
    }
    outcomes = declared | set(observed)
    if not outcomes:
        return {}
    denominator = sum(observed.values()) + prior * (len(outcomes) + 1)
    return {
        scene_id: (observed.get(scene_id, 0) + prior) / denominator
        for scene_id in outcomes
    }
