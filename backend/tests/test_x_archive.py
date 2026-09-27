from __future__ import annotations

from datetime import datetime
import pytest

from backend.core.library.x_archive import (
    DISPLAY_TIMEZONE,
    XArchiveStore,
    XArchivePartialFetchError,
    XPost,
    build_x_book_document,
    crawl_x_profile,
    normalize_quoted_text,
    normalize_tibo_x_archive_author,
    parse_nitter_rss,
    parse_nitter_timeline,
    translate_x_posts,
    translate_x_posts_online,
)


TIMELINE_SAMPLE = '''<div class="timeline">
<div class="timeline-item">
  <a class="tweet-link" href="/thsottiaux/status/2098639827084480864#m"></a>
  <div class="tweet-content">Astra powered ships this week\nNext <a>week</a> too.</div>
  <div class="quote"><a class="fullname">OpenAI</a><a class="username">@OpenAI</a>
    <div class="quote-text">Quoted announcement.</div>
    <img class="avatar" src="https://pbs.twimg.com/profile_images/avatar.jpg">
  </div>
  <div class="attachments"><img src="https://pbs.twimg.com/media/photo.jpg"></div>
</div><div class="show-more"><a href="?cursor=opaque">Load more</a></div></div>'''.encode()


X_PUBLIC_SAMPLE = b'''<article>
<a href="/thsottiaux/status/2099922755655479624">12h</a>
<div dir="auto">New post<br>Second line.</div>
<img src="https://pbs.twimg.com/media/test?format=webp">
<button>100 likes</button>
</article><article>
<a href="/thsottiaux/status/2099393115241300166">Sep 14</a>
<div dir="auto">Archived post</div>
<article><a href="/someone/status/2098639827084480864">Quote</a>
<div dir="auto" class="line-clamp-5">Truncated quote</div></article>
</article>'''


def test_public_x_incremental_preserves_existing_complete_quotes(monkeypatch):
    from io import BytesIO
    from backend.core.library import x_archive

    monkeypatch.setattr(x_archive, "urlopen", lambda *a, **k: BytesIO(X_PUBLIC_SAMPLE))
    posts = x_archive.crawl_x_public_profile(
        handle="thsottiaux", since=datetime(2026, 9, 1, tzinfo=DISPLAY_TIMEZONE),
        known_ids={"2099393115241300166"},
    )
    assert len(posts) == 1
    assert posts[0].id == "2099922755655479624"
    assert posts[0].text == "New post\nSecond line."
    assert posts[0].images == ["https://pbs.twimg.com/media/test?format=webp"]
    assert x_archive.crawl_x_public_profile(
        handle="thsottiaux", since=datetime(2026, 9, 1, tzinfo=DISPLAY_TIMEZONE),
        known_ids={"2099393115241300166", "2099922755655479624"},
    ) == []


def test_public_x_preview_gap_publishes_posts_with_warning(monkeypatch):
    from io import BytesIO
    from backend.core.library import x_archive

    sample = X_PUBLIC_SAMPLE.split(b"</article>")[0] + b"</article>"
    monkeypatch.setattr(x_archive, "urlopen", lambda *a, **k: BytesIO(sample))
    with pytest.raises(XArchivePartialFetchError) as error:
        x_archive.crawl_x_public_profile(
            handle="thsottiaux", since=datetime(2026, 9, 1, tzinfo=DISPLAY_TIMEZONE), known_ids=set(),
        )
    assert error.value.posts[0].id == "2099922755655479624"


def test_public_x_expands_new_quote_from_public_permalink(monkeypatch):
    from io import BytesIO
    from backend.core.library import x_archive

    urls = []
    def fetch(request, **kwargs):
        urls.append(request.full_url)
        if len(urls) == 1:
            return BytesIO(X_PUBLIC_SAMPLE)
        return BytesIO(b'''<article><a href="/someone/status/2098639827084480864">date</a>
            <div dir="auto">Complete quoted message.</div></article>''')

    monkeypatch.setattr(x_archive, "urlopen", fetch)
    posts = x_archive.crawl_x_public_profile(
        handle="thsottiaux", since=datetime(2026, 9, 1, tzinfo=DISPLAY_TIMEZONE),
        known_ids={"2099922755655479624"},
    )
    assert posts[0].quoted_text == "Complete quoted message."
    assert urls[1] == "https://x.com/someone/status/2098639827084480864"


@pytest.mark.parametrize("data", [b"<html>Log in</html>", X_PUBLIC_SAMPLE])
def test_public_x_rejects_login_and_truncated_quotes(data):
    from backend.core.library.x_archive import parse_x_public_profile

    with pytest.raises(RuntimeError):
        parse_x_public_profile(data, handle="thsottiaux")


def test_parse_public_timeline_preserves_content_quote_and_media() -> None:
    posts = parse_nitter_timeline(TIMELINE_SAMPLE, handle="thsottiaux")
    assert len(posts) == 1
    post = posts[0]
    assert post.id == "2098639827084480864"
    assert post.created_at == "2026-09-12 13:08"
    assert post.text == "Astra powered ships this week\nNext week too."
    assert post.quoted_author == "OpenAI (@OpenAI)"
    assert post.quoted_text == "Quoted announcement."
    assert post.images == ["https://pbs.twimg.com/media/photo.jpg"]


@pytest.mark.parametrize("data", [b"<html>Service unavailable</html>", b'<div class="timeline">Try again later</div>'])
def test_public_timeline_does_not_treat_error_page_as_up_to_date(data) -> None:
    with pytest.raises(RuntimeError):
        parse_nitter_timeline(data, handle="thsottiaux")


def test_crawl_continues_past_old_pinned_post_and_deduplicates() -> None:
    recent = make_post(id="recent", created_ts=datetime(2026, 9, 12, tzinfo=DISPLAY_TIMEZONE).timestamp())
    pinned = make_post(id="pinned", created_ts=1)
    second = make_post(id="second", created_ts=recent.created_ts - 3600)
    calls = []

    def fetch_page(*, handle, cursor):
        calls.append(cursor)
        return (b"first", "next") if not cursor else (b"second", "next")

    def parse_page(data, *, handle):
        return [pinned, recent] if data == b"first" else [recent, second]

    posts = crawl_x_profile(handle="thsottiaux", since=datetime(2026, 9, 1, tzinfo=DISPLAY_TIMEZONE),
                            fetch_page=fetch_page, parse_page=parse_page)
    assert calls == ["", "next"]
    assert [post.id for post in posts] == ["recent", "second"]


def test_pagination_failure_preserves_valid_posts() -> None:
    def fetch_page(*, handle, cursor):
        return (TIMELINE_SAMPLE, "next") if not cursor else (b"<html>Browser verification</html>", "")

    with pytest.raises(XArchivePartialFetchError) as error:
        crawl_x_profile(handle="thsottiaux", since=datetime(2026, 9, 1, tzinfo=DISPLAY_TIMEZONE),
                        fetch_page=fetch_page)
    assert [post.id for post in error.value.posts] == ["2098639827084480864"]


def test_partial_sync_publishes_latest_and_keeps_backfill_warning(tmp_path, monkeypatch) -> None:
    from sqlmodel import Session, SQLModel, create_engine
    from backend.core.library import x_archive
    from backend.models import LibraryBookAsset, ResourceIdentity

    db = create_engine("sqlite://")
    SQLModel.metadata.create_all(db, tables=[LibraryBookAsset.__table__, ResourceIdentity.__table__])
    written = []
    monkeypatch.setattr(x_archive, "_archive_root", lambda handle: tmp_path)
    monkeypatch.setattr(x_archive, "_ensure_placement", lambda *args: None)
    monkeypatch.setattr(x_archive, "_write_document", lambda owner, doc: written.append(doc))
    posts = parse_nitter_timeline(TIMELINE_SAMPLE, handle="thsottiaux")

    def incomplete(**kwargs):
        raise XArchivePartialFetchError(posts, RuntimeError("Browser verification"))

    with Session(db) as session:
        result = x_archive.sync_x_archive(session, owner_user_id=1, crawler=incomplete,
            translator=lambda pending: {p.id: ("新消息", "引用译文") for p in pending})
        assert result.status == "partial"
        assert result.new_count == result.translated_count == result.total_count == 1
        assert "Browser verification" in result.message
        assert len(written) == 1
        asset = session.get(LibraryBookAsset, result.book_id)
        assert asset.metadata_json["newest_post_at"] == "2026-09-12 13:08"
        assert asset.metadata_json["sync_warning"] == result.message
        result = x_archive.sync_x_archive(session, owner_user_id=1, crawler=lambda **kwargs: posts)
        assert result.status == "up_to_date"
        assert asset.metadata_json["sync_warning"] == ""
        assert len(written) == 1


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


def test_translation_rate_limit_uses_local_fallback_only_for_missing_posts() -> None:
    retried = []

    def remote(text):
        if text == "limited":
            raise RuntimeError("HTTP 429")
        return "远端译文"

    def fallback(posts):
        retried.extend(post.id for post in posts)
        return {post.id: ("本机译文", "") for post in posts}

    result = translate_x_posts_online([make_post(id="ok"), make_post(id="limited", text="limited")],
                                      translate_text=remote, fallback_translator=fallback)
    assert result == {"ok": ("远端译文", ""), "limited": ("本机译文", "")}
    assert retried == ["limited"]


def test_translation_cannot_silently_succeed_when_both_providers_fail() -> None:
    with pytest.raises(RuntimeError, match="未完成全部记录"):
        translate_x_posts_online([make_post()], translate_text=lambda text: "",
                                 fallback_translator=lambda posts: {})
