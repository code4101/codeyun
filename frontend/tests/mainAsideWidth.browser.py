"""Run against Vite: uv run --with playwright python frontend/tests/mainAsideWidth.browser.py."""
import asyncio
from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=True)
        try:
            page = await browser.new_page(viewport={"width": 1024, "height": 768}, has_touch=True)
            await page.goto("http://localhost:5173/")
            aside = page.locator(".main-aside")
            await aside.wait_for()
            toggle = page.locator(".toggle-button")
            if await toggle.get_attribute("aria-expanded") == "false":
                await toggle.tap()
            await page.wait_for_timeout(600)
            # Exercise the actual menu open/close callbacks repeatedly with touch.
            title = aside.locator(".el-sub-menu__title").first
            if await title.count():
                widths = []
                for _ in range(12):
                    await title.tap()
                    await page.wait_for_timeout(400)
                    widths.append((await aside.bounding_box())["width"])
                assert max(widths[::2]) - min(widths[::2]) <= 1, widths
                assert max(widths[1::2]) - min(widths[1::2]) <= 1, widths
                print("Repeated touch menu widths:", widths)
            result = await page.evaluate("""async () => {
              const {measureMenuEntryWidth} = await import('/src/layout/measureMenuEntryWidth.ts');
              const aside = document.querySelector('.main-aside');
              const entry = aside.querySelector('.el-menu-item');
              const original = aside.style.width;
              // Demonstrate the former feedback loop using the real rendered row.
              const oldWidths = [];
              aside.style.transition = 'none';
              let oldWidth = 200;
              for (let i = 0; i < 12; i++) {
                aside.style.width = `${oldWidth}px`;
                oldWidth = Math.min(420, Math.max(200, entry.scrollWidth + 12));
                oldWidths.push(oldWidth);
              }
              const widths = [200, 260, 340, 420].map(width => {
                aside.style.width = `${width}px`;
                aside.style.transition = 'none';
                return measureMenuEntryWidth(entry);
              });
              const count = entry.parentElement.children.length;
              for (let i = 0; i < 20; i++) measureMenuEntryWidth(entry);
              if (entry.parentElement.children.length !== count) throw new Error('measurement clone leaked');
              const longEntry = entry.cloneNode(true);
              longEntry.textContent = 'A very long navigation label that should expand the sidebar';
              entry.parentElement.append(longEntry);
              const longWidth = measureMenuEntryWidth(longEntry);
              longEntry.remove();
              aside.style.width = original;
              aside.style.transition = '';
              return { widths, longWidth, oldWidths };
            }""")
            assert max(result["widths"]) - min(result["widths"]) <= 1, result
            assert result["longWidth"] > max(result["widths"]), result
            assert result["oldWidths"][-1] > result["oldWidths"][0], result
            print("Intrinsic widths independent of parent; long label:", result)
        finally:
            await browser.close()


asyncio.run(main())
