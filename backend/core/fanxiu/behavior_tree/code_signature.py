from __future__ import annotations

import hashlib
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[4]
_FANXIU_SOURCE_ROOT = _REPO_ROOT / "backend" / "core" / "fanxiu"
_CODEX_SOURCE_ROOT = _REPO_ROOT / "backend" / "core" / "codex"


def _runtime_sources() -> list[Path]:
    # Both the Kernel and external dispatcher import the escalation boundary.
    # A transport change must not leave either process dispatching via old CLI code.
    return sorted([*_FANXIU_SOURCE_ROOT.rglob('*.py'), *_CODEX_SOURCE_ROOT.rglob('*.py')])


def _digest_sources(source_paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for source_path in source_paths:
        if not source_path.is_file():
            continue
        digest.update(source_path.relative_to(_REPO_ROOT).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(source_path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def fanxiu_behavior_tree_code_signature() -> str:
    """Fingerprint the Python code loaded by the long-lived Fanxiu Kernel."""

    return _digest_sources(_runtime_sources())


def fanxiu_scheduler_code_signature() -> str:
    """Fingerprint the external Scheduler and the Fanxiu modules it imports."""

    source_paths = _runtime_sources()
    source_paths.append(_REPO_ROOT / "scripts" / "fanxiu_bt.py")
    return _digest_sources(source_paths)
