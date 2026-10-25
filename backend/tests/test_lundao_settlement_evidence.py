"""Pure settlement ownership contracts; actual scene flow needs live acceptance."""
import pytest
from backend.core.fanxiu.data_annotation.tasks.lundao_settlement import (
    capture_pending_replay, replay_proves_defeat, settled_unseated_entry,
)


def test_capture_survives_source_clear_and_retains_transaction():
    replay = {"available": True, "pending": True, "win": False}
    evidence = capture_pending_replay(replay, "current")
    replay.clear()
    assert replay_proves_defeat(evidence, "current")
    assert not replay_proves_defeat(evidence, "next-action")


@pytest.mark.parametrize("replay", [None, {}, {"win": False},
    {"available": True, "pending": False, "win": False},
    {"available": True, "pending": True, "win": True}])
def test_missing_or_nonpending_evidence_cannot_prove_defeat(replay):
    evidence = capture_pending_replay(replay or {}, "current")
    assert not replay_proves_defeat(evidence, "current")


def test_old_proof_and_unbound_transaction_rejected():
    evidence = capture_pending_replay({"available": True, "pending": True, "win": False}, "old")
    assert not replay_proves_defeat(evidence, "new")
    assert not replay_proves_defeat(evidence, "")


def test_old_53_unseated_only_after_wait_and_no_live_replay():
    facts = {"available": True, "complete": True, "seated": False,
             "battle_replay": {"available": True, "pending": False}}
    assert settled_unseated_entry(facts, is_entry=True, wait_expired=True)
    assert not settled_unseated_entry(facts, is_entry=False, wait_expired=True)
    assert not settled_unseated_entry(facts, is_entry=True, wait_expired=False)
    assert not settled_unseated_entry({**facts, "seated": True}, is_entry=True, wait_expired=True)
    assert not settled_unseated_entry({**facts, "battle_replay": {"available": True, "pending": True}},
                                     is_entry=True, wait_expired=True)


