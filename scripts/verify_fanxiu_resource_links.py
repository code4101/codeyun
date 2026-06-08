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
from pathlib import Path
from typing import Any

import requests
import websockets
from sqlmodel import Session, select

import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.core.fanxiu_resources import resolve_fanxiu_export_root
from backend.core.fanxiu_mail_policy import fanxiu_mail_rewards_from_payload
from backend.core.fanxiu_mail_store import ensure_fanxiu_mail_table
from backend.db import engine
from backend.models import FanxiuMailRecord


DEFAULT_API_BASE = "http://127.0.0.1:8000/api"
DEFAULT_FRONTEND_BASE = "http://127.0.0.1:5173"
DEFAULT_CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
DEFAULT_REQUIRED_RESOURCES = ("item:3080008",)


RESOURCE_SPECS = (
    {
        "resource_type": "gongfa",
        "list_path": "/fanxiu/resources/gongfa/cards",
        "id_field": "id",
        "title_field": "name",
    },
    {
        "resource_type": "item",
        "list_path": "/fanxiu/resources/items/cards",
        "id_field": "id",
        "title_field": "name",
    },
    {
        "resource_type": "activity",
        "list_path": "/fanxiu/resources/activities/cards",
        "id_field": "id",
        "title_field": "name",
        "require_icon": False,
    },
    {
        "resource_type": "digitdoor",
        "list_path": "/fanxiu/resources/digitdoor/character-cards",
        "id_field": "id",
        "title_field": "name",
        "require_icon": True,
    },
    {
        "resource_type": "doupotd",
        "list_path": "/fanxiu/resources/doupotd/partner-cards",
        "id_field": "id",
        "title_field": "name",
        "require_icon": True,
    },
    {
        "resource_type": "lingjie",
        "list_path": "/fanxiu/resources/gongfa/lingjie-feature-cards",
        "id_field": "gongfa_id",
        "title_field": "name",
        "require_icon": True,
    },
)


def _get_json(url: str, *, params: dict[str, Any] | None = None, timeout: int = 30, attempts: int = 3) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            response = requests.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except (requests.ConnectionError, requests.Timeout) as error:
            last_error = error
            if attempt >= attempts:
                break
            time.sleep(0.4 * attempt)
    if last_error:
        raise last_error
    raise RuntimeError(f"GET did not return JSON: {url}")


async def _cdp_call(ws: Any, seq: int, method: str, params: dict[str, Any] | None = None) -> Any:
    await ws.send(json.dumps({"id": seq, "method": method, "params": params or {}}))
    while True:
        message = json.loads(await ws.recv())
        if message.get("id") == seq:
            if "error" in message:
                raise RuntimeError(message["error"])
            return message.get("result") or {}


async def _evaluate_json(ws: Any, seq_ref: list[int], expression: str) -> Any:
    seq = seq_ref[0]
    seq_ref[0] += 1
    result = await _cdp_call(ws, seq, "Runtime.evaluate", {"expression": expression, "returnByValue": True})
    return ((result or {}).get("result") or {}).get("value")


def _write_tsv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _back_link_matches(back_href: str, resource_type: str, resource_id: str) -> bool:
    parsed = urllib.parse.urlparse(str(back_href or ""))
    query = urllib.parse.parse_qs(parsed.query)
    tab = (query.get("tab") or [""])[0]
    item_id = (query.get("id") or [""])[0]
    return tab == resource_type and item_id == resource_id


def _resource_icon_matches(image_src: str, expected_icon: str) -> bool:
    icon = str(expected_icon or "").strip()
    if not icon:
        return True
    parsed = urllib.parse.urlparse(str(image_src or ""))
    query = urllib.parse.parse_qs(parsed.query)
    names = [urllib.parse.unquote(str(value)) for value in query.get("name", [])]
    if icon in names:
        return True
    return icon in urllib.parse.unquote(str(image_src or ""))


def _fetch_sample_resources(api_base: str, timeout: int, samples_per_type: int) -> list[dict[str, str]]:
    samples: list[dict[str, str]] = []
    base = api_base.rstrip("/")
    for spec in RESOURCE_SPECS:
        data = _get_json(
            f"{base}{spec['list_path']}",
            params={"limit": max(1, samples_per_type), "offset": 0},
            timeout=timeout,
        )
        rows = data.get("items") or data.get("cards") or data.get("rows") or []
        resource_rows = [row for row in rows if isinstance(row, dict)]
        if not resource_rows:
            raise RuntimeError(f"sample resource missing for {spec['resource_type']}")
        for first in resource_rows[: max(1, samples_per_type)]:
            resource_id = str(first.get(spec["id_field"]) or "").strip()
            title = str(first.get(spec["title_field"]) or "").strip()
            icon = str(first.get("icon") or first.get("small_icon") or first.get("quality_icon") or first.get("head_icon") or "").strip()
            if not resource_id:
                raise RuntimeError(f"sample resource id missing for {spec['resource_type']}")
            samples.append(
                {
                    "resource_type": spec["resource_type"],
                    "resource_id": resource_id,
                    "expected_title": title,
                    "expected_icon": icon,
                    "require_icon": bool(spec.get("require_icon", True)),
                    "required_probe": False,
                    "mail_reward_probe": False,
                    "icon_review_probe": False,
                }
            )
    return samples


def _parse_required_resource(value: str) -> tuple[str, str]:
    normalized = str(value or "").strip()
    if ":" not in normalized:
        raise ValueError(f"required resource must use <type>:<id>: {value}")
    resource_type, resource_id = [part.strip() for part in normalized.split(":", 1)]
    if resource_type not in {str(spec["resource_type"]) for spec in RESOURCE_SPECS}:
        raise ValueError(f"unsupported required resource type: {resource_type}")
    if not resource_id:
        raise ValueError(f"required resource id missing: {value}")
    return resource_type, resource_id


def _fetch_required_resource_sample(api_base: str, timeout: int, resource_type: str, resource_id: str) -> dict[str, Any]:
    base = api_base.rstrip("/")
    if resource_type == "item":
        card = _get_json(f"{base}/fanxiu/resources/items/card", params={"item_id": resource_id}, timeout=timeout).get("card") or {}
        return {
            "resource_type": resource_type,
            "resource_id": resource_id,
            "expected_title": str(card.get("name") or "").strip(),
            "expected_icon": str(card.get("icon") or card.get("small_icon") or "").strip(),
            "require_icon": True,
            "required_probe": True,
            "mail_reward_probe": False,
            "icon_review_probe": False,
        }
    raise ValueError(f"required resource type does not have a direct card probe yet: {resource_type}")


def _fetch_required_resource_samples(api_base: str, timeout: int, required_resources: list[str]) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for value in required_resources:
        resource_type, resource_id = _parse_required_resource(value)
        key = (resource_type, resource_id)
        if key in seen:
            continue
        seen.add(key)
        samples.append(_fetch_required_resource_sample(api_base, timeout, resource_type, resource_id))
    return samples


def _fetch_mail_reward_resource_samples(api_base: str, timeout: int, sample_count: int) -> list[dict[str, Any]]:
    if sample_count <= 0:
        return []
    ensure_fanxiu_mail_table()
    samples: list[dict[str, Any]] = []
    seen: set[str] = set()
    with Session(engine) as session:
        rows = session.exec(
            select(FanxiuMailRecord)
            .where(FanxiuMailRecord.source == "packet")
            .order_by(FanxiuMailRecord.updated_at.desc(), FanxiuMailRecord.id.desc())
            .limit(500)
        ).all()
    for row in rows:
        for reward in fanxiu_mail_rewards_from_payload(row.payload):
            item_id = str(reward.get("item_id") or "").strip()
            if not item_id or item_id in seen:
                continue
            seen.add(item_id)
            sample = _fetch_required_resource_sample(api_base, timeout, "item", item_id)
            sample["required_probe"] = True
            sample["mail_reward_probe"] = True
            sample["icon_review_probe"] = False
            samples.append(sample)
            if len(samples) >= sample_count:
                return samples
    return samples


def _select_icon_review_sample_refs(review_items: list[Any], sample_count: int) -> list[dict[str, str]]:
    if sample_count <= 0:
        return []
    per_group_samples: list[list[dict[str, str]]] = []
    for row in review_items:
        if not isinstance(row, dict):
            continue
        group_key = f"{row.get('field') or ''}:{row.get('icon') or ''}".strip(":")
        group_samples: list[dict[str, str]] = []
        for sample in row.get("representative_samples") or []:
            if not isinstance(sample, dict):
                continue
            item_id = str(sample.get("id") or "").strip()
            if not item_id:
                continue
            group_samples.append(
                {
                    "item_id": item_id,
                    "icon_review_group": group_key,
                    "icon_review_icon": str(row.get("icon") or "").strip(),
                    "icon_review_field": str(row.get("field") or "").strip(),
                }
            )
        if group_samples:
            per_group_samples.append(group_samples)

    selected: list[dict[str, str]] = []
    seen: set[str] = set()

    def add_candidate(candidate: dict[str, str]) -> bool:
        item_id = candidate["item_id"]
        if item_id in seen:
            return False
        seen.add(item_id)
        selected.append(candidate)
        return len(selected) >= sample_count

    for group_samples in per_group_samples:
        if add_candidate(group_samples[0]):
            return selected
    max_group_len = max((len(group) for group in per_group_samples), default=0)
    for sample_index in range(1, max_group_len):
        for group_samples in per_group_samples:
            if sample_index < len(group_samples) and add_candidate(group_samples[sample_index]):
                return selected
    return selected


def _fetch_icon_review_resource_samples(api_base: str, timeout: int, sample_count: int) -> list[dict[str, Any]]:
    if sample_count <= 0:
        return []
    base = api_base.rstrip("/")
    data = _get_json(
        f"{base}/fanxiu/resources/items/icon-quality-review",
        params={"threshold": 50},
        timeout=timeout,
    )
    samples: list[dict[str, Any]] = []
    for ref in _select_icon_review_sample_refs(data.get("items") or [], sample_count):
        resolved = _fetch_required_resource_sample(api_base, timeout, "item", ref["item_id"])
        resolved["required_probe"] = True
        resolved["mail_reward_probe"] = False
        resolved["icon_review_probe"] = True
        resolved["icon_review_group"] = ref["icon_review_group"]
        resolved["icon_review_icon"] = ref["icon_review_icon"]
        resolved["icon_review_field"] = ref["icon_review_field"]
        samples.append(resolved)
    return samples


def _merge_resource_samples(sample_resources: list[dict[str, Any]], required_resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for sample in [*sample_resources, *required_resources]:
        key = (str(sample.get("resource_type") or ""), str(sample.get("resource_id") or ""))
        if not key[0] or not key[1]:
            continue
        existing = by_key.get(key)
        if existing:
            existing["required_probe"] = bool(existing.get("required_probe")) or bool(sample.get("required_probe"))
            existing["mail_reward_probe"] = bool(existing.get("mail_reward_probe")) or bool(sample.get("mail_reward_probe"))
            existing["icon_review_probe"] = bool(existing.get("icon_review_probe")) or bool(sample.get("icon_review_probe"))
            for field in ["icon_review_group", "icon_review_icon", "icon_review_field"]:
                if sample.get(field) and not existing.get(field):
                    existing[field] = sample[field]
            if sample.get("expected_title"):
                existing["expected_title"] = sample["expected_title"]
            if sample.get("expected_icon"):
                existing["expected_icon"] = sample["expected_icon"]
            continue
        copied = dict(sample)
        by_key[key] = copied
        merged.append(copied)
    return merged


async def _open_chrome(chrome_path: str, frontend_base: str, width: int, height: int) -> tuple[subprocess.Popen[bytes], str]:
    port = 9800 + int(time.time() * 1000) % 200
    profile = Path(tempfile.gettempdir()) / "codeyun" / f"chrome-fanxiu-resource-link-audit-{port}"
    profile.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [
            chrome_path,
            "--headless=new",
            "--disable-gpu",
            "--disable-extensions",
            "--no-first-run",
            "--no-default-browser-check",
            f"--window-size={width},{height}",
            f"--user-data-dir={profile}",
            f"--remote-debugging-port={port}",
            frontend_base.rstrip("/") + "/",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    list_url = f"http://127.0.0.1:{port}/json/list"
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(list_url, timeout=1) as response:
                targets = json.loads(response.read().decode("utf-8"))
                pages = [target for target in targets if target.get("type") == "page"]
                if pages:
                    return process, pages[0]["webSocketDebuggerUrl"]
        except Exception:
            await asyncio.sleep(0.1)
    process.terminate()
    raise RuntimeError("Chrome DevTools endpoint did not start")


async def _wait_resource_page(ws: Any, seq_ref: list[int], wait_ms: int) -> dict[str, Any]:
    deadline = time.time() + max(1, wait_ms / 1000)
    state: dict[str, Any] = {}
    while time.time() < deadline:
        state = await _evaluate_json(
            ws,
            seq_ref,
            """
(() => {
  const loading = Boolean(document.querySelector('.el-loading-mask:not([style*="display: none"])'));
  const panel = document.querySelector('.resource-panel');
  const empty = document.querySelector('.resource-empty');
  const title = (document.querySelector('.detail-title h3')?.innerText || '').trim();
  const meta = (document.querySelector('.detail-meta')?.innerText || '').trim().replace(/\\s+/g, ' ');
  const backHref = document.querySelector('.resource-back-link')?.getAttribute('href') || '';
  const img = document.querySelector('.object-icon img');
  const sections = Array.from(document.querySelectorAll('.object-section h4')).map(el => (el.innerText || '').trim()).join('|');
  const bodyText = document.body ? document.body.innerText : '';
  return {
    loading,
    has_panel: Boolean(panel),
    has_empty: Boolean(empty),
    title,
    meta,
    back_href: backHref,
    sections,
    image_src: img ? (img.currentSrc || img.src || '') : '',
    image_complete: img ? Boolean(img.complete) : false,
    image_width: img ? (img.naturalWidth || 0) : 0,
    image_height: img ? (img.naturalHeight || 0) : 0,
    body_text: bodyText.slice(0, 1000),
    path: location.pathname,
  };
})()
""",
        ) or {}
        image_pending = bool(state.get("image_src")) and (
            not state.get("image_complete") or not state.get("image_width") or not state.get("image_height")
        )
        if not state.get("loading") and (state.get("has_panel") or state.get("has_empty")) and not image_pending:
            return state
        await asyncio.sleep(0.25)
    return state


def _resource_row_failures(row: dict[str, Any], resource_type: str, resource_id: str) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    if not row["has_panel"] or row["has_empty"]:
        failures.append({**row, "kind": "resource_panel_missing"})
    if not str(row["title"] or "").strip():
        failures.append({**row, "kind": "resource_title_missing"})
    if row["expected_title"] and row["expected_title"] not in str(row["title"] or ""):
        failures.append({**row, "kind": "resource_title_mismatch"})
    if resource_id not in str(row["meta"] or ""):
        failures.append({**row, "kind": "resource_id_not_rendered"})
    if not _back_link_matches(str(row["back_href"] or ""), resource_type, resource_id):
        failures.append({**row, "kind": "back_link_mismatch"})
    if row["require_icon"] and (not row["image_src"] or not row["image_complete"] or not row["image_width"] or not row["image_height"]):
        failures.append({**row, "kind": "resource_icon_not_loaded"})
    if row["require_icon"] and not _resource_icon_matches(str(row["image_src"] or ""), str(row["expected_icon"] or "")):
        failures.append({**row, "kind": "resource_icon_mismatch"})
    return failures


async def _audit_resource_sample(ws: Any, seq_ref: list[int], sample: dict[str, Any], frontend_base: str, wait_ms: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    resource_type = sample["resource_type"]
    resource_id = sample["resource_id"]
    path = f"/fanxiu-resource/{urllib.parse.quote(resource_type)}/{urllib.parse.quote(resource_id)}"
    url = frontend_base.rstrip("/") + path
    last_row: dict[str, Any] = {}
    last_failures: list[dict[str, Any]] = []
    for attempt in range(1, 3):
        seq = seq_ref[0]
        seq_ref[0] += 1
        await _cdp_call(ws, seq, "Page.navigate", {"url": url})
        state = await _wait_resource_page(ws, seq_ref, wait_ms)
        row = {
            **sample,
            "url": url,
            "path": state.get("path", ""),
            "title": state.get("title", ""),
            "meta": state.get("meta", ""),
            "sections": state.get("sections", ""),
            "back_href": state.get("back_href", ""),
            "image_src": state.get("image_src", ""),
            "image_complete": state.get("image_complete", False),
            "image_width": state.get("image_width", 0),
            "image_height": state.get("image_height", 0),
            "has_panel": state.get("has_panel", False),
            "has_empty": state.get("has_empty", False),
            "require_icon": sample.get("require_icon", True),
            "expected_icon": sample.get("expected_icon", ""),
            "body_text": state.get("body_text", ""),
            "attempt": attempt,
        }
        row_failures = _resource_row_failures(row, resource_type, resource_id)
        last_row = row
        last_failures = row_failures
        if not row_failures:
            return row, []
        await asyncio.sleep(0.4)
    return last_row, last_failures


async def run_resource_link_audit(args: argparse.Namespace) -> dict[str, Any]:
    export_root = resolve_fanxiu_export_root(args.export_root or None)
    output_dir = export_root / "parsed_configs" / "wiki_resource_link_audit"
    output_dir.mkdir(parents=True, exist_ok=True)
    sample_resources = _fetch_sample_resources(args.api_base, args.api_timeout, args.samples_per_type)
    required_resources = _fetch_required_resource_samples(args.api_base, args.api_timeout, args.required_resource)
    mail_reward_resources = _fetch_mail_reward_resource_samples(args.api_base, args.api_timeout, args.mail_reward_resource_samples)
    icon_review_resources = _fetch_icon_review_resource_samples(args.api_base, args.api_timeout, args.icon_review_resource_samples)
    samples = _merge_resource_samples(sample_resources, [*required_resources, *mail_reward_resources, *icon_review_resources])
    process, ws_url = await _open_chrome(args.chrome, args.frontend_base, args.width, args.height)
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    try:
        async with websockets.connect(ws_url, max_size=30_000_000) as ws:
            seq_ref = [1]
            await _cdp_call(ws, seq_ref[0], "Page.enable")
            seq_ref[0] += 1
            await _cdp_call(ws, seq_ref[0], "Runtime.enable")
            seq_ref[0] += 1
            for sample in samples:
                row, row_failures = await _audit_resource_sample(ws, seq_ref, sample, args.frontend_base, args.wait_ms)
                rows.append(row)
                failures.extend(row_failures)
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()

    _write_tsv(
        output_dir / "resource_links_latest.tsv",
        rows,
        [
            "resource_type",
            "resource_id",
            "expected_title",
            "url",
            "path",
            "title",
            "meta",
            "sections",
            "back_href",
            "image_src",
            "image_complete",
            "image_width",
            "image_height",
            "expected_icon",
            "require_icon",
            "required_probe",
            "mail_reward_probe",
            "icon_review_probe",
            "icon_review_group",
            "icon_review_icon",
            "icon_review_field",
            "has_panel",
            "has_empty",
            "attempt",
            "body_text",
        ],
    )
    _write_tsv(
        output_dir / "resource_link_failures_latest.tsv",
        failures,
        [
            "kind",
            "resource_type",
            "resource_id",
            "expected_title",
            "url",
            "path",
            "title",
            "meta",
            "back_href",
            "image_src",
            "image_complete",
            "image_width",
            "image_height",
            "expected_icon",
            "require_icon",
            "required_probe",
            "mail_reward_probe",
            "icon_review_probe",
            "icon_review_group",
            "icon_review_icon",
            "icon_review_field",
            "body_text",
        ],
    )
    required_rows = [row for row in rows if row.get("required_probe")]
    required_failures = [row for row in failures if row.get("required_probe")]
    mail_reward_rows = [row for row in rows if row.get("mail_reward_probe")]
    mail_reward_failures = [row for row in failures if row.get("mail_reward_probe")]
    icon_review_rows = [row for row in rows if row.get("icon_review_probe")]
    icon_review_failures = [row for row in failures if row.get("icon_review_probe")]
    summary = {
        "resource_count": len(rows),
        "failure_count": len(failures),
        "required_resource_count": len(required_rows),
        "required_resource_failure_count": len(required_failures),
        "mail_reward_resource_count": len(mail_reward_rows),
        "mail_reward_resource_failure_count": len(mail_reward_failures),
        "icon_review_resource_count": len(icon_review_rows),
        "icon_review_resource_failure_count": len(icon_review_failures),
        "loaded_icon_count": sum(1 for row in rows if row["image_src"] and row["image_complete"] and row["image_width"] and row["image_height"]),
        "required_icon_count": sum(1 for row in rows if row["require_icon"]),
        "title_mismatch_count": sum(1 for row in failures if row.get("kind") == "resource_title_mismatch"),
        "icon_mismatch_count": sum(1 for row in failures if row.get("kind") == "resource_icon_mismatch"),
        "supported_resource_types": [spec["resource_type"] for spec in RESOURCE_SPECS],
        "output_dir": str(output_dir),
    }
    (output_dir / "summary_latest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Fanxiu standalone resource detail links in a real browser.")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--frontend-base", default=DEFAULT_FRONTEND_BASE)
    parser.add_argument("--export-root", default="")
    parser.add_argument("--chrome", default=DEFAULT_CHROME)
    parser.add_argument("--api-timeout", type=int, default=30)
    parser.add_argument("--wait-ms", type=int, default=12000)
    parser.add_argument("--samples-per-type", type=int, default=5)
    parser.add_argument("--required-resource", action="append", default=list(DEFAULT_REQUIRED_RESOURCES), help="Always verify this resource detail page, as <type>:<id>.")
    parser.add_argument("--mail-reward-resource-samples", type=int, default=8, help="Also verify this many unique item resources from packet-derived mail attachments.")
    parser.add_argument("--icon-review-resource-samples", type=int, default=8, help="Also verify this many representative item resources from icon-quality review candidates.")
    parser.add_argument("--width", type=int, default=1120)
    parser.add_argument("--height", type=int, default=820)
    args = parser.parse_args()

    summary = asyncio.run(run_resource_link_audit(args))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["failure_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
