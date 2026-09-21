import base64

import pytest

from backend.core.media_download.wechat_identity import WeChatArticleIdentity, index_wechat_image_sources


def test_share_tracking_and_html_escaping_do_not_change_identity():
    biz = base64.b64encode(b"12345").decode()
    a = WeChatArticleIdentity.from_url(f"https://mp.weixin.qq.com/s?__biz={biz}&amp;mid=200&amp;idx=1&scene=8")
    b = WeChatArticleIdentity("12345", "200", "1")
    assert a == b
    assert a.media_id("detail", 0) != a.media_id("cover", 0)
    assert a.article_id != WeChatArticleIdentity("67890", "200", "1").article_id


def test_missing_or_ambiguous_identity_is_not_invented():
    for url in ["https://mp.weixin.qq.com/s/short", "https://example.com/s?__biz=MTIz&mid=2&idx=1", "https://mp.weixin.qq.com/s?__biz=MTIz&mid=2&mid=3&idx=1"]:
        with pytest.raises(ValueError):
            WeChatArticleIdentity.from_url(url)


def test_shared_image_keeps_multiple_sources_and_original_indexes():
    url = "https://mmbiz.qpic.cn/mmbiz_jpg/test/0"
    objects = [{"bizuin": author, "appmsg_info": [{"mid": 200, "idx": 1, "title": "同名", "cover_pic_urls": ["invalid", url]}]} for author in [123, 456]]
    result = index_wechat_image_sources(objects)
    assert len(result[url]) == 2
    assert {x["index"] for x in result[url]} == {1}
    assert len({x["media_id"] for x in result[url]}) == 2


def test_title_alone_cannot_assign_an_author():
    result = index_wechat_image_sources([{"title": "相邻标题", "picture_page_info_list": [{"cdn_url": "https://mmbiz.qpic.cn/test/0"}]}])
    assert result == {}
