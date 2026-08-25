from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from filelock import Timeout


REPO_ROOT = Path(__file__).resolve().parents[1]
if os.fspath(REPO_ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(REPO_ROOT))

DEFAULT_MAX_REMOTE_OPERATIONS = 50
MAX_REMOTE_OPERATIONS = 500
DEFAULT_SOURCES = ("pixiv_download",)
PIXIV_SOURCES = {
    "pixiv",
    "pixiv_download",
    "pixiv_home",
    "pixiv_related",
    "pixiv_membership",
}


class PixivIncrementalSafetyError(RuntimeError):
    """Raised when a safety gate blocks the formal Pixiv entrypoint."""


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _validate_budget(value: int) -> int:
    normalized = int(value)
    if not 1 <= normalized <= MAX_REMOTE_OPERATIONS:
        raise ValueError(f"Pixiv 远端操作预算必须在 1..{MAX_REMOTE_OPERATIONS} 之间。")
    return normalized


def _normalize_sources(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for raw_value in values:
        value = str(raw_value or "").strip().lower().replace("-", "_")
        if value == "pixiv_collect_ids":
            value = "pixiv_download"
        if value not in PIXIV_SOURCES:
            raise ValueError(f"不支持的 Pixiv 来源：{raw_value}")
        if value not in result:
            result.append(value)
    if not result:
        raise ValueError("至少需要一个 Pixiv 来源。")
    return result


def _optimizer_root() -> Path:
    from backend.core.settings import get_settings

    return get_settings().data_dir / "private-media-sync" / "optimizer"


def _legacy_trigger_enabled() -> bool:
    from backend.core.jobs.scheduler import MEDIA_SYNC_HOME_DISCOVERY_TASK_KEY, _is_task_enabled
    from backend.plugins.modules.media_sync.runtime import has_scheduled_pixiv_profiles

    return bool(_is_task_enabled(MEDIA_SYNC_HOME_DISCOVERY_TASK_KEY) and has_scheduled_pixiv_profiles())


def _load_profiles(user_id: int | None) -> list[Any]:
    from sqlmodel import Session, select

    from backend.db import engine
    from backend.plugins.modules.media_sync.models import MediaSyncProfile, ensure_private_media_sync_schema

    ensure_private_media_sync_schema()
    with Session(engine) as session:
        statement = select(MediaSyncProfile)
        if user_id is not None:
            statement = statement.where(MediaSyncProfile.user_id == int(user_id))
        profiles = list(session.exec(statement).all())
    return [profile for profile in profiles if bool(profile.pixiv_enabled)]


def _busy_profile_ids(profiles: Iterable[Any]) -> list[int]:
    return sorted(
        int(profile.user_id)
        for profile in profiles
        if str(profile.last_run_status or "").strip().lower() == "running"
    )


def _active_risk_circuit() -> dict[str, Any] | None:
    from backend.plugins.modules.media_sync.sources import read_pixiv_risk_circuit

    return read_pixiv_risk_circuit()


def _pixiv_source_activity_lease(*, lock_path: Path, timeout: float):
    from backend.plugins.modules.media_sync.runtime import pixiv_source_activity_lease

    return pixiv_source_activity_lease(lock_path=lock_path, timeout=timeout)


def _run_profile(
    profile: Any,
    *,
    sources: list[str],
    target_new_candidates: int,
    run_id: str,
) -> dict[str, Any]:
    from backend.plugins.modules.media_sync.runtime import sync_job_manager

    return sync_job_manager.run_now(
        profile,
        sources=sources,
        overrides={"platform_download_target_count": int(target_new_candidates)},
        scope_key=f"pixiv-incremental:{run_id}",
    )


def _empty_remote_audit(max_remote_operations: int) -> dict[str, Any]:
    return {
        "max_remote_operations": int(max_remote_operations),
        "remote_operations_total": 0,
        "operation_counts": {},
        "page_navigations": 0,
        "api_requests": 0,
        "detail_requests": 0,
        "downloads": 0,
        "retries": 0,
        "stop_reason": None,
        "error": None,
        "risk_circuit": None,
    }


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.{uuid4().hex}.tmp")
    temporary_path.write_text(content, encoding="utf-8")
    temporary_path.replace(path)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    _write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _render_markdown(report: dict[str, Any]) -> str:
    audit = report.get("remote_audit") or {}
    return "\n".join(
        [
            f"# Pixiv 增量同步审计：{report['run_id']}",
            "",
            f"- 状态：`{report.get('status')}`；停止原因：`{report.get('stop_reason')}`。",
            f"- 来源：{', '.join(report.get('requested_sources') or [])}。",
            f"- 旧入口启用：{report.get('legacy_trigger_enabled')}；运行中资料：{report.get('busy_profile_ids') or []}。",
            f"- 远端操作预算/实际：{audit.get('max_remote_operations', 0)}/{audit.get('remote_operations_total', 0)}。",
            f"- 页面/API/详情/下载/重试：{audit.get('page_navigations', 0)}/{audit.get('api_requests', 0)}/"
            f"{audit.get('detail_requests', 0)}/{audit.get('downloads', 0)}/{audit.get('retries', 0)}。",
            f"- 风控熔断：{(audit.get('risk_circuit') or report.get('persistent_risk_circuit') or {}).get('reason') or '否'}。",
            f"- 开始：{report.get('started_at')}；结束：{report.get('finished_at')}。",
            "",
        ]
    )


def _persist_report(optimizer_root: Path, report: dict[str, Any]) -> Path:
    started_at = datetime.fromisoformat(str(report["started_at"]))
    report_path = optimizer_root / "runs" / f"{started_at:%Y-%m-%d}-{report['run_id']}.json"
    markdown_path = report_path.with_suffix(".md")
    report["report_json"] = os.fspath(report_path)
    report["report_markdown"] = os.fspath(markdown_path)
    _write_json(report_path, report)
    _write_text(markdown_path, _render_markdown(report))
    latest = {
        "run_id": report["run_id"],
        "finished_at": report.get("finished_at"),
        "status": report.get("status"),
        "stop_reason": report.get("stop_reason"),
        "report_json": os.fspath(report_path),
        "report_markdown": os.fspath(markdown_path),
        "remote_operations_total": (report.get("remote_audit") or {}).get("remote_operations_total", 0),
    }
    _write_json(optimizer_root / "latest.json", latest)
    return report_path


def run_once(
    *,
    max_remote_operations: int,
    sources: Iterable[str] = DEFAULT_SOURCES,
    target_new_candidates: int = 20,
    user_id: int | None = None,
    check_only: bool = False,
    optimizer_root: Path | None = None,
    run_id: str | None = None,
) -> tuple[dict[str, Any], Path]:
    budget = _validate_budget(max_remote_operations)
    normalized_sources = _normalize_sources(sources)
    normalized_target = max(int(target_new_candidates), 0)
    actual_run_id = str(run_id or f"{datetime.now():%Y%m%dT%H%M%S}-{uuid4().hex[:8]}")
    root = Path(optimizer_root) if optimizer_root is not None else _optimizer_root()
    report: dict[str, Any] = {
        "schema_version": 1,
        "run_id": actual_run_id,
        "source": "scripts/run_pixiv_incremental_once.py",
        "started_at": _now_iso(),
        "finished_at": None,
        "status": "running",
        "stop_reason": None,
        "requested_sources": normalized_sources,
        "target_new_candidates": normalized_target,
        "requested_user_id": user_id,
        "legacy_trigger_enabled": None,
        "busy_profile_ids": [],
        "profile_count": 0,
        "profiles": {},
        "persistent_risk_circuit": None,
        "remote_audit": _empty_remote_audit(budget),
    }
    report_path: Path | None = None
    try:
        with _pixiv_source_activity_lease(
            lock_path=root / "pixiv-incremental.lock",
            timeout=0,
        ):
            report["legacy_trigger_enabled"] = _legacy_trigger_enabled()
            if report["legacy_trigger_enabled"]:
                raise PixivIncrementalSafetyError("legacy_media_sync_home_discovery_enabled")

            report["persistent_risk_circuit"] = _active_risk_circuit()
            if report["persistent_risk_circuit"]:
                raise PixivIncrementalSafetyError("risk_circuit_open")

            profiles = _load_profiles(user_id)
            report["profile_count"] = len(profiles)
            report["busy_profile_ids"] = _busy_profile_ids(profiles)
            if not profiles:
                raise PixivIncrementalSafetyError("no_enabled_pixiv_profile")

            if check_only:
                report["status"] = "check_passed"
                report["stop_reason"] = "check_only"
            else:
                from backend.plugins.modules.media_sync.sources import pixiv_remote_run_audit

                audit_context = pixiv_remote_run_audit(
                    source="formal_incremental_entrypoint",
                    max_remote_operations=budget,
                    run_id=actual_run_id,
                )
                with audit_context as audit:
                    for profile in profiles:
                        report["profiles"][str(profile.user_id)] = _run_profile(
                            profile,
                            sources=normalized_sources,
                            target_new_candidates=normalized_target,
                            run_id=actual_run_id,
                        )
                        if audit.snapshot().get("stop_reason") in {"risk_circuit_open", "risk_circuit_tripped"}:
                            break
                report["remote_audit"] = audit.snapshot()
                audit_stop_reason = str(report["remote_audit"].get("stop_reason") or "")
                failed_profiles = [
                    profile_id
                    for profile_id, snapshot in report["profiles"].items()
                    if snapshot.get("error") or snapshot.get("stage") == "error"
                ]
                if audit_stop_reason in {"risk_circuit_open", "risk_circuit_tripped"}:
                    report["status"] = "safety_stopped"
                    report["stop_reason"] = audit_stop_reason
                else:
                    report["status"] = "partial_error" if failed_profiles else "completed"
                    report["stop_reason"] = "profile_error" if failed_profiles else "completed"
    except Timeout:
        report["status"] = "safety_skipped"
        report["stop_reason"] = "pixiv_source_activity_lease_held"
    except PixivIncrementalSafetyError as exc:
        report["status"] = "safety_skipped"
        report["stop_reason"] = str(exc)
    except ModuleNotFoundError as exc:
        missing_module = str(exc.name or "")
        if missing_module == "backend.plugins.modules.media_sync" or missing_module.startswith(
            "backend.plugins.modules.media_sync."
        ):
            report["status"] = "safety_skipped"
            report["stop_reason"] = "media_sync_plugin_missing"
            report["error"] = f"Missing required private plugin module: {missing_module}"
        else:
            report["status"] = "failed"
            report["stop_reason"] = report["stop_reason"] or "error"
            report["error"] = str(exc)
    except Exception as exc:
        report["status"] = "failed"
        report["stop_reason"] = report["stop_reason"] or "error"
        report["error"] = str(exc)
        audit = locals().get("audit")
        if audit is not None:
            report["remote_audit"] = audit.snapshot()
    finally:
        report["finished_at"] = _now_iso()
        report_path = _persist_report(root, report)

    return report, report_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="受预算、可审计的 Pixiv 单次增量同步正式入口。")
    parser.add_argument(
        "--max-remote-operations",
        type=int,
        default=DEFAULT_MAX_REMOTE_OPERATIONS,
        help=f"本轮 Pixiv 远端操作硬预算，范围 1..{MAX_REMOTE_OPERATIONS}（默认 50）。",
    )
    parser.add_argument("--source", action="append", dest="sources", choices=sorted(PIXIV_SOURCES))
    parser.add_argument("--target-new-candidates", type=int, default=20)
    parser.add_argument("--user-id", type=int)
    parser.add_argument("--check-only", action="store_true", help="只验证互斥和配置安全门，不运行 Pixiv。")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report, report_path = run_once(
        max_remote_operations=args.max_remote_operations,
        sources=args.sources or DEFAULT_SOURCES,
        target_new_candidates=args.target_new_candidates,
        user_id=args.user_id,
        check_only=args.check_only,
    )
    print(json.dumps({"report_path": os.fspath(report_path), **report}, ensure_ascii=False, indent=2))
    return 0 if report["status"] in {"completed", "check_passed"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
