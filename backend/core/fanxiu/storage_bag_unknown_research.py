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
            owner = state.get('owner_purposes', {}).get(str(base))
            if owner:
                item.update(purpose_status=owner['purpose'], owner_evidence=owner['evidence'])
                if item.get('operation_supported') or item.get('classification'):
                    item['status'] = 'resolved'
            discovered.append(signature)
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
        receipt = record_codex_stage_result(thread_id, 'storage-purpose:' + signature,
            f"储物袋「{item['name']}」用途仍需确认：{item['conclusion']}。先保留该道具，"
            '请确认留给资源榜/祈愿，或按已有使用规则处理。', store=store)
        item['confirmation_outbox'] = receipt
        return receipt


def sync_storage_bag_unknown_research(atlas, snapshot, *, session, path=None):
    """Formal backpack discovery hook. Queue read-only work without a Game Cell.

    Read current persisted structural rules through the business model. This
    never synchronizes the cumulative catalog as if all historic items existed.
    Research failure does not roll back or falsely complete ordinary bag work.
    """
    from sqlmodel import select
    from backend.models import FanxiuPacketBusinessRecord
    from backend.core.fanxiu.storage_bag_prayer_research import BOX_RULE_DOMAIN
    from backend.core.jobs.executor import background_task_queue
    rows = session.exec(select(FanxiuPacketBusinessRecord).where(
        FanxiuPacketBusinessRecord.domain == BOX_RULE_DOMAIN)).all()
    rules = {str(row.record_key): row.payload for row in rows}
    signatures = discover_storage_bag_unknown_items(atlas, snapshot, rules=rules, path=path)
    state = read_storage_bag_unknown_research(path=path)
    for signature in signatures:
        if state['items'][signature]['status'] == 'resolved':
            continue
        if (state['items'][signature]['status'] == 'awaiting_owner'
                and state.get('confirmation_thread_id')):
            queue_storage_bag_purpose_confirmation(signature,
                thread_id=state['confirmation_thread_id'], path=path)
            continue
        background_task_queue.enqueue_once('storage-unknown:' + signature,
            dispatch_storage_bag_unknown_research, signature, path=path,
            resource_lock='resource:codex-cli', metadata={'signature': signature, 'mode': 'read_only_research'})
    return signatures


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
        items.append(row)
    return {**atlas, 'items': items}


def configure_storage_bag_owner_purpose(*, base_id: int, purpose: str, owner_evidence: str, path=None):
    """Persist an explicit stable owner policy across drops/configuration revisions.

    Owner policy settles purpose only; it cannot explain an unknown operation
    template or enable auto_claim. Use this for confirmed ordinary non-prayer
    boxes whose complete random pool is not a prerequisite to the owner's choice.
    """
    if type(base_id) is not int or base_id <= 0 or not owner_evidence.strip():
        raise ValueError('Stable item ID and explicit owner evidence required')
    if purpose not in {'reserve', 'independent_lifecycle', 'use_existing_policy'}:
        raise ValueError('Unsupported owner purpose')
    with _edit(path) as state:
        policy = {'purpose': purpose, 'evidence': owner_evidence.strip()}
        state.setdefault('owner_purposes', {})[str(base_id)] = policy
        for item in state['items'].values():
            if item['base_id'] == base_id:
                item.update(purpose_status=purpose, owner_evidence=owner_evidence.strip(), status='resolved')
                if purpose == 'use_existing_policy' and not (item.get('operation_supported') or item.get('classification')):
                    item['status'] = 'discovered'
    return policy
