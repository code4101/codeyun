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

from backend.core.fanxiu_resources import resolve_fanxiu_export_root


DEFAULT_API_BASE = "http://127.0.0.1:8000/api"
ICON_FIELDS = ("icon", "small_icon", "quality_icon", "head_icon")


@dataclass(frozen=True)
class CatalogSpec:
    name: str
    list_path: str
    detail_path: str
    detail_param: str
    id_fields: tuple[str, ...]
    title_fields: tuple[str, ...] = ("name", "title", "title_name", "little_name")
    require_icon: bool = True
    check_detail: bool = True
    min_total: int = 1


CATALOG_SPECS = (
    CatalogSpec("gongfa", "/fanxiu/resources/gongfa/cards", "/fanxiu/resources/gongfa/card", "gongfa_id", ("id",), min_total=100),
    CatalogSpec("item", "/fanxiu/resources/items/cards", "/fanxiu/resources/items/card", "item_id", ("id",), min_total=1000),
    CatalogSpec(
        "activity",
        "/fanxiu/resources/activities/cards",
        "/fanxiu/resources/activities/card",
        "activity_id",
        ("id", "base_id"),
        require_icon=False,
        check_detail=False,
        min_total=1000,
    ),
    CatalogSpec(
        "lingjie",
        "/fanxiu/resources/gongfa/lingjie-feature-cards",
        "/fanxiu/resources/gongfa/lingjie-feature-card",
        "gongfa_id",
        ("gongfa_id", "id"),
        min_total=10,
    ),
    CatalogSpec(
        "digitdoor_character",
        "/fanxiu/resources/digitdoor/character-cards",
        "/fanxiu/resources/digitdoor/character-card",
        "character_id",
        ("id", "character_id"),
        min_total=5,
    ),
    CatalogSpec(
        "doupotd_partner",
        "/fanxiu/resources/doupotd/partner-cards",
        "/fanxiu/resources/doupotd/partner-card",
        "partner_id",
        ("id", "partner_id"),
        min_total=5,
    ),
)


def _get_json(url: str, *, params: dict[str, Any] | None = None, timeout: int = 30, attempts: int = 3) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            response = requests.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            last_error = exc
            if attempt >= attempts:
                break
            time.sleep(min(2.0, 0.35 * attempt))
    assert last_error is not None
    raise last_error


def _check_icon(api_base: str, icon: str, timeout: int) -> dict[str, Any]:
    try:
        response = requests.get(
            f"{api_base.rstrip('/')}/fanxiu/resources/icon",
            params={"name": icon},
            timeout=timeout,
        )
    except Exception as exc:
        return {"ok": False, "status_code": "", "content_type": "", "size": 0, "detail": str(exc)}
    content_type = response.headers.get("content-type", "")
    ok = response.status_code == 200 and content_type.startswith("image/") and len(response.content) > 0
    detail = ""
    if not ok:
        try:
            detail = response.json().get("detail", "")
        except Exception:
            detail = response.text[:240]
    return {
        "ok": ok,
        "status_code": response.status_code,
        "content_type": content_type,
        "size": len(response.content),
        "detail": detail,
    }


def _items_from_list_response(data: dict[str, Any]) -> list[dict[str, Any]]:
    rows = data.get("items") or data.get("cards") or data.get("rows") or []
    return [row for row in rows if isinstance(row, dict)]


def _sample_offsets(total: int, sample_count: int) -> list[int]:
    if total <= 0 or sample_count <= 0:
        return []
    if total <= sample_count:
        return list(range(total))
    offsets: list[int] = []
    for index in range(sample_count):
        offset = round(index * (total - 1) / (sample_count - 1))
        if offset not in offsets:
            offsets.append(offset)
    return offsets


def _fetch_sample_rows(api_base: str, spec: CatalogSpec, total: int, first_rows: list[dict[str, Any]], args: argparse.Namespace) -> list[tuple[int, dict[str, Any]]]:
    offsets = _sample_offsets(total, args.detail_sample)
    first_by_offset = {index: row for index, row in enumerate(first_rows)}
    samples: list[tuple[int, dict[str, Any]]] = []
    for offset in offsets:
        if offset in first_by_offset:
            samples.append((offset, first_by_offset[offset]))
            continue
        data = _get_json(
            f"{api_base}{spec.list_path}",
            params={"limit": 1, "offset": offset},
            timeout=args.api_timeout,
        )
        rows = _items_from_list_response(data)
        if rows:
            samples.append((offset, rows[0]))
        else:
            samples.append((offset, {}))
    return samples


def _card_from_detail_response(data: dict[str, Any]) -> dict[str, Any]:
    card = data.get("card")
    return card if isinstance(card, dict) else data


def _first_text(row: dict[str, Any], fields: tuple[str, ...]) -> str:
    for field in fields:
        text = str(row.get(field) or "").strip()
        if text:
            return text
    return ""


def _icon_values(row: dict[str, Any]) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    seen: set[str] = set()
    for field in ICON_FIELDS:
        icon = str(row.get(field) or "").strip()
        if not icon or icon in {"-", "0", "None", "null"} or icon in seen:
            continue
        seen.add(icon)
        values.append((field, icon))
    return values


def _write_tsv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _audit_catalog(spec: CatalogSpec, args: argparse.Namespace) -> dict[str, Any]:
    api_base = args.api_base.rstrip("/")
    list_data = _get_json(
        f"{api_base}{spec.list_path}",
        params={"limit": args.limit, "offset": 0},
        timeout=args.api_timeout,
    )
    rows = _items_from_list_response(list_data)
    total = int(list_data.get("total") or len(rows) or 0)
    sample_rows = _fetch_sample_rows(api_base, spec, total, rows, args)
    detail_failures: list[dict[str, Any]] = []
    field_failures: list[dict[str, Any]] = []
    icon_failures: list[dict[str, Any]] = []
    icon_checks = 0
    rows_with_icon = 0
    details_ok = 0

    if total < spec.min_total:
        field_failures.append(
            {
                "catalog": spec.name,
                "kind": "total_below_floor",
                "expected": spec.min_total,
                "observed": total,
                "item_id": "",
                "title": "",
            }
        )

    for row_index, row in sample_rows:
        item_id = _first_text(row, spec.id_fields)
        title = _first_text(row, spec.title_fields)
        icons = _icon_values(row)
        if icons:
            rows_with_icon += 1
        if not item_id:
            field_failures.append({"catalog": spec.name, "kind": "missing_id", "item_id": "", "title": title, "row_index": row_index})
            continue
        if not title:
            field_failures.append({"catalog": spec.name, "kind": "missing_title", "item_id": item_id, "title": "", "row_index": row_index})
        if spec.require_icon and not icons:
            field_failures.append({"catalog": spec.name, "kind": "missing_icon", "item_id": item_id, "title": title, "row_index": row_index})

        if not spec.check_detail:
            continue
        try:
            detail_data = _get_json(
                f"{api_base}{spec.detail_path}",
                params={spec.detail_param: item_id},
                timeout=args.api_timeout,
            )
            detail = _card_from_detail_response(detail_data)
            detail_id = _first_text(detail, spec.id_fields)
            detail_title = _first_text(detail, spec.title_fields)
            if not detail_id:
                field_failures.append({"catalog": spec.name, "kind": "detail_missing_id", "item_id": item_id, "title": title, "row_index": row_index})
            if not detail_title:
                field_failures.append({"catalog": spec.name, "kind": "detail_missing_title", "item_id": item_id, "title": title, "row_index": row_index})
            details_ok += 1
            for field, icon in _icon_values(detail):
                if args.max_icons_per_catalog > 0 and icon_checks >= args.max_icons_per_catalog:
                    continue
                icon_checks += 1
                check = _check_icon(api_base, icon, args.api_timeout)
                if not check["ok"]:
                    icon_failures.append(
                        {
                            "catalog": spec.name,
                            "item_id": item_id,
                            "title": title or detail_title,
                            "field": field,
                            "icon": icon,
                            **check,
                        }
                    )
        except Exception as exc:
            detail_failures.append(
                {
                    "catalog": spec.name,
                    "item_id": item_id,
                    "title": title,
                    "detail_path": spec.detail_path,
                    "detail": str(exc),
                }
            )

    return {
        "catalog": spec.name,
        "total": total,
        "sample_count": len(sample_rows),
        "rows_with_icon": rows_with_icon,
        "details_ok": details_ok,
        "detail_failures": detail_failures,
        "field_failures": field_failures,
        "icon_failures": icon_failures,
    }


def audit_card_catalogs(args: argparse.Namespace) -> dict[str, Any]:
    export_root = resolve_fanxiu_export_root(args.export_root or None)
    output_dir = export_root / "parsed_configs" / "wiki_catalog_audit"
    results = [_audit_catalog(spec, args) for spec in CATALOG_SPECS]
    detail_failures = [row for result in results for row in result["detail_failures"]]
    field_failures = [row for result in results for row in result["field_failures"]]
    icon_failures = [row for result in results for row in result["icon_failures"]]

    _write_tsv(
        output_dir / "card_catalogs_latest.tsv",
        [
            {
                "catalog": result["catalog"],
                "total": result["total"],
                "sample_count": result["sample_count"],
                "rows_with_icon": result["rows_with_icon"],
                "details_ok": result["details_ok"],
            }
            for result in results
        ],
        ["catalog", "total", "sample_count", "rows_with_icon", "details_ok"],
    )
    _write_tsv(output_dir / "card_catalog_detail_failures_latest.tsv", detail_failures, ["catalog", "item_id", "title", "detail_path", "detail"])
    _write_tsv(output_dir / "card_catalog_field_failures_latest.tsv", field_failures, ["catalog", "kind", "item_id", "title", "row_index", "expected", "observed"])
    _write_tsv(output_dir / "card_catalog_icon_failures_latest.tsv", icon_failures, ["catalog", "item_id", "title", "field", "icon", "status_code", "content_type", "size", "detail"])

    summary = {
        "catalog_count": len(results),
        "total_cards": sum(int(result["total"]) for result in results),
        "sample_count": sum(int(result["sample_count"]) for result in results),
        "details_ok": sum(int(result["details_ok"]) for result in results),
        "detail_failure_count": len(detail_failures),
        "field_failure_count": len(field_failures),
        "icon_failure_count": len(icon_failures),
        "catalogs": [
            {
                "catalog": result["catalog"],
                "total": result["total"],
                "sample_count": result["sample_count"],
                "rows_with_icon": result["rows_with_icon"],
                "details_ok": result["details_ok"],
                "detail_failure_count": len(result["detail_failures"]),
                "field_failure_count": len(result["field_failures"]),
                "icon_failure_count": len(result["icon_failures"]),
            }
            for result in results
        ],
        "output_dir": str(output_dir),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary_latest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Fanxiu wiki card catalog breadth, detail endpoints, and card icon resources.")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--export-root", default="")
    parser.add_argument("--limit", type=int, default=80)
    parser.add_argument("--detail-sample", type=int, default=8)
    parser.add_argument("--max-icons-per-catalog", type=int, default=12)
    parser.add_argument("--api-timeout", type=int, default=30)
    args = parser.parse_args()

    summary = audit_card_catalogs(args)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["detail_failure_count"] or summary["field_failure_count"] or summary["icon_failure_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
