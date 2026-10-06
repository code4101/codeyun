"""Full reader route: fixed frame and independent long-content scroll regions.

Run with Vite: uv run --with playwright python frontend/tests/readerScrolling.browser.py
CODEYUN_BROWSER_TEST_URL can select a deployed frontend. All API requests are
fixtures, so this isolated browser never reads or changes an owner's books.
"""
import asyncio
import os
import re
from urllib.parse import urlparse

from playwright.async_api import async_playwright


async def main():
    toc = [dict(title=f'Chapter {i}', number=str(i), level=1, anchor=f'c{i}',
                parent_anchor=None, source_post_number=None, inferred=False) for i in range(180)]
    content = ''.join(
        f'<article data-article-id="c{i}"><h1>Chapter {i}</h1>'
        + ''.join(f'<h2>Section {j}</h2><p>{"scroll fixture " * 6}</p>'
                  for j in range(60 if i == 0 else 1)) + '</article>' for i in range(180))
    book = dict(id='scroll-fixture', title='Scroll fixture', author='', revision='1',
                book_kind='html', format='html', content_html=content, content_markdown='', toc=toc,
                capabilities=dict(edit_mode=None, can_annotate=False, can_edit_content=False,
                                  source_policy='owned'), bookshelf_placement={})

    async def fixture(route):
        path = urlparse(route.request.url).path.removeprefix('/api/')
        data = []
        second = path.endswith('999998') or path.endswith('scroll-fixture-2')
        if path in ('reader-workspace/resources/999999', 'reader-workspace/resources/999998'):
            data = dict(kind='ebook', id='scroll-fixture-2' if second else book['id'],
                        publicId=999998 if second else 999999, title='Second fixture' if second else book['title'])
        elif path in ('linux-do-books/scroll-fixture', 'linux-do-books/scroll-fixture-2'):
            data = {**book, 'id': 'scroll-fixture-2' if second else book['id'],
                    'title': 'Second fixture' if second else book['title']}
        elif path.endswith('reading-state'):
            data = dict(chapter_id='c0', character_offset=0)
        await route.fulfill(json=data)

    selectors = ['.book-document', '.reader-tree', '.rich-text-outline-list', '.book-search-results']
    async def state(page):
        return await page.evaluate("""selectors => ({
            scroll: selectors.map(s => document.querySelector(s).scrollTop),
            frames: ['.workspace-menu', '.dock-workspace', '.reader-content', '.dock-region.left',
                '.dock-region.right', '.dock-region.bottom'].map(s => {
                const r = document.querySelector(s).getBoundingClientRect();
                return [r.x, r.y, r.width, r.height];
            }),
            outer: [document.scrollingElement.scrollTop, document.querySelector('.page-shell-main').scrollTop],
            overflow: getComputedStyle(document.querySelector('.page-shell-main')).overflowY
        })""", selectors)

    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='chrome', headless=True)
        try:
            page = await browser.new_page()
            await page.route(lambda url: urlparse(url).path.startswith('/api/'), fixture)
            await page.add_init_script("""localStorage.setItem('codeyun.reader-workspace:guest',JSON.stringify({
                version:1,tabs:[{kind:'ebook',id:'scroll-fixture',title:'Scroll fixture',publicId:999999},
                    {kind:'ebook',id:'scroll-fixture-2',title:'Second fixture',publicId:999998}],
                active:'ebook:scroll-fixture',shelfOpen:true,shelfActive:false,layout:{version:2,regions:{
                    left:{visible:true,size:250,tools:['toc'],active:['toc']},
                    right:{visible:true,size:250,tools:['outline'],active:['outline']},
                    bottom:{visible:true,size:200,tools:['search'],active:['search']}
                }}}))""")
            base = os.environ.get('CODEYUN_BROWSER_TEST_URL', 'http://localhost:5173')
            for ui in (1, 2):
                await page.set_viewport_size(dict(width=1440, height=900))
                await page.goto(f'{base}/reader?id=999999&ui={ui}')
                await page.locator('.book-document p').first.wait_for()
                await page.get_by_placeholder('搜索全文', exact=True).fill('fixture')
                await page.locator('.book-search-result').first.wait_for()
                for width, height in ((1440, 900), (1024, 768), (700, 600), (900, 420)):
                    await page.set_viewport_size(dict(width=width, height=height))
                    await page.wait_for_timeout(300)
                    for selector in selectors:
                        await page.locator(selector).evaluate('e => e.scrollTop = 0')
                    initial = await state(page)
                    assert await page.locator('.page-shell-main > .pane-body').evaluate("e => getComputedStyle(e).overflowY") == 'hidden', initial
                    assert initial['outer'] == [0, 0], initial
                    assert all(y >= 0 and y + h <= height + 1 for _, y, _, h in initial['frames']), initial
                    for index, selector in enumerate(selectors):
                        before = await state(page)
                        area = page.locator(selector)
                        bounds = await area.bounding_box()
                        await page.mouse.move(bounds['x'] + bounds['width'] / 2,
                                              bounds['y'] + bounds['height'] / 2)
                        await page.mouse.wheel(0, 200)
                        await page.wait_for_timeout(300)
                        after = await state(page)
                        assert after['scroll'][index] > before['scroll'][index], (selector, before, after)
                        assert all(after['scroll'][i] == before['scroll'][i] for i in range(4) if i != index), (selector, before, after)
                        assert after['frames'] == before['frames'] and after['outer'] == [0, 0], (selector, after)
                        # Reaching the scroll boundary must not move the containing workbench.
                        await area.evaluate('e => e.scrollTop = e.scrollHeight')
                        await page.mouse.wheel(0, 800)
                        await page.wait_for_timeout(150)
                        assert (await state(page))['outer'] == [0, 0]
                    print(f'PASS ui={ui} {width}x{height}: body/TOC/outline/bottom scroll independently')
                await page.set_viewport_size(dict(width=1440, height=900))
                for name in ('Second fixture', 'Scroll fixture', '书架', 'Second fixture', 'Scroll fixture'):
                    await page.get_by_role('tab', name=re.compile('^' + name)).filter(visible=True).click()
                    if name == '书架':
                        continue
                    area = page.locator('.book-document:visible')
                    await area.wait_for()
                    await area.evaluate('e => e.scrollTop = 0')
                    bounds = await area.bounding_box()
                    tabs = await page.locator('.editor-tab-strip:visible').bounding_box()
                    await page.mouse.move(bounds['x'] + bounds['width'] / 2, bounds['y'] + bounds['height'] / 2)
                    await page.mouse.wheel(0, 200)
                    await page.wait_for_timeout(250)
                    assert await area.evaluate('e => e.scrollTop') > 0
                    assert await page.locator('.editor-tab-strip:visible').bounding_box() == tabs
                print(f'PASS ui={ui}: restored tabs and return from bookshelf retain body scrolling')
        finally:
            await browser.close()


asyncio.run(main())

