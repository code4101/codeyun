from backend.core.messaging.wechat_reply_groups import reply_groups


def quote(event):
    return reply_groups('CODECLAW_REPLY_TO: 1\n结果', [dict(seq=1, local_id=61, **event)])[0]['quote_text']


def test_quote_limit_includes_ellipsis_and_preserves_short_text():
    assert quote({'text': '字'*20}) == '字'*20
    assert quote({'text': '字'*21}) == '字'*19+'…'
    assert len(quote({'text': '长问题'*100})) == 20
    assert quote({'text': '第一行\n第二行'}) == '第一行 第二行'


def test_voice_quote_uses_provider_transcript_not_attachment_or_context_header():
    event={'message_type':34, 'text':'<msg><voicemsg/></msg>',
           'voice':{'text':'实际使用还是会有弹出收起侧栏，展开侧栏啊。','source':'wechat-official'}}
    assert quote(event) == '实际使用还是会有弹出收起侧栏，展开侧栏…'
    assert quote({'message_type':34,'text':'<msg/>','voice':{'text':None}}) == '[转写待完成]'
