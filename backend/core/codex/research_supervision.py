"""Supervision of original research IDs, independent of group hooks.

inspect_codex_research is the engineering polling entry. It records newly observed
issues, not sent alerts or resumed work. Desktop ownership, child persisted turn
history and child persisted Goal are separate evidence; none grants game control.
Recovery is separately opt-in; existing registrations remain diagnostic only.
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


def project_codex_failure_timeline(desktop: dict, history: dict, failures: list[dict]) -> dict:
    """Separate bounded historical errors from the corroborated latest turn.

    A later completed native/desktop turn supersedes the transport failure,
    not the business Goal. Active work, missing reads and mismatched heads are
    unconfirmed; none releases a writer or proves recovery/Goal completion.
    Original failed-turn evidence remains intact for first-cause investigation.
    """
    live = desktop.get('latest_turn') or {}
    rows = history.get('turns') or []
    native = rows[0] if rows else {}
    same_head = bool(desktop.get('available') and history.get('available')
        and live.get('id') and live.get('id') == native.get('id')
        and live.get('status') == native.get('status'))
    idle = desktop.get('writer') == 'inactive_observed'
    current = live if same_head and idle and live.get('status') == 'failed' else None
    success = live if same_head and idle and live.get('status') == 'completed' else None
    later_success = live if success and any(
        row['id'] != live['id'] for row in failures) else None
    history_rows = [{
        'failure': failure,
        'relation': ('current_failure' if current and failure['id'] == current['id'] else
            'superseded_by_latest_success' if later_success and failure['id'] != later_success['id'] else
            'older_than_live_turn_unresolved' if desktop.get('available') and live.get('id')
                and failure['id'] != live['id'] else 'unconfirmed'),
        'superseded_by_turn_id': later_success['id'] if later_success else None,
    } for failure in failures]
    return {'state': ('current_failure' if current else 'latest_success_after_failure' if later_success
                     else 'active_or_unconfirmed' if failures else 'no_observed_failure'),
        'current_terminal_error': current, 'latest_success_after_failure': later_success,
        'latest_corroborated_success': success,
        'historical_failures': history_rows, 'business_goal_completed': False,
        'writer_gate_released': False, 'recovery_performed': False}


def project_codex_research_authority(desktop: dict, history: dict) -> dict:
    """Live ownership and reader lifecycle are orthogonal evidence sources.

    Independent thread/turns/list is a persisted read, not the live writer's
    terminal event stream. In particular same-ID interrupted cannot overrule a
    desktop inProgress writer. Neither source disagreement nor notLoaded grants
    recovery; the existing exact-head gate remains mandatory.
    """
    live = desktop.get('latest_turn') or {}
    rows = history.get('turns') or []
    persisted = rows[0] if rows else {}
    same_id = bool(live.get('id') and live.get('id') == persisted.get('id'))
    agreement = bool(desktop.get('available') and history.get('available') and same_id
        and live.get('status') == persisted.get('status'))
    contrast = bool(same_id and desktop.get('writer') == 'active'
        and live.get('status') == 'inProgress' and persisted.get('status') == 'interrupted')
    return {'writer_source': 'live_desktop', 'writer': desktop.get('writer', 'unknown'),
        'persisted_turn_writer_authority': False, 'latest_turn_agreement': agreement,
        'same_turn_lifecycle_contrast': contrast,
        'global_terminal_state': 'unconfirmed' if not agreement else live.get('status'),
        'recovery_admission_granted': False, 'writer_gate_released': False}


def read_codex_research_source_alignment(thread_id: str, *, timeout_seconds: float = 5) -> dict:
    """Bracket an independent reader with two bounded live desktop snapshots.

    A stable live writer plus same-ID interrupted/notLoaded reader evidence
    proves that reader lifecycle cannot release that writer. It does not prove
    why the persisted parser produced interrupted, fix a compact error, or
    authorize recovery. Changes/failures in the bracket remain unknown.
    """
    from backend.core.codex.app_server import read_codex_thread_readonly_session
    _validate(thread_id, 1, timeout_seconds)
    errors = []
    observations = {}

    def live():
        payload = call_desktop_tool('read_thread', {'threadId': thread_id, 'hostId': 'local',
            'turnLimit': 1, 'includeOutputs': False, 'maxOutputCharsPerItem': 100}, timeout=timeout_seconds)
        thread = payload.get('thread') or {}
        rows = payload.get('turns')
        if thread.get('id') != thread_id or not isinstance(rows, list) or len(rows) > 1:
            raise ValueError('Invalid live ownership response')
        return {'status': (thread.get('status') or {}).get('type'),
            'latest_turn': _turn(rows[0]) if rows else None, 'observed_at': time.time()}

    for name, reader in [('live_before', live), ('independent_reader', lambda:
            read_codex_thread_readonly_session(thread_id, timeout_seconds=timeout_seconds)), ('live_after', live)]:
        try:
            observations[name] = reader()
        except Exception as exc:
            observations[name] = None
            errors.append({'source': name, 'type': type(exc).__name__, 'message': str(exc)[:1200]})
    before, after = observations['live_before'] or {}, observations['live_after'] or {}
    child = observations['independent_reader'] or {}
    bt, at = before.get('latest_turn') or {}, after.get('latest_turn') or {}
    rows = child.get('turns') or []
    try:
        persisted = _turn(rows[0]) if rows else {}
    except Exception as exc:
        persisted = {}
        errors.append({'source': 'independent_reader_schema', 'type': type(exc).__name__, 'message': str(exc)[:1200]})
    stable_active = bool(not errors and before.get('status') == after.get('status') == 'active'
        and bt.get('id') and bt.get('id') == at.get('id')
        and bt.get('status') == at.get('status') == 'inProgress')
    contrast = bool(stable_active and persisted.get('id') == at['id']
        and persisted.get('status') == 'interrupted'
        and ((child.get('metadata') or {}).get('status') or {}).get('type') == 'notLoaded')
    return {'thread_id': thread_id, 'observations': observations, 'source_errors': errors,
        'live_writer_stable_active': stable_active, 'same_turn_lifecycle_contrast_observed': contrast,
        'writer_authority_source': 'live_desktop' if stable_active else 'unknown',
        'persisted_turn_writer_authority': False, 'global_terminal_state': 'unconfirmed' if contrast else 'unknown',
        'interpretation': ('independent_reader_lifecycle_is_not_live_writer_state' if contrast
                           else 'no_stable_contrast_proven'),
        'interrupted_parser_root_cause': 'unknown', 'recovery_admission_granted': False,
        'writer_gate_released': False, 'goal_changed': False}


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
            and item.get('status') == 'completed' and isinstance(item.get('id'), str)] if rows else [],
            completed_tool_ids=[item['id'] for item in items
                if item.get('type') in {'commandExecution', 'fileChange', 'mcpToolCall', 'webSearch'}
                and item.get('status') == 'completed' and isinstance(item.get('id'), str)])
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
    failure_timeline = project_codex_failure_timeline(desktop, history, failures)
    goal_status = (goal['value'] or {}).get('status')
    issues = []

    def issue(kind, identity, evidence):
        issues.append({'kind': kind, 'fingerprint': _fingerprint(thread_id, kind, identity), 'evidence': evidence})

    for failure in failures:
        if not failure_timeline['latest_success_after_failure']:
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
        'authority': project_codex_research_authority(desktop, history),
        # Compatibility field is the newest *failed* row in bounded history,
        # not necessarily the latest/current turn. New consumers use timeline.
        'latest_terminal_error': latest_terminal_error, 'failure_timeline': failure_timeline,
        'current_terminal_error': failure_timeline['current_terminal_error'], 'source_errors': errors,
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
                         or not isinstance(previous.get('notice_suppression', {}), dict)
                         or not isinstance(previous.get('notice_hold', {}), dict)
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
                    'deferred_failures': pending, 'handoff': previous.get('handoff', {}),
                    'notice_suppression': previous.get('notice_suppression', {}),
                    'notice_hold': previous.get('notice_hold', {})}
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
                                  limit: int = 20, snapshot: dict | None = None) -> dict:
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
    # Native observation never holds a handoff/registry lock. Registered patrol
    # passes the just-observed snapshot, avoiding a second 3-call observation
    # that would outlive its short observation claim.
    fresh = snapshot if snapshot is not None else read_codex_research_snapshot(thread_id)
    now = time.time()
    if (not isinstance(fresh, dict) or fresh.get('thread_id') != thread_id
        or type(fresh.get('observed_at')) not in {int, float}
        or not 0 <= now - fresh['observed_at'] <= 10):
        return {'admitted': [], 'pending': 0, 'reason': 'fresh_observation_required',
                'notification_sent': False, 'held': ['fresh_observation_required']}
    with FileLock(str(directory / (key + '.lock')), timeout=1):
        document = json.loads(path.read_text(encoding='utf-8'))
        if document.get('thread_id') != thread_id or not isinstance(document.get('seen_issues'), dict) or not isinstance(document.get('handoff', {}), dict):
            raise ValueError('Invalid supervision handoff ledger')
        handoff = document.get('handoff', {}).copy()
        if (not isinstance(document.get('notice_suppression', {}), dict)
            or not isinstance(document.get('notice_hold', {}), dict)):
            raise ValueError('Invalid notice suppression ledger')
        suppressed = document.get('notice_suppression', {}).copy()
        notice_hold = document.get('notice_hold', {}).copy()
        if (document.get('snapshot') or {}).get('captured_at', 0) > fresh.get('captured_at', 0):
            return {'admitted': [], 'pending': 0, 'reason': 'newer_observation_exists',
                    'notification_sent': False, 'held': ['fresh_observation_required']}

        def disposition(issue):
            if issue['kind'] == 'terminal_failure':
                timeline = fresh.get('failure_timeline') or {}
                rows = timeline.get('historical_failures') or []
                row = next((r for r in rows if r['failure']['id'] == issue['evidence']['id']), None)
                if row and row['relation'] == 'superseded_by_latest_success':
                    return 'covered'
                # The old failure may have fallen out of the bounded page.
                # An already-observed failed event strictly before the native
                # successful turn's start proves order without guessing IDs.
                success = timeline.get('latest_corroborated_success') or {}
                old_end = issue['evidence'].get('completedAt')
                if old_end is None:
                    old_end = issue.get('first_observed_at')
                start = success.get('startedAt')
                if (success.get('id') != issue['evidence']['id'] and type(old_end) in {int, float}
                    and type(start) in {int, float} and math.isfinite(old_end) and math.isfinite(start)
                    and old_end < start):
                    return 'covered'
                current = fresh.get('current_terminal_error')
                return 'eligible' if current and current['id'] == issue['evidence']['id'] else 'hold'
            if issue['kind'] == 'goal_blocked':
                source = fresh.get('goal') or {}
                value = source.get('value') or {}
                if not source.get('available') or not value.get('status'):
                    return 'hold'
                return ('eligible' if value['status'] == 'blocked'
                    and value.get('updatedAt') == issue['evidence'].get('updated_at') else 'covered')
            if any(row['fingerprint'] == issue['fingerprint'] for row in fresh.get('issues', [])):
                return 'eligible'
            source = issue['evidence'].get('source', 'desktop')
            current_source = fresh.get({'desktop': 'desktop', 'goal': 'goal', 'turn_history': 'turn_history'}.get(source, 'desktop')) or {}
            return 'covered' if current_source.get('available') and (
                source != 'desktop' or current_source.get('writer') != 'unknown') else 'hold'

        decisions = [(fp, item, disposition(item)) for fp, item in document['seen_issues'].items()]
        candidates = [(fp, item, status) for fp, item, status in decisions
                      if ((fp not in handoff and fp not in suppressed) or status in {'covered', 'hold'}
                          or (status == 'eligible' and fp in notice_hold))
                      and fp not in suppressed and not (status == 'hold' and fp in notice_hold)]
        # Unconfirmed rows do not consume every sweep's finite processing slots.
        candidates.sort(key=lambda row: row[2] == 'hold')
        admitted = []
        held = []
        for fingerprint, issue, status in candidates[:limit]:
            stage = 'research-issue:' + fingerprint
            turn_id = 'stage:' + stage
            if status == 'covered':
                # Suppression is not a delivery receipt. Already-sent/uncertain
                # outbox data is preserved; only unreserved pending is cancelled.
                receipt = store.suppress_pending_codex_notification(thread_id, turn_id,
                    reason='fresh native observation supersedes this historical notice')
                suppressed[fingerprint] = {'status': 'covered', 'checked_at': time.time(),
                    'observation_turn_id': (fresh['desktop'].get('latest_turn') or {}).get('id'),
                    'outbox': receipt}
                continue
            if status == 'hold':
                held.append(fingerprint)
                receipt = store.suppress_pending_codex_notification(thread_id, turn_id,
                    reason='current notice unconfirmed; retain unknown state', category='unconfirmed')
                notice_hold[fingerprint] = {'status': 'unconfirmed', 'checked_at': time.time(), 'outbox': receipt}
                continue
            evidence = issue['evidence']
            if issue['kind'] == 'terminal_failure':
                text = f"研发监督：原线程 {thread_id} 的当前最新 turn {evidence['id']} 已失败，第一现场已保留；未自动恢复或接管。"
            elif issue['kind'] == 'goal_blocked':
                text = f"研发监督：原线程 {thread_id} 的 Goal 观测为 blocked，桌面 writer 为 {fresh['desktop']['writer']}；须经合法控制入口处理，未自动恢复或接管。"
            else:
                text = f"研发监督：原线程 {thread_id} 的 {evidence.get('source', 'desktop')} 状态暂不可核验；保留运行权，未自动恢复或接管。"
            store.restore_held_codex_notification(thread_id, turn_id, text=text)
            record_codex_stage_result(thread_id, stage, text, store=store)
            notification = store.codex_notification(thread_id, turn_id)
            if notification is None or notification['status'] == 'baseline':
                continue
            receipt = {'notification_id': notification['id'], 'turn_id': turn_id,
                       'status': 'outbox_recorded', 'recorded_at': time.time()}
            handoff[fingerprint] = receipt
            notice_hold.pop(fingerprint, None)
            admitted.append(receipt)
        document['handoff'] = handoff
        document['notice_suppression'] = suppressed
        document['notice_hold'] = notice_hold
        _write_document(path, document)
    pending = sum(1 for fp, _, status in decisions if status == 'eligible' and fp not in handoff and fp not in suppressed)
    held = [fp for fp, _, status in decisions if status == 'hold' and fp not in suppressed]
    return {'admitted': admitted, 'pending': pending, 'suppressed': list(suppressed), 'held': held[:limit],
            'held_count': len(held),
            'notification_sent': False, 'reason': 'outbox_not_admitted' if pending else
            'latest_failure_unconfirmed' if held else None}


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
    Recovery is opt-in through a separate explicit owner policy. Without it this
    remains read-only. Enabled recovery owns its own durable/native writer gate;
    no inspection result by itself releases ownership or grants game actions.
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
            transfer = handoff_codex_research_issues(thread_id, store=store, state_dir=state_dir,
                snapshot=observation['snapshot']) if entry['notify'] else None
            errors = observation['snapshot']['source_errors']
            unknown = observation['snapshot']['desktop']['writer'] == 'unknown'
            recovery = None
            policies = store.get_meta('codex_research_recovery_policies') or {}
            if not isinstance(policies, dict):
                raise ValueError('Invalid research recovery policies; admission refused')
            policy = policies.get(thread_id)
            if policy is not None:
                if (not isinstance(policy, dict) or policy.get('thread_id') != thread_id
                    or type(policy.get('enabled')) is not bool or type(policy.get('dry_run')) is not bool):
                    raise ValueError('Invalid original-thread recovery policy')
                if policy['enabled']:
                    from backend.core.codex.research_recovery_worker import dispatch_codex_research_recovery
                    recovery = dispatch_codex_research_recovery(thread_id,
                        owner_authorization=policy['owner_authorization'], prompt=policy['prompt'],
                        dry_run=policy['dry_run'], state_dir=(Path(state_dir) / 'recovery'
                            if state_dir is not None else None), observation_timeout_seconds=timeout_seconds,
                        observation=observation['snapshot'], stop_event=stop_event)
            result = {'thread_id': thread_id, 'checked_at': time.time(), 'source_errors': errors,
                      'authority': observation['snapshot'].get('authority'),
                      'failure_timeline': observation['snapshot'].get('failure_timeline'),
                      'deferred_failures': observation['deferred_failures'], 'handoff': transfer, 'recovery': recovery,
                      'error': ('source_observation_unavailable' if errors or unknown else
                                'outbox_not_admitted' if transfer and transfer['pending'] else
                                'recovery_manual_review' if recovery and (recovery['status'] in
                                    {'manual_review', 'worker_lost', 'uncertain'} or
                                    (recovery.get('result') or {}).get('status') == 'manual_review') else None)}
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
              'recovery_execution_contract': 'independent-process-v1',
              'observer_thread_name': threading.current_thread().name, 'results': results, 'failures': failures,
              'registrations': [{'thread_id': row['thread_id'], 'enabled': row['enabled'],
                                 'next_check_at': row.get('next_check_at'), 'last_result': row.get('last_result')}
                                for row in registry.values()],
              'error': 'research supervision has unresolved errors' if failures else None,
              'notification_sent': False, 'recovery_performed': any(
                  (row.get('recovery') or {}).get('recovery_performed', False) for row in results)}
    store.set_meta('codex_research_supervision_worker', health)
    return health
