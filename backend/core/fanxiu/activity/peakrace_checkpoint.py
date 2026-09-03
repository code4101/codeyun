from __future__ import annotations

"""Checkpoint-specific, read-only completion predicates for Peak Race."""

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping

from backend.core.fanxiu.activity.peakrace_lifecycle import (
    GUESS_POLICY_KIND,
    LIFECYCLE_CLOSE_KIND,
    OCCURRENCE_RECONCILE_KIND,
    OUTER_REWARD_KIND,
    STAGE_CLOSE_KIND,
    STAGE_OPEN_KIND,
    WORSHIP_CLOSE_KIND,
    WORSHIP_OPEN_KIND,
    PeakRaceCheckpoint,
    PeakRaceOccurrence,
    checkpoints_for_peakrace,
)


@dataclass(frozen=True)
class PeakRaceCheckpointObservation:
    status: str
    reason: str
    retryable: bool
    facts: dict[str, Any]
    evidence: dict[str, Any]

    @property
    def terminal(self) -> bool:
        return self.status == "observed"

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "terminal": self.terminal}


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _timestamp(value: Any) -> datetime | None:
    raw = _integer(value)
    if raw is None or raw <= 0:
        return None
    return datetime.fromtimestamp(raw / 1000).astimezone().replace(microsecond=0)


def _family(schedule: Mapping[str, Any], outer_activity_id: int) -> Mapping[str, Any] | None:
    rows = [
        item
        for item in schedule.get("peakraceSchedules") or ()
        if isinstance(item, Mapping)
        and _integer(item.get("outerActivityId")) == outer_activity_id
    ]
    return rows[0] if len(rows) == 1 else None


def _descriptor(
    family: Mapping[str, Any],
    *,
    activity_id: int,
    role: str,
) -> Mapping[str, Any] | None:
    if role == "outer":
        candidates = [family.get("outer")]
    elif role == "worship":
        candidates = [family.get("worship")]
    else:
        candidates = list(family.get("stages") or ())
    rows = [
        item
        for item in candidates
        if isinstance(item, Mapping)
        and _integer(item.get("activityId")) == activity_id
    ]
    return rows[0] if len(rows) == 1 else None


def _runtime_row(descriptor: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if not isinstance(descriptor, Mapping) or descriptor.get("runtimeObserved") is not True:
        return None
    rows = [
        row
        for row in descriptor.get("runtimeOccurrences") or ()
        if isinstance(row, Mapping)
    ]
    return rows[0] if len(rows) == 1 else None


def _base_evidence(
    schedule: Mapping[str, Any],
    business_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    sources = business_snapshot.get("sources")
    sources = sources if isinstance(sources, Mapping) else {}
    return {
        "schedule_complete": schedule.get("peakraceScheduleComplete") is True,
        "source_identity_coherent": sources.get("identity_coherent") is True,
        "activity_schedule": sources.get("activity_schedule"),
        "peakrace_runtime": sources.get("peakrace_runtime"),
        "rank_snapshots": sources.get("rank_snapshots"),
    }


def _result(
    status: str,
    reason: str,
    *,
    facts: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> PeakRaceCheckpointObservation:
    return PeakRaceCheckpointObservation(
        status=status,
        reason=reason,
        retryable=status != "observed",
        facts=dict(facts),
        evidence=dict(evidence),
    )


def evaluate_peakrace_checkpoint(
    checkpoint: PeakRaceCheckpoint,
    occurrence: PeakRaceOccurrence,
    schedule: Mapping[str, Any],
    business_snapshot: Mapping[str, Any],
    *,
    now: datetime,
    observed_keys: Iterable[tuple[str, str, int, str]] = (),
) -> PeakRaceCheckpointObservation:
    """Evaluate exactly one checkpoint without inheriting top-level status."""

    evidence = _base_evidence(schedule, business_snapshot)
    state = business_snapshot.get("state")
    state = state if isinstance(state, Mapping) else {}
    facts = {
        "checkpoint_kind": checkpoint.checkpoint_kind,
        "subject_activity_id": checkpoint.subject_activity_id,
        "phase": state.get("phase"),
        "outer_phase": state.get("outer_phase"),
        "variant": state.get("variant"),
        "current_round": state.get("current_round"),
        "expected_round": state.get("expected_round"),
        "qualification": state.get("qualification"),
        "current_group": state.get("current_group"),
        "self_rank": state.get("self_rank"),
        "blockers": list(state.get("blockers") or ()),
    }
    if now.tzinfo is None or now.utcoffset() is None:
        return _result("error", "business_time_not_timezone_aware", facts=facts, evidence=evidence)
    if checkpoint.instance_key != occurrence.instance_key:
        return _result("error", "checkpoint_occurrence_mismatch", facts=facts, evidence=evidence)
    family = _family(schedule, occurrence.outer_activity_id)
    if (
        schedule.get("peakraceScheduleComplete") is not True
        or family is None
        or family.get("configurationComplete") is not True
    ):
        return _result("blocked", "peakrace_family_incomplete", facts=facts, evidence=evidence)
    if evidence["source_identity_coherent"] is not True:
        return _result("blocked", "runtime_source_identity_unverified", facts=facts, evidence=evidence)

    kind = checkpoint.checkpoint_kind
    if kind == OCCURRENCE_RECONCILE_KIND:
        outer = _runtime_row(_descriptor(
            family,
            activity_id=occurrence.outer_activity_id,
            role="outer",
        ))
        outer_start = _timestamp((outer or {}).get("startTime"))
        outer_end = _timestamp((outer or {}).get("endTime"))
        facts["current_runtime_id"] = str((outer or {}).get("id") or "") or None
        if (
            outer is None
            or outer_start != occurrence.start_at
            or outer_end != occurrence.end_at
        ):
            return _result("blocked", "outer_runtime_occurrence_unobserved", facts=facts, evidence=evidence)
        return _result("observed", "outer_occurrence_reconciled", facts=facts, evidence=evidence)

    if kind in {STAGE_OPEN_KIND, STAGE_CLOSE_KIND}:
        row = _runtime_row(_descriptor(
            family,
            activity_id=checkpoint.subject_activity_id,
            role="stage",
        ))
        if row is None:
            return _result("blocked", "stage_runtime_occurrence_unobserved", facts=facts, evidence=evidence)
        start_at = _timestamp(row.get("startTime"))
        end_at = _timestamp(row.get("endTime"))
        facts.update(
            {
                "stage_runtime_id": str(row.get("id") or "") or None,
                "stage_start_at": start_at.isoformat() if start_at else None,
                "stage_end_at": end_at.isoformat() if end_at else None,
            }
        )
        if start_at is None or end_at is None:
            return _result("error", "stage_runtime_interval_invalid", facts=facts, evidence=evidence)
        if kind == STAGE_CLOSE_KIND:
            return _result(
                "observed" if now >= end_at else "blocked",
                "stage_close_observed" if now >= end_at else "stage_not_closed",
                facts=facts,
                evidence=evidence,
            )
        stage = state.get("stage")
        stage = stage if isinstance(stage, Mapping) else {}
        completion = state.get("completion")
        completion = completion if isinstance(completion, Mapping) else {}
        open_complete = bool(
            start_at <= now < end_at
            and _integer(stage.get("activity_id")) == checkpoint.subject_activity_id
            and stage.get("schedule_phase") == "open"
            and state.get("current_round") == state.get("expected_round")
            and state.get("qualification") in {"qualified", "not_qualified"}
            and completion.get("observation_complete") is True
        )
        return _result(
            "observed" if open_complete else "blocked",
            "stage_open_observed" if open_complete else "stage_open_facts_incomplete",
            facts=facts,
            evidence=evidence,
        )

    if kind == GUESS_POLICY_KIND:
        guess = state.get("guess")
        guess = guess if isinstance(guess, Mapping) else {}
        facts["guess"] = dict(guess)
        policy_observed = bool(
            _integer((state.get("stage") or {}).get("activity_id"))
            == checkpoint.subject_activity_id
            and guess.get("required") is not None
            and guess.get("status") not in {None, "policy_unknown", "limit_unknown"}
        )
        return _result(
            "observed" if policy_observed else "blocked",
            "guess_policy_observed" if policy_observed else "guess_policy_not_authoritative",
            facts=facts,
            evidence=evidence,
        )

    if kind == OUTER_REWARD_KIND:
        total_rank = state.get("total_rank")
        total_rank = total_rank if isinstance(total_rank, Mapping) else {}
        facts["total_rank"] = dict(total_rank)
        observed = state.get("outer_phase") == "reward" and total_rank.get("complete") is True
        return _result(
            "observed" if observed else "blocked",
            "outer_reward_window_observed" if observed else "outer_reward_window_unverified",
            facts=facts,
            evidence=evidence,
        )

    if kind in {WORSHIP_OPEN_KIND, WORSHIP_CLOSE_KIND}:
        row = _runtime_row(_descriptor(
            family,
            activity_id=checkpoint.subject_activity_id,
            role="worship",
        ))
        if row is None:
            return _result("blocked", "worship_runtime_occurrence_unobserved", facts=facts, evidence=evidence)
        start_at = _timestamp(row.get("startTime"))
        end_at = _timestamp(row.get("endTime"))
        if start_at is None or end_at is None:
            return _result("error", "worship_runtime_interval_invalid", facts=facts, evidence=evidence)
        if kind == WORSHIP_OPEN_KIND:
            observed = start_at <= now < end_at and state.get("phase") == "worship"
            reason = "worship_open_observed" if observed else "worship_open_unverified"
        else:
            observed = now >= end_at
            reason = "worship_close_observed" if observed else "worship_not_closed"
        return _result("observed" if observed else "blocked", reason, facts=facts, evidence=evidence)

    if kind == LIFECYCLE_CLOSE_KIND:
        prior = {
            key
            for key in observed_keys
            if key[0] == occurrence.instance_key
            and key != checkpoint.key
        }
        expected = {
            item.key
            for item in checkpoints_for_peakrace(occurrence)
            if item.key != checkpoint.key
        }
        facts["prior_observed_count"] = len(prior)
        facts["prior_expected_count"] = len(expected)
        observed = now >= occurrence.close_at and expected.issubset(prior)
        return _result(
            "observed" if observed else "blocked",
            "lifecycle_close_audited" if observed else "lifecycle_history_incomplete",
            facts=facts,
            evidence=evidence,
        )

    return _result("error", "unknown_checkpoint_kind", facts=facts, evidence=evidence)


__all__ = ["PeakRaceCheckpointObservation", "evaluate_peakrace_checkpoint"]
