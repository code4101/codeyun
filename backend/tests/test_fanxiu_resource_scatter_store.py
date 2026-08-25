import json

import pytest

from backend.core.fanxiu.resource_scatter_store import ResourceScatterStore
from backend.core.fanxiu.resource_target_planner import ResourceActionPoint


def _point(phase, *, action="a", progress=100, inventory=10, pet=1701):
    aptitudes = (10, 20, 30, 40, 50) if phase == "before_action" else (10, 20, 30, 140, 50)
    return ResourceActionPoint(
        "event", pet, (1, 2, 3, 4), action, phase, 9, 1, progress,
        inventory, aptitudes, "2026-08-13T15:00:00+08:00",
    )


def test_store_persists_before_after_and_round_trips_five_aptitudes(tmp_path):
    path = tmp_path / "points.json"
    store = ResourceScatterStore(path)
    assert store.append(_point("before_action")) is True
    assert store.append(_point("after_action", progress=200, inventory=9)) is True
    loaded = store.list_points(instance_id="event", selected_pet_id=1701)
    assert [row.phase for row in loaded] == ["before_action", "after_action"]
    assert loaded[-1].aptitude_values == (10, 20, 30, 140, 50)
    assert json.loads(path.read_text("utf-8"))["schema_version"] == 1


def test_exact_retry_is_idempotent(tmp_path):
    store = ResourceScatterStore(tmp_path / "points.json")
    point = _point("before_action")
    assert store.append(point) is True
    assert store.append(point) is False
    assert len(store.list_points()) == 1


def test_conflicting_retry_fails_closed(tmp_path):
    store = ResourceScatterStore(tmp_path / "points.json")
    store.append(_point("before_action"))
    with pytest.raises(ValueError, match="键冲突"):
        store.append(_point("before_action", inventory=11))


def test_after_requires_persisted_before(tmp_path):
    store = ResourceScatterStore(tmp_path / "points.json")
    with pytest.raises(ValueError, match="缺少已持久化"):
        store.append(_point("after_action", progress=200, inventory=9))


def test_after_must_prove_inventory_and_progress_transition(tmp_path):
    store = ResourceScatterStore(tmp_path / "points.json")
    store.append(_point("before_action"))
    with pytest.raises(ValueError, match="有效单调结果"):
        store.append(_point("after_action", progress=100, inventory=10))


def test_pending_action_blocks_a_different_before_for_same_pet_context(tmp_path):
    store = ResourceScatterStore(tmp_path / "points.json")
    store.append(_point("before_action", action="a"))
    with pytest.raises(ValueError, match="已有未闭合动作"):
        store.append(_point("before_action", action="b"))


def test_pending_action_on_other_pet_does_not_block(tmp_path):
    store = ResourceScatterStore(tmp_path / "points.json")
    store.append(_point("before_action", action="a", pet=1701))
    assert store.append(_point("before_action", action="b", pet=1702)) is True
