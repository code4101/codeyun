"""Generic intake + independent remote sessions, bounded concurrent execution.

The single hook worker owns local transactions; remote turns execute in
parallel. Persist POST intent first and reconcile ambiguous acceptance through
public reads. Cancellation is a queued directive because the current remote
protocol cannot interrupt a running turn; never claim it already stopped.
"""
from __future__ import annotations
import json
import re
import time
import uuid
from backend.core.messaging.wechat_delivery import prepare_reply, deliver_reply
from backend.core.messaging.wechat_device_agent import INSTRUCTION
from backend.core.messaging.wechat_content import is_image_message
from backend.core.messaging.wechat_task_routing import classify_task_plan
from backend.core.messaging.wechat_wake_policy import is_hard


def routing_candidates(tasks):
    """Running/waiting work takes precedence over recently completed history."""
    actionable = [t for t in tasks if t['kind'] != 'clarify']
    unfinished = [t for t in actionable if t.get('request') or t['status'] != 'completed']
    completed = [t for t in actionable if t not in unfinished]
    selected = unfinished + completed[-max(0, 32 - len(unfinished)):] if len(unfinished) < 32 else unfinished
    ids = {t['id'] for t in selected}
    return [t for t in actionable if t['id'] in ids]


def process_task_relay(service, hook, client, *, classifier=classify_task_plan) -> bool:
    key, store = hook['key'], service.store
    if store.hook(key)['retry_at'] > time.time():
        return False
    store.update_hook(key, status='running')
    try:
        result = run_task_scheduler(service, hook, client, classifier=classifier)
        store.update_hook(key, last_error=None)
        return result
    finally:
        if store.hook(key)['status'] == 'running':
            store.update_hook(key, status='idle')


def run_task_scheduler(service, hook, client, *, classifier=classify_task_plan) -> bool:
    """One bounded scheduler tick; one failed task never stops another lane."""
    key, store = hook['key'], service.store
    if service.last_error or (service.workers and service.last_poll_at is None):
        return False
    if store.uncertain_replies(key):
        if not service.recover_delivery_for_new_questions(hook):
            return False
    progress = False
    tasks = store.conversation_tasks(key)
    selectable = routing_candidates(tasks)
    for message in store.unassigned_task_inputs(key):
        history = store.task_context_messages(key, message['seq'] - 1, limit=12)
        from backend.core.messaging.wechat_agent import model_message
        projected = model_message(message)
        reference_text = projected.get('text', '') + '\n' + (projected.get('quote') or {}).get('content', '')
        choices = list(selectable)
        missing_reference = False
        for reference in set(re.findall(r'(?<![0-9a-f])([0-9a-f]{8,32})(?![0-9a-f])', reference_text)):
            referenced = store.conversation_task_reference(key, reference)
            if referenced is None and re.search(r'任务\s*' + re.escape(reference), reference_text):
                missing_reference = True
            if referenced and referenced['kind'] != 'clarify' and referenced['id'] not in {t['id'] for t in choices}:
                choices.append(referenced)
        # Images carry context but never execution authority. Bind only when
        # textual context makes a task reference explicit; otherwise clarify.
        if missing_reference:
            decision = {'action': 'clarify', 'task_id': None, 'explicit': is_hard(message, hook)}
        elif is_image_message(message):
            explicit = is_hard(message, hook)
            decision = {'action': 'clarify' if explicit else 'ignore', 'task_id': None, 'explicit': explicit}
        else:
            decision = classifier(message, choices, history, hook)
        store.assign_task_plan(key, message, decision if isinstance(decision,list) else [decision])
        tasks = store.conversation_tasks(key)
        selectable = routing_candidates(tasks)
        progress = True
    tasks = store.conversation_tasks(key)
    def is_active(t):
        return bool(t.get('request')) and t['status'] == 'running'
    active = sum(is_active(t) for t in tasks)
    for task in tasks:
        if service.stop_event.is_set():
            break
        if task.get('retry_at', 0) > time.time():
            continue
        inputs = store.task_inputs(key, task['id'])
        if not inputs:
            if task.get('request') and task['request'].get('candidate'):
                was_active = is_active(task)
                task.update(request=None, status='completed', last_error=None)
                store.update_conversation_task(key, task)
                active -= int(was_active)
            continue
        if not task.get('request') and active >= hook.get('task_concurrency', 3) and task['kind'] != 'clarify':
            continue
        was_active = is_active(task)
        try:
            progress = advance_task(service, hook, client, task, inputs,
                                    may_submit=active - int(was_active) < hook.get('task_concurrency', 3)) or progress
        except Exception as exc:
            # Preserve exact request intent on all failures; no automatic POST replay.
            task.update(last_error=str(exc)[:500], retry_at=time.time() + 30)
            store.update_conversation_task(key, task)
        active += int(is_active(task)) - int(was_active)
    return progress


def advance_task(service, hook, client, task, messages, *, may_submit=True) -> bool:
    from backend.core.messaging.wechat_agent import OWNER_ACCOUNT, model_message
    store, key = service.store, hook['key']
    target = ('lingnet:' + hook['lingnet_peer_id'] if hook['processor'] == 'lingnet' else hook['device_entry_id'])
    if task.get('target') and task['target'] != target:
        raise ValueError('Task target cannot change')
    request = task.get('request')
    if task['kind'] == 'clarify':
        text = '请说明需要补充、暂停哪条任务，或具体交给机器人的新需求。'
        if all(is_image_message(m) for m in messages):
            text = '请用文字说明这张图片需要我做什么，以及对应哪条任务。'
        return send_task_answer(service, hook, task, messages, text)
    if request and not request.get('turn_id'):
        if task.get('session_id'):
            sessions = [client.request('GET', '/sessions/' + task['session_id'])]
        else:
            sessions = client.request('GET', '/sessions', params={'limit': 100})['items']
            sessions = [client.request('GET', '/sessions/' + s['id']) for s in sessions if s['title'] == request['title']]
        matches = [(s, t) for s in sessions for t in s.get('turns', [])
                   if t.get('context', {}).get('relay_request_id') == request['id']]
        if len(matches) != 1:
            raise RuntimeError('远端受理状态未确认，保留任务，不重发')
        parent, turn = matches[0]
        task['session_id'], request['turn_id'] = parent['id'], turn['id']
        store.update_conversation_task(key, task)
    if request and not request.get('candidate'):
        turn = client.request('GET', '/turns/' + request['turn_id'])
        if turn['status'] in {'pending', 'running'}:
            return False
        if turn['status'] != 'completed':
            task['status'] = 'failed'
            raise RuntimeError('远端执行失败，任务保留，不自动重放')
        task['status'] = 'waiting'
        candidate = json.loads((turn.get('result_report') or {}).get('final_message') or '{}')
        if (candidate.get('action') not in {'reply', 'ignore', 'defer'}
                or not isinstance(candidate.get('text'), str)
                or not isinstance(candidate.get('question_seqs'), list)
                or any(type(s) is not int or s not in request['seqs'] for s in candidate['question_seqs'])
                or (candidate['action'] == 'reply' and (not candidate['text'].strip() or not candidate['question_seqs']))):
            raise ValueError('远端任务答复格式无效，保留结果')
        request['candidate'] = candidate
        store.update_conversation_task(key, task)
    if request and request.get('candidate'):
        candidate = request['candidate']
        if candidate['action']=='reply' and not set(candidate['question_seqs']) & {m['seq'] for m in messages}:
            # Delivery committed before the task progress write; the lane's
            # receipt already owns completion. Retain only unanswered inputs.
            task.update(request=None,status='queued',last_error=None)
            store.update_conversation_task(key,task)
            return True
        newer = [m for m in messages if m['seq'] not in request['seqs']]
        if not newer:
            if candidate['action'] == 'defer':
                task['status'] = 'waiting'
                store.update_conversation_task(key, task)
                return False
            if candidate['action'] == 'ignore':
                store.resolve_task_inputs(key, task['id'], request['seqs'])
                task.update(request=None, status='completed', last_error=None)
                store.update_conversation_task(key, task)
                return True
            return send_task_answer(service, hook, task, messages, candidate['text'],
                                    covered_seqs=candidate['question_seqs'])
        # A correction/cancel in this lane suppresses its stale answer; other
        # lanes cannot cause this branch. Remote history retains executed facts.
    if not may_submit:
        store.update_conversation_task(key, task)
        return False
    client.validate_target()
    identity = uuid.uuid4().hex
    title = f"WeChat {hook.get('name', key)} task:{task['id']} request:{identity}"
    instruction = INSTRUCTION + ('本轮消息已由本地分流关联到同一任务。intake.action=cancel 表示停止/暂停请求；'
        'text是分配给当前task_id的原文子任务，original_text仅为共享背景，禁止替其他task_id执行操作。'
        '当前协议无法中断已运行的操作，必须如实说明何时停止，禁止把排队中的取消声称为已停止。'
        '任务参与者不必是所有者，普通对话可以处理；工程与私有资料权限仍按verified_owner核验。')
    payload = {'requester': {'kind': 'device', 'id': 'wechat-relay:' + key, 'display_name': '微信任务中转'},
               'request_type': 'delegate', 'instruction': instruction,
               'context': {'relay_request_id': identity, 'task_id': task['id'],
                   'group': {'name': hook.get('name'), 'description': hook.get('description'), 'chat_id': hook['chat_id']},
                   'messages': [{**model_message(m), 'text':m['assigned_text'], 'original_text':model_message(m)['text'], 'intake': m['intake'],
                                 'verified_owner': m['sender_id'] == OWNER_ACCOUNT} for m in messages]}}
    task.update(target=target, status='running', retry_at=0, last_error=None,
                request={'id': identity, 'title': title, 'seqs': [m['seq'] for m in messages]})
    store.update_conversation_task(key, task)
    if task.get('session_id'):
        result = client.request('POST', '/sessions/' + task['session_id'] + '/turns', json=payload)
        task['request']['turn_id'] = result['id']
    else:
        result = client.request('POST', '/sessions', json={**payload, 'title': title})
        task['session_id'], task['request']['turn_id'] = result['id'], result['last_turn_id']
    store.update_conversation_task(key, task)
    return True


def send_task_answer(service, hook, task, messages, text, *, covered_seqs=None) -> bool:
    # A stable short reference makes simultaneous replies intelligible in a group.
    text = f"[任务 {task['id'][:8]}] " + text
    plan = prepare_reply(text, messages, private=False, limit=service.config.get('reply_max_chars', 800))
    if any(kind != 'text' for kind, _ in plan.parts):
        raise ValueError('任务中转仅支持纯文本答复')
    reviewed = [m['seq'] for m in messages]
    seqs = list(covered_seqs) if covered_seqs is not None else reviewed
    plan.groups[0]['question_seqs'], plan.question_seqs = seqs, set(seqs)
    result = deliver_reply(service.store, hook, max(seqs), text, plan,
                           task_id=task['id'],task_reviewed_seqs=reviewed, sender=service.sender, stop_event=service.stop_event)
    if result != 'sent':
        return False
    remaining = service.store.task_inputs(hook['key'],task['id'])
    task.update(request=None, status='queued' if remaining else 'completed', last_error=None, retry_at=0, last_reply=text[-1600:])
    service.store.update_conversation_task(hook['key'], task)
    return True
