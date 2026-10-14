"""Generic intake + independent remote sessions, bounded concurrent execution.

The single hook worker owns local transactions; remote turns execute in
parallel. Persist POST intent first and reconcile ambiguous acceptance through
public reads. Cancellation is a queued directive because the current remote
protocol cannot interrupt a running turn; never claim it already stopped.
"""
from __future__ import annotations
import json
import time
import uuid
from backend.core.messaging.wechat_delivery import prepare_reply, deliver_reply
from backend.core.messaging.wechat_device_agent import INSTRUCTION
from backend.core.messaging.wechat_content import is_image_message
from backend.core.messaging.wechat_task_routing import classify_message


def process_task_relay(service, hook, client, *, classifier=classify_message) -> bool:
    """One bounded scheduler tick; one failed task never stops another lane."""
    key, store = hook['key'], service.store
    if service.last_error or (service.workers and service.last_poll_at is None):
        return False
    if store.uncertain_replies(key):
        if not service.recover_delivery_for_new_questions(hook):
            return False
    progress = False
    tasks = store.conversation_tasks(key)
    selectable = [t for t in tasks if t['kind'] != 'clarify'][-32:]
    for message in store.unassigned_task_inputs(key):
        history = [m for m in store.messages(key, max(0, message['seq'] - 40), limit=50)
                   if m['seq'] < message['seq']]
        # Images carry context but never execution authority. Bind only when
        # textual context makes a task reference explicit; otherwise clarify.
        if is_image_message(message):
            decision = {'action': 'clarify', 'task_id': None, 'explicit': False}
        else:
            decision = classifier(message, selectable, history, hook)
        store.assign_task_input(key, message, decision)
        tasks = store.conversation_tasks(key)
        selectable = [t for t in tasks if t['kind'] != 'clarify'][-32:]
        progress = True
    tasks = store.conversation_tasks(key)
    active = sum(bool(t.get('request')) for t in tasks)
    for task in tasks:
        if service.stop_event.is_set():
            break
        if task.get('retry_at', 0) > time.time():
            continue
        inputs = store.task_inputs(key, task['id'])
        if not inputs:
            if task.get('request') and task['request'].get('candidate'):
                task.update(request=None, status='completed', last_error=None)
                store.update_conversation_task(key, task)
                active -= 1
            continue
        if not task.get('request') and active >= hook.get('task_concurrency', 3) and task['kind'] != 'clarify':
            continue
        was_active = bool(task.get('request'))
        try:
            progress = advance_task(service, hook, client, task, inputs) or progress
        except Exception as exc:
            # Preserve exact request intent on all failures; no automatic POST replay.
            task.update(last_error=str(exc)[:500], retry_at=time.time() + 30)
            store.update_conversation_task(key, task)
        active += int(bool(task.get('request'))) - int(was_active)
    return progress


def advance_task(service, hook, client, task, messages) -> bool:
    from backend.core.messaging.wechat_agent import OWNER_ACCOUNT, model_message
    store, key = service.store, hook['key']
    target = ('lingnet:' + hook['lingnet_peer_id'] if hook['processor'] == 'lingnet' else hook['device_entry_id'])
    if task.get('target') and task['target'] != target:
        raise ValueError('Task target cannot change')
    request = task.get('request')
    if task['kind'] == 'clarify':
        text = '请说明是交给机器人的新任务，还是补充、暂停哪条已有任务；多个任务请分条发送。'
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
        newer = [m for m in messages if m['seq'] > max(request['seqs'])]
        if not newer:
            if candidate['action'] == 'defer':
                task['status'] = 'waiting'
                store.update_conversation_task(key, task)
                return False
            if candidate['action'] == 'ignore':
                store.resolve_routed_messages(key, request['seqs'])
                task.update(request=None, status='completed', last_error=None)
                store.update_conversation_task(key, task)
                return True
            # Partial results cannot silently consume other assigned questions.
            if set(candidate['question_seqs']) != {m['seq'] for m in messages}:
                raise ValueError('任务答复未覆盖本轮全部消息')
            return send_task_answer(service, hook, task, messages, candidate['text'])
        # A correction/cancel in this lane suppresses its stale answer; other
        # lanes cannot cause this branch. Remote history retains executed facts.
    client.validate_target()
    identity = uuid.uuid4().hex
    title = f"WeChat {hook.get('name', key)} task:{task['id']} request:{identity}"
    instruction = INSTRUCTION + ('本轮消息已由本地分流关联到同一任务。intake.action=cancel 表示停止/暂停请求；'
        '当前协议无法中断已运行的操作，必须如实说明何时停止，禁止把排队中的取消声称为已停止。'
        '任务参与者不必是所有者，普通对话可以处理；工程与私有资料权限仍按verified_owner核验。')
    payload = {'requester': {'kind': 'device', 'id': 'wechat-relay:' + key, 'display_name': '微信任务中转'},
               'request_type': 'delegate', 'instruction': instruction,
               'context': {'relay_request_id': identity, 'task_id': task['id'],
                   'group': {'name': hook.get('name'), 'description': hook.get('description'), 'chat_id': hook['chat_id']},
                   'messages': [{**model_message(m), 'intake': m['intake'],
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


def send_task_answer(service, hook, task, messages, text) -> bool:
    # A stable short reference makes simultaneous replies intelligible in a group.
    text = f"[任务 {task['id'][:8]}] " + text
    plan = prepare_reply(text, messages, private=False, limit=service.config.get('reply_max_chars', 800))
    if any(kind != 'text' for kind, _ in plan.parts):
        raise ValueError('任务中转仅支持纯文本答复')
    seqs = [m['seq'] for m in messages]
    plan.groups[0]['question_seqs'], plan.question_seqs = seqs, set(seqs)
    result = deliver_reply(service.store, hook, max(seqs), text, plan,
                           task_id=task['id'], sender=service.sender, stop_event=service.stop_event)
    if result != 'sent':
        return False
    task.update(request=None, status='completed', last_error=None, retry_at=0)
    service.store.update_conversation_task(hook['key'], task)
    return True
