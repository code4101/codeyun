from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from pyxllib.prog import read_json_state_dict, write_json_state

from backend.core.settings import get_settings


FANXIU_WINDOWS_INFO_WINDOW_HEARTBEAT_TTL_SECONDS = 3.0
FANXIU_INFO_WINDOW_DECISION_SCOPE = "decision"
FANXIU_INFO_WINDOW_DEFAULT_SETTINGS = {
    "enabled": True,
    "show_scene_id": True,
    "show_scene_score": True,
    "show_scene_identity_shapes": True,
    "show_all_shapes": False,
}


def fanxiu_info_window_state_path() -> Path:
    path = get_settings().data_dir / "fanxiu" / "data-annotation" / "runtime" / "info_window_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def fanxiu_windows_info_window_heartbeat_path() -> Path:
    path = get_settings().data_dir / "fanxiu" / "data-annotation" / "runtime" / "windows_info_window.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def fanxiu_info_window_settings_path() -> Path:
    path = get_settings().data_dir / "fanxiu" / "data-annotation" / "runtime" / "info_window_settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def fanxiu_info_window_user_settings_path(user_id: int) -> Path:
    path = (
        get_settings().data_dir
        / "fanxiu"
        / "data-annotation"
        / "runtime"
        / "info-window-users"
        / f"user_{int(user_id)}.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def normalize_fanxiu_info_window_settings(payload: dict[str, Any] | None = None) -> dict[str, bool]:
    source = payload if isinstance(payload, dict) else {}
    settings = {
        key: bool(source.get(key, default))
        for key, default in FANXIU_INFO_WINDOW_DEFAULT_SETTINGS.items()
    }
    return settings


def read_fanxiu_info_window_settings() -> dict[str, bool]:
    try:
        payload = dict(read_json_state_dict(fanxiu_info_window_settings_path()))
    except Exception:
        payload = {}
    return normalize_fanxiu_info_window_settings(payload)


def write_fanxiu_info_window_settings(payload: dict[str, Any]) -> dict[str, bool]:
    settings = normalize_fanxiu_info_window_settings(payload)
    write_json_state(fanxiu_info_window_settings_path(), settings)
    return settings


def read_fanxiu_info_window_user_settings(user_id: int) -> dict[str, bool]:
    """Read one user's preference, migrating the legacy machine setting once."""
    path = fanxiu_info_window_user_settings_path(user_id)
    if path.is_file():
        try:
            return normalize_fanxiu_info_window_settings(dict(read_json_state_dict(path)))
        except Exception:
            pass
    settings = read_fanxiu_info_window_settings()
    write_json_state(path, settings)
    return settings


def write_fanxiu_info_window_user_settings(user_id: int, payload: dict[str, Any]) -> dict[str, bool]:
    settings = normalize_fanxiu_info_window_settings(payload)
    write_json_state(fanxiu_info_window_user_settings_path(user_id), settings)
    return settings


def format_fanxiu_scene_text(
    scene_id: int | None,
    score: float,
    *,
    asset_directory: str = "",
    show_scene_id: bool = True,
    show_scene_score: bool = True,
) -> str:
    scene_text = f"#{int(scene_id)}" if scene_id is not None else "unknown"
    parts: list[str] = []
    if show_scene_id:
        directory = str(asset_directory or "").strip().strip("/")
        parts.append(f"{directory} {scene_text}" if directory else scene_text)
    if show_scene_score:
        parts.append(f"{max(0.0, float(score or 0.0)):.0f}%")
    return " ".join(parts)


def format_fanxiu_observation_age(
    observed_at: float | int | None,
    *,
    now: float | int | None = None,
) -> str:
    """Format the integer age of one recognition result for the overlay."""

    try:
        observed_timestamp = float(observed_at or 0.0)
        current_timestamp = float(now if now is not None else time.time())
    except (TypeError, ValueError):
        return ""
    if observed_timestamp <= 0.0:
        return ""
    elapsed_seconds = max(0, int(current_timestamp - observed_timestamp))
    if elapsed_seconds < 60:
        return f"{elapsed_seconds}s"
    return f"{elapsed_seconds // 60}min"


class FanxiuInfoWindowState:
    """Process-local latest scene observation with a persisted read-only snapshot."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._revision = 0
        self._latest: dict[str, Any] = {}

    def publish(
        self,
        scene_id: int | None,
        score: float,
        *,
        source: str,
        scope: str = "probe",
        entry_id: str = "",
        asset_generation: int = 0,
        frame_id: str = "",
        asset_directory: str = "",
        boxes: list[dict[str, Any]] | None = None,
        all_shape_boxes: list[dict[str, Any]] | None = None,
        frame_width: int = 0,
        frame_height: int = 0,
        captured_at: float | None = None,
        committed_at: float | None = None,
        observed_at: float | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        capture_timestamp = float(
            captured_at
            if captured_at is not None
            else (observed_at if observed_at is not None else time.time())
        )
        commit_timestamp = float(committed_at if committed_at is not None else time.time())
        normalized_scope = str(scope or "").strip().lower()
        with self._lock:
            payload = {
                "ok": True,
                "name": "凡修信息窗",
                "scene_id": int(scene_id) if scene_id is not None else None,
                "score": round(max(0.0, float(score or 0.0)), 3),
                "asset_directory": str(asset_directory or "").strip().strip("/"),
                "text": format_fanxiu_scene_text(
                    scene_id,
                    score,
                    asset_directory=asset_directory,
                ),
                "boxes": [
                    {
                        "x": round(float(box.get("x") or 0.0), 3),
                        "y": round(float(box.get("y") or 0.0), 3),
                        "w": round(max(0.0, float(box.get("w") or 0.0)), 3),
                        "h": round(max(0.0, float(box.get("h") or 0.0)), 3),
                    }
                    for box in (boxes or [])
                    if isinstance(box, dict)
                ],
                "all_shape_boxes": [
                    {
                        "x": round(float(box.get("x") or 0.0), 3),
                        "y": round(float(box.get("y") or 0.0), 3),
                        "w": round(max(0.0, float(box.get("w") or 0.0)), 3),
                        "h": round(max(0.0, float(box.get("h") or 0.0)), 3),
                    }
                    for box in (all_shape_boxes or [])
                    if isinstance(box, dict)
                ],
                "frame_width": max(0, int(frame_width or 0)),
                "frame_height": max(0, int(frame_height or 0)),
                "source": str(source or "runtime"),
                "scope": normalized_scope,
                "entry_id": str(entry_id or ""),
                "asset_generation": max(0, int(asset_generation or 0)),
                "frame_id": str(frame_id or ""),
                "captured_at": capture_timestamp,
                "committed_at": commit_timestamp,
                # Compatibility alias for older readers. Its meaning is now
                # explicitly the frame capture time, never the commit time.
                "observed_at": capture_timestamp,
            }
            # The renderer is a projection of the scene observation which the
            # behavior tree finally accepted for its current decision.  Layer0
            # checks, route ranking and other candidate probes are deliberately
            # non-authoritative: publishing their miss (or transient hit) must
            # not erase the last committed scene shown in the title.
            if normalized_scope != FANXIU_INFO_WINDOW_DECISION_SCOPE:
                return {
                    **payload,
                    "revision": self._revision,
                    "committed": False,
                }
            self._revision += 1
            payload.update({
                "revision": self._revision,
                "committed": True,
            })
            self._latest = payload
        if persist:
            try:
                write_json_state(fanxiu_info_window_state_path(), payload)
            except Exception:
                # Rendering is observational. A snapshot write failure must never
                # turn a valid behavior-tree scene recognition into a failed Cell.
                pass
        return dict(payload)

    def read(self) -> dict[str, Any]:
        with self._lock:
            if self._latest:
                return dict(self._latest)
        path = fanxiu_info_window_state_path()
        if not path.is_file():
            return {}
        try:
            return dict(read_json_state_dict(path))
        except Exception:
            return {}


fanxiu_info_window_state = FanxiuInfoWindowState()


def publish_fanxiu_scene_recognition(
    scene_id: int | None,
    score: float,
    *,
    scope: str = "probe",
    source: str = "runtime",
    entry_id: str = "",
    asset_generation: int = 0,
    frame_id: str = "",
    asset_directory: str = "",
    boxes: list[dict[str, Any]] | None = None,
    all_shape_boxes: list[dict[str, Any]] | None = None,
    frame_width: int = 0,
    frame_height: int = 0,
    captured_at: float | None = None,
    committed_at: float | None = None,
) -> dict[str, Any]:
    return fanxiu_info_window_state.publish(
        scene_id,
        score,
        source=source,
        scope=scope,
        entry_id=entry_id,
        asset_generation=asset_generation,
        frame_id=frame_id,
        asset_directory=asset_directory,
        boxes=boxes,
        all_shape_boxes=all_shape_boxes,
        frame_width=frame_width,
        frame_height=frame_height,
        captured_at=captured_at,
        committed_at=committed_at,
    )


class FanxiuWindowsInfoWindowClient:
    """Read the Windows renderer heartbeat without coupling it to the Kernel."""

    def __init__(self, *, heartbeat_path: Path | None = None) -> None:
        self.heartbeat_path = heartbeat_path

    def available(self, *, now: float | None = None) -> bool:
        payload = self._read_heartbeat()
        timestamp = float(now if now is not None else time.time())
        return bool(payload.get("visible")) and self._heartbeat_is_fresh(payload, now=timestamp)

    def running(self, *, now: float | None = None) -> bool:
        payload = self._read_heartbeat()
        timestamp = float(now if now is not None else time.time())
        return bool(payload.get("running", payload.get("pid"))) and self._heartbeat_is_fresh(payload, now=timestamp)

    def _read_heartbeat(self) -> dict[str, Any]:
        path = self.heartbeat_path or fanxiu_windows_info_window_heartbeat_path()
        try:
            return dict(read_json_state_dict(path))
        except Exception:
            return {}

    @staticmethod
    def _heartbeat_is_fresh(payload: dict[str, Any], *, now: float) -> bool:
        updated_at = float(payload.get("updated_at") or 0.0)
        return 0.0 <= now - updated_at <= FANXIU_WINDOWS_INFO_WINDOW_HEARTBEAT_TTL_SECONDS

    def status(self) -> dict[str, Any]:
        payload = self._read_heartbeat()
        return {
            **payload,
            "running": self.running(),
            "available": self.available(),
        }


fanxiu_windows_info_window_client = FanxiuWindowsInfoWindowClient()


def read_fanxiu_info_window_state() -> dict[str, Any]:
    payload = fanxiu_info_window_state.read()
    return {
        **payload,
        "renderer": {
            "windows": fanxiu_windows_info_window_client.status(),
        },
        "settings": read_fanxiu_info_window_settings(),
    }
