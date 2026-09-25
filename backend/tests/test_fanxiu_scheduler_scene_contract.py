import json

import pytest

from backend.core.fanxiu.data_annotation.state import (
    normalize_kernel_scheduler_current_scene,
    normalize_kernel_scheduler_display,
    persist_kernel_scheduler_status,
    read_data_annotation_world_facts,
    read_kernel_scheduler_status,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), (34, 34), ("34", 34), ("success", None), ([], None), (float("inf"), None), (float("nan"), None)],
)
def test_current_scene_has_one_integer_or_none_contract(value, expected):
    status = {"current_scene": value, "message": "业务结果"}
    assert normalize_kernel_scheduler_current_scene(status) is status
    assert status["current_scene"] == expected
    assert status["message"] == "业务结果"
    normalize_kernel_scheduler_display(status)
    assert status["current_scene"] == expected


def test_legacy_status_read_normalizes_without_rewriting_file(tmp_path):
    path = tmp_path / "execution.json"
    path.write_text('{"current_scene":"success","running":false}', encoding="utf-8")
    before = path.read_bytes()
    assert read_kernel_scheduler_status(path)["current_scene"] is None
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"current_scene": "success"}, None),
        ({"context": {"current_scene": "success"}}, None),
        ({"current_scene": "34"}, 34),
        ({"context": {"current_scene": "34"}}, 34),
    ],
)
def test_legacy_world_context_uses_same_scene_contract_without_write(tmp_path, raw, expected):
    path = tmp_path / "world.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    before = path.read_bytes()
    assert read_data_annotation_world_facts(path)["context"]["current_scene"] == expected
    assert path.read_bytes() == before


def test_persistence_does_not_create_discovery_for_invalid_scene(tmp_path):
    execution = tmp_path / "execution.json"
    facts = tmp_path / "facts.json"
    persist_kernel_scheduler_status(execution, facts, {"current_scene": "success", "status": "success"})
    assert json.loads(execution.read_text(encoding="utf-8"))["current_scene"] is None
    snapshot = read_data_annotation_world_facts(facts)
    assert snapshot["context"]["current_scene"] is None
    assert "success" not in snapshot["discoveries"]["scene"]

    persist_kernel_scheduler_status(execution, facts, {"current_scene": "34", "status": "success"})
    snapshot = read_data_annotation_world_facts(facts)
    assert snapshot["context"]["current_scene"] == 34
    assert snapshot["discoveries"]["scene"]["34"]["scene"] == 34
