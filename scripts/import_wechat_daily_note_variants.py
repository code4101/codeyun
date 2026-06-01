from __future__ import annotations

import argparse
import html
import json
import sys
import time
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.core.ai_app_config import AI_APP_WECHAT_DAILY_SUMMARY, resolve_ai_app_runtime_config
from backend.core.ai_chat import OllamaClientError, chat_with_provider
from backend.core.note_identity import allocate_new_note_identity
from backend.core.note_progress import (
    NOTE_COMPLETION_PROGRESS_EXPR_FIELD,
    get_custom_field_value,
)
from backend.core.note_semantics import (
    NOTE_CATEGORY_DEFAULT,
    NOTE_FORM_DOCUMENT,
    NOTE_SCENE_DEFAULT,
    derive_legacy_semantics_from_taxonomy,
)
from backend.db import engine
from backend.models import NoteNode
from scripts.import_wechat_daily_notes import (
    FIELD_CHAT_NAME,
    FIELD_CHAT_USERNAME,
    FIELD_DATE,
    FIELD_SOURCE,
    AiSummaryDraft,
    DayChatSummary,
    _settings_wechat_db_storage_path,
    build_ai_system_prompt,
    build_ai_user_prompt,
    build_note_content,
    collect_day_summaries,
    compute_progress,
    compute_weight,
    custom_fields,
    extract_json_object,
    format_ts,
    normalize_ai_summary_payload,
    parse_day_range,
    resolve_user,
)
from scripts.wechat_daily_summary_variants import VARIANTS, WeChatSummaryVariant, variant_by_key


FIELD_VARIANT_KEY = "wechat_daily_variant_key"
FIELD_VARIANT_NAME = "wechat_daily_variant_name"
FIELD_VARIANT_INDEX = "wechat_daily_variant_index"
FIELD_VARIANT_SET = "wechat_daily_variant_set"
FIELD_VARIANT_SOURCE_HINT = "wechat_daily_variant_source_hint"

DEFAULT_VARIANT_SET = "summary_prompt_compare_20260601"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Star Map note comparison nodes for WeChat summary prompts.")
    parser.add_argument("--date", required=True, help="Date in YYYY-MM-DD, interpreted in Asia/Shanghai.")
    parser.add_argument("--chat", required=True, help="Only compare chats whose name, username or table contains this text.")
    parser.add_argument("--user", default="code4101", help="CodeYun username to own created notes.")
    parser.add_argument("--db-storage", default="", help="Override decrypted WeChat db_storage path.")
    parser.add_argument("--min-messages", type=int, default=1, help="Skip chats with fewer messages.")
    parser.add_argument("--limit-chats", type=int, default=1, help="Compare at most N matching chats.")
    parser.add_argument("--variant", action="append", default=[], help="Variant key to run; repeatable. Defaults to all.")
    parser.add_argument("--limit-variants", type=int, default=0, help="Run only first N variants after filtering.")
    parser.add_argument("--variant-set", default=DEFAULT_VARIANT_SET, help="Label for this comparison batch.")
    parser.add_argument("--ai-provider", default="", help="Override AI provider, defaults to app config.")
    parser.add_argument("--ai-model", default="", help="Override AI model, defaults to app config.")
    parser.add_argument("--ai-timeout", type=float, default=900.0, help="AI call timeout seconds per variant.")
    parser.add_argument("--ai-limit-chars", type=int, default=9000, help="Max transcript characters sent per variant.")
    parser.add_argument("--include-resource-context", action="store_true", default=True, help="Attach resource evidence.")
    parser.add_argument("--no-resource-context", dest="include_resource_context", action="store_false")
    parser.add_argument("--ocr-images", action="store_true", default=True, help="OCR exported images and include OCR text.")
    parser.add_argument("--no-ocr-images", dest="ocr_images", action="store_false")
    parser.add_argument("--dry-run", action="store_true", help="Only print planned nodes.")
    return parser.parse_args()


def select_variant_entries(keys: list[str], limit: int) -> list[tuple[int, WeChatSummaryVariant]]:
    if keys:
        key_set = set(keys)
        variants = [(index, variant) for index, variant in enumerate(VARIANTS, 1) if variant.key in key_set]
        missing_keys = [key for key in keys if key not in {variant.key for _, variant in variants}]
        if missing_keys:
            raise KeyError(", ".join(missing_keys))
    else:
        variants = list(enumerate(VARIANTS, 1))
    if limit > 0:
        variants = variants[:limit]
    return variants


def build_variant_system_prompt(variant: WeChatSummaryVariant) -> str:
    return f"""
{build_ai_system_prompt()}

本次是提示词风格横向对比，额外采用以下模板：
- 模板名称：{variant.name}
- 来源思路：{variant.source_hint}
- 风格要求：{variant.prompt}

请仍然严格输出同一个 JSON 结构，只允许包含 summary 和 notes。模板只是组织重点，不要新增其他顶层字段。
""".strip()


def build_variant_ai_draft(
    session: Session,
    user: Any,
    summary: DayChatSummary,
    day: str,
    variant: WeChatSummaryVariant,
    *,
    provider: str,
    model: str,
    timeout_seconds: float,
    char_limit: int,
) -> AiSummaryDraft:
    runtime = resolve_ai_app_runtime_config(
        session=session,
        current_user=user,
        app_id=AI_APP_WECHAT_DAILY_SUMMARY,
        provider=provider or None,
        model=model or None,
    )
    resolved_provider = str(runtime.get("provider") or "").strip()
    resolved_model = str(runtime.get("model") or "").strip()
    response = chat_with_provider(
        provider_id=resolved_provider,
        base_url=runtime.get("base_url"),
        api_key=runtime.get("api_key"),
        model=resolved_model,
        system_prompt=build_variant_system_prompt(variant),
        messages=[{"role": "user", "content": build_ai_user_prompt(summary, day, char_limit)}],
        response_format="json",
        temperature=0.25,
        timeout_seconds=timeout_seconds,
        extra_providers=tuple(runtime.get("extra_providers") or ()),
    )
    response_model = str(response.get("model") or resolved_model or "").strip()
    draft = normalize_ai_summary_payload(
        extract_json_object(response.get("content")),
        source=f"ai:{resolved_provider}:variant:{variant.key}",
        model=response_model,
    )
    return draft


def variant_custom_fields(
    summary: DayChatSummary,
    day: str,
    progress_expr: str,
    draft: AiSummaryDraft,
    variant: WeChatSummaryVariant,
    variant_index: int,
    variant_set: str,
) -> list[list[Any]]:
    fields = custom_fields(summary, day, progress_expr, draft)
    fields.extend(
        [
            [FIELD_VARIANT_KEY, "string", variant.key],
            [FIELD_VARIANT_NAME, "string", variant.name],
            [FIELD_VARIANT_INDEX, "number", variant_index],
            [FIELD_VARIANT_SET, "string", variant_set],
            [FIELD_VARIANT_SOURCE_HINT, "string", variant.source_hint],
        ]
    )
    return fields


def build_variant_content(
    summary: DayChatSummary,
    day: str,
    draft: AiSummaryDraft,
    variant: WeChatSummaryVariant,
) -> str:
    base_content = build_note_content(summary, day, draft)
    return f"""
<p><strong>对比模板</strong>：{html.escape(variant.name)}。{html.escape(variant.source_hint)}</p>
<p><strong>模板要求</strong>：{html.escape(variant.prompt)}</p>
{base_content}
""".strip()


def find_existing_variant_note(
    session: Session,
    user_id: int,
    day: str,
    chat_username: str,
    variant_key: str,
    variant_set: str,
    start_ts: int,
    end_ts: int,
) -> NoteNode | None:
    notes = session.exec(
        select(NoteNode)
        .where(NoteNode.user_id == user_id)
        .where(NoteNode.start_at >= start_ts)
        .where(NoteNode.start_at < end_ts)
    ).all()
    for note in notes:
        if note.deleted_at:
            continue
        fields = note.custom_fields
        if (
            get_custom_field_value(fields, FIELD_DATE) == day
            and get_custom_field_value(fields, FIELD_CHAT_USERNAME) == chat_username
            and get_custom_field_value(fields, FIELD_VARIANT_KEY) == variant_key
            and get_custom_field_value(fields, FIELD_VARIANT_SET) == variant_set
        ):
            return note
    return None


def upsert_variant_note(
    session: Session,
    user: Any,
    summary: DayChatSummary,
    day: str,
    draft: AiSummaryDraft,
    variant: WeChatSummaryVariant,
    variant_index: int,
    variant_set: str,
) -> str:
    day_start_ts, day_end_ts, _ = parse_day_range(day)
    progress = compute_progress(summary)
    progress_expr = f"{progress:.4f}"
    taxonomy = derive_legacy_semantics_from_taxonomy(
        [{"key": NOTE_CATEGORY_DEFAULT, "weight": 100}],
        primary_category=NOTE_CATEGORY_DEFAULT,
        note_form=NOTE_FORM_DOCUMENT,
        note_scene=NOTE_SCENE_DEFAULT,
        lifecycle_stage="done",
    )
    title = f"{summary.name} · {variant_index:02d} {variant.name}"
    data = {
        "title": title,
        "content": build_variant_content(summary, day, draft, variant),
        "weight": compute_weight(summary),
        "weight_mode": None,
        "private_level": 0,
        "custom_fields": variant_custom_fields(summary, day, progress_expr, draft, variant, variant_index, variant_set),
        "start_at": float(summary.first_ts + variant_index * 60),
        "updated_at": time.time(),
        **taxonomy,
    }

    existing = find_existing_variant_note(
        session,
        int(user.id),
        day,
        summary.username,
        variant.key,
        variant_set,
        day_start_ts,
        day_end_ts,
    )
    if existing is not None:
        for key, value in data.items():
            setattr(existing, key, value)
        session.add(existing)
        return "updated"

    note_identity = allocate_new_note_identity(session)
    note = NoteNode(
        id=note_identity.primary_id,
        numeric_id=note_identity.numeric_id,
        legacy_id=note_identity.legacy_id,
        user_id=int(user.id),
        created_at=time.time(),
        history=[],
        color=None,
        **data,
    )
    session.add(note)
    return "created"


def main() -> None:
    args = parse_args()
    db_storage = Path(args.db_storage).expanduser() if args.db_storage else _settings_wechat_db_storage_path()
    if not db_storage.exists():
        raise SystemExit(f"WeChat db_storage not found: {db_storage}")

    summaries = collect_day_summaries(
        db_storage,
        args.date,
        args.min_messages,
        include_resource_context=args.include_resource_context,
        ocr_images=args.ocr_images,
    )
    keyword = args.chat.strip().lower()
    summaries = [
        item
        for item in summaries
        if keyword in item.name.lower()
        or keyword in item.username.lower()
        or keyword in item.table_name.lower()
    ]
    if args.limit_chats > 0:
        summaries = summaries[: args.limit_chats]
    variant_entries = select_variant_entries(args.variant, args.limit_variants)

    if args.dry_run:
        print(f"db_storage={db_storage}")
        print(f"date={args.date}, chats={len(summaries)}, variants={len(variant_entries)}, variant_set={args.variant_set}")
        for summary in summaries:
            print(
                f"chat={summary.name} username={summary.username} "
                f"messages={summary.message_count} text={summary.text_count} resources={summary.resource_count}"
            )
        for index, variant in variant_entries:
            print(f"{index:02d} {variant.key}: {variant.name} - {variant.source_hint}")
        return

    created = 0
    updated = 0
    failed = 0
    with Session(engine) as session:
        user = resolve_user(session, args.user)
        for summary in summaries:
            for index, variant in variant_entries:
                try:
                    draft = build_variant_ai_draft(
                        session,
                        user,
                        summary,
                        args.date,
                        variant,
                        provider=args.ai_provider,
                        model=args.ai_model,
                        timeout_seconds=args.ai_timeout,
                        char_limit=args.ai_limit_chars,
                    )
                    result = upsert_variant_note(
                        session,
                        user,
                        summary,
                        args.date,
                        draft,
                        variant,
                        index,
                        args.variant_set,
                    )
                    if result == "created":
                        created += 1
                    else:
                        updated += 1
                    session.commit()
                    print(f"{result}: {summary.name} · {index:02d} {variant.name} ({draft.model})")
                except (OllamaClientError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
                    failed += 1
                    session.rollback()
                    print(f"failed: {summary.name} · {index:02d} {variant.name}: {exc}", file=sys.stderr)

    print(
        f"Generated WeChat summary variants for {args.date}: "
        f"created={created}, updated={updated}, failed={failed}, chats={len(summaries)}, variants={len(variant_entries)}"
    )


if __name__ == "__main__":
    main()
