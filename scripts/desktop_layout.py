"""Inspect or restore the managed desktop layout; --apply corrects drift."""
from pathlib import Path
import argparse
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--token', help='Reuse an existing desktop lease')
    args = parser.parse_args()
    from backend.core.attendance.independent_engine_adapter import ensure_attendance_engine_importable
    ensure_attendance_engine_importable()
    import xlproject.loadenv
    from backend.core.runtime.desktop_layout import run_desktop_layout
    print(json.dumps(run_desktop_layout(apply=args.apply, token=args.token), ensure_ascii=False, indent=2))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
