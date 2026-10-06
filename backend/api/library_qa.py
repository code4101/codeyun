"""统一阅读问答接口：服务端按资源和阅读位置取得已授权的正文。"""
from __future__ import annotations

from typing import Literal
from dataclasses import asdict, is_dataclass
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator
from sqlmodel import Session

from backend.core.access.auth import get_current_active_user
from backend.db import get_session
from backend.models import User
from backend.core.library.reader_qa import Passage, answer_question, html_passages, question_terms, relevance, select_context

router = APIRouter()


class QaMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=6000)


class ReadingQuestion(BaseModel):
    resource_type: Literal["rich-text", "pdf", "skill-book"]
    resource_id: str = Field(min_length=1, max_length=300)
    chapter_id: str = Field(default="", max_length=500)
    page: int = Field(default=1, ge=1)
    bookshelf_id: str = Field(default="", max_length=300)
    question: str = Field(min_length=1, max_length=2000)
    history: list[QaMessage] = Field(default_factory=list, max_length=12)

    @field_validator("question")
    @classmethod
    def strip_question(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("请输入问题")
        return value.strip()


def load_reading_context(payload: ReadingQuestion, session: Session, user: User) -> dict:
    """复用阅读器公共读取契约；权限拒绝发生在任何模型调用之前。"""
    if payload.resource_type == "rich-text":
        from backend.api.linux_do_books import get_book
        book = get_book(payload.resource_id, session, user)
        toc = [row if isinstance(row, dict) else asdict(row) if is_dataclass(row) else row.model_dump() for row in book.toc]
        passages = html_passages(book.content_html, toc)
        known = {row["anchor"] for row in toc}
        if payload.chapter_id and payload.chapter_id not in known:
            raise HTTPException(422, "章节已变化，请重新打开书籍后提问")
        current = payload.chapter_id or (passages[0].chapter_id if passages else "")
        ids = {current}
        # 父章节包含其后代；片段仍保留自己的锚点，来源导航准确。
        children: dict[str, list[str]] = {}
        for row in toc:
            children.setdefault(row.get("parent_anchor"), []).append(row["anchor"])
        pending = [current]
        while pending:
            for child in children.get(pending.pop(), []):
                if child not in ids:
                    ids.add(child)
                    pending.append(child)
        location = next((row["title"] for row in toc if row["anchor"] == current), "正文")
        return select_context(book.title, location, passages, ids, payload.question,
                              toc=" / ".join(row["title"] for row in toc))

    if payload.resource_type == "skill-book":
        from backend.api.skill_books import get_local_skill_book_catalog, get_local_skill_book_chapter
        if not payload.bookshelf_id:
            raise HTTPException(422, "请选择技能书所在书柜")
        catalog = get_local_skill_book_catalog(Response(), payload.bookshelf_id, session, user)
        if payload.resource_id != catalog.asset_id:
            raise HTTPException(404, "图书不存在")
        chapters = [chapter for skill in catalog.skills for chapter in skill.chapters]
        current = next((chapter for chapter in chapters if chapter.id == payload.chapter_id), None)
        if payload.chapter_id and current is None:
            raise HTTPException(422, "章节已变化，请重新选择章节")
        if current is None and chapters:
            current = chapters[0]
        terms = question_terms(payload.question)
        related = sorted((chapter for chapter in chapters if chapter != current),
                         key=lambda chapter: relevance(Passage(chapter.title + " " + chapter.relative_path, ""), terms), reverse=True)
        selected = ([current] if current else []) + [chapter for chapter in related if relevance(Passage(chapter.title + " " + chapter.relative_path, ""), terms)][:3]
        passages = []
        for chapter in selected:
            content = get_local_skill_book_chapter(chapter.id, Response(), session, user)
            passages.append(Passage(chapter.title, content.markdown, chapter.id))
        return select_context(catalog.title, current.title if current else "正文", passages,
                              {current.id} if current else set(), payload.question,
                              toc=" / ".join(chapter.title for chapter in chapters))

    from backend.api.pdf_documents import get_pdf_document, get_pdf_outline, get_pdf_reading_pages, search_pdf_document
    try:
        pdf_id = int(payload.resource_id)
    except ValueError as exc:
        raise HTTPException(404, "PDF 不存在") from exc
    book = get_pdf_document(pdf_id, session, user)
    count = book.metadata.page_count or 1
    if payload.page > count:
        raise HTTPException(422, "当前页码超出书籍范围")
    outline = get_pdf_outline(pdf_id, session, user)
    entries = [row for row in outline["entries"] if row.get("page")]
    before = [row for row in entries if row["page"] <= payload.page]
    chapter = max(before, key=lambda row: (row["page"], row["level"])) if before else None
    start, end = max(1, payload.page - 2), min(count, payload.page + 2)
    if chapter:
        start = max(start, chapter["page"])
        following = [row["page"] for row in entries if row["page"] > payload.page and row["level"] <= chapter["level"]]
        if following:
            end = min(end, min(following) - 1)
    rows = get_pdf_reading_pages(pdf_id, start, end, session, user)
    pages = {row["page"]: row for row in rows}
    warnings = []
    if not pages.get(payload.page, {}).get("available"):
        warnings.append("当前页尚无 OCR 文字；回答只能参考已有识别页。")
    # 搜索只读取现有 OCR，远处命中页再走公共分页接口取原文。
    for term in question_terms(payload.question)[:3]:
        try:
            matches = search_pdf_document(pdf_id, term, 1, count, 0, session, user)
        except HTTPException as exc:
            if exc.status_code == 422:
                warnings.append("此 PDF 的全书检索暂不可用。")
                break
            raise
        for hit in matches["hits"][:3]:
            page = hit["page"]
            if page not in pages:
                pages[page] = get_pdf_reading_pages(pdf_id, page, page, session, user)[0]
    passages = [Passage(f"第 {row['page']} 页", "\n".join(block.get("text", "") for block in row["blocks"]),
                        f"page:{row['page']}", row["page"]) for row in pages.values() if row["available"]]
    location = f"{chapter['title']} · 第 {payload.page} 页" if chapter else f"第 {payload.page} 页"
    return select_context(book.title, location, passages, {f"page:{payload.page}"}, payload.question,
                          toc=" / ".join(row["title"] for row in entries), warnings=list(dict.fromkeys(warnings)))


class QaSource(BaseModel):
    number: int
    title: str
    chapter_id: str
    page: int | None = None


class QaContext(BaseModel):
    location: str
    warnings: list[str]
    characters: int


class ReadingAnswer(BaseModel):
    answer: str
    model: str
    sources: list[QaSource]
    context: QaContext


@router.post("/ask", response_model=ReadingAnswer)
def ask_reading_question(payload: ReadingQuestion, session: Session = Depends(get_session),
                         current_user: User = Depends(get_current_active_user)):
    from backend.core.ai.app_config import AI_APP_LIBRARY_QA, AiAppConfigError, get_user_ai_app_config, resolve_ai_app_runtime_config
    from backend.core.ai.chat import OllamaClientError
    context = load_reading_context(payload, session, current_user)
    if not context["sources"]:
        return answer_question(context, payload.question, [], {})
    if not get_user_ai_app_config(session, current_user.id, AI_APP_LIBRARY_QA).get("enabled", True):
        raise HTTPException(403, "阅读问答已停用，可在 AI 应用设置中启用")
    try:
        runtime = resolve_ai_app_runtime_config(session=session, current_user=current_user, app_id=AI_APP_LIBRARY_QA)
        return answer_question(context, payload.question, [row.model_dump() for row in payload.history], runtime)
    except AiAppConfigError as exc:
        raise HTTPException(503, "阅读问答模型尚未配置，请检查 AI 应用设置") from exc
    except (OllamaClientError, ValueError) as exc:
        raise HTTPException(502, "阅读问答模型暂时未能回答，请稍后重试") from exc
