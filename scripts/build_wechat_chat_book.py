from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

from sqlmodel import Session, select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.api.linux_do_books import upsert_derived_rich_text_book
from backend.api.wechat_archive import _open_wechat_db_storage
from backend.core.ai.app_config import AI_APP_WECHAT_CHAT_BOOK, resolve_ai_app_runtime_config
from backend.core.library.wechat_chat_book import (
    book_cache_key,
    generate_wechat_chat_book,
    write_snapshot,
)
from backend.db import engine
from backend.models import User


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a library book from one WeChat group chat.")
    parser.add_argument("--chat", required=True, help="Exact group name or chat username.")
    parser.add_argument("--title", default="", help="Book title.")
    parser.add_argument("--user", default="code4101", help="CodeYun owner username.")
    parser.add_argument("--device-id", default="", help="Local WeChat data device id.")
    parser.add_argument("--bookshelf-id", default="", help="Optional target bookshelf id.")
    parser.add_argument("--model", default="", help="Optional AI model override for this run.")
    parser.add_argument(
        "--extract-model",
        default="",
        help="Optional faster model used only for source extraction.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    storage = _open_wechat_db_storage(args.device_id or None)
    candidates = storage.list_chats(limit=20_000, q=args.chat, include_folded_entry=True)
    chat = next(
        (
            item
            for item in candidates
            if args.chat in {
                str(item.get("name") or ""),
                str(item.get("username") or ""),
            }
        ),
        None,
    )
    if chat is None:
        raise SystemExit(f"找不到微信群：{args.chat}")
    if str(chat.get("chat_type") or "") != "chatroom":
        raise SystemExit("当前成书功能只处理微信群聊")

    chat_username = str(chat.get("username") or "")
    chat_name = str(chat.get("name") or chat_username)
    title = args.title.strip() or (
        "未来社微信分部群志"
        if chat_name == "未来社微信分部"
        else f"{chat_name}群志"
    )
    resolved_device_id = args.device_id or "current"
    cache_key = book_cache_key(
        device_id=resolved_device_id,
        chat_username=chat_username,
        first_time=chat.get("first_time"),
        last_time=chat.get("last_time"),
        message_count=int(chat.get("message_count") or 0),
    )
    with Session(engine) as session:
        user = session.exec(select(User).where(User.username == args.user)).first()
        if user is None or user.id is None:
            raise SystemExit(f"CodeYun 用户不存在：{args.user}")
        runtime = resolve_ai_app_runtime_config(
            session=session,
            current_user=user,
            app_id=AI_APP_WECHAT_CHAT_BOOK,
        )
        if args.model.strip():
            runtime["model"] = args.model.strip()
        if args.extract_model.strip():
            runtime["leaf_model"] = args.extract_model.strip()
        user_id = int(user.id)
    print(
        f"cache_key={cache_key} chat={chat_name} messages={chat.get('message_count')} "
        f"provider={runtime.get('provider')} model={runtime.get('model')}",
        flush=True,
    )

    def progress(payload: dict) -> None:
        print(
            f"phase={payload.get('phase')} chunks={payload.get('done_count')}/{payload.get('target_count')} "
            f"synthesis={payload.get('synthesis_done_count')}/{payload.get('synthesis_target_count')}",
            flush=True,
        )

    document, snapshot = generate_wechat_chat_book(
        storage=storage,
        user_id=user_id,
        cache_key=cache_key,
        chat_username=chat_username,
        chat_name=chat_name,
        title=title,
        runtime=runtime,
        progress_callback=progress,
    )
    source_digest = hashlib.sha256(chat_username.encode("utf-8")).hexdigest()[:24]
    with Session(engine) as session:
        user = session.get(User, user_id)
        if user is None:
            raise SystemExit("成书用户已不存在")
        book = upsert_derived_rich_text_book(
            session=session,
            current_user=user,
            document=document,
            source_kind=f"wechat-chat-book:{source_digest}",
            book_kind="wechat-chat-book",
            bookshelf_id=args.bookshelf_id or None,
            cover_color="#526f5d",
            metadata={
                "chat_username": chat_username,
                "chat_name": chat_name,
                "cache_key": cache_key,
                "pipeline_version": snapshot.get("pipeline_version"),
                "statistics": snapshot.get("statistics"),
            },
        )
    snapshot["book_id"] = book.id
    snapshot["library_path"] = "/notes/library"
    write_snapshot(user_id, cache_key, snapshot)
    print(
        f"book_id={book.id} title={book.title} chapters={book.post_count} "
        f"estimated_pages={book.estimated_page_count}",
        flush=True,
    )


if __name__ == "__main__":
    main()
