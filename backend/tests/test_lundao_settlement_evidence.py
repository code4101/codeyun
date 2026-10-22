"""Deterministic evidence contracts only; no simulated game/scene recognition."""
from types import SimpleNamespace
import threading

import pytest

from backend.core.fanxiu.data_annotation.tasks.lundao_execution import LundaoTaskMixin
from backend.core.fanxiu.data_annotation.tasks.lundao_settlement import (
    capture_pending_replay, replay_proves_defeat, settled_unseated_entry,
)


def finish(generator):
    while True:
        try:
            next(generator)
        except StopIteration as result:
            return result.value


def returned(value):
    yield from ()
    return value


def test_dialogue_captures_before_native_callback_clears(monkeypatch):
    from backend.core.fanxiu.instrumentation import lundao
    replay = {"available": True, "pending": True, "win": False}
    monkeypatch.setattr(lundao, "read_lundao_snapshot", lambda: {"battle_replay": replay})
    runner = LundaoTaskMixin()
    runner._log = lambda *args: None

    def clear_dialogue(*args, **kwargs):
        replay.clear()  # Native callback clears the original object.
        yield from ()
        return 1

    context = SimpleNamespace(advance_dialogue=clear_dialogue,
                             wait_scene=lambda *a, **k: returned(53))
    result = finish(runner._advance_daily_lundao_kick_dialogue(
        context, start_scene=303, transaction_id="current"))
    assert replay == {}
    assert replay_proves_defeat(result["battle_replay"], "current")
    assert not replay_proves_defeat(result["battle_replay"], "next-action")


def test_room_action_passes_same_transaction_and_witness():
    runner = LundaoTaskMixin()
    captured = {}

    def kick(*args, transaction_id, **kwargs):
        captured["tx"] = transaction_id
        captured["witness"] = capture_pending_replay(
            {"available": True, "pending": True, "win": False}, transaction_id)
        return returned({"scene_id": 53, "battle_replay": captured["witness"]})

    def close(*args, transaction_id, battle_replay, expected_room_id, **kwargs):
        assert transaction_id == captured["tx"]
        assert battle_replay is captured["witness"]
        assert expected_room_id == 14
        return returned("battle_lost")

    runner._run_daily_lundao_kick_for_seat_strategy = kick
    runner._complete_daily_lundao_seat_and_leave = close
    context = SimpleNamespace(wait_scene=lambda *a, **k: returned(
        SimpleNamespace(scene_id=297, score=100, frame_data_url="")))
    assert finish(runner._run_daily_lundao_room_action(
        context, threading.Event(), opportunity={"action": "kick", "room_id": 14,
                                               "target": {"id": 1}})) == "battle_lost"


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


@pytest.mark.parametrize("seated,expected", [(False, "unseated"), (True, "success")])
def test_closure_uses_current_seat_instead_of_old_53_label(monkeypatch, seated, expected):
    from backend.core.fanxiu.instrumentation import lundao
    from backend.core.fanxiu.data_annotation.tasks import lundao_execution
    facts = {"available": True, "complete": True, "seated": seated, "room_id": 14 if seated else None,
             "battle_replay": {"available": True, "pending": False}}
    monkeypatch.setattr(lundao, "read_lundao_snapshot", lambda: facts)
    times = iter([0, 16])
    monkeypatch.setattr(lundao_execution.time, "monotonic", lambda: next(times))
    runner = LundaoTaskMixin()
    runner._prefer_daily_lundao_seat_choice_scene = lambda *args: (53, 100)
    runner._raise_if_stopped = lambda *args: None
    runner._log = lambda *args: None
    runner._require_daily_lundao_seated_or_completed = lambda: None
    exits = []
    runner._leave_daily_lundao_seated_for_daily_entry = lambda *args: (exits.append(args[-1]) or returned(None))
    assert finish(runner._complete_daily_lundao_seat_and_leave(
        object(), threading.Event(), 53)) == expected
    assert exits == [53]


def test_closure_cannot_consume_previous_action_defeat(monkeypatch):
    from backend.core.fanxiu.instrumentation import lundao
    from backend.core.fanxiu.data_annotation.tasks import lundao_execution
    monkeypatch.setattr(lundao, "read_lundao_snapshot", lambda: {
        "available": True, "complete": True, "seated": False,
        "battle_replay": {"available": True, "pending": False}})
    times = iter([0, 181, 181])
    monkeypatch.setattr(lundao_execution.time, "monotonic", lambda: next(times))
    runner = LundaoTaskMixin()
    runner._prefer_daily_lundao_seat_choice_scene = lambda *args: (53, 100)
    runner._raise_if_stopped = lambda *args: None
    old = capture_pending_replay({"available": True, "pending": True, "win": False}, "previous")
    with pytest.raises(RuntimeError, match="expected_room_id=14"):
        finish(runner._complete_daily_lundao_seat_and_leave(
            object(), threading.Event(), 53, expected_room_id=14,
            battle_replay=old, transaction_id="current"))

