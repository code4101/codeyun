import re

import pytest

from backend.core.fanxiu.instrumentation.chat import (
    select_chat_channel_title_patterns,
    select_chat_channel_route,
    select_chat_row_anchors,
    select_repeated_chat_phrase,
)


def test_world_channel_has_realm_derived_row_title_pattern():
    pattern = select_chat_channel_title_patterns(4)

    assert len(pattern) == 1
    assert re.search(pattern[0], "【仙界】传音群")
    assert select_chat_channel_title_patterns(104) == []


def test_select_chat_channel_route_uses_runtime_group_type_not_gui_badge():
    route = select_chat_channel_route(
        101,
        [{"id": 4, "groupType": 2}, {"id": 101, "groupType": 1}],
        [
            {"type": -1, "sort": 1, "name": 8017},
            {"type": 1, "sort": 2, "name": 7908},
            {"type": 2, "sort": 3, "name": 7909},
        ],
    )

    assert route == {
        "channel": 101,
        "group_type": 1,
        "tab_label": "活动",
        "sort": 2,
        "name_id": 7908,
    }


def test_select_chat_channel_route_fails_closed_on_unknown_channel():
    with pytest.raises(RuntimeError, match="配置不唯一"):
        select_chat_channel_route(
            101,
            [{"id": 4, "groupType": 2}],
            [{"type": 2, "sort": 3, "name": 7909}],
        )


def test_select_chat_row_anchors_uses_rendered_runtime_preview_text():
    anchors = select_chat_row_anchors(
        {
            "content": "<color=#193970>【跨服】鸿运福签</color>：吉签启鸿运，佳奖落君身！",
            "sender_name": "凌舒玄",
        }
    )

    assert "吉签启鸿运" in anchors
    assert all("color" not in item for item in anchors)


def test_select_chat_row_anchors_uses_light_preview_for_face_only_message():
    anchors = select_chat_row_anchors(
        {
            "content": "{0;0:1018_1;}",
            "chat_href": "<#face&f10118|200x200>",
            "light_chat_href": "[谢谢老板]",
            "sender_name": "浮黎",
        }
    )

    assert anchors == ["谢谢老板"]


def test_select_repeated_chat_phrase_requires_dominant_consensus():
    phrase = "吉签启鸿运，佳奖落君身！祝贺道友抽得大奖"
    result = select_repeated_chat_phrase(
        [
            {"content": phrase, "content_type": 0},
            {"content": phrase, "content_type": 0},
            {"content": phrase, "content_type": 0},
            {"content": "普通聊天", "content_type": 0},
        ]
    )

    assert result["ready"] is True
    assert result["phrase"] == phrase
    assert result["occurrences"] == 3
    assert result["agreement_ratio"] == 0.75


def test_select_repeated_chat_phrase_fails_closed_on_weak_consensus():
    result = select_repeated_chat_phrase(
        [
            {"content": "甲", "content_type": 0},
            {"content": "乙", "content_type": 0},
            {"content": "甲", "content_type": 0},
        ]
    )

    assert result["ready"] is False
    assert result["phrase"] == ""
    assert result["reason"] == "phrase_consensus_insufficient"
