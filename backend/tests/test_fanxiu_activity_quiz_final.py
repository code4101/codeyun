import base64
import io
import threading
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from sqlmodel import Session, create_engine

from backend.core.fanxiu.choice_knowledge.model import ChoiceOption, ChoiceQuestion
from backend.core.fanxiu.choice_knowledge.activity_quiz_ai import ActivityQuizAiDecision
from backend.core.fanxiu.choice_knowledge.store import (
    CONTEXT_ACTIVITY_QUIZ_FINAL,
    upsert_activity_quiz_final_result,
)
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
)
from backend.core.fanxiu.data_annotation.default_jobs import (
    register_fanxiu_data_annotation_default_runtime_jobs,
)
from backend.core.fanxiu.data_annotation.behavior_tree_control import read_scheduler_tasks
from backend.core.fanxiu.data_annotation.scheduler_defaults import (
    default_data_annotation_scheduler_tasks,
)
from backend.core.fanxiu.data_annotation.tasks.activity_quiz_final import (
    FinalQuizOption,
    detect_final_quiz_correct_position,
    execute_activity_quiz_final_task,
    final_quiz_hint_point,
    is_authoritative_final_quiz_question,
    parse_final_quiz_options,
    resolve_final_quiz_native_target,
    resolve_final_quiz_target,
)
from scripts.fanxiu_bt import _require_one_shot_confirmation
from backend.models import FanxiuChoiceKnowledge


def _token(text, x, y, w=30, h=30):
    return {"text": text, "x": x, "y": y, "w": w, "h": h}


@pytest.fixture(autouse=True)
def _native_snapshot_unavailable_by_default(monkeypatch):
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.activity_quiz_final."
        "fanxiu_instrumentation_service.final_camp_answer_snapshot",
        lambda **_kwargs: {
            "ok": False,
            "available": False,
            "fresh": False,
            "reason": "test unavailable",
        },
    )


def test_final_options_use_current_visual_rows_and_centers():
    tokens = [
        _token("收纳异火", 300, 1300, 120),
        _token("攻防一体", 300, 1100, 120),
        _token("炼制丹药", 300, 1200, 120),
        _token("加速修炼", 300, 1000, 120),
    ]

    options = parse_final_quiz_options(tokens)

    assert [option.text for option in options] == ["加速修炼", "攻防一体", "炼制丹药", "收纳异火"]
    assert [option.y for option in options] == [1015.0, 1115.0, 1215.0, 1315.0]


def test_final_known_answer_ignores_saved_position_and_uses_current_order():
    question = ChoiceQuestion(
        domain="quiz",
        prompt="林轩的九天明月环主要功能是？",
        options=[ChoiceOption("攻防一体", status=1, position=0)],
    )
    current = (
        FinalQuizOption("加速修炼", 400, 1000),
        FinalQuizOption("炼制丹药", 400, 1100),
        FinalQuizOption("收纳异火", 400, 1200),
        FinalQuizOption("攻防一体", 400, 1300),
    )

    assert resolve_final_quiz_target(question, current) == 3


def test_final_ai_tentative_answer_has_no_click_authority():
    tentative = ChoiceQuestion(
        domain="quiz",
        prompt="未知题",
        source="activity_quiz_ai",
        options=[
            ChoiceOption(
                "猜测答案",
                status=1,
                source="activity_quiz_ai:gpt-5.3-codex-spark",
            )
        ],
    )

    assert is_authoritative_final_quiz_question(tentative) is False


def test_final_result_marker_locates_green_current_row():
    image = Image.new("RGB", (900, 1600), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((610, 1075, 660, 1125), outline=(45, 130, 90), width=8)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    frame = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
    options = tuple(
        FinalQuizOption(text, 400, y)
        for text, y in zip(("甲", "乙", "丙", "丁"), (1000, 1100, 1200, 1300), strict=True)
    )

    assert detect_final_quiz_correct_position(frame, options) == 1


def test_final_game_truth_is_persisted_without_fixed_option_order(tmp_path):
    db = create_engine(f"sqlite:///{tmp_path / 'final-quiz.db'}")
    FanxiuChoiceKnowledge.__table__.create(db, checkfirst=True)
    with Session(db) as session:
        item = upsert_activity_quiz_final_result(
            session,
            observed_prompt="哪项正确？",
            observed_options=["甲", "乙", "丙", "丁"],
            correct_position=2,
        )

    context = next(value for value in item.contexts if value["key"] == CONTEXT_ACTIVITY_QUIZ_FINAL)
    assert context["options_order_fixed"] is False
    assert [option["status"] for option in item.options] == [-1, -1, 1, -1]
    assert all(option["source"] == "activity_quiz_final_runtime" for option in item.options)


def test_activity_quiz_final_is_manual_standard_one_shot_job(tmp_path):
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("activity_quiz_final")
    assert definition is not None
    assert definition.scheduler_supported is True
    assert definition.standard_job is True
    assert definition.standard_job_id == "activity-quiz-final"

    tasks = read_scheduler_tasks(
        scheduler_state_path=tmp_path / "scheduler.json",
        world_facts_path=tmp_path / "facts.json",
    )
    task = next(item for item in tasks if item["task_type"] == "activity_quiz_final")
    assert task["id"] == "activity-quiz-final"
    assert task["label"] == "活动_答题决赛"
    assert task["trigger_description"] == "手动"
    assert task["next_time"] is None
    assert task["payload"]["ai_timeout_seconds"] == 45
    assert task["payload"]["ai_hint_interval_seconds"] == 1
    assert task["payload"]["ai_hint_max_clicks"] == 3
    assert task["payload"]["match_score_threshold"] == 82
    assert task["payload"]["native_snapshot_max_age_seconds"] == 1
    assert task["payload"]["native_wait_seconds"] == 1.2
    assert task["payload"]["native_prompt_match_threshold"] == 82

    default = next(
        item
        for item in default_data_annotation_scheduler_tasks()
        if item["task_type"] == "activity_quiz_final"
    )
    assert default["id"] == "activity-quiz-final"

    try:
        _require_one_shot_confirmation("activity-quiz-final", confirmed=False)
    except SystemExit as exc:
        assert "--confirm-one-shot" in str(exc)
    else:
        raise AssertionError("答题决赛不应在缺少显式确认时启动")


def test_final_loop_clicks_current_answer_row_not_saved_position(monkeypatch):
    question = ChoiceQuestion(
        domain="quiz",
        prompt="林轩的九天明月环主要功能是？",
        id="known-question",
        options=[ChoiceOption("攻防一体", status=1, position=0)],
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.activity_quiz_final.match_activity_quiz_question_cached",
        lambda _prompt: (question, 100.0),
    )

    class Runtime:
        def __init__(self):
            self.clicks = []

        def cur_frame(self, *, update):
            assert update is True
            return "frame"

        def ocr_tokens_in_shapes(self, _scene_id, shapes, **_kwargs):
            if tuple(shapes) == ("题目",):
                return [_token("林轩的九天明月环主要功能是？", 100, 500, 300)]
            return [
                _token("加速修炼", 300, 1000, 120),
                _token("炼制丹药", 300, 1100, 120),
                _token("收纳异火", 300, 1200, 120),
                _token("攻防一体", 300, 1300, 120),
            ]

        def click_frame_point_fast(self, scene_id, x, y):
            self.clicks.append((scene_id, x, y))

    runtime = Runtime()

    class Runner:
        def _fanxiu_runtime(self, _ctx, _path, *, stop_event):
            assert stop_event.is_set() is False
            return runtime

    result = execute_activity_quiz_final_task(
        Runner(),
        {"asset_tree_path": Path("asset-tree.json")},
        {
            "max_runtime_seconds": 1,
            "idle_after_click_seconds": 0,
            "native_wait_seconds": 0,
        },
        threading.Event(),
    )

    assert result["answered"] == 0
    assert result["confirmed_answers"] == 0
    assert result["click_attempts"] == 1
    assert runtime.clicks == [(61, 360.0, 1315.0)]


def test_final_unknown_question_uses_outer_lane_as_advisory_hint(monkeypatch):
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.activity_quiz_final.match_activity_quiz_question_cached",
        lambda _prompt: (None, 0.0),
    )
    def publish_hint(question, *, timeout_seconds):
        question.ai_decision = ActivityQuizAiDecision(
            position=2,
            choice="C",
            answer="丙",
        )
        return True

    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.activity_quiz_final._start_final_ai_request",
        publish_hint,
    )

    class Runtime:
        def __init__(self):
            self.clicks = []

        def cur_frame(self, *, update):
            return "frame"

        def view(self, _scene_id):
            return type("View", (), {"raw": {"width": 900, "height": 1600}})()

        def shape(self, _scene_id, title):
            assert title == "外框"
            return type(
                "Shape",
                (),
                {"raw": {"x": 0.86, "y": 0.60, "w": 0.09, "h": 0.26}},
            )()

        def ocr_tokens_in_shapes(self, _scene_id, shapes, **_kwargs):
            if tuple(shapes) == ("题目",):
                return [_token("用户知道但题库未知的新题", 100, 500, 300)]
            return [
                _token("甲", 300, 1000),
                _token("乙", 300, 1100),
                _token("丙", 300, 1200),
                _token("丁", 300, 1300),
            ]

        def click_frame_point_fast(self, scene_id, x, y):
            self.clicks.append((scene_id, x, y))

    runtime = Runtime()

    class Runner:
        def _fanxiu_runtime(self, _ctx, _path, *, stop_event):
            return runtime

    result = execute_activity_quiz_final_task(
        Runner(),
        {"asset_tree_path": Path("asset-tree.json")},
        {
            "max_runtime_seconds": 0.35,
            "poll_seconds": 0.01,
            "ai_hint_interval_seconds": 0.03,
            "ai_hint_max_clicks": 2,
            "native_wait_seconds": 0,
        },
        threading.Event(),
    )

    assert result["click_attempts"] == 0
    assert result["answered"] == 0
    assert result["ai_requests"] == 1
    assert result["hint_clicks"] == 2
    assert all(scene_id == 61 for scene_id, _x, _y in runtime.clicks)
    assert all(x == 814.5 and y == 1215.0 for _scene_id, x, y in runtime.clicks)


def test_final_hint_point_uses_annotated_outer_x_and_live_option_y():
    class Runtime:
        def view(self, _scene_id):
            return type("View", (), {"raw": {"width": 900, "height": 1600}})()

        def shape(self, _scene_id, title):
            assert title == "外框"
            return type(
                "Shape",
                (),
                {"raw": {"x": 0.85, "y": 0.59, "w": 0.10, "h": 0.27}},
            )()

    assert final_quiz_hint_point(Runtime(), FinalQuizOption("乙", 360, 1115)) == (810, 1115)


def test_final_missing_prompt_permanently_stops_current_question_hint(monkeypatch):
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.activity_quiz_final.match_activity_quiz_question_cached",
        lambda _prompt: (None, 0.0),
    )

    def publish_hint(question, *, timeout_seconds):
        question.ai_decision = ActivityQuizAiDecision(position=0, choice="A", answer="甲")
        return True

    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.activity_quiz_final._start_final_ai_request",
        publish_hint,
    )

    class Runtime:
        def __init__(self):
            self.prompt_reads = 0
            self.clicks = []

        def cur_frame(self, *, update):
            return "frame"

        def view(self, _scene_id):
            return type("View", (), {"raw": {"width": 900, "height": 1600}})()

        def shape(self, _scene_id, _title):
            return type(
                "Shape",
                (),
                {"raw": {"x": 0.86, "y": 0.60, "w": 0.09, "h": 0.26}},
            )()

        def ocr_tokens_in_shapes(self, _scene_id, shapes, **_kwargs):
            if tuple(shapes) == ("题目",):
                self.prompt_reads += 1
                if self.prompt_reads == 1:
                    return [_token("即将结束的新题", 100, 500, 300)]
                return []
            return [
                _token("甲", 300, 1000),
                _token("乙", 300, 1100),
                _token("丙", 300, 1200),
                _token("丁", 300, 1300),
            ]

        def click_frame_point_fast(self, scene_id, x, y):
            self.clicks.append((scene_id, x, y))

    runtime = Runtime()

    class Runner:
        def _fanxiu_runtime(self, _ctx, _path, *, stop_event):
            return runtime

    result = execute_activity_quiz_final_task(
        Runner(),
        {"asset_tree_path": Path("asset-tree.json")},
        {
            "max_runtime_seconds": 0.25,
            "poll_seconds": 0.01,
            "ai_hint_interval_seconds": 0.02,
            "ai_hint_max_clicks": 3,
            "native_wait_seconds": 0,
        },
        threading.Event(),
    )

    assert runtime.prompt_reads > 1
    assert result["hint_clicks"] == 1
    assert runtime.clicks == [(61, 814.5, 1015.0)]


def test_final_native_answer_clicks_current_shuffled_row(monkeypatch):
    snapshot = {
        "ok": True,
        "available": True,
        "fresh": True,
        "cache_age_seconds": 0.04,
        "quest_id": 3107,
        "progress": 13,
        "question": "林轩的九天明月环主要功能是？",
        "options": [
            {"id": 41, "text": "攻防一体"},
            {"id": 42, "text": "加速修炼"},
            {"id": 43, "text": "炼制丹药"},
            {"id": 44, "text": "收纳异火"},
        ],
        "correct_option_id": 41,
        "correct_answer": "攻防一体",
    }
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.activity_quiz_final."
        "fanxiu_instrumentation_service.final_camp_answer_snapshot",
        lambda **_kwargs: snapshot,
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.activity_quiz_final."
        "match_activity_quiz_question_cached",
        lambda _prompt: pytest.fail("native hit must not query fallback bank"),
    )

    current = (
        FinalQuizOption("加速修炼", 360, 1015),
        FinalQuizOption("炼制丹药", 360, 1115),
        FinalQuizOption("收纳异火", 360, 1215),
        FinalQuizOption("攻防一体", 360, 1315),
    )
    assert resolve_final_quiz_native_target(
        snapshot, snapshot["question"], current
    ) == (3, "")

    class Runtime:
        def __init__(self):
            self.clicks = []

        def cur_frame(self, *, update):
            return "frame"

        def ocr_tokens_in_shapes(self, _scene_id, shapes, **_kwargs):
            if tuple(shapes) == ("题目",):
                return [_token(snapshot["question"], 100, 500, 300)]
            return [
                _token("加速修炼", 300, 1000, 120),
                _token("炼制丹药", 300, 1100, 120),
                _token("收纳异火", 300, 1200, 120),
                _token("攻防一体", 300, 1300, 120),
            ]

        def click_frame_point_fast(self, scene_id, x, y):
            self.clicks.append((scene_id, x, y))

    runtime = Runtime()

    class Runner:
        def _fanxiu_runtime(self, _ctx, _path, *, stop_event):
            return runtime

    result = execute_activity_quiz_final_task(
        Runner(),
        {"asset_tree_path": Path("asset-tree.json")},
        {"max_runtime_seconds": 1, "idle_after_click_seconds": 0},
        threading.Event(),
    )

    assert runtime.clicks == [(61, 360.0, 1315.0)]
    assert result["native_clicks"] == 1
    assert result["click_attempts"] == 1


def test_final_native_answer_rejects_stale_or_different_question():
    options = tuple(
        FinalQuizOption(text, 360, y)
        for text, y in zip(
            ("甲", "乙", "丙", "丁"),
            (1015, 1115, 1215, 1315),
            strict=True,
        )
    )
    snapshot = {
        "available": True,
        "fresh": True,
        "question": "另一道完全不同的问题",
        "options": [
            {"id": 1, "text": "甲"},
            {"id": 2, "text": "乙"},
            {"id": 3, "text": "丙"},
            {"id": 4, "text": "丁"},
        ],
        "correct_option_id": 2,
        "correct_answer": "乙",
    }

    position, reason = resolve_final_quiz_native_target(
        snapshot,
        "当前题目是什么？",
        options,
    )
    assert position is None
    assert "题面不一致" in reason

    snapshot["question"] = "当前题目是什么？"
    snapshot["fresh"] = False
    position, reason = resolve_final_quiz_native_target(
        snapshot,
        snapshot["question"],
        options,
    )
    assert position is None
    assert reason == "Runtime 快照不可用"
