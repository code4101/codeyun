from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Callable, Iterable, Literal

from backend.core.ai.chat import chat_with_provider
from backend.core.settings import get_settings


SKILL_BOOK_TRANSLATION_VERSION = 1
SKILL_BOOK_TRANSLATION_LANGUAGE = "zh-CN"
SKILL_BOOK_TRANSLATION_CHUNK_SIZE = 10_000

SourceLanguage = Literal["zh", "en", "mixed"]
TranslationStatus = Literal["not_needed", "missing", "pending", "done", "error"]


@dataclass(frozen=True)
class SkillTranslationSource:
    chapter_id: str
    relative_path: str
    source_revision: str
    markdown: str


def detect_markdown_language(markdown: str) -> SourceLanguage:
    prose = re.sub(r"```[\s\S]*?```|~~~[\s\S]*?~~~", " ", markdown)
    prose = re.sub(r"`[^`\n]+`", " ", prose)
    prose = re.sub(r"https?://\S+", " ", prose)
    latin_count = len(re.findall(r"[A-Za-z]", prose))
    cjk_count = len(re.findall(r"[\u3400-\u9fff]", prose))
    total = latin_count + cjk_count
    if total < 40:
        return "mixed"
    cjk_ratio = cjk_count / total
    if cjk_ratio >= 0.28:
        return "zh"
    if cjk_ratio <= 0.08 and latin_count >= 80:
        return "en"
    return "mixed"


def translation_root() -> Path:
    return (
        get_settings().data_dir
        / "library"
        / "skill-book"
        / "translations"
        / SKILL_BOOK_TRANSLATION_LANGUAGE
    )


def translation_snapshot_path(chapter_id: str) -> Path:
    digest = hashlib.sha256(chapter_id.encode("utf-8")).hexdigest()
    return translation_root() / f"{digest}.json"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.{time.time_ns()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def read_translation_snapshot(chapter_id: str) -> dict[str, Any] | None:
    path = translation_snapshot_path(chapter_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def translation_state(
    *,
    chapter_id: str,
    source_revision: str,
    source_language: SourceLanguage,
) -> dict[str, Any]:
    if source_language != "en":
        return {
            "status": "not_needed",
            "language": SKILL_BOOK_TRANSLATION_LANGUAGE,
            "source_revision": source_revision,
            "revision": "",
            "markdown": "",
            "updated_at": None,
            "error_message": "",
        }
    snapshot = read_translation_snapshot(chapter_id)
    if (
        snapshot is None
        or int(snapshot.get("version") or 0) != SKILL_BOOK_TRANSLATION_VERSION
        or str(snapshot.get("source_revision") or "") != source_revision
    ):
        return {
            "status": "missing",
            "language": SKILL_BOOK_TRANSLATION_LANGUAGE,
            "source_revision": source_revision,
            "revision": "",
            "markdown": "",
            "updated_at": None,
            "error_message": "",
        }
    status = str(snapshot.get("status") or "missing")
    if status not in {"pending", "done", "error"}:
        status = "missing"
    return {
        "status": status,
        "language": SKILL_BOOK_TRANSLATION_LANGUAGE,
        "source_revision": source_revision,
        "revision": str(snapshot.get("revision") or ""),
        "markdown": str(snapshot.get("translated_markdown") or "") if status == "done" else "",
        "updated_at": snapshot.get("updated_at"),
        "error_message": str(snapshot.get("error_message") or ""),
    }


def _protected_markdown(markdown: str) -> tuple[str, dict[str, str]]:
    protected: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        token = f"ZXQPROTECTED{len(protected):05d}ZXQ"
        protected[token] = match.group(0)
        return token

    patterns = (
        r"```[\s\S]*?```|~~~[\s\S]*?~~~",
        r"`[^`\n]+`",
        r"!?\[[^\]\n]*\]\((?:[^()\n]|\([^()\n]*\))+\)",
        r"https?://[^\s)>]+",
        r"</?[A-Za-z][^>\n]*>",
    )
    result = markdown
    for pattern in patterns:
        result = re.sub(pattern, replace, result)
    return result, protected


def _restore_protected_markdown(markdown: str, protected: dict[str, str]) -> str:
    result = markdown
    missing: list[str] = []
    for token, original in protected.items():
        if token not in result:
            missing.append(token)
            continue
        result = result.replace(token, original)
    if missing:
        raise RuntimeError("Skill 中文翻译未完整保留代码或链接。")
    return result


def _split_markdown(markdown: str, max_characters: int) -> list[str]:
    limit = max(1_000, int(max_characters))
    paragraphs = re.split(r"(\n{2,})", markdown)
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if current and len(current) + len(paragraph) > limit:
            chunks.append(current)
            current = ""
        if len(paragraph) <= limit:
            current += paragraph
            continue
        if current:
            chunks.append(current)
            current = ""
        for start in range(0, len(paragraph), limit):
            chunks.append(paragraph[start : start + limit])
    if current:
        chunks.append(current)
    return chunks or [""]


def _unwrap_markdown_response(value: str) -> str:
    text = value.strip()
    match = re.fullmatch(r"```(?:markdown|md)?\s*\n([\s\S]*?)\n```", text, re.IGNORECASE)
    return match.group(1) if match else text


def translate_markdown_to_chinese(
    markdown: str,
    *,
    runtime: dict[str, Any],
    chat: Callable[..., dict[str, Any]] = chat_with_provider,
) -> str:
    protected_markdown, protected = _protected_markdown(markdown)
    translated_chunks: list[str] = []
    for chunk in _split_markdown(protected_markdown, SKILL_BOOK_TRANSLATION_CHUNK_SIZE):
        if not re.search(r"[A-Za-z]{3}", chunk):
            translated_chunks.append(chunk)
            continue
        response = chat(
            provider_id=str(runtime.get("provider") or ""),
            base_url=runtime.get("base_url"),
            api_key=runtime.get("api_key"),
            model=runtime.get("model"),
            extra_providers=tuple(runtime.get("extra_providers") or ()),
            messages=[{"role": "user", "content": chunk}],
            system_prompt=(
                "你是严谨的技术文档英译中编辑。把输入 Markdown 中的英文叙述翻译成自然、准确、简洁的简体中文。"
                "必须保留 Markdown 标题、列表、引用、表格等结构；所有 ZXQPROTECTED...ZXQ 占位符必须逐字原样保留。"
                "保留产品名、库名、类名、函数名、参数名和通行技术术语；不要总结、解释、扩写或添加标题。"
                "只返回翻译后的 Markdown 正文，不要包裹代码块。"
            ),
            temperature=0,
            timeout_seconds=240,
        )
        translated = _unwrap_markdown_response(str(response.get("content") or ""))
        if not translated:
            raise RuntimeError("Skill 中文翻译返回空内容。")
        translated_chunks.append(translated)
    return _restore_protected_markdown("".join(translated_chunks), protected).strip()


def translate_skill_source(
    source: SkillTranslationSource,
    *,
    runtime: dict[str, Any],
    chat: Callable[..., dict[str, Any]] = chat_with_provider,
) -> dict[str, Any]:
    path = translation_snapshot_path(source.chapter_id)
    base = {
        "version": SKILL_BOOK_TRANSLATION_VERSION,
        "language": SKILL_BOOK_TRANSLATION_LANGUAGE,
        "chapter_id": source.chapter_id,
        "relative_path": source.relative_path,
        "source_revision": source.source_revision,
    }
    _atomic_write_json(path, {**base, "status": "pending", "updated_at": time.time()})
    try:
        translated = translate_markdown_to_chinese(source.markdown, runtime=runtime, chat=chat)
        revision = hashlib.sha256(translated.encode("utf-8")).hexdigest()
        payload = {
            **base,
            "status": "done",
            "revision": revision,
            "translated_markdown": translated,
            "updated_at": time.time(),
            "error_message": "",
        }
    except Exception as exc:
        payload = {
            **base,
            "status": "error",
            "revision": "",
            "translated_markdown": "",
            "updated_at": time.time(),
            "error_message": str(exc),
        }
    _atomic_write_json(path, payload)
    return payload


def translate_skill_sources(
    sources: Iterable[SkillTranslationSource],
    *,
    runtime: dict[str, Any],
    chat: Callable[..., dict[str, Any]] = chat_with_provider,
    progress_callback: Callable[[int, int], None] | None = None,
) -> dict[str, int]:
    source_items = list(sources)
    translated_count = 0
    failed_count = 0
    for index, source in enumerate(source_items, start=1):
        current = translation_state(
            chapter_id=source.chapter_id,
            source_revision=source.source_revision,
            source_language="en",
        )
        if current["status"] == "done":
            if progress_callback is not None:
                progress_callback(index, len(source_items))
            continue
        result = translate_skill_source(source, runtime=runtime, chat=chat)
        if result["status"] == "done":
            translated_count += 1
        else:
            failed_count += 1
        if progress_callback is not None:
            progress_callback(index, len(source_items))
    return {"translated_count": translated_count, "failed_count": failed_count}
