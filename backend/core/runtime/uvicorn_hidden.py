from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path


_LOG_HANDLES: list[object] = []


def _attach_stdio() -> None:
    log_dir = Path(tempfile.gettempdir()) / "codeyun" / "uvicorn-hidden"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "uvicorn.log"
    handle = log_path.open("a", encoding="utf-8", buffering=1)
    _LOG_HANDLES.append(handle)
    sys.stdout = handle
    sys.stderr = handle


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run CodeYun uvicorn without a Windows console.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    if os.name == "nt":
        _attach_stdio()

    from backend.core.runtime.process_launcher import install_child_process_no_window_default

    install_child_process_no_window_default()

    import uvicorn

    args = _parse_args(sys.argv[1:] if argv is None else argv)
    uvicorn.run(
        "backend.app:app",
        host=args.host,
        port=args.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
