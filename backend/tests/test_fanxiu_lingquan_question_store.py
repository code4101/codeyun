from sqlmodel import Session, create_engine, select

from backend.core.fanxiu.choice_knowledge.store import (
    CONTEXT_LINGQUAN,
    DOMAIN_LINGQUAN,
    question_from_record,
)
from backend.core.fanxiu.quiz.seed import LEGACY_LINGQUAN_QUESTION_GROUPS
from backend.core.fanxiu.quiz.store import (
    ensure_lingquan_question_seed,
    match_lingquan_question,
)
from backend.models import FanxiuChoiceKnowledge


def test_seed_preserves_groups_and_legacy_matcher():
    test_engine = create_engine("sqlite://")
    FanxiuChoiceKnowledge.__table__.create(test_engine)
    expected = sum(len(items) for items in LEGACY_LINGQUAN_QUESTION_GROUPS.values())

    with Session(test_engine) as session:
        assert ensure_lingquan_question_seed(session) == expected
        assert ensure_lingquan_question_seed(session) == 0
        rows = list(session.exec(
            select(FanxiuChoiceKnowledge).where(
                FanxiuChoiceKnowledge.domain == DOMAIN_LINGQUAN
            )
        ).all())
        lingquan_rows = [
            row
            for row in rows
            if question_from_record(row).context(CONTEXT_LINGQUAN)
        ]
        assert len(lingquan_rows) == expected
        assert {row.group_name for row in lingquan_rows} == {"游戏剧情", "常识问题", "玩法知识"}

        matched, score = match_lingquan_question(session, "韩立最终从人界飞升到哪里?")
        assert matched is not None
        assert matched.answer == "灵界"
        assert score > 90


def test_match_uses_every_question_without_per_item_enable_state():
    test_engine = create_engine("sqlite://")
    FanxiuChoiceKnowledge.__table__.create(test_engine)

    with Session(test_engine) as session:
        session.add(FanxiuChoiceKnowledge(
            domain=DOMAIN_LINGQUAN,
            group_name="测试分组",
            prompt="这是一道旧数据中的停用题目吗？",
            normalized_prompt="这是一道旧数据中的停用题目吗",
            interaction_mode="text_input",
            options=[{"text": "仍然参与匹配", "status": 1}],
        ))
        session.commit()

        matched, score = match_lingquan_question(session, "这是一道旧数据中的停用题目吗？")
        assert matched is not None
        assert matched.answer == "仍然参与匹配"
        assert score == 100


def test_match_tolerates_question_prompt_prefix_without_losing_discriminator():
    test_engine = create_engine("sqlite://")
    FanxiuChoiceKnowledge.__table__.create(test_engine)

    with Session(test_engine) as session:
        ensure_lingquan_question_seed(session)

        matched, score = match_lingquan_question(session, "题目示：第一座混沌灵塔名字为？")
        assert matched is not None
        assert matched.answer == "摩诃之塔"
        assert score == 100

        _ambiguous, ambiguous_score = match_lingquan_question(
            session,
            "题目示：座混沌灵塔名字为？",
        )
        assert ambiguous_score <= 90
