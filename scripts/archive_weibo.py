from __future__ import annotations

import argparse
import sys
from pathlib import Path

from DrissionPage import Chromium, ChromiumOptions

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.core.library.weibo_archive import (
    WeiboArchiveStore,
    cache_weibo_media,
    crawl_weibo_profile,
    export_markdown,
    load_batch_jsonl,
    write_yearly_book_json,
)
from backend.core.settings import get_settings
from backend.core.resources.storage import ATTACHMENTS_URL_PREFIX, get_attachments_dir


DEFAULT_UID = "2273105342"
DEFAULT_TITLE = "武陵惟海老头子微博摘录"


def parse_args() -> argparse.Namespace:
    default_root = get_settings().data_dir / "library" / "weibo" / DEFAULT_UID
    parser = argparse.ArgumentParser(description="使用已开启调试端口的 Chrome 归档个人微博。")
    parser.add_argument("--uid", default=DEFAULT_UID)
    parser.add_argument("--debug-address", default="127.0.0.1:9222")
    parser.add_argument("--output-dir", type=Path, default=default_root)
    parser.add_argument("--max-posts", type=int, default=100)
    parser.add_argument("--title", default=DEFAULT_TITLE)
    parser.add_argument("--author", default="惟海法师")
    parser.add_argument("--book-json", type=Path)
    parser.add_argument(
        "--cache-media",
        action="store_true",
        help="把微博图片缓存到 CodeYun 附件目录，并在书中改用本地地址。",
    )
    parser.add_argument(
        "--import-batches",
        type=Path,
        help="导入由已登录 Chrome 采集的 JSONL 批次；提供后不再连接调试端口。",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir.resolve()
    store = WeiboArchiveStore(output_dir / "weibo.sqlite3")

    if args.import_batches:
        posts = load_batch_jsonl(args.import_batches)
        store.upsert_many(posts)
        markdown_path = export_markdown(
            store,
            uid=args.uid,
            path=output_dir / "微博摘录.md",
            title=args.title,
        )
        media_result = (
            cache_weibo_media(
                store,
                uid=args.uid,
                directory=get_attachments_dir() / "weibo" / args.uid,
                url_prefix=f"{ATTACHMENTS_URL_PREFIX}/weibo/{args.uid}",
            )
            if args.cache_media
            else None
        )
        book_path = (
            write_yearly_book_json(
                store,
                uid=args.uid,
                path=args.book_json.resolve(),
                title=args.title,
                author=args.author,
            )
            if args.book_json
            else None
        )
        print(
            {
                "uid": args.uid,
                "imported": len(posts),
                "post_count": store.count(args.uid),
                "database": str(store.path),
                "markdown": str(markdown_path),
                "book_json": str(book_path) if book_path else "",
                "media": media_result,
            }
        )
        return 0

    options = ChromiumOptions(read_file=False)
    options.set_address(args.debug_address)
    try:
        browser = Chromium(options)
    except Exception as exc:
        raise SystemExit(
            f"无法连接 Chrome 调试地址 {args.debug_address}。"
            "请先让目标 Chrome 以 remote-debugging-port 启动，再重试。"
        ) from exc

    tab = browser.new_tab(background=False)
    try:
        result = crawl_weibo_profile(
            tab,
            uid=args.uid,
            store=store,
            max_posts=max(args.max_posts, 0),
        )
        markdown_path = export_markdown(
            store,
            uid=args.uid,
            path=output_dir / "微博摘录.md",
            title=args.title,
        )
        media_result = (
            cache_weibo_media(
                store,
                uid=args.uid,
                directory=get_attachments_dir() / "weibo" / args.uid,
                url_prefix=f"{ATTACHMENTS_URL_PREFIX}/weibo/{args.uid}",
            )
            if args.cache_media
            else None
        )
        book_path = (
            write_yearly_book_json(
                store,
                uid=args.uid,
                path=args.book_json.resolve(),
                title=args.title,
                author=args.author,
            )
            if args.book_json
            else None
        )
        print({
            **result,
            "markdown": str(markdown_path),
            "book_json": str(book_path) if book_path else "",
            "media": media_result,
        })
    finally:
        tab.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
