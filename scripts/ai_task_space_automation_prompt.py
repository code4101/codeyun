from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.core.ai_task_space import build_automation_prompt


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    _configure_stdio()
    parser = argparse.ArgumentParser(description="Print the Codex automation prompt for AI task-space automation execution.")
    parser.add_argument("--username", help="CodeYun username to pass to the planning check script.")
    args = parser.parse_args()

    print(build_automation_prompt(args.username or ""))


if __name__ == "__main__":
    main()

