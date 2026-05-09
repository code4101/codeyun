from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.core.stock import EASTMONEY_HOME_URL, open_eastmoney_browser


def main() -> None:
    browser, tab, paths = open_eastmoney_browser(EASTMONEY_HOME_URL)
    try:
        print("已打开东方财富。")
        print(f"title: {tab.title}")
        print(f"url: {tab.url}")
        input("请在浏览器里完成登录，登录成功后回到这里按回车...")

        print("登录后页面状态：")
        print(f"title: {tab.title}")
        print(f"url: {tab.url}")
        print(f"用户数据目录: {paths.user_data_dir}")
        input("按回车关闭浏览器...")
    finally:
        browser.quit()


if __name__ == "__main__":
    main()
