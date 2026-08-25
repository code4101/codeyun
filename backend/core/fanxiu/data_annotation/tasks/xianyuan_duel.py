from __future__ import annotations

import itertools
from typing import Any, Iterable

from backend.core.fanxiu.runtime_gui import (
    normalize_ocr_name,
    ocr_name_similarity,
)


def normalize_xianyuan_duel_name(value: Any) -> str:
    """Compatibility alias for the shared OCR-name normalizer."""

    return normalize_ocr_name(value)

def xianyuan_duel_name_similarity(left: Any, right: Any) -> float:
    """Compatibility alias for the shared OCR-name edit similarity."""

    return ocr_name_similarity(left, right)


def map_xianyuan_duel_targets_to_slots(
    targets: Iterable[dict[str, Any]],
    ocr_names: Iterable[str],
    *,
    minimum_pair_score: float = 0.35,
    minimum_assignment_margin: float = 0.08,
) -> dict[str, Any]:
    """Map three Runtime targets onto the three UI slots by global fuzzy assignment.

    OCR names are the primary identity evidence.  If OCR is too weak, the
    verified UI score ordering is an explicit fallback; equal scores remain
    ambiguous instead of being resolved by Runtime array order or power.
    """

    target_list = [dict(item) for item in targets]
    name_list = [str(item or "") for item in ocr_names]
    if len(target_list) != 3 or len(name_list) != 3:
        raise ValueError("仙缘斗法目标映射要求恰好 3 个 Runtime 目标和 3 个姓名区域")

    assignments: list[tuple[float, float, tuple[int, ...], list[float]]] = []
    for permutation in itertools.permutations(range(3)):
        scores = [
            xianyuan_duel_name_similarity(name_list[slot], target_list[target_index].get("name"))
            for slot, target_index in enumerate(permutation)
        ]
        assignments.append((sum(scores), min(scores), permutation, scores))
    assignments.sort(key=lambda item: (item[0], item[1]), reverse=True)
    best = assignments[0]
    second_total = assignments[1][0]
    if best[0] > 0:
        mapped: list[dict[str, Any]] = []
        for slot, target_index in enumerate(best[2], start=1):
            item = dict(target_list[target_index])
            item.update(
                {
                    "ui_slot": slot,
                    "challenge_shape": f"挑战{slot}",
                    "ocr_name": name_list[slot - 1],
                    "name_similarity": best[3][slot - 1],
                }
            )
            mapped.append(item)
        return {
            "ok": True,
            "method": (
                "fuzzy_name_assignment"
                if best[1] >= float(minimum_pair_score)
                else "highest_name_similarity_fallback"
            ),
            "assignment_score": round(best[0], 6),
            "assignment_margin": round(best[0] - second_total, 6),
            "targets": mapped,
        }

    scores = [item.get("score") for item in target_list]
    if any(not isinstance(score, int) for score in scores) or len(set(scores)) != 3:
        return {
            "ok": False,
            "method": "ambiguous",
            "reason": "姓名 OCR 匹配置信不足且候选积分并列，无法可靠映射 UI",
            "assignment_score": round(best[0], 6),
            "assignment_margin": round(best[0] - second_total, 6),
            "targets": [],
        }
    ordered = sorted(target_list, key=lambda item: int(item["score"]), reverse=True)
    mapped = []
    for slot, item in enumerate(ordered, start=1):
        row = dict(item)
        row.update(
            {
                "ui_slot": slot,
                "challenge_shape": f"挑战{slot}",
                "ocr_name": name_list[slot - 1],
                "name_similarity": xianyuan_duel_name_similarity(name_list[slot - 1], item.get("name")),
            }
        )
        mapped.append(row)
    return {
        "ok": True,
        "method": "score_order_fallback",
        "assignment_score": round(best[0], 6),
        "assignment_margin": round(best[0] - second_total, 6),
        "targets": mapped,
    }


def choose_xianyuan_duel_target(
    targets: Iterable[dict[str, Any]],
    *,
    self_power: int,
    allow_unbeatable_fallback: bool = False,
) -> dict[str, Any] | None:
    """Choose a target with soft ally protection and an optional forced fallback."""

    target_list = [dict(item) for item in targets]
    beatable = [
        item
        for item in target_list
        if isinstance(item.get("team_power"), int)
        and 0 < int(item["team_power"]) < int(self_power)
    ]
    if not beatable:
        if not allow_unbeatable_fallback:
            return None
        valid = [
            item
            for item in target_list
            if isinstance(item.get("team_power"), int) and int(item["team_power"]) > 0
        ]
        if not valid:
            return None
        chosen = min(
            valid,
            key=lambda item: (
                int(item["team_power"]),
                item.get("camp") == "friendly",
                -int(item.get("score") or 0),
            ),
        )
        chosen["selection_group"] = "lowest_power_forced"
        return chosen
    non_friendly = [item for item in beatable if item.get("camp") == "non_friendly"]
    pool = non_friendly or beatable
    chosen = max(pool, key=lambda item: int(item.get("score") or 0))
    chosen["selection_group"] = "non_friendly" if non_friendly else "friendly_fallback"
    return chosen
