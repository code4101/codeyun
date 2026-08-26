from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import time
import uuid
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping

from filelock import FileLock
from PIL import Image

from backend.core.fanxiu.data_annotation.state import write_data_annotation_json
from backend.core.fanxiu.data_annotation.storage import data_annotation_entry_dir


AMBIGUITY_SAMPLE_LIMIT = 5
AMBIGUITY_DEDUPE_SECONDS = 1.0
RECOGNIZER_VERSION = "scene-graph-v1"


def _ops_root(entry_id: str) -> Path:
    return data_annotation_entry_dir(entry_id) / "recognition-ops"


def recognition_ambiguity_groups_dir(entry_id: str) -> Path:
    return _ops_root(entry_id) / "ambiguity-groups"


def recognition_ambiguity_events_dir(entry_id: str) -> Path:
    return _ops_root(entry_id) / "ambiguity-events"


def recognition_frame_blobs_dir(entry_id: str) -> Path:
    return _ops_root(entry_id) / "frame-blobs"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _safe_signature(value: str) -> str:
    text = str(value or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{24}", text):
        raise ValueError("识别并列签名不合法")
    return text


def _decode_frame(frame_data_url: str) -> tuple[bytes, str, int, int]:
    if not isinstance(frame_data_url, str) or not frame_data_url.startswith("data:image"):
        raise ValueError("识别并列事件缺少原始画面")
    try:
        encoded = frame_data_url.split(",", 1)[1]
        raw = base64.b64decode(encoded)
        image = Image.open(io.BytesIO(raw)).convert("RGB")
        image.load()
    except Exception as exc:
        raise ValueError("识别并列事件画面无法解码") from exc
    width, height = image.size
    digest = hashlib.sha256(
        width.to_bytes(4, "big") + height.to_bytes(4, "big") + image.tobytes()
    ).hexdigest()
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue(), digest, int(width), int(height)


def _save_frame_blob(entry_id: str, frame_data_url: str) -> dict[str, Any]:
    png, digest, width, height = _decode_frame(frame_data_url)
    relative = Path("recognition-ops") / "frame-blobs" / digest[:2] / f"{digest}.png"
    path = data_annotation_entry_dir(entry_id) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + ".lock", timeout=10):
        if not path.is_file():
            path.write_bytes(png)
    return {
        "sha256": digest,
        "path": relative.as_posix(),
        "width": width,
        "height": height,
    }


def _captured_at_text(value: float | int | str | None) -> tuple[str, float]:
    try:
        timestamp = float(value or 0.0)
    except (TypeError, ValueError):
        timestamp = 0.0
    if timestamp <= 0:
        timestamp = time.time()
    return datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="milliseconds"), timestamp


def _normalized_similarities(
    tied_scene_ids: tuple[int, ...],
    similarities: Mapping[int, float | int | None] | Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    values: dict[int, float | None] = {}
    if isinstance(similarities, Mapping):
        source = similarities.items()
    else:
        source = (
            (item.get("scene_id"), item.get("score"))
            for item in similarities
            if isinstance(item, Mapping)
        )
    for raw_scene_id, raw_score in source:
        try:
            scene_id = int(raw_scene_id)
        except (TypeError, ValueError):
            continue
        if scene_id not in tied_scene_ids:
            continue
        try:
            score = float(raw_score) if raw_score is not None else None
        except (TypeError, ValueError):
            score = None
        values[scene_id] = round(score, 3) if score is not None else None
    return [{"scene_id": scene_id, "score": values.get(scene_id)} for scene_id in tied_scene_ids]


def record_recognition_ambiguity(
    *,
    entry_id: str,
    frame_data_url: str,
    captured_at: float | int | str | None,
    layer: int,
    tied_scene_ids: Iterable[int],
    similarities: Mapping[int, float | int | None] | Iterable[Mapping[str, Any]],
    fallback_scene_id: int | None,
    asset_tree_sha256: str,
    recognizer_version: str = RECOGNIZER_VERSION,
) -> dict[str, Any]:
    """Persist one real graph tie without storing recomputable intermediate evidence."""

    safe_entry_id = str(entry_id or "").strip()
    if not safe_entry_id:
        raise ValueError("识别并列事件缺少 entry_id")
    candidates = tuple(sorted({int(item) for item in tied_scene_ids if int(item) > 0}))
    if len(candidates) < 2:
        raise ValueError("识别并列事件至少需要两个候选场景")
    if fallback_scene_id is not None and int(fallback_scene_id) not in candidates:
        raise ValueError("相似度兜底结果必须属于并列候选")

    signature_source = f"{int(layer)}|{','.join(str(item) for item in candidates)}"
    signature = hashlib.sha256(signature_source.encode("utf-8")).hexdigest()[:24]
    captured_text, captured_timestamp = _captured_at_text(captured_at)
    frame_ref = _save_frame_blob(safe_entry_id, frame_data_url)
    similarity_summary = _normalized_similarities(candidates, similarities)
    event_fingerprint = hashlib.sha256(
        f"{signature}|{frame_ref['sha256']}|{captured_text}".encode("utf-8")
    ).hexdigest()

    group_path = recognition_ambiguity_groups_dir(safe_entry_id) / f"{signature}.json"
    group_path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(group_path) + ".lock", timeout=10):
        group = _read_json(group_path) or {}
        last_timestamp = float(group.get("_last_event_timestamp") or 0.0)
        duplicate = group.get("_last_event_fingerprint") == event_fingerprint or (
            group.get("_last_frame_sha256") == frame_ref["sha256"]
            and captured_timestamp - last_timestamp <= AMBIGUITY_DEDUPE_SECONDS
            and captured_timestamp >= last_timestamp
        )
        if duplicate:
            return {key: value for key, value in group.items() if not str(key).startswith("_")}

        stamp = datetime.fromtimestamp(captured_timestamp).strftime("%Y%m%d-%H%M%S-%f")
        event_id = f"ambiguity-{stamp}-{uuid.uuid4().hex[:8]}"
        event = {
            "schema_version": 1,
            "id": event_id,
            "entry_id": safe_entry_id,
            "category": "identity_ambiguity",
            "captured_at": captured_text,
            "layer": int(layer),
            "tied_scene_ids": list(candidates),
            "similarities": similarity_summary,
            "fallback_scene_id": int(fallback_scene_id) if fallback_scene_id is not None else None,
            "frame_ref": frame_ref,
            "asset_tree_sha256": str(asset_tree_sha256 or ""),
            "recognizer_version": str(recognizer_version or RECOGNIZER_VERSION),
        }
        event_path = recognition_ambiguity_events_dir(safe_entry_id) / captured_text[:10] / f"{event_id}.json"
        write_data_annotation_json(event_path, event)

        frame_hashes = [str(item) for item in group.get("_frame_hashes", []) if isinstance(item, str)]
        if frame_ref["sha256"] not in frame_hashes:
            frame_hashes.append(frame_ref["sha256"])
        sample_frames = [item for item in group.get("sample_frames", []) if isinstance(item, dict)]
        if not any(item.get("sha256") == frame_ref["sha256"] for item in sample_frames):
            sample = {**frame_ref, "captured_at": captured_text, "fallback_scene_id": event["fallback_scene_id"]}
            if len(sample_frames) < AMBIGUITY_SAMPLE_LIMIT:
                sample_frames.append(sample)
            else:
                sample_frames[-1] = sample
        selected_counts = {
            str(key): int(value)
            for key, value in (group.get("selected_scene_counts") or {}).items()
            if str(value).isdigit() or isinstance(value, int)
        }
        selected_key = str(event["fallback_scene_id"]) if event["fallback_scene_id"] is not None else "unresolved"
        selected_counts[selected_key] = selected_counts.get(selected_key, 0) + 1
        first_timestamp = min(float(group.get("_first_event_timestamp") or captured_timestamp), captured_timestamp)
        last_captured_timestamp = max(float(group.get("_last_captured_timestamp") or captured_timestamp), captured_timestamp)
        group = {
            "schema_version": 1,
            "id": f"ambiguity:{signature}",
            "signature": signature,
            "entry_id": safe_entry_id,
            "category": "identity_ambiguity",
            "review_status": str(group.get("review_status") or "pending"),
            "layer": int(layer),
            "tied_scene_ids": list(candidates),
            "occurrence_count": int(group.get("occurrence_count") or 0) + 1,
            "distinct_frame_count": len(frame_hashes),
            "first_seen_at": datetime.fromtimestamp(first_timestamp).astimezone().isoformat(timespec="milliseconds"),
            "last_seen_at": datetime.fromtimestamp(last_captured_timestamp).astimezone().isoformat(timespec="milliseconds"),
            "selected_scene_counts": selected_counts,
            "sample_frames": sample_frames,
            "latest_event_id": event_id,
            "latest_similarities": similarity_summary,
            "asset_tree_sha256": str(asset_tree_sha256 or ""),
            "recognizer_version": str(recognizer_version or RECOGNIZER_VERSION),
            "_frame_hashes": frame_hashes,
            "_first_event_timestamp": first_timestamp,
            "_last_captured_timestamp": last_captured_timestamp,
            "_last_event_fingerprint": event_fingerprint,
            "_last_frame_sha256": frame_ref["sha256"],
            "_last_event_timestamp": captured_timestamp,
        }
        write_data_annotation_json(group_path, group)
    return {key: value for key, value in group.items() if not str(key).startswith("_")}


@lru_cache(maxsize=32)
def _cached_group_summaries(
    root_value: str,
    signature: tuple[tuple[str, int, int], ...],
) -> tuple[dict[str, Any], ...]:
    root = Path(root_value)
    result: list[dict[str, Any]] = []
    for filename, _mtime_ns, _size in signature:
        payload = _read_json(root / filename)
        if payload is not None:
            result.append({key: value for key, value in payload.items() if not str(key).startswith("_")})
    return tuple(result)


def list_recognition_ambiguity_summaries(entry_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
    root = recognition_ambiguity_groups_dir(entry_id)
    if not root.is_dir():
        return []
    candidates: list[tuple[int, str, int]] = []
    for path in root.glob("*.json"):
        try:
            stat = path.stat()
        except OSError:
            continue
        candidates.append((stat.st_mtime_ns, path.name, stat.st_size))
    candidates.sort(reverse=True)
    signature = tuple((name, mtime, size) for mtime, name, size in candidates[: max(1, int(limit))])
    return list(_cached_group_summaries(str(root.resolve(strict=False)), signature))


def load_recognition_ambiguity(entry_id: str, signature: str, *, include_frames: bool = False) -> dict[str, Any] | None:
    safe_signature = _safe_signature(signature)
    root = recognition_ambiguity_groups_dir(entry_id).resolve(strict=False)
    path = (root / f"{safe_signature}.json").resolve(strict=False)
    if path.parent != root:
        raise ValueError("识别并列事件路径越界")
    payload = _read_json(path)
    if payload is None:
        return None
    result = {key: value for key, value in payload.items() if not str(key).startswith("_")}
    if not include_frames:
        return result
    encoded: dict[str, str] = {}
    entry_root = data_annotation_entry_dir(entry_id).resolve(strict=False)
    for item in result.get("sample_frames", []):
        if not isinstance(item, Mapping):
            continue
        relative = str(item.get("path") or "")
        frame_path = (entry_root / relative).resolve(strict=False)
        if not relative or entry_root not in frame_path.parents or not frame_path.is_file():
            continue
        encoded[relative] = "data:image/png;base64," + base64.b64encode(frame_path.read_bytes()).decode("ascii")
    result["frame_data_urls"] = encoded
    return result
