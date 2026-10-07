"""Chapter deep links override saved progress and stay scoped to their book.

Run with Vite: uv run --with playwright python frontend/tests/readerChapterLinks.browser.py
All APIs are fixtures; the test never changes an owner's reading state.
"""
import asyncio
import os
from urllib.parse import urlparse

from playwright.async_api import async_playwright


async def main():
    toc = [dict(title=f'Chapter {i}', number=str(i), level=1, anchor=f'c{i}',
                parent_anchor=None, source_post_number=None, inferred=False) for i in range(3)]
    book = dict(id='chapter-fixture', title='Chapter fixture', author='', revision='1',
                book_kind='html', format='html', content_markdown='', toc=toc,
                content_html=''.join(f'<article data-article-id="c{i}"><h1>Chapter {i}</h1>'
                                     + '<p>Long chapter body.</p>' * 100 + '</article>' for i in range(3)),
                capabilities=dict(can_annotate=False, can_edit_content=False, source_policy='owned'),
                bookshelf_placement={})

    async def fixture(route):
        path = urlparse(route.request.url).path.removeprefix('/api/')
        second = '999998' in path or 'chapter-second' in path
        data = []
        if path.startswith('reader-workspace/resources/'):
            data = dict(kind='ebook', id='chapter-second' if second else book['id'],
                        publicId=999998 if second else 999999, title=book['title'])
        elif path.endswith('reading-state'):
            data = dict(chapter_id='c2', character_offset=500)
        elif path.startswith('linux-do-books/') and '/annotations' not in path:
            data = {**book, 'id': 'chapter-second' if second else book['id']}
        await route.fulfill(json=data)

    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='chrome', headless=True)
        try:
            page = await browser.new_page(viewport=dict(width=1440, height=900))
            await page.route(lambda url: urlparse(url).path.startswith('/api/'), fixture)
            base = os.environ.get('CODEYUN_BROWSER_TEST_URL', 'http://localhost:5173')

            async def heading(expected, at_top=False):
                await page.wait_for_function("expected => [...document.querySelectorAll('.book-document:not(.is-loading)')].find(e => e.offsetWidth)?.querySelector('h1')?.textContent === expected", arg=expected)
                if at_top:
                    await page.wait_for_function("[...document.querySelectorAll('.book-document:not(.is-loading)')].find(e => e.offsetWidth)?.scrollTop < 2")

            await page.goto(f'{base}/reader?id=999999&chapter=c0')
            await heading('Chapter 0', True)
            assert 'chapter=c0' in page.url
            await page.locator('.book-document:visible').evaluate('e => e.scrollTop = 800')
            # Browser history navigation exercises the mounted-reader route watcher.
            await page.evaluate("history.pushState({}, '', '/reader?id=999999&chapter=c1'); dispatchEvent(new PopStateEvent('popstate'))")
            await heading('Chapter 1', True)
            await page.goto(f'{base}/reader?id=999999')
            await heading('Chapter 2')
            await page.goto(f'{base}/reader?id=999999&chapter=missing')
            await heading('Chapter 2')
            await page.goto(f'{base}/reader?id=999998&chapter=c0')
            await heading('Chapter 0', True)
            print('PASS: explicit chapter, mounted navigation, saved-position fallback, invalid anchor, book scope')
        finally:
            await browser.close()


asyncio.run(main())
