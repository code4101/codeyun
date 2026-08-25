from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


GUIDE_PATH = Path(__file__).with_name("guide.json")


@lru_cache(maxsize=1)
def load_guigubahuang_guide() -> dict[str, Any]:
    """Load the curated guide snapshot without touching the game installation."""

    return json.loads(GUIDE_PATH.read_text(encoding="utf-8"))
