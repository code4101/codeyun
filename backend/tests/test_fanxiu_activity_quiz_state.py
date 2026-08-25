from backend.core.fanxiu.data_annotation.tasks.activity_quiz import (
    ActivityQuizRunState,
    _remember_native_question,
    parse_option_rows,
    parse_question_number,
    resolve_activity_quiz_native_target,
)
from backend.core.fanxiu.data_annotation.behavior_tree_control import (
    read_scheduler_tasks,
)
from backend.core.fanxiu.data_annotation.scheduler_defaults import (
    default_data_annotation_scheduler_tasks,
)
from scripts.fanxiu_bt import _require_one_shot_confirmation


def test_question_number_only_requires_first_two_integers():
    assert parse_question_number("第 6 / 15 题") == (6, 15)
    assert parse_question_number("杂字１２之１５问") == (12, 15)
    assert parse_question_number("第六题") is None


def test_result_page_cannot_rearm_completed_question():
    state = ActivityQuizRunState()
    question = state.observe_question(11, 15, "题目十一")
    assert question is not None
    assert state.mark_clicked(11) is True
    assert state.settle(11, 1) is True

    assert state.observe_question(11, 15, "题目十一") is None
    assert state.mark_clicked(11) is False
    assert state.settle(11, 1) is False


def test_missing_result_does_not_swallow_later_questions():
    state = ActivityQuizRunState()
    assert state.observe_question(6, 15, "题目六") is not None
    assert state.mark_clicked(6) is True

    question8 = state.observe_question(8, 15, "题目八")

    assert question8 is not None
    assert question8.number == 8
    assert state.missed_result_numbers == {6}
    assert state.observe_question(7, 15, "迟到的旧帧") is None


def test_batches_share_one_monotonic_state_until_question_15_settles():
    state = ActivityQuizRunState()
    for number in range(1, 16):
        question = state.observe_question(number, 15, f"题目{number}")
        assert question is not None
        assert question.batch == (number - 1) // 5 + 1
        assert state.mark_clicked(number)
        assert state.settle(number, number % 3)
        if number < 15:
            assert state.finished is False
    assert state.finished is True
    assert state.completed_numbers == set(range(1, 16))
    assert state.answer_sources == {number: "knowledge" for number in range(1, 16)}


def test_answer_source_distinguishes_ai_and_external_game_results():
    state = ActivityQuizRunState()
    assert state.observe_question(1, 15, "第一题") is not None
    assert state.mark_clicked(1, "ai")
    assert state.settle(1, 2)
    assert state.observe_question(2, 15, "第二题") is not None
    assert state.settle(2, 0)

    assert state.answer_sources == {1: "ai", 2: "external"}


def test_batch_can_advance_when_fifth_result_frame_was_missed():
    state = ActivityQuizRunState()
    question = state.observe_question(5, 15, "第五题")
    assert question is not None
    assert state.mark_clicked(5)

    state.close_batch_without_result()
    next_question = state.observe_question(6, 15, "第六题")

    assert next_question is not None
    assert state.missed_result_numbers == {5}


def test_option_rows_keep_fixed_order_and_find_correct_marker():
    tokens = [
        {"text": "甲", "x": 300, "y": 1040, "w": 30, "h": 30},
        {"text": "乙", "x": 300, "y": 1118, "w": 30, "h": 30},
        {"text": "正确", "x": 500, "y": 1118, "w": 60, "h": 30},
        {"text": "丙", "x": 300, "y": 1195, "w": 30, "h": 30},
        {"text": "错误", "x": 500, "y": 1195, "w": 60, "h": 30},
    ]

    options, correct_position = parse_option_rows(tokens)

    assert options == ["甲", "乙", "丙"]
    assert correct_position == 1


def test_activity_quiz_is_a_visible_manual_standard_job(tmp_path):
    scheduler_path = tmp_path / "scheduler.json"
    world_facts_path = tmp_path / "world-facts.json"

    tasks = read_scheduler_tasks(
        scheduler_state_path=scheduler_path,
        world_facts_path=world_facts_path,
    )
    task = next(job for job in tasks if job["task_type"] == "activity_quiz")
    assert task["id"] == "activity-quiz"
    assert task["next_time"] is None
    assert task["trigger_description"] == "手动"
    assert task["payload"] == {
        "max_runtime_seconds": 240,
        "native_snapshot_max_age_seconds": 2,
        "native_prompt_match_threshold": 82,
        "match_score_threshold": 82,
        "ai_timeout_seconds": 45,
    }


def test_activity_quiz_is_in_default_scheduler_checklist():
    task = next(
        job
        for job in default_data_annotation_scheduler_tasks()
        if job["task_type"] == "activity_quiz"
    )

    assert task["id"] == "activity-quiz"
    assert task["label"] == "活动_答题"
    assert task["trigger_description"] == "手动"
    assert task["next_time"] is None
    assert task["payload"] == {
        "max_runtime_seconds": 240,
        "native_snapshot_max_age_seconds": 2,
        "native_prompt_match_threshold": 82,
        "match_score_threshold": 82,
        "ai_timeout_seconds": 45,
    }


def test_activity_quiz_cli_requires_explicit_one_shot_confirmation():
    try:
        _require_one_shot_confirmation("activity-quiz", confirmed=False)
    except SystemExit as exc:
        assert "--confirm-one-shot" in str(exc)
    else:
        raise AssertionError("每日一次活动不应在缺少显式确认时启动")

    _require_one_shot_confirmation("activity-quiz", confirmed=True)
    _require_one_shot_confirmation("daily-redpacket", confirmed=False)


def test_activity_quiz_native_plan_requires_matching_number_and_prompt():
    snapshot = {
        "available": True,
        "fresh": True,
        "questions": [
            {
                "index": 6,
                "config_id": 3107,
                "question": "韩立的本命法宝是什么？",
                "correct_position": 1,
                "answer": "青竹蜂云剑",
            }
        ],
    }

    target, config_id = resolve_activity_quiz_native_target(
        snapshot,
        6,
        "韩立的本命法宝是什么",
    )
    assert target is not None
    assert (target.position, target.answer, config_id) == (1, "青竹蜂云剑", 3107)

    assert resolve_activity_quiz_native_target(snapshot, 7, snapshot["questions"][0]["question"]) == (None, None)
    assert resolve_activity_quiz_native_target(snapshot, 6, "完全不同的问题") == (None, None)

    state = ActivityQuizRunState()
    question = state.observe_question(6, 15, "韩立的本命法宝是什么")
    assert question is not None
    question.native_config_id = config_id
    snapshot["questions"][0]["options"] = [
        {"position": 0, "text": "虚天鼎"},
        {"position": 1, "text": "青竹蜂云剑"},
        {"position": 2, "text": "元磁神山"},
    ]
    _remember_native_question(question, snapshot)
    assert question.native_prompt == "韩立的本命法宝是什么？"
    assert question.native_options == ["虚天鼎", "青竹蜂云剑", "元磁神山"]
