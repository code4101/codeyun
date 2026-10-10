"""Opt-in, original-thread recovery episodes; never mutate Goals or fork work.

The diagnostic patrol remains read-only. Engineering can call this explicit
entry with retained owner authorization; dry-run is the default. A per-thread
durable lock serializes CodeYun admission, and native thread/resume acquires
the real writer before a second exact failure/Goal check and turn/start.
Production enablement and native transport acceptance are separate evidence.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
import uuid
from pathlib import Path

from filelock import FileLock

from backend.core.codex.research_supervision import read_codex_research_snapshot
from backend.core.temp_paths import codeyun_temp_root
from backend.core.codex.turn_errors import CodexTurnFailed


class ResearchRecoveryAdmissionRejected(RuntimeError):
    """No model turn was submitted; exact native admission failed closed."""


class ResearchRecoveryInterrupted(RuntimeError):
    """The admitted native recovery turn returned an interruption receipt."""


def configure_codex_research_recovery(thread_id: str, *, owner_authorization: str,
        prompt: str, enabled: bool = False, dry_run: bool = True, store=None) -> dict:
    """Explicit engineering opt-in; neither resumes work nor changes a Goal.

    Existing patrol registrations remain read-only without this separate policy.
    Enabling actual recovery requires retaining the owner's actual instruction.
    Configure does not register a thread, create an agent or start a service.
    """
    if (not isinstance(thread_id, str) or not thread_id.strip() or thread_id != thread_id.strip()
        or not isinstance(owner_authorization, str) or not owner_authorization.strip()
        or not isinstance(prompt, str) or not prompt.strip()
        or type(enabled) is not bool or type(dry_run) is not bool):
        raise ValueError('Exact original thread, owner authorization and explicit policy required')
    from backend.core.messaging.codex_results import result_store
    from backend.core.codex.research_supervision import _registry_lock
    store = store or result_store()
    with _registry_lock(store):
        policies = store.get_meta('codex_research_recovery_policies') or {}
        if not isinstance(policies, dict):
            raise ValueError('Invalid recovery policy ledger')
        policy = {'thread_id': thread_id, 'owner_authorization': owner_authorization,
            'prompt': prompt, 'enabled': enabled, 'dry_run': dry_run}
        policies[thread_id] = policy
        store.set_meta('codex_research_recovery_policies', policies)
    return policy


def read_codex_research_recovery(thread_id: str, *, state_dir: Path | None = None) -> dict:
    """Read bounded persistent episode evidence, without observing or recovering.

    Missing evidence is explicit; malformed/foreign evidence is a failure. This
    query creates no directory, lock, thread, process, notification or Goal.
    """
    if not isinstance(thread_id, str) or not thread_id.strip() or thread_id != thread_id.strip():
        raise ValueError('Exact original thread ID required')
    directory = Path(state_dir) if state_dir is not None else codeyun_temp_root(
        'codex-research-recovery', create=False)
    path = directory / (hashlib.sha256(thread_id.encode()).hexdigest() + '.json')
    ledger = _read_ledger(path, thread_id)
    return {'found': path.is_file(), 'evidence_path': str(path), **ledger}


def research_goal_identity(goal: dict) -> str:
    """Exact immutable native Goal identity; missing identity is not reusable."""
    if (not isinstance(goal, dict) or not isinstance(goal.get('threadId'), str)
        or not goal['threadId'].strip() or not isinstance(goal.get('objective'), str)
        or not goal['objective'].strip() or goal.get('createdAt') is None):
        raise ResearchRecoveryAdmissionRejected('native Goal identity missing')
    return hashlib.sha256(json.dumps(
        [goal['threadId'], goal['createdAt'], goal['objective']],
        ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def run_codex_research_recovery_turn(thread_id: str, *, failed_turn_id: str,
        goal_identity: str, prompt: str, on_start, on_tool, timeout_seconds: float = 300,
        stop_event=None) -> None:
    """Provider-owned atomic writer admission, followed by one original turn.

    No open_thread fallback, fork, create or Goal mutation is used. Native
    thread/resume rejects a competing writer. Once held, native history and
    Goal are re-read through that SAME client before submitting the prompt.
    Exceptions after submission are uncertain until on_start/native evidence
    identifies the admitted turn; callers must never blindly replay them.
    """
    from backend.core.codex.wechat_agent import CodexWechatClient
    client = CodexWechatClient()
    try:
        client.start()
        try:
            receipt = client.rpc('thread/resume', {'threadId': thread_id})
            if (receipt.get('thread') or {}).get('id') != thread_id:
                raise ValueError('native writer identity mismatch')
        except Exception as exc:
            raise ResearchRecoveryAdmissionRejected(str(exc)) from exc

        def before_start():
            try:
                if stop_event is not None and stop_event.is_set():
                    raise ValueError('recovery stop requested before native turn admission')
                history = client.rpc('thread/turns/list', {'threadId': thread_id, 'limit': 1,
                    'sortDirection': 'desc', 'itemsView': 'notLoaded'})
                rows = history.get('data')
                if (not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict)
                        or rows[0].get('id') != failed_turn_id or rows[0].get('status') != 'failed'):
                    raise ValueError('latest failed turn changed after writer acquisition')
                goal = client.rpc('thread/goal/get', {'threadId': thread_id}).get('goal')
                if (not isinstance(goal, dict) or goal.get('threadId') != thread_id
                        or goal.get('status') != 'active' or research_goal_identity(goal) != goal_identity):
                    raise ValueError('native active Goal changed after writer acquisition')
            except Exception as exc:
                raise ResearchRecoveryAdmissionRejected(str(exc)) from exc

        def activity(event):
            item = event.get('item') or {}
            if (item.get('type') in {'commandExecution', 'fileChange', 'mcpToolCall', 'webSearch'}
                    and item.get('status') == 'completed' and item.get('id')):
                on_tool(event.get('turn_id'), item['id'])

        # From here even a lost turn/start acknowledgement must be reconciled,
        # not classified as a clean pre-admission rejection.
        result = client.run(thread_id, prompt, lambda: False, stop_event or threading.Event(), on_start,
            timeout=timeout_seconds, wall_timeout=timeout_seconds, raw_text=True,
            on_activity=activity, before_start=before_start)
        if result.get('interrupted'):
            raise ResearchRecoveryInterrupted('recovery turn interrupted; not a success receipt')
    finally:
        # This client owns only the admitted recovery writer, never a desktop
        # writer or a different thread. No interrupt/resume of other work.
        client.close()


def _write_document(path: Path, document: dict) -> None:
    temporary = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(document, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_ledger(path: Path, thread_id: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {'schema_version': 1, 'thread_id': thread_id, 'episode': None, 'closed': []}
    if (not isinstance(value, dict) or value.get('schema_version') != 1
        or value.get('thread_id') != thread_id or not isinstance(value.get('closed'), list)
        or len(value['closed']) > 32 or any(not isinstance(row, dict) for row in value['closed'])
        or not (value.get('episode') is None or isinstance(value['episode'], dict))):
        raise ValueError('Invalid research recovery ledger; preserve evidence')
    episode = value.get('episode')
    if episode and (not isinstance(episode.get('attempts'), list)
        or len(episode['attempts']) > 2 or not episode.get('id')
        or not episode.get('origin_failed_turn_id') or not episode.get('goal_identity')
        or episode.get('status') not in {'ready', 'attempting', 'awaiting_outcome', 'manual_review'}):
        raise ValueError('Invalid research recovery episode; preserve evidence')
    if episode:
        for attempt in episode['attempts']:
            if (not isinstance(attempt, dict) or not attempt.get('id') or not attempt.get('failed_turn_id')
                or attempt.get('status') not in {'claimed', 'admitted', 'outcome_unknown', 'completed', 'failed'}
                or not (attempt.get('turn_id') is None or isinstance(attempt['turn_id'], str))
                or type(attempt.get('next_attempt_at')) not in {int, float}
                or not math.isfinite(attempt['next_attempt_at'])):
                raise ValueError('Invalid research recovery attempt; preserve evidence')
            if attempt['status'] == 'failed' and (
                    not attempt.get('turn_id') or attempt.get('native_terminal_status') != 'failed'
                    or not isinstance(attempt.get('native_error'), dict)):
                raise ValueError('Failed recovery attempt lacks exact native terminal evidence')
    return value


def _latest(snapshot: dict, thread_id: str) -> tuple[dict, dict]:
    if snapshot.get('thread_id') != thread_id or snapshot.get('source_errors'):
        raise ResearchRecoveryAdmissionRejected('snapshot identity/read failure')
    if (snapshot.get('authority') or {}).get('same_turn_lifecycle_contrast'):
        raise ResearchRecoveryAdmissionRejected(
            'live writer inProgress versus independent interrupted; terminal unconfirmed, writer retained')
    desktop, history, source = snapshot['desktop'], snapshot['turn_history'], snapshot['goal']
    turn = desktop.get('latest_turn')
    rows = history.get('turns')
    goal = source.get('value')
    if (not desktop.get('available') or not history.get('available') or not source.get('available')
        or not isinstance(turn, dict) or not isinstance(rows, list) or not rows
        or rows[0].get('id') != turn.get('id') or rows[0].get('status') != turn.get('status')):
        raise ResearchRecoveryAdmissionRejected('latest live/native turn unconfirmed')
    if not isinstance(goal, dict) or goal.get('threadId') != thread_id:
        raise ResearchRecoveryAdmissionRejected('native Goal identity/read failure')
    research_goal_identity(goal)
    return turn, goal


def recover_codex_research_once(thread_id: str, *, owner_authorization: str, prompt: str,
        state_dir: Path | None = None, dry_run: bool = True,
        observation_timeout_seconds: float = 10, stop_event=None) -> dict:
    """One bounded recovery decision and, when explicitly enabled, one turn.

    Retained authorization is an actual owner instruction, not an inferred
    permission from failed history. Two attempts belong to the origin episode,
    not to each new failed turn. New confirmed tool work or a successful turn
    closes the old episode. Unknown acknowledgement is a durable manual review,
    never another automatic send. A process crash while attempting leaves an
    ambiguous claim that also cannot be replayed without reconciliation.
    dry_run may reconcile/persist diagnostics but never starts transport work.
    No registry configuration or production enablement is implied by this API.
    """
    if not isinstance(thread_id, str) or not thread_id.strip() or thread_id != thread_id.strip():
        raise ValueError('Exact original thread ID required')
    if (not isinstance(owner_authorization, str) or not owner_authorization.strip()
        or not isinstance(prompt, str) or not prompt.strip() or type(dry_run) is not bool):
        raise ValueError('Explicit owner authorization, prompt and boolean dry_run required')
    directory = Path(state_dir) if state_dir is not None else codeyun_temp_root('codex-research-recovery')
    directory.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(thread_id.encode()).hexdigest()
    path = directory / (key + '.json')
    with FileLock(str(directory / (key + '.lock')), timeout=1):
        ledger = _read_ledger(path, thread_id)
        document_lock = threading.RLock()
        performed = False

        def persist():
            with document_lock:
                _write_document(path, ledger)

        snapshot = read_codex_research_snapshot(thread_id, timeout_seconds=observation_timeout_seconds)
        ledger['last_observation'] = snapshot
        now = time.time()

        def finish(status, **extra):
            ledger['updated_at'] = time.time()
            ledger['last_decision'] = {'status': status, **extra}
            persist()
            return {'thread_id': thread_id, 'status': status, 'dry_run': dry_run,
                'evidence_path': str(path), 'episode': ledger.get('episode'),
                'recovery_performed': performed, **extra}

        def manual(reason):
            episode = ledger['episode']
            episode['status'] = 'manual_review'
            episode.setdefault('manual_intervention', {'id': episode['id'] + ':manual',
                'reason': reason, 'created_at': now})
            return finish('manual_review', reason=reason)

        if stop_event is not None and stop_event.is_set():
            return finish('stopped', reason='stop requested before recovery admission')
        try:
            turn, goal = _latest(snapshot, thread_id)
        except ResearchRecoveryAdmissionRejected as exc:
            return finish('rejected', reason=str(exc))
        identity = research_goal_identity(goal)
        episode = ledger.get('episode')
        tools = snapshot['desktop'].get('completed_tool_ids', [])
        progress = bool(episode and turn['id'] != episode['origin_failed_turn_id'] and
            (turn['status'] == 'completed' or tools))
        if progress and episode['goal_identity'] == identity:
            ledger['closed'] = (ledger['closed'] + [{**episode, 'status': 'resolved',
                'resolved_by_turn_id': turn['id'], 'resolved_tool_ids': tools, 'resolved_at': now}])[-32:]
            ledger['episode'] = episode = None
        if turn['status'] != 'failed':
            return finish('no_latest_failure', reason='historical failures cannot resume a newer turn')
        if any(closed.get('origin_failed_turn_id') == turn['id'] for closed in ledger['closed']):
            return finish('no_latest_failure', reason='resolved failure episode cannot be replayed')
        if goal.get('status') != 'active':
            return finish('rejected', reason='native Goal must be active; lawful Goal recovery required')
        if snapshot['desktop'].get('writer') != 'inactive_observed':
            return finish('rejected', reason='writer is active or unknown')
        if episode and episode['goal_identity'] != identity:
            return finish('rejected', reason='original Goal identity changed; no implicit fresh episode')
        if not episode:
            episode_id = hashlib.sha256(json.dumps([thread_id, turn['id'], identity]).encode()).hexdigest()
            episode = {'id': episode_id, 'origin_failed_turn_id': turn['id'], 'goal_identity': identity,
                'attempts': [], 'status': 'ready', 'created_at': now,
                'authorization': owner_authorization, 'last_failed_turn_id': turn['id']}
            ledger['episode'] = episode
        attempts = episode['attempts']
        if episode['status'] in {'attempting', 'manual_review'}:
            return manual('uncertain submission or previously escalated episode')
        if attempts:
            last = attempts[-1]
            if last.get('turn_id') != turn['id']:
                return manual('admitted recovery outcome does not match latest native turn')
            if now < last['next_attempt_at']:
                return finish('backoff', next_attempt_at=last['next_attempt_at'])
        if len(attempts) >= 2:
            return manual('two recovery attempts exhausted without new tool work or success')
        episode['last_failed_turn_id'] = turn['id']
        if dry_run:
            return finish('would_recover')
        if stop_event is not None and stop_event.is_set():
            return finish('stopped', reason='stop requested before recovery claim')
        attempt = {'id': episode['id'] + ':' + str(len(attempts) + 1), 'failed_turn_id': turn['id'],
            'claimed_at': now, 'turn_id': None, 'next_attempt_at': now + 30 * (2 ** len(attempts)),
            'tools': [], 'status': 'claimed'}
        attempts.append(attempt)
        episode['status'] = 'attempting'
        persist()  # Claim precedes native writer/turn mutation.

        def on_start(turn_id):
            nonlocal performed
            if not isinstance(turn_id, str) or not turn_id.strip() or turn_id == turn['id']:
                raise ValueError('Invalid recovery turn acknowledgement')
            with document_lock:
                attempt.update(turn_id=turn_id, status='admitted')
                performed = True
                persist()

        def on_tool(turn_id, item_id):
            with document_lock:
                if turn_id == attempt['turn_id'] and item_id not in attempt['tools']:
                    attempt['tools'].append(item_id)
                    persist()

        try:
            run_codex_research_recovery_turn(thread_id, failed_turn_id=turn['id'], goal_identity=identity,
                prompt=prompt + '\n[CodeYun recovery episode ' + attempt['id'] + ']',
                on_start=on_start, on_tool=on_tool, stop_event=stop_event)
        except ResearchRecoveryAdmissionRejected as exc:
            # Native writer conflict/read/mismatch proves turn/start was never
            # reached. Retain the rejection fact, but do not spend the 2 turns.
            attempts.pop()
            episode.setdefault('admission_rejections', []).append({'at': now, 'reason': str(exc)})
            episode['admission_rejections'] = episode['admission_rejections'][-8:]
            episode['status'] = 'ready'
            return finish('rejected', reason=str(exc))
        except ResearchRecoveryInterrupted as exc:
            attempt.update(status='outcome_unknown', native_terminal_status='interrupted', error=str(exc))
            episode['status'] = 'awaiting_outcome'
            return finish('interrupted', reason=str(exc))
        except Exception as exc:
            attempt.update(status='outcome_unknown', error=f'{type(exc).__name__}: {exc}',
                next_attempt_at=time.time() + 30 * (2 ** (len(attempts) - 1)))
            native_failed = (isinstance(exc, CodexTurnFailed)
                and exc.thread_id == thread_id and exc.turn_id == attempt['turn_id'])
            if native_failed:
                attempt.update(status='failed', native_terminal_status='failed',
                    native_error=exc.native_error, native_error_events=exc.error_events)
            if not attempt['turn_id']:
                return manual('turn/start acknowledgement unknown; do not replay')
            if attempt['tools']:
                ledger['closed'] = (ledger['closed'] + [{**episode, 'status': 'resolved',
                    'resolved_by_turn_id': attempt['turn_id'], 'resolved_tool_ids': attempt['tools'],
                    'resolved_at': time.time()}])[-32:]
                ledger['episode'] = None
                return finish('new_tool_progress', reason=(
                    'old failure cleared by new tool work; current native turn failed'
                    if native_failed else 'old failure cleared; current turn outcome remains unknown'),
                    **({'native_terminal_status': 'failed', 'native_error': exc.native_error,
                        'failed_turn_id': exc.turn_id} if native_failed else {}))
            episode['status'] = 'awaiting_outcome'
            if native_failed:
                # Terminal failure is known, but the existing fresh live/native
                # writer + active Goal gate must still admit any later retry.
                return finish('native_turn_failed', reason=attempt['error'],
                    native_terminal_status='failed', native_error=exc.native_error)
            return finish('awaiting_outcome', reason=attempt['error'])
        if not attempt['turn_id']:
            return manual('transport returned without an exact admitted turn acknowledgement')
        attempt['status'] = 'completed'
        ledger['closed'] = (ledger['closed'] + [{**episode, 'status': 'resolved',
            'resolved_by_turn_id': attempt['turn_id'], 'resolved_at': time.time()}])[-32:]
        ledger['episode'] = None
        return finish('turn_completed')
