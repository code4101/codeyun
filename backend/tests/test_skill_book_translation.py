from __future__ import annotations

from backend.core.library import skill_book_translation as translation


def test_detect_markdown_language_ignores_code_and_identifiers() -> None:
    english = """# Setup

This guide explains how to install and configure the application for production use.
It also documents the expected deployment workflow and troubleshooting steps.

```python
def configure_application():
    return "unchanged"
```
"""
    chinese = """# 安装

这份文档说明如何安装和配置应用，并介绍生产环境中的部署流程与排错步骤。
调用 `configure_application()` 完成初始化。
"""

    assert translation.detect_markdown_language(english) == "en"
    assert translation.detect_markdown_language(chinese) == "zh"


def test_translate_skill_source_preserves_protected_markdown(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(translation, "translation_root", lambda: tmp_path)
    source_markdown = """# Install

Install the package from https://example.com/docs and call `setup_app()`.

```python
setup_app(mode="safe")
```
"""

    def fake_chat(**kwargs):
        content = kwargs["messages"][0]["content"]
        return {"content": content.replace("# Install", "# 安装").replace("Install the package", "安装软件包")}

    source = translation.SkillTranslationSource(
        chapter_id="chapter-1",
        relative_path="english/SKILL.md",
        source_revision="source-revision",
        markdown=source_markdown,
    )
    result = translation.translate_skill_source(
        source,
        runtime={"provider": "test", "model": "test"},
        chat=fake_chat,
    )

    assert result["status"] == "done"
    assert "# 安装" in result["translated_markdown"]
    assert "https://example.com/docs" in result["translated_markdown"]
    assert "`setup_app()`" in result["translated_markdown"]
    assert 'setup_app(mode="safe")' in result["translated_markdown"]
    assert source.markdown == source_markdown
    state = translation.translation_state(
        chapter_id=source.chapter_id,
        source_revision=source.source_revision,
        source_language="en",
    )
    assert state["status"] == "done"
    assert state["markdown"] == result["translated_markdown"]


def test_translation_snapshot_expires_when_source_changes(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(translation, "translation_root", lambda: tmp_path)
    translation._atomic_write_json(
        translation.translation_snapshot_path("chapter-2"),
        {
            "version": translation.SKILL_BOOK_TRANSLATION_VERSION,
            "status": "done",
            "source_revision": "old",
            "translated_markdown": "旧译文",
            "revision": "translation-revision",
            "updated_at": 1,
        },
    )

    state = translation.translation_state(
        chapter_id="chapter-2",
        source_revision="new",
        source_language="en",
    )

    assert state["status"] == "missing"
    assert state["markdown"] == ""
