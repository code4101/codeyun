from __future__ import annotations

import tempfile
from pathlib import Path


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

