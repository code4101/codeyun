"""Cell-local settlement evidence; never reuse it across seat actions or attempts.

The battle branch awaits natural game coverage. These pure rules verify evidence
ownership, not the game's timing, scene recognition or battle outcome.
"""
from collections.abc import Mapping
from typing import Any


def capture_pending_replay(replay: Mapping[str, Any], transaction_id: str) -> dict[str, Any] | None:
    """Copy the pending outcome before the client's dialogue callback clears it."""
    if not transaction_id or replay.get("available") is not True or replay.get("pending") is not True:
        return None
    if not isinstance(replay.get("win"), bool):
        return None
    return {**replay, "transaction_id": transaction_id}


def replay_proves_defeat(evidence: Mapping[str, Any] | None, transaction_id: str) -> bool:
    """Only a pending defeat captured by this action is admissible."""
    return bool(evidence and transaction_id and evidence.get("transaction_id") == transaction_id
                and evidence.get("available") is True and evidence.get("pending") is True
                and evidence.get("win") is False)


def settled_unseated_entry(facts: Mapping[str, Any], *, is_entry: bool, wait_expired: bool) -> bool:
    """Old #53 may close without spending only after the observation budget.

    A current seat or a live replay always prevents this recovery branch.
    """
    replay = facts.get("battle_replay") or {}
    return bool(is_entry and wait_expired and facts.get("available") is True
                and facts.get("complete") is True and facts.get("seated") is False
                and replay.get("available") is True and replay.get("pending") is False)
