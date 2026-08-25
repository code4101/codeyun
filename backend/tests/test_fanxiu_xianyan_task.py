from __future__ import annotations

from types import GeneratorType

from PIL import Image, ImageDraw

from backend.core.fanxiu.data_annotation.default_jobs import (
    register_fanxiu_data_annotation_default_runtime_jobs,
)
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
)
from backend.core.fanxiu.data_annotation.scheduler_defaults import (
    default_data_annotation_scheduler_tasks,
)
from backend.core.fanxiu.behavior_tree.runtime import create_behavior_tree_runtime_runner


def _drain(value):
    if not isinstance(value, GeneratorType):
        return value
    while True:
        try:
            next(value)
        except StopIteration as exc:
            return exc.value


def test_xianyan_rewards_is_registered_as_manual_scheduler_task():
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("xianyan_rewards")
    task = next(
        item
        for item in default_data_annotation_scheduler_tasks()
        if item["id"] == "xianyan-rewards"
    )

    assert definition is not None
    assert definition.label == "仙宴_获得奖励"
    assert definition.scheduler_supported is True
    assert not hasattr(definition, "lifecycle")
    assert task["task_type"] == "xianyan_rewards"
    assert task["label"] == "仙宴_获得奖励"
    assert task["trigger_description"] == "手动"
    assert task["next_time"] is None


def test_xianyan_host_all_is_one_manual_standard_job():
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("xianyan_host_baihua")
    tasks = [
        item
        for item in default_data_annotation_scheduler_tasks()
        if item["id"] == "xianyan-host-baihua"
    ]

    assert definition is not None
    assert definition.label == "仙宴_清理"
    assert definition.scheduler_supported is True
    assert len(tasks) == 1
    assert tasks[0]["trigger_description"] == "手动"
    assert tasks[0]["next_time"] is None
    assert tasks[0]["payload"] == {
        "max_rounds": 100,
        "max_scrolls": 100,
        "max_runtime_seconds": 3600,
    }


def test_xianyan_participation_is_a_reusable_manual_standard_job():
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("xianyan_participation")
    tasks = [
        item
        for item in default_data_annotation_scheduler_tasks()
        if item["id"] == "xianyan-participation"
    ]

    assert definition is not None
    assert definition.label == "仙宴_参与同档"
    assert definition.scheduler_supported is True
    assert len(tasks) == 1
    assert tasks[0]["trigger_description"] == "手动"
    assert tasks[0]["next_time"] is None
    assert tasks[0]["payload"] == {
        "max_rounds": 100,
        "max_scrolls": 100,
        "max_runtime_seconds": 3600,
    }


def test_baihua_stock_requires_one_non_negative_number():
    runner = create_behavior_tree_runtime_runner()

    class Runtime:
        def ocr_numbers_in_shapes(self, view, shapes, **options):
            assert (view, shapes) == (649, ["百花宴拥有数量"])
            assert options == {"frame_data_url": "frame", "crop": True}
            return [22], "拥有22个"

    assert runner._read_baihua_banquet_count(Runtime(), "frame") == 22


def test_xianyan_stock_reads_all_types_in_priority_order():
    runner = create_behavior_tree_runtime_runner()

    class Runtime:
        def ocr_numbers_in_shapes(self, view, shapes, **options):
            assert view == 649
            assert options == {"frame_data_url": "frame", "crop": True}
            return {
                "百花宴拥有数量": ([0], "拥有0个"),
                "龙凤宴拥有数量": ([1], "拥有1个"),
                "瑶星宴拥有数量": ([0], "拥有0个"),
            }[shapes[0]]

    assert runner._read_xianyan_banquet_counts(Runtime(), "frame") == {
        "百花宴": 0,
        "龙凤宴": 1,
        "瑶星宴": 0,
    }


def test_xianyan_gifts_must_match_banquet_quality_and_fail_closed():
    runner = create_behavior_tree_runtime_runner()

    assert runner._matching_xianyan_gift("百花宴") == "随礼·碧螺春"
    assert runner._matching_xianyan_gift("龙凤仙宴") == "随礼·白玉酿"
    assert runner._matching_xianyan_gift("瑶星宴") == "随礼·醉仙酿"
    assert runner._matching_xianyan_gift_is_allowed(
        "龙凤仙宴",
        {"随礼·白玉酿": True, "随礼·醉仙酿": True},
    )
    assert not runner._matching_xianyan_gift_is_allowed(
        "龙凤仙宴",
        {"随礼·白玉酿": False, "随礼·醉仙酿": True},
    )


def test_xianyan_participation_candidates_skip_ended_and_preserve_occurrence():
    runner = create_behavior_tree_runtime_runner()
    fragments = [
        {"text": "龙凤仙宴", "y": 500, "h": 30},
        {"text": "宾客数：3/5", "y": 540, "h": 30},
        {"text": "查看", "y": 510, "h": 40},
        {"text": "百花宴", "y": 700, "h": 30},
        {"text": "宾客数：0/5", "y": 740, "h": 30},
        {"text": "已经结束", "y": 760, "h": 30},
        {"text": "查看", "y": 710, "h": 40},
    ]

    candidates = runner._xianyan_participation_candidates(fragments)

    assert candidates[0]["occurrence"] == 0
    assert candidates[0]["banquet_name"] == "龙凤仙宴"
    assert candidates[0]["guest_count"] == 3
    assert candidates[0]["ended"] is False
    assert "3/5" not in candidates[0]["stable_key"]
    assert candidates[1]["occurrence"] == 1
    assert candidates[1]["ended"] is True


def test_xianyan_gift_row_policy_rejects_forced_upgrade():
    runner = create_behavior_tree_runtime_runner()
    rows = runner._xianyan_gift_rows_from_fragments(
        [
            {"text": "随礼白玉酿", "y": 100, "h": 20},
            {"text": "不允许", "y": 102, "h": 20},
            {"text": "随礼醉仙酿", "y": 200, "h": 20},
            {"text": "9", "y": 202, "h": 20},
        ],
    )

    assert rows == {
        "随礼·碧螺春": False,
        "随礼·白玉酿": False,
        "随礼·醉仙酿": True,
    }
    assert not runner._matching_xianyan_gift_is_allowed("龙凤仙宴", rows)


def test_xianyan_shortcut_detail_state_distinguishes_spend_from_attended_transition():
    runner = create_behavior_tree_runtime_runner()

    assert runner._xianyan_detail_state(
        "赴宴礼物：随礼·白玉酿（90） 更换 剩余13位道友可供查看"
    ) == {"state": "white_ready", "white_count": 90, "remaining": 13}
    assert runner._xianyan_detail_state("已赴宴 查看下一个 剩余0位道友可供查看") == {
        "state": "attended",
        "white_count": None,
        "remaining": 0,
    }
    assert runner._xianyan_detail_state("拥有可用礼物：99") == {
        "state": "picker_required",
        "white_count": None,
        "remaining": None,
    }


def test_xianyan_checkbox_green_ratio_requires_a_real_green_signal():
    runner = create_behavior_tree_runtime_runner()
    unchecked = Image.new("RGB", (100, 100), (35, 35, 35))
    checked = unchecked.copy()
    ImageDraw.Draw(checked).rectangle((25, 25, 34, 34), fill=(20, 190, 80))
    box = (0.2, 0.2, 0.2, 0.2)

    assert runner._xianyan_green_checkbox_ratio(unchecked, box) == 0.0
    assert runner._xianyan_green_checkbox_ratio(checked, box) >= 0.2


def test_xianyan_remaining_time_uses_one_full_ocr_line():
    runner = create_behavior_tree_runtime_runner()

    class Runtime:
        def full_frame_ocr_tokens(self, frame):
            assert frame == "frame"
            return [
                {"text": text, "parent_line_id": "countdown", "order": order}
                for order, text in enumerate(("剩", "余", "时", "间", "：", "02", ":", "40", ":", "10"))
            ]

    assert runner._read_xianyan_remaining_seconds(Runtime(), "frame") == 2 * 3600 + 40 * 60 + 10


def test_xianyan_entry_visibility_requires_live_activity_label():
    runner = create_behavior_tree_runtime_runner()

    class Runtime:
        def __init__(self, texts):
            self.texts = texts

        def full_frame_ocr_tokens(self, frame):
            assert frame == "frame"
            return [{"text": text} for text in self.texts]

    assert runner._xianyan_entry_is_visible(Runtime(["仙园", "游宴"]), "frame")
    assert not runner._xianyan_entry_is_visible(Runtime(["炼丹", "特惠"]), "frame")


def test_xianyan_entry_click_uses_live_ocr_box_and_waits_for_home():
    runner = create_behavior_tree_runtime_runner()
    calls = []

    class Match:
        text = "仙园游宴"

        @staticmethod
        def point():
            return 204.0, 426.5

    class Runtime:
        def click_ocr_text(self, view, target, **options):
            calls.append(("click", view, target, options))
            return Match()

        def wait_scene(self, *views, **options):
            calls.append(("wait", views, options))
            if False:
                yield None
            return 630

    assert _drain(runner._open_xianyan_entry(Runtime(), "frame")) == 630
    assert calls[0] == (
        "click",
        20,
        "仙园游宴",
        {
            "in_shapes": ["仙园游宴"],
            "frame_data_url": "frame",
            "crop": True,
        },
    )
    assert calls[1] == (
        "wait",
        (630,),
        {"timeout": 30.0, "label": "仙园游宴：等待活动主页"},
    )


def test_xianyan_host_treats_noop_host_button_as_idempotent_completion(monkeypatch):
    runner = create_behavior_tree_runtime_runner()

    class Runtime:
        def __init__(self):
            self.actions = []
            self.completion_message = ""

        def current_scene(self, candidates, *, update=False):
            self.actions.append(("scene", tuple(candidates), update))
            return 642, 100.0, "home"

        def go_scene(self, target):
            self.actions.append(("go_scene", target))
            yield 1

        def wait_click(self, source, shape, **options):
            self.actions.append(("click", source, shape, options))
            yield 1

        def wait_view(self, *targets, **options):
            self.actions.append(("wait_view", targets, options))
            yield 1
            return 642

        def set_completion_message(self, message):
            self.completion_message = message

    runtime = Runtime()
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: runtime)

    result = _drain(
        runner._execute_xianyan_host_baihua_task(
            {"asset_tree_path": None},
            type("StopEvent", (), {"is_set": lambda self: False})(),
            {},
        )
    )

    assert result == "success"
    assert runtime.actions[0] == ("go_scene", 34)
    assert (
        "wait_view",
        (659, 649, 642),
        {"timeout": 15.0, "label": "百花宴：等待选择层或下一份结算"},
    ) in runtime.actions
    assert runtime.completion_message == (
        "仙宴举办幂等结束：举办入口未打开选择层，"
        "活动已结束或当前没有可举办宴席"
    )


def test_xianyan_rewards_claims_until_scene_422_disappears(monkeypatch):
    runner = create_behavior_tree_runtime_runner()

    class Runtime:
        def __init__(self):
            self.scenes = iter(
                [
                    (422, 100.0, "frame-422"),
                    (422, 100.0, "frame-422"),
                    (None, 0.0, "done"),
                ]
            )
            self.actions = []
            self.completion_message = ""

        def current_scene(self, candidates, *, update=False):
            self.actions.append(("scene", tuple(candidates), update))
            return next(self.scenes)

        def go_scene(self, target):
            self.actions.append(("go_scene", target))
            yield 1

        def full_frame_ocr_tokens(self, frame):
            assert frame == "done"
            return []

        def wait_click_then_view(self, source, shape, target, **options):
            self.actions.append(("click_then_view", source, shape, target, options))
            yield 1
            return target

        def wait_view(self, *targets, **options):
            self.actions.append(("wait_view", targets, options))
            yield 1
            return targets[0]

        def wait_click(self, source, shape):
            self.actions.append(("click", source, shape))
            yield 1

        def wait_action_settle(self, seconds):
            self.actions.append(("settle", seconds))
            yield 1

        def set_completion_message(self, message):
            self.completion_message = message

    runtime = Runtime()
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: runtime)
    persisted = []
    monkeypatch.setattr(
        runner,
        "_persist_scheduler_task_next_time",
        lambda task_id, next_time: persisted.append((task_id, next_time)),
    )

    result = _drain(
        runner._execute_xianyan_rewards_task(
            {"asset_tree_path": None},
            type("StopEvent", (), {"is_set": lambda self: False})(),
            {},
        )
    )

    assert result == "success"
    assert runtime.actions == [
        ("go_scene", 34),
        ("scene", (422, 423, 642, 659), True),
        ("scene", (422, 423, 642, 659), True),
        ("click", 422, "获得奖励"),
        (
            "wait_view",
            (423, 642, 659),
            {"timeout": 15.0, "label": "仙宴_获得奖励：等待奖励层或幂等返回"},
        ),
        ("click", 423, "继续"),
        ("settle", 1.0),
        ("scene", (422, 423, 642, 659), True),
    ]
    assert persisted == [("xianyan-rewards", None)]
    assert runtime.completion_message == "仙宴_获得奖励：完成，共领取 1 轮"


def test_every_public_xianyan_job_normalizes_to_world_before_same_attempt_logic(monkeypatch):
    methods = (
        ("_execute_xianyan_host_baihua_task", "_execute_xianyan_host_baihua_same_attempt"),
        ("_execute_xianyan_rewards_task", "_execute_xianyan_rewards_same_attempt"),
        ("_execute_xianyan_participation_task", "_execute_xianyan_participation_same_attempt"),
    )

    for public_name, same_attempt_name in methods:
        runner = create_behavior_tree_runtime_runner()
        actions = []

        class Runtime:
            def go_scene(self, target):
                actions.append(("go_scene", target))
                yield target

        runtime = Runtime()
        monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: runtime)

        def same_attempt(actual_runtime, _stop_event, _payload):
            actions.append(("same_attempt", same_attempt_name, actual_runtime is runtime))
            if False:
                yield None
            return "success"

        monkeypatch.setattr(runner, same_attempt_name, same_attempt)
        result = _drain(
            getattr(runner, public_name)(
                {"asset_tree_path": None},
                type("StopEvent", (), {"is_set": lambda self: False})(),
                {},
            )
        )

        assert result == "success"
        assert actions == [
            ("go_scene", 34),
            ("same_attempt", same_attempt_name, True),
        ]


def test_xianyan_clean_normalizes_once_and_reuses_one_same_attempt_runtime(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    actions = []

    class Runtime:
        def go_scene(self, target):
            actions.append(("go_scene", target))
            yield target

    runtime = Runtime()
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: runtime)

    for name in (
        "_execute_xianyan_host_baihua_same_attempt",
        "_execute_xianyan_participation_same_attempt",
        "_execute_xianyan_rewards_same_attempt",
    ):
        def stage(actual_runtime, _stop_event, _payload, *, stage_name=name):
            actions.append((stage_name, actual_runtime is runtime))
            if False:
                yield None
            return "success"

        monkeypatch.setattr(runner, name, stage)

    assert _drain(
        runner._execute_xianyan_clean_task(
            {"asset_tree_path": None},
            type("StopEvent", (), {"is_set": lambda self: False})(),
            {},
        )
    ) == "success"
    assert actions == [
        ("go_scene", 34),
        ("_execute_xianyan_host_baihua_same_attempt", True),
        ("_execute_xianyan_participation_same_attempt", True),
        ("_execute_xianyan_rewards_same_attempt", True),
    ]


def test_xianyan_reward_result_overlay_is_dismissed_before_completion():
    runner = create_behavior_tree_runtime_runner()

    class Runtime:
        def __init__(self):
            self.scenes = iter([(659, 100.0, "stale-result"), (None, 0.0, "done")])
            self.actions = []

        def current_scene(self, candidates, *, update=False):
            self.actions.append(("scene", tuple(candidates), update))
            return next(self.scenes)

        def wait_click(self, source, shape):
            self.actions.append(("click", source, shape))
            yield 1

        def wait_action_settle(self, seconds):
            self.actions.append(("settle", seconds))
            yield 1

    runtime = Runtime()
    claimed, scene_id, frame = _drain(
        runner._claim_available_xianyan_rewards(
            runtime,
            type("StopEvent", (), {"is_set": lambda self: False})(),
            max_rounds=2,
            settle_seconds=1.0,
            wait_timeout=15.0,
        )
    )

    assert (claimed, scene_id, frame) == (0, None, "done")
    assert ("click", 659, "点击屏幕继续") in runtime.actions


def test_xianyan_reward_direct_home_keeps_wait_view_landing():
    runner = create_behavior_tree_runtime_runner()

    class Runtime:
        def __init__(self):
            self.actions = []

        def current_scene(self, candidates, *, update=False):
            self.actions.append(("scene", tuple(candidates), update))
            return 422, 100.0, "reward-page"

        def wait_click(self, source, shape):
            self.actions.append(("click", source, shape))
            yield 1

        def wait_view(self, *targets, **kwargs):
            self.actions.append(("wait_view", targets, kwargs))
            yield 1
            return 642

    runtime = Runtime()
    claimed, scene_id, frame = _drain(
        runner._claim_available_xianyan_rewards(
            runtime,
            type("StopEvent", (), {"is_set": lambda self: False})(),
            max_rounds=2,
            settle_seconds=1.0,
            wait_timeout=15.0,
        )
    )

    assert (claimed, scene_id, frame) == (1, 642, "reward-page")
    assert [action[0] for action in runtime.actions].count("scene") == 1
