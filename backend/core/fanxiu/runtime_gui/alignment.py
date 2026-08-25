from __future__ import annotations

"""Align authoritative game Runtime entities with noisy GUI observations.

The game Runtime answers *what the entity is* and whether it is actionable.
The GUI answers *where it is currently rendered*.  OCR is therefore evidence
for alignment, not the business source of truth.
"""

import itertools
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable, Literal, Mapping, Sequence

from backend.core.fanxiu.runtime_gui.text import ocr_name_similarity


AlignmentStatus = Literal["aligned", "incomplete_runtime", "insufficient", "ambiguous", "too_large"]


@dataclass(frozen=True)
class RuntimeEntity:
    """One authoritative entity read from the game Runtime or an equivalent snapshot."""

    key: str
    name: str
    aliases: tuple[str, ...] = ()
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GuiCandidate:
    """One visible GUI candidate with optional OCR and action geometry."""

    key: str
    text: str = ""
    slot: int | None = None
    point: tuple[float, float] | None = None
    box: tuple[float, float, float, float] | None = None
    reliability: float = 1.0
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeGuiPairEvidence:
    runtime_key: str
    gui_key: str
    runtime_name: str
    gui_text: str
    matched_alias: str
    text_score: float
    context_score: float | None
    reliability: float
    score: float


@dataclass(frozen=True)
class RuntimeGuiMapping:
    entity: RuntimeEntity
    candidate: GuiCandidate
    evidence: RuntimeGuiPairEvidence


@dataclass(frozen=True)
class RuntimeGuiAlignment:
    status: AlignmentStatus
    reason: str
    mappings: tuple[RuntimeGuiMapping, ...] = ()
    best_score: float = 0.0
    second_score: float = 0.0
    score_margin: float = 0.0
    minimum_pair_score: float = 0.0
    runtime_fingerprint: str = ""
    gui_fingerprint: str = ""

    @property
    def aligned(self) -> bool:
        return self.status == "aligned"

    def model_dump(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["aligned"] = self.aligned
        return payload


@dataclass(frozen=True)
class RuntimeEvidenceValidation:
    ok: bool
    reason: str
    complete: bool
    age_seconds: float | None
    pid: int | None
    process_start_ticks: int | None
    fingerprint: str = ""


def validate_runtime_evidence(
    snapshot: Mapping[str, Any],
    *,
    max_age_seconds: float | None = None,
    now_epoch: float | None = None,
    require_process_identity: bool = True,
) -> RuntimeEvidenceValidation:
    """Validate freshness and process identity before Runtime facts authorize GUI work."""

    evidence = snapshot.get("evidence") if isinstance(snapshot.get("evidence"), Mapping) else {}
    complete = snapshot.get("complete") is True
    pid_raw = evidence.get("pid") if evidence else snapshot.get("pid")
    ticks_raw = evidence.get("process_start_ticks") if evidence else snapshot.get("process_start_ticks")
    try:
        pid = int(pid_raw) if pid_raw is not None else None
    except (TypeError, ValueError):
        pid = None
    try:
        ticks = int(ticks_raw) if ticks_raw is not None else None
    except (TypeError, ValueError):
        ticks = None
    captured_raw = snapshot.get("captured_at_epoch")
    if captured_raw is None and evidence:
        captured_raw = evidence.get("captured_at_epoch")
    try:
        captured_at = float(captured_raw) if captured_raw is not None else None
    except (TypeError, ValueError):
        captured_at = None
    age = None if captured_at is None else abs(float(now_epoch if now_epoch is not None else time.time()) - captured_at)
    fingerprint = str(
        snapshot.get("fingerprint")
        or snapshot.get("sequence_fingerprint")
        or (evidence.get("fingerprint") if evidence else "")
        or ""
    )
    if not complete:
        reason = "游戏 Runtime 快照不完整"
    elif require_process_identity and (pid is None or ticks is None):
        reason = "游戏 Runtime 快照缺少进程身份"
    elif max_age_seconds is not None and (age is None or age > float(max_age_seconds)):
        reason = "游戏 Runtime 快照缺少时间或已经过期"
    else:
        reason = "游戏 Runtime 证据完整且在有效期内"
    return RuntimeEvidenceValidation(
        ok=reason == "游戏 Runtime 证据完整且在有效期内",
        reason=reason,
        complete=complete,
        age_seconds=round(age, 6) if age is not None else None,
        pid=pid,
        process_start_ticks=ticks,
        fingerprint=fingerprint,
    )


PairContextScorer = Callable[[RuntimeEntity, GuiCandidate], float | None]


def _bounded_score(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return None


def _coerce_runtime_entity(value: RuntimeEntity | Mapping[str, Any], index: int) -> RuntimeEntity:
    if isinstance(value, RuntimeEntity):
        return value
    key = str(value.get("key") or value.get("id") or index)
    name = str(value.get("name") or value.get("title") or value.get("label") or "")
    aliases = tuple(str(item) for item in (value.get("aliases") or ()) if str(item))
    return RuntimeEntity(key=key, name=name, aliases=aliases, payload=dict(value))


def _coerce_gui_candidate(value: GuiCandidate | Mapping[str, Any], index: int) -> GuiCandidate:
    if isinstance(value, GuiCandidate):
        return value
    point = value.get("point")
    box = value.get("box")
    return GuiCandidate(
        key=str(value.get("key") or value.get("id") or value.get("slot") or index),
        text=str(value.get("text") or value.get("ocr_text") or value.get("name") or ""),
        slot=int(value["slot"]) if value.get("slot") is not None else None,
        point=tuple(point) if isinstance(point, Sequence) and len(point) == 2 else None,
        box=tuple(box) if isinstance(box, Sequence) and len(box) == 4 else None,
        reliability=float(value.get("reliability", 1.0) or 0.0),
        payload=dict(value),
    )


def score_runtime_gui_pair(
    entity: RuntimeEntity,
    candidate: GuiCandidate,
    *,
    context_scorer: PairContextScorer | None = None,
    text_weight: float = 0.8,
    context_weight: float = 0.2,
) -> RuntimeGuiPairEvidence:
    """Score identity evidence without promoting OCR to business truth."""

    labels = tuple(dict.fromkeys((entity.name, *entity.aliases)))
    ranked = sorted(
        ((ocr_name_similarity(label, candidate.text), label) for label in labels if label),
        reverse=True,
    )
    text_score, matched_alias = ranked[0] if ranked else (0.0, "")
    context_score = _bounded_score(context_scorer(entity, candidate)) if context_scorer else None
    reliability = _bounded_score(candidate.reliability) or 0.0
    weighted: list[tuple[float, float]] = []
    if candidate.text.strip() and matched_alias:
        weighted.append((max(0.0, float(text_weight)), text_score))
    if context_score is not None:
        weighted.append((max(0.0, float(context_weight)), context_score))
    weight_total = sum(weight for weight, _score in weighted)
    score = sum(weight * value for weight, value in weighted) / weight_total if weight_total else 0.0
    score *= reliability
    return RuntimeGuiPairEvidence(
        runtime_key=entity.key,
        gui_key=candidate.key,
        runtime_name=entity.name,
        gui_text=candidate.text,
        matched_alias=matched_alias,
        text_score=round(text_score, 6),
        context_score=round(context_score, 6) if context_score is not None else None,
        reliability=round(reliability, 6),
        score=round(score, 6),
    )


def align_runtime_gui_candidates(
    runtime_entities: Iterable[RuntimeEntity | Mapping[str, Any]],
    gui_candidates: Iterable[GuiCandidate | Mapping[str, Any]],
    *,
    runtime_complete: bool,
    context_scorer: PairContextScorer | None = None,
    minimum_pair_score: float = 0.55,
    minimum_assignment_margin: float = 0.08,
    require_all_runtime_entities: bool = True,
    runtime_fingerprint: str = "",
    gui_fingerprint: str = "",
    maximum_assignment_size: int = 8,
) -> RuntimeGuiAlignment:
    """Globally align Runtime identities to GUI coordinates and fail closed.

    This primitive is intended for small visible candidate sets. Ordered long
    windows should first project a bounded visible window, then call this
    function for that window.
    """

    if not runtime_complete:
        return RuntimeGuiAlignment(
            status="incomplete_runtime",
            reason="游戏 Runtime 快照不完整，拒绝依据局部数据操作 GUI",
            runtime_fingerprint=runtime_fingerprint,
            gui_fingerprint=gui_fingerprint,
        )
    entities = tuple(_coerce_runtime_entity(item, index) for index, item in enumerate(runtime_entities))
    candidates = tuple(_coerce_gui_candidate(item, index) for index, item in enumerate(gui_candidates))
    if not entities or not candidates:
        return RuntimeGuiAlignment(
            status="insufficient",
            reason="Runtime 实体或 GUI 候选为空",
            runtime_fingerprint=runtime_fingerprint,
            gui_fingerprint=gui_fingerprint,
        )
    if require_all_runtime_entities and len(candidates) < len(entities):
        return RuntimeGuiAlignment(
            status="insufficient",
            reason="GUI 候选少于必须对齐的 Runtime 实体",
            runtime_fingerprint=runtime_fingerprint,
            gui_fingerprint=gui_fingerprint,
        )
    assignment_size = min(len(entities), len(candidates))
    if assignment_size > int(maximum_assignment_size):
        return RuntimeGuiAlignment(
            status="too_large",
            reason=f"候选规模 {assignment_size} 超过小窗口全局对齐上限 {maximum_assignment_size}",
            runtime_fingerprint=runtime_fingerprint,
            gui_fingerprint=gui_fingerprint,
        )

    selected_entities = entities if require_all_runtime_entities else entities[:assignment_size]
    hypotheses: list[tuple[float, float, tuple[int, ...], tuple[RuntimeGuiPairEvidence, ...]]] = []
    for candidate_indices in itertools.permutations(range(len(candidates)), len(selected_entities)):
        evidence = tuple(
            score_runtime_gui_pair(entity, candidates[candidate_index], context_scorer=context_scorer)
            for entity, candidate_index in zip(selected_entities, candidate_indices, strict=True)
        )
        hypotheses.append((sum(item.score for item in evidence), min(item.score for item in evidence), candidate_indices, evidence))
    hypotheses.sort(key=lambda item: (item[0], item[1]), reverse=True)
    best = hypotheses[0]
    second_score = hypotheses[1][0] if len(hypotheses) > 1 else 0.0
    margin = best[0] - second_score
    common = {
        "best_score": round(best[0], 6),
        "second_score": round(second_score, 6),
        "score_margin": round(margin, 6),
        "minimum_pair_score": float(minimum_pair_score),
        "runtime_fingerprint": runtime_fingerprint,
        "gui_fingerprint": gui_fingerprint,
    }
    if best[1] < float(minimum_pair_score):
        return RuntimeGuiAlignment(
            status="insufficient",
            reason=f"至少一个实体的最佳对齐分 {best[1]:.3f} 低于阈值 {minimum_pair_score:.3f}",
            **common,
        )
    if len(hypotheses) > 1 and margin < float(minimum_assignment_margin):
        return RuntimeGuiAlignment(
            status="ambiguous",
            reason=f"最佳方案领先 {margin:.3f}，不足以排除相邻候选",
            **common,
        )
    mappings = tuple(
        RuntimeGuiMapping(entity, candidates[candidate_index], evidence)
        for entity, candidate_index, evidence in zip(selected_entities, best[2], best[3], strict=True)
    )
    return RuntimeGuiAlignment(
        status="aligned",
        reason="游戏 Runtime 实体已与 GUI 候选唯一对齐",
        mappings=mappings,
        **common,
    )


__all__ = [
    "GuiCandidate",
    "RuntimeEntity",
    "RuntimeGuiAlignment",
    "RuntimeGuiMapping",
    "RuntimeGuiPairEvidence",
    "RuntimeEvidenceValidation",
    "align_runtime_gui_candidates",
    "score_runtime_gui_pair",
    "validate_runtime_evidence",
]
