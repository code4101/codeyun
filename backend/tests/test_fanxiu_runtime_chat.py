from backend.core.fanxiu.instrumentation.chat import select_repeated_chat_phrase


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
