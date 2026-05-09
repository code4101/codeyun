from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlmodel import Session, select

from backend.core.ai_chat import OllamaClientError, chat_with_provider, get_ai_provider
from backend.core.settings import get_settings
from backend.models import AppSetting


CODEX_SAVER_SETTING_KEY = "global:codex_saver_config"
DEFAULT_LOG_FILE_NAME = ".codexsaver.log"
DEFAULT_LOG_BACKUP_FILE_NAME = ".codexsaver.log.backup"
MULTIMODAL_INPUT_KINDS = {
    "image",
    "screenshot",
    "video",
    "audio",
    "browser_visual",
    "file",
    "document",
    "attachment",
}
TEXT_INPUT_KINDS = {"text", "code", "diff", "json"}

RouteDecision = Literal["deepseek", "deny"]
ResultStatus = Literal["handled", "applied", "codex_required", "failed"]
_RUNS_LOCK = threading.Lock()
_ACTIVE_RUNS: dict[str, dict[str, Any]] = {}
_RECENT_RUNS: list[dict[str, Any]] = []
_MAX_RECENT_RUNS = 20


class CodexSaverError(RuntimeError):
    """Raised when CodexSaver configuration or execution cannot continue."""


class CodexSaverRuleMatch(BaseModel):
    prompt_includes: list[str] = Field(default_factory=list)
    path_includes: list[str] = Field(default_factory=list)
    file_extensions: list[str] = Field(default_factory=list)
    input_kinds: list[str] = Field(default_factory=list)


class CodexSaverRule(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    label: str
    enabled: bool = True
    order: int = 0
    match: CodexSaverRuleMatch = Field(default_factory=CodexSaverRuleMatch)
    decision: RouteDecision = "deepseek"
    reason: str = ""


class CodexSaverConfig(BaseModel):
    provider_id: str = "deepseek"
    model: str = ""
    flash_model: str = "deepseek-v4-flash"
    pro_model: str = "deepseek-v4-pro"
    use_flash_gate: bool = True
    default_decision: RouteDecision = "deepseek"
    multimodal_decision: RouteDecision = "deny"
    auto_apply: bool = True
    write_boundary_mode: Literal["none", "cwd", "allowlist"] = "none"
    allowed_write_roots: list[str] = Field(default_factory=list)
    log_file_name: str = DEFAULT_LOG_FILE_NAME
    log_backup_file_name: str = DEFAULT_LOG_BACKUP_FILE_NAME
    log_max_bytes: int = 1024 * 1024
    require_verification_success: bool = False
    rules: list[CodexSaverRule] = Field(default_factory=list)


class CodexSaverTask(BaseModel):
    task: str
    cwd: str = ""
    context: str = ""
    files: list[str] = Field(default_factory=list)
    input_kinds: list[str] = Field(default_factory=lambda: ["text"])
    verification_commands: list[str] = Field(default_factory=list)
    allow_auto_apply: bool | None = None


def build_default_codex_saver_config() -> dict[str, Any]:
    return CodexSaverConfig(
        rules=[
            CodexSaverRule(
                label="文本和代码默认 DeepSeek",
                order=100,
                match=CodexSaverRuleMatch(input_kinds=sorted(TEXT_INPUT_KINDS)),
                decision="deepseek",
                reason="DeepSeek 代理接管文本和代码类任务",
            ),
        ]
    ).model_dump()


def _normalize_config(payload: Any) -> CodexSaverConfig:
    if not isinstance(payload, dict) or not payload:
        payload = build_default_codex_saver_config()
    if isinstance(payload, dict):
        payload = dict(payload)
        legacy_model = str(payload.get("model") or "").strip()
        payload.setdefault("flash_model", "deepseek-v4-flash")
        payload.setdefault("pro_model", legacy_model or "deepseek-v4-pro")
        payload.setdefault("use_flash_gate", True)
        payload.setdefault("multimodal_decision", "deny")
        if payload.get("default_decision") == "codex":
            payload["default_decision"] = "deny"
        if payload.get("multimodal_decision") == "codex":
            payload["multimodal_decision"] = "deny"
        payload["rules"] = [
            {**rule, "decision": "deny"} if isinstance(rule, dict) and rule.get("decision") == "codex" else rule
            for rule in payload.get("rules") or []
        ]
    try:
        config = CodexSaverConfig.model_validate(payload)
    except Exception as exc:
        raise CodexSaverError(f"CodexSaver 配置无效：{exc}") from exc
    config.rules.sort(key=lambda item: item.order)
    return config


def get_codex_saver_config(session: Session) -> dict[str, Any]:
    row = session.exec(select(AppSetting).where(AppSetting.key == CODEX_SAVER_SETTING_KEY)).first()
    return _normalize_config(row.value if row else {}).model_dump()


def save_codex_saver_config(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    config = _normalize_config(payload)
    now = time.time()
    row = session.exec(select(AppSetting).where(AppSetting.key == CODEX_SAVER_SETTING_KEY)).first()
    if row is None:
        row = AppSetting(key=CODEX_SAVER_SETTING_KEY, value=config.model_dump(), updated_at=now)
        session.add(row)
    else:
        row.value = config.model_dump()
        row.updated_at = now
    session.commit()
    return config.model_dump()


def _contains_any(value: str, needles: list[str]) -> bool:
    haystack = value.lower()
    return any(needle.strip().lower() in haystack for needle in needles if needle.strip())


def _match_rule(rule: CodexSaverRule, task: CodexSaverTask) -> bool:
    matcher = rule.match
    checks: list[bool] = []
    if matcher.prompt_includes:
        checks.append(_contains_any(task.task, matcher.prompt_includes))
    if matcher.path_includes:
        paths = "\n".join([task.cwd, *task.files])
        checks.append(_contains_any(paths, matcher.path_includes))
    if matcher.file_extensions:
        wanted = {item.strip().lower() for item in matcher.file_extensions if item.strip()}
        checks.append(any(Path(path).suffix.lower() in wanted for path in task.files))
    if matcher.input_kinds:
        wanted = {item.strip().lower() for item in matcher.input_kinds if item.strip()}
        checks.append(any(item.strip().lower() in wanted for item in task.input_kinds))
    return bool(checks) and all(checks)


def _route_task(config: CodexSaverConfig, task: CodexSaverTask) -> dict[str, Any]:
    task_input_kinds = {item.strip().lower() for item in task.input_kinds if item.strip()}
    multimodal_kinds = sorted(task_input_kinds & MULTIMODAL_INPUT_KINDS)
    if multimodal_kinds and config.multimodal_decision != "deepseek":
        reason = "多模态输入被 CodexSaver 拒绝，交给外部 Codex 处理"
        return {
            "decision": config.multimodal_decision,
            "reason": reason,
            "hit_rules": [],
        }
    for rule in sorted((item for item in config.rules if item.enabled), key=lambda item: item.order):
        if _match_rule(rule, task):
            return {
                "decision": rule.decision,
                "reason": rule.reason or rule.label,
                "hit_rules": [rule.model_dump()],
            }
    return {
        "decision": config.default_decision,
        "reason": "使用默认路由策略",
        "hit_rules": [],
    }


def preview_codex_saver_route(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    config = _normalize_config(get_codex_saver_config(session))
    task = CodexSaverTask.model_validate(payload)
    return _route_task(config, task)


def _resolve_cwd(task: CodexSaverTask) -> Path:
    cwd = Path(task.cwd).expanduser() if task.cwd.strip() else Path.cwd()
    return cwd.resolve()


def _log_paths(config: CodexSaverConfig, cwd: Path) -> tuple[Path, Path]:
    return cwd / config.log_file_name, cwd / config.log_backup_file_name


def _append_log(config: CodexSaverConfig, cwd: Path, payload: dict[str, Any]) -> str:
    log_path, backup_path = _log_paths(config, cwd)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
    if log_path.exists() and log_path.stat().st_size + len(encoded.encode("utf-8")) > config.log_max_bytes:
        if backup_path.exists():
            backup_path.unlink()
        log_path.replace(backup_path)
    with log_path.open("a", encoding="utf-8") as file:
        file.write(encoded)
    return str(log_path)


def get_codex_saver_logs(session: Session, cwd: str = "", max_bytes: int = 200_000) -> dict[str, Any]:
    config = _normalize_config(get_codex_saver_config(session))
    resolved_cwd = Path(cwd).expanduser().resolve() if cwd.strip() else Path.cwd().resolve()
    items = []
    for path in _log_paths(config, resolved_cwd):
        if not path.exists():
            continue
        data = path.read_bytes()[-max(1, max_bytes):]
        items.append({"path": str(path), "content": data.decode("utf-8", errors="replace")})
    return {"items": items}


def _task_preview(task: CodexSaverTask) -> str:
    text = " ".join(task.task.strip().split())
    return text[:220]


def _snapshot_run(run: dict[str, Any]) -> dict[str, Any]:
    snapshot = dict(run)
    snapshot["age_ms"] = int(max(0, time.time() - float(snapshot.get("started_at") or time.time())) * 1000)
    if snapshot.get("finished_at"):
        snapshot["duration_ms"] = int(
            max(0, float(snapshot["finished_at"]) - float(snapshot.get("started_at") or snapshot["finished_at"])) * 1000
        )
    return snapshot


def _start_run(task: CodexSaverTask) -> str:
    run_id = uuid.uuid4().hex
    now = time.time()
    run = {
        "id": run_id,
        "status": "running",
        "stage": "received",
        "started_at": now,
        "updated_at": now,
        "finished_at": None,
        "task": _task_preview(task),
        "cwd": task.cwd,
        "input_kinds": task.input_kinds,
        "model": "",
        "model_tier": "",
        "summary": "",
        "reason": "",
        "error": "",
        "fallback": "",
    }
    with _RUNS_LOCK:
        _ACTIVE_RUNS[run_id] = run
    return run_id


def _update_run(run_id: str, **updates: Any) -> None:
    with _RUNS_LOCK:
        run = _ACTIVE_RUNS.get(run_id)
        if not run:
            return
        run.update(updates)
        run["updated_at"] = time.time()


def _finish_run(run_id: str, result: dict[str, Any]) -> None:
    now = time.time()
    with _RUNS_LOCK:
        run = _ACTIVE_RUNS.pop(run_id, None)
        if not run:
            return
        run.update(
            {
                "status": result.get("status", "unknown"),
                "stage": "finished",
                "updated_at": now,
                "finished_at": now,
                "model": result.get("model", ""),
                "model_tier": result.get("model_tier", ""),
                "summary": result.get("summary", ""),
                "reason": result.get("reason", ""),
                "error": result.get("error", ""),
                "fallback": result.get("fallback", ""),
                "duration_ms": result.get("duration_ms", 0),
            }
        )
        _RECENT_RUNS.insert(0, run)
        del _RECENT_RUNS[_MAX_RECENT_RUNS:]


def get_codex_saver_runtime_status() -> dict[str, Any]:
    with _RUNS_LOCK:
        active = [_snapshot_run(item) for item in _ACTIVE_RUNS.values()]
        recent = [_snapshot_run(item) for item in _RECENT_RUNS]
    active.sort(key=lambda item: item.get("started_at") or 0, reverse=True)
    return {"active": active, "recent": recent, "now": time.time()}


def get_codex_saver_mcp_bearer_config(*, reveal: bool = False) -> dict[str, Any]:
    token = get_settings().device_token.strip()
    return {
        "url": "http://localhost:8000/api/codex-saver/mcp/",
        "environment_variable": "MCP_BEARER_TOKEN",
        "header_name": "Authorization",
        "header_scheme": "Bearer",
        "configured": bool(token),
        "token": token if reveal else "",
    }


def _build_deepseek_prompt(task: CodexSaverTask) -> str:
    return (
        "Task:\n"
        f"{task.task.strip()}\n\n"
        "Context:\n"
        f"{task.context.strip()}\n\n"
        "Files:\n"
        f"{json.dumps(task.files, ensure_ascii=False)}"
    )


def _build_worker_system_prompt(*, tier: Literal["flash", "pro"]) -> str:
    if tier == "flash":
        return (
            "You are the fast CodexSaver worker using a cheap model. "
            "Return only JSON. You handle text and code tasks only. Handle simple requests directly "
            "when the provided text/code context is enough, including explanation, planning, writing, "
            "summarization, translation, operations guidance, and code work. "
            "For complex implementation, architecture, broad refactors, risky edits, "
            "images, screenshots, audio, video, documents, file attachments, missing local/UI/visual "
            "context, or anything uncertain, do not solve it; return "
            '{"status":"escalate","reason":"...","summary":"","patch":"","changed_files":[],"commands":[]}. '
            "For handled tasks return JSON with status='handled', summary, patch, changed_files, commands. "
            "patch must be an empty string when no file edit is needed."
        )
    return (
        "You are the stronger CodexSaver worker. Return only JSON with keys: "
        "status, summary, patch, changed_files, commands. "
        "You are responsible for text and code CodeYun request types only. "
        "Use status='handled' unless Codex itself is required because local tools, UI/browser work, "
        "visual inspection, multimodal input, document/file attachment analysis, or unavailable context is needed. "
        "If no file edit is needed, patch must be an empty string."
    )


def _parse_worker_json(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CodexSaverError(f"DeepSeek 返回不是合法 JSON：{exc}") from exc
    if not isinstance(payload, dict):
        raise CodexSaverError("DeepSeek 返回 JSON 必须是对象")
    return payload


def _extract_patch(worker_payload: dict[str, Any]) -> str:
    patch = worker_payload.get("patch")
    return patch if isinstance(patch, str) else ""


def _worker_requests_escalation(worker_payload: dict[str, Any]) -> bool:
    status = str(worker_payload.get("status") or "").strip().lower()
    decision = str(worker_payload.get("decision") or "").strip().lower()
    return status in {"escalate", "refuse", "refused", "pro_required"} or decision in {
        "escalate",
        "pro",
        "pro_required",
    }


def _call_deepseek_worker(
    *,
    config: CodexSaverConfig,
    task: CodexSaverTask,
    model: str,
    tier: Literal["flash", "pro"],
    timeout_seconds: float,
) -> dict[str, Any]:
    response = chat_with_provider(
        provider_id=config.provider_id,
        messages=[{"role": "user", "content": _build_deepseek_prompt(task)}],
        model=model,
        system_prompt=_build_worker_system_prompt(tier=tier),
        temperature=0.1,
        timeout_seconds=timeout_seconds,
    )
    return _parse_worker_json(str(response.get("content") or ""))


def _check_write_boundary(config: CodexSaverConfig, cwd: Path, patch_text: str) -> None:
    if config.write_boundary_mode == "none" or not patch_text.strip():
        return
    roots = [cwd] if config.write_boundary_mode == "cwd" else [
        Path(item).expanduser().resolve() for item in config.allowed_write_roots if item.strip()
    ]
    if not roots:
        raise CodexSaverError("自动应用已启用，但没有可写根目录")
    for line in patch_text.splitlines():
        if not (line.startswith("+++ ") or line.startswith("--- ")):
            continue
        raw_path = line[4:].strip()
        if raw_path == "/dev/null":
            continue
        if raw_path.startswith(("a/", "b/")):
            raw_path = raw_path[2:]
        target = (cwd / raw_path).resolve()
        if not any(target == root or root in target.parents for root in roots):
            raise CodexSaverError(f"补丁路径超出写入边界：{target}")


def _run_patch_check(cwd: Path, patch_text: str) -> dict[str, Any]:
    if not patch_text.strip():
        return {"ok": True, "checked": False, "reason": "无 patch"}
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".patch", delete=False) as file:
        file.write(patch_text)
        patch_path = file.name
    try:
        result = subprocess.run(
            ["git", "apply", "--check", patch_path],
            cwd=os.fspath(cwd),
            text=True,
            capture_output=True,
            timeout=60,
        )
        return {
            "ok": result.returncode == 0,
            "checked": True,
            "stdout": result.stdout[-4000:],
            "stderr": result.stderr[-4000:],
        }
    finally:
        Path(patch_path).unlink(missing_ok=True)


def _apply_patch(cwd: Path, patch_text: str) -> dict[str, Any]:
    if not patch_text.strip():
        return {"applied": False, "reason": "无 patch"}
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".patch", delete=False) as file:
        file.write(patch_text)
        patch_path = file.name
    try:
        result = subprocess.run(
            ["git", "apply", patch_path],
            cwd=os.fspath(cwd),
            text=True,
            capture_output=True,
            timeout=60,
        )
        return {
            "applied": result.returncode == 0,
            "stdout": result.stdout[-4000:],
            "stderr": result.stderr[-4000:],
        }
    finally:
        Path(patch_path).unlink(missing_ok=True)


def _run_verification_commands(cwd: Path, commands: list[str]) -> list[dict[str, Any]]:
    results = []
    for command in commands:
        if not command.strip():
            continue
        result = subprocess.run(
            command,
            cwd=os.fspath(cwd),
            text=True,
            capture_output=True,
            shell=True,
            timeout=180,
        )
        results.append(
            {
                "command": command,
                "ok": result.returncode == 0,
                "stdout": result.stdout[-4000:],
                "stderr": result.stderr[-4000:],
            }
        )
    return results


def execute_codex_saver_task(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    started = time.time()
    config = _normalize_config(get_codex_saver_config(session))
    task = CodexSaverTask.model_validate(payload)
    run_id = _start_run(task)
    cwd = _resolve_cwd(task)
    route = _route_task(config, task)
    _update_run(run_id, stage="routed", reason=route["reason"], model=config.pro_model or config.model)
    base_result = {
        "reason": route["reason"],
        "hit_rules": route["hit_rules"],
        "model": config.pro_model or config.model,
        "duration_ms": 0,
    }
    if route["decision"] != "deepseek":
        result = {"status": "codex_required", **base_result}
        result["duration_ms"] = int((time.time() - started) * 1000)
        _append_log(config, cwd, {"event": "route", **result})
        _finish_run(run_id, result)
        return result

    try:
        _update_run(run_id, stage="provider")
        provider = get_ai_provider(config.provider_id)
        flash_model = config.flash_model.strip() or "deepseek-v4-flash"
        pro_model = config.pro_model.strip() or config.model.strip() or provider.default_model or "deepseek-v4-pro"
        used_model = pro_model
        worker_tier = "pro"
        escalated = False
        escalation_reason = ""
        if config.use_flash_gate:
            try:
                _update_run(run_id, stage="flash", model=flash_model, model_tier="flash")
                worker_payload = _call_deepseek_worker(
                    config=config,
                    task=task,
                    model=flash_model,
                    tier="flash",
                    timeout_seconds=provider.timeout_seconds,
                )
                used_model = flash_model
                worker_tier = "flash"
                if _worker_requests_escalation(worker_payload):
                    escalated = True
                    escalation_reason = str(worker_payload.get("reason") or "flash requested pro")
            except (CodexSaverError, OllamaClientError) as exc:
                escalated = True
                escalation_reason = f"flash failed: {exc}"
        else:
            worker_payload = {}

        if not config.use_flash_gate or escalated:
            _update_run(run_id, stage="pro", model=pro_model, model_tier="pro", reason=escalation_reason)
            worker_payload = _call_deepseek_worker(
                config=config,
                task=task,
                model=pro_model,
                tier="pro",
                timeout_seconds=provider.timeout_seconds,
            )
            used_model = pro_model
            worker_tier = "pro"

        patch_text = _extract_patch(worker_payload)
        _update_run(run_id, stage="patch_check", summary=worker_payload.get("summary", ""))
        _check_write_boundary(config, cwd, patch_text)
        patch_check = _run_patch_check(cwd, patch_text)
        command_results: list[dict[str, Any]] = []
        if patch_check["ok"]:
            _update_run(run_id, stage="verification")
            command_results = _run_verification_commands(cwd, task.verification_commands)
        verification_ok = bool(patch_check["ok"]) and all(item["ok"] for item in command_results)
        if config.require_verification_success and not verification_ok:
            raise CodexSaverError("验证未通过")
        should_apply = config.auto_apply if task.allow_auto_apply is None else bool(task.allow_auto_apply)
        apply_result = {"applied": False, "reason": "未开启自动应用"}
        status: ResultStatus = "handled"
        if should_apply and patch_text.strip():
            if not patch_check["ok"]:
                raise CodexSaverError("patch check 未通过，拒绝自动应用")
            _update_run(run_id, stage="apply")
            apply_result = _apply_patch(cwd, patch_text)
            status = "applied" if apply_result["applied"] else "failed"
        result = {
            "status": status,
            **base_result,
            "model": used_model,
            "model_tier": worker_tier,
            "escalated": escalated,
            "escalation_reason": escalation_reason,
            "summary": worker_payload.get("summary", ""),
            "patch": patch_text,
            "changed_files": worker_payload.get("changed_files", []),
            "verification": {
                "ok": verification_ok,
                "patch": patch_check,
                "commands": command_results,
                "apply": apply_result,
            },
        }
    except (CodexSaverError, OllamaClientError, subprocess.SubprocessError, OSError) as exc:
        result = {"status": "failed", **base_result, "error": str(exc), "fallback": "codex_required"}

    result["duration_ms"] = int((time.time() - started) * 1000)
    result["log_ref"] = _append_log(config, cwd, {"event": "execute", **result})
    _finish_run(run_id, result)
    return result


def doctor_codex_saver(session: Session, cwd: str = "") -> dict[str, Any]:
    config = _normalize_config(get_codex_saver_config(session))
    resolved_cwd = Path(cwd).expanduser().resolve() if cwd.strip() else Path.cwd().resolve()
    provider_status: dict[str, Any]
    try:
        provider = get_ai_provider(config.provider_id)
        provider_status = {
            "id": provider.id,
            "label": provider.label,
            "kind": provider.kind,
            "configured": provider.configured,
            "supports_vision": provider.supports_vision,
            "default_model": provider.default_model,
            "flash_model": config.flash_model,
            "pro_model": config.pro_model,
            "use_flash_gate": config.use_flash_gate,
        }
    except Exception as exc:
        provider_status = {"configured": False, "error": str(exc)}
    log_path, _ = _log_paths(config, resolved_cwd)
    writable = True
    error = ""
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=os.fspath(log_path.parent), delete=True):
            pass
    except OSError as exc:
        writable = False
        error = str(exc)
    return {
        "provider": provider_status,
        "log": {"path": str(log_path), "writable": writable, "error": error},
        "mcp": {
            "available": True,
            "stdio_entry": "python -m backend.core.codex_saver.mcp_server",
            "streamable_http_url": "http://localhost:8000/api/codex-saver/mcp/",
        },
    }
