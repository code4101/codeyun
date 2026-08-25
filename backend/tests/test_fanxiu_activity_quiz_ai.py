from concurrent.futures import Future

from sqlmodel import Session, create_engine

from backend.core.fanxiu.choice_knowledge.activity_quiz_ai import (
    ACTIVITY_QUIZ_AI_MODEL,
    ActivityQuizAiDecision,
    parse_activity_quiz_ai_decision,
    request_activity_quiz_ai_decision,
)
from backend.core.fanxiu.choice_knowledge.store import (
    upsert_activity_quiz_ai_guess,
    upsert_activity_quiz_result,
)
from backend.core.fanxiu.data_annotation.tasks.activity_quiz import (
    ActivityQuizQuestionState,
    _claim_ai_decision,
    _publish_ai_decision,
)
from backend.models import FanxiuChoiceKnowledge


def _test_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'quiz-ai.db'}")
    FanxiuChoiceKnowledge.__table__.create(engine, checkfirst=True)
    return engine


def test_ai_choice_is_mapped_directly_to_visible_option():
    decision = parse_activity_quiz_ai_decision(
        '{"choice":"B"}',
        ["筑基丹", "结丹丹", "元婴丹"],
    )

    assert decision.position == 1
    assert decision.choice == "B"
    assert decision.answer == "结丹丹"
    assert decision.model == ACTIVITY_QUIZ_AI_MODEL


def test_ai_choice_accepts_lowercase_but_rejects_non_choice():
    decision = parse_activity_quiz_ai_decision('{"choice":"c"}', ["甲", "乙", "丙"])
    assert decision.position == 2

    try:
        parse_activity_quiz_ai_decision('{"choice":"D"}', ["甲", "乙", "丙"])
    except ValueError as exc:
        assert "A/B/C" in str(exc)
    else:
        raise AssertionError("invalid choice should fail")


def test_ai_request_includes_labeled_options_and_uses_minimal_schema(monkeypatch):
    captured = {}

    def fake_chat_with_provider(**kwargs):
        captured.update(kwargs)
        return {"content": '{"choice":"A"}'}

    monkeypatch.setattr(
        "backend.core.fanxiu.choice_knowledge.activity_quiz_ai.chat_with_provider",
        fake_chat_with_provider,
    )
    decision = request_activity_quiz_ai_decision("谁正确？", ["甲", "乙", "丙"])

    assert decision.position == 0
    assert captured["response_format"]["required"] == ["choice"]
    content = captured["messages"][0]["content"]
    assert "A. 甲" in content and "B. 乙" in content and "C. 丙" in content


def test_ai_request_supports_four_final_options(monkeypatch):
    captured = {}

    def fake_chat_with_provider(**kwargs):
        captured.update(kwargs)
        return {"content": '{"choice":"D"}'}

    monkeypatch.setattr(
        "backend.core.fanxiu.choice_knowledge.activity_quiz_ai.chat_with_provider",
        fake_chat_with_provider,
    )
    decision = request_activity_quiz_ai_decision("谁正确？", ["甲", "乙", "丙", "丁"])

    assert (decision.position, decision.choice, decision.answer) == (3, "D", "丁")
    assert captured["response_format"]["properties"]["choice"]["enum"] == ["A", "B", "C", "D"]


def test_game_result_discards_late_ai_decision():
    question = ActivityQuizQuestionState(3, 15, "新题")
    question.observed_options = ["甲", "乙", "丙"]
    question.settled = True
    future = Future()
    future.set_result(ActivityQuizAiDecision(position=2, choice="C", answer="丙"))

    _publish_ai_decision(question, future)

    assert question.ai_decision is None
    assert _claim_ai_decision(question) is None


def test_ai_guess_is_tentative_and_runtime_truth_overrides_it(tmp_path):
    engine = _test_engine(tmp_path)
    with Session(engine) as session:
        guessed = upsert_activity_quiz_ai_guess(
            session,
            observed_prompt="谁最先到达？",
            observed_options=["甲", "乙", "丙"],
            selected_position=1,
            model=ACTIVITY_QUIZ_AI_MODEL,
        )
        assert [option["status"] for option in guessed.options] == [0, 1, 0]
        assert guessed.options[1]["source"].startswith("activity_quiz_ai")

        confirmed = upsert_activity_quiz_result(
            session,
            observed_prompt="谁最先到达？",
            observed_options=["甲", "乙", "丙"],
            correct_position=2,
            knowledge_id=guessed.id,
        )

    assert [option["status"] for option in confirmed.options] == [-1, -1, 1]
    assert all(option["source"] == "activity_quiz_runtime" for option in confirmed.options)


def test_late_ai_guess_cannot_override_runtime_truth(tmp_path):
    engine = _test_engine(tmp_path)
    with Session(engine) as session:
        confirmed = upsert_activity_quiz_result(
            session,
            observed_prompt="哪一个正确？",
            observed_options=["甲", "乙", "丙"],
            correct_position=0,
        )
        unchanged = upsert_activity_quiz_ai_guess(
            session,
            observed_prompt="哪一个正确？",
            observed_options=["甲", "乙", "丙"],
            selected_position=2,
            model=ACTIVITY_QUIZ_AI_MODEL,
        )

    assert unchanged.id == confirmed.id
    assert [option["status"] for option in unchanged.options] == [1, -1, -1]
    assert all(option["source"] == "activity_quiz_runtime" for option in unchanged.options)
