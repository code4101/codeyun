from __future__ import annotations

import os
from pathlib import Path
import sys

from backend.core.settings import ROOT_DIR


def attendance_source_root() -> Path:
    configured = os.getenv("KQ_XLPROJECT_ROOT", "").strip()
    project_root = Path(configured).expanduser().resolve() if configured else (ROOT_DIR.parent / "xlproject").resolve()
    return project_root / "src"


def ensure_attendance_engine_importable() -> Path:
    """CodeYun-side adapter for the separately installed/local attendance system."""

    source_root = attendance_source_root()
    package_root = source_root / "xlsln" / "kq5034"
    if not package_root.is_dir():
        raise RuntimeError(f"独立考勤系统不存在：{package_root}")
    source_text = os.fspath(source_root)
    if source_text not in sys.path:
        sys.path.insert(0, source_text)
    return source_root
