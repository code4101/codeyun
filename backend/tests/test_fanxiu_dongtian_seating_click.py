import threading

import pytest

from backend.core.fanxiu.data_annotation.behavior_tree_runtime import (
    BehaviorTreeRuntimeRunner,
)
from backend.core.fanxiu.data_annotation.dongtian_seating_click import (
    build_dongtian_seating_place_authorization,
)
from backend.core.fanxiu.data_annotation.tasks.daily_foundation import (
    _DONGTIAN_PLACE_ANCHORS,
)


def _probe(
    *,
    mine_id=8,
    config_name=None,
    own_union_id=42,
    mine_union_id=42,
    pid=7,
    ticks=9,
):
    if config_name is None:
        config_name = _DONGTIAN_PLACE_ANCHORS[7]
    return {
        "available": True,
        "complete": True,
        "status": "ready",
        "own_union_id": own_union_id,
        "occupied_mine_ids": [],
        "excluded_mine_ids": [],
        "mines_place_config_sha256": "cfg-v1",
        "selected_mine": {
            "id": mine_id,
            "config_id": mine_id,
            "config_name": config_name,
            "config_group": 3,
            "config_pos_y": -1609,
            "cross_union_id": mine_union_id,
            "friendly": mine_union_id == own_union_id,
        },
        "evidence": {"pid": pid, "process_start_ticks": ticks},
    }


def _finish(generator):
    try:
        while True:
            next(generator)
    except StopIteration as done:
        return done.value


def _authorized_probe():
    probe = _probe()
    authorization = build_dongtian_seating_place_authorization(
        probe,
    )
    return probe, authorization


def _run_wrapper(monkeypatch, authorization, fresh_probe):
    runner = BehaviorTreeRuntimeRunner()
    locator_calls = []

    def locator(_runtime, _stop_event, names, **kwargs):
        locator_calls.append((list(names), dict(kwargs)))
        if False:
            yield None
        return names[0]

    monkeypatch.setattr(runner, "_daily_dongtian_click_place", locator)
    generator = runner._daily_dongtian_click_seating_target(
        object(),
        threading.Event(),
        authorization,
        max_scrolls=3,
        probe_reader=lambda **_kwargs: fresh_probe,
    )
    return generator, locator_calls


def test_seating_click_rejects_bare_place_name_without_locator_call(monkeypatch):
    generator, calls = _run_wrapper(monkeypatch, "[洞天]月虹梁", _probe())

    with pytest.raises(RuntimeError, match="裸地点名"):
        _finish(generator)
    assert calls == []


def test_place_authorization_uses_sparse_config_id_name_without_dense_index():
    probe = _probe(mine_id=19, config_name="[福地]蛰龙窟")

    authorization = build_dongtian_seating_place_authorization(probe)

    assert authorization["mine_id"] == 19
    assert authorization["config_id"] == 19
    assert authorization["place_name"] == "[福地]蛰龙窟"


def test_seating_click_rejects_nonfriendly_visible_mine_without_locator_call(monkeypatch):
    _probe_before, authorization = _authorized_probe()
    fresh = _probe(mine_union_id=77)

    generator, calls = _run_wrapper(monkeypatch, authorization, fresh)
    with pytest.raises(RuntimeError, match="selected_mine_nonfriendly"):
        _finish(generator)
    assert calls == []


def test_seating_click_rejects_owner_flip_without_locator_call(monkeypatch):
    _probe_before, authorization = _authorized_probe()
    fresh = _probe(own_union_id=77, mine_union_id=77)

    generator, calls = _run_wrapper(monkeypatch, authorization, fresh)
    with pytest.raises(RuntimeError, match="own_union_changed"):
        _finish(generator)
    assert calls == []


def test_seating_click_rejects_selected_mine_change_without_locator_call(monkeypatch):
    _probe_before, authorization = _authorized_probe()
    fresh = _probe(mine_id=9)

    generator, calls = _run_wrapper(monkeypatch, authorization, fresh)
    with pytest.raises(RuntimeError, match="selected_mine_changed"):
        _finish(generator)
    assert calls == []


def test_seating_click_rejects_tampered_place_name_without_locator_call(monkeypatch):
    fresh, authorization = _authorized_probe()
    authorization["place_name"] = "[洞天]伪造地点"

    generator, calls = _run_wrapper(monkeypatch, authorization, fresh)
    with pytest.raises(RuntimeError, match="place_name_mismatch"):
        _finish(generator)
    assert calls == []


def test_seating_click_rejects_process_restart_without_locator_call(monkeypatch):
    _probe_before, authorization = _authorized_probe()
    fresh = _probe(ticks=10)

    generator, calls = _run_wrapper(monkeypatch, authorization, fresh)
    with pytest.raises(RuntimeError, match="process_identity_changed"):
        _finish(generator)
    assert calls == []


def test_seating_click_rejects_target_that_became_occupied(monkeypatch):
    _probe_before, authorization = _authorized_probe()
    fresh = _probe()
    fresh["occupied_mine_ids"] = [8]

    generator, calls = _run_wrapper(monkeypatch, authorization, fresh)
    with pytest.raises(RuntimeError, match="target_mine_already_occupied"):
        _finish(generator)
    assert calls == []


def test_seating_click_calls_low_level_locator_once_after_fresh_exact_match(monkeypatch):
    fresh, authorization = _authorized_probe()

    generator, calls = _run_wrapper(monkeypatch, authorization, fresh)
    assert _finish(generator) == _DONGTIAN_PLACE_ANCHORS[7]
    assert calls == [
        (
            [_DONGTIAN_PLACE_ANCHORS[7]],
            {
                "max_scrolls": 3,
                "scroll_directions": ("down", "up"),
                "task_label": "洞天_座位研究",
            },
        )
    ]


def test_enemy_clear_can_still_reuse_low_level_locator_with_explicit_wrapper(monkeypatch):
    runner = BehaviorTreeRuntimeRunner()
    calls = []

    def locator(_runtime, _stop_event, names, **kwargs):
        calls.append((list(names), dict(kwargs)))
        if False:
            yield None
        return names[0]

    monkeypatch.setattr(runner, "_daily_dongtian_click_place", locator)
    result = _finish(
        runner._daily_dongtian_click_first_enemy_place(
            object(), threading.Event(), ["[洞天]月虹梁"], max_scrolls=2
        )
    )

    assert result == "[洞天]月虹梁"
    assert calls[0][1]["task_label"] == "洞天_行动力"
