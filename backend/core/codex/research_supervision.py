"""Read-only supervision of original research IDs, independent of group hooks.

inspect_codex_research is the engineering polling entry. It records newly observed
issues, not sent alerts or resumed work. Desktop ownership, child persisted turn
history and child persisted Goal are separate evidence; none grants game control.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
import threading
import uuid
from pathlib import Path
from typing import Any

from filelock import FileLock

from backend.core.codex.app_server import read_codex_thread_goal, read_codex_thread_turns
from backend.core.codex.desktop import call_desktop_tool
from backend.core.temp_paths import codeyun_temp_root


_TURN_FIELDS = ('id', 'status', 'error', 'startedAt', 'completedAt', 'durationMs')
_GOAL_FIELDS = ('threadId', 'objective', 'status', 'createdAt', 'updatedAt',
                'tokensUsed', 'timeUsedSeconds', 'tokenBudget', 'timeBudgetSeconds')
_TURN_STATES = {'completed', 'failed', 'interrupted', 'inProgress'}
_GOAL_STATES = {'active', 'blocked', 'paused', 'complete', 'budgetLimited', 'usageLimited'}


def _validate(thread_id: str, turn_limit: int, timeout_seconds: float) -> None:
    if not isinstance(thread_id, str) or not thread_id.strip() or len(thread_id) > 200:
        raise ValueError('A nonempty original research thread ID is required')
    if type(turn_limit) is not int or not 1 <= turn_limit <= 10:
        raise ValueError('turn_limit must be between 1 and 10')
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 30:
        raise ValueError('timeout_seconds must be finite and between 0 and 30')


def _turn(row: dict) -> dict:
    if not isinstance(row, dict) or (not isinstance(row.get('id'), str) or not row['id'].strip()) or row.get('status') not in _TURN_STATES:
        raise ValueError('Invalid bounded turn status response')
    result = {key: row.get(key) for key in _TURN_FIELDS}
    error = result['error']
    if error is not None:
        if not isinstance(error, dict):
            raise ValueError('Invalid native turn error')
        result['error'] = project_codex_turn_error(error)
    return result


def project_codex_turn_error(error: dict) -> dict:
    """Keep bounded native first-error evidence without assigning a root cause.

    The official turn error includes additionalDetails and codexErrorInfo may
    carry an HTTP status in a structured variant. Stringifying that variant
    loses its contract for callers. This projection never retries or resumes.
    """
    if not isinstance(error, dict):
        raise ValueError('Invalid native turn error')

    def bounded(value, depth=0):
        if isinstance(value, dict) and depth < 2:
            return {str(key)[:128]: bounded(child, depth + 1)
                    for key, child in list(value.items())[:8]}
        if value is None or isinstance(value, (bool, int)):
            return value
        return str(value)[:1200]

    return {key: bounded(error[key]) for key in
            ('message', 'codexErrorInfo', 'additionalDetails') if error.get(key) is not None}


def _fingerprint(thread_id: str, kind: str, identity: Any) -> str:
    value = json.dumps([thread_id, kind, identity], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(value.encode()).hexdigest()


def read_codex_research_snapshot(thread_id: str, *, turn_limit: int = 5,
                                 timeout_seconds: float = 10) -> dict:
    """Read three bounded sources once; source failures remain explicit unknowns.

    Timeout bounds each call, not the combined duration (at most three calls).
    There are no resumes, starts, Goal changes, GUI/game calls or full history
    fallback. A child app-server's interrupted status never releases a writer.
    """
    _validate(thread_id, turn_limit, timeout_seconds)
    thread_id = thread_id.strip()
    captured_at = time.time()
    errors = []
    desktop = {'source': 'live_desktop', 'available': False, 'writer': 'unknown', 'latest_turn': None}
    history = {'source': 'child_persisted_turns', 'available': False, 'turns': [],
               'limit': turn_limit, 'has_more': None, 'writer_authority': False}
    goal = {'source': 'child_persisted_goal', 'available': False, 'value': None,
            'desktop_goal_receipt': False}

    def failed(source, exc):
        errors.append({'source': source, 'type': type(exc).__name__, 'message': str(exc)[:1200]})

    try:
        payload = call_desktop_tool('read_thread', {'threadId': thread_id, 'hostId': 'local',
            'turnLimit': 1, 'includeOutputs': False, 'maxOutputCharsPerItem': 100}, timeout=timeout_seconds)
        thread = payload.get('thread') or {}
        state = (thread.get('status') or {}).get('type')
        if thread.get('id') != thread_id or state not in {'active', 'idle', 'notLoaded', 'systemError'}:
            raise ValueError('Unknown/mismatched desktop ownership response')
        rows = payload.get('turns')
        if not isinstance(rows, list) or len(rows) > 1:
            raise ValueError('Desktop response exceeded bounded turn contract')
        latest = _turn(rows[0]) if rows else None
        items = rows[0].get('items', []) if rows else []
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise ValueError('Invalid desktop turn items schema')
        writer = ('active' if state == 'active' or latest and latest['status'] == 'inProgress'
                  else 'inactive_observed' if state == 'idle' else 'unknown')
        desktop.update(available=True, status=state, writer=writer, latest_turn=latest,
            updated_at=thread.get('updatedAt'), completed_command_ids=[item['id'] for item in
            items if isinstance(item, dict) and item.get('type') == 'commandExecution'
            and item.get('status') == 'completed' and isinstance(item.get('id'), str)] if rows else [])
    except Exception as exc:
        failed('desktop', exc)
    try:
        payload = read_codex_thread_turns(thread_id, limit=turn_limit, timeout_seconds=timeout_seconds)
        rows = payload.get('data')
        if not isinstance(rows, list) or len(rows) > turn_limit:
            raise ValueError('Native history exceeded bounded turn contract')
        turns = [_turn(row) for row in rows]
        history.update(available=True, turns=turns, has_more=bool(payload.get('nextCursor')))
    except Exception as exc:
        failed('turn_history', exc)
    try:
        payload = read_codex_thread_goal(thread_id, timeout_seconds=timeout_seconds)
        value = payload.get('goal')
        if value is None:
            goal.update(available=True, missing=True)
        elif (not isinstance(value, dict) or value.get('threadId') != thread_id
              or value.get('status') not in _GOAL_STATES or not isinstance(value.get('objective'), str)
              or not value['objective'].strip()):
            raise ValueError('Unknown/mismatched native Goal response')
        else:
            goal.update(available=True, value={key: value.get(key) for key in _GOAL_FIELDS})
    except Exception as exc:
        failed('goal', exc)

    failures = [row for row in history['turns'] if row['status'] == 'failed']
    if (desktop.get('latest_turn') or {}).get('status') == 'failed' and not any(
            row['id'] == desktop['latest_turn']['id'] for row in failures):
        failures.insert(0, desktop['latest_turn'])
    latest_terminal_error = failures[0] if failures else None
    goal_status = (goal['value'] or {}).get('status')
    issues = []

    def issue(kind, identity, evidence):
        issues.append({'kind': kind, 'fingerprint': _fingerprint(thread_id, kind, identity), 'evidence': evidence})

    for failure in failures:
        issue('terminal_failure', failure['id'], failure)
    if goal_status == 'blocked':
        issue('goal_blocked', (goal['value'] or {}).get('updatedAt'), {'goal_status': goal_status,
            'updated_at': goal['value'].get('updatedAt'), 'writer': desktop['writer']})
    for error in errors:
        # Volatile transport details stay in evidence, not in issue identity.
        issue('observation_unavailable', [error['source'], error['type']], error)
    if desktop['writer'] == 'unknown' and not any(e['source'] == 'desktop' for e in errors):
        issue('observation_unavailable', ['desktop', 'unknown_ownership'], {'desktop_status': desktop.get('status')})

    return {'schema_version': 1, 'thread_id': thread_id, 'captured_at': captured_at,
        'observed_at': time.time(), 'source_snapshot_atomic': False, 'desktop': desktop, 'turn_history': history, 'goal': goal,
        'latest_terminal_error': latest_terminal_error, 'source_errors': errors,
        'blocked_with_running_writer': goal_status == 'blocked' and desktop['writer'] == 'active',
        'takeover_allowed': False, 'writer_gate_released': False,
        'control_admission_required': True, 'issues': issues,
        'network_root_cause': 'unknown', 'recovery_performed': False}


def inspect_codex_research(thread_id: str, *, state_dir: Path | None = None,
                           turn_limit: int = 5, timeout_seconds: float = 10) -> dict:
    """Engineering sweep entry: read once and atomically deduplicate issues.

    state_dir defaults to CodeYun TEMP; a dedicated directory can be provided by
    an engineering caller. FileLock owns bounded cross-process admission. One
    atomic JSON document contains snapshot and seen fingerprints; corruption or
    publication/lock failure propagates instead of resetting deduplication.
    new_issues are newly persisted diagnostics for the caller's authorized alert
    policy, NOT delivery receipts. No scheduler registration or sender is added.
    Older concurrent observations cannot overwrite a newer stored snapshot or
    emit fresh alerts. Their unseen failures remain durable deferred evidence,
    requiring a fresh bounded observation before alert admission; they are not
    inserted into the seen ledger merely because an old read finished late.
    """
    snapshot = read_codex_research_snapshot(thread_id, turn_limit=turn_limit, timeout_seconds=timeout_seconds)
    directory = Path(state_dir) if state_dir is not None else codeyun_temp_root('codex-research-supervision')
    directory.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(snapshot['thread_id'].encode()).hexdigest()
    path = directory / (key + '.json')
    with FileLock(str(directory / (key + '.lock')), timeout=1):
        previous = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        if previous and (previous.get('schema_version') != 1 or previous.get('thread_id') != snapshot['thread_id']
                         or not isinstance(previous.get('seen_issues'), dict) or not isinstance(previous.get('snapshot'), dict)
                         or not isinstance(previous.get('deferred_failures', {}), dict)):
            raise ValueError('Invalid persisted research supervision ledger; deduplication preserved')
        seen = previous.get('seen_issues', {}).copy()
        pending = previous.get('deferred_failures', {}).copy()
        latest = previous.get('snapshot') or {}
        superseded = latest.get('captured_at', 0) > snapshot['captured_at']
        new = []
        for item in snapshot['issues']:
            fingerprint = item['fingerprint']
            if fingerprint in seen:
                pending.pop(fingerprint, None)
                continue
            entry = {**item, 'first_observed_at': snapshot['observed_at']}
            if superseded:
                if item['kind'] == 'terminal_failure':
                    pending.setdefault(fingerprint, {**entry,
                        'status': 'awaiting_fresh_observation', 'captured_at': snapshot['captured_at']})
                continue
            seen[fingerprint] = entry
            pending.pop(fingerprint, None)
            new.append(entry)
        document = {'schema_version': 1, 'thread_id': snapshot['thread_id'],
                    'snapshot': latest if superseded else snapshot, 'seen_issues': seen,
                    'deferred_failures': pending, 'handoff': previous.get('handoff', {})}
        if superseded:
            document['last_superseded_snapshot'] = snapshot
        elif previous.get('last_superseded_snapshot'):
            document['last_superseded_snapshot'] = previous['last_superseded_snapshot']
        _write_document(path, document)
    return {'snapshot': snapshot, 'new_issues': new, 'evidence_path': str(path),
            'superseded': superseded, 'deferred_failures': list(pending.values()),
            'fresh_observation_required': bool(pending), 'notification_sent': False, 'recovery_performed': False}



def _write_document(path: Path, document: dict) -> None:
    temporary = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('w', encoding='utf-8') as file:
            json.dump(document, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def handoff_codex_research_issues(thread_id: str, *, store=None, state_dir: Path | None = None,
                                  limit: int = 20) -> dict:
    """Recover persisted diagnostics into the existing engineering-only outbox.

    No sender is called and no subscription is created. Exact durable notification
    lookup, not enqueue's boolean, proves admission. A crash after enqueue but
    before the atomic handoff receipt retries the same stage ID and reconciles
    its existing notification. Seen diagnostics are never treated as delivered.
    Late deferred failures are excluded until fresh corroboration admits them.
    """
    _validate(thread_id, 1, 10)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('handoff limit must be between 1 and 100')
    from backend.core.messaging.codex_results import record_codex_stage_result, result_store
    store = store or result_store()
    thread_id = thread_id.strip()
    directory = Path(state_dir) if state_dir is not None else codeyun_temp_root('codex-research-supervision')
    key = hashlib.sha256(thread_id.encode()).hexdigest()
    path = directory / (key + '.json')
    if not path.exists():
        return {'admitted': [], 'pending': 0, 'reason': 'no_observation', 'notification_sent': False}
    with FileLock(str(directory / (key + '.lock')), timeout=1):
        document = json.loads(path.read_text(encoding='utf-8'))
        if document.get('thread_id') != thread_id or not isinstance(document.get('seen_issues'), dict) or not isinstance(document.get('handoff', {}), dict):
            raise ValueError('Invalid supervision handoff ledger')
        handoff = document.get('handoff', {}).copy()
        candidates = [item for fingerprint, item in document['seen_issues'].items() if fingerprint not in handoff]
        admitted = []
        for issue in candidates[:limit]:
            fingerprint = issue['fingerprint']
            stage = 'research-issue:' + fingerprint
            turn_id = 'stage:' + stage
            evidence = issue['evidence']
            if issue['kind'] == 'terminal_failure':
                text = f"研发监督：原线程 {thread_id} 的 turn {evidence['id']} 已失败，第一现场已保留；未自动恢复或接管。"
            elif issue['kind'] == 'goal_blocked':
                text = f"研发监督：原线程 {thread_id} 的 Goal 观测为 blocked，桌面 writer 为 {evidence['writer']}；须经合法控制入口处理，未自动恢复或接管。"
            else:
                text = f"研发监督：原线程 {thread_id} 的 {evidence.get('source', 'desktop')} 状态暂不可核验；保留运行权，未自动恢复或接管。"
            record_codex_stage_result(thread_id, stage, text, store=store)
            notification = store.codex_notification(thread_id, turn_id)
            if notification is None or notification['status'] == 'baseline':
                continue
            receipt = {'notification_id': notification['id'], 'turn_id': turn_id,
                       'status': 'outbox_recorded', 'recorded_at': time.time()}
            handoff[fingerprint] = receipt
            admitted.append(receipt)
        document['handoff'] = handoff
        _write_document(path, document)
    return {'admitted': admitted, 'pending': len(document['seen_issues']) - len(handoff),
            'notification_sent': False, 'reason': None if not candidates or admitted else 'outbox_not_admitted'}


_REGISTRY_KEY = 'codex_research_supervision_registry'


def _registry_lock(store):
    identity = str(Path(store.path).resolve())
    key = hashlib.sha256(identity.encode()).hexdigest()
    return FileLock(str(codeyun_temp_root('codex-research-supervision') / ('registry-' + key + '.lock')), timeout=1)


def register_codex_research(thread_id: str, *, store=None, interval_seconds: int = 60,
                            enabled: bool = True, notify: bool = True) -> dict:
    """Owner-authorized local registration, independent of the current group hook.

    Does not create/resume threads, alter Goals or subscriptions, or send. notify
    requires an existing enabled destination subscription. Re-registering retains
    due times/health; changing the registry does not start an inspection service.
    """
    _validate(thread_id, 1, 10)
    if type(interval_seconds) is not int or not 30 <= interval_seconds <= 3600 or type(enabled) is not bool or type(notify) is not bool:
        raise ValueError('Invalid research polling registration')
    from backend.core.messaging.codex_results import result_store
    store = store or result_store()
    thread_id = thread_id.strip()
    if notify and not any(row['thread_id'] == thread_id and row['enabled'] for row in store.codex_notifications_status()['subscriptions']):
        raise ValueError('Existing enabled Codex destination subscription required')
    with _registry_lock(store):
        registry = store.get_meta(_REGISTRY_KEY) or {}
        entry = {**registry.get(thread_id, {}), 'thread_id': thread_id, 'enabled': enabled,
                 'notify': notify, 'interval_seconds': interval_seconds}
        if thread_id not in registry and len(registry) >= 32:
            raise ValueError('Research supervision registry is full (32 original IDs)')
        registry[thread_id] = entry
        store.set_meta(_REGISTRY_KEY, registry)
    return entry


def inspect_registered_codex_research_once(*, store=None, state_dir: Path | None = None,
                                          stop_event=None, timeout_seconds: float = 10) -> dict:
    """One bounded registered sweep for the resident independent patrol loop.

    Registry locks protect only admission/health; no native read holds a registry,
    assistance-control or Kernel lock. Each registration has a finite in-flight
    lease, due interval and bounded failure backoff. Stops are checked before each
    thread; in-flight public reads retain their per-call timeout. Diagnosis/outbox
    handoff is retried from persistent seen evidence on every due sweep.
    """
    _validate('registry', 5, timeout_seconds)
    from backend.core.messaging.codex_results import result_store
    store = store or result_store()
    with _registry_lock(store):
        registry = store.get_meta(_REGISTRY_KEY) or {}
        # Never-checked registrations first, then the oldest due time. A slow
        # first batch cannot monopolize all later sweeps once it becomes due again.
        ids = sorted(registry, key=lambda key: (
            bool(registry[key].get('last_result')), registry[key].get('next_check_at', 0), key))
    results = []
    for thread_id in ids:
        if len(results) >= 4:
            break
        if stop_event is not None and stop_event.is_set():
            break
        now = time.time()
        claim = uuid.uuid4().hex
        with _registry_lock(store):
            registry = store.get_meta(_REGISTRY_KEY) or {}
            entry = registry.get(thread_id) or {}
            if not entry.get('enabled') or now < entry.get('next_check_at', 0) or now < entry.get('in_flight_until', 0):
                continue
            entry.update(claim_id=claim, in_flight_until=now + 3 * timeout_seconds + 30)
            registry[thread_id] = entry
            store.set_meta(_REGISTRY_KEY, registry)
        try:
            observation = inspect_codex_research(thread_id, state_dir=state_dir, timeout_seconds=timeout_seconds)
            transfer = handoff_codex_research_issues(thread_id, store=store, state_dir=state_dir) if entry['notify'] else None
            errors = observation['snapshot']['source_errors']
            unknown = observation['snapshot']['desktop']['writer'] == 'unknown'
            result = {'thread_id': thread_id, 'checked_at': time.time(), 'source_errors': errors,
                      'deferred_failures': observation['deferred_failures'], 'handoff': transfer,
                      'error': ('source_observation_unavailable' if errors or unknown else
                                'outbox_not_admitted' if transfer and transfer['pending'] else None)}
        except Exception as exc:
            result = {'thread_id': thread_id, 'checked_at': time.time(),
                      'error': f'{type(exc).__name__}: {exc}', 'source_errors': None}
        with _registry_lock(store):
            registry = store.get_meta(_REGISTRY_KEY) or {}
            latest = registry.get(thread_id) or {}
            if latest.get('claim_id') == claim:
                count = latest.get('consecutive_failures', 0) + 1 if result['error'] else 0
                delay = min(600, latest['interval_seconds'] * 2 ** min(count, 4))
                latest.update(last_result=result, consecutive_failures=count,
                              next_check_at=time.time() + delay, in_flight_until=0)
                registry[thread_id] = latest
                store.set_meta(_REGISTRY_KEY, registry)
        results.append(result)
    with _registry_lock(store):
        registry = store.get_meta(_REGISTRY_KEY) or {}
        failures = [row['last_result'] for row in registry.values()
                    if row.get('enabled') and (row.get('last_result') or {}).get('error')]
    health = {'checked_at': time.time(), 'observer_process_id': os.getpid(),
              'observer_thread_name': threading.current_thread().name, 'results': results, 'failures': failures,
              'registrations': [{'thread_id': row['thread_id'], 'enabled': row['enabled'],
                                 'next_check_at': row.get('next_check_at'), 'last_result': row.get('last_result')}
                                for row in registry.values()],
              'error': 'research supervision has unresolved errors' if failures else None,
              'notification_sent': False, 'recovery_performed': False}
    store.set_meta('codex_research_supervision_worker', health)
    return health
