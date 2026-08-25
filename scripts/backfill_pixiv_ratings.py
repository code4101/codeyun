"""从 Pixiv 官方作品详情补齐历史 xRestrict，供 pixiv/pixi 精确分流。"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from sqlmodel import Session, select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.temp_paths import codeyun_temp_root
from backend.db import engine
from backend.plugins.modules.media_sync.models import MediaSyncSourceItem
from backend.plugins.modules.media_sync.runtime import pixiv_source_activity_lease
from backend.plugins.modules.media_sync.sources import (
    PIXIV_MAX_REMOTE_OPERATIONS,
    get_or_open_pixiv_tab,
    keep_one_domain_tab,
    open_browser,
    pixiv_rating_family,
    pixiv_remote_run_audit,
    wait_for_pixiv_request_slot,
)
from scripts.split_pixiv_rating_directories import load_classifications


ARTWORK_ID_RE = re.compile(r"(?<!\d)(\d{6,})(?!\d)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root-dir", default=r"E:\data\m2510mn")
    parser.add_argument("--user-id", type=int, default=2)
    parser.add_argument("--pixiv-user-id", default="32754614")
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def collect_unknown_ids(root: Path, known: dict[str, int]) -> set[str]:
    unknown: set[str] = set()
    with Session(engine) as session:
        rows = session.exec(
            select(MediaSyncSourceItem).where(
                MediaSyncSourceItem.platform == "pixiv",
                MediaSyncSourceItem.absolute_path.is_not(None),
            )
        ).all()
        for row in rows:
            path = Path(str(row.absolute_path or ""))
            if path.is_file() and (path == root or root in path.parents) and str(row.remote_id) not in known:
                unknown.add(str(row.remote_id))

    for tier_name in ("1、pixiv", "2、pixiv", "3、pixiv"):
        tier_root = root / tier_name
        if not tier_root.exists():
            continue
        for path in tier_root.rglob("*"):
            if not path.is_file() or "_state" in path.relative_to(tier_root).parts:
                continue
            for part in reversed(path.relative_to(tier_root).parts):
                match = ARTWORK_ID_RE.search(part)
                if match:
                    if match.group(1) not in known:
                        unknown.add(match.group(1))
                    break
    return unknown


def persist_results(*, user_id: int, ratings: dict[str, int]) -> int:
    updated = 0
    now = time.time()
    with Session(engine) as session:
        rows = session.exec(
            select(MediaSyncSourceItem).where(
                MediaSyncSourceItem.user_id == user_id,
                MediaSyncSourceItem.platform == "pixiv",
                MediaSyncSourceItem.remote_id.in_(list(ratings)),
            )
        ).all()
        for row in rows:
            x_restrict = int(ratings[str(row.remote_id)])
            row.extra_json = {
                **(row.extra_json or {}),
                "x_restrict": x_restrict,
                "rating_family": pixiv_rating_family(x_restrict),
            }
            row.updated_at = now
            session.add(row)
            updated += 1
        session.commit()
    return updated


def main() -> int:
    args = parse_args()
    root = Path(args.root_dir).expanduser().resolve()
    known = load_classifications(root)
    unknown_ids = sorted(collect_unknown_ids(root, known), key=lambda value: int(value))
    if not args.apply:
        print(json.dumps({"mode": "dry-run", "unknown_artwork_count": len(unknown_ids)}, ensure_ascii=False, indent=2))
        return 0
    if not unknown_ids:
        print(json.dumps({"mode": "applied", "fetched": 0, "errors": 0}, ensure_ascii=False, indent=2))
        return 0
    if len(unknown_ids) + 5 > PIXIV_MAX_REMOTE_OPERATIONS:
        raise RuntimeError(f"未知作品过多，超过单轮 Pixiv 安全预算: {len(unknown_ids)}")

    fetched: dict[str, int] = {}
    errors: dict[str, str] = {}
    browser = None
    tab = None
    with pixiv_source_activity_lease(timeout=0):
        try:
            browser = open_browser()
            tab = get_or_open_pixiv_tab(browser, user_id=args.pixiv_user_id)
            with pixiv_remote_run_audit(
                source="x-restrict-backfill",
                max_remote_operations=len(unknown_ids) + 5,
            ):
                for index, artwork_id in enumerate(unknown_ids, 1):
                    try:
                        wait_for_pixiv_request_slot("detail_api")
                        payload = tab.run_js(
                            f"return fetch('/ajax/illust/{artwork_id}?lang=zh').then(r => r.json())"
                        )
                        if payload.get("error"):
                            raise RuntimeError(payload.get("message") or "Pixiv 作品详情接口失败")
                        body = payload.get("body") or {}
                        fetched[artwork_id] = max(int(body.get("xRestrict") or 0), 0)
                    except Exception as exc:
                        errors[artwork_id] = str(exc)
                    if index % 20 == 0:
                        print(f"Pixiv xRestrict 回填进度 {index}/{len(unknown_ids)}", flush=True)
        finally:
            if browser is not None:
                keep_one_domain_tab(browser, "pixiv.net", preferred_tab=tab)

    cache_path = codeyun_temp_root("pixiv-rating") / "x-restrict.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    merged = {}
    if cache_path.exists():
        merged.update(json.loads(cache_path.read_text(encoding="utf-8")))
    merged.update(fetched)
    temporary = cache_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(cache_path)
    updated_rows = persist_results(user_id=args.user_id, ratings=fetched)
    report_path = codeyun_temp_root("pixiv-rating") / "backfill-report.json"
    report_path.write_text(
        json.dumps({"fetched": fetched, "errors": errors}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "mode": "applied",
                "requested": len(unknown_ids),
                "fetched": len(fetched),
                "r18": sum(1 for value in fetched.values() if value > 0),
                "errors": len(errors),
                "updated_source_rows": updated_rows,
                "cache_path": str(cache_path),
                "report_path": str(report_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
