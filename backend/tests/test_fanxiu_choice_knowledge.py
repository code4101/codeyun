import random

from sqlmodel import Session, create_engine, select, text

from backend.core.fanxiu.choice_knowledge.catalog import ChoiceKnowledgeCatalog
from backend.core.fanxiu.choice_knowledge.model import (
    ChoiceOption,
    ChoiceQuestion,
    clean_ocr_choice_text,
)
from backend.core.fanxiu.choice_knowledge.seed import (
    LILIAN_EVENT_RECOMMENDED_ANSWERS,
)
from backend.core.fanxiu.choice_knowledge.store import (
    CONTEXT_ACTIVITY_QUIZ,
    CONTEXT_LINGQUAN,
    DOMAIN_LILIAN_EVENT,
    ensure_choice_knowledge_seeds,
    question_from_record,
    upsert_activity_quiz_result,
    update_exclusive_choice_outcome,
    update_choice_status,
)
from backend.models import FanxiuChoiceKnowledge
from backend.migrations.manager import (
    v99_add_fanxiu_choice_knowledge,
    v100_add_fanxiu_choice_knowledge_contexts,
)


def _question(options):
    return ChoiceQuestion(
        domain=DOMAIN_LILIAN_EVENT,
        prompt="测试事件",
        interaction_mode="choice_click",
        options=[ChoiceOption.from_value(option) for option in options],
    )


def test_lilian_seed_preserves_note_as_positive_incomplete_knowledge():
    engine = create_engine("sqlite://")
    FanxiuChoiceKnowledge.__table__.create(engine)

    with Session(engine) as session:
        ensure_choice_knowledge_seeds(session)
        rows = list(session.exec(
            select(FanxiuChoiceKnowledge).where(
                FanxiuChoiceKnowledge.domain == DOMAIN_LILIAN_EVENT
            )
        ).all())

    assert len(rows) == len(LILIAN_EVENT_RECOMMENDED_ANSWERS)
    miaofa = next(row for row in rows if row.prompt == "妙法玉简")
    assert miaofa.options_complete is True
    assert [
        (option["position"], option["text"], option["status"])
        for option in miaofa.options
    ] == [
        (0, "捡漏买下", 0),
        (1, "询问出处", 1),
    ]
    assert miaofa.options[1]["aliases"] == ["询问"]


def test_question_expands_hint_into_full_ordered_options():
    question = _question([
        {"text": "询问", "status": 1, "source": "user_note"},
    ])

    selection = question.recommend(
        ["捡漏买下", "询问出处"],
        rng=random.Random(0),
    )

    assert [
        (option.position, option.text, option.status)
        for option in question.options
    ] == [
        (0, "捡漏买下", 0),
        (1, "询问出处", 1),
    ]
    assert question.options[1].aliases == ["询问"]
    assert question.options_complete is True
    assert (selection.text, selection.position, selection.reason) == (
        "询问出处",
        1,
        "positive_random",
    )


def test_multiple_positive_options_are_random_candidates():
    question = _question([
        {"text": "甲", "position": 0, "status": 1},
        {"text": "乙", "position": 1, "status": 1},
        {"text": "丙", "position": 2, "status": 0},
    ])

    selection = question.recommend(
        ["甲", "乙", "丙"],
        rng=random.Random(0),
    )

    assert selection.text in {"甲", "乙"}
    assert selection.status == 1
    assert selection.reason == "positive_random"


def test_zero_and_negative_states_still_run_top_down():
    unknown = _question([
        {"text": "甲", "position": 0, "status": 0},
        {"text": "乙", "position": 1, "status": 0},
    ])
    negative = _question([
        {"text": "甲", "position": 0, "status": -1},
        {"text": "乙", "position": 1, "status": -1},
    ])

    unknown_selection = unknown.recommend(["甲", "乙"])
    negative_selection = negative.recommend(["甲", "乙"])

    assert (unknown_selection.text, unknown_selection.reason) == (
        "甲",
        "unknown_top_down",
    )
    assert (negative_selection.text, negative_selection.reason) == (
        "甲",
        "retry_negative_top_down",
    )


def test_empty_saved_options_learn_current_screen_and_start_from_first():
    question = _question([])

    selection = question.recommend(["甲", "乙"])

    assert (selection.text, selection.reason) == ("甲", "unknown_top_down")
    assert [(option.text, option.status) for option in question.options] == [
        ("甲", 0),
        ("乙", 0),
    ]


def test_status_update_overwrites_current_state_without_history():
    engine = create_engine("sqlite://")
    FanxiuChoiceKnowledge.__table__.create(engine)
    record = FanxiuChoiceKnowledge(
        domain=DOMAIN_LILIAN_EVENT,
        prompt="测试事件",
        normalized_prompt="测试事件",
        interaction_mode="choice_click",
        options=[
            {"text": "甲", "position": 0, "status": 1},
            {"text": "乙", "position": 1, "status": 1},
            {"text": "丙", "position": 2, "status": 0},
        ],
    )

    with Session(engine) as session:
        session.add(record)
        session.commit()
        session.refresh(record)
        update_choice_status(
            session,
            record,
            observed_options=["甲", "乙", "丙"],
            selected_text="甲",
            selected_position=0,
            status=-1,
        )
        update_choice_status(
            session,
            record,
            observed_options=["甲", "乙", "丙"],
            selected_text="甲",
            selected_position=0,
            status=1,
        )
        update_choice_status(
            session,
            record,
            observed_options=["甲", "乙", "丙"],
            selected_text="甲",
            selected_position=0,
            status=-1,
        )

    assert [option.status for option in question_from_record(record).options] == [
        -1,
        1,
        0,
    ]


def test_exclusive_success_confirms_selected_and_rejects_other_options():
    engine = create_engine("sqlite://")
    FanxiuChoiceKnowledge.__table__.create(engine)
    record = FanxiuChoiceKnowledge(
        domain=DOMAIN_LILIAN_EVENT,
        prompt="测试事件",
        normalized_prompt="测试事件",
        interaction_mode="choice_click",
        options=[
            {"text": "甲", "position": 0, "status": 1},
            {"text": "乙", "position": 1, "status": 1},
            {"text": "丙", "position": 2, "status": 0},
        ],
    )
    with Session(engine) as session:
        session.add(record)
        session.commit()
        session.refresh(record)
        update_exclusive_choice_outcome(
            session,
            record,
            observed_options=["甲", "乙", "丙"],
            selected_text="乙",
            selected_position=1,
            success=True,
        )

    assert [option.status for option in question_from_record(record).options] == [
        -1,
        1,
        -1,
    ]


def test_exclusive_failure_only_rejects_selected_option():
    engine = create_engine("sqlite://")
    FanxiuChoiceKnowledge.__table__.create(engine)
    record = FanxiuChoiceKnowledge(
        domain=DOMAIN_LILIAN_EVENT,
        prompt="测试事件",
        normalized_prompt="测试事件",
        interaction_mode="choice_click",
        options=[
            {"text": "甲", "position": 0, "status": 1},
            {"text": "乙", "position": 1, "status": 1},
            {"text": "丙", "position": 2, "status": 0},
        ],
    )
    with Session(engine) as session:
        session.add(record)
        session.commit()
        session.refresh(record)
        update_exclusive_choice_outcome(
            session,
            record,
            observed_options=["甲", "乙", "丙"],
            selected_text="甲",
            selected_position=0,
            success=False,
        )

    assert [option.status for option in question_from_record(record).options] == [
        -1,
        1,
        0,
    ]


def test_memory_catalog_matches_without_a_database_query():
    record = FanxiuChoiceKnowledge(
        id="miaofa",
        domain=DOMAIN_LILIAN_EVENT,
        prompt="妙法玉简",
        normalized_prompt="妙法玉简",
        interaction_mode="choice_click",
        options=[{"text": "询问", "status": 1}],
    )
    catalog = ChoiceKnowledgeCatalog()
    assert catalog.replace([record]) == 1

    matched, score = catalog.match(
        domain=DOMAIN_LILIAN_EVENT,
        observed_prompt="妙法玉筒",
    )

    assert matched is not None
    assert matched.prompt == "妙法玉简"
    assert score >= 75


def test_legacy_lingquan_row_migrates_to_one_positive_option():
    engine = create_engine("sqlite://")
    FanxiuChoiceKnowledge.__table__.create(engine)

    with Session(engine) as session:
        session.exec(text(
            "CREATE TABLE fanxiulingquanquestion ("
            "id VARCHAR PRIMARY KEY, group_name VARCHAR, question TEXT, "
            "normalized_question VARCHAR, answer TEXT, enabled BOOLEAN, "
            "order_index INTEGER, source VARCHAR, created_at FLOAT, "
            "updated_at FLOAT)"
        ))
        session.exec(text(
            "INSERT INTO fanxiulingquanquestion "
            "(id, group_name, question, normalized_question, answer, enabled, "
            "order_index, source, created_at, updated_at) VALUES "
            "('legacy-question', '玩法知识', '测试旧题？', '测试旧题？', "
            "'旧答案', 1, 3, 'manual', 1.0, 2.0)"
        ))
        session.commit()
        v99_add_fanxiu_choice_knowledge(session)
        migrated = session.get(FanxiuChoiceKnowledge, "legacy-question")

    assert migrated is not None
    assert migrated.domain == "quiz"
    assert migrated.interaction_mode == "text_input"
    assert migrated.options_complete is False
    assert [(option["text"], option["status"]) for option in migrated.options] == [
        ("旧答案", 1),
    ]


def test_activity_quiz_seed_stores_fixed_order_and_confirmed_answer():
    engine = create_engine("sqlite://")
    FanxiuChoiceKnowledge.__table__.create(engine)

    with Session(engine) as session:
        ensure_choice_knowledge_seeds(session)
        row = session.exec(
            select(FanxiuChoiceKnowledge).where(
                FanxiuChoiceKnowledge.prompt
                == "用四个字评价韩立的样貌，这四个字是？"
            )
        ).first()

    assert row is not None
    question = question_from_record(row)
    context = question.context(CONTEXT_ACTIVITY_QUIZ)
    assert context is not None
    assert context.options_order_fixed is True
    assert context.group_name == "活动_答题"
    assert [(option.position, option.text, option.status) for option in question.options] == [
        (0, "平平无奇", 1),
        (1, "惊为天人", -1),
        (2, "奇丑无比", -1),
    ]


def test_overlapping_activity_question_keeps_both_usage_contexts():
    engine = create_engine("sqlite://")
    FanxiuChoiceKnowledge.__table__.create(engine)

    with Session(engine) as session:
        ensure_choice_knowledge_seeds(session)
        questions = [
            question_from_record(row)
            for row in session.exec(select(FanxiuChoiceKnowledge)).all()
        ]

    assert any(
        question.context(CONTEXT_LINGQUAN)
        and question.context(CONTEXT_ACTIVITY_QUIZ)
        for question in questions
    )


def test_activity_seed_does_not_overwrite_later_runtime_correction():
    engine = create_engine("sqlite://")
    FanxiuChoiceKnowledge.__table__.create(engine)

    with Session(engine) as session:
        ensure_choice_knowledge_seeds(session)
        row = session.exec(
            select(FanxiuChoiceKnowledge).where(
                FanxiuChoiceKnowledge.prompt
                == "用四个字评价韩立的样貌，这四个字是？"
            )
        ).one()
        update_exclusive_choice_outcome(
            session,
            row,
            observed_options=["平平无奇", "惊为天人", "奇丑无比"],
            selected_text="惊为天人",
            selected_position=1,
            success=True,
        )
        ensure_choice_knowledge_seeds(session)
        session.refresh(row)

    assert [option["status"] for option in row.options] == [-1, 1, -1]


def test_v100_migrates_legacy_lingquan_domain_into_quiz_context():
    engine = create_engine("sqlite://")
    with Session(engine) as session:
        session.exec(text(
            "CREATE TABLE fanxiuchoiceknowledge ("
            "id VARCHAR PRIMARY KEY, domain VARCHAR, group_name VARCHAR, "
            "prompt TEXT, normalized_prompt VARCHAR, interaction_mode VARCHAR, "
            "options JSON, options_complete BOOLEAN, order_index INTEGER, "
            "source VARCHAR, created_at FLOAT, updated_at FLOAT)"
        ))
        session.exec(text(
            "INSERT INTO fanxiuchoiceknowledge VALUES ("
            "'q1', 'lingquan', '玩法知识', '测试题？', '测试题', 'text_input', "
            "'[]', 0, 0, 'manual', 1, 1)"
        ))
        session.commit()
        v100_add_fanxiu_choice_knowledge_contexts(session)
        row = session.execute(text(
            "SELECT domain, contexts FROM fanxiuchoiceknowledge WHERE id='q1'"
        )).first()

    assert row is not None
    assert row[0] == "quiz"
    assert '"key": "lingquan"' in row[1]
    assert '"interaction_mode": "text_input"' in row[1]


def test_activity_result_does_not_fuzzy_merge_similar_distinct_questions():
    engine = create_engine("sqlite://")
    FanxiuChoiceKnowledge.__table__.create(engine)

    with Session(engine) as session:
        q6 = upsert_activity_quiz_result(
            session,
            observed_prompt="韩立是凭借什么信物加入黄枫谷的？",
            observed_options=["升仙令", "黄枫令", "宗门令"],
            correct_position=0,
        )
        q10 = upsert_activity_quiz_result(
            session,
            observed_prompt="黄枫谷中，韩立的直属师父是哪位?",
            observed_options=["李化元", "药老", "墨大夫"],
            correct_position=0,
            knowledge_id=q6.id,
        )
        q6_id, q10_id = q6.id, q10.id
        q6_prompt, q10_prompt = q6.prompt, q10.prompt

    assert q10_id != q6_id
    assert q6_prompt == "韩立是凭借什么信物加入黄枫谷的？"
    assert q10_prompt == "黄枫谷中，韩立的直属师父是哪位?"


def test_ocr_answers_drop_punctuation_before_persistence():
    engine = create_engine("sqlite://")
    FanxiuChoiceKnowledge.__table__.create(engine)

    assert clean_ocr_choice_text("（越国-）") == "越国"
    with Session(engine) as session:
        row = upsert_activity_quiz_result(
            session,
            observed_prompt="黄枫谷坐落于哪里？",
            observed_options=["（越国-", "天南。", "乱星海！"],
            correct_position=0,
        )

    assert row.answer == "越国"
    assert [option["text"] for option in row.options] == ["越国", "天南", "乱星海"]


def test_native_standard_data_replaces_matching_ocr_record():
    engine = create_engine("sqlite://")
    FanxiuChoiceKnowledge.__table__.create(engine)

    with Session(engine) as session:
        ocr = upsert_activity_quiz_result(
            session,
            observed_prompt="凡人修仙正传中，韩立的本命第一法宝是什么？",
            observed_options=["（青竹蜂云剑", "虚天鼎", "元磁神山"],
            correct_position=0,
        )
        native = upsert_activity_quiz_result(
            session,
            observed_prompt="韩立的本命法宝是什么？",
            observed_options=["青竹蜂云剑", "虚天鼎", "元磁神山"],
            correct_position=0,
            source="activity_quiz_native",
        )
        rows = list(session.exec(select(FanxiuChoiceKnowledge)).all())

    assert native.id == ocr.id
    assert len(rows) == 1
    assert native.prompt == "韩立的本命法宝是什么？"
    assert native.answer == "青竹蜂云剑"
    assert native.source == "activity_quiz_native"
