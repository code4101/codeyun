"""Durable local Claude incidents, independent of the HTTP server and Codex.

The worker owns process identity and one explicit Claude session. A transport
result is never business acceptance: every criterion needs a receipt and every
registered engineering verifier must pass. Failed incidents resume explicitly;
there is no timer that replays model tools or silently changes provider.
"""
from __future__ import annotations

import json
import codecs
import os
import subprocess
import sys
import time
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import psutil
from filelock import FileLock

from backend.core.agents.acceptance import run_checks, validate_checks
from backend.core.agents.contracts import (
    AgentDispatch, AgentDispatchStatus, AgentTaskRequest, INCIDENT_MODES, required_acceptance_checks,
)
from backend.core.services.launcher import popen_service, resolve_python
from backend.core.settings import ROOT_DIR, get_settings
from backend.core.temp_paths import codeyun_temp_root

TOOLS = ['Bash', 'Read', 'Edit', 'Write', 'Glob', 'Grep', 'WebFetch', 'WebSearch', 'NotebookEdit', 'Skill']


def incident_root(dispatch_id: str, *, create: bool = False) -> Path:
    """Resolve only this provider's exact incident identity, never a path."""
    if len(dispatch_id) != 32 or any(c not in '0123456789abcdef' for c in dispatch_id):
        raise ValueError('Invalid agent dispatch identity')
    root = Path(get_settings().data_dir) / 'agents' / 'incidents' / dispatch_id
    legacy = codeyun_temp_root('agent-incidents', dispatch_id, create=False)
    if not create and not root.exists() and legacy.exists():
        return legacy  # Early framework probes/pre-migration receipts keep their identity.
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root


def incident_logs(dispatch_id: str) -> Path:
    """Authoritative state lives in DATA_DIR; disposable process logs stay in TEMP."""
    return codeyun_temp_root('agent-incidents', dispatch_id)


def write_record(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def read_record(dispatch_id: str) -> dict:
    """Read an engineering-owned incident, without provider private transcripts."""
    return json.loads((incident_root(dispatch_id) / 'record.json').read_text(encoding='utf-8'))


def process_alive(identity: dict) -> bool:
    """PID reuse is not ownership; match the OS process creation timestamp."""
    try:
        p = psutil.Process(int(identity['pid']))
        return abs(p.create_time() - float(identity['created'])) < .01 and p.is_running()
    except (psutil.Error, KeyError, TypeError, ValueError):
        return False


def process_identity(pid: int) -> dict:
    return {'pid': pid, 'created': psutil.Process(pid).create_time()}


def build_incident_prompt(request: AgentTaskRequest) -> str:
    if not all(str(v).strip() for v in (request.title, request.problem, request.objective)):
        raise ValueError('Incident title, problem and objective are required')
    if request.mode not in INCIDENT_MODES or not request.completion_criteria:
        raise ValueError('Incident mode and explicit completion criteria are required')
    if not 30 <= request.execution_timeout_seconds <= 86400:
        raise ValueError('Execution timeout must be 30..86400 seconds')
    validate_checks(request.acceptance_checks)
    lines = [f'# {request.title}',
        '你是本机 Claude Code，独立负责本事故。读取项目 AGENTS.md 和 agent-collaboration 技能。',
        '本任务仅使用 cc，不启动 Codex 或 DS；不得递归派生另一维修主 agent。',
        '你没有 Codex 原生 Goal 工具；工程持久任务记录负责生命周期。不要伪造 create_goal/update_goal 调用。',
        '持续完成真实问题修复和工程化收敛；代码修改与进程结束都不等于业务完成。',
        '如需看图，使用 Read 读取证据中的本地截图路径。权限拒绝或外部阻塞必须报告，禁止绕过。']
    if request.business == 'fanxiu':
        lines += ['读取凡修技能。运行权尚未移交；仅在合法空闲且没有其他 AI writer 时显式接管。',
                  '游戏操作只用正式 Kernel/Cell/Task/Runtime/ADB API，不使用 Windows GUI 或第二执行协议。',
                  '保留现场，持权完成原失败动作和正式整单幂等验收，核对 next_time 后归还工程。']
    elif request.business == 'attendance':
        lines += ['读取考勤技能。仅通过正式课程/工作簿/API/CLI调查、修复和验收。',
                  '独立考勤不得反向依赖 CodeYun；小鹅通用考勤 DP 登录态，微信用正式账号接口。',
                  '本维修请求不授权资金动作、额外群消息、已完结课程写入或提前处理未到期课程。',
                  '保护正在运行的课程和资金步骤，在安全业务边界重载；正式入口回读及幂等验证后才报告完成。']
    if request.mode in {'read_only_research', 'smoke', 'maintenance'}:
        lines += ['本任务为离线维护/只读研究/框架验收；不得附带启动业务、资金或对外发送动作。',
                  ('允许为本任务修改代码、配置和测试；通过工程入口验收，不操作游戏。'
                   if request.mode == 'maintenance' else '仅允许保存研究结论或明确指定的框架测试产物。')]
    for label, value in [('问题', request.problem), ('目标', request.objective),
                         ('建议方向', request.suggested_focus), ('证据', request.evidence),
                         ('已尝试', request.attempted_actions), ('约束', request.constraints),
                         ('恢复入口', request.recovery_instructions), ('完成判据', request.completion_criteria)]:
        lines += [f'## {label}', value if isinstance(value, str) else '\n'.join('- ' + x for x in value)]
    lines += ['## 最终回执',
        '最后只输出 JSON 对象：{"outcome":"completed|blocked|incomplete","criteria":'
        '[{"criterion":"逐字复制一条完成判据","satisfied":true,"evidence":"真实接口回读/产物路径"}],'
        '"summary":"结果与阻塞"}。每条判据恰好一项；不能把计划当作证据。']
    if request.mode == 'repair' and request.business == 'fanxiu':
        lines += ['修复回执还必须包含 job_verifications 数组，每项含 task_id、last_run_at、next_time，'
                  '逐字来自 read_scheduler_tasks() 真实回读。工程会独立核对新完成记录和运行权，不能虚构。']
    return '\n'.join(lines)


def dispatch_claude(request: AgentTaskRequest, *, workspace_dir=None) -> AgentDispatch:
    prompt = build_incident_prompt(request)
    workspace = Path(workspace_dir or ROOT_DIR).resolve(strict=True)
    if not workspace.is_dir():
        raise ValueError('Workspace must be a directory')
    identity = uuid4().hex
    root = incident_root(identity, create=True)
    logs = incident_logs(identity)
    session = str(uuid4())
    (root / 'prompt.md').write_text(prompt, encoding='utf-8')
    write_record(root / 'request.json', asdict(request))
    record = dict(dispatch_id=identity, provider='claude', resource='cc', session_id=session,
                  workspace_dir=str(workspace), mode=request.mode, request=asdict(request),
                  state='starting', created_at=datetime.now(timezone.utc).isoformat(), generation=0,
                  prompt_path=str(root / 'prompt.md'), stdout_path=str(logs / 'stdout-0.jsonl'),
                  stderr_path=str(logs / 'stderr-0.log'), acceptance='pending', worker={}, child={})
    write_record(root / 'record.json', record)
    return launch_worker(identity)


def launch_worker(dispatch_id: str) -> AgentDispatch:
    root = incident_root(dispatch_id)
    with FileLock(str(root / 'record.lock'), timeout=10):
        r = read_record(dispatch_id)
        if process_alive(r.get('worker', {})) or process_alive(r.get('child', {})):
            raise RuntimeError('Incident still has a live process owner')
        with (incident_logs(dispatch_id) / 'worker.log').open('ab', buffering=0) as output:
            p = popen_service([resolve_python(), '-m', 'backend.core.agents.background', dispatch_id],
                cwd=str(ROOT_DIR), stdin=subprocess.DEVNULL, stdout=output, stderr=output, close_fds=True)
        r['worker'] = process_identity(p.pid)
        write_record(root / 'record.json', r)
    return AgentDispatch(dispatch_id=dispatch_id, provider='claude', resource='cc', pid=p.pid,
        workspace_dir=r['workspace_dir'], request_path=str(root / 'request.json'),
        prompt_path=r['prompt_path'], stdout_path=r['stdout_path'], stderr_path=r['stderr_path'],
        created_at=r['created_at'], session_id=r['session_id'], mode=r['mode'])


def inspect_claude(dispatch_id: str) -> AgentDispatchStatus:
    r = read_record(dispatch_id)
    live = process_alive(r.get('worker', {})) or process_alive(r.get('child', {}))
    state = r['state']
    error = r.get('error')
    if state in {'starting', 'running'} and not live:
        state, error = 'failed', 'Worker exited without a terminal receipt; inspect effects before explicit resume'
    elif state in {'completed', 'failed'} and live:
        state = 'running'  # never release the writer while its process can still act
    return AgentDispatchStatus(dispatch_id=dispatch_id, status=state, provider='claude',
        pid=(r.get('worker') or {}).get('pid', 0), session_id=r['session_id'], session_url=None,
        last_message=r.get('summary'), error=error, stdout_path=r['stdout_path'], stderr_path=r['stderr_path'],
        acceptance=r.get('acceptance'), progress_diagnostics={
            'worker': r.get('worker'), 'child': r.get('child'), 'updated_at': r.get('updated_at'),
            'generation': r['generation'], 'checks': r.get('checks'), 'receipt': r.get('receipt')})


def verify_claude_acceptance(dispatch_id: str) -> AgentDispatchStatus:
    """Explicitly recheck saved evidence, without a model call or replaying tools."""
    root = incident_root(dispatch_id)
    with FileLock(str(root / 'record.lock'), timeout=10):
        r = read_record(dispatch_id)
        if process_alive(r.get('worker', {})) or process_alive(r.get('child', {})):
            raise RuntimeError('Writer still active; acceptance cannot be changed')
        if not (r.get('receipt') or {}).get('complete'):
            raise ValueError('No complete business receipt to verify')
        checks = run_checks(r['request'].get('acceptance_checks', ()), r)
        accepted = all(row['ok'] for row in checks.values())
        r.update(state='completed' if accepted else 'failed', checks=checks,
                 acceptance='accepted' if accepted else 'verifier_failed', updated_at=time.time(),
                 error=None if accepted else 'Business verification incomplete')
        write_record(root / 'record.json', r)
    return inspect_claude(dispatch_id)


def resume_claude(dispatch_id: str, prompt: str, *, mode: str | None = None) -> AgentDispatch:
    """Resume the same Claude session; ``mode`` explicitly changes the next generation's mode.

    The change is applied only after every resume gate passed (no live owner,
    confirmed original session, not accepted) and the new prompt validated.
    The previous request is archived as ``request-gen<N>.json``; request.json,
    record['request'] and record['mode'] stay consistent. Refusal has no effect.
    """
    if not str(prompt).strip():
        raise ValueError('Explicit recovery instruction is required')
    if mode is not None and mode not in INCIDENT_MODES:
        raise ValueError(f'Invalid incident mode: {mode!r}')
    root = incident_root(dispatch_id)
    # Keep the resume gate locked through launch, including its record write.
    with FileLock(str(root / 'resume.lock'), timeout=10):
        with FileLock(str(root / 'record.lock'), timeout=10):
            r = read_record(dispatch_id)
            if r['provider'] != 'claude':
                raise ValueError('Provider mismatch')
            if process_alive(r.get('worker', {})) or process_alive(r.get('child', {})):
                raise RuntimeError('Cannot resume beside a live owner')
            if not r.get('session_created'):
                raise RuntimeError('Original Claude session was not confirmed; do not silently recreate it')
            if r.get('acceptance') == 'accepted':
                raise ValueError('Accepted incident must not be replayed')
            generation = r['generation'] + 1
            request = dict(r['request'])
            previous_mode = request.get('mode', r.get('mode'))
            if mode is not None and mode != previous_mode:
                request['mode'] = mode
                request['acceptance_checks'] = list(required_acceptance_checks(
                    request.get('business', ''), mode, request.get('acceptance_checks') or ()))
            # Validate before any write so a refused change leaves no trace.
            text = build_incident_prompt(AgentTaskRequest(**request)) + '\n## 新的明确指令\n' + prompt
            logs = incident_logs(dispatch_id)
            path = root / f'prompt-{generation}.md'
            path.write_text(text, encoding='utf-8')  # unreferenced until record.json advances
            if request != r['request']:
                archive = root / f'request-gen{r["generation"]}.json'
                if not archive.exists():
                    write_record(archive, r['request'])
                write_record(root / 'request.json', request)
                r.setdefault('mode_history', []).append(
                    {'generation': generation, 'from': previous_mode, 'to': request['mode'],
                     'archived_request': archive.name, 'at': time.time()})
                r.update(request=request, mode=request['mode'])
            r.update(generation=generation, prompt_path=str(path), state='starting', acceptance='pending',
                     stdout_path=str(logs / f'stdout-{generation}.jsonl'), stderr_path=str(logs / f'stderr-{generation}.log'),
                     error=None, receipt=None, worker={}, child={}, resume=True)
            r['checks'] = {}
            write_record(root / 'record.json', r)
        return launch_worker(dispatch_id)


def recover_stalled_claude(dispatch_id: str, *, expected_generation: int, reason: str) -> AgentDispatchStatus:
    """Explicitly reap a stranded worker after its registered CLI has exited.

    Never stop a live model writer or change business acceptance. Generation
    and OS creation times guard concurrent resume and PID reuse. This only
    releases this invocation; continuing still requires explicit resume.
    """
    if not str(reason).strip():
        raise ValueError('Recovery reason is required')
    root = incident_root(dispatch_id)
    with FileLock(str(root / 'resume.lock'), timeout=10):
        with FileLock(str(root / 'record.lock'), timeout=10):
            r = read_record(dispatch_id)
            if r['generation'] != expected_generation:
                raise RuntimeError('Generation changed; inspect the current owner')
            if r.get('state') not in {'starting', 'running'}:
                raise RuntimeError('Dispatch is already terminal')
            if not r.get('child') or process_alive(r['child']):
                raise RuntimeError('CLI writer is live or its identity is unconfirmed')
            identity = r.get('worker', {})
            stopped = []
            if process_alive(identity):
                worker = psutil.Process(identity['pid'])
                owned = [process_identity(p.pid) for p in worker.children(recursive=True)]
                owned.reverse()
                owned.append(identity)
                for item in owned:
                    if not process_alive(item):
                        continue
                    process = psutil.Process(item['pid'])
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except psutil.TimeoutExpired:
                        if process_alive(item):
                            process.kill()
                            process.wait(timeout=10)
                    stopped.append(item)
            if process_alive(r.get('worker', {})) or process_alive(r['child']):
                raise RuntimeError('Invocation still active; recovery did not release ownership')
            r.update(state='failed', acceptance='transport_failed', updated_at=time.time(),
                     error='CLI exited; stranded worker explicitly recovered: ' + reason.strip(),
                     recovery={'generation': expected_generation, 'reason': reason.strip(),
                               'stopped': stopped, 'at': time.time()})
            write_record(root / 'record.json', r)
    return inspect_claude(dispatch_id)


def follow_process_log(process, path: Path, *, start_offset: int = 0):
    """Stream complete UTF-8 lines until the direct child exits, never until pipe EOF.

    MCP/tool descendants can inherit stdout and outlive the CLI. A regular
    file plus process polling preserves live events without letting those
    inherited handles hold worker ownership forever, including on timeout.
    """
    decoder = codecs.getincrementaldecoder('utf-8')('replace')
    pending = ''
    with path.open('rb') as source:
        source.seek(start_offset)
        while True:
            chunk = source.read(65536)
            if chunk:
                pending += decoder.decode(chunk)
                while '\n' in pending:
                    line, pending = pending.split('\n', 1)
                    yield line
                continue
            if process.poll() is not None:
                # The child may write its final bytes between our empty read
                # and exit; drain once more after exit is observed.
                tail = source.read()
                if tail:
                    pending += decoder.decode(tail)
                    while '\n' in pending:
                        line, pending = pending.split('\n', 1)
                        yield line
                pending += decoder.decode(b'', final=True)
                if pending:
                    yield pending
                return
            time.sleep(.1)


def parse_receipt(text: str, request: dict) -> dict:
    text = text.strip()
    if text.startswith('```'):
        text = '\n'.join(text.splitlines()[1:-1])
    receipt = json.loads(text)
    rows = receipt.get('criteria')
    criteria = request['completion_criteria']
    if (not isinstance(rows, list) or len(rows) != len(criteria)
            or sorted(str(x.get('criterion')) for x in rows) != sorted(criteria)):
        raise ValueError('Missing or duplicate completion criteria')
    if receipt.get('outcome') not in {'completed', 'blocked', 'incomplete'}:
        raise ValueError('Invalid receipt outcome')
    receipt['complete'] = (receipt['outcome'] == 'completed' and all(
        row.get('satisfied') is True and isinstance(row.get('evidence'), str) and row['evidence'].strip()
        for row in rows))
    return receipt


def run_worker(dispatch_id: str) -> None:
    from backend.core.agents.cli import bootstrap, cli_command
    from backend.core.agents.policy import load_config
    root = incident_root(dispatch_id)
    with FileLock(str(root / 'worker.lock'), timeout=0):
        def update(**changes):
            with FileLock(str(root / 'record.lock'), timeout=10):
                r = read_record(dispatch_id)
                r.update(changes, updated_at=time.time())
                write_record(root / 'record.json', r)
                return r
        # The launcher writes worker identity under record.lock before this read.
        r = update(state='running')
        config = load_config()
        bootstrap(config)
        args = ['-p', '--output-format', 'stream-json', '--verbose',
                '--resume' if r.get('resume') else '--session-id', r['session_id'],
                '--disallowedTools', 'CronCreate', 'CronDelete', 'ScheduleWakeup', 'Task',
                '--allowedTools', *TOOLS]
        command, environment = cli_command('claude', args, os.environ.copy())
        result, p, timer = None, None, None
        timed_out = threading.Event()

        def stop_child():
            if p and p.poll() is None:
                timed_out.set()
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(p.pid), '/T', '/F'], capture_output=True,
                                   creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)
                else:
                    p.terminate()
        try:
            with (Path(r['prompt_path']).open('rb') as incoming,
                  Path(r['stdout_path']).open('ab', buffering=0) as output,
                  Path(r['stderr_path']).open('ab', buffering=0) as error):
                start_offset = output.tell()
                p = popen_service(command, cwd=r['workspace_dir'], env=environment,
                    stdin=incoming, stdout=output, stderr=error, close_fds=True)
                update(child=process_identity(p.pid))
                timer = threading.Timer(r['request'].get('execution_timeout_seconds', 3600), stop_child)
                timer.daemon = True
                timer.start()
                for line in follow_process_log(p, Path(r['stdout_path']), start_offset=start_offset):
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    session = event.get('session_id')
                    if session and session != r['session_id']:
                        raise RuntimeError('Claude returned another session identity')
                    if event.get('type') == 'system' and event.get('subtype') == 'init':
                        update(session_created=True)
                    if event.get('type') == 'result':
                        result = event
                    update(last_event_type=event.get('type'))
                code = p.wait()
            if timed_out.is_set():
                raise TimeoutError('Claude execution timed out; preserve effects and explicitly resume original session')
            if code or not result or result.get('is_error') or result.get('permission_denials'):
                raise RuntimeError('Claude transport/permission failure; inspect generation logs before resume')
            try:
                receipt = parse_receipt(str(result.get('result') or ''), r['request'])
            except ValueError as exc:  # JSONDecodeError included: transport succeeded, receipt did not
                update(state='failed', acceptance='receipt_incomplete',
                       error=f'Receipt incomplete: {type(exc).__name__}: {exc}')
                return
            checks = run_checks(r['request'].get('acceptance_checks', ()), {**r, 'receipt': receipt})
            accepted = receipt['complete'] and all(x['ok'] for x in checks.values())
            update(state='completed' if accepted else 'failed', receipt=receipt, checks=checks,
                   acceptance='accepted' if accepted else ('verifier_failed' if receipt['complete'] else receipt['outcome']),
                   summary=str(receipt.get('summary', '')), error=None if accepted else 'Business acceptance incomplete')
        except Exception as exc:
            if p and p.poll() is None:
                # Only this worker's child tree; never kill a shared CLI/service.
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(p.pid), '/T', '/F'], capture_output=True,
                                   creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)
                else:
                    p.terminate()
                p.wait(timeout=15)
            update(state='failed', acceptance='transport_failed', error=f'{type(exc).__name__}: {exc}')
        finally:
            if timer:
                timer.cancel()


if __name__ == '__main__':
    run_worker(sys.argv[1])
