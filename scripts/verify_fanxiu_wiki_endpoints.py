from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root


DEFAULT_API_BASE = "http://127.0.0.1:8000/api"


@dataclass(frozen=True)
class EndpointCheck:
    name: str
    path: str
    params: dict[str, Any]
    auth_expected: bool = False
    min_count: int | None = None
    max_bytes: int | None = None
    required_item_fields: tuple[str, ...] = ()


CHECKS = [
    EndpointCheck("summary", "/fanxiu/resources/summary", {}),
    EndpointCheck("wiki_catalog", "/fanxiu/resources/wiki/catalog", {}),
    EndpointCheck("wiki_link_index", "/fanxiu/resources/wiki/link-index", {}, min_count=1, required_item_fields=("tab", "id", "title", "kind")),
    EndpointCheck("wiki_texts", "/fanxiu/resources/wiki/texts", {"limit": 3}, min_count=1, required_item_fields=("id", "title", "plain_preview")),
    EndpointCheck("wiki_gallery", "/fanxiu/resources/wiki/gallery", {"limit": 3}, min_count=1, required_item_fields=("kind", "name", "path")),
    EndpointCheck("gongfa", "/fanxiu/resources/gongfa/cards", {"limit": 3}, min_count=1, required_item_fields=("id", "name", "icon", "quality_name")),
    EndpointCheck(
        "item",
        "/fanxiu/resources/items/cards",
        {"limit": 3, "include_facets": "false"},
        min_count=1,
        max_bytes=150_000,
        required_item_fields=("id", "name", "icon", "type_name"),
    ),
    EndpointCheck(
        "activity",
        "/fanxiu/resources/activities/cards",
        {"limit": 3, "include_facets": "false"},
        min_count=1,
        max_bytes=100_000,
        required_item_fields=("id", "name", "source_table", "presence_status"),
    ),
    EndpointCheck("lingjie", "/fanxiu/resources/gongfa/lingjie-feature-cards", {"limit": 3}, min_count=1, required_item_fields=("gongfa_id", "name", "icon")),
    EndpointCheck("digitdoor_character", "/fanxiu/resources/digitdoor/character-cards", {"limit": 3}, min_count=1, required_item_fields=("id", "name", "icon", "skill_name")),
    EndpointCheck("digitdoor_level", "/fanxiu/resources/digitdoor/level-configs", {"limit": 3}, min_count=1, required_item_fields=("id", "name", "stage", "reward_preview")),
    EndpointCheck("digitdoor_enhance", "/fanxiu/resources/digitdoor/enhance-groups", {"limit": 3}, min_count=1, required_item_fields=("id", "char_id", "name")),
    EndpointCheck("doupotd_partner", "/fanxiu/resources/doupotd/partner-cards", {"limit": 3}, min_count=1, required_item_fields=("id", "name", "icon", "skill_name")),
    EndpointCheck("doupotd_reward", "/fanxiu/resources/doupotd/reward-configs", {"limit": 3}, min_count=1, required_item_fields=("config_id", "name", "reward_items")),
    EndpointCheck("visual", "/fanxiu/resources/visual/manifest", {"limit": 3}, min_count=1, required_item_fields=("name", "media_url", "source_kind")),
    EndpointCheck("asset", "/fanxiu/resources/asset/manifest", {"limit": 3}, min_count=1, required_item_fields=("asset_id", "name", "relative_path")),
    EndpointCheck("audio", "/fanxiu/resources/wwise/mp3-manifest", {"limit": 3}, min_count=1, required_item_fields=("source_bank", "wem_id", "media_url")),
    EndpointCheck("protocol", "/fanxiu/resources/protocol-semantics", {"limit": 3, "edge_limit": 3}, min_count=1, required_item_fields=("id", "packet", "operation")),
    EndpointCheck("mail", "/fanxiu/mail-records", {"limit": 3, "source": "all"}, auth_expected=True),
    EndpointCheck("storage_bag", "/fanxiu/business-data/storage-bag", {}, auth_expected=True),
    EndpointCheck("player_profile", "/fanxiu/business-data/player-profiles", {"limit": 3}, auth_expected=True),
]


def _extract_count(data: Any) -> int | str:
    if not isinstance(data, dict):
        return ""
    for key in ("total", "filtered", "count"):
        value = data.get(key)
        if isinstance(value, int):
            return value
    for key in ("items", "cards", "rows", "edges"):
        value = data.get(key)
        if isinstance(value, list):
            return len(value)
    counts = data.get("counts")
    if isinstance(counts, dict):
        for key in ("rows", "total", "cards"):
            value = counts.get(key)
            if isinstance(value, int):
                return value
    stats = data.get("stats")
    if isinstance(stats, dict):
        for key in ("card_count", "audio_file_count", "bank_scan_count"):
            value = stats.get(key)
            if isinstance(value, int):
                return value
    return ""


def _extract_items(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    for key in ("items", "cards", "rows", "edges"):
        value = data.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    return []


def _has_field_value(row: dict[str, Any], field: str) -> bool:
    if field not in row:
        return False
    value = row.get(field)
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return True


def _required_field_failures(data: Any, required_fields: tuple[str, ...]) -> list[str]:
    if not required_fields:
        return []
    rows = _extract_items(data)
    if not rows:
        return [f"no list rows available for required fields {','.join(required_fields)}"]
    failures: list[str] = []
    for index, row in enumerate(rows[:3]):
        missing = [field for field in required_fields if not _has_field_value(row, field)]
        if missing:
            row_id = row.get("id") or row.get("gongfa_id") or row.get("config_id") or row.get("name") or index
            failures.append(f"row {index} ({row_id}) missing {','.join(missing)}")
    return failures


def _extract_detail(response: requests.Response, data: Any) -> str:
    if isinstance(data, dict):
        detail = data.get("detail") or data.get("message")
        if detail:
            return str(detail)[:500]
    if response.ok:
        return ""
    return response.text[:500]


def _write_tsv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "name",
        "url",
        "status",
        "ok",
        "auth_expected",
        "warning",
        "elapsed_seconds",
        "count",
        "bytes",
        "detail",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def run_checks(api_base: str, timeout: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    base = api_base.rstrip("/")
    for check in CHECKS:
        url = f"{base}{check.path}"
        started = time.perf_counter()
        status: int | str = ""
        data: Any = None
        detail = ""
        body_bytes = 0
        try:
            response = requests.get(url, params=check.params, timeout=timeout)
            elapsed = time.perf_counter() - started
            status = response.status_code
            body_bytes = len(response.content)
            try:
                data = response.json()
            except ValueError:
                data = None
            detail = _extract_detail(response, data)
            ok = response.ok or (check.auth_expected and response.status_code in {401, 403})
            count = _extract_count(data)
            warning = ""
            if ok and check.min_count is not None and isinstance(count, int) and count < check.min_count:
                ok = False
                warning = f"count {count} below expected minimum {check.min_count}"
                detail = warning if not detail else f"{warning}; {detail}"
            if ok and check.max_bytes is not None and body_bytes > check.max_bytes:
                ok = False
                warning = f"body {body_bytes} bytes above expected maximum {check.max_bytes}"
                detail = warning if not detail else f"{warning}; {detail}"
            if ok and check.required_item_fields:
                field_failures = _required_field_failures(data, check.required_item_fields)
                if field_failures:
                    ok = False
                    warning = "required fields missing"
                    field_detail = "; ".join(field_failures)
                    detail = field_detail if not detail else f"{field_detail}; {detail}"
        except Exception as exc:
            elapsed = time.perf_counter() - started
            ok = False
            count = ""
            warning = ""
            detail = f"{type(exc).__name__}: {exc}"
        rows.append(
            {
                "name": check.name,
                "url": requests.Request("GET", url, params=check.params).prepare().url,
                "status": status,
                "ok": ok,
                "auth_expected": check.auth_expected,
                "warning": warning,
                "elapsed_seconds": round(elapsed, 3),
                "count": count,
                "bytes": body_bytes,
                "detail": detail,
            }
        )
        print(f"{check.name}: status={status} ok={ok} elapsed={elapsed:.2f}s count={rows[-1]['count']} {detail}", flush=True)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Fanxiu wiki API endpoints used by the resource atlas page.")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--export-root", default="")
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()

    export_root = resolve_fanxiu_export_root(args.export_root or None)
    output_dir = export_root / "parsed_configs" / "wiki_endpoint_audit"
    rows = run_checks(args.api_base, args.timeout)
    failures = [row for row in rows if not row["ok"]]
    slow = [row for row in rows if float(row["elapsed_seconds"]) > args.timeout * 0.75 and row["ok"]]
    _write_tsv(output_dir / "endpoints_latest.tsv", rows)
    _write_tsv(output_dir / "endpoint_failures_latest.tsv", failures)
    summary = {
        "endpoint_count": len(rows),
        "failure_count": len(failures),
        "slow_count": len(slow),
        "auth_expected_count": sum(1 for row in rows if row["auth_expected"]),
        "output_dir": str(output_dir),
    }
    (output_dir / "summary_latest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
