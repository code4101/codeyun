from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.core.stock import EASTMONEY_HOME_URL, capture_tab_snapshot, open_eastmoney_browser


def main() -> None:
    parser = argparse.ArgumentParser(description="打开东方财富页面并输出最小页面状态。")
    parser.add_argument("url", nargs="?", default=EASTMONEY_HOME_URL)
    parser.add_argument("--keep-open", action="store_true", help="输出状态后保持浏览器打开，便于人工观察。")
    args = parser.parse_args()

    browser, tab, paths = open_eastmoney_browser(args.url)
    try:
        snapshot = capture_tab_snapshot(tab)
        print(f"title: {snapshot.title}")
        print(f"url: {snapshot.url}")
        print(f"login_hint: {snapshot.login_hint}")
        print(f"user_data_dir: {paths.user_data_dir}")
        print("html_sample:")
        print(snapshot.html_sample[:800])
        if args.keep_open:
            input("按回车关闭浏览器...")
    finally:
        browser.quit()


if __name__ == "__main__":
    main()
