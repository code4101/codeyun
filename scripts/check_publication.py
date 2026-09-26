"""Reject private material before publishing Git objects.

Policies live in ignored plugin directories as publication-policy.json, with
``patterns`` (case-insensitive regular expressions). No policy contents or
matched source lines are printed. --staged checks the next commit; --refs
checks every reachable blob, filename and commit message, including deletions.
The latter is intended for a local pre-push hook, not only a HEAD scan.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args])


def check(patterns: list[str], *, refs: list[str] | None = None) -> list[str]:
    if not patterns:
        return []
    matcher = re.compile("|".join(f"(?:{pattern})" for pattern in patterns), re.I)
    objects: dict[str, str] = {}
    failures: list[str] = []
    if refs:
        for line in git("rev-list", "--objects", *refs).decode("utf-8", "replace").splitlines():
            oid, _, path = line.partition(" ")
            objects[oid] = path
        # A blob may occur at several paths, and rev-list lists only one of them.
        for ref in git("rev-list", *refs).decode().splitlines():
            for path in git("ls-tree", "-rz", "--name-only", ref).decode("utf-8", "replace").split("\0"):
                if matcher.search(path):
                    failures.append(f"private filename in {ref[:12]}")
                    break
    else:
        for entry in git("ls-files", "--stage", "-z").decode("utf-8", "replace").split("\0"):
            if not entry:
                continue
            metadata, path = entry.split("\t", 1)
            _, oid, stage = metadata.split()
            if stage != "0":
                failures.append("unmerged index")
            if matcher.search(path):
                failures.append("private filename in index")
            objects[oid] = path
    with subprocess.Popen(["git", "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE) as process:
        assert process.stdin is not None and process.stdout is not None
        for oid, path in objects.items():
            process.stdin.write((oid + "\n").encode())
            process.stdin.flush()
            header = process.stdout.readline().split()
            if len(header) != 3:
                raise RuntimeError(f"Cannot inspect Git object {oid}")
            content = process.stdout.read(int(header[2]))
            process.stdout.read(1)
            kind = header[1]
            if kind == b"commit":
                content = content.partition(b"\n\n")[2]
            if kind in (b"blob", b"commit", b"tag") and matcher.search(content.decode("utf-8", "replace")):
                failures.append(f"private {kind.decode()} {oid[:12]}")
        process.stdin.close()
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", action="append", type=Path)
    parser.add_argument("--staged", action="store_true")
    parser.add_argument("--refs", nargs="+")
    args = parser.parse_args()
    root = Path(git("rev-parse", "--show-toplevel").decode().strip())
    policies = args.policy or sorted((root / "backend/plugins/modules").glob("*/publication-policy.json"))
    patterns = [pattern for path in policies for pattern in json.loads(path.read_text(encoding="utf-8"))["patterns"]]
    failures = check(patterns, refs=args.refs)
    for failure in failures[:20]:
        print(failure, file=sys.stderr)
    print(f"Publication check: {len(policies)} policies; {len(failures)} violations")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
