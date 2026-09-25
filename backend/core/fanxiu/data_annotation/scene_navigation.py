from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pyxllib.autogui import SceneNavigator, View, image_number


@dataclass
class NavigationCycleTracker:
    """Track observed screen-state cycles within one navigation attempt.

    Each state key includes the recognized scene and a visual signature. A
    return to an already observed state proves that the path since that state
    did not reach the destination. The first action on that cycle is the
    candidate to reconsider; later exit actions may be perfectly valid.
    Only the caller decides when repeated evidence warrants exclusion.
    """

    path: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)
    cycle_counts: dict[tuple[str, tuple[Any, ...]], int] = field(default_factory=dict)
    last_landing_state: str = ""

    def observe(
        self,
        source_state: str,
        action_key: tuple[Any, ...],
        landing_state: str,
    ) -> tuple[tuple[Any, ...], int] | None:
        if not source_state or not landing_state:
            self.path.clear()
            self.last_landing_state = ""
            return None
        if self.last_landing_state and self.last_landing_state != source_state:
            self.path.clear()
        self.last_landing_state = landing_state
        self.path.append((source_state, action_key))
        for index, (state, first_action) in enumerate(self.path):
            if state != landing_state:
                continue
            cycle_key = (state, first_action)
            count = self.cycle_counts.get(cycle_key, 0) + 1
            self.cycle_counts[cycle_key] = count
            del self.path[index:]
            return first_action, count
        return None


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

    Declared destinations receive at least one effective observation. These
    pseudo-counts are calculation-only; real observations are never changed.
    ``alpha`` reserves mass for an unknown landing.
    """

    declared = {int(item) for item in declared_landing_ids}
    observed = {
        int(scene_id): max(0, int(count))
        for scene_id, count in observed_counts.items()
    }
    outcomes = declared | set(observed)
    if not outcomes:
        return {}, 0.0
    table = {scene_id: max(1 if scene_id in declared else 0, observed.get(scene_id, 0))
             for scene_id in outcomes}
    evidence = float(sum(table.values())) + max(0.0, float(alpha))
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
    """Compute landing weights with a minimum effective count of one per declaration.

    Real positive counts remain unchanged; declared zero/missing counts receive
    one calculation-only pseudo-count, including when other landings have been
    observed. Unknown landings reserve ``alpha`` mass. Observation uncertainty
    discounts only real observations, never the unobserved declaration prior.
    Inputs and persisted jump frequencies are never mutated.
    """

    declared_ids = list(dict.fromkeys(int(item) for item in declared_landing_ids))
    table, evidence = _landing_evidence(
        observed_counts, declared_ids, alpha,
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
        scene_id: probability if scene_id in declared_ids and observed_counts.get(scene_id, 0) <= 0 else max(
            0.0,
            probability
            - z * (max(0.0, probability * (1.0 - probability)) / evidence) ** 0.5,
        )
        for scene_id, probability in probabilities.items()
    }
