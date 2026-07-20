from sqlmodel import Session, create_engine, select

from backend.core.fanxiu.quiz.seed import LEGACY_LINGQUAN_QUESTION_GROUPS
from backend.core.fanxiu.quiz.store import ensure_lingquan_question_seed, match_lingquan_question
from backend.models import FanxiuLingquanQuestion


def test_seed_preserves_groups_and_legacy_matcher():
    test_engine = create_engine("sqlite://")
    FanxiuLingquanQuestion.__table__.create(test_engine)
    expected = sum(len(items) for items in LEGACY_LINGQUAN_QUESTION_GROUPS.values())

    with Session(test_engine) as session:
        assert ensure_lingquan_question_seed(session) == expected
        assert ensure_lingquan_question_seed(session) == 0
        rows = list(session.exec(select(FanxiuLingquanQuestion)).all())
        assert len(rows) == expected
        assert {row.group_name for row in rows} == {"游戏剧情", "常识问题", "玩法知识"}

        matched, score = match_lingquan_question(session, "韩立最终从人界飞升到哪里?")
        assert matched is not None
        assert matched.answer == "灵界"
        assert score > 90

