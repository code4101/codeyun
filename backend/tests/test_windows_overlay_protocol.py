from __future__ import annotations

import os

import pytest

from backend.core.windows_overlay.protocol import (
    normalize_scene_document,
    read_overlay_preferences,
    read_scene_document,
    write_overlay_preferences,
    write_scene_document,
)


def test_scene_protocol_normalizes_and_orders_elements() -> None:
    document = normalize_scene_document({
        "revision": 3,
        "target": {"title_contains": "Example"},
        "viewport": {"width": 800, "height": 600},
        "elements": [
            {"id": "front", "type": "text", "z_index": 2, "text": "你好"},
            {"id": "back", "type": "rect", "z_index": 1, "style": {"background": "#FFFFFF"}},
            {"type": "unsupported"},
        ],
    })

    assert document["protocol_version"] == 1
    assert document["target"]["only_when_foreground"] is True
    assert document["viewport"]["coordinate_mode"] == "exact"
    assert [element["id"] for element in document["elements"]] == ["back", "front"]


def test_scene_protocol_round_trips_atomically(tmp_path) -> None:
    path = tmp_path / "scene.json"
    written = write_scene_document({
        "revision": 5,
        "target": {"hwnd": 123},
        "viewport": {"width": 1440, "height": 2512},
        "elements": [],
    }, path)

    assert read_scene_document(path) == written
    assert written["producer_id"] == "anonymous"
    assert written["ttl_ms"] == 750


def test_scene_protocol_retries_transient_replace_failure(tmp_path, monkeypatch) -> None:
    path = tmp_path / "scene.json"
    real_replace = os.replace
    attempts = 0

    def flaky_replace(source, destination) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PermissionError(5, "sharing violation")
        real_replace(source, destination)

    monkeypatch.setattr("backend.core.windows_overlay.protocol.os.replace", flaky_replace)
    write_scene_document({
        "revision": 8,
        "producer_id": "test-producer",
        "ttl_ms": 500,
        "target": {"hwnd": 123},
        "viewport": {"width": 800, "height": 600},
        "elements": [],
    }, path)

    assert attempts == 2
    assert read_scene_document(path)["producer_id"] == "test-producer"


def test_scene_protocol_rejects_missing_target() -> None:
    with pytest.raises(ValueError, match="requires hwnd or title_contains"):
        normalize_scene_document({
            "viewport": {"width": 800, "height": 600},
            "elements": [],
        })


def test_scene_protocol_keeps_both_window_identity_fields() -> None:
    document = normalize_scene_document({
        "target": {"hwnd": 123, "title_contains": "Expected document"},
        "viewport": {"width": 800, "height": 600},
        "elements": [],
    })

    assert document["target"]["hwnd"] == 123
    assert document["target"]["title_contains"] == "Expected document"


def test_overlay_preferences_default_to_enabled_and_round_trip(tmp_path) -> None:
    path = tmp_path / "preferences.json"

    assert read_overlay_preferences(path) == {
        "enhancement_enabled": True,
        "click_through_enabled": True,
    }
    assert write_overlay_preferences({
        "enhancement_enabled": False,
        "click_through_enabled": False,
    }, path) == {
        "enhancement_enabled": False,
        "click_through_enabled": False,
    }
    assert read_overlay_preferences(path) == {
        "enhancement_enabled": False,
        "click_through_enabled": False,
    }


def test_scene_protocol_normalizes_hover_popover() -> None:
    document = normalize_scene_document({
        "target": {"hwnd": 123},
        "viewport": {"width": 800, "height": 600},
        "elements": [{
            "id": "abstract-help",
            "type": "popover",
            "x": 80,
            "y": 120,
            "width": 28,
            "height": 28,
            "marker": "?",
            "title": "摘要翻译",
            "text": "中文摘要",
            "popup": {"width": 420},
        }],
    })

    popover = document["elements"][0]
    assert popover["type"] == "popover"
    assert popover["marker"] == "?"
    assert popover["popup"]["width"] == 420
