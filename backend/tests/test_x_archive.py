from __future__ import annotations

from datetime import datetime

from backend.core.library.x_archive import (
    DISPLAY_TIMEZONE,
    XArchiveStore,
    XPost,
    build_x_book_document,
    normalize_quoted_text,
    normalize_tibo_x_archive_author,
    parse_nitter_rss,
    translate_x_posts,
    translate_x_posts_online,
)


RSS_SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>Newest message</title>
      <link>https://nitter.net/thsottiaux/status/2002#m</link>
      <guid>2002</guid>
      <pubDate>Sun, 26 Jul 2026 04:04:58 GMT</pubDate>
      <description><![CDATA[
        <p>Newest message<br><br>Second line.</p>
        <hr/>
        <blockquote>
          <b>OpenAI Developers (@OpenAIDevs)</b>
          <p><p>Quoted message.</p>
          <img src="https://nitter.net/pic/media%2Fexample.jpg"/></p>
        </blockquote>
      ]]></description>
    </item>
    <item>
      <title>Older message</title>
      <link>https://nitter.net/thsottiaux/status/2001#m</link>
      <guid>2001</guid>
      <pubDate>Sat, 25 Jul 2026 19:17:12 GMT</pubDate>
      <description><![CDATA[<p>Older message</p>]]></description>
    </item>
  </channel>
</rss>
"""


def make_post(**overrides: object) -> XPost:
    values = {
        "id": "2001",
        "handle": "thsottiaux",
        "url": "https://x.com/thsottiaux/status/2001",
        "created_at": "2026-07-26 03:17",
        "created_ts": 100.0,
        "text": "We reset.",
    }
    values.update(overrides)
    return XPost(**values)


def test_parse_nitter_rss_keeps_quote_media_and_normalizes_x_url() -> None:
    posts = parse_nitter_rss(RSS_SAMPLE, handle="thsottiaux")

    assert [post.id for post in posts] == ["2002", "2001"]
    assert posts[0].url == "https://x.com/thsottiaux/status/2002"
    assert posts[0].created_at == "2026-07-26 12:04"
    assert posts[0].text == "Newest message\nSecond line."
    assert posts[0].quoted_author == "OpenAI Developers (@OpenAIDevs)"
    assert posts[0].quoted_text == "Quoted message."
    assert posts[0].images == ["https://pbs.twimg.com/media/example.jpg"]


def test_normalize_quoted_text_removes_nested_rss_duplicate() -> None:
    assert normalize_quoted_text(
        "Your agent can sign in.\nVideo\n\nYour agent can sign in."
    ) == "Your agent can sign in.\nVideo"


def test_store_preserves_translation_until_source_changes(tmp_path) -> None:
    store = XArchiveStore(tmp_path / "x.sqlite3")
    store.upsert_many([make_post()])
    store.save_translations({"2001": ("我们重置了。", "")})
    store.upsert_many([make_post()])

    assert store.list_posts("thsottiaux")[0].text_zh == "我们重置了。"

    store.upsert_many([make_post(text="We reset again.")])

    assert store.list_posts("thsottiaux")[0].text_zh == ""


def test_book_orders_months_and_entries_newest_first() -> None:
    posts = [
        make_post(id="a", created_at="2026-06-20 10:00", created_ts=1, text_zh="六月"),
        make_post(id="b", created_at="2026-07-20 10:00", created_ts=2, text_zh="七月较早"),
        make_post(id="c", created_at="2026-07-21 10:00", created_ts=3, text_zh="七月最新"),
    ]

    document = build_x_book_document(
        posts,
        topic_id=-1,
        title="Tibo X 消息摘录",
        author="Tibo",
        source_url="https://x.com/thsottiaux",
        imported_at=1,
    )

    assert [item.title for item in document.toc] == ["2026-07（2则）", "2026-06（1则）"]
    assert document.content_html.index("七月最新") < document.content_html.index("七月较早")
    assert document.content_html.index("七月较早") < document.content_html.index("六月")
    assert "中文翻译" in document.content_html
    assert "英文原文" not in document.content_html
    assert "We reset." in document.content_html


def test_tibo_archive_author_normalizes_legacy_long_name() -> None:
    assert normalize_tibo_x_archive_author("Tibo（Thibault Sottiaux）") == "Tibo"
    assert normalize_tibo_x_archive_author("Tibo") == "Tibo"
    assert normalize_tibo_x_archive_author("自定义作者") == "自定义作者"


def test_book_shows_translation_and_original_for_quoted_message() -> None:
    document = build_x_book_document(
        [
            make_post(
                text_zh="我们重置了。",
                quoted_text="Does this mean a reset?",
                quoted_text_zh="这意味着会重置吗？",
                images=["https://pbs.twimg.com/media/example.jpg"],
            )
        ],
        topic_id=-1,
        title="Tibo X 消息摘录",
        author="Tibo",
        source_url="https://x.com/thsottiaux",
        imported_at=1,
    )

    assert "我们重置了。" in document.content_html
    assert "We reset." in document.content_html
    assert "这意味着会重置吗？" in document.content_html
    assert "Does this mean a reset?" in document.content_html
    assert document.content_html.index("We reset.") < document.content_html.index("我们重置了。")
    assert (
        document.content_html.index("We reset.")
        < document.content_html.index("Does this mean a reset?")
        < document.content_html.index("example.jpg")
        < document.content_html.index("中文翻译")
        < document.content_html.index("我们重置了。")
        < document.content_html.index("这意味着会重置吗？")
    )
    assert "aspect-ratio:1" not in document.content_html
    assert "width:100%;height:auto" in document.content_html
    assert 'data-book-page-atomic="true"' in document.content_html
    assert '<figure class="x-entry"' in document.content_html
    assert "border:1px solid #eef0f2" not in document.content_html
    assert "background:#f6f7f9" not in document.content_html
    assert "border-left:3px solid #d8dee7" in document.content_html


def test_translate_x_posts_accepts_structured_batch_response() -> None:
    captured: dict = {}

    def fake_chat(**kwargs):
        captured.update(kwargs)
        return {
            "content": (
                '{"translations":[{"id":"2001","text_zh":"我们重置了。",'
                '"quoted_text_zh":""}]}'
            )
        }

    result = translate_x_posts([make_post()], chat=fake_chat)

    assert result == {"2001": ("我们重置了。", "")}
    assert captured["temperature"] == 0
    assert captured["response_format"]["type"] == "object"
    assert datetime.now(DISPLAY_TIMEZONE).tzinfo is not None


def test_online_translation_keeps_main_and_quote_mapping() -> None:
    post = make_post(quoted_text="Quoted text.")

    result = translate_x_posts_online(
        [post],
        max_workers=1,
        translate_text=lambda text: f"中：{text}",
    )

    assert result == {
        "2001": ("中：We reset.", "中：Quoted text."),
    }
