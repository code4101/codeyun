from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


DEFAULT_ITEM_TYPE_LABELS = ["云梦论剑", "时装", "礼包宝匣", "材料", "功法"]
TRANSIENT_SERVICE_FAILURE_PATTERNS = (
    "Failed to establish a new connection",
    "由于目标计算机积极拒绝",
    "Connection refused",
    "Max retries exceeded",
    "NewConnectionError",
    "ReadTimeout",
    "Read timed out",
    "read timeout",
)


def _python_command(*args: str) -> list[str]:
    return [sys.executable, *args]


def _npm_command(*args: str) -> list[str]:
    npm = shutil.which("npm.cmd") or shutil.which("npm") or "npm"
    return [npm, *args]


def _run_command_step(name: str, command: list[str], *, timeout: int) -> dict[str, Any]:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        started = subprocess.run(
            command,
            cwd=REPO_ROOT,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
        return {
            "name": name,
            "returncode": started.returncode,
            "command": " ".join(command),
            "output": started.stdout,
            "timeout_seconds": timeout,
            "timed_out": False,
            "error": "",
        }
    except subprocess.TimeoutExpired as exc:
        output = exc.output or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        return {
            "name": name,
            "returncode": 124,
            "command": " ".join(command),
            "output": f"{output}\nstep timed out after {timeout}s",
            "timeout_seconds": timeout,
            "timed_out": True,
            "error": f"timeout after {timeout}s",
        }
    except Exception as exc:
        return {
            "name": name,
            "returncode": 1,
            "command": " ".join(command),
            "output": str(exc),
            "timeout_seconds": timeout,
            "timed_out": False,
            "error": str(exc),
        }


def _run_step(name: str, args: list[str], *, timeout: int) -> dict[str, Any]:
    return _run_command_step(name, _python_command(*args), timeout=timeout)


def _looks_like_transient_service_failure(row: dict[str, Any]) -> bool:
    if row.get("returncode") == 0:
        return False
    output = str(row.get("output") or "")
    error = str(row.get("error") or "")
    haystack = f"{output}\n{error}"
    return any(pattern in haystack for pattern in TRANSIENT_SERVICE_FAILURE_PATTERNS)


def _run_quality_step(name: str, command: list[str], *, timeout: int, transient_retries: int = 1) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    for attempt in range(max(1, transient_retries + 1)):
        row = _run_command_step(name, command, timeout=timeout)
        attempts.append(row)
        if not _looks_like_transient_service_failure(row) or attempt >= transient_retries:
            break
        time.sleep(min(8.0, 1.5 * (attempt + 1)))

    if len(attempts) == 1:
        return attempts[0]

    final = dict(attempts[-1])
    final["retry_count"] = len(attempts) - 1
    final["transient_retry_outputs"] = [
        {
            "returncode": row.get("returncode"),
            "timed_out": row.get("timed_out", False),
            "output_tail": _tail_output(str(row.get("output") or ""), 1600),
            "error": row.get("error", ""),
        }
        for row in attempts[:-1]
    ]
    final["output"] = "\n".join(
        [
            *(f"[transient retry {index}] {_tail_output(str(row.get('output') or ''), 1600)}" for index, row in enumerate(attempts[:-1], start=1)),
            str(final.get("output") or ""),
        ]
    )
    return final


def _tail_output(output: str, limit: int = 3000) -> str:
    if len(output) <= limit:
        return output
    return output[-limit:]


def _extract_last_json_object(output: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    text = output or ""
    parsed: dict[str, Any] | None = None
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and not text[index + end:].strip():
            parsed = value
    return parsed


def _step_by_name(results: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    return next((row for row in results if row.get("name") == name), None)


def _quality_consistency_failures(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mail_records_initial = _step_by_name(results, "mail_records")
    mail_records_final = _step_by_name(results, "mail_records_final")
    mail_records = mail_records_final or mail_records_initial
    mail_browser = _step_by_name(results, "mail_browser")
    if not mail_records or not mail_browser:
        return []
    if mail_records.get("returncode") != 0 or mail_browser.get("returncode") != 0:
        return []

    initial_data = _extract_last_json_object(str(mail_records_initial.get("output") or "")) if mail_records_initial else None
    final_data = _extract_last_json_object(str(mail_records_final.get("output") or "")) if mail_records_final else None
    mail_data = final_data or initial_data
    browser_data = _extract_last_json_object(str(mail_browser.get("output") or ""))
    failures: list[dict[str, Any]] = []
    if not mail_data:
        failures.append({"kind": "mail_records_json_missing", "detail": "mail_records output must end with JSON summary"})
        return failures
    if not browser_data:
        failures.append({"kind": "mail_browser_json_missing", "detail": "mail_browser output must end with JSON summary"})
        return failures

    checks: list[tuple[str, Any, Any, Any]] = [
        (
            "mail_row_count",
            initial_data.get("records") if initial_data else None,
            final_data.get("records") if final_data else None,
            browser_data.get("mail_row_count"),
        ),
        (
            "mail_reward_image_count",
            initial_data.get("reward_items") if initial_data else None,
            final_data.get("reward_items") if final_data else None,
            browser_data.get("mail_reward_image_count"),
        ),
        (
            "mail_item_link_count",
            initial_data.get("reward_items") if initial_data else None,
            final_data.get("reward_items") if final_data else None,
            browser_data.get("mail_item_link_count"),
        ),
        (
            "mail_content_button_count",
            initial_data.get("records") if initial_data else None,
            final_data.get("records") if final_data else None,
            browser_data.get("mail_content_button_count"),
        ),
    ]
    for field, initial_expected, final_expected, observed in checks:
        expected_values = [value for value in [initial_expected, final_expected] if isinstance(value, (int, float))]
        if len(expected_values) >= 2:
            expected_min = min(expected_values)
            expected_max = max(expected_values)
            if not isinstance(observed, (int, float)) or observed < expected_min or observed > expected_max:
                failures.append(
                    {
                        "kind": f"{field}_outside_snapshot_range",
                        "detail": "mail browser audit count must stay within the before/after database snapshot range",
                        "expected_min": expected_min,
                        "expected_max": expected_max,
                        "observed": observed,
                        "initial": initial_expected,
                        "final": final_expected,
                    }
                )
            continue

        expected = expected_values[0] if expected_values else None
        if expected != observed:
            failures.append(
                {
                    "kind": f"{field}_mismatch",
                    "detail": "mail database counts must match the rendered browser audit counts",
                    "expected": expected,
                    "observed": observed,
                }
            )
    return failures


def _json_summary_for_step(results: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    row = _step_by_name(results, name)
    if not row or row.get("returncode") != 0:
        return None
    return _extract_last_json_object(str(row.get("output") or ""))


def _append_failure(failures: list[dict[str, Any]], step: str, kind: str, field: str, expected: Any, observed: Any) -> None:
    failures.append(
        {
            "step": step,
            "kind": kind,
            "field": field,
            "expected": expected,
            "observed": observed,
        }
    )


def _require_positive(failures: list[dict[str, Any]], step: str, data: dict[str, Any], field: str) -> None:
    observed = data.get(field)
    if not isinstance(observed, (int, float)) or observed <= 0:
        _append_failure(failures, step, "must_be_positive", field, "> 0", observed)


def _require_zero(failures: list[dict[str, Any]], step: str, data: dict[str, Any], field: str) -> None:
    observed = data.get(field)
    if observed != 0:
        _append_failure(failures, step, "must_be_zero", field, 0, observed)


def _require_empty(failures: list[dict[str, Any]], step: str, data: dict[str, Any], field: str) -> None:
    observed = data.get(field)
    if observed != []:
        _append_failure(failures, step, "must_be_empty_list", field, [], observed)


def _require_true(failures: list[dict[str, Any]], step: str, data: dict[str, Any], field: str) -> None:
    observed = data.get(field)
    if observed is not True:
        _append_failure(failures, step, "must_be_true", field, True, observed)


def _require_equal_field(
    failures: list[dict[str, Any]],
    step: str,
    data: dict[str, Any],
    observed_field: str,
    expected_field: str,
) -> None:
    observed = data.get(observed_field)
    expected = data.get(expected_field)
    if observed != expected:
        _append_failure(failures, step, "field_mismatch", observed_field, f"same as {expected_field} ({expected})", observed)


def _require_at_least(failures: list[dict[str, Any]], step: str, data: dict[str, Any], field: str, expected: int | float) -> None:
    observed = data.get(field)
    if not isinstance(observed, (int, float)) or observed < expected:
        _append_failure(failures, step, "must_be_at_least", field, f">= {expected}", observed)


def _require_icon_review_no_candidate_details(failures: list[dict[str, Any]], data: dict[str, Any]) -> None:
    count = data.get("no_candidate_group_count")
    if not isinstance(count, (int, float)) or count <= 0:
        return
    rows = data.get("no_candidate_groups")
    expected_count = min(int(count), 12)
    if not isinstance(rows, list) or len(rows) < expected_count:
        _append_failure(failures, "item_icon_quality_report", "must_include_no_candidate_group_details", "no_candidate_groups", f">= {expected_count}", rows)
        return
    for index, row in enumerate(rows[:expected_count]):
        if not isinstance(row, dict):
            _append_failure(failures, "item_icon_quality_report", "invalid_no_candidate_group", f"no_candidate_groups[{index}]", "object", row)
            continue
        sample = row.get("sample")
        missing_fields: list[str] = []
        if not str(row.get("icon") or "").strip():
            missing_fields.append("icon")
        if not str(row.get("review_priority") or "").strip():
            missing_fields.append("review_priority")
        if not str(row.get("review_status") or "").strip():
            missing_fields.append("review_status")
        if not str(row.get("suggested_manual_action") or "").strip():
            missing_fields.append("suggested_manual_action")
        if not str(row.get("remaining_risk") or "").strip():
            missing_fields.append("remaining_risk")
        if not isinstance(sample, dict) or not str(sample.get("id") or "").strip():
            missing_fields.append("sample.id")
        if missing_fields:
            _append_failure(
                failures,
                "item_icon_quality_report",
                "incomplete_no_candidate_group",
                f"no_candidate_groups[{index}]",
                f"fields: {', '.join(missing_fields)}",
                row,
            )


def _require_icon_review_contact_sheet(failures: list[dict[str, Any]], data: dict[str, Any]) -> None:
    count = data.get("no_candidate_group_count")
    if not isinstance(count, (int, float)) or count <= 0:
        return
    raw_path = str(data.get("no_candidate_contact_sheet_path") or "").strip()
    if not raw_path:
        _append_failure(failures, "item_icon_quality_report", "must_include_contact_sheet", "no_candidate_contact_sheet_path", "non-empty path", raw_path)
        return
    path = Path(raw_path)
    if not path.is_file():
        _append_failure(failures, "item_icon_quality_report", "contact_sheet_missing", "no_candidate_contact_sheet_path", "existing file", raw_path)
        return
    if path.stat().st_size <= 0:
        _append_failure(failures, "item_icon_quality_report", "contact_sheet_empty", "no_candidate_contact_sheet_path", "non-empty file", raw_path)
        return
    try:
        from PIL import Image

        with Image.open(path) as image:
            width, height = image.size
            image.verify()
    except Exception as exc:
        _append_failure(failures, "item_icon_quality_report", "contact_sheet_unreadable", "no_candidate_contact_sheet_path", "readable image", f"{raw_path}: {exc}")
        return
    if width <= 1 or height <= 1:
        _append_failure(failures, "item_icon_quality_report", "contact_sheet_too_small", "no_candidate_contact_sheet_path", "image larger than 1x1", {"path": raw_path, "width": width, "height": height})


def _quality_summary_failures(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []

    reverse_manifest = _json_summary_for_step(results, "reverse_manifest")
    if _step_by_name(results, "reverse_manifest") and reverse_manifest is None and _step_by_name(results, "reverse_manifest").get("returncode") == 0:
        failures.append({"step": "reverse_manifest", "kind": "json_summary_missing"})
    elif reverse_manifest:
        _require_positive(failures, "reverse_manifest", reverse_manifest, "entry_count")
        _require_positive(failures, "reverse_manifest", reverse_manifest, "raw_input_count")
        _require_positive(failures, "reverse_manifest", reverse_manifest, "hashed_file_count")
        _require_positive(failures, "reverse_manifest", reverse_manifest, "directory_summary_count")
        _require_zero(failures, "reverse_manifest", reverse_manifest, "missing_count")
        digest = str(reverse_manifest.get("manifest_digest") or "")
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest.lower()):
            _append_failure(failures, "reverse_manifest", "invalid_digest", "manifest_digest", "64 hex chars", digest)

    mail_records = _json_summary_for_step(results, "mail_records")
    if _step_by_name(results, "mail_records") and mail_records is None and _step_by_name(results, "mail_records").get("returncode") == 0:
        failures.append({"step": "mail_records", "kind": "json_summary_missing"})
    elif mail_records:
        _require_positive(failures, "mail_records", mail_records, "records")
        _require_positive(failures, "mail_records", mail_records, "reward_items")
        _require_equal_field(failures, "mail_records", mail_records, "reward_items_with_icon", "reward_items")
        _require_equal_field(failures, "mail_records", mail_records, "reward_items_with_name", "reward_items")
        _require_equal_field(failures, "mail_records", mail_records, "reward_items_with_item_id", "reward_items")
        _require_equal_field(failures, "mail_records", mail_records, "with_content", "records")
        for field in [
            "icon_endpoint_failures",
            "missing_content_records",
            "weak_content_records",
            "malformed_content_records",
            "missing_reward_fields",
            "required_content_failures",
        ]:
            _require_empty(failures, "mail_records", mail_records, field)

    wiki_icons = _json_summary_for_step(results, "wiki_icons")
    if _step_by_name(results, "wiki_icons") and wiki_icons is None and _step_by_name(results, "wiki_icons").get("returncode") == 0:
        failures.append({"step": "wiki_icons", "kind": "json_summary_missing"})
    elif wiki_icons:
        _require_positive(failures, "wiki_icons", wiki_icons, "unique_icons")
        _require_positive(failures, "wiki_icons", wiki_icons, "icon_uses")
        for field in [
            "icon_endpoint_failures",
            "api_endpoint_errors",
            "browser_broken_image_endpoint_failures",
        ]:
            _require_zero(failures, "wiki_icons", wiki_icons, field)

    item_icons = _json_summary_for_step(results, "item_icons")
    if _step_by_name(results, "item_icons") and item_icons is None and _step_by_name(results, "item_icons").get("returncode") == 0:
        failures.append({"step": "item_icons", "kind": "json_summary_missing"})
    elif item_icons:
        _require_positive(failures, "item_icons", item_icons, "card_count")
        _require_positive(failures, "item_icons", item_icons, "unique_icon_count")
        _require_equal_field(failures, "item_icons", item_icons, "ok", "unique_icon_count")
        _require_zero(failures, "item_icons", item_icons, "fail")

    item_icon_quality_report = _json_summary_for_step(results, "item_icon_quality_report")
    if (
        _step_by_name(results, "item_icon_quality_report")
        and item_icon_quality_report is None
        and _step_by_name(results, "item_icon_quality_report").get("returncode") == 0
    ):
        failures.append({"step": "item_icon_quality_report", "kind": "json_summary_missing"})
    elif item_icon_quality_report:
        _require_positive(failures, "item_icon_quality_report", item_icon_quality_report, "item_count")
        _require_positive(failures, "item_icon_quality_report", item_icon_quality_report, "group_count")
        _require_positive(failures, "item_icon_quality_report", item_icon_quality_report, "primary_group_count")
        _require_positive(failures, "item_icon_quality_report", item_icon_quality_report, "small_group_count")
        _require_positive(failures, "item_icon_quality_report", item_icon_quality_report, "candidate_group_count")
        _require_positive(failures, "item_icon_quality_report", item_icon_quality_report, "candidate_icon_total")
        no_candidate_count = item_icon_quality_report.get("no_candidate_group_count")
        if isinstance(no_candidate_count, (int, float)) and no_candidate_count > 0:
            _require_positive(failures, "item_icon_quality_report", item_icon_quality_report, "unresolved_no_candidate_group_count")
            if not str(item_icon_quality_report.get("no_candidate_review_status") or "").strip():
                _append_failure(
                    failures,
                    "item_icon_quality_report",
                    "must_include_no_candidate_review_status",
                    "no_candidate_review_status",
                    "non-empty status",
                    item_icon_quality_report.get("no_candidate_review_status"),
                )
        _require_icon_review_no_candidate_details(failures, item_icon_quality_report)
        _require_icon_review_contact_sheet(failures, item_icon_quality_report)

    wiki_endpoints = _json_summary_for_step(results, "wiki_endpoints")
    if _step_by_name(results, "wiki_endpoints") and wiki_endpoints is None and _step_by_name(results, "wiki_endpoints").get("returncode") == 0:
        failures.append({"step": "wiki_endpoints", "kind": "json_summary_missing"})
    elif wiki_endpoints:
        _require_positive(failures, "wiki_endpoints", wiki_endpoints, "endpoint_count")
        _require_zero(failures, "wiki_endpoints", wiki_endpoints, "failure_count")

    card_catalogs = _json_summary_for_step(results, "card_catalogs")
    if _step_by_name(results, "card_catalogs") and card_catalogs is None and _step_by_name(results, "card_catalogs").get("returncode") == 0:
        failures.append({"step": "card_catalogs", "kind": "json_summary_missing"})
    elif card_catalogs:
        _require_positive(failures, "card_catalogs", card_catalogs, "catalog_count")
        _require_positive(failures, "card_catalogs", card_catalogs, "total_cards")
        _require_positive(failures, "card_catalogs", card_catalogs, "sample_count")
        _require_at_least(failures, "card_catalogs", card_catalogs, "sample_count", 48)
        _require_at_least(failures, "card_catalogs", card_catalogs, "details_ok", 40)
        for field in [
            "detail_failure_count",
            "field_failure_count",
            "icon_failure_count",
        ]:
            _require_zero(failures, "card_catalogs", card_catalogs, field)

    resource_links = _json_summary_for_step(results, "resource_links")
    if _step_by_name(results, "resource_links") and resource_links is None and _step_by_name(results, "resource_links").get("returncode") == 0:
        failures.append({"step": "resource_links", "kind": "json_summary_missing"})
    elif resource_links:
        _require_positive(failures, "resource_links", resource_links, "resource_count")
        _require_at_least(failures, "resource_links", resource_links, "required_resource_count", 1)
        _require_at_least(failures, "resource_links", resource_links, "mail_reward_resource_count", 8)
        _require_at_least(failures, "resource_links", resource_links, "icon_review_resource_count", 8)
        _require_positive(failures, "resource_links", resource_links, "loaded_icon_count")
        _require_equal_field(failures, "resource_links", resource_links, "loaded_icon_count", "required_icon_count")
        for field in [
            "failure_count",
            "required_resource_failure_count",
            "mail_reward_resource_failure_count",
            "icon_review_resource_failure_count",
            "title_mismatch_count",
            "icon_mismatch_count",
        ]:
            _require_zero(failures, "resource_links", resource_links, field)

    mail_browser = _json_summary_for_step(results, "mail_browser")
    if _step_by_name(results, "mail_browser") and mail_browser is None and _step_by_name(results, "mail_browser").get("returncode") == 0:
        failures.append({"step": "mail_browser", "kind": "json_summary_missing"})
    elif mail_browser:
        _require_positive(failures, "mail_browser", mail_browser, "mail_row_count")
        _require_positive(failures, "mail_browser", mail_browser, "mail_reward_image_count")
        _require_at_least(failures, "mail_browser", mail_browser, "mail_required_content_check_count", 2)
        for field in [
            "request_failure_count",
            "mail_broken_visible_reward_image_count",
            "mail_missing_icon_slot_count",
            "mail_empty_alt_image_count",
            "mail_invalid_item_link_count",
            "mail_required_content_failure_count",
        ]:
            _require_zero(failures, "mail_browser", mail_browser, field)
        _require_true(failures, "mail_browser", mail_browser, "mail_content_dialog_ok")
        _require_true(failures, "mail_browser", mail_browser, "mail_item_link_navigation_ok")

    item_browser = _json_summary_for_step(results, "item_browser")
    if _step_by_name(results, "item_browser") and item_browser is None and _step_by_name(results, "item_browser").get("returncode") == 0:
        failures.append({"step": "item_browser", "kind": "json_summary_missing"})
    elif item_browser:
        _require_positive(failures, "item_browser", item_browser, "item_row_icon_count")
        _require_positive(failures, "item_browser", item_browser, "item_route_filter_count")
        _require_positive(failures, "item_browser", item_browser, "item_route_clear_count")
        _require_positive(failures, "item_browser", item_browser, "item_icon_review_count")
        _require_at_least(failures, "item_browser", item_browser, "item_icon_route_check_count", 2)
        for field in [
            "request_failure_count",
            "broken_visible_image_count",
            "hard_image_failure_count",
            "item_route_filter_failure_count",
            "item_route_clear_failure_count",
            "item_icon_review_failure_count",
            "item_icon_route_check_failure_count",
            "item_row_icon_missing_count",
            "item_row_icon_mismatch_count",
            "item_row_icon_wait_timeout_count",
            "item_row_missing_id_count",
            "item_row_fallback_visible_count",
        ]:
            _require_zero(failures, "item_browser", item_browser, field)

    core_browser = _json_summary_for_step(results, "core_browser")
    if _step_by_name(results, "core_browser") and core_browser is None and _step_by_name(results, "core_browser").get("returncode") == 0:
        failures.append({"step": "core_browser", "kind": "json_summary_missing"})
    elif core_browser:
        _require_positive(failures, "core_browser", core_browser, "observation_count")
        _require_equal_field(failures, "core_browser", core_browser, "core_tab_with_rows_count", "core_tab_count")
        _require_equal_field(failures, "core_browser", core_browser, "core_tab_with_visible_images_count", "core_tab_image_required_count")
        for field in [
            "request_failure_count",
            "broken_visible_image_count",
            "hard_image_failure_count",
        ]:
            _require_zero(failures, "core_browser", core_browser, field)

    return failures


def _quality_steps(args: argparse.Namespace) -> list[tuple[str, list[str], int]]:
    steps: list[tuple[str, list[str], int]] = [
        (
            "reverse_boundary",
            _python_command("scripts/verify_fanxiu_reverse_boundary.py"),
            120,
        ),
        (
            "reverse_manifest",
            _python_command("scripts/verify_fanxiu_reverse_manifest.py"),
            180,
        ),
        (
            "mail_records",
            _python_command("scripts/verify_fanxiu_mail_records.py", "--api-timeout", str(args.api_timeout)),
            120,
        ),
        (
            "wiki_endpoints",
            _python_command("scripts/verify_fanxiu_wiki_endpoints.py", "--timeout", str(args.api_timeout)),
            120,
        ),
        (
            "wiki_icons",
            _python_command(
                "scripts/verify_fanxiu_wiki_icons.py",
                "--full-items",
                "--max-icons",
                "0",
                "--api-timeout",
                str(args.icon_timeout),
            ),
            360,
        ),
        (
            "item_icons",
            _python_command("scripts/verify_fanxiu_item_icons.py"),
            180,
        ),
        (
            "item_icon_quality_report",
            _python_command("scripts/build_fanxiu_item_icon_quality_report.py", "--threshold", "50"),
            120,
        ),
        (
            "card_catalogs",
            _python_command("scripts/verify_fanxiu_card_catalogs.py", "--api-timeout", str(args.api_timeout)),
            180,
        ),
        (
            "resource_links",
            _python_command(
                "scripts/verify_fanxiu_resource_links.py",
                "--api-timeout",
                str(args.api_timeout),
                "--wait-ms",
                str(args.wait_ms),
                "--samples-per-type",
                str(args.resource_samples_per_type),
                "--required-resource",
                "item:3080008",
                "--mail-reward-resource-samples",
                "8",
                "--icon-review-resource-samples",
                "8",
            ),
            240,
        ),
    ]
    if not args.skip_frontend:
        steps.extend(
            [
                ("frontend_typecheck", _npm_command("run", "typecheck", "--prefix", "frontend"), 180),
                ("frontend_build", _npm_command("run", "build", "--prefix", "frontend"), 300),
            ]
        )
    if not args.skip_browser:
        mail_browser_args = _python_command(
            "scripts/verify_fanxiu_wiki_browser.py",
            "--tab",
            "mail",
            "--local-auth-user",
            args.local_auth_user,
            "--scroll-steps",
            str(args.scroll_steps),
            "--wait-ms",
            str(args.wait_ms),
            "--mail-page-limit",
            str(args.mail_page_limit),
            "--screenshot",
        )
        item_browser_args = _python_command(
            "scripts/verify_fanxiu_wiki_browser.py",
            "--tab",
            "item",
            "--scroll-steps",
            str(args.item_scroll_steps),
            "--wait-ms",
            str(args.wait_ms),
            "--screenshot",
        )
        for label in args.item_type_label:
            item_browser_args.extend(["--item-type-label", label])
        core_browser_args = _python_command(
            "scripts/verify_fanxiu_wiki_browser.py",
            "--tab",
            "gongfa",
            "--tab",
            "activity",
            "--tab",
            "lingjie",
            "--tab",
            "digitdoor",
            "--tab",
            "doupotd",
            "--scroll-steps",
            str(args.core_scroll_steps),
            "--wait-ms",
            str(args.wait_ms),
        )
        steps.extend(
            [
                ("mail_browser", mail_browser_args, 180),
                (
                    "mail_records_final",
                    _python_command("scripts/verify_fanxiu_mail_records.py", "--api-timeout", str(args.api_timeout)),
                    120,
                ),
                ("item_browser", item_browser_args, 300),
                ("core_browser", core_browser_args, 180),
            ]
        )
    return steps


def verify_quality(args: argparse.Namespace) -> dict[str, Any]:
    steps = _quality_steps(args)
    results = [_run_quality_step(name, command_args, timeout=timeout) for name, command_args, timeout in steps]
    consistency_failures = _quality_consistency_failures(results)
    summary_failures = _quality_summary_failures(results)
    failed_steps = [row["name"] for row in results if row["returncode"] != 0]
    if consistency_failures:
        failed_steps.append("cross_layer_consistency")
    if summary_failures:
        failed_steps.append("quality_summary")
    return {
        "ok": not failed_steps,
        "step_count": len(results),
        "failed_steps": failed_steps,
        "consistency_failures": consistency_failures,
        "summary_failures": summary_failures,
        "steps": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Fanxiu wiki quality gate for mail, item icons, and browser rendering.")
    parser.add_argument("--api-timeout", type=int, default=30)
    parser.add_argument("--icon-timeout", type=int, default=60)
    parser.add_argument("--wait-ms", type=int, default=12000)
    parser.add_argument("--scroll-steps", type=int, default=1)
    parser.add_argument("--core-scroll-steps", type=int, default=1)
    parser.add_argument("--item-scroll-steps", type=int, default=3)
    parser.add_argument("--mail-page-limit", type=int, default=20)
    parser.add_argument("--resource-samples-per-type", type=int, default=5)
    parser.add_argument("--local-auth-user", default="admin")
    parser.add_argument("--item-type-label", action="append", default=list(DEFAULT_ITEM_TYPE_LABELS))
    parser.add_argument("--skip-browser", action="store_true")
    parser.add_argument("--skip-frontend", action="store_true")
    args = parser.parse_args()

    summary = verify_quality(args)
    public_summary = {
        "ok": summary["ok"],
        "step_count": summary["step_count"],
        "failed_steps": summary["failed_steps"],
        "consistency_failures": summary.get("consistency_failures", []),
        "summary_failures": summary.get("summary_failures", []),
        "steps": [
            {
                "name": row["name"],
                "returncode": row["returncode"],
                "command": row["command"],
                "timed_out": row.get("timed_out", False),
                "retry_count": row.get("retry_count", 0),
                "error": row.get("error", ""),
                "output_tail": _tail_output(row["output"]),
            }
            for row in summary["steps"]
        ],
    }
    print(json.dumps(public_summary, ensure_ascii=False, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
