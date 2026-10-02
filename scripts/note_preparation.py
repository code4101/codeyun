"""Prepare notes without running their business intentions.

Examples (repository root):
  uv run python scripts/note_preparation.py snapshot --owner-id 2
  uv run python scripts/note_preparation.py discover --owner-id 2 --workers 12 --limit 40
  uv run python scripts/note_preparation.py deepen --owner-id 2 --workers 12 --limit 40
  uv run python scripts/note_preparation.py status --owner-id 2

Each finite batch resumes through a content-addressed cache. Source snapshots and
research packets remain in CODEYUN_DATA_DIR/note-preparation/<owner>, never TEMP.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# CodeYun's environment authority is the sibling xlproject package.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "xlproject" / "src"))
import xlproject.loadenv  # noqa: F401,E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["snapshot", "discover", "deepen", "status"])
    parser.add_argument("--owner-id", type=int, required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--note-limit", type=int, default=5000)
    parser.add_argument("--kind", choices=["pg", "note", "all"], default="pg")
    args = parser.parse_args()
    from backend.core.note_preparation import PreparationStore, collect_sources
    store = PreparationStore(args.owner_id)
    if args.action == "snapshot":
        from backend.api.auth import list_account_user_options
        from backend.db import engine
        from backend.models import User
        from sqlmodel import Session
        with Session(engine) as session:
            identity = User(id=0, username="local-preparation", hashed_password="")
            options = list_account_user_options(q="", limit=100, session=session, current_user=identity)
            owner = next((user for user in options.users if user.id == args.owner_id), None)
            if owner is None:
                raise ValueError("指定用户不在公开用户列表中，请核对身份")
            user = User(id=owner.id, username=owner.username, hashed_password="")
            sources, coverage = asyncio.run(collect_sources(session, user, note_limit=args.note_limit))
        snapshot = store.cache_snapshot(sources, coverage)
        result = {"snapshot_id": snapshot["id"], "source_count": len(sources), "coverage": coverage}
    elif args.action == "status":
        overview = store.overview()
        result = {"path": str(store.root), "packets": len(overview["packets"]),
                  "coverage": (overview["snapshot"] or {}).get("coverage"), "last_runs": overview["runs"][-8:]}
    else:
        from backend.core.note_preparation_research import run_research
        result = run_research(args.owner_id, phase=args.action, workers=args.workers, limit=args.limit, kind=args.kind)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
