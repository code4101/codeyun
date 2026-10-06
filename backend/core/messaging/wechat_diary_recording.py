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

SHANGHAI = ZoneInfo('Asia/Shanghai')
NEEDS_INTERPRETATION = re.compile(
    r'昨|前天|\d+[号日月]|\d{4}[-/]\d|中午|晚上|早上|刚才|之前|以前|曾经|时隔|'
    r'纠正|其实|不是|改[成为]|不要|应[该当]|补[记充]|取消|删除|撤回|日记|笔记|'
    r'密码|账号|身份证|手机号|卡号|[?？]')


def plain_diary_batch(hook: dict, payload: dict) -> bool:
    """Conservative identity/intent gate; uncertainty must not bypass interpretation."""
    if not hook.get('engineering_diary') or not diary_model_profile(hook, payload):
        return False
    messages = payload['new_messages']
    return all(m.get('message_type') in (1, '1') and not m.get('mentions')
               and m.get('message_id') and m.get('timestamp')
               and not NEEDS_INTERPRETATION.search(m.get('text', '')) for m in messages)


def record_plain_diary(hook: dict, payload: dict, *, client=None, base_url=None) -> dict | None:
    """Return a silent verified result, or None when normal AI processing is required.

Retrying a message is safe: the source marker is checked in the current note. A
version conflict is propagated so the caller can reread effects before AI fallback.
"""
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
    found = call('POST', '/query', json={'rules':[{'field':'content','op':'contains','value':prefix}],
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
        additions = []
        for m in remaining:
            timestamp = datetime.fromtimestamp(m['timestamp'], SHANGHAI).strftime('%H:%M')
            additions.append('<p><strong>' + timestamp + '</strong> ' + escape(m['text']) + '</p>')
            additions.append('<!-- 来源：' + escape(m['message_id']).replace('--', '&#45;&#45;')
                             + '；时间为现场消息时间参照。 -->')
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
