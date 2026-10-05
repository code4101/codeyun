import threading

from backend.core.messaging.wechat_content import MessageContentResolver


def test_slow_article_does_not_block_other_messages_and_can_be_reused():
    entered,release,finished=threading.Event(),threading.Event(),threading.Event()
    def read(messages,account):
        entered.set()
        assert release.wait(5)
        finished.set()
        return [{**messages[0],'text':'真实正文','articles':[{'status':'ok'}]}]
    resolver=MessageContentResolver(voice_reader=lambda messages,*_:messages,article_reader=read)
    article={'seq':1,'message_type':1,'text':'https://example.com/article'}
    plain={'seq':2,'message_type':1,'text':'另外一个问题'}
    try:
        first=resolver.enrich([article,plain],'account','chat',private=True)
        assert entered.wait(2)
        assert not finished.is_set() and first[1]==plain
        assert '正在后台准备' in first[0]['text']
        assert article['text']=='https://example.com/article'
        release.set()
        # Await the owned job deterministically; application enrich never waits.
        next(iter(resolver.jobs.values()))[0].result(timeout=3)
        assert resolver.enrich([article],'account','chat',private=True)[0]['text']=='真实正文'
    finally:
        release.set()
        resolver.close()
