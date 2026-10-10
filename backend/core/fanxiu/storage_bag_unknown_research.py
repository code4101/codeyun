"""Current-stock unknown discovery and durable, read-only research workflow.

This ledger owns investigation/confirmation, never auto_claim or game actions.
Catalog history is not current stock. A resolved purpose is a rule input, not
consumption authorization; existing selection, activity and GUI gates still apply.
"""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
from pathlib import Path
import time
from typing import Mapping

from filelock import FileLock
from backend.core.fanxiu.data_annotation.state import read_data_annotation_json, write_data_annotation_json
from backend.core.fanxiu.storage_bag_usage import storage_bag_analysis_fingerprint

VERSION = 1

# Durable wait reason for an awaiting_owner item whose confirmation route is not
# bound yet. This is a solvable configuration gap, never a research dispatch.
WAITING_CONFIRMATION_ROUTE = 'confirmation_route_unconfigured'
CONFIGURE_CONFIRMATION_ROUTE = 'configure_storage_bag_research_confirmation'


def _path(path):
    if path is not None:
        return Path(path)
    from backend.core.fanxiu.behavior_tree.kernel_scheduler import fanxiu_kernel_scheduler_settings_path
    return fanxiu_kernel_scheduler_settings_path().with_name('storage_bag_unknown_research.json')


@contextmanager
def _edit(path=None):
    path = _path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + '.lock', timeout=30):
        state = read_storage_bag_unknown_research(path=path)
        yield state
        write_data_annotation_json(path, state)


def read_storage_bag_unknown_research(*, path=None):
    """Read durable rules/requests; no Runtime, launch or write side effects."""
    return read_data_annotation_json(_path(path), {'version': VERSION, 'items': {}})


def read_storage_bag_unknown_research_status(*, path=None):
    """Read formal discovery/sync evidence without inventing a current inventory.

    Old ledgers without these receipts remain unverified. Enqueued research is
    not concluded research, and outbox acceptance is not an archived answer.
    """
    state = read_storage_bag_unknown_research(path=path)
    counts = {}
    for item in state['items'].values():
        status = item.get('status', 'unknown')
        counts[status] = counts.get(status, 0) + 1
    discovery = state.get('last_discovery')
    sync = state.get('last_sync')
    diagnostics = (sync or {}).get('diagnostics')
    signatures = (sync or {}).get('signatures')
    valid_diagnostics = (isinstance(diagnostics, dict) and isinstance(signatures, list)
                         and all(isinstance(signature, str) and signature for signature in signatures)
                         and len(signatures) == len(set(signatures))
                         and set(diagnostics) == set(signatures)
                         and all(isinstance(detail, dict) and detail.get('action') in {
                             'skip', 'awaiting_confirmation_route', 'queue_confirmation',
                             'dispatch_enqueued', 'dispatch_already_active'}
                             and isinstance(detail.get('reason'), str) and detail['reason']
                             for detail in diagnostics.values()))
    matched = bool(discovery and sync and
                   sync.get('record_kind') == 'formal_unknown_sync' and valid_diagnostics and
                   sync.get('inventory_observation') == discovery.get('inventory_observation')
                   and sync.get('signatures') == discovery.get('signatures'))
    observation = (discovery or {}).get('inventory_observation') or {}
    identity_bound = all(type(observation.get(key)) is int and observation[key] > 0
                         for key in ('pid', 'process_start_ticks'))
    return {'read_only': True, 'item_count': len(state['items']), 'status_counts': counts,
            'last_discovery': discovery, 'last_sync': sync, 'latest_sync_evidence_complete': matched,
            'inventory_process_identity_bound': identity_bound,
            'confirmation_thread_id': state.get('confirmation_thread_id'),
            'formal_policy_plan_acceptance': 'requires_formal_job_receipt',
            'confirmation_archive_acceptance': 'requires_engineering_archive_receipt'}


def _inventory_fingerprint(snapshot):
    return sha256(json.dumps(sorted(
        (int(i['base_id']), int(i.get('num') or 0)) for i in snapshot['items']
        if not i.get('is_padding'))).encode()).hexdigest()


def _inventory_observation(snapshot):
    """Retain the Runtime provider's evidence identity, without historic backfill.

    Legacy top-level identities remain readable, but conflicting projections
    are rejected rather than silently binding an observation to another process.
    Missing identity stays explicit and cannot prove cross-process continuity.
    """
    evidence = snapshot.get('evidence')
    evidence = evidence if isinstance(evidence, Mapping) else {}
    identity = {}
    for key in ('pid', 'process_start_ticks'):
        nested, legacy = evidence.get(key), snapshot.get(key)
        if nested is not None and legacy is not None and nested != legacy:
            raise ValueError(f'Conflicting inventory process identity: {key}')
        identity[key] = nested if nested is not None else legacy
    return {'captured_at_epoch': snapshot['captured_at_epoch'], 'source': snapshot['source'],
            'complete': snapshot['complete'],
            'current_type_count': len({int(i['base_id']) for i in snapshot['items']
                                      if not i.get('is_padding') and int(i.get('num') or 0) > 0}),
            **identity,
            'inventory_fingerprint': _inventory_fingerprint(snapshot)}


def discover_storage_bag_unknown_items(atlas: Mapping, snapshot: Mapping, *, rules: Mapping,
                                       path=None, now=None):
    """Explicitly sync unknowns from a fresh full active backpack, selected or not.

    Identity binds catalog and reward-rule versions, excluding quantities,
    instances and observation timestamps. Older identities retain their audit.
    Unsupported templates or incomplete known reward graphs warrant research.
    Missing rules alone do not re-open every already-supported ordinary item.
    """
    now = time.time() if now is None else now
    observed = snapshot.get('captured_at_epoch')
    if (snapshot.get('complete') is not True
            or snapshot.get('source') != 'active_backpack_panel_item_info_list'
            or not isinstance(observed, (float, int)) or not 0 <= now - observed <= 120
            or not isinstance(snapshot.get('items'), list)):
        raise ValueError('Unknown discovery requires a fresh complete active backpack snapshot')
    current = {int(i['base_id']) for i in snapshot['items']
               if not i.get('is_padding') and int(i.get('num') or 0) > 0}
    observation = _inventory_observation(snapshot)
    discovered = []
    with _edit(path) as state:
        for row in atlas.get('items', []):
            base = int(row.get('base_id') or 0)
            if base not in current:
                continue
            rule = rules.get(str(base), rules.get(base, {}))
            incomplete = (rule.get('classification_complete') is False
                          or ((row.get('item') or {}).get('metadata_only') is True
                              and (row.get('item') or {}).get('type') in {2, 21}
                              and rule.get('classification_complete') is not True))
            if row.get('operation_template') != 'unsupported' and not incomplete:
                continue
            identity = {'version': VERSION, 'base_id': base,
                        'catalog': storage_bag_analysis_fingerprint(row),
                        'rule': rule.get('catalog_fingerprint', ''),
                        'classification_complete': rule.get('classification_complete'),
                        'categories': sorted(rule.get('resource_categories') or []),
                        'operation_kind': rule.get('operation_kind'),
                        'unresolved': sorted(rule.get('unresolved_item_ids') or [])}
            signature = sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
            item = state['items'].setdefault(signature, {
                'signature': signature, 'base_id': base, 'identity': identity,
                'name': (row.get('item') or {}).get('name', row.get('name', '')),
                'classification_complete': bool(rule.get('classification_complete'))
                    if rule else not incomplete and row.get('operation_template') != 'unsupported',
                'operation_supported': row.get('operation_template') != 'unsupported',
                'purpose_status': 'unknown', 'status': 'discovered', 'first_seen': now,
                'evidence': {'catalog': row.get('item') or {}, 'rule': dict(rule)},
            })
            item['last_seen'] = now
            if observed >= (item.get('last_inventory_observation') or {}).get('captured_at_epoch', 0):
                item['last_inventory_observation'] = dict(observation)
            from backend.core.fanxiu.storage_bag_catalog_purpose import (
                classify_storage_bag_catalog_purpose, storage_bag_catalog_purpose_fingerprint,
            )
            card = row.get('item') or {}
            finding = state.get('catalog_findings', {}).get(storage_bag_catalog_purpose_fingerprint(card)) if card.get('id') else None
            if not finding and card.get('id') and item.get('purpose_status') == 'unknown':
                # New native catalog cards need no AI when the existing pure
                # classifier proves a non-consuming retention/lifecycle route.
                # Incomplete candidates and owner questions still follow the
                # research/confirmation workflow; owner rules below win last.
                candidate = classify_storage_bag_catalog_purpose(card, reward_rule=rule)
                if (candidate.get('purpose_complete') is True
                        and candidate.get('purpose_status') in {'reserve', 'independent_lifecycle'}):
                    finding = candidate
            if finding and finding['purpose_status'] in {'reserve', 'independent_lifecycle'}:
                item.update(purpose_status=finding['purpose_status'], status='resolved',
                            conclusion=finding['evidence'], catalog_route=finding['route'])
            elif finding and finding['purpose_status'] == 'await_owner':
                item.update(purpose_status='await_owner', status='awaiting_owner',
                            conclusion=finding['evidence'], catalog_fingerprint=finding['fingerprint'])
            owner = state.get('owner_purposes', {}).get(str(base))
            if owner:
                item.update(purpose_status=owner['purpose'], owner_evidence=owner['evidence'])
                # Owner purpose overrides catalog suggestions on every fresh
                # discovery. Retention needs no operation adapter; permission
                # to use still needs supported execution semantics.
                item['status'] = ('discovered'
                    if owner['purpose'] == 'use_existing_policy'
                    and not (item.get('operation_supported') or item.get('classification'))
                    else 'resolved')
            discovered.append(signature)
        previous = state.get('last_discovery') or {}
        if observed >= (previous.get('inventory_observation') or {}).get('captured_at_epoch', 0):
            state['last_discovery'] = {'recorded_at': now, 'inventory_observation': observation,
                                      'signatures': list(discovered)}
    return discovered


def dispatch_storage_bag_unknown_research(signature: str, *, path=None, now=None):
    """Serialize launch/retry across processes. Suppression is retryable after 60s.

    A lost completion may retry after six hours; the shared assistance provider
    additionally owns the live-agent gate. No low-level dispatch is duplicated.
    """
    from backend.core.codex import CodexEscalationRequest
    from backend.core.fanxiu.data_annotation.ai_assistance import request_ai_assistance
    now = time.time() if now is None else now
    with _edit(path) as state:
        item = state['items'][signature]
        if item['status'] in {'resolved', 'awaiting_owner'}:
            return dict(item)
        retry_at = float(item.get('retry_at') or 0)
        if now < retry_at:
            return dict(item)
        item.update(status='requested', retry_at=now + 60)
        # Persist reservation before the external launch, including crash recovery.
        write_data_annotation_json(_path(path), state)
        try:
            dispatch = request_ai_assistance(CodexEscalationRequest(
                title=f'凡修储物袋只读未知研究：{signature}',
                problem='调查以下道具的身份、分类和用途。复用已保存同源证据，不重复扫描。\n'
                    + json.dumps(item, ensure_ascii=False, default=str),
                objective='通过 storage_bag_unknown_research.record_storage_bag_research_conclusion '
                          '保存权威证据；用途不确定则 await_owner。不要创建游戏 Cell。',
                constraints=('不得改变 auto_claim、消费授权、游戏运行权或调度。',),
                completion_criteria=('领域接口回读结论，缺失证据明确保留；不以 agent 退出视为解决。',),
            ), mode='read_only_research')
        except Exception as exc:
            item.update(status='failed', error=f'{type(exc).__name__}: {exc}', retry_at=now + 300)
        else:
            item.update(status='dispatched' if dispatch else 'suppressed',
                        dispatch=dispatch.model_dump() if dispatch else None,
                        retry_at=now + (21600 if dispatch else 60))
        return dict(item)


def record_storage_bag_research_conclusion(signature, *, evidence: str, purpose: str,
                                           classification_complete: bool, classification=None, path=None):
    """Save read-only evidence. Unknown purpose must be confirmed by the owner.

    Known reserve/independent lifecycle conclusions never grant consumption.
    Use-existing-policy needs owner confirmation even if the classification is known.
    """
    if purpose not in {'await_owner', 'reserve', 'independent_lifecycle'} or not evidence.strip():
        raise ValueError('Research must supply evidence and a non-consuming purpose')
    if classification is not None:
        from backend.core.fanxiu.storage_bag_usage import STORAGE_BAG_OPERATION_TEMPLATES, STORAGE_BAG_YIELD_MODES
        if (not classification_complete
                or classification.get('operation_template') not in STORAGE_BAG_OPERATION_TEMPLATES - {'unsupported'}
                or classification.get('yield_mode') not in STORAGE_BAG_YIELD_MODES):
            raise ValueError('Structured classification requires complete evidence and supported semantics')
        classification = {k: classification[k] for k in ('operation_template', 'yield_mode')}
    with _edit(path) as state:
        item = state['items'][signature]
        if item.get('owner_evidence'):
            if classification:
                item.update(classification=classification, classification_complete=True,
                            conclusion=evidence.strip(), status='resolved')
            return dict(item)  # A late researcher cannot reverse an owner decision.
        item.update(conclusion=evidence.strip(), classification_complete=bool(classification_complete),
                    classification=classification, purpose_status=purpose,
                    status='awaiting_owner' if purpose == 'await_owner' else 'resolved')
        if purpose == 'await_owner':
            from backend.core.fanxiu.storage_bag_catalog_purpose import storage_bag_catalog_purpose_fingerprint
            card = item.get('evidence', {}).get('catalog', {})
            if card.get('id'):
                item['catalog_fingerprint'] = storage_bag_catalog_purpose_fingerprint(card)
        result = dict(item)
        thread_id = state.get('confirmation_thread_id')
    if purpose == 'await_owner' and thread_id:
        queue_storage_bag_purpose_confirmation(signature, thread_id=thread_id, path=path)
    return result


def confirm_storage_bag_item_purpose(signature, *, purpose: str, owner_evidence: str, path=None):
    """Explicit owner instruction, bound to this identity, resolves purpose only.

    This does not select/enable an item. An explicit owner decision can settle
    purpose without a complete reward pool, e.g. a confirmed non-prayer box.
    """
    if purpose not in {'reserve', 'independent_lifecycle', 'use_existing_policy'} or not owner_evidence.strip():
        raise ValueError('Explicit owner instruction and supported purpose required')
    with _edit(path) as state:
        item = state['items'][signature]
        item.update(purpose_status=purpose, status='resolved', owner_evidence=owner_evidence.strip())
        if purpose == 'use_existing_policy' and not (item.get('operation_supported') or item.get('classification')):
            item['status'] = 'discovered'
        return dict(item)


def queue_storage_bag_purpose_confirmation(signature, *, thread_id: str, path=None, store=None):
    """Queue one required decision in the existing engineering outbox; never send."""
    from backend.core.messaging.codex_results import record_codex_stage_result
    with _edit(path) as state:
        item = state['items'][signature]
        if item['status'] != 'awaiting_owner':
            raise ValueError('No pending purpose confirmation')
        if _catalog_confirmation_covers(state, item):
            receipt = {'outbox_accepted': True, 'already_covered': True, 'scope': 'catalog_only'}
            item['confirmation_outbox'] = receipt
            return receipt
        receipt = record_codex_stage_result(thread_id, 'storage-purpose:' + signature,
            f"储物袋「{item['name']}」用途仍需确认：{item['conclusion']}。先保留该道具，"
            '请确认留给资源榜/祈愿，或按已有使用规则处理。', store=store)
        item['confirmation_outbox'] = receipt
        return receipt


class StorageBagUnknownSyncReport(list):
    """Discovered signatures plus structured per-signature sync diagnostics.

    It still behaves as the original signature list so existing callers keep
    working; ``diagnostics`` and ``waiting`` add observability without any
    sending, consumption authorization or game side effect.
    """

    def __init__(self, signatures, diagnostics):
        super().__init__(signatures)
        self.diagnostics = dict(diagnostics)

    @property
    def waiting(self):
        """Signatures blocked on an unbound, explicitly solvable confirmation route."""
        return {signature: detail for signature, detail in self.diagnostics.items()
                if detail['action'] == 'awaiting_confirmation_route'}


def _catalog_confirmation_covers(state, item):
    catalog_key = item.get('catalog_fingerprint')
    if not catalog_key:
        return False
    findings = state.get('catalog_findings', {})
    current = findings.get(catalog_key, {})
    # Metadata provenance/default-field changes do not create a new ordinary
    # box policy question. Require a complete graph and identical purpose,
    # evidence and all reward names; choices and incomplete graphs stay bound
    # to their exact version. This only reuses the question, never its answer.
    def ordinary_semantics(finding):
        if (finding.get('route') != 'ordinary_box_candidate'
                or finding.get('purpose_status') != 'await_owner'
                or not finding.get('reward_graph_complete')
                or not finding.get('reward_names')):
            return None
        return {k: v for k, v in finding.items()
                if k not in {'fingerprint', 'source', 'scope'}}
    semantics = ordinary_semantics(current)
    for question in state.get('catalog_confirmations', {}).values():
        if not question.get('outbox_accepted'):
            continue
        covered = question.get('fingerprints', [])
        if catalog_key in covered:
            return True
        if semantics is not None and any(ordinary_semantics(findings.get(key, {})) == semantics
                                         for key in covered):
            return True
    return False


def plan_storage_bag_unknown_research_sync(state, signatures):
    """Pure sync plan: one structured action per discovered signature.

    Reads state only; performs no queue, dispatch or write. An awaiting_owner
    identity without a bound confirmation route becomes an explicit, solvable
    wait instead of an invalid read-only research dispatch. Bind the route via
    ``configure_storage_bag_research_confirmation`` to make it actionable.
    """
    plan = {}
    for signature in signatures:
        item = state['items'][signature]
        status = item['status']
        if status == 'resolved':
            plan[signature] = {'action': 'skip', 'reason': 'resolved'}
        elif status == 'awaiting_owner':
            if not state.get('confirmation_thread_id'):
                plan[signature] = {'action': 'awaiting_confirmation_route',
                    'reason': WAITING_CONFIRMATION_ROUTE,
                    'resolve_by': CONFIGURE_CONFIRMATION_ROUTE,
                    'purpose_status': item.get('purpose_status')}
            elif _catalog_confirmation_covers(state, item):
                plan[signature] = {'action': 'skip', 'reason': 'catalog_confirmation_already_queued'}
            else:
                plan[signature] = {'action': 'queue_confirmation', 'reason': 'awaiting_owner'}
        else:
            plan[signature] = {'action': 'dispatch_research', 'reason': status}
    return plan


def _apply_storage_bag_waiting_reasons(signatures, *, path=None):
    """Reconcile durable wait reasons against current state in one transaction.

    A stale sync snapshot may have planned a wait before the owner bound the
    confirmation route or before another writer resolved the item. Re-read the
    ledger under its lock and only mark identities that are still awaiting_owner
    with the route still unbound; every other identity has its stale reason
    cleared. This never dispatches, sends or changes any purpose.
    """
    if not signatures:
        return
    with _edit(path) as state:
        route_bound = bool(state.get('confirmation_thread_id'))
        for signature in signatures:
            item = state['items'].get(signature)
            if item is None:
                continue
            if item.get('status') == 'awaiting_owner' and not route_bound:
                item['waiting_reason'] = WAITING_CONFIRMATION_ROUTE
            else:
                item.pop('waiting_reason', None)


def sync_storage_bag_unknown_research(atlas, snapshot, *, session, path=None):
    """Formal backpack discovery hook. Queue read-only work without a Game Cell.

    Read current persisted structural rules through the business model. This
    never synchronizes the cumulative catalog as if all historic items existed.
    Research failure does not roll back or falsely complete ordinary bag work.
    An awaiting_owner identity without a bound confirmation route is recorded
    with an explicit waiting reason and never dispatched as invalid progress.
    """
    from sqlmodel import select
    from backend.models import FanxiuPacketBusinessRecord
    from backend.core.fanxiu.storage_bag_prayer_research import BOX_RULE_DOMAIN
    from backend.core.jobs.executor import background_task_queue
    rows = session.exec(select(FanxiuPacketBusinessRecord).where(
        FanxiuPacketBusinessRecord.domain == BOX_RULE_DOMAIN)).all()
    rules = {str(row.record_key): row.payload for row in rows}
    original_observation = _inventory_observation(snapshot)
    signatures = discover_storage_bag_unknown_items(atlas, snapshot, rules=rules, path=path)
    state = read_storage_bag_unknown_research(path=path)
    plan = plan_storage_bag_unknown_research_sync(state, signatures)
    diagnostics = {}
    for signature in signatures:
        entry = plan[signature]
        action = entry['action']
        if action == 'queue_confirmation':
            queue_storage_bag_purpose_confirmation(
                signature, thread_id=state['confirmation_thread_id'], path=path)
        elif action == 'dispatch_research':
            task_id, created = background_task_queue.enqueue_once('storage-unknown:' + signature,
                dispatch_storage_bag_unknown_research, signature, path=path,
                resource_lock='resource:codex-cli',
                metadata={'signature': signature, 'mode': 'read_only_research'})
            entry = {**entry, 'task_id': task_id,
                     'action': 'dispatch_enqueued' if created else 'dispatch_already_active'}
        diagnostics[signature] = entry
    _apply_storage_bag_waiting_reasons(signatures, path=path)
    # Keep the legacy list return while retaining the same formal observation's
    # disposition. Never bind an older concurrent sync to a newer discovery.
    with _edit(path) as latest:
        discovery = latest.get('last_discovery') or {}
        if (discovery.get('inventory_observation') == original_observation
                and discovery.get('signatures') == list(signatures)):
            latest['last_sync'] = {'record_kind': 'formal_unknown_sync', 'recorded_at': time.time(),
                                  'inventory_observation': original_observation,
                                  'signatures': list(signatures), 'diagnostics': diagnostics}
    return StorageBagUnknownSyncReport(signatures, diagnostics)


def configure_storage_bag_research_confirmation(*, thread_id: str, path=None, store=None):
    """Bind owner questions to an existing enabled engineering subscription."""
    from backend.core.messaging.codex_results import result_store
    store = store or result_store()
    if not any(s['thread_id'] == thread_id and s['enabled']
               for s in store.codex_notifications_status()['subscriptions']):
        raise ValueError('Confirmation needs an existing enabled engineering subscription')
    with _edit(path) as state:
        state['confirmation_thread_id'] = thread_id
    return {'thread_id': thread_id}


def project_storage_bag_research_policy(atlas, *, current_signatures, path=None):
    """Overlay a version-bound hold for only uncertain/reserved items.

    This projection never enables auto_claim. Resolved use-existing-policy
    removes this hold but leaves all pre-existing execution gates intact.
    """
    state = read_storage_bag_unknown_research(path=path)
    items = []
    for row in atlas.get('items', []):
        row = dict(row)
        from backend.core.fanxiu.storage_bag_catalog_purpose import storage_bag_catalog_purpose_fingerprint
        card = row.get('item') or {}
        finding = state.get('catalog_findings', {}).get(storage_bag_catalog_purpose_fingerprint(card)) if card.get('id') else None
        if finding and finding['purpose_status'] in {'unknown', 'await_owner', 'reserve', 'independent_lifecycle'}:
            row['research_hold'] = finding['purpose_status']
        owner = state.get('owner_purposes', {}).get(str(row.get('base_id')))
        if owner and owner['purpose'] == 'use_existing_policy':
            row.pop('research_hold', None)
        matching = [r for key, r in state['items'].items() if key in current_signatures
                    if r['base_id'] == row.get('base_id')
                    and r['identity']['catalog'] == storage_bag_analysis_fingerprint(row)]
        latest = max(matching, key=lambda r: r['last_seen'], default={})
        if latest.get('classification_complete') and latest.get('classification'):
            row.update(latest['classification'], analysis_status='classified',
                       analysis_fingerprint=storage_bag_analysis_fingerprint(row),
                       analysis_reason=latest.get('conclusion', ''))
        if latest.get('purpose_status') in {'unknown', 'await_owner', 'reserve', 'independent_lifecycle'}:
            row['research_hold'] = latest['purpose_status']
        elif latest.get('owner_evidence') and latest.get('purpose_status') == 'use_existing_policy':
            row.pop('research_hold', None)
        items.append(row)
    return {**atlas, 'items': items}


def configure_storage_bag_owner_purpose(*, base_id: int, purpose: str, owner_evidence: str,
                                      prayer_relevance: str | None = None, path=None):
    """Persist an explicit stable owner policy across drops/configuration revisions.

    Owner policy settles purpose only; it cannot explain an unknown operation
    template or enable auto_claim. Use this for confirmed ordinary non-prayer
    boxes whose complete random pool is not a prerequisite to the owner's choice.
    Explicit non_prayer relevance settles research scope, not reward-graph facts
    or consumption permission. A generic purpose never implies this classification.
    """
    if type(base_id) is not int or base_id <= 0 or not owner_evidence.strip():
        raise ValueError('Stable item ID and explicit owner evidence required')
    if purpose not in {'reserve', 'independent_lifecycle', 'use_existing_policy'}:
        raise ValueError('Unsupported owner purpose')
    if prayer_relevance not in {None, 'non_prayer'}:
        raise ValueError('Unsupported explicit prayer relevance')
    with _edit(path) as state:
        policy = {'purpose': purpose, 'evidence': owner_evidence.strip()}
        if prayer_relevance is not None:
            policy['prayer_relevance'] = prayer_relevance
        state.setdefault('owner_purposes', {})[str(base_id)] = policy
        for item in state['items'].values():
            if item['base_id'] == base_id:
                item.update(purpose_status=purpose, owner_evidence=owner_evidence.strip(), status='resolved')
                if purpose == 'use_existing_policy' and not (item.get('operation_supported') or item.get('classification')):
                    item['status'] = 'discovered'
    return policy


def sync_storage_bag_catalog_purpose_findings(cards, *, reward_rules=None, reward_cards=None, path=None):
    """Explicit catalog-only research, with no inventory/agent/notification side effects.

    Version-bound findings can be reused when a future fresh-stock discovery
    sees the same identity. This ledger bucket never populates current items.
    """
    from backend.core.fanxiu.storage_bag_catalog_purpose import classify_storage_bag_catalog_purpose
    findings = [classify_storage_bag_catalog_purpose(card,
        reward_rule=(reward_rules or {}).get(str(card['id'])), cards_by_id=reward_cards) for card in cards]
    with _edit(path) as state:
        bucket = state.setdefault('catalog_findings', {})
        for finding in findings:
            bucket[finding['fingerprint']] = finding
    return findings


def queue_storage_bag_catalog_confirmation(fingerprints, *, text: str, path=None, store=None):
    """Queue a catalog policy/choice question without inventing current inventory.

    Only findings already saved by the domain boundary may be referenced. The
    stable signature excludes quantity/time; retries use the engineering outbox.
    Answer application is explicit configure_storage_bag_owner_purpose, never
    an inferred auto_claim toggle or a Game Cell.
    """
    from backend.core.messaging.codex_results import record_codex_stage_result
    keys = sorted(set(fingerprints))
    if not keys or not text.strip():
        raise ValueError('Stored catalog identities and a required decision needed')
    with _edit(path) as state:
        findings = state.get('catalog_findings', {})
        if any(k not in findings for k in keys):
            raise ValueError('Unknown catalog finding')
        thread_id = state.get('confirmation_thread_id')
        if not thread_id:
            raise ValueError('Engineering confirmation subscription is not configured')
        signature = sha256(json.dumps(keys).encode()).hexdigest()
        receipt = record_codex_stage_result(thread_id, 'storage-catalog-purpose:' + signature, text, store=store)
        state.setdefault('catalog_confirmations', {})[signature] = {
            'fingerprints': keys, 'text': text, 'outbox_accepted': bool(receipt), 'scope': 'catalog_only'}
        return receipt
