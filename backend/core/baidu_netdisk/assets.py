"""Immutable local image store for netdisk UI assets.

Layout under ``root`` (all private; nothing here is published anywhere)::

    originals/<image_id>.png|.json   captured screenshots, bytes kept verbatim
    derived/<image_id>.png|.json     PIL crops/scales/redactions of a parent
    scenes/<scene_id>/r<N>.json      immutable scene revisions
    scenes/<scene_id>/current.json   pointer to the latest revision
    .scenes.lock                     cross-process lock for scene publishing

Contract:
- Image files and records are created once and never overwritten; writes go to
  a temp file and are linked into place, so readers never see partial files.
- ``sha256`` is always computed over the bytes actually stored on disk.
- Image ids are server-generated UUID4 hex; scene/shape ids are short slugs.
  Every dynamic path is checked to stay inside ``root`` and not traverse links.
- Scene ``revision`` numbers are independent of image hashes; publishing needs
  the caller's ``base_revision`` (0 for a new scene) and fails on mismatch.
  Publishing is serialised across processes by a file lock. A revision file
  left without its pointer (crash between the two writes) is reused only when
  a retry carries the same base and scene body; anything else conflicts and
  the orphan is never modified.
- Stored and derived images are limited to ``MAX_PIXELS`` pixels.
- Only derived images carrying explicit ``redact_boxes`` can be exported, with
  a whitelisted technical metadata subset and ``requires_review=True``: drawn
  boxes do not prove a human checked the image.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from filelock import FileLock, Timeout
from PIL import Image

_IMAGE_ID = re.compile(r"^[0-9a-f]{32}$")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
MAX_PIXELS = 16_000_000
_LOCK_TIMEOUT = 30
_EXPORT_FIELDS = ("image_id", "sha256", "width", "height", "format", "parent_id",
                  "root_image_id", "crop_xywh", "scale", "transform", "redact_boxes")


class AssetError(ValueError):
    """Invalid input, unknown id, or a path/immutability violation."""


class SceneConflict(AssetError):
    """``base_revision`` no longer matches the scene's current revision."""


def _is_link(path: Path) -> bool:
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _box(value: Any, name: str) -> tuple[int, int, int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 4 or not all(
        isinstance(v, int) and not isinstance(v, bool) for v in value
    ):
        raise AssetError(f"{name} must be four integers [x, y, w, h]")
    x, y, w, h = value
    if x < 0 or y < 0 or w <= 0 or h <= 0:
        raise AssetError(f"{name} must have x,y >= 0 and w,h > 0")
    return x, y, w, h


def _check_pixels(width: int, height: int) -> None:
    if width * height > MAX_PIXELS:
        raise AssetError(f"image {width}x{height} exceeds {MAX_PIXELS} pixels")


def _inside(box: Sequence[int], width: int, height: int) -> bool:
    x, y, w, h = box
    return x + w <= width and y + h <= height


class AssetStore:
    """Image/scene store rooted at one private directory (created if missing)."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        root_path = Path(root)
        if _is_link(root_path):
            raise AssetError("store root must not be a symlink")
        root_path.mkdir(parents=True, exist_ok=True)
        self.root = root_path.resolve()
        self._lock = threading.Lock()

    # -- paths and atomic writes ------------------------------------------
    def _path(self, *parts: str) -> Path:
        path = self.root
        for part in parts:
            if not part or part in (".", "..") or "/" in part or "\\" in part:
                raise AssetError(f"illegal path component: {part!r}")
            path = path / part
            if _is_link(path):
                raise AssetError(f"symlink not allowed in store: {path.name}")
        resolved = path.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise AssetError("path escapes store root")
        return path

    def _create(self, path: Path, data: bytes) -> None:
        """Atomically create ``path``; never replaces an existing file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        with open(tmp, "xb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError:
            raise AssetError(f"refusing to overwrite {path.name}") from None
        finally:
            tmp.unlink()

    def _replace(self, path: Path, data: bytes) -> None:
        """Atomic replace, used only for the scene ``current`` pointer."""
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        with open(tmp, "xb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)

    @staticmethod
    def _json(obj: Any) -> bytes:
        return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1).encode()

    def _image_paths(self, image_id: str) -> tuple[Path, Path]:
        if not isinstance(image_id, str) or not _IMAGE_ID.match(image_id):
            raise AssetError(f"illegal image id: {image_id!r}")
        for kind in ("originals", "derived"):
            meta = self._path(kind, f"{image_id}.json")
            if meta.is_file():
                return self._path(kind, f"{image_id}.png"), meta
        raise AssetError(f"unknown image id: {image_id}")

    def _store_image(self, kind: str, png: bytes, record: dict) -> dict:
        image_id = uuid.uuid4().hex
        record = {"image_id": image_id, "kind": kind, "sha256": _sha256(png),
                  "bytes": len(png), "format": "PNG", "private": True,
                  "created_at": _now(), **record}
        with self._lock:
            self._create(self._path(kind, f"{image_id}.png"), png)
            self._create(self._path(kind, f"{image_id}.json"), self._json(record))
        return record

    # -- images -----------------------------------------------------------
    def capture(self, image_bytes: bytes, metadata: dict | None = None) -> dict:
        """Store a PNG screenshot verbatim and return its record.

        ``metadata`` (client version, profile, display scale, coordinate space,
        observed_at, window_id, ...) must be JSON-serialisable and is kept
        privately under ``source``; it is never exported.
        """
        if not isinstance(image_bytes, (bytes, bytearray)) or not image_bytes:
            raise AssetError("image_bytes must be non-empty bytes")
        try:
            with Image.open(io.BytesIO(image_bytes)) as img:
                img.verify()
            with Image.open(io.BytesIO(image_bytes)) as img:
                fmt, (width, height) = img.format, img.size
        except Exception as exc:
            raise AssetError(f"not a readable image: {exc}") from None
        if fmt != "PNG":
            raise AssetError(f"only PNG captures are accepted, got {fmt}")
        _check_pixels(width, height)
        source = dict(metadata or {})
        json.dumps(source)
        return self._store_image("originals", bytes(image_bytes), {
            "width": width, "height": height, "source": source,
        })

    def read_image(self, image_id: str) -> bytes:
        """Return stored image bytes, verifying they still match ``sha256``."""
        png_path, _ = self._image_paths(image_id)
        data = png_path.read_bytes()
        if _sha256(data) != self.get_image(image_id)["sha256"]:
            raise AssetError(f"stored bytes for {image_id} do not match sha256")
        return data

    def get_image(self, image_id: str) -> dict:
        """Return the immutable record of an original or derived image."""
        _, meta = self._image_paths(image_id)
        return json.loads(meta.read_text("utf-8"))

    def derive(self, image_id: str, crop_xywh: Sequence[int] | None = None,
               redact_boxes: Iterable[Sequence[int]] = (), scale: float = 1) -> dict:
        """Crop, black-out and scale a stored image into a new derived image.

        ``crop_xywh`` and ``redact_boxes`` are in the parent's pixel space and
        must lie inside the parent (redactions inside the crop). Redaction is
        applied before scaling. ``transform`` maps parent -> derived pixels as
        ``derived = (parent - offset) * scale_xy``.
        """
        parent = self.get_image(image_id)
        pw, ph = parent["width"], parent["height"]
        crop = _box(list(crop_xywh), "crop_xywh") if crop_xywh is not None else (0, 0, pw, ph)
        if not _inside(crop, pw, ph):
            raise AssetError("crop_xywh exceeds parent image bounds")
        cx, cy, cw, ch = crop
        boxes = [_box(list(b), "redact_box") for b in redact_boxes]
        for bx, by, bw, bh in boxes:
            if bx < cx or by < cy or bx + bw > cx + cw or by + bh > cy + ch:
                raise AssetError("redact box must lie inside the crop")
        if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not 0 < scale <= 8:
            raise AssetError("scale must be a number in (0, 8]")
        out_w, out_h = max(1, round(cw * scale)), max(1, round(ch * scale))
        _check_pixels(out_w, out_h)
        with Image.open(io.BytesIO(self.read_image(image_id))) as src:
            img = src.convert("RGBA").crop((cx, cy, cx + cw, cy + ch))
        for bx, by, bw, bh in boxes:
            img.paste((0, 0, 0, 255), (bx - cx, by - cy, bx - cx + bw, by - cy + bh))
        if (out_w, out_h) != (cw, ch):
            img = img.resize((out_w, out_h), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return self._store_image("derived", buf.getvalue(), {
            "width": out_w, "height": out_h, "parent_id": image_id,
            "root_image_id": parent.get("root_image_id") or image_id,
            "crop_xywh": list(crop), "scale": scale,
            "transform": {"offset": [cx, cy], "scale_xy": [out_w / cw, out_h / ch]},
            "redact_boxes": [list(b) for b in boxes],
            "redacted": bool(boxes),
        })

    def export_sample(self, image_id: str, destination: str | os.PathLike[str]) -> dict:
        """Copy a redacted derived image plus whitelisted metadata to a directory.

        Refuses originals and derived images not produced with explicit
        ``redact_boxes``. Writes ``<image_id>.png`` and ``<image_id>.json`` and
        never overwrites. The result still ``requires_review``.
        """
        record = self.get_image(image_id)
        if record["kind"] != "derived" or not record.get("redacted"):
            raise AssetError("only derived images with explicit redact_boxes can be exported")
        dest = Path(destination)
        if _is_link(dest) or (dest.exists() and not dest.is_dir()):
            raise AssetError("destination must be a real directory")
        dest = dest.resolve()
        if dest == self.root or self.root in dest.parents:
            raise AssetError("destination must be outside the private store")
        exported = {k: record[k] for k in _EXPORT_FIELDS if k in record}
        exported.update(requires_review=True, exported_at=_now())
        dest.mkdir(parents=True, exist_ok=True)
        self._create(dest / f"{image_id}.png", self.read_image(image_id))
        self._create(dest / f"{image_id}.json", self._json(exported))
        return {**exported, "path": str(dest / f"{image_id}.png")}

    # -- scenes -----------------------------------------------------------
    def _check_ids(self, ids: Any, name: str) -> list[str]:
        if not isinstance(ids, list):
            raise AssetError(f"{name} must be a list of image ids")
        for image_id in ids:
            self.get_image(image_id)
        return ids

    def _validate_scene(self, scene: dict) -> dict:
        if not isinstance(scene, dict):
            raise AssetError("scene must be a dict")
        scene_id = scene.get("scene_id")
        if not isinstance(scene_id, str) or not _SLUG.match(scene_id):
            raise AssetError(f"illegal scene_id: {scene_id!r}")
        for key in ("title", "profile"):
            if not isinstance(scene.get(key), str) or not scene[key]:
                raise AssetError(f"scene {key} must be a non-empty string")
        ref = self.get_image(scene.get("reference_image_id"))
        shapes, seen = scene.get("shapes"), set()
        if not isinstance(shapes, list) or not shapes:
            raise AssetError("scene shapes must be a non-empty list")
        for shape in shapes:
            sid = shape.get("shape_id") if isinstance(shape, dict) else None
            if not isinstance(sid, str) or not _SLUG.match(sid) or sid in seen:
                raise AssetError(f"illegal or duplicate shape_id: {sid!r}")
            seen.add(sid)
            if not isinstance(shape.get("role"), str) or not shape["role"]:
                raise AssetError(f"shape {sid} needs a role")
            if not _inside(_box(shape.get("bbox"), f"shape {sid} bbox"), ref["width"], ref["height"]):
                raise AssetError(f"shape {sid} bbox exceeds reference image")
            threshold = shape.get("threshold")
            if threshold is None and not isinstance(shape.get("ocr"), str):
                raise AssetError(f"shape {sid} needs ocr text or image threshold")
            if threshold is not None and (isinstance(threshold, bool) or not isinstance(
                    threshold, (int, float)) or not 0 < threshold <= 1):
                raise AssetError(f"shape {sid} threshold must be in (0, 1]")
            for key in ("positive_sample_ids", "negative_sample_ids", "failure_evidence_ids"):
                if key in shape:
                    self._check_ids(shape[key], f"shape {sid} {key}")
        for key in ("positive_sample_ids", "negative_sample_ids", "failure_evidence_ids"):
            if key in scene:
                self._check_ids(scene[key], key)
        return json.loads(json.dumps(scene))

    def publish_scene(self, scene: dict, base_revision: int) -> dict:
        """Publish ``scene`` as revision ``base_revision + 1``.

        ``base_revision`` must equal the current revision (0 when the scene
        does not exist yet), otherwise :class:`SceneConflict` is raised.
        Revisions are immutable; ``current.json`` points at the newest one.
        If revision ``base_revision + 1`` exists but was never committed to
        ``current.json``, a retry with an identical scene body commits and
        returns that existing record; a different body raises SceneConflict.
        """
        body = self._validate_scene(scene)
        if isinstance(base_revision, bool) or not isinstance(base_revision, int) or base_revision < 0:
            raise AssetError("base_revision must be an integer >= 0")
        scene_id = body["scene_id"]
        lock = FileLock(str(self._path(".scenes.lock")), timeout=_LOCK_TIMEOUT)
        try:
            with self._lock, lock:
                return self._publish_locked(scene_id, body, base_revision)
        except Timeout:
            raise AssetError(f"timed out waiting for scene lock of {scene_id}") from None

    def _publish_locked(self, scene_id: str, body: dict, base_revision: int) -> dict:
        current = self._current_revision(scene_id)
        if current != base_revision:
            raise SceneConflict(f"scene {scene_id} is at revision {current}, not {base_revision}")
        revision = base_revision + 1
        path = self._path("scenes", scene_id, f"r{revision}.json")
        if path.exists():
            try:
                record = json.loads(path.read_text("utf-8"))
            except (OSError, ValueError):
                record = None
            if not (isinstance(record, dict) and record.get("scene_id") == scene_id
                    and record.get("revision") == revision
                    and record.get("base_revision") == base_revision
                    and record.get("scene") == body):
                raise SceneConflict(
                    f"scene {scene_id} has an uncommitted revision {revision} with different content")
        else:
            record = {"scene_id": scene_id, "revision": revision,
                      "base_revision": base_revision, "published_at": _now(),
                      "private": True, "scene": body}
            try:
                self._create(path, self._json(record))
            except AssetError:
                raise SceneConflict(f"scene {scene_id} revision {revision} already exists") from None
        self._replace(self._path("scenes", scene_id, "current.json"),
                      self._json({"revision": revision}))
        return record

    def _current_revision(self, scene_id: str) -> int:
        pointer = self._path("scenes", scene_id, "current.json")
        if not pointer.is_file():
            return 0
        return int(json.loads(pointer.read_text("utf-8"))["revision"])

    def get_scene(self, scene_id: str, revision: int | None = None) -> dict:
        """Return the current (or a specific) immutable scene revision record."""
        if not isinstance(scene_id, str) or not _SLUG.match(scene_id):
            raise AssetError(f"illegal scene_id: {scene_id!r}")
        if revision is None:
            revision = self._current_revision(scene_id)
        elif isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise AssetError(f"revision must be a positive integer: {revision!r}")
        path = self._path("scenes", scene_id, f"r{revision}.json")
        if not revision or not path.is_file():
            raise AssetError(f"unknown scene: {scene_id}")
        return json.loads(path.read_text("utf-8"))
