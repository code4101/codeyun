"""Bind the desktop provider or retain an actual native Goal tool result."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('bind', help='Bind from the authorized Codex desktop executor')
    record = commands.add_parser('record-goal', help='Save a captured native Goal tool result unchanged')
    record.add_argument('dispatch_id')
    record.add_argument('result_json')
    args = parser.parse_args()
    if args.command == 'bind':
        from backend.core.codex.desktop import bind_codex_desktop
        result = bind_codex_desktop()
    else:
        from backend.core.codex.escalation import record_desktop_goal_result
        result = record_desktop_goal_result(args.dispatch_id, json.loads(args.result_json))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
