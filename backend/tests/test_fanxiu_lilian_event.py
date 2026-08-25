from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import backend.core.fanxiu.instrumentation.lilian_event as lilian_instrumentation
import backend.core.fanxiu.data_annotation.tasks.lilian_event as lilian_task
from backend.core.fanxiu.data_annotation.default_jobs import (
    register_fanxiu_data_annotation_default_runtime_jobs,
)
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
)
from backend.core.fanxiu.data_annotation.tasks.lilian_event import (
    LilianEventFlowError,
    execute_lilian_event_task,
    record_lilian_choice_reward_outcome,
    select_lilian_event_option,
)
from backend.core.fanxiu.data_annotation.scheduler_defaults import (
    default_data_annotation_scheduler_tasks,
)
from backend.core.fanxiu.data_annotation.behavior_tree_control import read_scheduler_tasks
from backend.core.fanxiu.choice_knowledge.store import question_from_record
from backend.core.fanxiu.instrumentation.lilian_event import (
    LILIAN_SUCCESS_ITEM_ID,
    select_lilian_condition_partner,
)
from backend.models import FanxiuChoiceKnowledge
from sqlmodel import Session, create_engine


def _drain(generator):
    while True:
        try:
            next(generator)
        except StopIteration as exc:
            return exc.value


class _Runtime:
    def __init__(
        self,
        scene_id,
        landings=(),
        *,
        prompt="妙法玉简",
        options=("捡漏买下", "询问出处"),
        condition_texts=("特殊条件（已满足）",),
    ):
        self.scene_id = scene_id
        self.landings = list(landings)
        self.actions = []
        self.prompt = prompt
        self.options = list(options)
        self.condition_texts = list(condition_texts)
        self.runner = SimpleNamespace(_frame_size=lambda _raw: (900, 1600))

    def current_scene(self, views, update=False):
        self.actions.append(("current_scene", tuple(views), update))
        return self.scene_id, 100.0, "frame"

    def wait_click_then_view(self, scene, shape, *targets, **options):
        self.actions.append(
            (
                "wait_click_then_view",
                scene,
                shape,
                targets,
                options,
            )
        )
        if False:
            yield None
        self.scene_id = self.landings.pop(0)
        return SimpleNamespace(id=self.scene_id)

    def click_frame_point(self, scene, x, y):
        self.actions.append(("click_frame_point", scene, x, y))
        if scene != 435:
            self.scene_id = self.landings.pop(0)

    def wait_click(self, scene, shape, **options):
        self.actions.append(("wait_click", scene, shape, options))
        if False:
            yield None

    def ocr_text_in_shapes(self, scene, shapes, **options):
        self.actions.append(("ocr_text_in_shapes", scene, shapes, options))
        if tuple(shapes) == ("特殊条件",):
            if len(self.condition_texts) > 1:
                return self.condition_texts.pop(0)
            return self.condition_texts[0]
        return self.prompt

    def wait_ocr_text(self, scene, target, **options):
        self.actions.append(("wait_ocr_text", scene, target, options))
        if False:
            yield None
        return SimpleNamespace(point=lambda: (350.0, 650.0))

    def wait_action_settle(self, seconds):
        self.actions.append(("wait_action_settle", seconds))
        if False:
            yield None

    def view(self, scene):
        return SimpleNamespace(id=scene, raw={"width": 900, "height": 1600})

    def shape(self, scene, title):
        assert scene == 435
        assert title == "状态"
        return SimpleNamespace(raw={"x": 0.77, "w": 0.11})

    def cur_frame(self, update=False):
        self.actions.append(("cur_frame", update))
        return "frame"

    def ocr_fragments_in_shapes(self, scene, shapes, **options):
        self.actions.append(("ocr_fragments_in_shapes", scene, shapes, options))
        return [{"text": option} for option in self.options]

    def click_ocr_text(self, scene, target, **options):
        self.actions.append(("click_ocr_text", scene, target, options))
        self.scene_id = self.landings.pop(0)

    def wait_view(self, *views, **options):
        self.actions.append(("wait_view", views, options))
        if False:
            yield None
        return SimpleNamespace(id=self.scene_id)

    def goto_view(self, scene_id):
        self.actions.append(("goto_view", int(scene_id)))
        self.scene_id = int(scene_id)
        if False:
            yield None
        return self.scene_id


class _Runner:
    def __init__(self, runtime, now):
        self.runtime = runtime
        self.now = now
        self.scheduled = []
        self.runtime_asset_tree_paths = []

    def _fanxiu_runtime(self, *args, **_kwargs):
        self.runtime_asset_tree_paths.append(
            args[1] if len(args) > 1 else kwargs.get("asset_tree_path")
        )
        return self.runtime

    def _now(self):
        return self.now

    def _persist_scheduler_task_next_time(self, task_id, next_time):
        self.scheduled.append((task_id, next_time))


def _ctx():
    return {"asset_tree_path": Path("asset-tree.json")}


def test_lilian_event_job_is_registered_and_in_default_checklist():
    register_fanxiu_data_annotation_default_runtime_jobs()

    definition = get_fanxiu_data_annotation_task_cell_definition(
        "lilian_event"
    )

    assert definition is not None
    assert definition.label == "历练_事件"
    assert definition.scheduler_supported is True
    assert definition.standard_job is True
    assert definition.standard_job_id == "lilian-event"
    assert definition.standard_job_description == "手动"
    assert not hasattr(definition, "lifecycle")
    task = next(
        task
        for task in default_data_annotation_scheduler_tasks(datetime(2026, 7, 30, 17, 30))
        if task["task_type"] == "lilian_event"
    )
    assert task["id"] == "lilian-event"
    assert task["next_time"] is None
    assert task["trigger_description"] == "手动"
    assert task["error_retry_delay_seconds"] == 600


def test_lilian_event_success_clears_next_time(monkeypatch):
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("lilian_event")
    assert definition is not None
    runner = _Runner(_Runtime(34), datetime(2026, 8, 6, 6, 0))
    monkeypatch.setattr(lilian_task, "execute_lilian_event_task", lambda *_args, **_kwargs: "success")

    result = _drain(definition.handler(runner, _ctx(), {}, threading.Event()))

    assert result == "success"
    assert runner.scheduled == [("lilian-event", None)]
    assert runner.runtime_asset_tree_paths == [Path("asset-tree.json")]


def test_lilian_event_failure_leaves_next_time_for_scheduler_retry(monkeypatch):
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("lilian_event")
    assert definition is not None
    runtime = _Runtime(429)
    runner = _Runner(runtime, datetime(2026, 8, 6, 6, 0))

    def fail(*_args, **_kwargs):
        raise RuntimeError("历练失败")

    monkeypatch.setattr(lilian_task, "execute_lilian_event_task", fail)

    with pytest.raises(RuntimeError, match="历练失败"):
        _drain(definition.handler(runner, _ctx(), {}, threading.Event()))

    assert runner.scheduled == []
    assert ("goto_view", 34) in runtime.actions


def test_lilian_complete_reward_updates_selected_option():
    engine = create_engine("sqlite://")
    FanxiuChoiceKnowledge.__table__.create(engine)
    record = FanxiuChoiceKnowledge(
        id="miaofa",
        domain="lilian_event",
        prompt="妙法玉简",
        normalized_prompt="妙法玉简",
        interaction_mode="choice_click",
        options=[
            {"text": "捡漏买下", "position": 0, "status": 0},
            {"text": "询问出处", "position": 1, "status": 1},
        ],
    )
    with Session(engine) as session:
        session.add(record)
        session.commit()
        record_lilian_choice_reward_outcome(
            session,
            knowledge_id="miaofa",
            observed_options=["捡漏买下", "询问出处"],
            selected_text="询问出处",
            selected_position=1,
            rewards=[
                {"code": LILIAN_SUCCESS_ITEM_ID, "amount": 1},
                {"code": 9070095, "amount": 1},
            ],
            capture_complete=True,
        )
        session.refresh(record)

    assert [
        option.status for option in question_from_record(record).options
    ] == [-1, 1]


def test_lilian_explicit_win_is_authoritative_without_magic_item():
    engine = create_engine("sqlite://")
    FanxiuChoiceKnowledge.__table__.create(engine)
    record = FanxiuChoiceKnowledge(
        id="explicit-win",
        domain="lilian_event",
        prompt="大罗之命",
        normalized_prompt="大罗之命",
        interaction_mode="choice_click",
        options=[
            {"text": "原地不动", "position": 0, "status": 0},
            {"text": "干脆离开", "position": 1, "status": 0},
        ],
    )
    with Session(engine) as session:
        session.add(record)
        session.commit()
        record_lilian_choice_reward_outcome(
            session,
            knowledge_id="explicit-win",
            observed_options=["原地不动", "干脆离开"],
            selected_text="原地不动",
            selected_position=0,
            rewards=[{"code": 9070095, "amount": 1}],
            capture_complete=True,
            success=True,
        )
        session.refresh(record)

    assert [
        option.status for option in question_from_record(record).options
    ] == [1, -1]


def test_lilian_incomplete_reward_never_updates_question():
    engine = create_engine("sqlite://")
    FanxiuChoiceKnowledge.__table__.create(engine)
    record = FanxiuChoiceKnowledge(
        id="miaofa",
        domain="lilian_event",
        prompt="妙法玉简",
        normalized_prompt="妙法玉简",
        interaction_mode="choice_click",
        options=[
            {"text": "捡漏买下", "position": 0, "status": 0},
            {"text": "询问出处", "position": 1, "status": 1},
        ],
    )
    with Session(engine) as session:
        session.add(record)
        session.commit()
        with pytest.raises(ValueError, match="读取不完整"):
            record_lilian_choice_reward_outcome(
                session,
                knowledge_id="miaofa",
                observed_options=["捡漏买下", "询问出处"],
                selected_text="询问出处",
                selected_position=1,
                rewards=[],
                capture_complete=False,
            )
        session.refresh(record)
        statuses = [
            option.status for option in question_from_record(record).options
        ]

    assert statuses == [0, 1]


def test_lilian_unknown_event_selects_first_option_and_creates_knowledge():
    engine = create_engine("sqlite://")
    FanxiuChoiceKnowledge.__table__.create(engine)

    with Session(engine) as session:
        knowledge, selection = select_lilian_event_option(
            session,
            observed_prompt="全新事件",
            observed_options=["先试这个", "再试那个"],
        )

    assert selection.text == "先试这个"
    assert selection.reason == "unknown_top_down"
    assert knowledge.source == "lilian_event_runtime"
    assert [option["text"] for option in knowledge.options] == [
        "先试这个",
        "再试那个",
    ]


def test_lilian_captain_conditions_select_live_partner_without_name_length_limit():
    snapshot = {
        "complete": True,
        "partners": [
            {"id": 1, "name": "银月", "sex": 2, "career": 4},
            {"id": 2, "name": "小极宫主", "sex": 2, "career": 2},
            {"id": 3, "name": "萧炎", "sex": 1, "career": 3},
        ],
    }

    sword = select_lilian_condition_partner(snapshot, "CaptainCareer|1")
    male = select_lilian_condition_partner(snapshot, "CaptainSex|1")
    included = select_lilian_condition_partner(snapshot, "IncludeXianLv|2_1")

    assert sword["name"] == "银月"
    assert sword["selection_role"] == "captain"
    assert male["name"] == "萧炎"
    assert included["name"] == "小极宫主"


def test_lilian_partner_snapshot_cache_is_scoped_to_game_process(tmp_path, monkeypatch):
    cache_path = tmp_path / "partner-snapshot.json"
    monkeypatch.setattr(
        lilian_instrumentation,
        "_lilian_partner_snapshot_cache_path",
        lambda: cache_path,
    )
    memory = SimpleNamespace(pid=123, process_start_ticks=456)
    snapshot = {
        "ok": True,
        "complete": True,
        "partners": [{"id": 1, "name": "银月", "sex": 2, "career": 4}],
    }

    lilian_instrumentation._write_lilian_partner_snapshot_cache(memory, snapshot)

    cached = lilian_instrumentation._read_lilian_partner_snapshot_cache(memory)
    assert cached is not None
    assert cached["snapshot_cache_hit"] is True
    assert cached["partners"] == snapshot["partners"]
    assert lilian_instrumentation._read_lilian_partner_snapshot_cache(
        SimpleNamespace(pid=123, process_start_ticks=457)
    ) is None


@pytest.mark.parametrize("scene_id", [436, 437, 438, 429])
def test_lilian_event_executor_rejects_cross_attempt_intermediate_scene(scene_id):
    runtime = _Runtime(scene_id)

    with pytest.raises(LilianEventFlowError, match="作业入口必须是 #34"):
        _drain(execute_lilian_event_task(
            _Runner(runtime, datetime(2026, 8, 25, 10, 30)),
            _ctx(),
            {},
            threading.Event(),
        ))

    assert not any(action[0] == "wait_click_then_view" for action in runtime.actions)


def test_lilian_event_handles_438_to_437_inside_one_job_attempt():
    runtime = _Runtime(
        34,
        landings=(
            425, 427, 428, 434, 435, 436,
            438, 437, 438, 425,
            427, 429, 425, 34,
        ),
        prompt="未知事件",
        options=("甲", "乙"),
    )

    result = _drain(
        execute_lilian_event_task(
            _Runner(runtime, datetime(2026, 8, 25, 10, 30)),
            _ctx(),
            {},
            threading.Event(),
        )
    )

    assert result["result"] == "success"
    assert result["current_scene"] == 34
    assert result["processed_event_count"] == 1
    reward_actions = [
        (action[1], action[2], action[3])
        for action in runtime.actions
        if action[0] == "wait_click_then_view"
        and action[1] in (437, 438)
    ]
    assert reward_actions == [
        (438, "关闭", (425, 437)),
        (437, "领取", (438,)),
        (438, "关闭", (425, 437)),
    ]


def test_lilian_standard_job_normalizes_437_to_world_before_whole_run(monkeypatch):
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("lilian_event")
    assert definition is not None
    runtime = _Runtime(437)
    runner = _Runner(runtime, datetime(2026, 8, 25, 10, 30))
    executor_entry_scenes = []

    def execute_from_world(*_args, **_kwargs):
        executor_entry_scenes.append(runtime.scene_id)
        return "success"

    monkeypatch.setattr(lilian_task, "execute_lilian_event_task", execute_from_world)

    result = _drain(definition.handler(runner, _ctx(), {}, threading.Event()))

    assert result == "success"
    assert executor_entry_scenes == [34]
    assert runtime.actions == [("goto_view", 34), ("goto_view", 34)]


def test_lilian_event_428_runs_full_base_flow(monkeypatch):
    test_engine = create_engine("sqlite://")
    FanxiuChoiceKnowledge.__table__.create(test_engine)
    with Session(test_engine) as session:
        session.add(FanxiuChoiceKnowledge(
            id="miaofa",
            domain="lilian_event",
            prompt="妙法玉简",
            normalized_prompt="妙法玉简",
            interaction_mode="choice_click",
            options=[
                {"text": "捡漏买下", "position": 0, "status": -1},
                {"text": "询问出处", "position": 1, "status": 1},
            ],
        ))
        session.commit()
    monkeypatch.setattr(lilian_task, "engine", test_engine)
    runtime = _Runtime(
        34,
        landings=(425, 427, 428, 434, 435, 436, 437, 438, 425, 427, 429, 425, 34),
    )
    runner = _Runner(runtime, datetime(2026, 7, 30, 17, 30))

    result = _drain(
        execute_lilian_event_task(
            runner,
            _ctx(),
            {"__scheduler_task_id": "lilian-event"},
            threading.Event(),
        )
    )

    assert result["result"] == "success"
    assert result["current_scene"] == 34
    assert result["processed_event_count"] == 1
    assert result["selected_option"] == "询问出处"
    assert runner.scheduled == []
    assert [
        (action[1], action[2])
        for action in runtime.actions
        if action[0] == "wait_click_then_view"
    ] == [
        (34, "大地图"),
        (425, "历练按钮"),
        (427, "事件"),
        (428, "前往"),
        (434, "历练"),
        (435, "派遣"),
        (437, "领取"),
            (438, "关闭"),
            (425, "历练按钮"),
            (427, "事件"),
            (429, "关闭事件页"),
    ]
    assert any(
        action[:3] == ("click_ocr_text", 436, "询问出处")
        for action in runtime.actions
    )


def test_lilian_event_same_attempt_retries_choice_after_confirmed_436_timeout(
    monkeypatch,
):
    test_engine = create_engine("sqlite://")
    FanxiuChoiceKnowledge.__table__.create(test_engine)
    with Session(test_engine) as session:
        session.add(FanxiuChoiceKnowledge(
            domain="lilian_event",
            prompt="英雄救美",
            normalized_prompt="英雄救美",
            options=[
                {"text": "踹他一脚", "position": 0, "status": 1},
                {"text": "赠予美酒", "position": 1, "status": -1},
            ],
        ))
        session.commit()
    monkeypatch.setattr(lilian_task, "engine", test_engine)

    class RetryRuntime(_Runtime):
        click_count = 0

        def click_ocr_text(self, scene, target, **options):
            self.actions.append(("click_ocr_text", scene, target, options))
            self.click_count += 1
            if self.click_count == 2:
                self.scene_id = 438

        def wait_view(self, *views, **options):
            self.actions.append(("wait_view", views, options))
            if False:
                yield None
            if self.click_count == 1:
                raise TimeoutError("still #436")
            return SimpleNamespace(id=self.scene_id)

    runtime = RetryRuntime(
        34,
        landings=(
            425, 427, 428, 434, 435, 436,
            425, 427, 429, 425, 34,
        ),
        options=("踹他一脚", "赠予美酒"),
    )
    result = _drain(execute_lilian_event_task(
        _Runner(runtime, datetime(2026, 8, 17, 13, 0)),
        _ctx(),
        {"lilian_option_click_attempts": 2},
        threading.Event(),
    ))

    assert result["result"] == "success"
    assert runtime.click_count == 2


def test_lilian_captain_condition_puts_selected_partner_first(monkeypatch):
    test_engine = create_engine("sqlite://")
    FanxiuChoiceKnowledge.__table__.create(test_engine)
    monkeypatch.setattr(lilian_task, "engine", test_engine)
    monkeypatch.setattr(
        lilian_task,
        "read_lilian_partner_snapshot",
        lambda: {
            "complete": True,
            "partners": [
                {"id": 9, "name": "银月", "sex": 2, "career": 4},
                {"id": 28, "name": "萧炎", "sex": 1, "career": 2},
            ],
        },
    )
    event = next(
        item
        for item in lilian_task.load_lilian_event_catalog()["events"]
        if item["name"] == "酗酒剑仙"
    )
    runtime = _Runtime(
        34,
        landings=(425, 427, 428, 434, 435, 436, 437, 438, 425, 427, 429, 425, 34),
        prompt=event["name"],
        options=tuple(choice["text"] for choice in event["choices"]),
        condition_texts=("特殊条件（未满足）", "特殊条件（已满足）"),
    )

    result = _drain(
        execute_lilian_event_task(
            _Runner(runtime, datetime(2026, 8, 6, 10, 0)),
            _ctx(),
            {},
            threading.Event(),
        )
    )

    assert result["result"] == "success"
    assert result["selected_partner"]["name"] == "银月"
    assert result["selected_partner"]["selection_role"] == "captain"
    assert not any(
        action[:3] == ("wait_click", 435, "一键上阵")
        for action in runtime.actions
    )
    assert [
        (action[2], action[3].get("occurrence"))
        for action in runtime.actions
        if action[0] == "wait_ocr_text"
    ] == [
        ("银月", None),
        ("上阵", 0),
        ("上阵", 0),
        ("上阵", 0),
        ("上阵", 0),
    ]


def test_lilian_event_enters_from_world_before_base_flow(monkeypatch):
    test_engine = create_engine("sqlite://")
    FanxiuChoiceKnowledge.__table__.create(test_engine)
    monkeypatch.setattr(lilian_task, "engine", test_engine)
    runtime = _Runtime(
        34,
        landings=(
            425, 427, 428,
            434, 435, 436, 437, 438, 425,
            427, 429, 425, 34,
        ),
        prompt="未知事件",
        options=("甲", "乙"),
    )
    runner = _Runner(runtime, datetime(2026, 7, 30, 17, 30))

    result = _drain(
        execute_lilian_event_task(
            runner,
            _ctx(),
            {},
            threading.Event(),
        )
    )

    assert result["selected_option"] == "甲"
    assert [
        (action[1], action[2], action[3])
        for action in runtime.actions
        if action[0] == "wait_click_then_view"
    ] == [
        (34, "大地图", (425,)),
        (425, "历练按钮", (427,)),
        (427, "事件", (428, 429)),
        (428, "前往", (434,)),
        (434, "历练", (435,)),
        (435, "派遣", (436,)),
        (437, "领取", (438,)),
            (438, "关闭", (425, 437)),
            (425, "历练按钮", (427,)),
            (427, "事件", (428, 429)),
            (429, "关闭事件页", (425,)),
    ]


def test_lilian_event_processes_every_event_until_fresh_429(monkeypatch):
    test_engine = create_engine("sqlite://")
    FanxiuChoiceKnowledge.__table__.create(test_engine)
    monkeypatch.setattr(lilian_task, "engine", test_engine)
    runtime = _Runtime(
        34,
        landings=(
            425, 427, 428,
            434, 435, 436, 437, 438, 425,
            427, 428,
            434, 435, 436, 437, 438, 425,
            427, 429,
            425, 34,
        ),
        prompt="未知事件",
        options=("甲", "乙"),
    )

    result = _drain(
        execute_lilian_event_task(
            _Runner(runtime, datetime(2026, 8, 7, 12, 0)),
            _ctx(),
            {},
            threading.Event(),
        )
    )

    assert result["result"] == "success"
    assert result["current_scene"] == 34
    assert result["processed_event_count"] == 2
    assert len(result["processed_events"]) == 2
    assert sum(
        action[:3] == ("wait_click_then_view", 428, "前往")
        for action in runtime.actions
    ) == 2
    assert (
        "wait_click_then_view",
        429,
        "关闭事件页",
        (425,),
        {"timeout": 20.0},
    ) in runtime.actions
    assert [
        action[:2]
        for action in runtime.actions
        if action[0] == "click_frame_point" and action[1] != 435
    ][-1:] == [("click_frame_point", 425)]


def test_lilian_event_standard_job_is_materialized_as_manual_instance(tmp_path):
    scheduler_path = tmp_path / "scheduler.json"
    world_facts_path = tmp_path / "world-facts.json"
    now = datetime(2026, 7, 30, 17, 30)

    tasks = read_scheduler_tasks(
        scheduler_state_path=scheduler_path,
        world_facts_path=world_facts_path,
        now=now,
    )
    task = next(item for item in tasks if item["task_type"] == "lilian_event")
    assert task["id"] == "lilian-event"
    assert task["next_time"] is None
    assert task["trigger_description"] == "手动"
    assert task["template_source"] == "preset"
