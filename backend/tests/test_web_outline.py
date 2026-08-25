from __future__ import annotations

import pytest

from backend.core.tools.web_outline import (
    SourceHeading,
    WebOutlineError,
    _canonical_article_url,
    _decode_html,
    build_rule_outline,
    extract_source_headings,
    normalize_article_url,
    number_outline,
    render_markdown,
    validate_public_url,
)


def _resolver_for(*addresses: str):
    def resolve(*_args, **_kwargs):
        return [(2, 1, 6, "", (address, 443)) for address in addresses]

    return resolve


def test_validate_public_url_rejects_private_and_mixed_dns_answers():
    with pytest.raises(WebOutlineError, match="内网"):
        validate_public_url("https://example.com/a", resolver=_resolver_for("127.0.0.1"))
    with pytest.raises(WebOutlineError, match="内网"):
        validate_public_url(
            "https://example.com/a",
            resolver=_resolver_for("93.184.216.34", "10.0.0.8"),
        )


def test_validate_public_url_accepts_public_https_address():
    assert (
        validate_public_url(
            "https://example.com/article",
            resolver=_resolver_for("93.184.216.34"),
        )
        == "https://example.com/article"
    )


def test_validate_public_url_accepts_proxy_fake_ip_only_for_domain_names():
    resolver = _resolver_for("198.18.0.45")
    assert validate_public_url("https://linux.do/topic/1", resolver=resolver) == "https://linux.do/topic/1"
    with pytest.raises(WebOutlineError, match="内网"):
        validate_public_url("https://198.18.0.45/topic/1", resolver=resolver)


def test_decode_html_prefers_document_charset_over_http_fallback():
    raw = '<html><head><meta charset="utf-8"></head><body>章节 ¶</body></html>'.encode("utf-8")
    assert "章节 ¶" in _decode_html(raw, "text/html", "iso-8859-1")


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("https://forum.example/t/topic/2538870/30", "https://forum.example/t/topic/2538870/30"),
        ("https://example.com/article#section-2", "https://example.com/article"),
        ("https://forum.example/article/2538870/30", "https://forum.example/article/2538870/30"),
    ],
)
def test_normalize_article_url_does_not_guess_site_routes(source: str, expected: str):
    assert normalize_article_url(source) == expected


def test_canonical_article_url_uses_standard_page_metadata():
    html = '<html><head><link rel="canonical" href="/articles/42#comments"></head></html>'
    assert _canonical_article_url(html, "https://example.com/view/42?page=2") == "https://example.com/articles/42"

    paged = '<html><head><link rel="canonical" href="/articles/42?page=3&amp;lang=zh"></head></html>'
    assert _canonical_article_url(paged, "https://example.com/articles/42/segment") == (
        "https://example.com/articles/42?lang=zh"
    )


def test_extract_and_number_outline_from_article_html():
    html = """
    <html><head><title>测试文章 - 网站</title></head><body>
      <nav><h2>导航标题</h2></nav>
      <article>
        <h1>测试文章</h1>
        <p>这是一段足够长的文章介绍，用来确保正文节点会被识别为候选正文区域。</p>
        <h3>第一部分</h3><p>第一部分的正文说明。</p>
        <h4>具体方法</h4><p>具体方法的正文说明。</p>
        <h3>第二部分</h3><p>第二部分的正文说明。</p>
      </article>
    </body></html>
    """
    title, headings = extract_source_headings(html, "https://example.com/article")
    assert title == "测试文章"
    assert [item.title for item in headings] == ["测试文章", "第一部分", "具体方法", "第二部分"]

    items = number_outline(build_rule_outline(title, headings))
    assert [(item["number"], item["title"]) for item in items] == [
        ("", "测试文章"),
        ("1", "第一部分"),
        ("1.1", "具体方法"),
        ("2", "第二部分"),
    ]
    assert "## 1 第一部分" in render_markdown(items)


def test_extract_repeated_content_blocks_chooses_heading_rich_article():
    html = """
    <html><head><meta property="og:title" content="主题标题"></head><body class="crawler">
      <noscript>
        <div class="topic-body crawler-post"><div class="post">
          <p>正文内容足够长，用于模拟 Discourse 面向搜索引擎输出的 crawler 页面结构。</p>
          <h3>正文标题</h3><p>正文说明。</p>
        </div></div>
        <div class="topic-body crawler-post"><div class="post">
          <p>这是一条比首帖更长的回复。过去的实现会按文本长度选择正文，因此可能错误选中这条回复。</p>
          <p>回复继续补充大量文字，但它不是待提取目录的原始文章，也不应参与首帖的目录生成。</p>
          <h3>回复里的标题</h3>
        </div></div>
      </noscript>
      <div id="related-topics"><h3>Related topics</h3></div>
    </body></html>
    """
    title, headings = extract_source_headings(html, "https://forum.example/t/1")
    assert title == "主题标题"
    assert [item.title for item in headings] == ["正文标题"]


def test_extract_prefers_complete_semantic_article_over_dense_subsection():
    html = """
    <html><head><title>通用文章</title></head><body>
      <article>
        <h1>通用文章</h1>
        <p>文章导言包含足够正文，用于验证完整文章候选不会被较短的小节覆盖。</p>
        <section><h2>第一章</h2><p>第一章正文。</p><h3>第一节</h3><p>第一节正文。</p></section>
        <section><h2>第二章</h2><p>第二章正文。</p></section>
      </article>
    </body></html>
    """
    title, headings = extract_source_headings(html, "https://example.com/article")
    assert title == "通用文章"
    assert [item.title for item in headings] == ["通用文章", "第一章", "第一节", "第二章"]


def test_rule_outline_uses_announced_sections_without_ai():
    headings = [
        SourceHeading(0, "性质甲", 3, "前文：以下是部分性质： 后文：说明"),
        SourceHeading(1, "性质乙", 3, "前文：说明 后文：说明"),
        SourceHeading(2, "技巧甲", 4, "前文：性质先写到这里，接下来说说基础技巧： 后文：说明"),
        SourceHeading(3, "技巧乙", 3, "前文：说明 后文：说明"),
        SourceHeading(4, "技巧乙的细节", 4, "前文：说明 后文：说明"),
    ]
    items = build_rule_outline("文章", headings)
    assert [(item["title"], item["level"]) for item in items] == [
        ("文章", 1),
        ("部分性质", 2),
        ("性质甲", 3),
        ("性质乙", 3),
        ("基础技巧", 2),
        ("技巧甲", 3),
        ("技巧乙", 3),
        ("技巧乙的细节", 4),
    ]
