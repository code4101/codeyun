"""Fail-closed Dongtian Xianlv-team observations for the player atlas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from sqlmodel import Session

from backend.core.fanxiu.data_annotation.dongtian_seating import (
    classify_dongtian_detail_freshness,
)
from backend.core.fanxiu.player_profiles import (
    ingest_fanxiu_player_battle_observation,
)


_SOURCE_PROTOCOLS = {
    "V_MineMasterDetailListDic": "dongtian.seat-detail.cache.v1",
    "V_GuarderTeamDic": "dongtian.seat-detail.final-guard-team-cache.v1",
}


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _detail(snapshot: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(snapshot, Mapping):
        return None
    nested = snapshot.get("detail")
    return dict(nested) if isinstance(nested, Mapping) else None


def _cache_absence_proven(snapshot: Mapping[str, Any] | None) -> bool:
    return bool(
        isinstance(snapshot, Mapping)
        and snapshot.get("available") is True
        and snapshot.get("cache_found") is False
        and snapshot.get("detail") is None
    )


def _process_identity(snapshot: Mapping[str, Any] | None) -> tuple[int, int] | None:
    if not isinstance(snapshot, Mapping):
        return None
    evidence = snapshot.get("evidence")
    if not isinstance(evidence, Mapping):
        return None
    pid = _positive_int(evidence.get("pid"))
    start_ticks = _positive_int(evidence.get("process_start_ticks"))
    return (pid, start_ticks) if pid is not None and start_ticks is not None else None


def _seat_role_id(
    probe: Mapping[str, Any] | None,
    *,
    mine_id: int,
    quality: int,
    seat_id: int,
) -> int | None:
    """Return one exact shallow occupant, never an inferred/name-matched role."""

    if not isinstance(probe, Mapping) or not probe.get("available") or not probe.get("complete"):
        return None
    mine = probe.get("selected_mine")
    if (
        not isinstance(mine, Mapping)
        or _positive_int(mine.get("id")) != mine_id
        or mine.get("seats_complete") is not True
    ):
        return None
    matches = [
        seat
        for seat in mine.get("seats") or []
        if isinstance(seat, Mapping)
        and _positive_int(seat.get("quality")) == quality
        and _positive_int(seat.get("id")) == seat_id
        and seat.get("complete") is True
        and not bool(seat.get("empty"))
    ]
    if len(matches) != 1:
        return None
    return _positive_int(matches[0].get("guarder_role_id"))


def _failure(reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "observation_rejected",
        "reason": reason,
        "observation": None,
    }


def build_fresh_dongtian_xianlv_team_observation(
    *,
    source_cache: str,
    target: Mapping[str, Any],
    before_probe: Mapping[str, Any],
    after_probe: Mapping[str, Any],
    before_detail_snapshot: Mapping[str, Any] | None,
    after_detail_snapshot: Mapping[str, Any],
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Validate one naturally refreshed defender team and build an atlas row.

    The detail cache proves the team score.  Independent shallow probes before
    and after the natural UI response prove that the same role still occupies
    the exact seat.  No public 1/2/3-team meaning is inferred from the cached
    team id.
    """

    expected_protocol = _SOURCE_PROTOCOLS.get(str(source_cache))
    if expected_protocol is None:
        return _failure("unsupported_source_cache")

    mine_id = _positive_int(target.get("mine_id"))
    quality = _positive_int(target.get("quality"))
    seat_id = _positive_int(target.get("seat_id"))
    target_role_id = _positive_int(target.get("guarder_role_id"))
    if None in {mine_id, quality, seat_id, target_role_id} or quality not in {1, 2}:
        return _failure("target_identity_incomplete")
    if source_cache == "V_MineMasterDetailListDic" and quality != 1:
        return _failure("master_cache_requires_master_seat")

    if (
        after_detail_snapshot.get("ok") is not True
        or after_detail_snapshot.get("available") is not True
        or after_detail_snapshot.get("complete") is not True
        or after_detail_snapshot.get("cache_found") is not True
        or str(after_detail_snapshot.get("source") or "") != "runtime_memory_cache"
        or str(after_detail_snapshot.get("protocol") or "") != expected_protocol
    ):
        return _failure("detail_envelope_incomplete")
    if (
        source_cache == "V_GuarderTeamDic"
        and after_detail_snapshot.get("detail_layer") != "site_info_guard_team"
    ):
        return _failure("guard_team_layer_missing")

    after_detail = _detail(after_detail_snapshot)
    before_detail = _detail(before_detail_snapshot)
    if not isinstance(after_detail, Mapping) or after_detail.get("complete") is not True:
        return _failure("detail_incomplete")
    if any(
        _positive_int(after_detail.get(key)) != expected
        for key, expected in (
            ("mine_id", mine_id),
            ("quality", quality),
            ("seat_id", seat_id),
        )
    ):
        return _failure("detail_seat_identity_mismatch")

    classified = classify_dongtian_detail_freshness(
        before=before_detail,
        after=after_detail,
        expected_mine_id=mine_id,
        expected_quality=quality,
        expected_seat_id=seat_id,
        before_absence_proven=_cache_absence_proven(before_detail_snapshot),
    )
    if not classified.get("ok") or not classified.get("fresh"):
        return _failure("detail_not_fresh")

    identities = {
        _process_identity(before_probe),
        _process_identity(after_probe),
        _process_identity(after_detail_snapshot),
    }
    if None in identities or len(identities) != 1:
        return _failure("process_identity_changed_or_missing")
    process_identity = next(iter(identities))
    assert process_identity is not None

    before_role_id = _seat_role_id(
        before_probe,
        mine_id=mine_id,
        quality=quality,
        seat_id=seat_id,
    )
    after_role_id = _seat_role_id(
        after_probe,
        mine_id=mine_id,
        quality=quality,
        seat_id=seat_id,
    )
    if before_role_id is None or after_role_id is None:
        return _failure("occupant_identity_missing")
    if before_role_id != target_role_id or after_role_id != target_role_id:
        return _failure("occupant_identity_changed")
    detail_role_id = _positive_int(after_detail.get("guarder_role_id"))
    if detail_role_id is not None and detail_role_id != target_role_id:
        return _failure("detail_occupant_identity_mismatch")

    fight_score = _positive_int(after_detail.get("fight_score"))
    cache_generation = _positive_int(after_detail.get("cache_generation_address"))
    if fight_score is None or cache_generation is None:
        return _failure("team_score_or_generation_missing")

    timestamp = str(observed_at or datetime.now(ZoneInfo("Asia/Shanghai")).isoformat())
    pid, process_start_ticks = process_identity
    observation_id = (
        "dongtian-xianlv:"
        f"{source_cache}:{pid}:{process_start_ticks}:{cache_generation}:"
        f"{mine_id}:{quality}:{seat_id}:{target_role_id}"
    )
    observation = {
        "observation_id": observation_id,
        "source_kind": "dongtian_xianlv_team_runtime",
        "protocol": expected_protocol,
        "role_id": str(target_role_id),
        "role_id_text": str(target_role_id),
        "xianlv_team_fight_score_max": fight_score,
        "xianlv_team_observed_at": timestamp,
        "observed_at": timestamp,
        "evidence": {
            "source_cache": source_cache,
            "freshness": str(classified.get("freshness") or ""),
            "pid": pid,
            "process_start_ticks": process_start_ticks,
            "cache_generation_address": cache_generation,
            "mine_id": mine_id,
            "quality": quality,
            "seat_id": seat_id,
            "guarder_role_id_before": before_role_id,
            "guarder_role_id_after": after_role_id,
            "team_id": _positive_int(after_detail.get("team_id")),
        },
    }
    return {
        "ok": True,
        "status": "observation_ready",
        "reason": "",
        "observation": observation,
    }


def ingest_fresh_dongtian_xianlv_team_observation(
    session: Session,
    **kwargs: Any,
) -> dict[str, Any]:
    """Build and persist only a fully validated Dongtian observation."""

    built = build_fresh_dongtian_xianlv_team_observation(**kwargs)
    if not built.get("ok"):
        return {**built, "ingest": None}
    observation = built.get("observation")
    assert isinstance(observation, dict)
    return {
        **built,
        "status": "observation_ingested",
        "ingest": ingest_fanxiu_player_battle_observation(session, observation),
    }


__all__ = [
    "build_fresh_dongtian_xianlv_team_observation",
    "ingest_fresh_dongtian_xianlv_team_observation",
]
