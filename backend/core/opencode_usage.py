"""Read the local opencode Go plan usage through its official API.

opencode stores the ``opencode-go`` API key in its own auth file; the official
usage endpoint returns the rolling (5h), weekly and monthly quota windows.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import requests


OPENCODE_GO_USAGE_URL = "https://opencode.ai/zen/go/v1/usage"
OPENCODE_GO_PROVIDER_ID = "opencode-go"
_OPENCODE_GO_TIMEOUT_SECONDS = 15.0

_WINDOW_ORDER = ("rolling", "weekly", "monthly")
_WINDOW_LABELS = {"rolling": "5 小时", "weekly": "每周", "monthly": "每月"}


class OpenCodeUsageError(RuntimeError):
    """Raised when the opencode Go usage response cannot be parsed."""


def resolve_opencode_auth_path() -> Path:
    base = (os.environ.get("XDG_DATA_HOME") or "").strip()
    if base:
        return Path(base) / "opencode" / "auth.json"
    return Path.home() / ".local" / "share" / "opencode" / "auth.json"


def read_opencode_go_key(*, path: Path | None = None) -> str:
    auth_path = path or resolve_opencode_auth_path()
    try:
        payload = json.loads(auth_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    entry = payload.get(OPENCODE_GO_PROVIDER_ID) if isinstance(payload, dict) else None
    if not isinstance(entry, dict):
        return ""
    return str(entry.get("key") or "").strip()


def parse_opencode_go_usage(payload: dict[str, Any]) -> list[dict[str, Any]]:
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        raise OpenCodeUsageError("opencode-go 未返回 usage")

    windows: list[dict[str, Any]] = []
    for key in _WINDOW_ORDER:
        window = usage.get(key)
        if not isinstance(window, dict):
            continue
        try:
            used_percent = int(window.get("percent"))
        except (TypeError, ValueError):
            continue
        windows.append(
            {
                "label": _WINDOW_LABELS[key],
                "remaining_percent": max(0, min(100, 100 - used_percent)),
                "reset_at": str(window.get("resetsAt") or ""),
                "status": str(window.get("status") or ""),
            }
        )
    return windows


def read_opencode_go_usage(*, timeout_seconds: float = _OPENCODE_GO_TIMEOUT_SECONDS) -> dict[str, Any]:
    key = read_opencode_go_key()
    if not key:
        return {"available": False, "windows": [], "error": "未找到 opencode-go 凭证"}

    try:
        response = requests.get(
            OPENCODE_GO_USAGE_URL,
            headers={"Authorization": f"Bearer {key}"},
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        return {"available": False, "windows": [], "error": f"读取 opencode Go 用量失败：{exc}"}

    try:
        windows = parse_opencode_go_usage(payload)
    except OpenCodeUsageError as exc:
        return {"available": False, "windows": [], "error": str(exc)}
    return {"available": True, "windows": windows, "error": ""}
