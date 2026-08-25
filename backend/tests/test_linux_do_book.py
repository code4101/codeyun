from __future__ import annotations

from backend.core.library.linux_do_book import (
    compose_linux_do_book,
    normalize_discussion_numbering,
    parse_linux_do_topic_url,
    select_substantive_replies,
)


def _post(number: int, username: str, cooked: str, *, likes: int = 0) -> dict:
    return {
        "id": 10_000 + number,
        "post_number": number,
        "username": username,
        "display_username": username,
        "name": username,
        "cooked": cooked,
        "updated_at": "2026-07-07T00:00:00Z",
        "actions_summary": [{"id": 2, "count": likes}],
    }


def test_parse_linux_do_topic_url_collapses_floor_and_slug() -> None:
    assert parse_linux_do_topic_url("https://linux.do/t/topic/2538870/30") == (
        2538870,
        "https://linux.do/t/topic/2538870",
    )


def test_compose_book_uses_stable_username_as_author() -> None:
    first = _post(1, "code4101", "<h2>正文</h2><p>主题内容。</p>")
    first["name"] = "code410"

    book = compose_linux_do_book(
        {"title": "头脑风暴", "posts_count": 1},
        [first],
        "https://linux.do/t/topic/1",
    )

    assert book.author == "code4101"
    assert parse_linux_do_topic_url("https://linux.do/t/anything/2538870") == (
        2538870,
        "https://linux.do/t/topic/2538870",
    )


def test_reply_selection_rejects_popular_chatter_but_keeps_structured_content() -> None:
    chatter = _post(2, "reader", "<p>看不懂，狠狠收藏！</p>", likes=80)
    useful = _post(
        3,
        "engineer",
        "<p>这里可以补一个工程上的判断条件：当上下文超过窗口的三分之二时先做归约，"
        "并把事实、假设和待验证项分栏保存。这样回滚时不会把推测当成事实继续传播。</p>"
        "<ul><li>事实单独存储</li><li>假设标注来源</li><li>失败后回到最近检查点</li></ul>",
        likes=2,
    )
    selected = select_substantive_replies([chatter, useful], original_poster="author")
    assert [post["post_number"] for post, _soup in selected] == [3]


def test_reply_selection_does_not_keep_short_op_chatter_or_bare_image() -> None:
    short_op = _post(2, "author", "<p>刚刚试了一下，确实挺有意思的</p>")
    bare_image = _post(3, "author", "<p><a href='/uploads/a.png'><img src='/uploads/a.png' alt='image'></a></p>")
    useful_link = _post(4, "author", "<p>可视化工具：<a href='https://example.com/demo'>https://example.com/demo</a></p>")
    selected = select_substantive_replies(
        [short_op, bare_image, useful_link],
        original_poster="author",
    )
    assert [post["post_number"] for post, _soup in selected] == [4]


def test_compose_book_builds_shared_numbered_toc_html_and_markdown() -> None:
    first = _post(
        1,
        "author",
        "<p>接下来说说部分性质：</p>"
        "<h3>语义漂移</h3><p>正文一。</p>"
        "<p>接下来说说基础技巧：</p>"
        "<h4>语义退火</h4><p><img src='/uploads/a.png'>正文二。</p>",
    )
    reply = _post(2, "author", "<p>补充示例见 <a href='/t/topic/2'>这个链接</a>，可用于验证边界条件。</p>")
    topic = {"title": "测试文章", "posts_count": 2}

    book = compose_linux_do_book(topic, [first, reply], "https://linux.do/t/topic/1")

    assert [(item.number, item.title) for item in book.toc[:4]] == [
        ("1", "部分性质"),
        ("1.1", "语义漂移"),
        ("2", "基础技巧"),
        ("2.1", "语义退火"),
    ]
    assert '<h3 id="section-1-1">1.1 语义漂移</h3>' in book.content_html
    assert 'src="https://linux.do/uploads/a.png"' in book.content_html
    assert "### 1.1 语义漂移" in book.content_markdown
    assert book.selected_reply_count == 1
    assert book.estimated_page_count >= 1


def test_compose_book_removes_discourse_lightbox_metadata_without_losing_image_link() -> None:
    first = _post(
        1,
        "author",
        '<p><div class="lightbox-wrapper">'
        '<a class="lightbox" data-download-href="/uploads/a.png?dl=1" '
        'href="/original/a.png">'
        '<img src="/optimized/a.png" width="655" height="500">'
        '<div class="meta"><svg><use href="#far-image"></use></svg>'
        '<span class="filename">image</span>'
        '<span class="informations">1494×1140 118 KB</span>'
        '<svg><use href="#expand"></use></svg></div>'
        '</a></div></p>',
    )

    book = compose_linux_do_book(
        {"title": "图片测试", "posts_count": 1},
        [first],
        "https://linux.do/t/topic/1",
    )

    assert 'class="imported-book-image"' in book.content_html
    assert 'class="imported-book-image-link"' in book.content_html
    assert 'href="https://linux.do/original/a.png"' in book.content_html
    assert 'src="https://linux.do/optimized/a.png"' in book.content_html
    assert 'class="meta"' not in book.content_html
    assert "1494×1140 118 KB" not in book.content_html
    assert "<svg" not in book.content_html


def test_compose_book_formats_selected_reply_as_named_question_and_answer() -> None:
    first = _post(1, "author", "<h2>正文</h2><p>主题内容。</p>")
    reply = _post(
        2,
        "answerer",
        '<aside class="quote" data-display-name="提问者">'
        '<div class="title">提问者：</div><blockquote><p>这里应该怎么处理？</p></blockquote>'
        '</aside><p>可以先保留事实，再验证假设；这样不容易把推测继续传播。</p>'
        '<ul><li>事实单独保存</li><li>假设标记来源</li></ul>',
    )
    reply["display_username"] = "回答者"

    book = compose_linux_do_book(
        {"title": "对话测试", "posts_count": 2},
        [first, reply],
        "https://linux.do/t/topic/1",
    )

    assert "第 2 楼 ·" not in book.content_html
    assert "查看原楼层" not in book.content_html
    assert "提问者 提问" in book.content_html
    assert "回答者 回复" in book.content_html
    assert "这里应该怎么处理？" in book.content_html
    discussion_item = next(item for item in book.toc if item.source_post_number == 2)
    assert discussion_item.number == ""
    assert ">第 2 楼<" in book.content_html
    assert ">2.1 第 2 楼<" not in book.content_html

    discussion_item.number = "2.1"
    book.content_html = book.content_html.replace(">第 2 楼<", ">2.1 第 2 楼<")
    normalized = normalize_discussion_numbering(book)
    assert discussion_item.number == ""
    assert ">第 2 楼<" in normalized.content_html
    assert ">2.1 第 2 楼<" not in normalized.content_html
