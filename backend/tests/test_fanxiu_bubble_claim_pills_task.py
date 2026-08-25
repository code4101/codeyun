from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from backend.core.fanxiu.data_annotation.default_jobs import register_fanxiu_data_annotation_default_runtime_jobs
from backend.core.fanxiu.data_annotation.jobs import get_fanxiu_data_annotation_task_cell_definition
from backend.core.fanxiu.data_annotation.scheduler_defaults import default_data_annotation_scheduler_tasks
from backend.core.fanxiu.data_annotation.tasks.bubble_claim_pills import BubbleClaimPillsTaskMixin
from backend.core.fanxiu.data_annotation.tasks.bubble_lifecycle import read_bubble_lifecycle_fact


def _done(value=None):
    if False:
        yield None
    return value


def _drain(generator):
    try:
        while True:
            next(generator)
    except StopIteration as exc:
        return exc.value


class _Item:
    def __init__(self, page: int, row: int, y: float) -> None:
        self.page = page
        self.row = row
        self.item_box = {"x": 36.0, "y": y, "w": 828.0, "h": 210.0}


class _FakeRuntime:
    def __init__(self, *, pages: list[list[bool]], landings: list[int | None] | None = None) -> None:
        self.scene_id: int | None = 591
        self.pages = [list(page) for page in pages]
        self.page = 0
        self.landings = list(landings or [])
        self.selected = False
        self.pending: tuple[int, int] | None = None
        self.role_text = "止清ღ羊驼 最近 岁序更替 243级"
        self.actions: list[tuple] = []

    def current_scene(self, views, **_kwargs):
        requested = [int(item) for item in views]
        scene = self.scene_id if self.scene_id in requested else None
        self.actions.append(("scene", tuple(requested), scene))
        return scene, 100.0 if scene is not None else 0.0, "frame"

    def open_sdk_bubble_menu(self, **_kwargs):
        self.actions.append(("open_menu",))
        self.scene_id = 590
        return _done(type("View", (), {"id": 590})())

    def click_shape_center_then_view(self, scene_id, shape, target, **_kwargs):
        self.actions.append(("shape_then_view", scene_id, shape, target))
        self.scene_id = int(target)
        if int(target) == 591:
            self.page = 0
        return _done(type("View", (), {"id": int(target)})())

    def find_floating_items_by_anchor_text(self, *_args, **kwargs):
        self.actions.append(("find_items", self.page, kwargs.get("container_shape")))
        if len(_args) >= 4 and _args[3] != "领取":
            return []
        return [_Item(self.page, row, 860.0 + row * 230.0) for row in range(len(self.pages[self.page]))]

    def ocr_tokens_in_shapes(self, *_args, **_kwargs):
        return [
            {
                "text": f"gift-{self.page}-{row}",
                "x": 80.0,
                "y": 885.0 + row * 230.0,
                "w": 180.0,
                "h": 36.0,
            }
            for row in range(len(self.pages[self.page]))
        ]

    def floating_item_field_is_fully_inside(self, item, field, container):
        self.actions.append(("inside", item.page, item.row, field, container))
        return True

    def click_floating_item_field(self, item, field):
        self.actions.append(("click_item", item.page, item.row, field, 760.0))
        if self.pages[item.page][item.row]:
            self.pending = (item.page, item.row)
            self.scene_id = 592

    def scroll_shape_content(self, scene_id, shape, **_kwargs):
        if _kwargs.get("direction") == "up":
            self.actions.append(("rewind", scene_id, shape, self.page))
            if self.page <= 0:
                return _done(False)
            self.page -= 1
            return _done(True)
        self.actions.append(("scroll", scene_id, shape, self.page))
        if self.page + 1 >= len(self.pages):
            return _done(False)
        self.page += 1
        return _done(True)

    def shape_matches(self, scene_id, shape):
        self.actions.append(("shape_matches", scene_id, shape))
        if scene_id == 592 and shape == "选择当前角色":
            return None if self.selected else {"matched": True}
        raise AssertionError((scene_id, shape))

    def cur_frame(self, update=False):
        self.actions.append(("frame", update))
        return "frame"

    def ocr_text_in_shapes(self, scene_id, shapes, **_kwargs):
        self.actions.append(("ocr", scene_id, tuple(shapes)))
        return self.role_text

    def wait_click(self, scene_id, shape, **_kwargs):
        self.actions.append(("wait_click", scene_id, shape))
        if scene_id == 592 and shape == "选择当前角色":
            self.selected = True
        elif scene_id == 592 and shape == "确认":
            assert self.selected and self.pending is not None
            page, row = self.pending
            self.pages[page][row] = False
            self.pending = None
            self.selected = False
            self.scene_id = self.landings.pop(0) if self.landings else 591
        elif scene_id == 421 and shape == "气泡":
            self.scene_id = 34
        else:
            raise AssertionError((scene_id, shape))
        return _done("clicked")

    def wait_action_settle(self, seconds):
        self.actions.append(("settle", seconds))
        return _done("settled")

    def match_view(self, scene_id, **_kwargs):
        self.actions.append(("match_view", scene_id))
        return self.scene_id == int(scene_id), 100.0, "frame"


class _Runner(BubbleClaimPillsTaskMixin):
    def __init__(self, runtime: _FakeRuntime, world_facts_path: Path) -> None:
        self.runtime = runtime
        self.world_facts_path = world_facts_path
        self.next_times: list[tuple[str, str | None]] = []
        self.logs: list[tuple[str, str]] = []

    def _fanxiu_runtime(self, *_args, **_kwargs):
        return self.runtime

    def _persist_scheduler_task_next_time(self, task_id, next_time):
        self.next_times.append((task_id, next_time))

    def _bubble_lifecycle_world_facts_path(self):
        return self.world_facts_path

    def _log(self, level, message):
        self.logs.append((level, message))


def _run(runtime: _FakeRuntime, world_facts_path: Path):
    runner = _Runner(runtime, world_facts_path)
    result = _drain(runner._execute_bubble_claim_pills_task(
        {"asset_tree_path": Path("asset-tree.json")},
        object(),
        {"poll_seconds": 0.2, "claim_probe_timeout_seconds": 1.0},
    ))
    return result, runner


def test_bubble_claim_pills_scans_rows_top_to_bottom_and_scrolls_window(tmp_path):
    runtime = _FakeRuntime(pages=[[True, True], [True, False], [False]])
    result, runner = _run(runtime, tmp_path / "world-facts.json")

    assert result["result"] == "success"
    assert result["claim_count"] == 3
    assert [action[1:3] for action in runtime.actions if action[0] == "click_item"] == [
        (0, 0), (0, 1), (1, 0), (1, 1), (2, 0),
    ]
    assert ("rewind", 591, "窗口", 2) in runtime.actions
    assert ("rewind", 591, "窗口", 1) in runtime.actions
    assert all(action[-1] == 760.0 for action in runtime.actions if action[0] == "click_item")
    assert sum(action[:3] == ("wait_click", 592, "确认") for action in runtime.actions) == 3
    assert runtime.scene_id == 34
    assert runner.next_times == []
    assert read_bubble_lifecycle_fact(runner.world_facts_path)["claim_count"] == 3


def test_bubble_claim_pills_retries_only_first_three_rows_once(tmp_path):
    runtime = _FakeRuntime(pages=[[False, False]])
    result, _runner = _run(runtime, tmp_path / "world-facts.json")

    assert result["claim_count"] == 0
    assert [action[1:3] for action in runtime.actions if action[0] == "click_item"] == [
        (0, 0), (0, 0), (0, 1), (0, 1),
    ]
    assert sum(action[0] == "scroll" for action in runtime.actions) == 1


def test_bubble_claim_role_tolerates_bounded_unknown_transition_before_592(tmp_path):
    runner = _Runner(_FakeRuntime(pages=[[]]), tmp_path / "world-facts.json")
    scenes = iter([None, None, 592])

    class _Runtime:
        def click_floating_item_field(self, _item, _field):
            return None

        def wait_action_settle(self, _seconds):
            if False:
                yield None

        def current_scene(self, *_args, **_kwargs):
            return next(scenes), 0.0, "frame"

        def match_view(self, *_args, **_kwargs):
            return False, 0.0, "frame"

        def find_floating_items_by_anchor_text(self, *_args, **_kwargs):
            return []

        def ocr_tokens_in_shapes(self, *_args, **_kwargs):
            return []

    opened = _drain(
        runner._try_open_bubble_claim_role(
            _Runtime(),
            object(),
            timeout=3.0,
            poll_seconds=0.75,
        )
    )

    assert opened is True


def test_bubble_claim_pills_structurally_resumes_591_after_combined_scene_is_none(tmp_path):
    runtime = _FakeRuntime(pages=[[True, False]], landings=[None])
    result, _runner = _run(runtime, tmp_path / "world-facts.json")

    assert result["claim_count"] == 1
    assert ("open_menu",) not in runtime.actions
    assert runtime.scene_id == 34


def test_bubble_claim_pills_resumes_dynamic_591_by_structural_items(tmp_path):
    runtime = _FakeRuntime(pages=[[False]])

    def no_scene(_views, **_kwargs):
        return None, 0.0, "frame"

    runtime.current_scene = no_scene
    runtime.match_view = lambda *_args, **_kwargs: (False, 0.0, "frame")
    runner = _Runner(runtime, tmp_path / "world-facts.json")

    result = _drain(runner._execute_bubble_claim_pills_task(
        {"asset_tree_path": Path("asset-tree.json")},
        object(),
        {"poll_seconds": 0.2, "claim_probe_timeout_seconds": 1.0},
    ))

    assert result["result"] == "success"
    assert ("open_menu",) not in runtime.actions


def test_bubble_claim_pills_uses_already_claimed_only_as_structure_not_weekly_count(tmp_path):
    runtime = _FakeRuntime(pages=[[False, False, False]])

    def completed_items(*args, **kwargs):
        runtime.actions.append(("find_items", runtime.page, kwargs.get("container_shape")))
        if len(args) >= 4 and args[3] == "已领取":
            return [_Item(runtime.page, row, 860.0 + row * 230.0) for row in range(3)]
        return []

    runtime.find_floating_items_by_anchor_text = completed_items
    result, runner = _run(runtime, tmp_path / "world-facts.json")

    assert result["claim_count"] == 0
    assert result["weekly_claim_count"] == 0
    assert not any(action[0] == "click_item" for action in runtime.actions)
    fact = read_bubble_lifecycle_fact(runner.world_facts_path)
    assert fact["claim_count"] == 0
    assert fact.get("claimed_item_ids") in (None, [])


def test_bubble_claim_pills_scheduler_mode_requires_three_completed_top_rewards(tmp_path):
    runtime = _FakeRuntime(pages=[[False, False]])
    runner = _Runner(runtime, tmp_path / "world-facts.json")

    with pytest.raises(RuntimeError, match="0/3 个“已领取”条目"):
        _drain(runner._execute_bubble_claim_pills_task(
            {"asset_tree_path": Path("asset-tree.json")},
            object(),
            {
                "poll_seconds": 0.2,
                "claim_probe_timeout_seconds": 1.0,
                "minimum_completed_rewards": 3,
            },
        ))

    fact = read_bubble_lifecycle_fact(runner.world_facts_path)
    assert fact.get("claimed_week") is None
    assert not any(task_id == "bubble-hide" for task_id, _next in runner.next_times)


def test_bubble_claim_pills_refuses_non_target_role_before_selection(tmp_path):
    runtime = _FakeRuntime(pages=[[True]])
    runtime.role_text = "另一角色 最近 243级"
    runner = _Runner(runtime, tmp_path / "world-facts.json")

    with pytest.raises(RuntimeError, match="未确认目标角色"):
        _drain(runner._execute_bubble_claim_pills_task(
            {"asset_tree_path": Path("asset-tree.json")}, object(), {},
        ))

    assert not any(action[:3] == ("wait_click", 592, "选择当前角色") for action in runtime.actions)
    assert runner.next_times == []


def test_bubble_claim_phase_records_week_without_scheduling_another_job(tmp_path):
    runner = _Runner(_FakeRuntime(pages=[[]]), tmp_path / "world-facts.json")
    next_time = runner._record_bubble_claim_pills_done(
        {"__scheduler_task_id": "bubble-instance"},
        claim_count=3,
        now=datetime(2026, 8, 18, 23, 30),
    )
    assert next_time is None
    assert runner.next_times == []
    fact = read_bubble_lifecycle_fact(runner.world_facts_path)
    assert fact["claimed_week"] == "2026-W34"
    assert fact["claim_count"] == 3
    assert "hide_pending_week" not in fact


def test_bubble_is_one_weekly_job_at_monday_0010():
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("bubble_weekly_pills")
    assert definition is not None
    assert definition.label == "气泡_每周丹药"
    assert definition.scheduler_supported is True
    assert get_fanxiu_data_annotation_task_cell_definition("bubble_claim_pills") is None
    assert get_fanxiu_data_annotation_task_cell_definition("bubble_hide") is None
    assert get_fanxiu_data_annotation_task_cell_definition("bubble_weekly_restart") is None

    tasks = {task["id"]: task for task in default_data_annotation_scheduler_tasks(now=datetime(2026, 8, 18, 23, 30))}
    bubble = tasks["bubble-weekly-pills"]
    assert bubble["task_type"] == "bubble_weekly_pills"
    assert bubble["label"] == "气泡_每周丹药"
    assert bubble["trigger_description"] == "每周"
    assert bubble["next_time"] == "2026-08-24 00:10:00"
    assert bubble["dispatch_level"] == 1
    assert not {"bubble-claim-pills", "bubble-hide", "bubble-weekly-restart"} & tasks.keys()


def test_bubble_weekly_wrapper_never_normalizes_underlying_game_scene():
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("bubble_weekly_pills")
    assert definition is not None
    sentinel = object()

    class _WrapperRunner:
        def _fanxiu_runtime(self, *_args, **_kwargs):
            raise AssertionError("wrapper must not create a runtime for goto_view")

        def _execute_bubble_weekly_task(self, ctx, stop_event, payload):
            assert ctx == {"current_scene": "xutian-exploration"}
            assert payload == {"claimed_week": "current"}
            assert stop_event is sentinel
            return _done({"result": "success", "branch": "hide_only"})

    result = _drain(definition.handler(
        _WrapperRunner(),
        {"current_scene": "xutian-exploration"},
        {"claimed_week": "current"},
        sentinel,
    ))

    assert result == {"result": "success", "branch": "hide_only"}
