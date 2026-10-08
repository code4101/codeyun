"""Opt-in, model-free recording of literal, present-day diary text.

The program preserves the owner's words; it does not summarize or infer events.
Corrections, historical dates, attachments and ambiguous batches stay with the AI
writer. All storage uses the versioned notes API and is verified before consumption.
"""
from datetime import datetime
from html import escape
import re
from zoneinfo import ZoneInfo

from backend.core.messaging.wechat_diary_model_routing import diary_model_profile
from backend.core.messaging.wechat_diary_model_routing import OWNER_ID

SHANGHAI = ZoneInfo('Asia/Shanghai')
NEEDS_INTERPRETATION = re.compile(
    r'昨|前天|\d+[号日月]|\d{4}[-/]\d|中午|晚上|早上|刚才|之前|以前|曾经|时隔|'
    r'纠正|其实|不是|改[成为]|不要|应[该当]|补[记充]|取消|删除|撤回|日记|笔记|'
    r'密码|账号|身份证|手机号|卡号|[?？]')


def record_diary_dash(hook: dict, payload: dict, *, client=None, base_url=None) -> dict | None:
    """Create independent private HTML documents for explicitly opted-in '-' messages.

    The message timestamp is the user's current time, stable across delayed delivery
    and retries. A provider-owned source lock spans query/create/readback, so a lost
    response or concurrent retry reuses the committed document. Hidden provenance
    keeps these placeholders out of the ordinary daily aggregation lane.
    """
    messages = payload.get('new_messages') or []
    if (not hook.get('diary_dash_document') or not hook.get('engineering_diary')
            or hook.get('domain') != 'general' or hook.get('kind', 'group') != 'group'
            or '日记' not in hook.get('description', '') or not messages
            or payload.get('previous_reply_unsent') or payload.get('pending_question_seqs')
            or payload.get('hard_event_seqs')):
        return None
    if any(m.get('sender_id') != OWNER_ID or m.get('message_type') not in (1, '1')
           or str(m.get('text') or '').strip() != '-' or m.get('mentions') or m.get('quote')
           or not m.get('message_id') or not m.get('timestamp')
           or m.get('chat_id', hook['chat_id']) != hook['chat_id'] for m in messages):
        return None
    import hashlib
    import requests
    from filelock import FileLock
    from backend.core.settings import get_settings
    from backend.core.access.auth import create_local_owner_session
    from backend.core.messaging.wechat_agent import agent_root
    if client is None:
        owner = create_local_owner_session(username='code4101', expires_minutes=10)
        if owner['user_id'] != 2:
            raise RuntimeError('Diary owner identity mismatch')
        client = requests.Session()
        client.headers['Authorization'] = 'Bearer ' + owner['access_token']
    base_url = base_url or 'http://127.0.0.1:' + str(get_settings().backend_port)

    def call(method, path, **kwargs):
        response = client.request(method, base_url + '/api/notes' + path, timeout=20, **kwargs)
        response.raise_for_status()
        return response.json()

    locks = agent_root() / 'diary-source-locks'
    locks.mkdir(parents=True, exist_ok=True)
    ids = []
    for message in messages:
        source = hook['account_id'] + ':' + message['message_id']
        marker = '<!-- diary-dash:' + escape(source).replace('--', '&#45;&#45;') + ' -->'
        with FileLock(str(locks / (hashlib.sha256(source.encode()).hexdigest() + '.lock')), timeout=30):
            found = call('POST', '/query', json={
                'rules':[{'field':'content', 'op':'contains', 'value':marker}],
                'limit':2, 'include_edges':False})
            if found['total_nodes'] > 1:
                raise RuntimeError('Duplicate diary dash sources require reconciliation')
            if found['nodes']:
                note_id = found['nodes'][0]['id']
            else:
                note_id = call('POST', '/', json={
                    'title':'-', 'content':marker, 'format_type':'html',
                    'note_form':'document', 'private_level':1,
                    'start_at':message['timestamp'], 'custom_fields':[]})['id']
            verified = call('GET', '/' + str(note_id))
            if (verified['user_id'] != 2 or verified['title'] != '-'
                    or verified['format_type'] != 'html' or verified['note_form'] != 'document'
                    or verified['private_level'] != 1 or verified.get('custom_fields')
                    or verified['start_at'] != message['timestamp']
                    or marker not in verified['content']):
                raise RuntimeError('Diary dash write did not pass readback')
            ids.append(note_id)
    return {'action':'ignore', 'text':'', 'question_seqs':[m['seq'] for m in messages],
            'summary':f'减号标记静默创建私密HTML文档并回读核验：{ids}；按消息时刻定位，来源去重。'}


def plain_diary_batch(hook: dict, payload: dict) -> bool:
    """Conservative identity/intent gate; uncertainty must not bypass interpretation."""
    if not hook.get('engineering_diary') or not diary_model_profile(hook, payload):
        return False
    messages = payload['new_messages']
    explained_image = False
    for message in messages:
        kind = message.get('message_type')
        if kind in (3, '3'):
            if message.get('mentions') or not message.get('message_id') or not message.get('timestamp'):
                return False
            explained_image = True
            continue
        if (kind not in (1, '1') or message.get('mentions') or not message.get('message_id')
                or not message.get('timestamp') or NEEDS_INTERPRETATION.search(message.get('text', ''))):
            return False
        explained_image = False
    return not explained_image


def record_plain_diary(hook: dict, payload: dict, *, client=None, base_url=None) -> dict | None:
    """Return a silent verified result, or None when normal AI processing is required.

Retrying a message is safe: the source marker is checked in the current note. A
version conflict is propagated so the caller can reread effects before AI fallback.
"""
    dash_result = record_diary_dash(hook, payload, client=client, base_url=base_url)
    if dash_result is not None:
        return dash_result
    # A polling batch can contain several physical messages. Execute standalone
    # commands independently and expose verified effects if the rest needs AI.
    dash_messages = [m for m in payload.get('new_messages', [])
                     if m.get('message_type') in (1, '1')
                     and str(m.get('text') or '').strip() == '-']
    if dash_messages and len(dash_messages) < len(payload['new_messages']):
        dash_result = record_diary_dash(hook, {**payload, 'new_messages':dash_messages},
                                        client=client, base_url=base_url)
        if dash_result is not None:
            payload['engineering_diary_dash_attempt'] = dash_result
            rest = {**payload, 'new_messages':[m for m in payload['new_messages'] if m not in dash_messages]}
            result = record_plain_diary(hook, rest, client=client, base_url=base_url)
            if result is not None:
                result['question_seqs'] = [m['seq'] for m in payload['new_messages']]
                result['summary'] = dash_result['summary'] + '\n' + result['summary']
            return result
    if not plain_diary_batch(hook, payload):
        return None
    import requests
    from backend.core.settings import get_settings
    from backend.core.access.auth import create_local_owner_session
    messages = payload['new_messages']
    dates = {datetime.fromtimestamp(m['timestamp'], SHANGHAI).date() for m in messages}
    if len(dates) != 1:
        return None
    day = next(iter(dates))
    if client is None:
        owner = create_local_owner_session(username='code4101', expires_minutes=10)
        if owner['user_id'] != 2:
            raise RuntimeError('Diary owner identity mismatch')
        client = requests.Session()
        client.headers['Authorization'] = 'Bearer ' + owner['access_token']
    base_url = base_url or 'http://127.0.0.1:' + str(get_settings().backend_port)

    def call(method, path, **kwargs):
        response = client.request(method, base_url + '/api/notes' + path, timeout=20, **kwargs)
        response.raise_for_status()
        return response.json()

    # Restrict the query to this group's provenance. Read full notes for actual versions.
    prefix = hook['chat_id'] + ':'
    found = call('POST', '/query', json={'rules':[{'field':'content','op':'contains','value':prefix},
                                      {'field':'content','op':'not_contains','value':'<!-- diary-dash:'}],
                                      'limit':100, 'include_edges':False})
    if found['total_nodes'] > 100:
        return None
    matches = [n for n in found['nodes'] if n.get('start_at') is not None
               and datetime.fromtimestamp(n['start_at'], SHANGHAI).date() == day]
    if len(matches) != 1:
        # New entries require event grouping/title interpretation; avoid split notes.
        return None
    note = call('GET', '/' + str(matches[0]['id']))
    if note['user_id'] != 2 or note['format_type'] != 'html' or note.get('custom_fields'):
        return None
    remaining = [m for m in messages if not re.search(
        re.escape(m['message_id']) + r'(?!\d)', note['content'])]
    if remaining:
        explained_images = []
        for index, message in enumerate(remaining):
            if message.get('message_type') not in (3, '3'):
                continue
            explanation = next((candidate for candidate in remaining[index + 1:]
                                if candidate.get('message_type') in (1, '1')), None)
            if not explanation:
                return None
            from pathlib import Path
            from pyxllib.autogui.wechat_accounts import get_account_storage
            resources = get_account_storage(hook['account_id']).message_resources(
                hook['chat_id'], message['local_id'])
            exports = [item['export'] for item in resources['items']
                       if item.get('export', {}).get('readable')
                       and item['export'].get('kind') == 'image']
            if not exports:
                raise RuntimeError('Explained diary photo is not readable')
            export = max(exports, key=lambda e:(e.get('variant') == 'high',
                                                e.get('width', 0) * e.get('height', 0)))
            path = Path(export['stored_path'])
            with path.open('rb') as image_file:
                response = client.post(base_url + '/api/upload/image',
                                       files={'file':(path.name,image_file,'image/jpeg')}, timeout=30)
            response.raise_for_status()
            uploaded = response.json()
            if uploaded.get('errno') != 0 or not uploaded.get('data', {}).get('url'):
                raise RuntimeError('Diary photo upload failed')
            explained_images.append((message, explanation, uploaded['data']['url']))
        additions = []
        for m in remaining:
            if m.get('message_type') in (3, '3'):
                continue
            timestamp = datetime.fromtimestamp(m['timestamp'], SHANGHAI).strftime('%H:%M')
            additions.append('<p><strong>' + timestamp + '</strong> ' + escape(m['text']) + '</p>')
            additions.append('<!-- 来源：' + escape(m['message_id']).replace('--', '&#45;&#45;')
                             + '；时间为现场消息时间参照。 -->')
        for photo, explanation, url in explained_images:
            time_text = datetime.fromtimestamp(explanation['timestamp'], SHANGHAI).strftime('%H:%M')
            additions.append('<p><strong>' + time_text + '</strong> ' + escape(explanation['text']) + '</p>')
            additions.append('<p><img src="' + escape(url, quote=True)
                             + '" alt="' + escape(explanation['text'][:48], quote=True)
                             + '" style="max-width:100%;height:auto;" /></p>')
            ids = [photo['message_id'], explanation['message_id']]
            additions.append('<!-- 来源：' + '、'.join(escape(mid).replace('--','&#45;&#45;') for mid in ids)
                             + '；图片在后续文字说明后关联记录。 -->')
        content = note['content'] + '\n' + '\n'.join(additions)
        call('PUT', '/' + str(note['id']), json={'base_version':note['version'],
             'expected_fields':{'content':note['content']}, 'content':content,
             'mutation_id':'diary-text:' + str(note['id']) + ':' + str(messages[-1]['seq'])})
        verified = call('GET', '/' + str(note['id']))
        if verified['content'] != content or verified['format_type'] != 'html':
            raise RuntimeError('Diary write did not pass readback')
    else:
        verified = note
    return {'action':'ignore', 'text':'', 'question_seqs':[m['seq'] for m in messages],
            'summary':f"工程静默记录已核验：日记{verified['id']}版本{verified['version']}，"
                      f"消息{[m['seq'] for m in messages]}；未调用模型。"}
