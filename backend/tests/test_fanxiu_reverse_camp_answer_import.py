from __future__ import annotations

import json

from sqlmodel import Session, SQLModel, create_engine, select

from backend.core.fanxiu.choice_knowledge.reverse_camp_answer import (
    REVERSE_SOURCE,
    import_reverse_camp_answer_knowledge,
)
from backend.models import FanxiuChoiceKnowledge


def _write_rows(tmp_path, questions, options):
    root = tmp_path / "exports"
    for name, rows in (("CampAnswer", questions), ("CampOptions", options)):
        path = root / "parsed_configs" / name / "rows.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return root


def _engine():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return engine


def test_imports_only_active_questions_and_is_idempotent(tmp_path):
    root = _write_rows(
        tmp_path,
        [{"id": 7, "question": "谁的法宝？", "options": [11, 12, 13], "answer": "12"}],
        [
            {"id": 11, "options": "甲", "topicId": 7},
            {"id": 12, "options": "乙", "topicId": 7},
            {"id": 13, "options": "丙", "topicId": 7},
            {"id": 99, "options": "旧题残留", "topicId": 99},
        ],
    )
    engine = _engine()
    with Session(engine) as session:
        first = import_reverse_camp_answer_knowledge(session, export_root=root)
        second = import_reverse_camp_answer_knowledge(session, export_root=root)
        records = list(session.exec(select(FanxiuChoiceKnowledge)).all())

    assert (first.inserted, first.ignored_orphan_option_rows) == (1, 1)
    assert (second.inserted, second.updated, second.unchanged) == (0, 0, 1)
    assert len(records) == 1
    assert records[0].answer == "乙"
    assert [item["position"] for item in records[0].options] == [0, 1, 2]
    assert {item["key"]: item["options_order_fixed"] for item in records[0].contexts} == {
        "activity_quiz": True,
        "activity_quiz_final": False,
    }
    assert records[0].source == REVERSE_SOURCE


def test_runtime_truth_wins_over_reverse_static_conflict(tmp_path):
    root = _write_rows(
        tmp_path,
        [{"id": 1, "question": "测试题", "options": [1, 2, 3], "answer": "2"}],
        [
            {"id": 1, "options": "甲", "topicId": 1},
            {"id": 2, "options": "乙", "topicId": 1},
            {"id": 3, "options": "丙", "topicId": 1},
        ],
    )
    engine = _engine()
    with Session(engine) as session:
        record = FanxiuChoiceKnowledge(
            domain="quiz",
            group_name="活动_答题",
            prompt="测试题",
            normalized_prompt="测试题",
            options=[
                {"text": "甲", "status": 1, "position": 0, "source": "activity_quiz_runtime"},
                {"text": "乙", "status": -1, "position": 1, "source": "activity_quiz_runtime"},
            ],
            source="activity_quiz_runtime",
        )
        session.add(record)
        session.commit()
        stats = import_reverse_camp_answer_knowledge(session, export_root=root)
        session.refresh(record)

    assert stats.protected_conflicts == 1
    assert record.answer == "甲"
    assert record.source == "activity_quiz_runtime"


def test_import_merges_matching_ocr_duplicate_and_cleans_unmatched_answers(tmp_path):
    root = _write_rows(
        tmp_path,
        [{"id": 1, "question": "韩立的本命法宝是什么？", "options": [1, 2, 3], "answer": "1"}],
        [
            {"id": 1, "options": "青竹蜂云剑", "topicId": 1},
            {"id": 2, "options": "虚天鼎", "topicId": 1},
            {"id": 3, "options": "元磁神山", "topicId": 1},
        ],
    )
    engine = _engine()
    with Session(engine) as session:
        session.add(FanxiuChoiceKnowledge(
            domain="quiz",
            group_name="活动_答题",
            prompt="凡人修仙正传中，韩立的本命第一法宝是什么？",
            normalized_prompt="凡人修仙正传中韩立的本命第一法宝是什么",
            options=[
                {"text": "（青竹蜂云剑", "status": 1, "position": 0, "source": "activity_quiz_runtime"},
                {"text": "虚天鼎", "status": -1, "position": 1, "source": "activity_quiz_runtime"},
            ],
            source="activity_quiz_runtime",
        ))
        session.add(FanxiuChoiceKnowledge(
            domain="quiz",
            group_name="活动_答题",
            prompt="新题？",
            normalized_prompt="新题",
            options=[
                {"text": "越国（-", "status": 1, "position": 0, "source": "activity_quiz_runtime"},
            ],
            source="activity_quiz_runtime",
        ))
        session.commit()
        stats = import_reverse_camp_answer_knowledge(session, export_root=root)
        rows = list(session.exec(select(FanxiuChoiceKnowledge)).all())

    assert stats.merged_ocr_records == 1
    assert stats.cleaned_ocr_records == 1
    assert len(rows) == 2
    assert next(row for row in rows if row.prompt == "新题？").answer == "越国"
