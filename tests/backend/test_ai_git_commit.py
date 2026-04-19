import json

from backend.core.ai_git_commit import _build_system_prompt, generate_ai_git_commit_draft
from backend.core.git_tools import format_git_commit_message, normalize_commit_body_lines


def test_build_system_prompt_mentions_numbered_body_format():
    prompt = _build_system_prompt(style="summary", include_body=True)

    assert "body 必须是 2 到 4 条中文短句数组" in prompt
    assert "自动格式化成 1、2、3 编号正文" in prompt


def test_normalize_commit_body_lines_and_format_message_use_numbered_items():
    normalized = normalize_commit_body_lines(
        [
            "- 补齐正文编号规范",
            "1、统一前后端预览格式",
            "2. 避免重复编号",
        ]
    )

    assert normalized == [
        "补齐正文编号规范",
        "统一前后端预览格式",
        "避免重复编号",
    ]
    assert format_git_commit_message("整理 AI 提交正文编号", normalized) == (
        "整理 AI 提交正文编号\n\n"
        "1、补齐正文编号规范\n"
        "2、统一前后端预览格式\n"
        "3、避免重复编号"
    )


def test_generate_ai_git_commit_draft_normalizes_prefixed_body_lines(monkeypatch):
    monkeypatch.setattr(
        "backend.core.ai_git_commit.chat_with_provider",
        lambda **_: {
            "model": "deepseek-chat",
            "content": json.dumps(
                {
                    "subject": "整理 AI 提交正文编号",
                    "body": [
                        "1、补齐正文编号规范",
                        "- 统一前后端预览格式",
                        "3. 避免重复编号",
                    ],
                    "needs_split": False,
                    "reason": "",
                },
                ensure_ascii=False,
            ),
        },
    )

    payload = generate_ai_git_commit_draft(
        context_text="demo diff context",
        provider_id="deepseek",
        base_url=None,
        api_key=None,
        model="deepseek-chat",
        style="summary",
        include_body=True,
    )

    assert payload["body"] == [
        "补齐正文编号规范",
        "统一前后端预览格式",
        "避免重复编号",
    ]
    assert payload["full_message"] == (
        "整理 AI 提交正文编号\n\n"
        "1、补齐正文编号规范\n"
        "2、统一前后端预览格式\n"
        "3、避免重复编号"
    )
