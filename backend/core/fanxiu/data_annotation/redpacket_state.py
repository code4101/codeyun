from __future__ import annotations

from typing import Any

from backend.core.fanxiu.instrumentation.red_packet import (
    read_cached_chat_red_packet_pending,
    read_red_packet_pending,
)
REDPACKET_STATE_PROBE_ID = "red-packet"
REDPACKET_SCHEDULER_TASK_ID = "daily-redpacket"
QMCH_REWARD_EVENT_TYPE = 9033
QMCH_REWARD_EVENT_KEY = "qmch_reward"
QMCH_REWARD_CONFIG_ID = 5022
QMCH_REWARD_CHANNEL = 101

# Reverse evidence: ChatModel.CheckHasRedBagShow() does not retain one Boolean;
# RedbagData is rebuilt from SM_OfflineRedBag and ordered SM_New/Update/Grab
# events.  Patrol therefore projects those passive server facts by stable UID.
# Process addresses are transient diagnostic evidence only, never business
# identity.  Do not turn this read-only probe into a Lua call/bridge.


def refresh_redpacket_runtime_snapshot() -> dict[str, Any]:
    """Read the prewarmed chat marker only; never discover inline."""

    return read_cached_chat_red_packet_pending()


def recover_redpacket_runtime_snapshot() -> dict[str, Any]:
    """Rebuild read-only roots without loading code into the game process."""

    return read_red_packet_pending(
        allow_discovery=True,
        # Patrol is a marker read.  It must never inject/load a Runtime bridge;
        # an unavailable marker is reported as unknown and retried later.
        allow_runtime_initialization=False,
        unavailable_cache_ttl_seconds=0.0,
        chat_only=True,
    )


def read_current_redpacket_state(
    *,
    max_age_seconds: float = 75.0,
) -> dict[str, Any]:
    """Return the current read-only Runtime projection."""

    del max_age_seconds  # Compatibility only; every patrol performs a fresh hot-path read.
    runtime = refresh_redpacket_runtime_snapshot()
    return classify_redpacket_runtime_snapshot(runtime)


def classify_redpacket_runtime_snapshot(runtime: dict[str, Any]) -> dict[str, Any]:
    """Classify one fresh read into evidence levels without action authority."""

    result = dict(runtime)
    chat = (runtime.get("sources") or {}).get("chat") or {}
    items = list(chat.get("items") or runtime.get("items") or [])
    structural_complete = bool(
        runtime.get("available")
        and runtime.get("complete")
        and chat
        and chat.get("available", True)
        and chat.get("complete")
    )
    trigger_complete = bool(
        structural_complete
        and chat.get("trigger_complete", chat.get("semantic_complete"))
        and all(
            item.get("uid") is not None
            for item in items
        )
    )
    claimability_complete = bool(
        structural_complete
        and chat.get("claimability_complete", chat.get("semantic_complete"))
    )
    pending_uids = sorted(
        {str(item.get("uid")) for item in items if item.get("uid") is not None}
    )
    # Runtime proves only a fresh candidate set. It may trigger the Job or let
    # a #395-negative attempt enter chat for visual inspection, but it never
    # authorizes choosing a group, card, or claim control.
    receive_queue_count = int(chat.get("receive_queue_count") or 0)
    trigger_ready = bool(
        structural_complete
        and (
            (trigger_complete and pending_uids)
            or receive_queue_count > 0
        )
    )
    result["evidence_levels"] = {
        "structural": structural_complete,
        # Compatibility name: this is trigger-semantic completeness, not
        # proof that any packet is claimable.
        "semantic": trigger_complete,
        "trigger": trigger_ready,
        "claimability": claimability_complete,
        "action": False,
    }
    result["pending_uids"] = pending_uids
    result["trigger_ready"] = trigger_ready
    result["action_authorized"] = False
    # Kept as a compatibility alias, but it now means the trigger evidence is
    # complete, not that a generic ``ok`` bit proves a packet is claimable.
    result["trigger_authoritative"] = bool(
        trigger_complete or receive_queue_count > 0
    )
    result["trigger_reason"] = (
        (
            "fresh_semantic_chat_candidates"
            if claimability_complete
            else "fresh_structural_chat_candidates_claimability_unknown"
        )
        if trigger_ready
        else (
            "no_fresh_semantic_chat_candidates"
            if trigger_complete
            else "chat_semantics_incomplete"
        )
    )
    result["recovery_required"] = bool(
        not structural_complete
        or (not trigger_complete and receive_queue_count <= 0)
    )
    return result


def classify_redpacket_runtime_routes(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Partition one fresh Runtime snapshot without granting a GUI action.

    The ``9033`` QMCH reward is rendered through a dedicated ``福`` entry.  It
    must never fall through to the ordinary chat-group/card flow merely because
    both surfaces use a similar badge.  This classifier is deliberately pure:
    it preserves the fresh UID/channel/sub-channel identity for the caller's
    dedicated handler and performs no navigation itself.
    """

    levels = snapshot.get("evidence_levels") or {}
    if not levels.get("structural") or not levels.get("semantic"):
        return {
            "status": "fail_closed",
            "route": "blocked",
            "reason": "fresh_runtime_identity_incomplete",
            "qmch_reward_items": [],
            "ordinary_chat_items": [],
            "deferred_ordinary_uids": [],
        }

    chat = (snapshot.get("sources") or {}).get("chat") or {}
    items = list(chat.get("items") or snapshot.get("items") or [])
    pending_uids = {
        str(uid)
        for uid in snapshot.get("pending_uids") or []
        if str(uid).strip()
    }
    qmch_items: list[dict[str, Any]] = []
    ordinary_items: list[dict[str, Any]] = []
    blocked_items: list[dict[str, Any]] = []

    for raw_item in items:
        item = dict(raw_item) if isinstance(raw_item, dict) else {}
        uid = str(item.get("uid") or "").strip()
        channel = item.get("channel")
        sub_id = item.get("sub_channel_id")
        special_signal = bool(
            item.get("event_type") == QMCH_REWARD_EVENT_TYPE
            or item.get("event_key") == QMCH_REWARD_EVENT_KEY
            or item.get("id") == QMCH_REWARD_CONFIG_ID
        )

        if special_signal:
            exact_qmch_identity = bool(
                uid
                and uid in pending_uids
                and item.get("event_type") == QMCH_REWARD_EVENT_TYPE
                and item.get("event_key") == QMCH_REWARD_EVENT_KEY
                and item.get("id") == QMCH_REWARD_CONFIG_ID
                and channel == QMCH_REWARD_CHANNEL
                and isinstance(sub_id, int)
                and not isinstance(sub_id, bool)
                and sub_id > 0
                and item.get("classification")
                == "special_event_gui_deep_check_candidate"
                and item.get("trigger_candidate") is True
                and item.get("action_authorized") is False
            )
            if exact_qmch_identity:
                qmch_items.append(item)
            else:
                # A partial 9033/5022/QMCH identity is excluded from the
                # ordinary flow even when it cannot authorize the special
                # handler.  Ambiguity therefore fails closed instead of being
                # reclassified as a normal red packet.
                blocked_items.append(item)
            continue

        if not uid or uid not in pending_uids or channel is None or sub_id is None:
            blocked_items.append(item)
            continue
        ordinary_items.append(item)

    if blocked_items:
        return {
            "status": "fail_closed",
            "route": "blocked",
            "reason": "runtime_route_identity_ambiguous",
            "qmch_reward_items": qmch_items,
            "ordinary_chat_items": ordinary_items,
            "blocked_items": blocked_items,
            "deferred_ordinary_uids": [],
        }

    qmch_route_keys = {
        (int(item["channel"]), int(item["sub_channel_id"]))
        for item in qmch_items
    }
    if len(qmch_route_keys) > 1:
        return {
            "status": "fail_closed",
            "route": "blocked",
            "reason": "multiple_qmch_routes_in_one_snapshot",
            "qmch_reward_items": qmch_items,
            "ordinary_chat_items": ordinary_items,
            "deferred_ordinary_uids": [],
        }

    if qmch_items:
        channel, sub_id = next(iter(qmch_route_keys))
        return {
            "status": "ready",
            "route": QMCH_REWARD_EVENT_KEY,
            "channel": channel,
            "sub_id": sub_id,
            "uids": [str(item["uid"]) for item in qmch_items],
            "qmch_reward_items": qmch_items,
            # Ordinary packets stay visible only as deferred identities.  They
            # are never candidates for this dedicated QMCH transaction.
            "ordinary_chat_items": [],
            "deferred_ordinary_uids": [
                str(item["uid"])
                for item in ordinary_items
            ],
        }

    return {
        "status": "ready",
        "route": "ordinary_chat" if ordinary_items else "none",
        "qmch_reward_items": [],
        "ordinary_chat_items": ordinary_items,
        "deferred_ordinary_uids": [],
    }


def inspect_redpacket_game_state() -> dict[str, Any]:
    facts = read_current_redpacket_state()
    chat = (facts.get("sources") or {}).get("chat") or {}
    pending_count = int(chat.get("pending_count") or 0)
    # RedbagData is the authoritative passive chat fact for patrol.  Do not
    # suppress a non-empty chat set with MainUI's transient display queue: the
    # latter can be empty while the current UI still exposes claimable red
    # packets.  Patrol only advances next_time; the Job independently repeats
    # its #395/#332/#30 visual guards before any click, so false-positive
    # candidates remain safe while false-negative scheduling is avoided.
    immediate_chat_pending = pending_count > 0
    return {
        "ok": bool(facts.get("ok")),
        "message": str(facts.get("reason") or ""),
        "facts": {"red_packet": facts},
        "due_task_ids": (
            [REDPACKET_SCHEDULER_TASK_ID]
            # The existing GUI Job handles chat red packets only. NPC-only marker
            # facts stay visible in patrol but must not trigger that Job.
            if facts.get("trigger_ready")
            and immediate_chat_pending
            else []
        ),
        "recovery_required": bool(facts.get("recovery_required")),
    }
