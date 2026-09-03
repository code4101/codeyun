from __future__ import annotations

import os
from pathlib import Path

from backend.core.temp_paths import codeyun_temp_root, prune_temp_files


DEFAULT_FANXIU_EVIDENCE_MAX_BYTES = 1 * 1024 * 1024 * 1024
DEFAULT_FANXIU_EVIDENCE_MAX_FILES = 2000
DEFAULT_FANXIU_EVIDENCE_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
DEFAULT_FANXIU_WATCH_MAX_BYTES = 512 * 1024 * 1024
DEFAULT_FANXIU_WATCH_MAX_FILES = 2000
DEFAULT_FANXIU_WATCH_MAX_AGE_SECONDS = 7 * 24 * 60 * 60


def _retention_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def prune_fanxiu_evidence(root: Path | None = None) -> dict[str, int]:
    """Keep diagnostic screenshots useful without letting them own the disk."""

    evidence_root = Path(root) if root is not None else codeyun_temp_root("fanxiu-evidence")
    max_bytes = _retention_int("CODEYUN_FANXIU_EVIDENCE_MAX_BYTES", DEFAULT_FANXIU_EVIDENCE_MAX_BYTES)
    max_files = _retention_int("CODEYUN_FANXIU_EVIDENCE_MAX_FILES", DEFAULT_FANXIU_EVIDENCE_MAX_FILES)
    max_age_seconds = _retention_int(
        "CODEYUN_FANXIU_EVIDENCE_MAX_AGE_SECONDS",
        DEFAULT_FANXIU_EVIDENCE_MAX_AGE_SECONDS,
    )
    return prune_temp_files(
        evidence_root,
        patterns=("*.png", "*.jpg", "*.jpeg", "*.json", "*.jsonl", "*.log"),
        recursive=True,
        max_files=max_files,
        target_files=max(0, int(max_files * 0.9)),
        max_bytes=max_bytes,
        target_bytes=max(0, int(max_bytes * 0.9)),
        max_age_seconds=max_age_seconds,
    )


def prune_fanxiu_watch(
    root: Path,
    *,
    protected_names: tuple[str, ...] = (),
) -> dict[str, int]:
    """Rotate old doctor-watch runs while preserving the active run contract."""

    max_bytes = _retention_int("CODEYUN_FANXIU_WATCH_MAX_BYTES", DEFAULT_FANXIU_WATCH_MAX_BYTES)
    max_files = _retention_int("CODEYUN_FANXIU_WATCH_MAX_FILES", DEFAULT_FANXIU_WATCH_MAX_FILES)
    max_age_seconds = _retention_int(
        "CODEYUN_FANXIU_WATCH_MAX_AGE_SECONDS",
        DEFAULT_FANXIU_WATCH_MAX_AGE_SECONDS,
    )
    return prune_temp_files(
        root,
        patterns=("*.ndjson", "*.json", "*.log"),
        max_files=max_files,
        target_files=max(0, int(max_files * 0.9)),
        max_bytes=max_bytes,
        target_bytes=max(0, int(max_bytes * 0.9)),
        max_age_seconds=max_age_seconds,
        protected_names=protected_names,
    )
