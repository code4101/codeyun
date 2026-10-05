import socket
from types import SimpleNamespace

import pytest

from backend.core.messaging import wechat_articles as articles


URL='https://mp.weixin.qq.com/s?__biz=abc&mid=12&idx=1&sn=def'


@pytest.fixture(autouse=True)
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(articles,'get_settings',lambda:SimpleNamespace(data_dir=tmp_path))


def test_share_identity_discards_browser_credentials_and_matches_exact_article():
    assert articles.canonical_url(URL+'&key=secret&uin=123&pass_ticket=secret#rd')==URL
    event={'message_type':49,'text':'<msg><appmsg><type>5</type><url>'+URL.replace('&','&amp;')+'</url></appmsg></msg>'}
    assert articles.message_links(event)==[URL]
    assert articles.message_links({'message_type':49,'text':'<msg><appmsg><type>57</type><url>https://example.com</url></appmsg></msg>'})==[]
    assert articles.message_links({'message_type':1,'text':'网址 https://example.com/a。'})==['https://example.com/a']


def test_success_is_cached_failure_retryable_and_context_preserves_input(monkeypatch):
    calls=[]
    def fetch(url):
        calls.append(url)
        return '<h1>正文标题</h1><article>'+'真正正文 '*100+'</article>'
    monkeypatch.setattr(articles,'_fetch_html',fetch)
    message={'seq':1,'message_type':1,'text':'请读 https://example.com/a'}
    result=articles.article_context([message],'account')
    assert '真正正文' in result[0]['text'] and '全文文件=' in result[0]['text']
    assert message['text']=='请读 https://example.com/a'
    assert articles.read_article('https://example.com/a',scope='account')['cached']
    assert len(calls)==1
    monkeypatch.setattr(articles,'_fetch_html',lambda _:'<p>当前环境异常 完成验证后即可继续访问</p>')
    assert articles.read_article(URL,scope='account')['status']=='needs_browser'
    monkeypatch.setattr(articles,'_fetch_html',lambda _:'<h1>文章</h1><div id="js_content">'+'正文内容 '*100+'</div>')
    assert articles.read_article(URL,scope='account')['status']=='ok'


def test_browser_capture_requires_matching_url_title_and_account_scope(monkeypatch):
    text='文章标题\n'+'通过微信读取的真实正文 '*50
    saved=articles.record_browser_article(URL,text,observed_url=URL+'&key=credential',title='文章标题',scope='a')
    assert saved['source']=='wechat-browser-uia' and 'credential' not in str(saved)
    with pytest.raises(ValueError,match='match'):
        articles.record_browser_article(URL,text,observed_url=URL.replace('mid=12','mid=99'),title='文章标题',scope='a')
    monkeypatch.setattr(articles,'_fetch_html',lambda _:b'not an article')
    assert articles.read_article(URL,scope='a')['cached']
    assert articles.read_article(URL,scope='b')['status']=='needs_browser'
    with pytest.raises(ValueError):
        articles.record_browser_article(URL,'文章标题 当前环境异常 '+'x'*100,observed_url=URL,title='文章标题',scope='a')


def test_private_network_targets_and_malformed_links_fail_closed(monkeypatch):
    monkeypatch.setattr(socket,'getaddrinfo',lambda *args:[(None,None,None,None,('127.0.0.1',443))])
    with pytest.raises(ValueError,match='public internet'):
        articles._public_target('https://example.com')
    result=articles.article_context([{'message_type':1,'text':'https://name:password@example.com'}],'a')
    assert result[0]['articles'][0]['status']=='error'
