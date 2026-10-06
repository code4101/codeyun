"""Nonblocking attachment preparation shared by initial input and live steering.

The current business agent retains GUI ownership. A bounded worker prepares
voice/article content; polling or steering never waits for network/downloads.
Unready input retains its original resource identity and an explicit handoff.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
import time

from backend.core.messaging.wechat_voice import voice_context
from backend.core.messaging.wechat_articles import article_context, message_links
import xml.etree.ElementTree as ET


IMAGE_WAIT_INSTRUCTION = "[图片仅作待解释附件：必须等待图片之后的人工文字说明，再判断用途；不得仅凭图片启动整理、保存、查询或业务操作。]"


def is_image_message(message: dict) -> bool:
    """Recognize archive image events, including legacy inputs without a type."""
    if message.get('message_type') in (3, '3'):
        return True
    body = str(message.get('text') or '')
    sender = message.get('sender_id', '')
    if sender and body.startswith(sender + ':\n'):
        body = body[len(sender) + 2:]
    try:
        return ET.fromstring(body).find('img') is not None
    except ET.ParseError:
        return False


class MessageContentResolver:
    def __init__(self, *, voice_reader=voice_context, article_reader=article_context):
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='wechat-content')
        self.jobs = {}
        self.lock = threading.RLock()
        self.voice_reader = voice_reader
        self.article_reader = article_reader

    def _read(self, message, account, chat, private):
        result = self.voice_reader([message], account, chat)
        result = self.article_reader(result, account)[0] if private else result[0]
        # Full article already lives in the provider cache; keep only the bounded
        # injected excerpt and provenance in the in-memory future cache.
        if 'articles' in result:
            result = {**result, 'articles':[{k:v for k,v in a.items() if k!='text'} for a in result['articles']]}
        return result

    def enrich(self, messages, account, chat, *, private):
        with self.lock:
            return self._enrich(messages, account, chat, private=private)

    def _enrich(self, messages, account, chat, *, private):
        output = []
        for message in messages:
            media = message.get('message_type') in (34, '34') or (private and message_links(message))
            if not media:
                output.append(message)
                continue
            key = (account, chat, message.get('message_id', message.get('seq')), message.get('text'), private)
            job = self.jobs.get(key)
            # Completed records are evicted first. Bound queue size under bursts.
            if job is None and len(self.jobs) >= 128:
                for old in list(self.jobs):
                    if self.jobs[old][0].done():
                        self.jobs.pop(old)
                        if len(self.jobs) < 128:
                            break
            if job is None and len(self.jobs) < 128:
                job = self.jobs[key] = (self.pool.submit(self._read, dict(message), account, chat, private), time.monotonic())
            if job and job[0].done():
                try:
                    result = job[0].result()
                except Exception:
                    result = None
                if result is not None:
                    output.append(result)
                    # Official transcript/browser cache may be populated later.
                    if time.monotonic()-job[1] > 5 and not (result.get('voice', {}).get('text') or
                            any(a.get('status')=='ok' for a in result.get('articles', []))):
                        self.jobs.pop(key, None)
                    continue
            output.append({**message, 'text':str(message.get('text') or '')+
                '\n[附件内容正在后台准备；此状态不代表识别或读取成功。需要立即读取时使用公共语音/文章接口；'
                '语音可调用 get_account_storage(account_id).voice_message(chat_id, local_id)；'
                '文章可调用 backend.core.messaging.wechat_articles.read_article(url, scope=account_id)。'
                '微信官方转写或内置浏览器操作仍由当前智能体负责，结束后关闭本次新增窗口。]'} )
        return output

    def close(self):
        self.pool.shutdown(wait=False, cancel_futures=True)
