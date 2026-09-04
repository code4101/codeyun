from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from backend.core.services.launcher import popen_service
from backend.core.settings import ROOT_DIR
from backend.core.temp_paths import codeyun_temp_root


DEFAULT_ESCALATION_MODEL = "gpt-5.6-sol"
DEFAULT_ESCALATION_REASONING_EFFORT = "high"
SUPPORTED_REASONING_EFFORTS = {"low", "medium", "high", "xhigh", "max", "ultra"}


@dataclass(frozen=True)
class CodexEscalationRequest:
    """Describe one engineering incident that Codex must own to completion."""

    title: str
    problem: str
    objective: str
    suggested_focus: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    attempted_actions: tuple[str, ...] = ()
    recovery_instructions: str = ""
    completion_criteria: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()


@dataclass(frozen=True)
class CodexDispatch:
    """Describe one locally dispatched Codex task."""

    dispatch_id: str
    pid: int
    workspace_dir: str
    request_path: str
    prompt_path: str
    stdout_path: str
    stderr_path: str
    created_at: str

    def model_dump(self) -> dict[str, Any]:
        """Return a JSON-serializable representation of the dispatch."""

        return asdict(self)


@dataclass(frozen=True)
class CodexDispatchStatus:
    """Expose the observable state parsed from one Codex dispatch."""

    dispatch_id: str
    status: Literal["starting", "running", "completed", "failed"]
    pid: int
    thread_id: str | None
    codex_url: str | None
    last_message: str | None
    error: str | None
    stdout_path: str
    stderr_path: str

    def model_dump(self) -> dict[str, Any]:
        """Return a JSON-serializable representation of the status."""

        return asdict(self)


def _resolve_codex_executable() -> str:
    candidates = ("codex.exe", "codex") if os.name == "nt" else ("codex",)
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved and (os.name != "nt" or Path(resolved).suffix.lower() == ".exe"):
            return resolved
    raise FileNotFoundError("未找到可直接启动的 Codex CLI")


def _normalized_lines(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(text for value in values if (text := str(value).strip()))


def _render_section(title: str, values: tuple[str, ...]) -> list[str]:
    normalized = _normalized_lines(values)
    if not normalized:
        return []
    return ["", f"## {title}", *(f"- {value}" for value in normalized)]


def build_codex_escalation_prompt(request: CodexEscalationRequest) -> str:
    """Build a self-contained handoff prompt for an independent Codex task."""

    title = str(request.title).strip()
    problem = str(request.problem).strip()
    objective = str(request.objective).strip()
    if not title:
        raise ValueError("Codex 接管任务标题不能为空")
    if not problem:
        raise ValueError("Codex 接管问题不能为空")
    if not objective:
        raise ValueError("Codex 接管目标不能为空")

    lines = [
        f"# {title}",
        "",
        "你收到的是 CodeYun 本机工程自动化的异常升级。工程路径已经熔断并把本问题交由你独立接管。",
        "你已获授权在整个 CodeYun 范围内自行选择调查和修复路径；下面提供的问题位置、运行权和恢复入口都只是参考方向，不限制你的行动范围。",
        "解决真实问题、完成工程化收敛并恢复工程稳定运行是唯一核心目标。最小充分干预只是调查起点；一旦证据表明根因跨越当前问题边界，应主动扩大修复面。",
        "不要只分析或给建议；持续完成调查、修复、验证、运行环境恢复和业务恢复，同时遵守项目已有的稳定业务安全门禁。",
        "",
        "## 现场问题",
        problem,
        "",
        "## 接管目标",
        objective,
    ]
    lines.extend(_render_section("建议调查方向（仅供参考）", request.suggested_focus))
    lines.extend(_render_section("第一现场证据", request.evidence))
    lines.extend(_render_section("工程已经尝试", request.attempted_actions))
    lines.extend(_render_section("约束", request.constraints))

    recovery = str(request.recovery_instructions).strip()
    if recovery:
        lines.extend(["", "## 建议恢复入口（仅供参考）", recovery])
    lines.extend(_render_section("完成判据", request.completion_criteria))
    lines.extend(
        [
            "",
            "## 必须遵守的闭环",
            "1. 先保留并读取第一现场，从最窄可证伪边界开始定位真实根因。",
            "2. 修复因果链上最早的错误层；证据要求时扩大到整个 CodeYun，并把新知识收回正式代码、契约、测试或诊断接口。",
            "3. 使用正式工程入口验证；需要重载 CodeYun 时允许重载，并在服务恢复后继续本任务。",
            "4. 不恢复已失效 Cell 的中间步骤；从稳定入口重新提交完整、幂等的新 attempt。",
            "5. 自行选择适当方式恢复工程，优先复用所提供的正式入口；验证凡修业务终态与工程运行权已经归还后才可结束。",
            "6. 如果缺少必要授权或外部条件，明确保留现场并指出唯一阻塞点，不要伪造完成。",
        ]
    )
    return "\n".join(lines).strip()


def _dispatch_root(dispatch_id: str, *, create: bool) -> Path:
    normalized = str(dispatch_id).strip()
    if not normalized or not normalized.isalnum():
        raise ValueError("Codex dispatch_id 无效")
    return codeyun_temp_root("codex-escalations", normalized, create=create)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def escalate_to_codex(
    request: str | CodexEscalationRequest,
    *,
    workspace_dir: str | Path | None = None,
    model: str | None = DEFAULT_ESCALATION_MODEL,
    reasoning_effort: str | None = DEFAULT_ESCALATION_REASONING_EFFORT,
) -> CodexDispatch:
    """Hand an incident to an independent local Codex task and return immediately.

    A structured request tells Codex to own diagnosis, repair, reload, recovery
    and final verification. Passing a string remains available for small smoke
    tests, where the string is sent unchanged.

    :param request: Structured incident handoff or a complete raw prompt.
    :param workspace_dir: Workspace Codex should operate in; defaults to CodeYun.
    :param str model: Codex model override; defaults to the escalation model.
    :param str reasoning_effort: Optional Codex reasoning effort override.
    :return CodexDispatch: Local dispatch identity, process id and diagnostic files.
    """

    prompt = (
        build_codex_escalation_prompt(request)
        if isinstance(request, CodexEscalationRequest)
        else str(request).strip()
    )
    if not prompt:
        raise ValueError("Codex 请示内容不能为空")
    if reasoning_effort and reasoning_effort.strip() not in SUPPORTED_REASONING_EFFORTS:
        raise ValueError(f"Codex reasoning_effort 无效：{reasoning_effort}")

    workspace = Path(workspace_dir or ROOT_DIR).expanduser().resolve()
    if not workspace.is_dir():
        raise NotADirectoryError(f"Codex 工作目录不存在或不是目录：{workspace}")

    dispatch_id = uuid4().hex
    dispatch_dir = _dispatch_root(dispatch_id, create=True)
    dispatch_dir.mkdir(parents=True, exist_ok=True)
    request_path = dispatch_dir / "request.json"
    prompt_path = dispatch_dir / "prompt.md"
    dispatch_path = dispatch_dir / "dispatch.json"
    stdout_path = dispatch_dir / "stdout.jsonl"
    stderr_path = dispatch_dir / "stderr.log"
    created_at = datetime.now(tz=timezone.utc).isoformat()

    request_payload = asdict(request) if isinstance(request, CodexEscalationRequest) else {"prompt": prompt}
    _write_json(request_path, request_payload)
    prompt_path.write_text(prompt, encoding="utf-8")

    command = [
        _resolve_codex_executable(),
        "exec",
        "--ignore-user-config",
        "--disable",
        "image_generation",
        "--disable",
        "plugins",
        "--skip-git-repo-check",
        "--approve-for-me",
        "--color",
        "never",
        "--json",
        "--cd",
        os.fspath(workspace),
    ]
    if model and model.strip():
        command.extend(["--model", model.strip()])
    if reasoning_effort and reasoning_effort.strip():
        command.extend(["-c", f'model_reasoning_effort="{reasoning_effort.strip()}"'])
    command.append("-")

    with (
        prompt_path.open("rb") as prompt_file,
        stdout_path.open("ab", buffering=0) as stdout_file,
        stderr_path.open("ab", buffering=0) as stderr_file,
    ):
        process = popen_service(
            command,
            cwd=os.fspath(workspace),
            stdin=prompt_file,
            stdout=stdout_file,
            stderr=stderr_file,
            close_fds=True,
        )

    dispatch = CodexDispatch(
        dispatch_id=dispatch_id,
        pid=process.pid,
        workspace_dir=os.fspath(workspace),
        request_path=os.fspath(request_path),
        prompt_path=os.fspath(prompt_path),
        stdout_path=os.fspath(stdout_path),
        stderr_path=os.fspath(stderr_path),
        created_at=created_at,
    )
    _write_json(dispatch_path, dispatch.model_dump())
    return dispatch


def inspect_codex_dispatch(dispatch_id: str) -> CodexDispatchStatus:
    """Read one dispatch's task id, progress and final message from its logs."""

    dispatch_dir = _dispatch_root(dispatch_id, create=False)
    dispatch_path = dispatch_dir / "dispatch.json"
    if not dispatch_path.is_file():
        raise KeyError(f"未找到 Codex 投递：{dispatch_id}")
    payload = json.loads(dispatch_path.read_text(encoding="utf-8"))
    stdout_path = Path(payload["stdout_path"])
    stderr_path = Path(payload["stderr_path"])
    thread_id: str | None = None
    last_message: str | None = None
    error: str | None = None
    turn_started = False
    completed = False

    if stdout_path.is_file():
        for line in stdout_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            event_type = event.get("type")
            if event_type == "thread.started":
                thread_id = str(event.get("thread_id") or "").strip() or thread_id
            elif event_type == "turn.started":
                turn_started = True
            elif event_type == "turn.completed":
                completed = True
            elif event_type in {"turn.failed", "error"}:
                raw_error = event.get("error") or event.get("message")
                if isinstance(raw_error, dict):
                    raw_error = raw_error.get("message") or raw_error.get("code")
                error = str(raw_error or event_type).strip()
            item = event.get("item")
            if event_type == "item.completed" and isinstance(item, dict):
                if item.get("type") == "agent_message":
                    last_message = str(item.get("text") or "").strip() or last_message

    stderr = stderr_path.read_text(encoding="utf-8", errors="replace").strip() if stderr_path.is_file() else ""
    if not error and stderr.lower().startswith("error:"):
        error = stderr
    if completed:
        status: Literal["starting", "running", "completed", "failed"] = "completed"
    elif error:
        status = "failed"
    elif thread_id or turn_started:
        status = "running"
    else:
        status = "starting"
    return CodexDispatchStatus(
        dispatch_id=str(payload["dispatch_id"]),
        status=status,
        pid=int(payload["pid"]),
        thread_id=thread_id,
        codex_url=f"codex://threads/{thread_id}" if thread_id else None,
        last_message=last_message,
        error=error or (stderr if status == "failed" else None),
        stdout_path=os.fspath(stdout_path),
        stderr_path=os.fspath(stderr_path),
    )
