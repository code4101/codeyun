"""Internal housekeeping stays durable and never appears in task answers."""
import json

import pytest

from backend.core.messaging.wechat_delivery import prepare_reply, deliver_reply
from backend.core.messaging.wechat_reply_policy import extract_operational_notes
from backend.tests.test_wechat_message_routing import fixture_store, ingest, HOOK


NOTE = 'CODECLAW_OPS_NOTE: ' + json.dumps({'category':'routine_housekeeping',
                                        'message':'临时产物待维护，不影响任务结果'},ensure_ascii=False)


def test_sender_archives_and_deduplicates_housekeeping_without_echo(tmp_path):
    store = fixture_store(tmp_path)
    sent = []
    for seq in (1, 2):
        ingest(store, seq, '修复功能')
        store.record_message_route('group',seq,'main')
        text = '功能已修复。\n' + NOTE
        plan = prepare_reply(text,[],private=False)
        assert deliver_reply(store,HOOK,seq,text,plan,sender=lambda _,body,**kw: sent.append(body) or {}) == 'sent'
    assert sent == ['功能已修复。','功能已修复。']
    restarted = type(store)(store.path)
    notices = restarted.operational_notices('group')
    assert len(notices) == 1 and notices[0]['occurrences'] == 2
    assert '临时产物' not in ''.join(r['text'] for r in restarted.deliveries('group'))


def test_required_disclosure_and_explicit_operations_answer_are_preserved():
    for text in ('自动审查阻断了当前部署，需要批准。','你询问的临时产物目前尚未清理。'):
        clean, notes = extract_operational_notes(text)
        assert clean == text and notes == []


def test_directive_in_quoted_code_is_not_interpreted():
    text = '示例：\n```text\n' + NOTE + '\n```'
    assert extract_operational_notes(text) == (text, [])


@pytest.mark.parametrize('text',[
    NOTE,
    '功能已修复。\nCODECLAW_OPS_NOTE: {"category":"approval_block","message":"不能隐藏"}',
    '功能已修复。\nCODECLAW_OPS_NOTE: {"category":"routine_housekeeping","message":""}',
])
def test_notes_cannot_replace_an_answer_or_hide_blocking_disclosures(text):
    with pytest.raises(ValueError):
        prepare_reply(text,[],private=False)
