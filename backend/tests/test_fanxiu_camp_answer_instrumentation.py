from backend.core.fanxiu.instrumentation import camp_answer


class _Memory:
    pid = 123
    process_start_ticks = 456


class _Reader:
    def list_items(self, value):
        return list(value), len(value)

    def long(self, value):
        return int(value)


def test_camp_answer_snapshot_resolves_downloaded_question_plan(monkeypatch):
    reader = _Reader()
    monkeypatch.setattr(camp_answer, "LuaJitReader", lambda _memory: reader)
    monkeypatch.setattr(
        camp_answer,
        "_object_fields",
        lambda _reader, value: dict(value or {}),
    )
    monkeypatch.setattr(
        camp_answer,
        "_camp_answer_data_fields",
        lambda _reader, _root: {
            "_campAnswerInfo": {
                "index": 0,
                "campAnswerVO": {
                    "questions": [
                        {
                            "index": 1,
                            "configId": 3107,
                            "answer": 0,
                            "correct": False,
                            "startTime": 0,
                            "deadline": 0,
                        }
                    ]
                },
            }
        },
    )
    question_table = {
        3107: {
            "question": "韩立的本命法宝是什么？",
            "options": [41, 42, 43],
            "answer": "42",
        }
    }
    option_table = {
        41: {"options": "掌天瓶"},
        42: {"options": "青竹蜂云剑"},
        43: {"options": "玄天斩灵剑"},
    }
    monkeypatch.setattr(
        camp_answer,
        "_config_table",
        lambda _reader, _root, name: (
            question_table
            if name == camp_answer._QUESTION_TABLE_NAME
            else option_table
        ),
    )

    result = camp_answer._snapshot(
        _Memory(),
        0x111,
        0x222,
        camp_cache_hit=True,
        db_cache_hit=False,
    )

    assert result["complete"] is True
    assert result["question_count"] == 1
    assert result["questions"][0]["correct_position"] == 1
    assert result["questions"][0]["answer"] == "青竹蜂云剑"
    assert result["evidence"]["camp_root_address"] == "0x111"
