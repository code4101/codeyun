from __future__ import annotations

import json

from backend.core.library.weibo_archive import (
    WeiboArchiveStore,
    WeiboPost,
    build_yearly_book_payload,
    cache_weibo_media,
    filter_weibo_media_urls,
    load_batch_jsonl,
    normalize_weibo_datetime,
    normalize_weibo_text,
    prefer_original_sina_image_url,
    render_weibo_entry_html,
    render_markdown,
)


def make_post(**overrides: object) -> WeiboPost:
    values = {
        "id": "Qp59aaYV6",
        "uid": "2273105342",
        "url": "https://weibo.com/2273105342/Qp59aaYV6",
        "created_at": "2026-01-27 19:18",
        "text": "被人被物被感性战胜是俘虏，战胜自己是道者。",
    }
    values.update(overrides)
    return WeiboPost(**values)


def test_normalize_weibo_text_removes_invisible_characters() -> None:
    assert normalize_weibo_text("甲\u200b  \n乙\n\n\n丙") == "甲\n乙\n\n丙"


def test_normalize_weibo_datetime_expands_detail_page_year() -> None:
    assert normalize_weibo_datetime("26-3-2 06:06") == "2026-03-02 06:06"
    assert normalize_weibo_datetime("2024-5-8 07:03") == "2024-05-08 07:03"


def test_store_upsert_preserves_full_text_over_truncated_version(tmp_path) -> None:
    store = WeiboArchiveStore(tmp_path / "weibo.sqlite3")
    store.upsert_many([make_post(text="完整正文", truncated=False)])
    store.upsert_many([make_post(text="完整正", truncated=True)])

    posts = store.list_posts(uid="2273105342")

    assert store.count("2273105342") == 1
    assert posts[0].text == "完整正文"
    assert posts[0].truncated is False


def test_render_markdown_omits_repeated_author_source_and_keeps_repost_context() -> None:
    post = make_post(
        author_name="武陵惟海老头子",
        source_label="来自 微博网页版",
        text="转发说明",
        is_repost=True,
        repost_text="被转发的内容",
        images=["https://example.com/a.jpg"],
        video_poster_url="https://example.com/video.jpg",
        video_url="https://video.weibo.com/show?fid=1",
        video_duration="00:59",
        video_views="105万次观看",
    )

    markdown = render_markdown([post], title="个人摘录")

    assert "# 个人摘录" in markdown
    assert "转发说明" in markdown
    assert "武陵惟海老头子" not in markdown
    assert "来自 微博网页版" not in markdown
    assert "> 被转发的内容" in markdown
    assert "![微博图片](https://example.com/a.jpg)" in markdown
    assert "[![微博视频封面](https://example.com/video.jpg)](https://video.weibo.com/show?fid=1)" in markdown
    assert "00:59 · 105万次观看" in markdown
    assert "[查看原微博](https://weibo.com/2273105342/Qp59aaYV6)" in markdown


def test_load_batch_jsonl_deduplicates_and_prefers_full_text(tmp_path) -> None:
    path = tmp_path / "batches.jsonl"
    path.write_text(
        "[{\"id\":\"a\",\"uid\":\"1\",\"url\":\"https://weibo.com/1/a\","
        "\"created_at\":\"2026-01-01\",\"text\":\"短\",\"truncated\":true}]\n"
        "[{\"id\":\"a\",\"uid\":\"1\",\"url\":\"https://weibo.com/1/a\","
        "\"created_at\":\"2026-01-01\",\"text\":\"完整\",\"truncated\":false}]\n",
        encoding="utf-8",
    )

    posts = load_batch_jsonl(path)

    assert len(posts) == 1
    assert posts[0].text == "完整"
    assert posts[0].truncated is False


def test_filter_weibo_media_urls_removes_non_content_images() -> None:
    avatar = (
        "https://tvax1.sinaimg.cn/crop.0.0.1080.1080.1024/"
        "6a215f10ly8ie6yk3cyz1j20u00u0jvi.jpg?KID=imgbed"
    )
    media = "https://wx1.sinaimg.cn/orj480/6a215f10ly1iaokn5v1mgj20hq0a03yv.jpg"
    blog_placeholder = "https://s6.sinaimg.cn/thumb180/6c0c0bb3tdf7bdeae9c65"
    cached_blog_placeholder = (
        "/static/attachments/weibo/2273105342/"
        "278da44c9dabeda882047a94d1071c6dfbe29ece1bc79f8a5067803147cebc06.jpg"
    )

    assert filter_weibo_media_urls(
        [media, avatar, blog_placeholder, cached_blog_placeholder, media]
    ) == [media]


def test_prefer_original_sina_image_url_rewrites_thumbnail_rendition() -> None:
    assert prefer_original_sina_image_url(
        "https://wx1.sinaimg.cn/orj360/example.jpg?tag=1"
    ) == "https://wx1.sinaimg.cn/large/example.jpg?tag=1"
    assert prefer_original_sina_image_url(
        "https://example.com/orj360/example.jpg"
    ) == "https://example.com/orj360/example.jpg"


def test_render_weibo_entry_omits_repeated_identity_and_keeps_content_geometry() -> None:
    post = make_post(
        author_name="武陵惟海老头子",
        author_avatar_url="https://example.com/avatar.jpg",
        source_label="来自 微博网页版",
        text="转发微博",
        repost_text="@过期少年Rebirth\n被转发的内容",
        is_repost=True,
        video_poster_url="https://example.com/video.jpg",
        video_url="https://video.weibo.com/show?fid=1",
        video_duration="00:59",
        video_views="105万次观看",
        repost_created_at="2026-02-27 03:08",
        repost_url="https://weibo.com/1780571920/example",
        repost_reposts=1853,
        repost_comments=418,
        repost_likes=6079,
        reposts=6,
        comments=1,
        likes=23,
    )

    html = render_weibo_entry_html(post)

    assert "max-width:640px" in html
    assert "武陵惟海老头子" not in html
    assert "avatar.jpg" not in html
    assert "来自 微博网页版" not in html
    assert "2026-01-27 19:18" in html
    assert 'class="weibo-entry-meta"' in html
    assert 'class="weibo-entry-stats"' in html
    assert "原微博</a><span>转 6</span><span>评 1</span><span>赞 23</span>" in html
    assert "grid-template-columns:repeat(4,1fr)" not in html
    assert "查看原微博" not in html
    assert "padding:8px 18px 12px;background:#f9f9f9" in html
    assert "max-width:542px;aspect-ratio:16/9" in html
    assert "font-size:15px;line-height:24px" in html
    assert "转发 1853" in html
    assert "评论 418" in html
    assert "赞 6079" in html


def test_yearly_book_payload_uses_one_toc_entry_per_year_and_month_sections() -> None:
    payload = build_yearly_book_payload(
        [
            make_post(id="z", created_at="2025-12-31 08:00", text="旧年"),
            make_post(id="a", created_at="2026-04-01 08:00", text="甲"),
            make_post(id="b", created_at="2026-04-02 08:00", text="乙"),
            make_post(id="c", created_at="2026-05-01 08:00", text="丙"),
        ],
        topic_id=2273105342,
        title="惟海法师微博摘录",
        author="惟海法师",
        source_url="https://weibo.com/u/2273105342",
        imported_at=1.0,
    )

    assert [item["title"] for item in payload["toc"]] == ["2026年（3则）", "2025年（1则）"]
    assert payload["post_count"] == 4
    assert "4月（2则）" in payload["content_html"]
    assert "5月（1则）" in payload["content_html"]
    assert payload["content_html"].index("2026年（3则）") < payload["content_html"].index("2025年（1则）")
    assert payload["content_html"].index("甲") < payload["content_html"].index("乙")
    assert payload["content_html"].index("乙") < payload["content_html"].index("丙")
    assert "本月提要" not in payload["content_html"]


def test_cache_weibo_media_rewrites_successful_images_to_local_urls(tmp_path) -> None:
    store = WeiboArchiveStore(tmp_path / "weibo.sqlite3")
    store.upsert_many([make_post(images=["https://example.com/a.jpg"])])

    result = cache_weibo_media(
        store,
        uid="2273105342",
        directory=tmp_path / "media",
        url_prefix="/static/attachments/weibo/2273105342",
        fetcher=lambda _url, _referer: b"\x89PNG\r\n\x1a\n" + b"image-data",
    )

    post = store.list_posts(uid="2273105342")[0]
    assert result == {"referenced": 1, "cached": 1, "failed": 0, "upgraded": 0}
    assert post.images[0].startswith("/static/attachments/weibo/2273105342/")
    assert (tmp_path / "media" / post.images[0].rsplit("/", 1)[-1]).is_file()


def test_cache_weibo_media_upgrades_existing_sina_thumbnail(tmp_path) -> None:
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    source_url = "https://wx1.sinaimg.cn/orj360/example.jpg"
    local_url = "/static/attachments/weibo/2273105342/existing.jpg"
    (media_dir / "existing.jpg").write_bytes(b"old-thumbnail")
    (media_dir / "manifest.json").write_text(
        json.dumps({source_url: local_url}),
        encoding="utf-8",
    )
    store = WeiboArchiveStore(tmp_path / "weibo.sqlite3")
    store.upsert_many([make_post(images=[local_url])])
    fetched_urls: list[str] = []

    def fetch(url: str, _referer: str) -> bytes:
        fetched_urls.append(url)
        return b"\x89PNG\r\n\x1a\n" + b"original-image"

    result = cache_weibo_media(
        store,
        uid="2273105342",
        directory=media_dir,
        url_prefix="/static/attachments/weibo/2273105342",
        fetcher=fetch,
    )

    assert fetched_urls == ["https://wx1.sinaimg.cn/large/example.jpg"]
    assert (media_dir / "existing.jpg").read_bytes().endswith(b"original-image")
    assert result["upgraded"] == 1


def test_video_without_recoverable_poster_uses_styled_fallback() -> None:
    html = render_weibo_entry_html(make_post(
        video_url="https://video.weibo.com/example",
        video_duration="25:31",
        video_views="44.9万次观看",
    ))

    assert "微博视频" in html
    assert "微博视频封面" not in html
    assert "25:31" in html


def test_cache_weibo_media_removes_unrecoverable_remote_reference(tmp_path) -> None:
    store = WeiboArchiveStore(tmp_path / "weibo.sqlite3")
    store.upsert_many([make_post(
        images=["https://example.com/missing.jpg"],
        video_poster_url="https://example.com/missing.jpg",
        video_url="https://video.weibo.com/example",
    )])

    result = cache_weibo_media(
        store,
        uid="2273105342",
        directory=tmp_path / "media",
        url_prefix="/static/attachments/weibo/2273105342",
        fetcher=lambda _url, _referer: b"not-an-image",
    )

    post = store.list_posts(uid="2273105342")[0]
    assert result == {"referenced": 1, "cached": 0, "failed": 1, "upgraded": 0}
    assert post.images == []
    assert post.video_poster_url == ""
    assert "微博视频" in render_weibo_entry_html(post)
