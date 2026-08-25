from __future__ import annotations

from typing import Any, Mapping


DONGTIAN_SEATING_PLACE_AUTHORIZATION_PROTOCOL = (
    "dongtian.seating.place-authorization.v1"
)


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None


def _int_value(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _mine_ids(values: Any) -> tuple[int, ...]:
    if not isinstance(values, (list, tuple, set, frozenset)):
        return ()
    return tuple(
        sorted(
            {
                normalized
                for value in values
                if (normalized := _positive_int(value)) is not None
            }
        )
    )


def build_dongtian_seating_place_authorization(
    probe: Mapping[str, Any],
) -> dict[str, Any]:
    """Freeze one Runtime-selected friendly place into a click authorization.

    The result is deliberately a structured capability rather than a place
    name.  It carries the process identity and ownership facts that a fresh
    probe must reproduce immediately before GUI navigation.
    """

    if not probe.get("available") or not probe.get("complete"):
        raise ValueError("洞天上座地点授权失败：Runtime probe 不完整")
    if str(probe.get("status") or "") != "ready":
        raise ValueError("洞天上座地点授权失败：Runtime probe 尚无可用地点")
    mine = probe.get("selected_mine")
    evidence = probe.get("evidence")
    if not isinstance(mine, Mapping) or not isinstance(evidence, Mapping):
        raise ValueError("洞天上座地点授权失败：缺少地点或进程身份")

    own_union_id = _positive_int(probe.get("own_union_id"))
    mine_id = _positive_int(mine.get("id"))
    config_id = _positive_int(mine.get("config_id"))
    config_name = str(mine.get("config_name") or "").strip()
    config_group = _positive_int(mine.get("config_group"))
    config_pos_y = _int_value(mine.get("config_pos_y"))
    mine_union_id = _positive_int(mine.get("cross_union_id"))
    pid = _positive_int(evidence.get("pid"))
    process_start_ticks = _positive_int(evidence.get("process_start_ticks"))
    occupied_mine_ids = _mine_ids(probe.get("occupied_mine_ids"))
    config_sha256 = str(probe.get("mines_place_config_sha256") or "").strip()
    if None in {
        own_union_id,
        mine_id,
        config_id,
        config_group,
        config_pos_y,
        mine_union_id,
        pid,
        process_start_ticks,
    } or not config_name or not config_sha256:
        raise ValueError("洞天上座地点授权失败：关键 Runtime 身份字段缺失")
    assert own_union_id is not None
    assert mine_id is not None
    assert mine_union_id is not None
    assert config_id is not None
    assert config_group is not None
    assert config_pos_y is not None
    assert pid is not None
    assert process_start_ticks is not None
    if mine_union_id != own_union_id or mine.get("friendly") is not True:
        raise ValueError("洞天上座地点授权失败：目标不是本方联盟地点")
    if mine_id in occupied_mine_ids:
        raise ValueError("洞天上座地点授权失败：本方已有队伍位于目标地点")
    if config_id != mine_id:
        raise ValueError("洞天上座地点授权失败：动态地点与 MinesPlace 配置 ID 不一致")

    return {
        "protocol": DONGTIAN_SEATING_PLACE_AUTHORIZATION_PROTOCOL,
        "mine_id": mine_id,
        "config_id": config_id,
        "place_name": config_name,
        "config_name": config_name,
        "config_group": config_group,
        "config_pos_y": config_pos_y,
        "own_union_id": own_union_id,
        "cross_union_id": mine_union_id,
        "excluded_mine_ids": list(_mine_ids(probe.get("excluded_mine_ids"))),
        "mines_place_config_sha256": config_sha256,
        "evidence": {
            "pid": pid,
            "process_start_ticks": process_start_ticks,
        },
    }


def validate_dongtian_seating_place_authorization(
    authorization: Mapping[str, Any],
    fresh_probe: Mapping[str, Any],
) -> dict[str, Any]:
    """Fail closed unless a fresh probe reproduces every click authority fact."""

    def reject(reason: str) -> dict[str, Any]:
        return {"ok": False, "status": "click_blocked", "reason": reason}

    if not isinstance(authorization, Mapping):
        return reject("structured_authorization_required")
    if (
        authorization.get("protocol")
        != DONGTIAN_SEATING_PLACE_AUTHORIZATION_PROTOCOL
    ):
        return reject("authorization_protocol_mismatch")
    if not fresh_probe.get("available") or not fresh_probe.get("complete"):
        return reject("fresh_probe_incomplete")
    if str(fresh_probe.get("status") or "") != "ready":
        return reject("fresh_probe_not_ready")

    target_mine_id = _positive_int(authorization.get("mine_id"))
    target_config_id = _positive_int(authorization.get("config_id"))
    target_config_name = str(authorization.get("config_name") or "").strip()
    target_config_group = _positive_int(authorization.get("config_group"))
    target_config_pos_y = _int_value(authorization.get("config_pos_y"))
    target_own_union_id = _positive_int(authorization.get("own_union_id"))
    target_union_id = _positive_int(authorization.get("cross_union_id"))
    target_evidence = authorization.get("evidence")
    fresh_evidence = fresh_probe.get("evidence")
    fresh_mine = fresh_probe.get("selected_mine")
    if (
        target_mine_id is None
        or target_config_id is None
        or target_config_group is None
        or target_config_pos_y is None
        or not target_config_name
        or target_own_union_id is None
        or target_union_id is None
        or not isinstance(target_evidence, Mapping)
        or not isinstance(fresh_evidence, Mapping)
        or not isinstance(fresh_mine, Mapping)
    ):
        return reject("authorization_identity_incomplete")

    target_process = (
        _positive_int(target_evidence.get("pid")),
        _positive_int(target_evidence.get("process_start_ticks")),
    )
    fresh_process = (
        _positive_int(fresh_evidence.get("pid")),
        _positive_int(fresh_evidence.get("process_start_ticks")),
    )
    if None in target_process or fresh_process != target_process:
        return reject("process_identity_changed")

    fresh_own_union_id = _positive_int(fresh_probe.get("own_union_id"))
    fresh_mine_id = _positive_int(fresh_mine.get("id"))
    fresh_config_id = _positive_int(fresh_mine.get("config_id"))
    fresh_config_name = str(fresh_mine.get("config_name") or "").strip()
    fresh_config_group = _positive_int(fresh_mine.get("config_group"))
    fresh_config_pos_y = _int_value(fresh_mine.get("config_pos_y"))
    fresh_union_id = _positive_int(fresh_mine.get("cross_union_id"))
    if target_own_union_id != target_union_id:
        return reject("authorization_nonfriendly")
    if fresh_own_union_id != target_own_union_id:
        return reject("own_union_changed")
    if fresh_mine_id != target_mine_id:
        return reject("selected_mine_changed")
    if target_config_id != target_mine_id or fresh_config_id != fresh_mine_id:
        return reject("place_config_id_mismatch")
    if (
        fresh_config_name != target_config_name
        or fresh_config_group != target_config_group
        or fresh_config_pos_y != target_config_pos_y
    ):
        return reject("place_config_changed")
    if fresh_union_id != fresh_own_union_id or fresh_mine.get("friendly") is not True:
        return reject("selected_mine_nonfriendly")
    if target_mine_id in _mine_ids(fresh_probe.get("occupied_mine_ids")):
        return reject("target_mine_already_occupied")
    if target_mine_id in _mine_ids(authorization.get("excluded_mine_ids")):
        return reject("target_mine_excluded")

    canonical_name = fresh_config_name
    if str(authorization.get("place_name") or "").strip() != canonical_name:
        return reject("place_name_mismatch")
    planned_config_sha = str(
        authorization.get("mines_place_config_sha256") or ""
    )
    fresh_config_sha = str(fresh_probe.get("mines_place_config_sha256") or "")
    if not planned_config_sha or not fresh_config_sha or fresh_config_sha != planned_config_sha:
        return reject("place_config_changed")

    return {
        "ok": True,
        "status": "click_authorized",
        "mine_id": target_mine_id,
        "place_name": canonical_name,
        "own_union_id": fresh_own_union_id,
        "cross_union_id": fresh_union_id,
        "evidence": dict(fresh_evidence),
    }


__all__ = [
    "DONGTIAN_SEATING_PLACE_AUTHORIZATION_PROTOCOL",
    "build_dongtian_seating_place_authorization",
    "validate_dongtian_seating_place_authorization",
]
