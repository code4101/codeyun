from __future__ import annotations

from types import SimpleNamespace

from backend.core.fanxiu.data_annotation.default_jobs import register_fanxiu_data_annotation_default_runtime_jobs
from backend.core.fanxiu.data_annotation.jobs import get_fanxiu_data_annotation_task_cell_definition
from backend.core.fanxiu.data_annotation.scheduler_defaults import default_data_annotation_scheduler_tasks
from backend.core.fanxiu.data_annotation.tasks.moyu_signup import MoyuSignupTaskMixin


class _Runner(MoyuSignupTaskMixin):
    pass


class _Runtime:
    def __init__(self, *, signed: bool):
        self.signed = signed
        self.actions = []
        self.next_times = []

    def set_next_time(self, next_time):
        self.next_times.append(next_time)

    def wait_click_then_view(self, source, shape, target):
        self.actions.append(("click_then_view", source, shape, target))
        yield "running"
        return SimpleNamespace(id=target)

    def open_daily_entry(self, **options):
        self.actions.append(("open_daily_entry", options))
        yield "running"
        return "open"

    def wait_view_or_ocr(self, target, predicate, **options):
        self.actions.append(("wait_view_or_ocr", target, predicate("大道外域 魔狱封阵"), options))
        yield "running"
        return ("text", target, 0.0)

    def ocr_text(self, *, update):
        assert update is True
        return "已报名二阶·青冥蝠君" if self.signed else "报名"

    def wait_click(self, source, shape):
        self.actions.append(("click", source, shape))
        self.signed = True
        yield "running"

    def ocr_matches(self, predicate, **_options):
        return ("ocr", predicate)

    def wait_any(self, conditions, **options):
        self.actions.append(("wait_any", tuple(conditions), options))
        assert conditions["已报名"][1]("已报名") is True
        yield "running"
        return "已报名"


def _consume(generator):
    while True:
        try:
            next(generator)
        except StopIteration as exc:
            return exc.value


def test_moyu_signup_is_registered_as_scheduler_job_without_scene_policy():
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("moyu_signup")

    assert definition is not None
    assert definition.label == "魔狱_报名"
    assert definition.scheduler_supported is True
    assert not hasattr(definition, "lifecycle")


def test_moyu_signup_is_available_as_daily_template_by_default():
    task = next(task for task in default_data_annotation_scheduler_tasks() if task["id"] == "moyu-signup")

    assert task["task_type"] == "moyu_signup"
    assert task["label"] == "魔狱_报名"
    assert task["trigger_description"] == "每日"
    assert task["next_time"] is not None


def test_moyu_signup_flow_clicks_signup_and_returns_to_world():
    runtime = _Runtime(signed=False)
    result = _consume(_Runner().moyu_signup_flow(runtime))

    assert result["result"] == "success"
    assert result["current_scene"] == 34
    assert result["message"] == "魔狱_报名：已报名并回到世界"
    assert result["already_signed"] is False
    assert "next_time" not in result
    assert runtime.next_times
    assert ("click", 401, "报名") in runtime.actions
    assert runtime.actions[-2:] == [
        ("click_then_view", 401, "返回", 400),
        ("click_then_view", 400, "返回", 34),
    ]
    entry = next(action for action in runtime.actions if action[0] == "open_daily_entry")
    assert entry[1]["title_pattern"] == r"魔狱|封阵"
    assert entry[1]["progress_can_mark_done"] is False
    travel = next(action for action in runtime.actions if action[0] == "wait_view_or_ocr")
    assert travel == (
        "wait_view_or_ocr",
        400,
        True,
        {
            "timeout": 180.0,
            "label": "魔狱_报名：等待自动寻路抵达大道外域 #400",
        },
    )


def test_moyu_signup_flow_is_idempotent_when_already_signed():
    runtime = _Runtime(signed=True)
    result = _consume(_Runner().moyu_signup_flow(runtime))

    assert result["result"] == "success"
    assert result["already_signed"] is True
    assert not any(action[:3] == ("click", 401, "报名") for action in runtime.actions)
    assert runtime.actions[-2:] == [
        ("click_then_view", 401, "返回", 400),
        ("click_then_view", 400, "返回", 34),
    ]


def test_moyu_signup_treats_challenge_phase_as_already_signed():
    assert _Runner._moyu_signup_text_is_signed("珍贵奖励 挑战? 魔狱封阵 奖励") is True
    assert _Runner._moyu_signup_text_is_signed("魔狱封阵 报名") is False
