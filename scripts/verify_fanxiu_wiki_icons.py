from __future__ import annotations

import argparse
import asyncio
import csv
import json
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
import websockets

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root


DEFAULT_API_BASE = "http://127.0.0.1:8000/api"
DEFAULT_FRONTEND_BASE = "http://127.0.0.1:5173"
DEFAULT_CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


@dataclass(frozen=True)
class IconUse:
    source: str
    item_id: str
    item_name: str
    field: str
    icon: str


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _icon_value(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text in {"-", "0", "None", "null"}:
        return ""
    return text


def _collect_from_cards(source: str, cards: list[dict[str, Any]]) -> list[IconUse]:
    uses: list[IconUse] = []
    for card in cards:
        item_id = str(card.get("id") or card.get("key") or card.get("source_row_key") or "")
        item_name = str(card.get("name") or card.get("title") or "")
        for field in ("icon", "small_icon", "quality_icon", "head_icon"):
            icon = _icon_value(card.get(field))
            if icon:
                uses.append(IconUse(source, item_id, item_name, field, icon))
    return uses


def _collect_local_item_icons(export_root: Path, limit: int | None = None) -> list[IconUse]:
    catalog_path = export_root / "parsed_configs" / "item_catalog" / "item_catalog.json"
    data = _read_json(catalog_path)
    cards = list(data.get("cards") or [])
    if limit is not None:
        cards = cards[:limit]
    return _collect_from_cards("item_catalog", cards)


def _items_from_list_response(data: dict[str, Any]) -> list[dict[str, Any]]:
    cards = data.get("items") or data.get("cards") or data.get("rows") or []
    return cards if isinstance(cards, list) else []


def _fetch_endpoint_page(api_base: str, source: str, path: str, params: dict[str, Any], timeout: int) -> tuple[list[dict[str, Any]], int]:
    url = f"{api_base.rstrip('/')}{path}"
    started = time.perf_counter()
    response = requests.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    cards = _items_from_list_response(data)
    total = int(data.get("total") or len(cards) or 0)
    print(
        f"{source}: {len(cards)} rows offset={params.get('offset', 0)} total={total} in {time.perf_counter() - started:.1f}s",
        flush=True,
    )
    return cards, total


def _fetch_endpoint_icons(
    api_base: str,
    source: str,
    path: str,
    *,
    page_size: int,
    timeout: int,
    full_scan: bool,
) -> list[IconUse]:
    all_cards: list[dict[str, Any]] = []
    offset = 0
    first_page = True
    total = 0
    while first_page or (full_scan and offset < total):
        first_page = False
        cards, total = _fetch_endpoint_page(
            api_base,
            source,
            path,
            {"limit": page_size, "offset": offset},
            timeout,
        )
        all_cards.extend(cards)
        if not full_scan or not cards:
            break
        offset += page_size
    return _collect_from_cards(source, all_cards)


def _collect_api_page_icons(api_base: str, limit: int, timeout: int) -> list[IconUse]:
    page_size = max(1, limit)
    endpoints = [
        ("gongfa_api_full", "/fanxiu/resources/gongfa/cards", True),
        ("activity_api_page", "/fanxiu/resources/activities/cards", False),
        ("lingjie_api_full", "/fanxiu/resources/gongfa/lingjie-feature-cards", True),
        ("digitdoor_character_api_full", "/fanxiu/resources/digitdoor/character-cards", True),
        ("doupotd_partner_api_full", "/fanxiu/resources/doupotd/partner-cards", True),
    ]
    uses: list[IconUse] = []
    for source, path, full_scan in endpoints:
        try:
            uses.extend(
                _fetch_endpoint_icons(
                    api_base,
                    source,
                    path,
                    page_size=page_size,
                    timeout=timeout,
                    full_scan=full_scan,
                )
            )
        except Exception as exc:
            print(f"{source}: ERROR {exc}", flush=True)
            uses.append(IconUse(source, "", f"ENDPOINT_ERROR: {exc}", "endpoint", ""))
    return uses


def _get_with_retry(url: str, *, params: dict[str, Any] | None = None, timeout: int = 60, attempts: int = 3) -> requests.Response:
    last_exc: Exception | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return requests.get(url, params=params, timeout=timeout)
        except requests.RequestException as exc:
            last_exc = exc
            if attempt >= attempts:
                break
            time.sleep(min(2.0, 0.35 * attempt))
    assert last_exc is not None
    raise last_exc


def _response_to_icon_check(response: requests.Response, icon: str = "") -> dict[str, Any]:
    content_type = response.headers.get("content-type", "")
    ok = response.status_code == 200 and content_type.startswith("image/")
    detail = ""
    if not ok:
        try:
            detail = response.json().get("detail", "")
        except Exception:
            detail = response.text[:240]
    return {
        "icon": icon,
        "ok": ok,
        "status_code": response.status_code,
        "content_type": content_type,
        "size": len(response.content),
        "detail": detail,
    }


def _check_icon_endpoint(api_base: str, icon: str) -> dict[str, Any]:
    url = f"{api_base.rstrip('/')}/fanxiu/resources/icon"
    last_check: dict[str, Any] | None = None
    for attempt in range(1, 4):
        try:
            response = _get_with_retry(url, params={"name": icon}, timeout=60, attempts=1)
            last_check = _response_to_icon_check(response, icon)
        except Exception as exc:
            last_check = {"icon": icon, "ok": False, "status_code": "", "content_type": "", "size": 0, "detail": str(exc)}
        if last_check["ok"] or attempt >= 3:
            return last_check
        time.sleep(min(1.5, 0.35 * attempt))
    assert last_check is not None
    return last_check


def _check_image_url(url: str) -> dict[str, Any]:
    last_check: dict[str, Any] | None = None
    for attempt in range(1, 4):
        try:
            response = _get_with_retry(url, timeout=20, attempts=1)
            last_check = _response_to_icon_check(response)
            last_check.pop("icon", None)
        except Exception as exc:
            last_check = {"ok": False, "status_code": "", "content_type": "", "size": 0, "detail": str(exc)}
        if last_check["ok"] or attempt >= 3:
            return last_check
        time.sleep(min(1.5, 0.35 * attempt))
    assert last_check is not None
    return last_check


def _write_tsv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _icon_reuse_risk_for_fields(fields: set[str]) -> str:
    if fields == {"small_icon"}:
        return "high_reuse_small_icon"
    if fields == {"head_icon"}:
        return "high_reuse_head_icon"
    if "icon" in fields:
        return "high_reuse_primary_icon"
    return "high_reuse_mixed_icon"


def _build_icon_quality_risk_rows(icon_to_uses: dict[str, list[IconUse]], *, primary_reuse_threshold: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    primary_fields = {"icon", "small_icon", "head_icon"}
    for icon, icon_uses in sorted(icon_to_uses.items(), key=lambda item: (-len(item[1]), item[0])):
        primary_uses = [use for use in icon_uses if use.field in primary_fields]
        if len(primary_uses) < primary_reuse_threshold:
            continue
        samples = []
        type_sources = {use.source for use in primary_uses}
        fields = {use.field for use in primary_uses}
        item_catalog_use_count = sum(1 for use in primary_uses if use.source == "item_catalog")
        if type_sources == {"item_catalog"}:
            source_scope = "item_catalog_only"
        elif len(type_sources) == 1:
            source_scope = "other_catalog_only"
        else:
            source_scope = "cross_catalog"
        for use in primary_uses:
            label = f"{use.item_id}:{use.item_name}".strip(":")
            if label and label not in samples:
                samples.append(label)
            if len(samples) >= 12:
                break
        rows.append(
            {
                "icon": icon,
                "primary_use_count": len(primary_uses),
                "item_catalog_use_count": item_catalog_use_count,
                "total_use_count": len(icon_uses),
                "risk": _icon_reuse_risk_for_fields(fields),
                "source_scope": source_scope,
                "sources": ",".join(sorted(type_sources)),
                "fields": ",".join(sorted(fields)),
                "samples": " | ".join(samples),
            }
        )
    return rows


def _count_icon_quality_risks_by_type(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        risk = str(row.get("risk") or "").strip()
        if not risk:
            continue
        counts[risk] = counts.get(risk, 0) + 1
    return counts


def _count_icon_quality_risks_by_scope(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        scope = str(row.get("source_scope") or "").strip()
        if not scope:
            continue
        counts[scope] = counts.get(scope, 0) + 1
    return counts


async def _cdp_call(ws: Any, seq: int, method: str, params: dict[str, Any] | None = None) -> Any:
    await ws.send(json.dumps({"id": seq, "method": method, "params": params or {}}))
    while True:
        message = json.loads(await ws.recv())
        if message.get("id") == seq:
            if "error" in message:
                raise RuntimeError(message["error"])
            return message.get("result")


async def _scan_page_broken_images(chrome_path: str, url: str, wait_ms: int) -> dict[str, Any]:
    port = 9222 + int(time.time() * 1000) % 1000
    profile = Path(tempfile.gettempdir()) / "codeyun" / f"chrome-wiki-icon-audit-{port}"
    profile.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [
            chrome_path,
            "--headless=new",
            "--disable-gpu",
            "--disable-extensions",
            "--no-first-run",
            "--no-default-browser-check",
            f"--user-data-dir={profile}",
            f"--remote-debugging-port={port}",
            url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        list_url = f"http://127.0.0.1:{port}/json/list"
        ws_url = ""
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(list_url, timeout=1) as response:
                    targets = json.loads(response.read().decode("utf-8"))
                    page_targets = [target for target in targets if target.get("type") == "page"]
                    if page_targets:
                        ws_url = page_targets[0]["webSocketDebuggerUrl"]
                        break
            except Exception:
                await asyncio.sleep(0.1)
        if not ws_url:
            raise RuntimeError("Chrome DevTools endpoint did not start")
        async with websockets.connect(ws_url, max_size=20_000_000) as ws:
            seq = 1
            await _cdp_call(ws, seq, "Page.enable")
            seq += 1
            await _cdp_call(ws, seq, "Runtime.enable")
            seq += 1
            await asyncio.sleep(wait_ms / 1000)
            expression = """
(() => {
  const imgs = Array.from(document.images);
  return {
    title: document.title,
    imageCount: imgs.length,
    lazyPending: imgs
      .filter(img => img.loading === 'lazy' && !img.complete && (img.currentSrc || img.src))
      .length,
    broken: imgs
      .filter(img => img.complete && (img.naturalWidth === 0 || img.naturalHeight === 0))
      .map(img => ({
        src: img.currentSrc || img.src || '',
        alt: img.alt || '',
        className: img.className || '',
        width: img.naturalWidth || 0,
        height: img.naturalHeight || 0
      }))
  };
})()
"""
            result = await _cdp_call(ws, seq, "Runtime.evaluate", {"expression": expression, "returnByValue": True})
            value = ((result or {}).get("result") or {}).get("value") or {}
            return {"url": url, **value}
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Fanxiu wiki icon resources and browser image rendering.")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--frontend-base", default=DEFAULT_FRONTEND_BASE)
    parser.add_argument("--export-root", default="")
    parser.add_argument("--api-limit", type=int, default=200)
    parser.add_argument("--api-timeout", type=int, default=30)
    parser.add_argument("--max-icons", type=int, default=40, help="Maximum unique icons to verify through the HTTP icon endpoint; use 0 for all.")
    parser.add_argument("--full-items", action="store_true", help="Validate every item_catalog icon, not only current API pages.")
    parser.add_argument("--browser", action="store_true", help="Open wiki pages in headless Chrome and report broken images.")
    parser.add_argument("--chrome", default=DEFAULT_CHROME)
    parser.add_argument("--browser-wait-ms", type=int, default=8000)
    parser.add_argument(
        "--primary-reuse-risk-threshold",
        type=int,
        default=50,
        help="Report primary item icons reused at least this many times as quality risks; quality frames are ignored.",
    )
    args = parser.parse_args()

    export_root = resolve_fanxiu_export_root(args.export_root or None)
    output_dir = export_root / "parsed_configs" / "wiki_icon_audit"

    uses = _collect_local_item_icons(export_root, None if args.full_items else args.api_limit)
    uses.extend(_collect_api_page_icons(args.api_base, args.api_limit, args.api_timeout))
    if args.full_items:
        print("full item_catalog icon scan enabled", flush=True)

    icon_to_uses: dict[str, list[IconUse]] = {}
    endpoint_errors: list[IconUse] = []
    for use in uses:
        if use.field == "endpoint":
            endpoint_errors.append(use)
            continue
        icon_to_uses.setdefault(use.icon, []).append(use)

    icons = sorted(icon_to_uses)
    usage_rows = []
    for icon, icon_uses in sorted(icon_to_uses.items(), key=lambda item: (-len(item[1]), item[0])):
        samples = []
        sources = set()
        fields = set()
        for use in icon_uses:
            sources.add(use.source)
            fields.add(use.field)
            label = f"{use.item_id}:{use.item_name}".strip(":")
            if label and label not in samples:
                samples.append(label)
            if len(samples) >= 8:
                break
        usage_rows.append(
            {
                "icon": icon,
                "use_count": len(icon_uses),
                "sources": ",".join(sorted(sources)),
                "fields": ",".join(sorted(fields)),
                "samples": " | ".join(samples),
            }
        )
    _write_tsv(
        output_dir / "icon_usage_summary_latest.tsv",
        usage_rows,
        ["icon", "use_count", "sources", "fields", "samples"],
    )
    primary_reuse_risk_threshold = max(1, int(getattr(args, "primary_reuse_risk_threshold", 50) or 50))
    quality_risk_rows = _build_icon_quality_risk_rows(
        icon_to_uses,
        primary_reuse_threshold=primary_reuse_risk_threshold,
    )
    quality_risk_counts_by_type = _count_icon_quality_risks_by_type(quality_risk_rows)
    quality_risk_counts_by_scope = _count_icon_quality_risks_by_scope(quality_risk_rows)
    _write_tsv(
        output_dir / "icon_quality_risks_latest.tsv",
        quality_risk_rows,
        [
            "icon",
            "primary_use_count",
            "item_catalog_use_count",
            "total_use_count",
            "risk",
            "source_scope",
            "sources",
            "fields",
            "samples",
        ],
    )
    if args.max_icons > 0:
        icons = icons[: args.max_icons]
    print(f"checking {len(icons)} / {len(icon_to_uses)} unique icons through HTTP icon endpoint", flush=True)
    checks = []
    for index, icon in enumerate(icons, start=1):
        if index == 1 or index % 10 == 0 or index == len(icons):
            print(f"icon {index}/{len(icons)}: {icon}", flush=True)
        checks.append(_check_icon_endpoint(args.api_base, icon))
    failed_checks = [check for check in checks if not check["ok"]]
    if failed_checks:
        print(f"retrying {len(failed_checks)} failed icon endpoint checks after cooldown", flush=True)
        time.sleep(5)
        retry_by_icon = {check["icon"]: _check_icon_endpoint(args.api_base, check["icon"]) for check in failed_checks}
        checks = [retry_by_icon.get(check["icon"], check) if not check["ok"] else check for check in checks]
    failures = []
    for check in checks:
        if check["ok"]:
            continue
        for use in icon_to_uses.get(check["icon"], [])[:20]:
            failures.append(
                {
                    **check,
                    "source": use.source,
                    "item_id": use.item_id,
                    "item_name": use.item_name,
                    "field": use.field,
                }
            )

    endpoint_rows = [
        {"source": use.source, "detail": use.item_name}
        for use in endpoint_errors
    ]
    _write_tsv(
        output_dir / "icon_endpoint_failures_latest.tsv",
        failures,
        ["icon", "ok", "status_code", "content_type", "size", "detail", "source", "item_id", "item_name", "field"],
    )
    _write_tsv(output_dir / "api_endpoint_errors_latest.tsv", endpoint_rows, ["source", "detail"])

    browser_rows: list[dict[str, Any]] = []
    browser_hard_failure_rows: list[dict[str, Any]] = []
    browser_lazy_pending_count = 0
    if args.browser:
        pages = [
            f"{args.frontend_base.rstrip('/')}/fanxiu/wiki?tab=item&id=30060000",
            f"{args.frontend_base.rstrip('/')}/fanxiu/wiki?tab=activity",
            f"{args.frontend_base.rstrip('/')}/fanxiu/wiki?tab=gongfa",
        ]
        for url in pages:
            scan = asyncio.run(_scan_page_broken_images(args.chrome, url, args.browser_wait_ms))
            browser_lazy_pending_count += int(scan.get("lazyPending") or 0)
            for broken in scan.get("broken") or []:
                row = {
                    "url": scan.get("url", url),
                    "image_count": scan.get("imageCount", 0),
                    "src": broken.get("src", ""),
                    "alt": broken.get("alt", ""),
                    "class_name": broken.get("className", ""),
                }
                browser_rows.append(row)
        checked_srcs: dict[str, dict[str, Any]] = {}
        for row in browser_rows:
            src = row["src"]
            if src not in checked_srcs:
                checked_srcs[src] = _check_image_url(src)
            check = checked_srcs[src]
            if not check["ok"]:
                browser_hard_failure_rows.append({**row, **check})
        _write_tsv(output_dir / "browser_broken_images_latest.tsv", browser_rows, ["url", "image_count", "src", "alt", "class_name"])
        _write_tsv(
            output_dir / "browser_broken_image_endpoint_failures_latest.tsv",
            browser_hard_failure_rows,
            ["url", "image_count", "src", "alt", "class_name", "ok", "status_code", "content_type", "size", "detail"],
        )

    summary = {
        "scan_full_items": bool(args.full_items),
        "api_limit": args.api_limit,
        "checked_icon_limit": args.max_icons,
        "unique_icons": len(icon_to_uses),
        "icon_uses": sum(len(v) for v in icon_to_uses.values()),
        "max_icon_reuse_count": max((len(v) for v in icon_to_uses.values()), default=0),
        "icon_usage_summary_path": str(output_dir / "icon_usage_summary_latest.tsv"),
        "primary_icon_reuse_risk_threshold": primary_reuse_risk_threshold,
        "primary_icon_reuse_risk_count": len(quality_risk_rows),
        "icon_quality_risk_counts_by_type": quality_risk_counts_by_type,
        "icon_quality_risk_counts_by_scope": quality_risk_counts_by_scope,
        "icon_quality_risks_path": str(output_dir / "icon_quality_risks_latest.tsv"),
        "icon_endpoint_failures": len({row["icon"] for row in failures}),
        "api_endpoint_errors": len(endpoint_rows),
        "browser_broken_images_observed": len(browser_rows),
        "browser_broken_image_endpoint_failures": len(browser_hard_failure_rows),
        "browser_lazy_pending_images_observed": browser_lazy_pending_count,
        "output_dir": str(output_dir),
    }
    (output_dir / "summary_latest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if failures or endpoint_rows or browser_hard_failure_rows else 0


if __name__ == "__main__":
    raise SystemExit(main())
