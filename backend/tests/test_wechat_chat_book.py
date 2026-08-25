from __future__ import annotations

from datetime import datetime

import pytest

import backend.core.library.wechat_chat_book as wechat_chat_book
from backend.core.ai.chat import OllamaClientError
from backend.core.library.wechat_chat_book import (
    _call_ai,
    _apply_direct_speech_repairs,
    _event_is_transient_noise,
    _forwarded_chat_records,
    _leaf_prompt,
    _monthly_prompt,
    _normalize_direct_speech,
    _payload_has_editorial_voice,
    _validate_month_events,
    collect_source_chunks,
    compose_book_document,
    is_substantive_message,
)


class FakeStorage:
    def __init__(self, items, contacts=None):
        self.items = items
        self.contacts = contacts or {}

    def list_messages(self, *, limit, offset, **_kwargs):
        return {
            "total": len(self.items),
            "items": self.items[offset : offset + limit],
        }

    def _contact_map(self):
        return self.contacts


def _message(local_id: int, text: str, *, sender: str = "测试者", timestamp: int = 1_770_000_000):
    return {
        "local_id": local_id,
        "create_time": timestamp + local_id,
        "sender_name": sender,
        "message_text": text,
        "message_content": text,
        "appmsg": None,
    }


def test_source_filter_preserves_low_value_context_for_semantic_curation():
    assert is_substantive_message(_message(1, "哈哈哈"))
    assert is_substantive_message(_message(2, "[抱拳]"))
    assert is_substantive_message(_message(3, "价格是 30 元"))
    assert is_substantive_message(_message(4, "SQLite"))


def test_collect_source_chunks_strips_sender_prefix_and_reports_noise():
    storage = FakeStorage(
        [
            _message(1, "wxid_abc123:\n哈哈哈"),
            _message(2, "wxid_abc123:\n先访谈一线用户，再决定产品方案"),
            {
                **_message(3, ""),
                "appmsg": {
                    "title": "一篇方法论文章",
                    "description": "文章强调先收集真实材料，再做结构化判断。",
                    "url": "https://example.test/article",
                },
            },
        ]
    )

    chunks, stats = collect_source_chunks(
        storage,
        chat_username="room@chatroom",
        page_size=2,
        target_chars=10_000,
    )

    assert len(chunks) == 1
    assert "先访谈一线用户" in chunks[0]["content"]
    assert "一篇方法论文章" in chunks[0]["content"]
    assert "wxid_abc123" not in chunks[0]["content"]
    assert stats["scanned_message_count"] == 3
    assert stats["kept_message_count"] == 3
    assert stats["discarded_message_count"] == 0
    assert stats["source_chunk_count"] == 1
    assert stats["month_count"] == 1


def test_collect_source_chunks_never_crosses_month_boundary():
    january = int(datetime(2026, 1, 31, 23, 59).timestamp())
    february = int(datetime(2026, 2, 1, 0, 1).timestamp())
    storage = FakeStorage(
        [
            _message(1, "一月形成的有效观点", timestamp=january - 1),
            _message(1, "二月继续形成新观点", timestamp=february - 1),
        ]
    )

    chunks, stats = collect_source_chunks(
        storage,
        chat_username="room@chatroom",
        target_chars=10_000,
    )

    assert [chunk["period_key"] for chunk in chunks] == ["2026-01", "2026-02"]
    assert stats["month_count"] == 2


def test_collect_source_chunks_never_crosses_date_boundary():
    first_day = int(datetime(2026, 7, 25, 23, 59).timestamp())
    second_day = int(datetime(2026, 7, 26, 0, 1).timestamp())
    storage = FakeStorage(
        [
            _message(1, "第一天的话题", timestamp=first_day - 1),
            _message(1, "第二天的话题", timestamp=second_day - 1),
        ]
    )

    chunks, _stats = collect_source_chunks(
        storage,
        chat_username="room@chatroom",
        target_chars=10_000,
    )

    assert [chunk["content"][:10] for chunk in chunks] == [
        "2026-07-25",
        "2026-07-26",
    ]


def test_collect_source_chunks_attaches_readable_image(tmp_path):
    image_path = tmp_path / "job.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nimage")
    row = {
        **_message(1, "<?xml version=\"1.0\"?><msg><img /></msg>"),
        "local_type": 3,
        "resource": {
            "items": [
                {
                    "export": {
                        "kind": "image",
                        "stored_path": str(image_path),
                    }
                }
            ]
        },
    }

    chunks, stats = collect_source_chunks(
        FakeStorage([row]),
        chat_username="room@chatroom",
    )

    assert stats["kept_message_count"] == 1
    assert "[图片附件：job.png]" in chunks[0]["content"]
    assert chunks[0]["images"][0].startswith("data:image/png;base64,")
    assert chunks[0]["image_assets"]["job.png"].startswith("data:image/png;base64,")


def test_collect_source_chunks_prefers_wechat_nickname_over_contact_remark():
    row = {
        **_message(1, "先讨论语义边界", sender="通讯录备注"),
        "sender_username": "wxid_example123",
    }
    storage = FakeStorage(
        [row],
        contacts={
            "wxid_example123": {
                "remark": "通讯录备注",
                "nick_name": "群里昵称",
                "alias": "account_alias",
            }
        },
    )

    chunks, _stats = collect_source_chunks(
        storage,
        chat_username="room@chatroom",
    )

    assert "｜群里昵称：先讨论语义边界" in chunks[0]["content"]
    assert "通讯录备注" not in chunks[0]["content"]
    assert "wxid_example123" not in chunks[0]["content"]


def test_forwarded_chat_records_expand_all_text_items_with_original_attribution():
    row = {
        **_message(1, ""),
        "message_content": """
<msg><appmsg><recorditem><![CDATA[
<recordinfo><datalist count="3">
  <dataitem datatype="1"><sourcename>小明</sourcename><sourcetime>2026-07-26 09:31</sourcetime><datadesc>先看问题背后的结构。</datadesc></dataitem>
  <dataitem datatype="2"><sourcename>小明</sourcename><sourcetime>2026-07-26 09:32</sourcetime></dataitem>
  <dataitem datatype="1"><sourcename>小红</sourcename><sourcetime>2026-07-26 09:33</sourcetime><datadesc>再选择合适的模型。</datadesc></dataitem>
</datalist></recordinfo>
]]></recorditem></appmsg></msg>
""".strip(),
        "appmsg": {"title": "小明和小红的聊天记录"},
    }

    records = _forwarded_chat_records(row)

    assert [(record["speaker"], record["stamp"], record["text"]) for record in records] == [
        ("小明", "2026-07-26 09:31", "先看问题背后的结构。"),
        ("小红", "2026-07-26 09:33", "再选择合适的模型。"),
    ]


def test_compose_book_document_builds_year_month_date_event_outline():
    payload = {
        "title": "未来社微信分部群志",
        "subtitle": "未来社微信分部群聊重点事件摘编",
        "preface": ["本书按月归档、按日期组织重点事件。"],
        "years": [
            {
                "year": "2026",
                "months": [
                    {
                        "period_key": "2026-01",
                        "source_period": "2026-01-23 至 2026-01-31",
                        "dates": [
                            {
                                "date": "2026-01-24",
                                "events": [
                                    {
                                        "title": "先验证真实需求",
                                        "background": "讨论从产品方案是否应立即实施开始。",
                                        "start_time": "2026-01-24 09:30",
                                        "end_time": "2026-01-24 09:30",
                                        "entries": [
                                            {
                                                "speaker": "测试者",
                                                "time": "2026-01-24 09:30",
                                                "text": "先访谈一线用户，再决定产品方案。",
                                            },
                                            {
                                                "speaker": "测试者",
                                                "time": "2026-01-24 09:38",
                                                "text": "验证成立后再继续扩展。",
                                                "images": [
                                                    {
                                                        "name": "proof.png",
                                                        "src": "data:image/png;base64,aW1hZ2U=",
                                                    }
                                                ],
                                            }
                                        ],
                                    },
                                    {
                                        "title": "官网补上产品说明",
                                        "start_time": "2026-01-24 15:36",
                                        "end_time": "2026-01-24 15:36",
                                        "entries": [
                                            {
                                                "speaker": "测试者",
                                                "time": "2026-01-24 15:36",
                                                "text": "官网终于有介绍文字了，至少能知道产品是做什么的。",
                                            }
                                        ],
                                    },
                                ],
                            }
                        ],
                    },
                    {
                        "period_key": "2026-02",
                        "dates": [],
                    },
                ],
            }
        ],
    }

    document = compose_book_document(
        payload,
        chat_name="未来社微信分部",
        chat_username="room@chatroom",
        editor_name="code4101",
        statistics={
            "scanned_message_count": 100,
            "kept_message_count": 40,
            "discarded_message_count": 60,
        },
        revision="rev-1",
    )

    assert document.title == payload["title"]
    assert document.author == "code4101"
    assert document.post_count == 1
    assert [item.title for item in document.toc] == [
        "编者说明",
        "2026年",
        "1月",
        "24日",
        "2月",
        "整理说明",
    ]
    assert document.toc[2].parent_anchor == "year-2026"
    assert document.toc[3].parent_anchor == "month-2026-01"
    assert [item.number for item in document.toc[1:5]] == ["1", "", "", ""]
    assert 'data-article-id="month-2026-01"' in document.content_html
    assert 'data-article-id="date-2026-01-24"' in document.content_html
    assert "<h1>2026年1月</h1>" in document.content_html
    assert "<h1>1.1 2026年1月</h1>" not in document.content_html
    assert "日期大纲" in document.content_html
    assert "<strong>24日</strong>：收录 2 个事件" in document.content_html
    assert "先验证真实需求" in document.content_html
    assert "官网补上产品说明" in document.content_html
    assert "先访谈一线用户，再决定产品方案。" in document.content_html
    assert "09:30 测试者：" in document.content_html
    assert "09:38：" in document.content_html
    assert "09:38 测试者：" not in document.content_html
    assert 'class="wechat-event-image"' in document.content_html
    assert 'src="data:image/png;base64,aW1hZ2U="' in document.content_html
    assert 'alt="先验证真实需求相关微信图片"' in document.content_html
    assert '<ol class="wechat-date-outline">' in document.content_html
    assert "<blockquote" not in document.content_html
    assert "重点事件实录" in document.content_html
    assert "最终收录 2 个重点事件" in document.content_html


def test_validate_month_events_groups_by_date_and_drops_unverifiable_entries():
    payload = {
        "overview": "不应保留的月度综述",
        "events": [
            {
                "title": "先验证，再扩展",
                "background": "讨论产品推进方式。",
                "entries": [
                    {
                        "speaker": "小明",
                        "source_quotes": [
                            "我觉得先做一个最小验证，然后再讨论怎么扩展。",
                            "[图片附件：validation.png]",
                        ],
                        "text": "先做最小验证，再讨论扩展。",
                    },
                    {
                        "speaker": "小明",
                        "source_quotes": ["这是一句没有出现在聊天里的话。"],
                        "text": "虚构内容。",
                    },
                ],
            },
        ]
    }
    result = _validate_month_events(
        payload,
        [
            {
                "content": (
                    "2026-07-03 10:20｜小明：我觉得先做一个最小验证，然后再讨论怎么扩展。\n"
                    "2026-07-03 10:20｜小明：[图片附件：validation.png]\n"
                    "2026-07-03 10:21｜小红：我补一个真实案例。"
                ),
                "image_assets": {
                    "validation.png": "data:image/png;base64,aW1hZ2U="
                },
            }
        ],
    )

    assert "overview" not in result
    assert "events" not in result
    assert result["dates"] == [
        {
            "date": "2026-07-03",
            "events": [
                {
                    "title": "先验证，再扩展",
                    "primary_date": "2026-07-03",
                    "start_time": "2026-07-03 10:20",
                    "end_time": "2026-07-03 10:20",
                    "background": "讨论产品推进方式。",
                    "entries": [
                        {
                            "speaker": "小明",
                            "time": "2026-07-03 10:20",
                            "text": "先做最小验证，再讨论扩展。",
                            "source_quotes": [
                                "我觉得先做一个最小验证，然后再讨论怎么扩展。",
                                "[图片附件：validation.png]",
                            ],
                            "images": [
                                {
                                    "name": "validation.png",
                                    "src": "data:image/png;base64,aW1hZ2U=",
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    ]


def test_normalize_direct_speech_removes_editorial_speech_wrappers():
    assert _normalize_direct_speech("我说很好。") == "很好。"
    assert _normalize_direct_speech("我解释这不是一般层面的建模话题。") == "这不是一般层面的建模话题。"
    assert _normalize_direct_speech("我补充：普通人更需要知识广度。") == "普通人更需要知识广度。"
    assert _normalize_direct_speech("我提到很好。") == "很好。"
    assert _normalize_direct_speech("我不知道量子力学原理。") == "我不知道量子力学原理。"


def test_editorial_voice_detection_rejects_narrated_entry_text():
    assert _payload_has_editorial_voice(
        {"dates": [{"events": [{"entries": [{"text": "强调要使用专业术语。"}]}]}]}
    )
    assert not _payload_has_editorial_voice(
        {"dates": [{"events": [{"entries": [{"text": "专业术语能更高效地表达问题特性。"}]}]}]}
    )


def test_transient_status_events_are_dropped_but_billing_rules_are_kept():
    transient_entries = [
        {"source_quotes": ["额度用完了", "官方重置了呀，又100%了"]}
    ]
    billing_entries = [
        {
            "source_quotes": [
                "按起点总额算使用率，再按比例乘每天40元。",
            ]
        }
    ]

    assert _event_is_transient_noise(
        "账号借测与额度重置恢复",
        "",
        transient_entries,
    )
    assert not _event_is_transient_noise(
        "账号额度分摊结算规则",
        "",
        billing_entries,
    )


def test_direct_speech_repairs_cannot_change_event_structure():
    payload = {
        "dates": [
            {
                "date": "2026-07-26",
                "events": [
                    {
                        "title": "事件一",
                        "entries": [{"speaker": "甲", "text": "强调要验证。"}],
                    },
                    {
                        "title": "事件二",
                        "entries": [{"speaker": "乙", "text": "补充要分组。"}],
                    },
                ],
            }
        ]
    }
    repairs = {
        "entries": [
            {"id": "d0e0i0", "text": "先验证。"},
            {"id": "d0e1i0", "text": "再分组。"},
            {"id": "d0e2i0", "text": "不得新增。"},
        ]
    }

    result = _apply_direct_speech_repairs(payload, repairs)

    assert [event["title"] for event in result["dates"][0]["events"]] == ["事件一", "事件二"]
    assert result["dates"][0]["events"][0]["entries"][0]["text"] == "先验证。"
    assert result["dates"][0]["events"][1]["entries"][0]["text"] == "再分组。"


def test_prompts_define_semantic_thread_event_editing():
    chunk = {
        "first_time": int(datetime(2026, 7, 1).timestamp()),
        "last_time": int(datetime(2026, 7, 2).timestamp()),
        "content": "2026-07-01 10:00｜小明：先验证，再扩展。",
    }
    leaf_system, leaf_user = _leaf_prompt("未来社微信分部", chunk)
    month_system, month_user = _monthly_prompt(
        "未来社微信分部",
        "2026-07",
        [{"events": []}],
    )

    assert "语义话题线程" in leaf_system
    assert "多个话题并行穿插" in leaf_system
    assert "轻量小事件" in leaf_system
    assert "主题相似不等于同一事件" in leaf_system
    assert '"source_quotes"' in leaf_user
    assert '"dates"' in leaf_user
    assert '"events"' in leaf_user
    assert '"title"' in leaf_user
    assert "时间不连续也不代表话题结束" in leaf_user
    assert "官网终于补上用途说明" in leaf_user
    assert "轻量简记默认一事一条" in leaf_user
    assert "禁止用“看了新图”" in leaf_user
    assert "成书时挂回原图" in leaf_user
    assert "不同事件必须分别识别" in leaf_user
    assert "日期是硬边界" in leaf_user
    assert "禁止写“我说很好”" in leaf_user
    assert "背景是" in leaf_user
    assert "按语义线程重新归组" in month_system
    assert "当日简记" in month_system
    assert "同一事件跨时间" in month_user
    assert '"dates"' in month_user
    assert "日期大纲是硬边界" in month_user
    assert "产品官网补上用途说明" in month_user
    assert "不能合成“页面与岗位观察”" in month_user
    assert "不要压成一句语录" in month_user
    assert "图片附件标识也是证据" in month_user
    assert "禁止写“我说……、我解释" in month_user


def test_call_ai_falls_back_to_mini_only_when_spark_quota_is_exhausted(monkeypatch):
    called_models: list[str] = []

    def fake_chat_with_provider(**kwargs):
        model = str(kwargs.get("model") or "")
        called_models.append(model)
        if model == "gpt-5.3-codex-spark":
            raise OllamaClientError("You've hit your usage limit for GPT-5.3-Codex-Spark.")
        return {"content": '{"ok": true}'}

    monkeypatch.setattr(wechat_chat_book, "chat_with_provider", fake_chat_with_provider)

    result = _call_ai(
        {"provider": "codex-cli", "model": "gpt-5.3-codex-spark"},
        "system",
        "user",
    )

    assert result == {"ok": True}
    assert called_models == ["gpt-5.3-codex-spark", "gpt-5.4-mini"]


def test_call_ai_does_not_change_model_for_non_quota_failures(monkeypatch):
    called_models: list[str] = []

    def fake_chat_with_provider(**kwargs):
        called_models.append(str(kwargs.get("model") or ""))
        raise OllamaClientError("network connection failed")

    monkeypatch.setattr(wechat_chat_book, "chat_with_provider", fake_chat_with_provider)

    with pytest.raises(OllamaClientError, match="network connection failed"):
        _call_ai(
            {"provider": "codex-cli", "model": "gpt-5.3-codex-spark"},
            "system",
            "user",
        )

    assert called_models == ["gpt-5.3-codex-spark"]
