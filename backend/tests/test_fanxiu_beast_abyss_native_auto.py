from __future__ import annotations

import pytest

from backend.core.fanxiu.data_annotation.tasks.beast_abyss_native_auto import (
    BEAST_ABYSS_PRODUCTION_OPTIONS,
    BeastAbyssAutoTerminal,
    BeastAbyssNativeAutoAssets,
    BeastAbyssNativeAutoRequest,
    classify_beast_abyss_auto_terminal,
    configure_beast_abyss_native_auto_options,
    prepare_beast_abyss_native_auto,
    run_beast_abyss_native_auto,
)
from backend.core.fanxiu.data_annotation.tasks.beast_abyss_task_rewards import (
    claim_beast_abyss_cultivation_rewards,
)


@pytest.fixture(autouse=True)
def _stub_cultivation_reward_maintenance(monkeypatch):
    """Keep native-auto unit tests independent from the live Runtime reader."""

    calls: list[object] = []

    def no_claimable_rewards(runtime):
        calls.append(runtime)
        if False:
            yield None
        return {
            "checked": True,
            "claimed_task_ids": [],
            "remaining_claimable": [],
            "gui_opened": False,
        }

    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.beast_abyss_native_auto."
        "claim_beast_abyss_cultivation_rewards",
        no_claimable_rewards,
    )
    return calls


class _Match:
    def __init__(self, matched: bool) -> None:
        self.matched = matched


class _Condition:
    def __init__(self, runtime, title: str) -> None:
        self.runtime = runtime
        self.title = title

    def check(self, _runtime, _frame):
        if self.title in {"自动探查", "快捷处理"}:
            return _Match(
                self.runtime.stage == "explore"
                and self.runtime.root_title == self.title
            )
        name = self.title.removesuffix("_已选").removesuffix("_未选")
        desired = self.title.endswith("_已选")
        return _Match(self.runtime.toggles[name] is desired)


class _View:
    def __init__(self, shapes: set[str]) -> None:
        self.shapes = shapes

    def get_shape(self, title: str):
        return title if title in self.shapes else None


class FakeRuntime:
    def __init__(
        self,
        *,
        bad_explore_scene: bool = False,
        root_title: str = "快捷处理",
        terminal_text: str = "已完成预设的自动探查次数",
    ) -> None:
        self.stage = "home"
        self.bad_explore_scene = bad_explore_scene
        self.root_title = root_title
        self.terminal_text = terminal_text
        self.count = 3
        self.clicks = []
        self.frame_updates = []
        self.delayed_toggles = {}
        self.pending_toggles = {}
        self.ocr_count_reads = 0
        self.pending_count_delta = 0
        self.pending_count_waits = 0
        self.toggles = {
            "仙侣事件": False,
            "妖兽事件": False,
            "玩家事件": True,
            "自动使用探查符": True,
            "被击杀停止": False,
            "快速自动": False,
            "跳过动画": False,
        }

    def current_scene(self, _views, update=False):
        rows = {
            "home": (535, "进入活动 兽渊探秘"),
            "explore": (999 if self.bad_explore_scene else 601, self.root_title),
            "help_view": (603, "开启自动 自动探查次数"),
            "running": (604, self.terminal_text),
        }
        scene, text = rows[self.stage]
        return scene, 100.0, text

    def ocr_text(self, frame):
        return frame

    def cur_frame(self, update=False):
        self.frame_updates.append(update)
        if update:
            for title in list(self.pending_toggles):
                self.pending_toggles[title] -= 1
                if self.pending_toggles[title] <= 0:
                    del self.pending_toggles[title]
                    self.toggles[title] = not self.toggles[title]
                    if title == "自动使用探查符" and not self.toggles[title]:
                        self.count = min(self.count, 30)
                    elif title == "自动使用探查符":
                        self.count = 7398
        return "frame"

    def shape_visible(self, _scene, title):
        return _Condition(self, title)

    def view(self, scene_id):
        shapes = set(self.toggles)
        shapes.update({f"{title}_已选" for title in self.toggles})
        shapes.update({f"{title}_未选" for title in self.toggles})
        if scene_id == 601:
            shapes.add(self.root_title)
        return _View(shapes)

    def click_shape_center(self, _scene, title):
        self.clicks.append(title)
        transitions = {"进入活动": "explore", "自动探查": "help_view", "快捷处理": "help_view", "开启自动": "running"}
        if title in transitions:
            self.stage = transitions[title]
        elif title.endswith("_增加"):
            self.count += 1
        elif title.endswith("_减少"):
            self.count -= 1
        else:
            delay = int(self.delayed_toggles.get(title, 0))
            if delay > 0:
                self.pending_toggles[title] = delay
            else:
                self.toggles[title] = not self.toggles[title]
                if title == "自动使用探查符" and not self.toggles[title]:
                    self.count = min(self.count, 30)
                elif title == "自动使用探查符":
                    self.count = 7398

    def wait_action_settle(self, _seconds):
        if self.pending_count_waits > 0:
            self.pending_count_waits -= 1
            if self.pending_count_waits == 0:
                self.count += self.pending_count_delta
                self.pending_count_delta = 0
        if False:
            yield None

    def wait_click_then_view(self, scene_id, title, target_scene_id, **_options):
        self.click_shape_center(scene_id, title)
        if False:
            yield None
        return target_scene_id

    def ocr_numbers_in_shapes(self, _scene, _shapes):
        self.ocr_count_reads += 1
        return [self.count], str(self.count)


def _drain(generator):
    while True:
        try:
            next(generator)
        except StopIteration as stopped:
            return stopped.value


def _assets():
    return BeastAbyssNativeAutoAssets(601, 603, (604,))


def test_cultivation_reward_maintenance_skips_gui_when_runtime_has_no_claims():
    result = _drain(
        claim_beast_abyss_cultivation_rewards(
            object(),
            reader=lambda: {
                "ok": True,
                "complete": True,
                "authorized_claim_task_ids": [],
            },
        )
    )

    assert result == {
        "checked": True,
        "claimed_task_ids": [],
        "remaining_claimable": [],
        "gui_opened": False,
    }


def test_cultivation_reward_maintenance_claims_exact_runtime_task_and_returns():
    state = {"claimed": False}

    def reader():
        return {
            "ok": True,
            "complete": True,
            "authorized_claim_task_ids": [] if state["claimed"] else [101],
            "claimed_task_ids": [101] if state["claimed"] else [],
        }

    class RewardRuntime:
        def __init__(self):
            self.scene = 657
            self.actions: list[object] = []

        def current_scene(self, expected, update=False):
            self.actions.append(("scene", tuple(expected), update))
            return self.scene, 100.0, "frame"

        def wait_click_then_view(self, scene_id, title, target_scene_ids, **_options):
            self.actions.append(("transition", scene_id, title))
            self.scene = int(target_scene_ids[0])
            if False:
                yield None
            return self.scene

        def click_shape_center(self, scene_id, title):
            self.actions.append(("click", scene_id, title))
            state["claimed"] = True

        def wait_action_settle(self, _seconds):
            if False:
                yield None

        def goto_view(self, scene_id):
            self.actions.append(("goto", scene_id))
            self.scene = int(scene_id)
            if False:
                yield None

    runtime = RewardRuntime()
    result = _drain(
        claim_beast_abyss_cultivation_rewards(runtime, reader=reader)
    )

    assert result == {
        "checked": True,
        "claimed_task_ids": [101],
        "remaining_claimable": [],
        "gui_opened": True,
    }
    assert ("click", 664, "首条任务进度区") in runtime.actions
    assert runtime.scene == 657


def test_native_auto_preflight_allows_missing_terminal_assets_without_starting():
    runtime = FakeRuntime()
    assets = BeastAbyssNativeAutoAssets(601, 603, ())

    settings = _drain(
        prepare_beast_abyss_native_auto(
            runtime,
            assets,
            BeastAbyssNativeAutoRequest(False),
        )
    )

    assert settings.requested_explores == 10
    assert runtime.stage == "help_view"
    assert "开启自动" not in runtime.clicks


def test_native_auto_preflight_always_runs_cultivation_reward_maintenance(
    _stub_cultivation_reward_maintenance,
):
    runtime = FakeRuntime()

    _drain(
        prepare_beast_abyss_native_auto(
            runtime,
            BeastAbyssNativeAutoAssets(601, 603, ()),
            BeastAbyssNativeAutoRequest(False),
        )
    )

    assert _stub_cultivation_reward_maintenance == [runtime]


def test_native_auto_measurement_clamps_item_backed_capacity_before_setting_ten():
    runtime = FakeRuntime()
    runtime.count = 7398

    settings = _drain(
        prepare_beast_abyss_native_auto(
            runtime,
            BeastAbyssNativeAutoAssets(601, 603, ()),
            BeastAbyssNativeAutoRequest(False),
        )
    )

    assert settings.auto_use_explore_items is False
    assert settings.requested_explores == 10
    assert runtime.clicks.count("自动探查次数_减少") == 20
    # Two stable reads before/after the batch, then final settings verification.
    assert runtime.ocr_count_reads == 5
    assert "开启自动" not in runtime.clicks


def test_native_auto_preflight_can_resume_from_explore_page():
    runtime = FakeRuntime()
    runtime.stage = "explore"

    settings = _drain(
        prepare_beast_abyss_native_auto(
            runtime,
            BeastAbyssNativeAutoAssets(601, 603, ()),
            BeastAbyssNativeAutoRequest(False),
        )
    )

    assert settings.requested_explores == 10
    assert "进入活动" not in runtime.clicks
    assert runtime.stage == "help_view"


def test_native_auto_production_options_are_idempotent_and_do_not_set_count():
    runtime = FakeRuntime()
    runtime.stage = "help_view"
    runtime.toggles["自动使用探查符"] = False
    runtime.count = 9

    first = _drain(
        configure_beast_abyss_native_auto_options(
            runtime,
            603,
            BEAST_ABYSS_PRODUCTION_OPTIONS,
        )
    )
    clicks_after_first = list(runtime.clicks)
    second = _drain(
        configure_beast_abyss_native_auto_options(
            runtime,
            603,
            BEAST_ABYSS_PRODUCTION_OPTIONS,
        )
    )

    assert list(first.values()) == [False, True, True, True, False, True, True]
    assert second == first
    assert runtime.clicks == clicks_after_first
    assert not any("自动探查次数_" in title for title in runtime.clicks)
    assert runtime.count == 7398


def test_native_auto_waits_for_item_capacity_recalculation_before_readback():
    runtime = FakeRuntime()
    runtime.count = 7398
    runtime.delayed_toggles["自动使用探查符"] = 4

    settings = _drain(
        prepare_beast_abyss_native_auto(
            runtime,
            BeastAbyssNativeAutoAssets(601, 603, ()),
            BeastAbyssNativeAutoRequest(False),
        )
    )

    assert settings.auto_use_explore_items is False
    assert settings.requested_explores == 10
    assert runtime.count == 10
    assert runtime.pending_toggles == {}
    assert True in runtime.frame_updates


def test_native_auto_count_absorbs_a_pending_click_before_batching():
    runtime = FakeRuntime()
    runtime.stage = "help_view"
    runtime.count = 30
    runtime.pending_count_delta = -1
    runtime.pending_count_waits = 1

    from backend.core.fanxiu.data_annotation.tasks.beast_abyss_native_auto import _set_count

    _drain(_set_count(runtime, BeastAbyssNativeAutoAssets(601, 603, ()), 10))

    assert runtime.count == 10
    assert runtime.clicks.count("自动探查次数_减少") == 19


def test_native_auto_does_not_treat_a_missing_entry_shape_as_visible():
    runtime = FakeRuntime(root_title="快捷处理")
    original_view = runtime.view

    def view(scene_id):
        if scene_id == 601:
            return _View({"自动探查"})
        return original_view(scene_id)

    runtime.view = view

    with pytest.raises(RuntimeError, match="必须在「自动探查/快捷处理」中恰好命中一个"):
        _drain(
            prepare_beast_abyss_native_auto(
                runtime,
                BeastAbyssNativeAutoAssets(601, 603, ()),
                BeastAbyssNativeAutoRequest(False),
            )
        )

    assert "快捷处理" not in runtime.clicks


def test_native_auto_full_run_fails_closed_before_start_without_terminal_assets():
    runtime = FakeRuntime()
    assets = BeastAbyssNativeAutoAssets(601, 603, ())

    with pytest.raises(RuntimeError, match="未点击「开启自动」"):
        _drain(
            run_beast_abyss_native_auto(
                runtime,
                assets,
                BeastAbyssNativeAutoRequest(False),
                poll_seconds=0,
            )
        )

    assert runtime.stage == "help_view"
    assert "开启自动" not in runtime.clicks


def test_native_auto_sets_and_reads_safe_measurement_settings():
    runtime = FakeRuntime()
    result = _drain(run_beast_abyss_native_auto(runtime, _assets(), BeastAbyssNativeAutoRequest(False), poll_seconds=0))

    assert result.terminal is BeastAbyssAutoTerminal.COMPLETED
    assert result.settings.fairy_events is True
    assert result.settings.player_events is False
    assert result.settings.auto_use_explore_items is False
    assert result.settings.stop_when_killed is True
    assert result.settings.fast_auto is True
    assert result.settings.skip_animation is True
    assert result.settings.requested_explores == 10
    assert runtime.clicks[:2] == ["进入活动", "快捷处理"]
    assert runtime.clicks[-1] == "开启自动"


def test_native_auto_rejects_ocr_candidate_when_runtime_scene_disagrees():
    runtime = FakeRuntime(bad_explore_scene=True)
    with pytest.raises(RuntimeError, match="首次进入动画"):
        _drain(run_beast_abyss_native_auto(runtime, _assets(), BeastAbyssNativeAutoRequest(False), poll_seconds=0))
    assert "快捷处理" not in runtime.clicks


@pytest.mark.parametrize("root_title", ["自动探查", "快捷处理"])
def test_native_auto_clicks_exactly_one_visible_help_root(root_title):
    runtime = FakeRuntime(root_title=root_title)
    result = _drain(
        run_beast_abyss_native_auto(
            runtime,
            _assets(),
            BeastAbyssNativeAutoRequest(False),
            poll_seconds=0,
        )
    )

    assert result.terminal is BeastAbyssAutoTerminal.COMPLETED
    assert runtime.clicks.count(root_title) == 1
    other = "快捷处理" if root_title == "自动探查" else "自动探查"
    assert other not in runtime.clicks


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("已完成预设的自动探查次数", BeastAbyssAutoTerminal.COMPLETED),
        ("探查体力和探查符不足", BeastAbyssAutoTerminal.RESOURCE_EXHAUSTED),
        ("有三个妖兽事件未完成击杀", BeastAbyssAutoTerminal.MONSTER_BLOCKED),
        ("被其他玩家击杀", BeastAbyssAutoTerminal.KILLED),
        ("自动探查中", BeastAbyssAutoTerminal.UNKNOWN),
    ],
)
def test_terminal_classification(text, expected):
    assert classify_beast_abyss_auto_terminal(text) is expected


def test_terminal_requires_runtime_scene_even_when_ocr_says_completed():
    runtime = FakeRuntime()
    assets = BeastAbyssNativeAutoAssets(601, 603, (605,))
    result = _drain(run_beast_abyss_native_auto(runtime, assets, BeastAbyssNativeAutoRequest(False), terminal_polls=1, poll_seconds=0))
    assert result.terminal is BeastAbyssAutoTerminal.UNKNOWN
    assert result.scene_id is None
