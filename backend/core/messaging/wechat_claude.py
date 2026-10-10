"""Claude Code provider for WeChat hooks; the engineering host stays provider-neutral.

The service owns reading, de-duplication, authorization, receipts and delivery.
This adapter only supplies the small client surface the host already uses
(open_thread/run/has_user_input/compress_background/current_defaults/close) on
top of the public local ``claude`` CLI. It never starts or talks to a Codex
app-server, and a thread of another provider can never be resumed here.

Transport: one ``claude -p --input-format stream-json --output-format
stream-json`` process per model turn. Within a turn a soft supplement is a
second stdin user message that the CLI injects at the next tool boundary; a
strong event is the public ``interrupt`` control request. Between turns the
conversation continues with ``--resume <session>``. An engineering-owned ledger
(admitted prompts, owning child process) lives next to the hook store so that
a lost process can be reconciled without reading Claude's private transcript.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time
import uuid

PROVIDER = 'claude'
THREAD_PREFIX = 'claude:'
AGENT_RESOURCE = 'cc'
REPLY_ACTIONS = {'reply', 'ignore', 'defer'}
# Public --allowedTools names for unattended work. Nothing bypasses CLI
# permission checks: any other tool request is denied and fails the turn.
MAIN_TOOLS = ['Bash', 'Read', 'Edit', 'Write', 'Glob', 'Grep', 'WebFetch', 'WebSearch',
              'NotebookEdit', 'Skill', 'Task']
# Engineering owns scheduling and messaging; a model must not create its own.
MAIN_DENIED_TOOLS = ['CronCreate', 'CronDelete', 'ScheduleWakeup']
# Routing, compression and format-repair turns are text-only: every built-in tool
# the CLI reports is denied (``--tools ""`` cannot cross the Windows npm shim),
# and a tool_use event in such a turn fails it as a second line of defence.
ROUTER_DENIED_TOOLS = ['Bash', 'Read', 'Edit', 'Write', 'Glob', 'Grep', 'WebFetch', 'WebSearch',
                       'NotebookEdit', 'Skill', 'Task', 'TaskStop', 'CronCreate', 'CronDelete', 'CronList',
                       'ScheduleWakeup', 'EnterWorktree', 'ExitWorktree', 'SendMessage', 'ListAgents',
                       'ReportFindings', 'Workflow']

CLAUDE_PREAMBLE = """你是 Claude Code，被工程直接指定为本群的独立主 agent；调查、修改、测试和核验都由你亲自用自己的工具完成。
不要为了接管本群再启动 Claude Code 或 Codex 作为子 agent；只有任务本身有独立并行分工价值时，才按 agent-collaboration 公共入口委派。
项目规则以 C:/home/chenkunze/slns/codeyun/AGENTS.md 和 C:/home/chenkunze/slns/skills/agent-collaboration/SKILL.md 为准，默认依据代码、测试和公共接口调查。
本会话的 provider 是 Claude；下文若提到 Codex 会话、线程或 Computer Use，仅属其他 provider 的背景，对你不适用。
本群不依赖 Codex：工程不会为本群发送或下载 GUI 附件。不要输出 CODECLAW_IMAGE / CODECLAW_FILE 行（工程会移除并附注限制）；
不要调用 acquire_message_resources(allow_gui=True)（会被拒绝），不要自行操作微信桌面。已缓存的附件可读取，普通文本回复不受影响；
需要发送或下载附件时，在回复里如实说明本群暂不支持，请所有者处理。
"""

OUTPUT_CONTRACT = """
每个回合的最后一条文本输出必须只有一个 JSON 对象，不加 markdown 围栏或其他文字：
{"action":"reply|ignore|defer","text":"精炼群回复，无需回复时为空","summary":"未结问题及处理事实摘要","question_seqs":[整数seq]}
其他自然语言说明不要放在 JSON 之外；工具调用过程中的文字不受此限。
"""

REPAIR_PROMPT = ("上一条最终输出不是合法的候选回复JSON（原因：%s）。不要重新执行任何操作，"
                 "只按以下契约重新输出最终候选，且只输出一个JSON对象：")


class SessionLostError(RuntimeError):
    """A session the ledger knows was created is gone; never recreate it and replay the business turn."""


class SessionStartConflict(RuntimeError):
    def __init__(self, resume: bool, detail: str):
        super().__init__('Claude session start conflict: ' + detail)
        self.resume = resume


RECOVERY_NOTICE = ("[工程恢复提示] 此前一次提交在崩溃或中断前可能已被执行，无法确认。"
                   "先通过正式接口核验此前已产生的操作和效果，不得盲目重复；核验后再处理下面的内容。\n\n")


class ProviderMismatchError(RuntimeError):
    """A thread belongs to another provider; it must be handed over, never resumed."""


def new_thread_id() -> str:
    return THREAD_PREFIX + str(uuid.uuid4())


def is_claude_thread(thread_id: str | None) -> bool:
    return bool(thread_id) and re.fullmatch(THREAD_PREFIX + r'[0-9a-f-]{36}', thread_id) is not None


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def default_state_dir() -> Path:
    from backend.core.messaging.wechat_agent import agent_root
    return agent_root() / 'claude-sessions'


def default_instructions(*, private: bool = False) -> str:
    from backend.core.codex.wechat_agent import AGENT_INSTRUCTIONS, OWNER_INSTRUCTIONS  # prompt text only
    return CLAUDE_PREAMBLE + '\n' + (OWNER_INSTRUCTIONS if private else AGENT_INSTRUCTIONS)


def parse_candidate(text: str) -> dict:
    """Validate the final JSON candidate with the same limits as the Codex path."""
    body = (text or '').strip()
    fenced = re.fullmatch(r'```(?:json)?\s*(.*?)\s*```', body, flags=re.S)
    if fenced:
        body = fenced.group(1)
    try:
        candidate = json.loads(body)
    except ValueError:
        start, end = body.find('{'), body.rfind('}')
        if start < 0 or end <= start:
            raise ValueError('Final output is not a JSON object') from None
        try:
            candidate = json.loads(body[start:end + 1])
        except ValueError:
            raise ValueError('Final output is not a JSON object') from None
    if not isinstance(candidate, dict) or set(candidate) - {'action', 'text', 'summary', 'question_seqs'}:
        raise ValueError('Candidate must contain only action/text/summary/question_seqs')
    if candidate.get('action') not in REPLY_ACTIONS:
        raise ValueError('Invalid candidate action')
    if not isinstance(candidate.get('text'), str) or not isinstance(candidate.get('summary'), str):
        raise ValueError('Candidate text and summary must be strings')
    seqs = candidate.get('question_seqs')
    if not isinstance(seqs, list) or any(type(seq) is not int for seq in seqs):
        raise ValueError('Candidate question_seqs must be integers')
    if candidate['action'] == 'reply' and not 0 < len(candidate['text'].strip()) <= 10000:
        raise ValueError('Invalid reply length')
    return candidate


class SessionLedger:
    """Engineering-owned session facts: creation, admitted prompts and live child."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.lock = threading.RLock()

    def path(self, thread_id: str) -> Path:
        return self.directory / (thread_id[len(THREAD_PREFIX):] + '.json')

    def system_path(self, thread_id: str) -> Path:
        return self.directory / (thread_id[len(THREAD_PREFIX):] + '.system.txt')

    def load(self, thread_id: str) -> dict | None:
        with self.lock:
            try:
                return json.loads(self.path(thread_id).read_text(encoding='utf-8'))
            except FileNotFoundError:
                return None

    def save(self, thread_id: str, entry: dict) -> dict:
        with self.lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            temp = self.path(thread_id).with_suffix('.tmp')
            temp.write_text(json.dumps(entry, ensure_ascii=False, indent=1), encoding='utf-8')
            temp.replace(self.path(thread_id))
            return entry

    def update(self, thread_id: str, **values) -> dict:
        with self.lock:
            entry = self.load(thread_id) or {}
            entry.update(values)
            return self.save(thread_id, entry)

    def create(self, thread_id: str, *, title: str, kind: str, instructions: str) -> dict:
        with self.lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            self.system_path(thread_id).write_text(instructions, encoding='utf-8')
            return self.save(thread_id, {'thread_id': thread_id, 'title': title, 'kind': kind,
                                         'created': False, 'turns': 0, 'admitted': [], 'child': None,
                                         'created_at': time.time()})

    def admit(self, thread_id: str, prompt: str) -> None:
        with self.lock:
            entry = self.load(thread_id) or {}
            entry['admitted'] = [*entry.get('admitted', []), _digest(prompt)][-200:]
            entry['created'] = True
            self.save(thread_id, entry)

    def intent(self, thread_id: str, text: str | None) -> None:
        """Record a write to the child before it happens; an unconfirmed one is 'unknown', not 'absent'."""
        self.update(thread_id, intent=None if text is None else {'digest': _digest(text), 'at': time.time()})

    def admitted(self, thread_id: str, prompt: str) -> bool:
        entry = self.load(thread_id) or {}
        return _digest(prompt) in entry.get('admitted', [])


def kill_process_tree(pid: int) -> None:
    import psutil
    try:
        root = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    for process in [*root.children(recursive=True), root]:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            pass


class ClaudeWechatClient:
    provider = PROVIDER
    supports_tool_disable = True

    def __init__(self, *, state_dir: Path | None = None, popen=None, resource: str = AGENT_RESOURCE,
                 cwd: Path | None = None, reaper=None):
        self.model = None  # Reported by the CLI; never pinned or overridden here.
        self.resource = resource
        self._state_dir = state_dir
        self._ledger = None
        self._popen = popen or self._spawn
        self._reaper = reaper or self._reap
        self.cwd = cwd
        self.activity_turn_ids = {}
        self.current = None  # (process, thread_id) of the live turn, for close().

    @property
    def ledger(self) -> SessionLedger:
        if self._ledger is None:
            self._ledger = SessionLedger(self._state_dir or default_state_dir())
        return self._ledger

    def start(self):
        return None

    def rpc(self, method: str, params: dict, timeout=45) -> dict:
        raise RuntimeError(f'Claude provider has no app-server RPC: {method}')

    # -- threads -----------------------------------------------------------
    def open_thread(self, thread_id: str | None, title: str, *, private=False, instructions: str | None = None,
                    text_only: bool = False, recover_output_schema: bool = False) -> str:
        if thread_id:
            if not is_claude_thread(thread_id):
                raise ProviderMismatchError(
                    f'Thread {thread_id!r} belongs to another provider; use the provider handover interface')
            if self.ledger.load(thread_id) is None:
                self._create(thread_id, title, private, instructions, text_only)
            return thread_id
        thread_id = new_thread_id()
        self._create(thread_id, title, private, instructions, text_only)
        return thread_id

    def _create(self, thread_id, title, private, instructions, text_only):
        text = (instructions or default_instructions(private=private)) + ('' if private else OUTPUT_CONTRACT)
        kind = 'router' if text_only else 'owner' if private else 'main'
        self.ledger.create(thread_id, title=title, kind=kind, instructions=text)

    def session_lost(self, thread_id: str) -> bool:
        return bool((self.ledger.load(thread_id) or {}).get('lost'))

    def current_defaults(self) -> dict:
        return {'model': self.model or 'claude (CLI default)', 'effort': None}

    def has_user_input(self, thread_id: str, prompt: str) -> bool:
        """Only an echo of the user item observed by the host proves admission."""
        return self.ledger.admitted(thread_id, prompt)

    def compress_background(self, thread_id: str, previous_summary: str, stop_event, *, extra_context: str = "") -> str:
        prompt = json.dumps({"task": "仅压缩本会话，供下一次会话接续背景。禁止调用工具、执行业务或回复微信。",
            "instructions": "结合已有完整上下文及此前交接，保留当前话题、明确约定、已核验操作与结果、"
                "未完成事项及阻碍、必要的对象ID和文件/会话索引。区分已完成与待办，勿把历史请求当作新授权，"
                "删去闲聊和过时细节。summary目标3000字以内，最多6000字；action=ignore、text为空、question_seqs为空。",
            "previous_handoff": previous_summary, "extra_context": extra_context}, ensure_ascii=False)
        result = self.run(thread_id, prompt, lambda: False, stop_event, lambda _: None, tools_disabled=True)
        summary = result.get('summary')
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 6000:
            raise ValueError('Invalid session handoff; old conversation retained')
        return summary.strip()

    # -- process plumbing --------------------------------------------------
    def _args(self, entry: dict, *, no_tools: bool = False, resume: bool | None = None) -> list[str]:
        thread_id = entry['thread_id']
        session = thread_id[len(THREAD_PREFIX):]
        args = ['-p', '--input-format', 'stream-json', '--output-format', 'stream-json',
                '--verbose', '--replay-user-messages']
        resume = bool(entry.get('created')) if resume is None else resume
        args += ['--resume' if resume else '--session-id', session]
        # The CLI rebuilds its system prompt from flags on every start, so the
        # business rules are re-attached on resume instead of assumed persisted.
        args += ['--append-system-prompt-file', str(self.ledger.system_path(thread_id).resolve())]
        # Variadic options go last; instructions and prompts travel via files/stdin
        # because the Windows npm shim cannot pass quotes or empty arguments intact.
        if entry.get('kind') == 'router' or no_tools:
            args += ['--disallowedTools', *ROUTER_DENIED_TOOLS]
        else:
            args += ['--disallowedTools', *MAIN_DENIED_TOOLS, '--allowedTools', *MAIN_TOOLS]
        return args

    def _spawn(self, args: list[str]):
        from backend.core.agents.cli import bootstrap, cli_command
        from backend.core.agents.policy import load_config, resolve
        from backend.core.services.launcher import popen_service
        from backend.core.settings import ROOT_DIR
        config = load_config()
        plan = resolve(config, pair=self.resource)
        if plan['main'] != AGENT_RESOURCE:
            raise ValueError('Claude WeChat provider requires the cc resource')
        bootstrap(config)  # public environment loader; credentials stay in the environment
        from backend.core.messaging.wechat_capabilities import PROVIDER_ENV
        command, env = cli_command(config['resources'][AGENT_RESOURCE]['driver'], args,
                                   {**os.environ, PROVIDER_ENV: PROVIDER})
        return popen_service(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, encoding='utf-8', errors='replace', bufsize=1,
                             cwd=str(self.cwd or ROOT_DIR), env=env, close_fds=True)

    def _reap(self, entry: dict) -> None:
        """A crashed host can leave its child running; never resume beside it."""
        child = entry.get('child') or {}
        if not child.get('pid'):
            return
        import psutil
        try:
            process = psutil.Process(child['pid'])
            if abs(process.create_time() - child.get('create_time', 0)) < 2 and \
                    entry['thread_id'][len(THREAD_PREFIX):] in ' '.join(process.cmdline() + [str(process.environ())]):
                kill_process_tree(child['pid'])
        except (psutil.Error, OSError):
            pass
        self.ledger.update(entry['thread_id'], child=None)

    @staticmethod
    def _user_message(text: str) -> str:
        return json.dumps({'type': 'user', 'message': {'role': 'user',
                           'content': [{'type': 'text', 'text': text}]}}, ensure_ascii=False) + '\n'

    @staticmethod
    def _text_of(event: dict) -> str:
        content = (event.get('message') or {}).get('content')
        if isinstance(content, str):
            return content
        return ''.join(block.get('text', '') for block in content or []
                       if isinstance(block, dict) and block.get('type') == 'text')

    @staticmethod
    def _is_tool_result(event: dict) -> bool:
        content = (event.get('message') or {}).get('content')
        return isinstance(content, list) and any(
            isinstance(block, dict) and block.get('type') == 'tool_result' for block in content)

    # -- one model turn ----------------------------------------------------
    def _turn(self, entry, prompt, hard_interrupt, stop_event, on_start, timeout, soft_updates, on_activity,
              no_tools=False) -> dict:
        """Run one turn; a session whose creation was attempted but never admitted is reconciled once."""
        resume = None
        for attempt in range(2):
            try:
                return self._turn_once(entry, prompt, hard_interrupt, stop_event, on_start, timeout, soft_updates,
                                       on_activity, no_tools, resume)
            except SessionLostError as lost:
                self.ledger.update(entry['thread_id'], lost={'at': time.time(), 'detail': str(lost)[:500]})
                raise
            except SessionStartConflict as conflict:
                if attempt:
                    raise RuntimeError(str(conflict)) from None
                resume = conflict.resume
        raise AssertionError('unreachable')

    def _turn_once(self, entry, prompt, hard_interrupt, stop_event, on_start, timeout, soft_updates, on_activity,
                   no_tools, resume) -> dict:
        thread_id = entry['thread_id']
        self._reaper(entry)
        entry = self.ledger.load(thread_id)
        process = self._popen(self._args(entry, no_tools=no_tools, resume=resume))
        self.current = (process, thread_id)
        try:
            self.ledger.update(thread_id, child={'pid': process.pid, 'started': time.time(),
                                                 'create_time': _create_time(process.pid)})
        except OSError:
            pass
        events, stderr_tail = queue.Queue(), []

        def read_out():
            try:
                for line in process.stdout:
                    try:
                        events.put(json.loads(line))
                    except ValueError:
                        continue
            finally:
                events.put(None)

        def read_err():
            for line in process.stderr:
                stderr_tail.append(line.rstrip())
                del stderr_tail[:-30]

        threading.Thread(target=read_out, daemon=True, name='wechat-claude-out').start()
        threading.Thread(target=read_err, daemon=True, name='wechat-claude-err').start()

        turn_id = f"{thread_id[len(THREAD_PREFIX):]}:{entry.get('turns', 0) + 1}"
        state = {'admitted': False, 'interrupted': False, 'status': 'inProgress'}
        steers = {}  # outstanding stdin supplements by exact text
        last_report = [0.0]

        def report(event, status=None, force=False):
            status = status or state['status']
            if not on_activity or not (force or time.monotonic() - last_report[0] >= 15):
                return
            last_report[0] = time.monotonic()
            on_activity({'thread_id': thread_id, 'turn_id': turn_id, 'updated_at': time.time(),
                         'event': event, 'state': status, 'item': {}})

        wire = RECOVERY_NOTICE + prompt if entry.get('intent') else prompt  # unknown earlier write: verify first

        def write(text):
            self.ledger.intent(thread_id, text)  # persisted before the child can see it
            process.stdin.write(self._user_message(text))
            process.stdin.flush()

        failure = None
        try:
            write(wire)
            deadline = time.monotonic() + timeout
            interrupt_deadline = None
            result_seen_at = None
            while True:
                if (stop_event.is_set() or hard_interrupt()) and not state['interrupted']:
                    state['interrupted'] = True
                    interrupt_deadline = time.monotonic() + 20
                    try:
                        process.stdin.write(json.dumps({'type': 'control_request', 'request_id': uuid.uuid4().hex,
                                                        'request': {'subtype': 'interrupt'}}) + '\n')
                        process.stdin.flush()
                    except OSError:
                        pass
                now = time.monotonic()
                if interrupt_deadline and now > interrupt_deadline:
                    return self._finish_interrupted(state, report)
                if now > deadline:
                    raise TimeoutError('Claude WeChat turn inactive beyond watchdog deadline')
                if result_seen_at and now - result_seen_at > 20:
                    return self._finish(state, report, final)  # steer never admitted; host re-submits it
                try:
                    event = events.get(timeout=0.2)
                except queue.Empty:
                    continue
                if event is None:
                    if state['interrupted']:
                        return self._finish_interrupted(state, report)
                    tail = ' | '.join(stderr_tail[-3:])
                    if not state['admitted'] and resume is None:
                        # A crash can leave a session the ledger never confirmed (or none at all).
                        if 'already in use' in tail and not entry.get('created'):
                            raise SessionStartConflict(True, tail)
                        if 'No conversation found' in tail and entry.get('created'):
                            raise SessionLostError('Claude session no longer exists: ' + tail)
                    raise RuntimeError('Claude process exited: ' + tail)
                deadline = time.monotonic() + timeout
                kind = event.get('type')
                if kind == 'system' and event.get('subtype') == 'init':
                    self.model = event.get('model') or self.model
                    if event.get('session_id') and event['session_id'] != thread_id[len(THREAD_PREFIX):]:
                        raise RuntimeError('Claude session identity changed unexpectedly')
                elif kind == 'user' and event.get('isReplay'):
                    text = self._text_of(event)
                    if not state['admitted'] and text == wire:
                        state['admitted'] = True
                        self.ledger.admit(thread_id, prompt)
                        self.ledger.intent(thread_id, None)
                        self.ledger.update(thread_id, turns=entry.get('turns', 0) + 1)
                        self.activity_turn_ids[thread_id] = turn_id
                        report('turn/started', force=True)
                        on_start(turn_id)
                    elif text in steers:
                        steers.pop(text)
                        result_seen_at = None  # the supplement runs as the next turn; wait for its result
                        self.ledger.admit(thread_id, text)
                        self.ledger.intent(thread_id, None)
                        if soft_updates:
                            soft_updates(True)
                elif kind == 'user' and self._is_tool_result(event):
                    report('item/completed', force=True)
                    if (state['admitted'] and soft_updates and not steers and not state['interrupted']
                            and not no_tools):
                        pending = soft_updates(False)
                        if pending:
                            text = pending if isinstance(pending, str) else json.dumps(
                                {'soft_new_messages': pending}, ensure_ascii=False)
                            steers[text] = True
                            write(text)
                elif kind == 'assistant':
                    if no_tools and any(isinstance(block, dict) and block.get('type') == 'tool_use'
                                        for block in (event.get('message') or {}).get('content') or []):
                        raise RuntimeError('Claude requested a tool in a text-only turn')
                    report('item/started')
                elif kind == 'result':
                    final = event
                    if state['interrupted']:
                        return self._finish_interrupted(state, report)
                    if not steers and soft_updates and state['admitted'] and not no_tools:
                        # A text-only turn has no tool boundary; offer arrivals before closing it.
                        pending = soft_updates(False)
                        if pending:
                            text = pending if isinstance(pending, str) else json.dumps(
                                {'soft_new_messages': pending}, ensure_ascii=False)
                            steers[text] = True
                            write(text)
                    if steers:
                        result_seen_at = result_seen_at or time.monotonic()
                        continue  # a supplement is queued as the next turn
                    return self._finish(state, report, final)
        except BaseException as exc:
            failure = exc
            raise
        finally:
            if failure is not None:
                state['status'] = 'interrupted' if state['interrupted'] else 'failed'
                report('turn/completed', force=True)
            self._close_process(process, thread_id)

    def _finish(self, state, report, final: dict) -> dict:
        denials = final.get('permission_denials')
        if denials:
            names = sorted({str(d.get('tool_name', '?')) for d in denials if isinstance(d, dict)})
            state['status'] = 'failed'
            report('turn/completed', force=True)
            raise RuntimeError('Claude permission denied: ' + ', '.join(names))
        if final.get('is_error') or final.get('subtype') != 'success':
            state['status'] = 'failed'
            report('turn/completed', force=True)
            raise RuntimeError(f"Claude turn failed: {final.get('subtype')} {str(final.get('result', ''))[:300]}")
        state['status'] = 'completed'
        report('turn/completed', force=True)
        return {'text': str(final.get('result') or '')}

    def _finish_interrupted(self, state, report) -> dict:
        state['status'] = 'interrupted'
        report('turn/completed', force=True)
        return {'interrupted': True}

    def _close_process(self, process, thread_id):
        try:
            process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            kill_process_tree(process.pid)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        self.current = None
        try:
            self.ledger.update(thread_id, child=None)
        except OSError:
            pass

    # -- host surface ------------------------------------------------------
    def run(self, thread_id: str, prompt: str, hard_interrupt, stop_event, on_start, *, timeout=900,
            soft_updates=None, raw_text=False, model_profile: dict | None = None, on_activity=None,
            tools_disabled: bool = False) -> dict:
        if model_profile:
            raise ValueError('Per-turn model profiles are not supported by the Claude provider')
        if not is_claude_thread(thread_id):
            raise ProviderMismatchError(f'Thread {thread_id!r} belongs to another provider')
        entry = self.ledger.load(thread_id)
        if entry is None:
            raise RuntimeError('Unknown Claude session; open_thread must create it first')
        result = self._turn(entry, prompt, hard_interrupt, stop_event, on_start, timeout, soft_updates, on_activity,
                            no_tools=tools_disabled)
        if result.get('interrupted'):
            return result
        if raw_text:
            text = result['text']
            return {'action': 'reply' if text.strip() else 'ignore', 'text': text,
                    'summary': text[-2000:], 'question_seqs': []}
        try:
            return parse_candidate(result['text'])
        except ValueError as exc:
            # The turn already ran; ask for the output format only, once, in the same session.
            repair = self._turn(self.ledger.load(thread_id), REPAIR_PROMPT % str(exc) + OUTPUT_CONTRACT,
                                hard_interrupt, stop_event, lambda _: None, timeout, None, on_activity,
                                no_tools=True)
            if repair.get('interrupted'):
                return repair
            return parse_candidate(repair['text'])

    def close(self):
        current = self.current
        if current and current[0].poll() is None:
            kill_process_tree(current[0].pid)


def _create_time(pid: int) -> float:
    import psutil
    try:
        return psutil.Process(pid).create_time()
    except (psutil.Error, OSError):
        return 0.0
