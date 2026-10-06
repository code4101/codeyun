from types import SimpleNamespace as NS

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from backend.api import library_qa as api
from backend.core.library import reader_qa as qa
from backend.models import User, LibraryBookAsset


def question(**changes):
    return api.ReadingQuestion(resource_type="rich-text", resource_id="book", **changes)


def test_html_nested_anchors_never_duplicate_text():
    toc = [{"anchor": "a", "title": "第一章"}, {"anchor": "b", "title": "小节"}]
    passages = qa.html_passages('<article id="a"><h1>第一章</h1><p>前段</p><h2 id="b">小节</h2><p>后段</p></article><script>恶意脚本</script>', toc)
    assert [p.chapter_id for p in passages] == ["a", "b"]
    all_text = "".join(p.text for p in passages)
    assert all_text.count("前段") == all_text.count("后段") == 1
    assert "恶意脚本" not in all_text


def test_context_keeps_current_and_retrieves_late_evidence_with_budget():
    context = qa.select_context("书", "第一章", [
        qa.Passage("第一章", "普通内容" * 4000 + "光合作用的能量来源是太阳。", "a"),
        qa.Passage("无关章节", "下雨", "b"),
        qa.Passage("光合作用", "叶绿素吸收光能。" * 5000, "c"),
    ], {"a"}, "光合作用的能量来源是什么？")
    assert context["sources"][0]["chapter_id"] == "a"
    assert context["sources"][1]["chapter_id"] == "c"
    assert "能量来源是太阳" in context["text"]
    assert "叶绿素" in context["text"]
    assert len(context["text"]) <= qa.CONTEXT_LIMIT


def test_excerpt_prefers_specific_late_evidence_over_generic_heading():
    text = "photosynthesis\n" + "普通内容" * 5000 + "photosynthesis creates glucose using chlorophyll."
    result = qa.excerpt(text, ["photosynthesis", "glucose", "chlorophyll"], 3000)
    assert "creates glucose" in result


def test_history_is_bounded_and_roles_are_validated():
    history = [{"role": "assistant", "content": "字" * 4000} for _ in range(12)]
    kept = qa.bounded_history(history)
    assert sum(len(row["content"]) for row in kept) <= qa.HISTORY_LIMIT
    assert "photosynthesis" in qa.question_terms("What is photosynthesis?")
    assert "叶绿" in qa.question_terms("请解释叶绿素的作用")
    for changes in ({"question": " "}, {"question": "a", "page": 0},
                    {"question": "a", "history": [{"role": "system", "content": "指令"}]},
                    {"question": "a", "history": [{"role": "user", "content": "a"}] * 13}):
        with pytest.raises(ValidationError):
            question(**changes)


def test_book_context_is_server_loaded_and_stale_chapter_rejected(monkeypatch):
    from backend.api import linux_do_books
    from backend.core.library.linux_do_book import LinuxDoTocItem
    book = NS(title="真实书名", content_html='<article id="chapter"><p>服务端内容</p></article>',
              toc=[LinuxDoTocItem(title="章节", anchor="chapter", number="1", level=0)])
    monkeypatch.setattr(linux_do_books, "get_book", lambda *args: book)
    context = api.load_reading_context(question(chapter_id="chapter", question="解释"), None, None)
    assert "服务端内容" in context["text"]
    with pytest.raises(HTTPException) as error:
        api.load_reading_context(question(chapter_id="stale", question="解释"), None, None)
    assert error.value.status_code == 422


def test_pdf_missing_ocr_never_calls_model(monkeypatch):
    from backend.api import pdf_documents
    monkeypatch.setattr(pdf_documents, "get_pdf_document", lambda *args: NS(title="PDF", metadata=NS(page_count=10)))
    monkeypatch.setattr(pdf_documents, "get_pdf_outline", lambda *args: {"entries": []})
    monkeypatch.setattr(pdf_documents, "get_pdf_reading_pages", lambda _id, start, end, *_: [
        {"page": page, "available": False, "blocks": []} for page in range(start, end + 1)])
    monkeypatch.setattr(pdf_documents, "search_pdf_document", lambda *args: {"hits": []})
    payload = api.ReadingQuestion(resource_type="pdf", resource_id="1", page=3, question="这页什么意思")
    result = api.ask_reading_question(payload, None, None)
    assert result["sources"] == []
    assert "OCR" in result["answer"]
    assert result["context"]["warnings"]


def test_pdf_current_page_and_remote_search_source(monkeypatch):
    from backend.api import pdf_documents
    monkeypatch.setattr(pdf_documents, "get_pdf_document", lambda *args: NS(title="PDF", metadata=NS(page_count=100)))
    monkeypatch.setattr(pdf_documents, "get_pdf_outline", lambda *args: {"entries": [{"title": "植物", "page": 4, "level": 0}]})
    monkeypatch.setattr(pdf_documents, "get_pdf_reading_pages", lambda _id, start, end, *_: [
        {"page": page, "available": True, "blocks": [{"text": "photosynthesis光合作用"}]} for page in range(start, end + 1)])
    monkeypatch.setattr(pdf_documents, "search_pdf_document", lambda *args: {"hits": [{"page": 80}]})
    context = api.load_reading_context(api.ReadingQuestion(resource_type="pdf", resource_id="1", page=5,
                                     question="photosynthesis是什么意思"), None, None)
    assert context["sources"][0]["page"] == 5
    assert any(source["page"] == 80 for source in context["sources"])
    assert "植物" in context["location"]


def test_permission_failure_never_invokes_model(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine, tables=[User.__table__, LibraryBookAsset.__table__])
    with Session(engine) as session:
        owner = User(username="owner", hashed_password="x")
        outsider = User(username="outsider", hashed_password="x")
        session.add_all([owner, outsider]); session.commit(); session.refresh(owner); session.refresh(outsider)
        session.add(LibraryBookAsset(id="book", owner_user_id=owner.id, source_kind="test", title="书")); session.commit()
        monkeypatch.setattr(api, "answer_question", lambda *args: pytest.fail("不应调用模型"))
        with pytest.raises(HTTPException) as error:
            api.ask_reading_question(question(question="书中内容"), session, outsider)
        assert error.value.status_code == 404


def test_http_response_exposes_sources_but_not_prompt(monkeypatch):
    from backend.core.ai import app_config
    app = FastAPI()
    app.include_router(api.router, prefix="/api/library-qa")
    app.dependency_overrides[api.get_session] = lambda: None
    app.dependency_overrides[api.get_current_active_user] = lambda: NS(id=1)
    context = qa.select_context("书", "章节", [qa.Passage("章节", "证据", "a")], {"a"}, "解释")
    monkeypatch.setattr(api, "load_reading_context", lambda *args: context)
    monkeypatch.setattr(app_config, "get_user_ai_app_config", lambda *args: {"enabled": True})
    monkeypatch.setattr(app_config, "resolve_ai_app_runtime_config", lambda **kwargs: {})
    monkeypatch.setattr(api, "answer_question", lambda *args: {"answer": "回答[1]", "model": qa.MODEL,
                                                             "sources": context["sources"], "context": context})
    response = TestClient(app).post("/api/library-qa/ask", json=question(question="解释").model_dump())
    assert response.status_code == 200
    assert response.json()["sources"][0]["chapter_id"] == "a"
    assert "text" not in response.json()["context"]


def test_disabled_app_never_calls_model(monkeypatch):
    from backend.core.ai import app_config
    context = qa.select_context("书", "章节", [qa.Passage("章节", "正文", "a")], {"a"}, "解释")
    monkeypatch.setattr(api, "load_reading_context", lambda *args: context)
    monkeypatch.setattr(app_config, "get_user_ai_app_config", lambda *args: {"enabled": False})
    monkeypatch.setattr(api, "answer_question", lambda *args: pytest.fail("停用时不应调用模型"))
    with pytest.raises(HTTPException) as error:
        api.ask_reading_question(question(question="解释"), None, NS(id=1))
    assert error.value.status_code == 403


def test_model_receives_separate_context_history_and_question(monkeypatch):
    from backend.core.ai import chat
    captured = {}
    def fake_chat(**kwargs):
        captured.update(kwargs)
        return {"content": "太阳[1]", "model": qa.MODEL}
    monkeypatch.setattr(chat, "chat_with_provider", fake_chat)
    context = qa.select_context("书", "章节", [qa.Passage("章节", "太阳", "a")], {"a"}, "能源")
    runtime = dict(provider="deepseek", base_url=None, api_key=None, extra_providers=(), model=qa.MODEL)
    result = qa.answer_question(context, "能源", [{"role": "user", "content": "以前的问题"}], runtime)
    assert result["answer"] == "太阳[1]"
    assert captured["messages"][0]["content"].startswith("本轮书籍资料")
    assert captured["messages"][-1]["content"] == "能源"
    assert "以前的问题" == captured["messages"][1]["content"]


def test_model_can_search_then_read_and_returns_real_web_sources(monkeypatch):
    from backend.core.ai import chat
    from backend.core.tools import reading_web
    import json
    sent = []
    def fake_chat(**kwargs):
        sent.append({**kwargs, "messages": list(kwargs["messages"])})
        if len(sent) < 3:
            name, args = ("web_search", {"query": "公开概念 最新资料"}) if len(sent) == 1 else ("read_webpage", {"url": "https://example.org/fact"})
            calls = [{"id": f"call{len(sent)}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]
            message = {"role": "assistant", "content": None, "tool_calls": calls, "reasoning_content": "核实"}
            return {"content": "", "tool_calls": calls, "message": message}
        return {"content": "**已核实事实**。[官方资料](https://example.org/fact)", "model": qa.MODEL}
    monkeypatch.setattr(chat, "chat_with_provider", fake_chat)
    row = {"title": "原始资料", "url": "https://example.org/fact", "text": "公开证据"}
    monkeypatch.setattr(reading_web, "search_web", lambda query: [row])
    monkeypatch.setattr(reading_web, "read_webpage", lambda url: row)
    context = qa.select_context("书", "章节", [qa.Passage("章节", "正文", "a")], {"a"}, "最新资料")
    runtime = dict(provider="deepseek", base_url=None, api_key=None, extra_providers=(), model=qa.MODEL)
    result = qa.answer_question(context, "最新资料", [], runtime)
    assert len(sent) == 3
    assert sent[-1]["tool_choice"] == "none"
    assert sent[1]["messages"][-1]["role"] == "tool"
    assert sent[1]["messages"][-1]["tool_call_id"] == "call1"
    assert sent[1]["messages"][-2]["reasoning_content"] == "核实"
    assert result["web_sources"] == [{"title": "原始资料", "url": "https://example.org/fact"}]
    assert result["sources"] == context["sources"]


def test_search_failure_keeps_book_answer_and_does_not_invent_web_sources(monkeypatch):
    from backend.core.ai import chat
    from backend.core.tools import reading_web
    calls = [{"id": "search1", "type": "function", "function": {"name": "web_search", "arguments": '{"query":"公开事实"}'}}]
    sent = []
    def fake_chat(**kwargs):
        sent.append(list(kwargs["messages"]))
        if len(sent) == 1:
            return {"content": "", "tool_calls": calls, "message": {"role": "assistant", "tool_calls": calls}}
        assert '"error"' in sent[-1][-1]["content"]
        return {"content": "书中已知事实[1]"}
    monkeypatch.setattr(chat, "chat_with_provider", fake_chat)
    def fail(query):
        raise reading_web.ReadingWebError("搜索暂时不可用")
    monkeypatch.setattr(reading_web, "search_web", fail)
    context = qa.select_context("书", "章节", [qa.Passage("章节", "正文", "a")], {"a"}, "最新资料")
    runtime = dict(provider="deepseek", base_url=None, api_key=None, extra_providers=(), model=qa.MODEL)
    result = qa.answer_question(context, "最新资料", [], runtime)
    assert result["answer"] == "书中已知事实[1]"
    assert result["web_sources"] == []


def test_skill_context_checks_book_identity_and_reads_only_relevant_chapters(monkeypatch):
    from backend.api import skill_books
    chapters = [NS(id="a", title="当前章", relative_path="a.md"), NS(id="b", title="photosynthesis", relative_path="b.md"),
                NS(id="c", title="无关章", relative_path="c.md")]
    monkeypatch.setattr(skill_books, "get_local_skill_book_catalog", lambda *args: NS(asset_id="skill", title="技能书", skills=[NS(chapters=chapters)]))
    loaded = []
    def load(chapter_id, *args):
        loaded.append(chapter_id)
        return NS(markdown="photosynthesis 解释")
    monkeypatch.setattr(skill_books, "get_local_skill_book_chapter", load)
    payload = api.ReadingQuestion(resource_type="skill-book", resource_id="skill", chapter_id="a", bookshelf_id="shelf", question="photosynthesis?")
    result = api.load_reading_context(payload, None, None)
    assert loaded == ["a", "b"]
    assert result["sources"][0]["chapter_id"] == "a"
    with pytest.raises(HTTPException) as error:
        api.load_reading_context(payload.model_copy(update={"resource_id": "wrong"}), None, None)
    assert error.value.status_code == 404


def test_default_flash_configuration_uses_system_fallback(monkeypatch):
    from backend.core.ai import app_config
    from backend.core.ai.chat import get_ai_provider
    monkeypatch.setattr(app_config, "get_user_ai_app_config", lambda *args: {"provider": "deepseek", "model": qa.MODEL})
    monkeypatch.setattr(app_config, "list_user_ai_chat_custom_provider_configs", lambda *args: [])
    monkeypatch.setattr(app_config, "get_user_ai_chat_provider_runtime_config", lambda *args: {})
    monkeypatch.setattr(app_config, "resolve_system_ai_resource", lambda **kwargs: {
        "provider": "deepseek", "base_url": None, "api_key": "test-system-key", "extra_providers": (), "system_resource_id": "ai.provider.deepseek"})
    runtime = app_config.resolve_ai_app_runtime_config(session=None, current_user=NS(id=2), app_id=app_config.AI_APP_LIBRARY_QA)
    assert runtime["model"] == qa.MODEL
    assert runtime["resource_scope"] == "system"
    assert get_ai_provider(runtime["provider"], api_key=runtime["api_key"]).configured
