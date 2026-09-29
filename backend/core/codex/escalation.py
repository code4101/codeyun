from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import psutil

from backend.core.services.launcher import popen_service, resolve_python
from backend.core.settings import ROOT_DIR
from backend.core.temp_paths import codeyun_temp_root


# None delegates model selection to Codex's normal configuration layers on
# each new dispatch. Do not pin a second default beside the user's config.
DEFAULT_ESCALATION_MODEL: str | None = None
DEFAULT_ESCALATION_REASONING_EFFORT: str | None = None
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
    transport: str = 'cli'
    thread_id: str | None = None

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
    goal_status: str | None = None

    def model_dump(self) -> dict[str, Any]:
        """Return a JSON-serializable representation of the status."""

        return asdict(self)


def _windows_codex_fallbacks() -> tuple[Path, ...]:
    """Return the native Codex binaries the desktop client installs under LOCALAPPDATA."""

    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return ()
    bin_dir = Path(local_app_data) / "OpenAI" / "Codex" / "bin"
    if not bin_dir.is_dir():
        return ()
    candidates = [bin_dir / "codex.exe"]
    versioned = [path for path in bin_dir.glob("*/codex.exe") if path.is_file()]
    versioned.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    candidates.extend(versioned)
    return tuple(candidates)


def resolve_codex_executable() -> str:
    """Return a directly executable Codex CLI binary, avoiding shell shims."""

    candidates = ("codex.exe", "codex") if os.name == "nt" else ("codex",)
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved and (os.name != "nt" or Path(resolved).suffix.lower() == ".exe"):
            return resolved
    if os.name == "nt":
        for fallback in _windows_codex_fallbacks():
            if fallback.is_file():
                return str(fallback)
    raise FileNotFoundError("未找到可直接启动的 Codex CLI")


def _resolve_codex_executable() -> str:
    """Backward-compatible private alias for older callers and tests."""

    return resolve_codex_executable()


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
        "你收到的是 CodeYun 本机工程自动化的异常升级。请求已发出不代表运行权已移交；先核实当前调度状态，由你实际接管时显式取得运行权，再操作共享资源。",
        "你已获授权在整个 CodeYun 范围内自行选择调查和修复路径；下面提供的问题位置、运行权和恢复入口都只是参考方向，不限制你的行动范围。",
        "解决真实问题、完成工程化收敛并恢复工程稳定运行是唯一核心目标。最小充分干预只是调查起点；一旦证据表明根因跨越当前问题边界，应主动扩大修复面。",
        "不要只分析或给建议；持续完成调查、修复、验证、运行环境恢复和业务恢复，同时遵守项目已有的稳定业务安全门禁。",
        "",
        "## 现场问题",
        problem,
        "",
        "## 接管目标",
        objective,
        "",
        "## 原生 Goal（必须建立）",
        "用户明确要求每次独立工程异常在新会话中建立原生 Goal。首先调用 create_goal，"
        "将上述接管目标和下面全部完成判据写入 objective；不要只在回复或计划里写一个目标。",
        "同一次故障在本会话的同一个 Goal 中持续调查、修复和复验；不要另建会话或派生维修 Agent。"
        "阶段报告、单步成功、测试通过和单轮结束都不是 Goal 完成。",
        "所有完成判据满足后才调用 update_goal(status='complete')。涉及工程运行权的任务，"
        "必须先确认正式业务终态、正确写回 next_time、通过 resume_engineering_control 归还运行权，"
        "并核实 dispatcher 已恢复，再完成 Goal。",
        "遇到真正外部阻塞时遵循原生 Goal 的阻塞判定规则，留下具体证据和所需人工操作；"
        "不得伪造完成，也不得用重复空检查无限循环。若原生 Goal 工具不可用，明确报告能力缺失，"
        "不得把普通单轮执行宣称为持续 Goal。",
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
    transport: Literal['desktop', 'cli'] = 'desktop',
) -> CodexDispatch:
    """Hand an incident to an independent local Codex task and return immediately.

    A structured request tells Codex to own diagnosis, repair, reload, recovery
    and final verification. Passing a string remains available for small smoke
    tests, where the string is sent unchanged.

    :param request: Structured incident handoff or a complete raw prompt.
    :param workspace_dir: Workspace Codex should operate in; defaults to CodeYun.
    :param str model: Optional override; omitted values follow Codex configuration.
    :param str reasoning_effort: Optional override; defaults to Codex configuration.
    :param transport: Desktop by default; CLI only by explicit diagnostic request.
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

    if transport == 'desktop':
        from backend.core.codex.desktop import create_desktop_repair, DesktopDispatchUncertain
        if not isinstance(request, CodexEscalationRequest):
            raise ValueError('桌面维修必须提供结构化请求和原生 Goal 完成判据')
        prompt += (
            '\n\n## 桌面 Goal 回执（必须实际调用）\n'
            '桌面在 Goal 完成后会清空当前 Goal，工程需要保留原生工具返回值。'
            '建立 Goal 后以及完成 Goal 时，都必须在同一个 functions.exec 中捕获原生工具返回值，'
            '直接传给下面的公开 CLI，不能手写或根据文字概述构造回执。'
            '创建后把 create_goal 的原始返回值记入 r；完成时使用以下代码，'
            '仅在全部业务验收及交权已完成后执行：\n'
            '```javascript\n'
            'const r = await tools.update_goal({status: "complete"});\n'
            'const quoted = "\'" + JSON.stringify(r).replaceAll("\'", "\'\'") + "\'";\n'
            f'const saved = await tools.exec_command({{cmd: "uv run python scripts/codex_desktop.py record-goal {dispatch_id} " + quoted}});\n'
            'text(saved);\n'
            '```\n'
            '创建 Goal 时同样把 r 原样交给这条 record-goal 命令。'
            '若回执保存失败，修复回执传输；不要伪造 Goal 状态，也不要重复执行业务动作。'
        )
        prompt_path.write_text(prompt, encoding='utf-8')
        try:
            result = create_desktop_repair(prompt=prompt, title=request.title, workspace=workspace,
                                           model=model, reasoning_effort=reasoning_effort)
        except DesktopDispatchUncertain as exc:
            result = {'uncertain': True, 'error': str(exc)}
        # Persist the provider receipt even when it does not contain a ready ID.
        # A timeout/queued receipt must never cause an automatic CLI duplicate.
        _write_json(dispatch_dir / 'desktop-receipt.json', result)
        thread_id = result.get('threadId')
        dispatch = CodexDispatch(
            dispatch_id=dispatch_id, pid=0, workspace_dir=str(workspace),
            request_path=str(request_path), prompt_path=str(prompt_path),
            stdout_path=str(stdout_path), stderr_path=str(stderr_path), created_at=created_at,
            transport='desktop', thread_id=thread_id,
        )
        _write_json(dispatch_path, dispatch.model_dump())
        return dispatch
    if transport != 'cli':
        raise ValueError(f'未知 Codex transport：{transport}')

    command = [
        _resolve_codex_executable(),
        "exec",
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
    if isinstance(request, CodexEscalationRequest):
        # Each invocation is a fresh `exec`, never `resume <fixed-thread-id>`.
        # Keep the native Goal tools enabled even when local defaults differ.
        command.extend(["--enable", "goals"])
    if model and model.strip():
        command.extend(["--model", model.strip()])
    if reasoning_effort and reasoning_effort.strip():
        command.extend(["-c", f'model_reasoning_effort="{reasoning_effort.strip()}"'])
    command.append("-")

    if isinstance(request, CodexEscalationRequest):
        spec_path = dispatch_dir / 'goal-worker.json'
        _write_json(spec_path, dict(command=command, prompt_path=str(prompt_path), workspace_dir=str(workspace)))
        command = [resolve_python(), '-m', 'backend.core.codex.goal_worker', str(spec_path)]

    with (
        prompt_path.open("rb") as prompt_file,
        stdout_path.open("ab", buffering=0) as stdout_file,
        stderr_path.open("ab", buffering=0) as stderr_file,
    ):
        process = popen_service(
            command,
            cwd=os.fspath(ROOT_DIR) if isinstance(request, CodexEscalationRequest) else os.fspath(workspace),
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
    if payload.get('transport') == 'desktop':
        from backend.core.codex.desktop import read_desktop_repair
        if not payload.get('thread_id'):
            raise RuntimeError(f'桌面投递结果不明，禁止重复创建；请核对回执：{dispatch_dir}')
        # Connection/read failures propagate: unknown ownership cannot release
        # the one-agent gate and start another repair against the same game.
        receipt_path = dispatch_dir / 'desktop-goal.json'
        receipt = json.loads(receipt_path.read_text(encoding='utf-8')) if receipt_path.is_file() else None
        state = read_desktop_repair(payload['thread_id'], goal_receipt=receipt)
        return CodexDispatchStatus(
            dispatch_id=dispatch_id, pid=0, thread_id=payload['thread_id'],
            codex_url=f"codex://threads/{payload['thread_id']}",
            stdout_path=payload['stdout_path'], stderr_path=payload['stderr_path'], **state,
        )
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
    # CLI interruption/crash can leave only turn.started in stdout. Logs alone
    # cannot prove that an Agent still owns recovery after its process exits.
    if not completed and not error and not psutil.pid_exists(int(payload["pid"])):
        error = "Codex 维修进程已退出，未记录完成终态；需要接续维修"
    if completed:
        status: Literal["starting", "running", "completed", "failed"] = "completed"
    elif error:
        status = "failed"
    elif thread_id or turn_started:
        status = "running"
    else:
        status = "starting"
    goal_status = None
    request_path = payload.get('request_path')
    structured_request = (
        json.loads(Path(request_path).read_text(encoding='utf-8'))
        if request_path and Path(request_path).is_file() else {}
    )
    if completed and structured_request.get('objective'):
        # A turn-completed event can precede the next native Goal continuation.
        # Never advertise a multi-turn repair as done at that intermediate point.
        from backend.core.codex.app_server import read_codex_thread_goal
        try:
            goal = (read_codex_thread_goal(thread_id) if thread_id else {}).get('goal') or {}
            goal_status = goal.get('status')
            if goal_status != 'complete':
                if goal_status == 'active' and psutil.pid_exists(int(payload['pid'])):
                    status = 'running'
                else:
                    status = 'failed'
                    error = f'维修 Goal 尚未完成：{goal_status or "未建立"}；不能视为业务恢复'
        except Exception as exc:
            status = 'failed'
            error = f'无法核验维修 Goal：{exc}'
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
        goal_status=goal_status,
    )


def record_desktop_goal_result(dispatch_id: str, result: dict[str, Any]) -> dict[str, Any]:
    """Retain the native Goal tool's result before the desktop clears completion.

    This is an execution receipt, not a replacement Goal or a success inferred
    from assistant prose. The agent passes the captured tool result unchanged.
    Only the matching dispatch/thread can report; completed receipts are terminal.
    """
    root = _dispatch_root(dispatch_id, create=False)
    dispatch = json.loads((root / 'dispatch.json').read_text(encoding='utf-8'))
    goal = result.get('goal') or {}
    if dispatch.get('transport') != 'desktop' or goal.get('threadId') != dispatch.get('thread_id'):
        raise ValueError('Goal 回执与桌面维修会话不匹配')
    if goal.get('status') not in {'active', 'complete', 'paused', 'blocked'} or not goal.get('objective'):
        raise ValueError('必须提交原生 Goal 工具完整返回值')
    path = root / 'desktop-goal.json'
    if path.is_file():
        previous = json.loads(path.read_text(encoding='utf-8'))
        if previous.get('goal', {}).get('status') == 'complete':
            if goal.get('status') != 'complete':
                raise ValueError('已完成 Goal 回执不能退回活动状态')
            return previous
    _write_json(path, result)
    return result
