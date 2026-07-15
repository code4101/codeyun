from __future__ import annotations

import datetime as dt
import os
import socket
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from sqlmodel import Session

from backend.core.ai.chat import (
    CODEX_CLI_DEFAULT_COMMAND,
    CODEX_CLI_DEFAULT_MODEL,
    AiProviderConfig,
    chat_with_provider,
)
from backend.core.settings import get_settings
from backend.models import AppSetting


FANXIU_SLIMMING_TASK_KEY = "fanxiu_slimming"
FANXIU_SLIMMING_RUN_TIME = "01:00"
FANXIU_SLIMMING_RETENTION_HOURS = 24
FANXIU_SLIMMING_PROVIDER_ID = "fanxiu-slimming-codex-cli"
FANXIU_SLIMMING_SETTING_KEY = "background_task.fanxiu_slimming.latest_run"
FANXIU_SLIMMING_DATA_DIR = Path(r"D:\home\chenkunze\data\m2508凡修")
FANXIU_SLIMMING_SOURCE_DIR = Path(r"D:\home\chenkunze\slns\fx")
FANXIU_SLIMMING_RESULT_TEXT_LIMIT = 20000


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name) or default)
    except ValueError:
        return default


FANXIU_SLIMMING_TIMEOUT_SECONDS = max(60, _env_int("CODEYUN_FANXIU_SLIMMING_TIMEOUT_SECONDS", 3600))


def is_fanxiu_slimming_allowed_host() -> bool:
    allowed_hosts = (os.getenv("CODEYUN_FANXIU_SLIMMING_ALLOWED_HOSTS") or "codepc_mf,mf").strip()
    if allowed_hosts == "*":
        return True
    allowed = {item.strip().lower() for item in allowed_hosts.split(",") if item.strip()}
    hostname = socket.gethostname().strip().lower()
    data_dir_name = get_settings().data_dir.name.strip().lower()
    return bool({hostname, data_dir_name} & allowed)


def _now_ts() -> float:
    return time.time()


def _format_ts(value: float | None) -> str:
    if not value:
        return ""
    return dt.datetime.fromtimestamp(value).replace(microsecond=0).isoformat(sep=" ")


def _truncate_text(text: str, limit: int = FANXIU_SLIMMING_RESULT_TEXT_LIMIT) -> str:
    normalized = str(text or "").strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def _safe_path_text(path: Path) -> str:
    return os.fspath(path.resolve(strict=False))


def _json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _save_latest_run(run: dict[str, Any], db_bind: Any | None = None) -> None:
    if db_bind is None:
        from backend.db import engine

        db_bind = engine

    with Session(db_bind) as session:
        row = session.get(AppSetting, FANXIU_SLIMMING_SETTING_KEY)
        if row is None:
            row = AppSetting(key=FANXIU_SLIMMING_SETTING_KEY)
        row.value = {"latest_run": run}
        row.updated_at = _now_ts()
        session.add(row)
        session.commit()


def _update_run(run: dict[str, Any], db_bind: Any | None = None, **updates: Any) -> None:
    run.update(updates)
    run["updated_at"] = _now_ts()
    _save_latest_run(run, db_bind=db_bind)


def get_fanxiu_slimming_status(session: Session) -> dict[str, Any]:
    row = session.get(AppSetting, FANXIU_SLIMMING_SETTING_KEY)
    latest_run = row.value.get("latest_run") if row and isinstance(row.value, dict) else None
    return {"latest_run": latest_run if isinstance(latest_run, dict) else None}


def _collect_directory_usage(root: Path, cutoff_ts: float) -> dict[str, Any]:
    root = root.resolve(strict=False)
    payload = {
        "path": os.fspath(root),
        "exists": root.exists(),
        "file_count": 0,
        "size_bytes": 0,
        "old_file_count": 0,
        "old_size_bytes": 0,
        "inaccessible_count": 0,
    }
    if not root.exists():
        return payload

    for current_root, dir_names, file_names in os.walk(root):
        dir_names[:] = [name for name in dir_names if name not in {".git"}]
        for file_name in file_names:
            path = Path(current_root) / file_name
            try:
                stat = path.stat()
            except OSError:
                payload["inaccessible_count"] += 1
                continue
            payload["file_count"] += 1
            payload["size_bytes"] += int(stat.st_size)
            if stat.st_mtime < cutoff_ts:
                payload["old_file_count"] += 1
                payload["old_size_bytes"] += int(stat.st_size)
    return payload


def _build_codex_provider(source_dir: Path) -> AiProviderConfig:
    return AiProviderConfig(
        id=FANXIU_SLIMMING_PROVIDER_ID,
        label="Codex CLI",
        kind="codex_cli",
        base_url=CODEX_CLI_DEFAULT_COMMAND,
        default_model=CODEX_CLI_DEFAULT_MODEL,
        timeout_seconds=FANXIU_SLIMMING_TIMEOUT_SECONDS,
        api_key="",
        supports_stream=False,
        supports_vision=False,
        requires_api_key=False,
        configured=True,
        models=(CODEX_CLI_DEFAULT_MODEL,),
        is_custom=False,
        workspace_dir=_safe_path_text(source_dir),
    )


def _build_slimming_prompt(
    *,
    data_dir: Path,
    source_dir: Path,
    cutoff_ts: float,
    before_usage: dict[str, Any],
) -> str:
    cutoff_text = _format_ts(cutoff_ts)
    return "\n".join(
        [
            "请作为后台工程代理，在本机执行凡修项目的安全减肥巡检。",
            "",
            "上下文：",
            "- 凡修脚本已经迁移到 codepc_mf 本机运行；data-annotation、Runtime、Scheduler 和资产树均以 mf 为准。",
            "- codepc_mi15 的旧凡修运行数据不再作为运行事实来源，不要再把结果同步回 mi15。",
            f"- 数据目录：{_safe_path_text(data_dir)}",
            f"- 源码目录：{_safe_path_text(source_dir)}",
            f"- 安全时间线：只能自动处理最后修改时间早于 {cutoff_text} 的文件或目录。",
            "",
            "当前粗略体积：",
            f"- 数据目录：{before_usage.get('data', {})}",
            f"- 源码目录：{before_usage.get('source', {})}",
            "",
            "硬性边界：",
            "1. 只能访问和修改上面两个路径内的内容。",
            "2. 不要执行 git commit、git reset、git checkout、git clean，也不要改动同步配置。",
            "3. 不要删除 .git、.env、数据库、账号凭据、业务配置、原始人工录入数据。",
            "4. 自动删除只限 24 小时前的明确日志、缓存、临时文件、运行产物、构建产物、__pycache__、.pytest_cache、空目录等低风险对象。",
            "5. 源码功能、业务脚本、Python/前端源码文件不要因为“看起来旧”就自动删除；只能在报告里列出疑似废弃功能和理由。",
            "6. 如果要删除源码目录里的非源码产物，必须先确认它是生成物、日志、缓存或临时调试输出。",
            "7. 如果无法安全判断，保留文件，只写入候选清单。",
            "",
            "执行要求：",
            "- 先巡视目录结构和大文件/旧文件分布，再做小范围安全清理。",
            "- 清理后做一次轻量复查，确认没有越界操作。",
            "- 最终用中文输出简短报告：清理了什么、节省多少估算空间、未清理但建议人工确认的源码/功能候选、风险或后续动作。",
        ]
    ).strip()


def _write_report(
    *,
    run: dict[str, Any],
    result_text: str,
    before_usage: dict[str, Any],
    after_usage: dict[str, Any],
) -> str:
    report_dir = get_settings().data_dir / "fanxiu-slimming"
    report_dir.mkdir(parents=True, exist_ok=True)
    created_at = _format_ts(run.get("created_at")).replace(":", "")
    report_path = report_dir / f"{created_at}-{run['id'][:8]}.md"
    report_path.write_text(
        "\n".join(
            [
                "# 凡修减肥巡检",
                "",
                f"- run_id: {run['id']}",
                f"- status: {run.get('status')}",
                f"- created_at: {_format_ts(run.get('created_at'))}",
                f"- data_dir: {run.get('data_dir')}",
                f"- source_dir: {run.get('source_dir')}",
                f"- cutoff_at: {run.get('cutoff_at')}",
                "",
                "## 清理前",
                "",
                "```json",
                _json_dumps(before_usage),
                "```",
                "",
                "## 清理后",
                "",
                "```json",
                _json_dumps(after_usage),
                "```",
                "",
                "## Codex 输出",
                "",
                result_text.strip() or "(Codex CLI 没有返回文本)",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return os.fspath(report_path)


def run_fanxiu_slimming_worker(
    *,
    chat_func: Callable[..., dict[str, Any]] = chat_with_provider,
    db_bind: Any | None = None,
    data_dir: Path | None = None,
    source_dir: Path | None = None,
    require_allowed_host: bool = True,
) -> None:
    data_dir = data_dir or FANXIU_SLIMMING_DATA_DIR
    source_dir = source_dir or FANXIU_SLIMMING_SOURCE_DIR
    now_ts = _now_ts()
    cutoff_ts = now_ts - FANXIU_SLIMMING_RETENTION_HOURS * 3600
    run = {
        "id": uuid.uuid4().hex,
        "status": "running",
        "stage": "starting",
        "stage_label": "准备巡检",
        "data_dir": _safe_path_text(data_dir),
        "source_dir": _safe_path_text(source_dir),
        "cutoff_at": _format_ts(cutoff_ts),
        "created_at": now_ts,
        "started_at": now_ts,
        "updated_at": now_ts,
        "finished_at": None,
        "provider": FANXIU_SLIMMING_PROVIDER_ID,
        "model": CODEX_CLI_DEFAULT_MODEL,
    }
    _save_latest_run(run, db_bind=db_bind)

    if require_allowed_host and not is_fanxiu_slimming_allowed_host():
        _update_run(
            run,
            db_bind,
            status="skipped",
            stage="wrong_host",
            stage_label="当前机器不是 mf，已跳过",
            finished_at=_now_ts(),
            result_text="当前任务只允许在 codepc_mf/mf 运行。",
        )
        return

    missing_paths = [os.fspath(path) for path in (data_dir, source_dir) if not path.exists()]
    if missing_paths:
        _update_run(
            run,
            db_bind,
            status="failed",
            stage="missing_paths",
            stage_label="目标路径不存在",
            finished_at=_now_ts(),
            error_message="; ".join(missing_paths),
        )
        raise FileNotFoundError(f"凡修减肥巡检目标路径不存在：{'; '.join(missing_paths)}")

    before_usage = {
        "data": _collect_directory_usage(data_dir, cutoff_ts),
        "source": _collect_directory_usage(source_dir, cutoff_ts),
    }
    _update_run(run, db_bind, stage="calling_codex", stage_label="调用 Codex CLI 巡检清理", before_usage=before_usage)

    provider = _build_codex_provider(source_dir)
    try:
        response = chat_func(
            provider_id=provider.id,
            model=provider.default_model,
            system_prompt=(
                "你是 CodeYun 的后台安全清理代理。必须严格遵守用户给定路径和 24 小时保留边界；"
                "源码功能层面的减肥只输出候选报告，不自动删除业务代码。"
            ),
            messages=[
                {
                    "role": "user",
                    "content": _build_slimming_prompt(
                        data_dir=data_dir,
                        source_dir=source_dir,
                        cutoff_ts=cutoff_ts,
                        before_usage=before_usage,
                    ),
                }
            ],
            timeout_seconds=provider.timeout_seconds,
            extra_providers=(provider,),
        )
    except Exception as exc:
        _update_run(
            run,
            db_bind,
            status="failed",
            stage="codex_failed",
            stage_label="Codex CLI 巡检失败",
            finished_at=_now_ts(),
            error_message=str(exc),
        )
        raise

    after_usage = {
        "data": _collect_directory_usage(data_dir, cutoff_ts),
        "source": _collect_directory_usage(source_dir, cutoff_ts),
    }
    result_text = _truncate_text(str(response.get("content") or ""))
    run["status"] = "completed"
    report_path = _write_report(
        run=run,
        result_text=result_text,
        before_usage=before_usage,
        after_usage=after_usage,
    )
    _update_run(
        run,
        db_bind,
        status="completed",
        stage="completed",
        stage_label="巡检完成",
        finished_at=_now_ts(),
        model=str(response.get("model") or provider.default_model),
        result_text=result_text,
        report_path=report_path,
        old_data_file_count=after_usage["data"].get("old_file_count"),
        old_source_file_count=after_usage["source"].get("old_file_count"),
        before_usage=before_usage,
        after_usage=after_usage,
    )


def enqueue_fanxiu_slimming() -> str | None:
    from backend.core.jobs.executor import background_task_queue

    task_id, _ = background_task_queue.enqueue_once(FANXIU_SLIMMING_TASK_KEY, run_fanxiu_slimming_worker)
    return task_id
