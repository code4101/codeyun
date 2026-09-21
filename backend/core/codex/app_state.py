"""Keep the Codex desktop app's persisted model selection in sync with config.toml.

The desktop app defaults a new thread to the most recent entry of the composer's
``composer-recent-model-configurations-v1`` atom stored in
``.codex-global-state.json``.  Rewriting ``config.toml`` alone therefore leaves
new threads on the previous provider, so a provider switch updates this persisted
selection while the app is stopped.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

GLOBAL_STATE_FILENAME = ".codex-global-state.json"
_ATOM_STATE_KEY = "electron-persisted-atom-state"
_RECENT_MODELS_KEY = "composer-recent-model-configurations-v1"
_DEFAULT_REASONING_EFFORT = "medium"


def _load_global_state(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_global_state(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f"{path.name}.codeyun-tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def _entry_model(item: Any) -> str:
    return str(item.get("model") or "").strip() if isinstance(item, dict) else ""


def _entry_effort(item: Any) -> str:
    return str(item.get("reasoningEffort") or "").strip() if isinstance(item, dict) else ""


def set_current_model(codex_home: Path, model: str, *, reasoning_effort: str = "") -> bool:
    """Make ``model`` the newest composer configuration so new threads use it.

    Returns ``True`` when the persisted state changed.  Best effort: a missing,
    malformed, or unwritable state file leaves the app untouched.
    """

    target = str(model or "").strip()
    if not target:
        return False

    path = codex_home / GLOBAL_STATE_FILENAME
    payload = _load_global_state(path)
    if payload is None:
        return False
    atom = payload.get(_ATOM_STATE_KEY)
    if not isinstance(atom, dict):
        return False
    recent = atom.get(_RECENT_MODELS_KEY)
    if not isinstance(recent, list):
        return False
    # Already the active selection; leave the file alone.
    if recent and _entry_model(recent[-1]) == target:
        return False

    existing = next((item for item in recent if _entry_model(item) == target), None)
    effort = (
        str(reasoning_effort or "").strip()
        or _entry_effort(existing)
        or _DEFAULT_REASONING_EFFORT
    )
    entry: dict[str, Any] = {"model": target, "reasoningEffort": effort, "serviceTier": None}
    if isinstance(existing, dict) and existing.get("serviceTier") is not None:
        entry["serviceTier"] = existing["serviceTier"]

    atom[_RECENT_MODELS_KEY] = [item for item in recent if _entry_model(item) != target] + [entry]
    try:
        _write_global_state(path, payload)
    except OSError:
        return False
    return True
