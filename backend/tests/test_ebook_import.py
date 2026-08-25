from __future__ import annotations

import zipfile
from pathlib import Path

from backend.core.library.ebook_import import import_ebook


def _write_epub(path: Path) -> None:
    container = """<?xml version="1.0"?>
    <container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
      <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
    </container>"""
    package = """<?xml version="1.0" encoding="utf-8"?>
    <package xmlns="http://www.idpf.org/2007/opf" version="3.0">
      <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
        <dc:title>测试电子书</dc:title><dc:creator>测试作者</dc:creator>
      </metadata>
      <manifest>
        <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
        <item id="cover" href="cover.png" media-type="image/png" properties="cover-image"/>
      </manifest>
      <spine><itemref idref="chapter"/></spine>
    </package>"""
    chapter = """<html><head><title>第一章</title></head><body>
      <h1>第一章 入学</h1><p>正文内容。</p><img src="cover.png"/>
    </body></html>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", package)
        archive.writestr("OEBPS/chapter.xhtml", chapter)
        archive.writestr("OEBPS/cover.png", b"not-a-real-image")


def test_epub_import_reads_metadata_spine_and_rewrites_resources(tmp_path: Path):
    source = tmp_path / "book.epub"
    _write_epub(source)

    book = import_ebook(source, book_id="ebook:1:test")

    assert book.format == "epub"
    assert book.title == "测试电子书"
    assert book.author == "测试作者"
    assert [item.title for item in book.toc] == ["第一章 入学"]
    assert book.cover_resource_name == "cover.png"
    assert "正文内容" in book.content_text
    assert "/api/linux-do-books/ebook%3A1%3Atest/resources/" in book.content_html
    assert len(book.resources) == 2


def test_epub_import_builds_chapters_from_plain_text_directory(tmp_path: Path):
    source = tmp_path / "中国2185.epub"
    container = """<?xml version="1.0"?>
    <container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
      <rootfiles><rootfile full-path="content.opf" media-type="application/oebps-package+xml"/></rootfiles>
    </container>"""
    package = """<?xml version="1.0" encoding="utf-8"?>
    <package xmlns="http://www.idpf.org/2007/opf" version="2.0">
      <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
        <dc:title>中国2185</dc:title><dc:creator>未知</dc:creator>
      </metadata>
      <manifest>
        <item id="cover" href="titlepage.xhtml" media-type="application/xhtml+xml"/>
        <item id="part1" href="split_000.html" media-type="application/xhtml+xml"/>
        <item id="part2" href="split_001.html" media-type="application/xhtml+xml"/>
        <item id="cover-image" href="cover.jpg" media-type="image/jpeg" properties="cover-image"/>
      </manifest>
      <spine>
        <itemref idref="cover"/><itemref idref="part1"/><itemref idref="part2"/>
      </spine>
    </package>"""
    cover = """<html><head><title>Cover</title></head><body><svg><image href="cover.jpg"/></svg></body></html>"""
    first = """<html><head><title>i7oy4c</title></head><body>
      <p>中国2185</p><p>目录</p>
      <p>零 引子</p><p>一 最高执政官</p><p>二 复活</p>
      <p>零 引子</p><p>引子正文。</p>
      <p>一。最高执政官</p><p>第一章正文。</p>
    </body></html>"""
    second = """<html><head><title>i7oy4c</title></head><body>
      <p>第一章后半段。</p><p>二、复活</p><p>第二章正文。</p>
    </body></html>"""
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("content.opf", package)
        archive.writestr("titlepage.xhtml", cover)
        archive.writestr("split_000.html", first)
        archive.writestr("split_001.html", second)
        archive.writestr("cover.jpg", b"cover")

    source_with_author = source.with_name("中国2185 (刘慈欣) (z-library.sk).epub")
    source.rename(source_with_author)
    book = import_ebook(source_with_author, book_id="ebook:1:plain-directory")

    assert book.author == "刘慈欣"
    assert [item.title for item in book.toc] == ["零 引子", "一 最高执政官", "二 复活"]
    assert book.content_html.count("<article ") == 3
    assert "i7oy4c" not in book.content_html
    assert "目录" not in book.content_html
    assert "第一章后半段" in book.content_html
    assert book.cover_resource_name == "cover.jpg"


def test_text_import_becomes_a_readable_article(tmp_path: Path):
    source = tmp_path / "随笔.txt"
    source.write_text("第一段。\n\n第二段。", encoding="utf-8")

    book = import_ebook(source, book_id="ebook:1:text")

    assert book.format == "text"
    assert book.title == "随笔"
    assert len(book.toc) == 1
    assert "<p>第一段。</p>" in book.content_html


def test_gb18030_text_is_not_misread_as_utf16(tmp_path: Path):
    source = tmp_path / "旧书.txt"
    source.write_bytes("旧正文。\n\n第二段。".encode("gb18030"))

    book = import_ebook(source, book_id="ebook:1:gb18030")

    assert "旧正文" in book.content_html
    assert "第二段" in book.content_text


def test_html_import_unwraps_legacy_layout_table_and_builds_chapter_toc(tmp_path: Path):
    source = tmp_path / "旧网页.html"
    source.write_text(
        """
        <html>
          <head><title>旧网页书</title></head>
          <body>
            <table class="page-layout"><tr><td>
              <a href="/">站点首页</a> &gt; <a href="/author">作者</a>
              <hr>
              <p class="title">旧网页书</p>
              <h3>绪论</h3>
              第一段。<br><br>第二段。
              <h3>一 第一章</h3>
              第一章正文。
              <h3>二 第二章</h3>
              第二章正文。
            </td></tr></table>
          </body>
        </html>
        """,
        encoding="utf-8",
    )

    book = import_ebook(source, book_id="ebook:1:legacy-layout")

    assert [item.title for item in book.toc] == ["绪论", "一 第一章", "二 第二章"]
    assert [item.anchor for item in book.toc] == ["article-1", "article-2", "article-3"]
    assert book.content_html.count("<article ") == 3
    assert "<table" not in book.content_html
    assert "站点首页" not in book.content_html
    assert "第一段" in book.content_text
    assert "第二章正文" in book.content_text


def test_html_import_promotes_numbered_standalone_bold_sections(tmp_path: Path):
    source = tmp_path / "旧书内部小节.html"
    source.write_text(
        """
        <html>
          <head><title>旧书内部小节</title></head>
          <body>
            <h3>一 第一章</h3>
            <h4>思想意识的修养</h4>
            <b>一 要了解事业的艰难</b><br><br>
            <p>正文里有<strong>三个重点</strong>，这不是标题。</p>
            <b>二、个人利益服从集体利益</b><br><br>
            <h3>二 第二章</h3>
            <p><strong>（一）团结的重要性</strong></p>
          </body>
        </html>
        """,
        encoding="utf-8",
    )

    book = import_ebook(source, book_id="ebook:1:numbered-sections")

    assert "<h5>一 要了解事业的艰难</h5>" in book.content_html
    assert "<h5>二、个人利益服从集体利益</h5>" in book.content_html
    assert "<h4>（一）团结的重要性</h4>" in book.content_html
    assert "<strong>三个重点</strong>" in book.content_html


def test_html_import_preserves_real_data_table(tmp_path: Path):
    source = tmp_path / "数据表.html"
    source.write_text(
        """
        <html>
          <head><title>统计资料</title></head>
          <body>
            <h1>统计资料</h1>
            <table>
              <caption>年度数据</caption>
              <tr><th>年份</th><th>数量</th></tr>
              <tr><td>2025</td><td>12</td></tr>
            </table>
          </body>
        </html>
        """,
        encoding="utf-8",
    )

    book = import_ebook(source, book_id="ebook:1:data-table")

    assert len(book.toc) == 1
    assert "<table>" in book.content_html
    assert "<th>年份</th>" in book.content_html
    assert "年度数据" in book.content_text


def test_html_import_keeps_meaningful_preface_with_first_inferred_chapter(tmp_path: Path):
    source = tmp_path / "带前言.html"
    source.write_text(
        """
        <html>
          <head><title>有前言的书</title></head>
          <body>
            <p>这是一段不能丢失的编者前言，说明全书的整理原则和版本来源。</p>
            <h2>第一章</h2><p>甲。</p>
            <h2>第二章</h2><p>乙。</p>
          </body>
        </html>
        """,
        encoding="utf-8",
    )

    book = import_ebook(source, book_id="ebook:1:preface")

    assert [item.title for item in book.toc] == ["第一章", "第二章"]
    assert "编者前言" in book.content_html
    first_article = book.content_html.split("</article>", 1)[0]
    assert first_article.index("编者前言") < first_article.index("第一章")
