from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.import_yuque_journal import BOOK_ID, TZ, db_path, default_data_dir, get_json, yuque_session


SETTING_KEY_TEMPLATE = "note.calendar.year_month_memos.user.{user_id}"
YEAR_TITLE_RE = re.compile(r"^(?P<year>19\d{2}|20\d{2})\s*(?:年)?\s*(?P<title>.*)$")


def clean_year_title(value: str) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    text = re.sub(r"^[：:，,、.\-~\s]+", "", text)
    text = text.strip()
    if text in {"", "年"}:
        return ""
    return text[:80]


def parse_setting_value(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            payload = json.loads(value)
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            pass
    return {}


def normalize_title_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    titles: dict[str, str] = {}
    for key, text in value.items():
        year_key = str(key or "").strip()
        if not re.fullmatch(r"\d{4}", year_key):
            continue
        title = clean_year_title(str(text or ""))
        if title:
            titles[year_key] = title
    return dict(sorted(titles.items()))


def derive_year_titles_from_catalog(catalog: list[dict[str, Any]]) -> dict[str, str]:
    by_uuid = {node.get("uuid"): node for node in catalog if node.get("uuid")}
    volume_uuids = {
        node.get("uuid")
        for node in catalog
        if node.get("uuid") and not node.get("parent_uuid") and str(node.get("title") or "").startswith("卷")
    }

    labels_by_year: dict[str, list[str]] = defaultdict(list)
    for node in catalog:
        parent_uuid = node.get("parent_uuid")
        if parent_uuid not in volume_uuids:
            continue
        if node.get("type") != "DOC":
            continue
        title = str(node.get("title") or "").strip()
        match = YEAR_TITLE_RE.match(title)
        if not match:
            continue
        year = match.group("year")
        label = clean_year_title(match.group("title"))
        if not label:
            continue
        if label not in labels_by_year[year]:
            labels_by_year[year].append(label)

    return {
        year: " / ".join(labels)
        for year, labels in sorted(labels_by_year.items())
        if labels
    }


def sync(args: argparse.Namespace) -> None:
    data_dir = Path(args.data_dir) if args.data_dir else default_data_dir()
    database = db_path(data_dir)
    session = yuque_session()
    catalog = get_json(session, "https://www.yuque.com/api/catalog_nodes", book_id=BOOK_ID)
    derived_titles = derive_year_titles_from_catalog(catalog)

    con = sqlite3.connect(database)
    con.row_factory = sqlite3.Row
    setting_key = SETTING_KEY_TEMPLATE.format(user_id=int(args.user_id))
    row = con.execute("select value from appsetting where key=?", (setting_key,)).fetchone()
    payload = parse_setting_value(row["value"] if row else None)
    existing_memos = payload.get("memos") if isinstance(payload.get("memos"), dict) else {}
    existing_titles = normalize_title_map(payload.get("year_titles"))
    next_titles = (
        {**existing_titles, **derived_titles}
        if args.replace
        else {**derived_titles, **existing_titles}
    )

    print(json.dumps({
        "database": str(database),
        "setting_key": setting_key,
        "derived": derived_titles,
        "existing": existing_titles,
        "next": next_titles,
        "apply": bool(args.apply),
        "replace": bool(args.replace),
    }, ensure_ascii=False, indent=2))

    if not args.apply:
        con.close()
        return

    backup = Path(args.backup) if args.backup else Path.home() / "AppData" / "Local" / "Temp" / f"codeyun_calendar_year_titles_backup_{dt.datetime.now(TZ).strftime('%Y%m%d_%H%M%S')}.db"
    shutil.copy2(database, backup)
    now = time.time()
    next_payload = {
        **payload,
        "version": 1,
        "source": "yuque-catalog-calendar-year-titles",
        "year_titles": next_titles,
        "memos": existing_memos,
    }
    if row:
        con.execute(
            "update appsetting set value=?,updated_at=? where key=?",
            (json.dumps(next_payload, ensure_ascii=False), now, setting_key),
        )
    else:
        con.execute(
            "insert into appsetting (key,value,updated_at) values (?,?,?)",
            (setting_key, json.dumps(next_payload, ensure_ascii=False), now),
        )
    con.commit()
    con.close()
    print(f"Applied {len(next_titles)} year titles. Backup: {backup}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync calendar year titles from Yuque catalog into CodeYun settings.")
    parser.add_argument("--data-dir", default="")
    parser.add_argument("--user-id", type=int, default=2)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--replace", action="store_true", help="Let Yuque-derived titles overwrite existing edited titles.")
    parser.add_argument("--backup", default="")
    args = parser.parse_args()
    sync(args)


if __name__ == "__main__":
    main()
