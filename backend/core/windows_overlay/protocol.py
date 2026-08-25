from __future__ import annotations

"""Declarative protocol shared by Windows overlay producers and renderer."""

import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from backend.core.temp_paths import codeyun_temp_root


PROTOCOL_VERSION = 1
DEFAULT_SCENE_TTL_MS = 750
SUPPORTED_ELEMENT_TYPES = {"rect", "text", "popover"}
_COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")


def default_scene_path() -> Path:
    return codeyun_temp_root("windows-overlay-runtime") / "scene.json"


def default_heartbeat_path() -> Path:
    return codeyun_temp_root("windows-overlay-runtime") / "heartbeat.json"


def default_preferences_path() -> Path:
    return codeyun_temp_root("windows-overlay-runtime") / "preferences.json"


def _atomic_write_json(
    destination: Path,
    value: Any,
    *,
    retry_timeout: float = 0.35,
) -> None:
    """Atomically write JSON and tolerate transient Windows sharing violations."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        deadline = time.monotonic() + max(0.0, retry_timeout)
        delay = 0.005
        while True:
            try:
                os.replace(temporary_name, destination)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(delay)
                delay = min(0.05, delay * 2)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def read_overlay_preferences(path: Path | None = None) -> dict[str, Any]:
    source = Path(path or default_preferences_path())
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        raw = {}
    return {
        "enhancement_enabled": bool(raw.get("enhancement_enabled", True)),
        "click_through_enabled": bool(raw.get("click_through_enabled", True)),
    }


def write_overlay_preferences(value: Any, path: Path | None = None) -> dict[str, Any]:
    destination = Path(path or default_preferences_path())
    raw = value if isinstance(value, dict) else {}
    preferences = {
        "enhancement_enabled": bool(raw.get("enhancement_enabled", True)),
        "click_through_enabled": bool(raw.get("click_through_enabled", True)),
    }
    _atomic_write_json(destination, preferences)
    return preferences


def _number(value: Any, *, default: float = 0.0, minimum: float | None = None) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = default
    if minimum is not None:
        result = max(minimum, result)
    return result


def _color(value: Any, *, default: str) -> str:
    text = str(value or "").strip()
    return text if _COLOR_PATTERN.fullmatch(text) else default


def _normalize_target(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    hwnd = int(_number(raw.get("hwnd"), minimum=0))
    title_contains = str(raw.get("title_contains") or "").strip()
    if not hwnd and not title_contains:
        raise ValueError("overlay target requires hwnd or title_contains")
    return {
        "hwnd": hwnd or None,
        "title_contains": title_contains,
        "area": "window" if raw.get("area") == "window" else "client",
        "only_when_foreground": bool(raw.get("only_when_foreground", True)),
    }


def _normalize_element(value: Any, index: int) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    element_type = str(value.get("type") or "").strip().lower()
    if element_type not in SUPPORTED_ELEMENT_TYPES:
        return None
    element: dict[str, Any] = {
        "id": str(value.get("id") or f"element-{index}"),
        "type": element_type,
        "x": _number(value.get("x")),
        "y": _number(value.get("y")),
        "width": _number(value.get("width"), minimum=0),
        "height": _number(value.get("height"), minimum=0),
        "z_index": int(_number(value.get("z_index"))),
    }
    style = value.get("style") if isinstance(value.get("style"), dict) else {}
    element["style"] = {
        "color": _color(style.get("color"), default="#FFFFFF"),
        "background": _color(style.get("background"), default="#010203"),
        "stroke": _color(style.get("stroke"), default="#FFFFFF"),
        "stroke_width": _number(style.get("stroke_width"), default=2, minimum=0),
        "font_size": int(_number(style.get("font_size"), default=16, minimum=8)),
        "font_weight": "bold" if style.get("font_weight") == "bold" else "normal",
        "padding": int(_number(style.get("padding"), default=6, minimum=0)),
    }
    if element_type in {"text", "popover"}:
        element["text"] = str(value.get("text") or "")
    if element_type == "popover":
        popup = value.get("popup") if isinstance(value.get("popup"), dict) else {}
        element["marker"] = str(value.get("marker") or "?")[:2]
        element["title"] = str(value.get("title") or "")
        element["popup"] = {
            "width": _number(popup.get("width"), default=460, minimum=180),
            "offset_x": _number(popup.get("offset_x"), default=10),
            "offset_y": _number(popup.get("offset_y"), default=0),
            "color": _color(popup.get("color"), default="#1F2937"),
            "background": _color(popup.get("background"), default="#FFFFFF"),
            "stroke": _color(popup.get("stroke"), default="#D0D5DD"),
            "font_size": int(_number(popup.get("font_size"), default=13, minimum=8)),
            "padding": int(_number(popup.get("padding"), default=14, minimum=0)),
        }
    return element


def normalize_scene_document(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    version = int(_number(raw.get("protocol_version"), default=PROTOCOL_VERSION, minimum=1))
    if version != PROTOCOL_VERSION:
        raise ValueError(f"unsupported overlay protocol_version: {version}")
    viewport = raw.get("viewport") if isinstance(raw.get("viewport"), dict) else {}
    width = int(_number(viewport.get("width"), minimum=1))
    height = int(_number(viewport.get("height"), minimum=1))
    if width <= 0 or height <= 0:
        raise ValueError("overlay viewport requires positive width and height")
    elements = [
        normalized
        for index, item in enumerate(raw.get("elements") or [])
        if (normalized := _normalize_element(item, index)) is not None
    ]
    elements.sort(key=lambda item: (item["z_index"], item["id"]))
    return {
        "protocol_version": PROTOCOL_VERSION,
        "revision": int(_number(raw.get("revision"), minimum=0)),
        "channel": str(raw.get("channel") or "default"),
        "producer_id": str(raw.get("producer_id") or "anonymous"),
        "published_at": _number(raw.get("published_at"), default=time.time(), minimum=0),
        "ttl_ms": int(_number(raw.get("ttl_ms"), default=DEFAULT_SCENE_TTL_MS, minimum=100)),
        "target": _normalize_target(raw.get("target")),
        "viewport": {
            "width": width,
            "height": height,
            "coordinate_mode": "scale" if viewport.get("coordinate_mode") == "scale" else "exact",
        },
        "elements": elements,
    }


def read_scene_document(path: Path | None = None) -> dict[str, Any]:
    source = Path(path or default_scene_path())
    return normalize_scene_document(json.loads(source.read_text(encoding="utf-8")))


def write_scene_document(value: Any, path: Path | None = None) -> dict[str, Any]:
    destination = Path(path or default_scene_path())
    document = normalize_scene_document(value)
    _atomic_write_json(destination, document)
    return document
