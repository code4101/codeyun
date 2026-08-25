from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from backend.core.fanxiu.data_annotation.tasks.penglai_xianzang import (
    XianzangSelectionCompletionResult,
    complete_xianzang_optional_reward_selection,
)
from backend.core.fanxiu.data_annotation.tasks.kunlun_secret_navigation import (
    KUNLUN_MAIN_SCENE_ID,
    KUNLUN_OPTIONAL_REWARD_SCENE_ID,
)


KUNLUN_OPTIONAL_REWARD_ROW_LABELS = {
    1: "珍宝奖励",
    2: "稀有奖励",
    3: "普通奖励",
}


class KunlunFirstRowUndecided(RuntimeError):
    """No evidence-backed first-row selection is available yet."""


@dataclass(frozen=True)
class KunlunFirstRowCandidate:
    column: int
    item_id: int
    name: str
    target_id: int | None


@dataclass(frozen=True)
class KunlunOwnedProgress:
    target_id: int
    name: str
    rank: int
    weight: int


@dataclass(frozen=True)
class KunlunFirstRowDecision:
    column: int
    reason: str


KunlunFirstRowSelector = Callable[
    [tuple[KunlunFirstRowCandidate, ...], tuple[KunlunOwnedProgress, ...]],
    KunlunFirstRowDecision | None,
]


def normalize_kunlun_first_row_candidates(
    items: Sequence[Mapping[str, Any]],
) -> tuple[KunlunFirstRowCandidate, ...]:
    candidates = tuple(
        KunlunFirstRowCandidate(
            column=index,
            item_id=int(item.get("item_id") or item.get("reward_item_id") or 0),
            name=str(item.get("name") or item.get("reward_name") or "").strip(),
            target_id=(
                int(item["target_id"])
                if item.get("target_id") not in (None, "")
                else None
            ),
        )
        for index, item in enumerate(items, start=1)
    )
    if len(candidates) != 4:
        raise KunlunFirstRowUndecided(
            f"昆仑秘藏第一排必须完整读取 4 个候选，实际 {len(candidates)} 个"
        )
    if any(candidate.item_id <= 0 or not candidate.name for candidate in candidates):
        raise KunlunFirstRowUndecided("昆仑秘藏第一排候选身份不完整")
    return candidates


def normalize_kunlun_owned_progress(
    items: Sequence[Mapping[str, Any]],
) -> tuple[KunlunOwnedProgress, ...]:
    progress = tuple(
        KunlunOwnedProgress(
            target_id=int(item.get("target_id") or item.get("id") or 0),
            name=str(item.get("name") or "").strip(),
            rank=int(item.get("rank") or 0),
            weight=int(item.get("weight") or 0),
        )
        for item in items
    )
    if not progress or any(
        item.target_id <= 0 or not item.name or item.rank < 0 or item.weight < 0
        for item in progress
    ):
        raise KunlunFirstRowUndecided("昆仑秘藏已有阶数/重数数据不完整")
    return progress


def decide_kunlun_first_row(
    reward_items: Sequence[Mapping[str, Any]],
    owned_items: Sequence[Mapping[str, Any]],
    *,
    selector: KunlunFirstRowSelector | None,
) -> KunlunFirstRowDecision:
    """Validate evidence and delegate policy; never invent a default choice."""

    candidates = normalize_kunlun_first_row_candidates(reward_items)
    progress = normalize_kunlun_owned_progress(owned_items)
    if selector is None:
        raise KunlunFirstRowUndecided("尚未注入昆仑秘藏第一排选择策略")
    decision = selector(candidates, progress)
    if decision is None:
        raise KunlunFirstRowUndecided("第一排策略没有给出安全决策")
    if int(decision.column) not in {1, 2, 3, 4} or not str(decision.reason).strip():
        raise KunlunFirstRowUndecided("第一排策略结果缺少有效列或决策依据")
    return decision


def complete_kunlun_optional_reward_selection(
    runtime: Any, decision: KunlunFirstRowDecision
) -> XianzangSelectionCompletionResult:
    """Reuse the identical three-row geometry after a validated first-row decision."""

    if int(decision.column) not in {1, 2, 3, 4} or not str(decision.reason).strip():
        raise KunlunFirstRowUndecided("拒绝用未验证的第一排决策打开或确认自选")

    return complete_xianzang_optional_reward_selection(
        runtime,
        int(decision.column),
        scene_id=KUNLUN_OPTIONAL_REWARD_SCENE_ID,
        expected_after_scene_ids=(KUNLUN_MAIN_SCENE_ID,),
        row_labels=KUNLUN_OPTIONAL_REWARD_ROW_LABELS,
        # Scrolling world announcements can cover these labels.  The stable
        # per-column green checks are the authoritative selection evidence.
        require_fraction_ocr=False,
        timeout_seconds=30.0,
    )
