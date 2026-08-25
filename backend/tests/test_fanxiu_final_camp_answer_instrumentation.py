from backend.core.fanxiu.instrumentation import final_camp_answer


class _Memory:
    pid = 123
    process_start_ticks = 456


class _Reader:
    def long(self, value):
        return int(value)

    def list_items(self, value):
        return list(value), len(value)


def test_final_camp_answer_snapshot_resolves_native_answer_id(monkeypatch):
    reader = _Reader()
    monkeypatch.setattr(final_camp_answer, "LuaJitReader", lambda _memory: reader)
    monkeypatch.setattr(
        final_camp_answer,
        "_object_fields",
        lambda _reader, value: dict(value or {}),
    )
    monkeypatch.setattr(
        final_camp_answer,
        "_final_data_fields",
        lambda _reader, _root: {
            "_questInfo": {"questId": 3107, "progress": 13, "startTime": 123456}
        },
    )
    question_table = {
        3107: {
            "question": "林轩的九天明月环主要功能是？",
            "options": [41, 42, 43, 44],
            "answer": "41",
        }
    }
    option_table = {
        41: {"options": "攻防一体"},
        42: {"options": "加速修炼"},
        43: {"options": "炼制丹药"},
        44: {"options": "收纳异火"},
    }
    monkeypatch.setattr(
        final_camp_answer,
        "_config_table",
        lambda _reader, _root, name: (
            question_table
            if name == final_camp_answer._QUESTION_TABLE_NAME
            else option_table
        ),
    )

    result = final_camp_answer._snapshot(
        _Memory(),
        0x111,
        0x222,
        final_cache_hit=True,
        db_cache_hit=False,
    )

    assert result["question"] == "林轩的九天明月环主要功能是？"
    assert result["correct_option_id"] == 41
    assert result["correct_answer"] == "攻防一体"
    assert [item["id"] for item in result["options"]] == [41, 42, 43, 44]
    assert result["evidence"]["final_root_address"] == "0x111"


def test_final_camp_answer_cache_marks_old_snapshot_unavailable(monkeypatch):
    monkeypatch.setattr(
        final_camp_answer,
        "_CACHE_RESULT",
        {
            "ok": True,
            "available": True,
            "captured_at_epoch": 1.0,
        },
    )
    monkeypatch.setattr(final_camp_answer, "_CACHE_REFRESHING", True)

    result = final_camp_answer.get_final_camp_answer_snapshot(
        max_age_seconds=0.1
    )

    assert result["available"] is False
    assert result["fresh"] is False
    assert "过期" in result["reason"]
