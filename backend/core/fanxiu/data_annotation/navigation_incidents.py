from __future__ import annotations

import base64
import hashlib
import io
import json
import time
import uuid
from copy import deepcopy
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from backend.core.fanxiu.data_annotation.state import write_data_annotation_json
from backend.core.fanxiu.data_annotation.storage import data_annotation_entry_dir
from backend.core.fanxiu.data_annotation.unknown_recovery import build_unknown_evidence


NAVIGATION_STALL_MAX_SECONDS = 600.0
NAVIGATION_MAX_REPLAN_STEPS = 24
NAVIGATION_STABLE_FRAME_SIMILARITY = 95.0
NAVIGATION_STATE_EDGE_RETRY_LIMIT = 2
# One normal click plus progressively wider random retries.  Ten bounded
# attempts reach a useful area without returning to the old unbounded loop.
NAVIGATION_SEMANTIC_EDGE_RETRY_LIMIT = 10

_RECOGNITION_OPS_INCIDENT_FIELDS = (
    "id",
    "status",
    "review_status",
    "created_at",
    "updated_at",
    "elapsed_seconds",
    "target_scene_id",
    "current_scene_id",
    "fallback_used",
    "trigger",
    "runtime",
    "resolution",
)
_RECOGNITION_OPS_TIMELINE_FIELDS = ("source_scene_id", "landing_scene_id", "landing_score")


def navigation_incidents_dir(entry_id: str) -> Path:
    return data_annotation_entry_dir(entry_id) / "recognition-ops" / "navigation-incidents"


def _incident_dir(entry_id: str, incident_id: str) -> Path:
    safe_id = "".join(ch for ch in str(incident_id or "") if ch.isalnum() or ch in {"-", "_"})
    if not safe_id or safe_id != str(incident_id):
        raise ValueError("导航事件编号不合法")
    root = navigation_incidents_dir(entry_id).resolve(strict=False)
    path = (root / safe_id).resolve(strict=False)
    if path.parent != root:
        raise ValueError("导航事件路径越界")
    return path


def _scene_number(image: Mapping[str, Any]) -> int | None:
    for key in ("scene_id", "number", "image_id"):
        try:
            value = image.get(key)
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            pass
    stem = Path(str(image.get("filename") or "")).stem
    return int(stem) if stem.isdigit() else None


def _public_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _public_payload(item)
            for key, item in value.items()
            if not str(key).startswith("_")
        }
    if isinstance(value, list):
        return [_public_payload(item) for item in value]
    return value


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def list_navigation_incidents(entry_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
    root = navigation_incidents_dir(entry_id)
    if not root.is_dir():
        return []
    candidates = sorted(
        (path for path in root.iterdir() if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    incidents: list[dict[str, Any]] = []
    for path in candidates[: max(1, int(limit))]:
        payload = _read_json(path / "incident.json")
        if payload is not None:
            incidents.append(_public_payload(payload))
    return incidents


@lru_cache(maxsize=32)
def _cached_navigation_incident_summaries(
    root_value: str,
    signature: tuple[tuple[str, int, int], ...],
) -> tuple[dict[str, Any], ...]:
    root = Path(root_value)
    summaries: list[dict[str, Any]] = []
    for incident_id, _mtime_ns, _size in signature:
        payload = _read_json(root / incident_id / "incident.json")
        if payload is None:
            continue
        summary = {
            key: payload[key]
            for key in _RECOGNITION_OPS_INCIDENT_FIELDS
            if key in payload
        }
        timeline = payload.get("timeline") if isinstance(payload.get("timeline"), list) else []
        summary["timeline"] = [
            {
                key: item[key]
                for key in _RECOGNITION_OPS_TIMELINE_FIELDS
                if key in item
            }
            for item in timeline
            if isinstance(item, dict)
        ]
        summaries.append(_public_payload(summary))
    return tuple(summaries)


def list_navigation_incident_summaries(entry_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
    """Return the navigation fields used by recognition ops, cached until files change."""

    root = navigation_incidents_dir(entry_id)
    if not root.is_dir():
        return []
    candidates: list[tuple[int, str, int]] = []
    for path in root.iterdir():
        incident_path = path / "incident.json"
        if not path.is_dir() or not incident_path.is_file():
            continue
        try:
            stat = incident_path.stat()
        except OSError:
            continue
        candidates.append((stat.st_mtime_ns, path.name, stat.st_size))
    candidates.sort(reverse=True)
    signature = tuple(
        (incident_id, mtime_ns, size)
        for mtime_ns, incident_id, size in candidates[: max(1, int(limit))]
    )
    summaries = _cached_navigation_incident_summaries(str(root.resolve(strict=False)), signature)
    return deepcopy(list(summaries))


def load_navigation_incident(entry_id: str, incident_id: str, *, include_frames: bool = False) -> dict[str, Any] | None:
    directory = _incident_dir(entry_id, incident_id)
    payload = _read_json(directory / "incident.json")
    if payload is None:
        return None
    result = _public_payload(payload)
    if not include_frames:
        return result
    frames = result.get("frames") if isinstance(result.get("frames"), list) else []
    encoded: dict[str, str] = {}
    for item in frames:
        if not isinstance(item, dict):
            continue
        relative_path = str(item.get("path") or "")
        frame_path = (directory / relative_path).resolve(strict=False)
        if not relative_path or directory.resolve(strict=False) not in frame_path.parents or not frame_path.is_file():
            continue
        encoded[relative_path] = "data:image/png;base64," + base64.b64encode(frame_path.read_bytes()).decode("ascii")
    result["frame_data_urls"] = encoded
    return result


class NavigationIncidentRecorder:
    """Incrementally persist one abnormal go_scene episode for human review."""

    def __init__(
        self,
        runner: Any,
        ctx: dict[str, Any],
        asset_tree_path: Path,
        *,
        target_scene_id: int,
        started_monotonic: float,
    ) -> None:
        self.runner = runner
        self.ctx = ctx
        self.asset_tree_path = asset_tree_path
        self.target_scene_id = int(target_scene_id)
        self.started_monotonic = float(started_monotonic)
        self.incident_id = ""
        self.directory: Path | None = None
        self.payload: dict[str, Any] | None = None
        self.timeline: list[dict[str, Any]] = []
        self._frame_sequence = 0
        self._fallback_used = False

    @property
    def active(self) -> bool:
        return self.payload is not None and self.directory is not None

    @property
    def fallback_used(self) -> bool:
        return self._fallback_used

    def elapsed_seconds(self) -> float:
        return max(0.0, time.monotonic() - self.started_monotonic)

    def _runtime_metadata(self) -> dict[str, Any]:
        status = self.runner.status() if callable(getattr(self.runner, "status", None)) else {}
        generation = None
        try:
            from backend.core.fanxiu.behavior_tree.jupyter_kernel import fanxiu_kernel_manager_status

            generation = fanxiu_kernel_manager_status().get("generation")
        except Exception:
            generation = None
        return {
            "task": status.get("current_task"),
            "task_id": status.get("current_task_id"),
            "task_type": status.get("task_type"),
            "phase": status.get("phase"),
            "cell_id": status.get("current_cell_id"),
            "kernel_generation": generation,
        }

    def _asset_metadata(self) -> dict[str, Any]:
        try:
            data = self.asset_tree_path.read_bytes()
            return {
                "path": str(self.asset_tree_path),
                "updated_at": self.asset_tree_path.stat().st_mtime,
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        except OSError:
            return {"path": str(self.asset_tree_path), "updated_at": None, "sha256": None}

    def _related_scene_snapshots(self) -> list[dict[str, Any]]:
        scene_ids = {self.target_scene_id}
        for item in self.timeline:
            for key in ("source_scene_id", "landing_scene_id", "recognized_scene_id"):
                try:
                    value = item.get(key)
                    if value is not None:
                        scene_ids.add(int(value))
                except (TypeError, ValueError):
                    pass
        images = self.ctx.get("images") if isinstance(self.ctx.get("images"), dict) else {}
        snapshots: list[dict[str, Any]] = []
        for scene_id in sorted(scene_ids):
            image = images.get(scene_id)
            if isinstance(image, dict):
                snapshots.append(deepcopy(image))
        return snapshots

    def _save_frame(self, frame_data_url: str | None, *, role: str, timeline_index: int | None = None) -> str | None:
        if not self.active or not isinstance(frame_data_url, str) or not frame_data_url.startswith("data:image"):
            return None
        try:
            data = self.runner._decode_frame_data_url(frame_data_url)
        except Exception:
            return None
        self._frame_sequence += 1
        relative = f"frames/{self._frame_sequence:04d}-{role}.png"
        path = self.directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        frames = self.payload.setdefault("frames", [])
        frames.append({
            "path": relative,
            "role": role,
            "timeline_index": timeline_index,
            "captured_at": datetime.now().isoformat(timespec="seconds"),
        })
        return relative

    def _save_identity_crops(self, frame_data_url: str | None, diagnostic: dict[str, Any] | None) -> None:
        if not (
            self.active
            and isinstance(frame_data_url, str)
            and frame_data_url.startswith("data:image")
            and isinstance(diagnostic, dict)
        ):
            return
        try:
            from PIL import Image

            source_bytes = self.runner._decode_frame_data_url(frame_data_url)
            source = Image.open(io.BytesIO(source_bytes)).convert("RGB")
        except Exception:
            return
        images = self.ctx.get("images") if isinstance(self.ctx.get("images"), dict) else {}
        for candidate in diagnostic.get("candidates") or []:
            if not isinstance(candidate, dict):
                continue
            try:
                scene_id = int(candidate.get("scene_id"))
            except (TypeError, ValueError):
                continue
            image = images.get(scene_id)
            if not isinstance(image, dict):
                continue
            try:
                shapes = [
                    shape
                    for shape in self.runner._flatten_shapes(image.get("shapes"))
                    if isinstance(shape, dict)
                    and (
                        bool(shape.get("isSceneIdentity"))
                        or str(shape.get("sceneIdentityRole") or "").strip().lower() in {"required", "optional"}
                    )
                ]
            except Exception:
                shapes = []
            score_items = candidate.get("identity_scores") if isinstance(candidate.get("identity_scores"), list) else []
            used_shape_ids: set[str] = set()
            for score_item in score_items:
                if not isinstance(score_item, dict):
                    continue
                title = str(score_item.get("title") or "")
                shape = next(
                    (
                        item
                        for item in shapes
                        if str(item.get("title") or "") == title
                        and str(item.get("id") or "") not in used_shape_ids
                    ),
                    None,
                )
                if not isinstance(shape, dict):
                    continue
                used_shape_ids.add(str(shape.get("id") or ""))
                try:
                    box = self.runner._box(shape, image)
                    left = max(0, min(source.width, int(round(float(box.get("x") or 0)))))
                    top = max(0, min(source.height, int(round(float(box.get("y") or 0)))))
                    right = max(left + 1, min(source.width, int(round(left + float(box.get("w") or 0)))))
                    bottom = max(top + 1, min(source.height, int(round(top + float(box.get("h") or 0)))))
                    crop = source.crop((left, top, right, bottom))
                    self._frame_sequence += 1
                    relative = f"frames/{self._frame_sequence:04d}-scene{scene_id}-identity.png"
                    path = self.directory / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    crop.save(path, format="PNG")
                except Exception:
                    continue
                score_item["crop_path"] = relative
                self.payload.setdefault("frames", []).append({
                    "path": relative,
                    "role": "identity_crop",
                    "timeline_index": None,
                    "scene_id": scene_id,
                    "shape_id": shape.get("id"),
                    "shape_title": shape.get("title"),
                    "captured_at": datetime.now().isoformat(timespec="seconds"),
                })

    def _persist(self) -> None:
        if not self.active:
            return
        self.payload["timeline"] = _public_payload(self.timeline)
        self.payload["scene_snapshots"] = self._related_scene_snapshots()
        self.payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self.payload["elapsed_seconds"] = round(self.elapsed_seconds(), 1)
        write_data_annotation_json(self.directory / "incident.json", _public_payload(self.payload))

    def record_action(
        self,
        *,
        kind: str,
        source_scene_id: int | None,
        source_score: float,
        shape: Mapping[str, Any] | None,
        reason: str,
        before_frame: str | None,
        landing_scene_id: int | None,
        landing_score: float,
        after_frame: str | None,
        frame_similarity: float | None,
        navigation_state_key: str,
        attempt: int | None = None,
        point: tuple[float, float] | list[float] | None = None,
    ) -> None:
        item = {
            "index": len(self.timeline) + 1,
            "time": datetime.now().isoformat(timespec="seconds"),
            "kind": str(kind),
            "source_scene_id": source_scene_id,
            "recognized_scene_id": source_scene_id,
            "recognized_score": round(float(source_score or 0.0), 1),
            "shape_id": str((shape or {}).get("id") or ""),
            "shape_title": str((shape or {}).get("title") or ""),
            "point": (
                [round(float(point[0]), 1), round(float(point[1]), 1)]
                if isinstance(point, (tuple, list)) and len(point) >= 2
                else None
            ),
            "reason": str(reason or ""),
            "landing_scene_id": landing_scene_id,
            "landing_score": round(float(landing_score or 0.0), 1),
            "frame_similarity": round(float(frame_similarity), 1) if frame_similarity is not None else None,
            "progressed": landing_scene_id is not None and landing_scene_id != source_scene_id,
            "navigation_state_key": navigation_state_key,
            "attempt": attempt,
            "_before_frame": before_frame,
            "_after_frame": after_frame,
        }
        self.timeline.append(item)
        if self.active:
            item["before_frame"] = self._save_frame(before_frame, role="before", timeline_index=item["index"])
            item["after_frame"] = self._save_frame(after_frame, role="after", timeline_index=item["index"])
            self._persist()

    def trigger(
        self,
        *,
        trigger_type: str,
        trigger_label: str,
        threshold: Mapping[str, Any],
        frame_data_url: str | None,
        current_scene_id: int | None,
        current_score: float,
        candidate_scene_ids: list[int] | None = None,
    ) -> None:
        if self.active:
            return
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.incident_id = f"nav-{stamp}-{uuid.uuid4().hex[:8]}"
        entry_id = str(self.ctx.get("entry_id") or self.asset_tree_path.parent.name)
        self.directory = _incident_dir(entry_id, self.incident_id)
        self.directory.mkdir(parents=True, exist_ok=False)
        diagnostic: dict[str, Any] | None = None
        if isinstance(frame_data_url, str) and frame_data_url.startswith("data:image"):
            try:
                evidence = build_unknown_evidence(
                    self.runner,
                    self.ctx,
                    frame_data_url,
                    label=self.incident_id,
                    expected_scene_ids=[self.target_scene_id],
                    last_scene_id=current_scene_id,
                    last_score=current_score,
                    max_candidates=8,
                    candidate_scene_ids=candidate_scene_ids,
                )
                diagnostic = evidence.to_dict()
                diagnostic["frame_path"] = None
                diagnostic["report_path"] = None
            except Exception as exc:
                diagnostic = {"error": str(exc)}
        self.payload = {
            "schema_version": 1,
            "id": self.incident_id,
            "entry_id": entry_id,
            "category": "navigation_stall",
            "review_status": "pending",
            "status": "recovering",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "target_scene_id": self.target_scene_id,
            "current_scene_id": current_scene_id,
            "current_score": round(float(current_score or 0.0), 1),
            "trigger": {
                "type": str(trigger_type),
                "label": str(trigger_label),
                "threshold": dict(threshold),
            },
            "policy": {
                "max_duration_seconds": NAVIGATION_STALL_MAX_SECONDS,
                "max_replan_steps": NAVIGATION_MAX_REPLAN_STEPS,
                "stable_frame_similarity": NAVIGATION_STABLE_FRAME_SIMILARITY,
                "state_edge_retry_limit": NAVIGATION_STATE_EDGE_RETRY_LIMIT,
                "semantic_edge_retry_limit": NAVIGATION_SEMANTIC_EDGE_RETRY_LIMIT,
            },
            "runtime": self._runtime_metadata(),
            "asset_tree": self._asset_metadata(),
            "diagnostic": diagnostic,
            "frames": [],
            "timeline": [],
            "scene_snapshots": [],
            "resolution": None,
        }
        self._save_identity_crops(frame_data_url, diagnostic)
        for item in self.timeline:
            item["before_frame"] = self._save_frame(item.get("_before_frame"), role="before", timeline_index=item["index"])
            item["after_frame"] = self._save_frame(item.get("_after_frame"), role="after", timeline_index=item["index"])
        self._save_frame(frame_data_url, role="trigger")
        self._persist()

    def mark_fallback_used(self) -> None:
        self._fallback_used = True
        if self.active:
            self.payload["fallback_used"] = True
            self._persist()

    def finalize(
        self,
        *,
        status: str,
        final_scene_id: int | None,
        final_score: float = 0.0,
        final_frame: str | None = None,
        message: str = "",
    ) -> None:
        if not self.active:
            return
        self._save_frame(final_frame, role="final")
        self.payload["status"] = str(status)
        self.payload["resolution"] = {
            "scene_id": final_scene_id,
            "score": round(float(final_score or 0.0), 1),
            "message": str(message or ""),
            "finished_at": datetime.now().isoformat(timespec="seconds"),
        }
        self._persist()
