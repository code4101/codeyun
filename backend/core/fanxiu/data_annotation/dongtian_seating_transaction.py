from __future__ import annotations

from typing import Any, Mapping, Protocol

from backend.core.fanxiu.data_annotation.dongtian_seating import (
    classify_dongtian_detail_freshness,
    choose_dongtian_probe_action,
)


class DongtianSeatingRuntimeSessionLike(Protocol):
    def probe(self, *, excluded_mine_ids: set[int]) -> dict[str, Any]: ...

    def cached_seat_detail(
        self,
        *,
        mine_id: int,
        quality: int,
        seat_id: int,
    ) -> dict[str, Any]: ...

    def cached_final_guard_team_detail(
        self,
        *,
        mine_id: int,
        quality: int,
        seat_id: int,
    ) -> dict[str, Any]: ...

    def revalidate_process_identity(self) -> dict[str, Any]: ...


def _detail(snapshot: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(snapshot, Mapping):
        return None
    nested = snapshot.get("detail")
    if isinstance(nested, Mapping):
        return dict(nested)
    if snapshot.get("mine_id") is not None:
        return dict(snapshot)
    return None


def _cache_absence_proven(snapshot: Mapping[str, Any] | None) -> bool:
    """Distinguish a successful cache miss from an unavailable before read."""

    return bool(
        isinstance(snapshot, Mapping)
        and snapshot.get("available") is True
        and snapshot.get("cache_found") is False
        and snapshot.get("detail") is None
    )


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


class DongtianSeatingTransaction:
    """Coordinate one bounded Runtime-only seating decision transaction.

    GUI code owns navigation and natural clicks.  This object owns the safety
    facts around those clicks: one-mine-at-a-time early stop, detail freshness,
    process identity, team state and exact target revalidation.  It never
    performs an occupy/battle action itself.
    """

    def __init__(self, session: DongtianSeatingRuntimeSessionLike) -> None:
        self.session = session
        self.excluded_mine_ids: set[int] = set()
        self.native_seat_details: dict[str, dict[str, Any]] = {}
        self.final_guard_details: dict[str, dict[str, Any]] = {}

    def next_action(self, *, max_mines: int = 39) -> dict[str, Any]:
        """Return the first actionable/unresolved target and stop immediately."""

        for _ in range(max(1, int(max_mines))):
            probe = self.session.probe(
                excluded_mine_ids=set(self.excluded_mine_ids)
            )
            decision = choose_dongtian_probe_action(
                probe,
                seat_details=self.final_guard_details,
            )
            if not decision.get("ok"):
                return decision
            if decision.get("status") in {"need_detail", "refresh_defender"}:
                target = decision.get("target")
                if isinstance(target, Mapping):
                    seat_key = str(target.get("seat_key") or "")
                    quality = _positive_int(target.get("quality"))
                    if quality == 2 or seat_key in self.native_seat_details:
                        return {
                            **decision,
                            "status": "need_final_detail",
                            "action": "inspect_final_guard",
                            "probe": probe,
                        }
            if decision.get("status") != "advance_mine":
                return {**decision, "probe": probe}
            mine_id = decision.get("excluded_mine_id")
            if not isinstance(mine_id, int) or mine_id <= 0:
                return {
                    "ok": False,
                    "status": "incomplete",
                    "reason": "advance_mine_identity_missing",
                    "action": None,
                }
            self.excluded_mine_ids.add(mine_id)
        return {
            "ok": False,
            "status": "incomplete",
            "reason": "mine_scan_safety_limit",
            "action": None,
        }

    def record_natural_detail_response(
        self,
        *,
        target: Mapping[str, Any],
        before: Mapping[str, Any] | None,
        after: Mapping[str, Any] | None,
        request_watermark: int | None = None,
    ) -> dict[str, Any]:
        """Record a fresh master-list detail used only to address native seat."""

        mine_id = int(target.get("mine_id") or 0)
        quality = int(target.get("quality") or 0)
        seat_id = int(target.get("seat_id") or 0)
        seat_key = str(target.get("seat_key") or "")
        if quality != 1:
            return {
                "ok": False,
                "status": "native_detail_not_applicable",
                "reason": "only_master_has_native_list_detail",
                "seat_key": seat_key,
            }
        classified = classify_dongtian_detail_freshness(
            before=_detail(before),
            after=_detail(after),
            expected_mine_id=mine_id,
            expected_quality=quality,
            expected_seat_id=seat_id,
            request_watermark=request_watermark,
            before_absence_proven=_cache_absence_proven(before),
        )
        detail = classified.get("detail")
        if not classified.get("ok") or not classified.get("fresh") or not isinstance(detail, Mapping):
            return {
                **classified,
                "ok": False,
                "status": "detail_not_fresh",
                "seat_key": seat_key,
            }
        stored = {**dict(detail), "freshness": str(classified["freshness"])}
        self.native_seat_details[seat_key] = stored
        return {
            **classified,
            "ok": True,
            "status": "native_detail_recorded",
            "seat_key": seat_key,
        }

    def record_final_guard_detail_response(
        self,
        *,
        target: Mapping[str, Any],
        before: Mapping[str, Any] | None,
        after: Mapping[str, Any] | None,
        request_watermark: int | None = None,
    ) -> dict[str, Any]:
        """Record fresh #343 V_GuarderTeamDic detail for battle authority."""

        mine_id = int(target.get("mine_id") or 0)
        quality = int(target.get("quality") or 0)
        seat_id = int(target.get("seat_id") or 0)
        seat_key = str(target.get("seat_key") or "")
        if not isinstance(after, Mapping) or after.get("detail_layer") != "site_info_guard_team":
            return {
                "ok": False,
                "status": "final_detail_not_fresh",
                "reason": "final_guard_detail_layer_missing",
                "seat_key": seat_key,
            }
        classified = classify_dongtian_detail_freshness(
            before=_detail(before),
            after=_detail(after),
            expected_mine_id=mine_id,
            expected_quality=quality,
            expected_seat_id=seat_id,
            request_watermark=request_watermark,
            before_absence_proven=_cache_absence_proven(before),
        )
        detail = classified.get("detail")
        if (
            not classified.get("ok")
            or not classified.get("fresh")
            or not isinstance(detail, Mapping)
        ):
            return {
                **classified,
                "ok": False,
                "status": "final_detail_not_fresh",
                "seat_key": seat_key,
            }
        self.final_guard_details[seat_key] = {
            **dict(detail),
            "freshness": str(classified["freshness"]),
            "detail_layer": "site_info_guard_team",
        }
        return {
            **classified,
            "ok": True,
            "status": "final_detail_recorded",
            "seat_key": seat_key,
        }

    def revalidate_ready_target(
        self,
        ready_decision: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Re-read all decisive Runtime facts just before an occupy click."""

        if ready_decision.get("status") != "ready":
            return {
                "ok": False,
                "status": "not_ready",
                "reason": "decision_not_ready",
            }
        expected = ready_decision.get("target")
        if not isinstance(expected, Mapping):
            return {
                "ok": False,
                "status": "not_ready",
                "reason": "target_missing",
            }
        identity = self.session.revalidate_process_identity()
        if not identity.get("ok"):
            return {
                "ok": False,
                "status": "stale_process",
                "reason": str(identity.get("reason") or "process_identity_changed"),
                "identity": identity,
            }

        probe = self.session.probe(
            excluded_mine_ids=set(self.excluded_mine_ids)
        )
        current = choose_dongtian_probe_action(
            probe,
            seat_details=self.final_guard_details,
        )
        current_target = current.get("target")
        target_fields = (
            "mine_id",
            "quality",
            "seat_id",
            "team_id",
            "mode",
            "ui_route",
        )
        if (
            current.get("status") != "ready"
            or not isinstance(current_target, Mapping)
            or any(current_target.get(key) != expected.get(key) for key in target_fields)
        ):
            return {
                "ok": False,
                "status": "target_changed",
                "reason": "fresh_probe_changed_decision",
                "current": current,
            }

        if str(expected.get("mode")) == "replace_weaker_enemy":
            latest_snapshot = self.session.cached_final_guard_team_detail(
                mine_id=int(expected["mine_id"]),
                quality=int(expected["quality"]),
                seat_id=int(expected["seat_id"]),
            )
            latest = _detail(latest_snapshot)
            stored = self.final_guard_details.get(
                str(expected.get("seat_key") or "")
            )
            if (
                not isinstance(latest_snapshot, Mapping)
                or latest_snapshot.get("ok") is not True
                or latest_snapshot.get("available") is not True
                or latest_snapshot.get("complete") is not True
                or latest_snapshot.get("cache_found") is not True
                or latest_snapshot.get("detail_layer")
                != "site_info_guard_team"
                or any(
                    _positive_int(latest_snapshot.get(key))
                    != _positive_int(expected.get(target_key))
                    for key, target_key in (
                        ("mine_id", "mine_id"),
                        ("quality", "quality"),
                        ("seat_id", "seat_id"),
                    )
                )
                or not isinstance(latest, Mapping)
                or latest.get("complete") is not True
                or not isinstance(stored, Mapping)
                or stored.get("detail_layer") != "site_info_guard_team"
                or any(
                    _positive_int(latest.get(key))
                    != _positive_int(expected.get(target_key))
                    for key, target_key in (
                        ("mine_id", "mine_id"),
                        ("quality", "quality"),
                        ("seat_id", "seat_id"),
                    )
                )
                or latest.get("cache_generation_address")
                != stored.get("cache_generation_address")
                or latest.get("fight_score") != stored.get("fight_score")
                or any(
                    latest.get(key) != stored.get(key)
                    for key in (
                        "team_id",
                        "guarder_role_id",
                        "guarder_cross_union_id",
                    )
                    if key in latest or key in stored
                )
            ):
                return {
                    "ok": False,
                    "status": "target_changed",
                    "reason": "defender_cache_changed",
                }
        return {
            "ok": True,
            "status": "ready_revalidated",
            "target": dict(current_target),
            "identity": identity,
        }
