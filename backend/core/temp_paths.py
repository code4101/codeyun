from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Iterable


def codeyun_temp_root(*parts: str, create: bool = True) -> Path:
    """Return a CodeYun-owned directory under the system temp location."""

    root = Path(tempfile.gettempdir()) / "codeyun"
    for part in parts:
        normalized = "".join(char if char.isalnum() or char in "._-" else "_" for char in str(part))
        normalized = normalized.strip("._-")
        if normalized:
            root /= normalized
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root


def prune_temp_files(
    root: Path,
    *,
    patterns: Iterable[str] = ("*",),
    recursive: bool = False,
    max_files: int | None = None,
    target_files: int | None = None,
    max_bytes: int | None = None,
    target_bytes: int | None = None,
    max_age_seconds: float | None = None,
    protected_names: Iterable[str] = (),
    now: float | None = None,
) -> dict[str, int]:
    """Prune oldest temporary files under ``root`` using bounded retention.

    The helper only removes files, never directories or links.  Producers own
    their namespace and call this after a successful write, which avoids a
    global cleaner guessing whether an unmarked legacy directory is active.
    ``target_*`` values provide a low watermark so high-frequency writers do
    not rescan and delete on every write once they reach the hard limit.
    """

    resolved_root = Path(root).resolve(strict=False)
    if not resolved_root.is_dir():
        return {"scanned_files": 0, "removed_files": 0, "removed_bytes": 0, "remaining_files": 0, "remaining_bytes": 0}

    protected = {str(name) for name in protected_names}
    candidates: dict[Path, tuple[float, int]] = {}
    for pattern in patterns:
        iterator = resolved_root.rglob(pattern) if recursive else resolved_root.glob(pattern)
        for path in iterator:
            try:
                if path.name in protected or path.is_symlink() or not path.is_file():
                    continue
                resolved_path = path.resolve(strict=True)
                resolved_path.relative_to(resolved_root)
                stat = resolved_path.stat()
            except (OSError, RuntimeError, ValueError):
                continue
            candidates[resolved_path] = (float(stat.st_mtime), int(stat.st_size))

    ordered = sorted(candidates.items(), key=lambda item: (item[1][0], str(item[0]).lower()))
    removed_files = 0
    removed_bytes = 0
    current_time = float(time.time() if now is None else now)

    def remove(path: Path, size: int) -> bool:
        nonlocal removed_files, removed_bytes
        try:
            path.unlink()
        except OSError:
            return False
        removed_files += 1
        removed_bytes += size
        return True

    kept: list[tuple[Path, tuple[float, int]]] = []
    for path, metadata in ordered:
        modified_at, size = metadata
        expired = max_age_seconds is not None and current_time - modified_at > max(0.0, float(max_age_seconds))
        if expired and remove(path, size):
            continue
        kept.append((path, metadata))

    remaining_bytes = sum(metadata[1] for _, metadata in kept)
    exceeds_files = max_files is not None and len(kept) > max(0, int(max_files))
    exceeds_bytes = max_bytes is not None and remaining_bytes > max(0, int(max_bytes))
    if exceeds_files or exceeds_bytes:
        desired_files = max(0, int(target_files if target_files is not None else max_files)) if max_files is not None else None
        desired_bytes = max(0, int(target_bytes if target_bytes is not None else max_bytes)) if max_bytes is not None else None
        desired_files = min(desired_files, max(0, int(max_files))) if desired_files is not None and max_files is not None else desired_files
        desired_bytes = min(desired_bytes, max(0, int(max_bytes))) if desired_bytes is not None and max_bytes is not None else desired_bytes
        while kept and (
            (desired_files is not None and len(kept) > desired_files)
            or (desired_bytes is not None and remaining_bytes > desired_bytes)
        ):
            path, (_, size) = kept.pop(0)
            if remove(path, size):
                remaining_bytes -= size

    return {
        "scanned_files": len(ordered),
        "removed_files": removed_files,
        "removed_bytes": removed_bytes,
        "remaining_files": len(kept),
        "remaining_bytes": remaining_bytes,
    }


def trim_file_tail(path: Path, *, max_bytes: int) -> bool:
    """Keep the newest complete-line tail of an append-only temporary log."""

    target = Path(path)
    limit = max(0, int(max_bytes))
    try:
        size = target.stat().st_size
    except OSError:
        return False
    if size <= limit:
        return False
    if limit <= 0:
        target.write_bytes(b"")
        return True

    with target.open("rb") as source:
        source.seek(max(0, size - limit))
        if source.tell() > 0:
            source.readline()
        payload = source.read()
    temporary = target.with_name(f".{target.name}.trim")
    temporary.write_bytes(payload)
    temporary.replace(target)
    return True

