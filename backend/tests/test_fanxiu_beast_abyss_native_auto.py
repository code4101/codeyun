from __future__ import annotations

import pytest

from backend.core.fanxiu.activity.beast_abyss_challenge_planning import (
    BeastAbyssAutoSettings,
)
from backend.core.fanxiu.data_annotation.tasks.beast_abyss_native_auto import (
    BEAST_ABYSS_PRODUCTION_OPTIONS,
    TOGGLES,
    BeastAbyssAutoTerminal,
    BeastAbyssNativeAutoAssets,
    BeastAbyssNativeAutoRequest,
    classify_beast_abyss_auto_terminal,
    parse_beast_abyss_terminal_evidence,
    configure_beast_abyss_native_auto_options,
    prepare_beast_abyss_native_auto,
    run_beast_abyss_native_auto,
)
from backend.core.fanxiu.data_annotation.tasks.beast_abyss_task_rewards import (
    claim_beast_abyss_task_rewards,
)


@pytest.fixture(autouse=True)
def _stub_task_reward_maintenance(monkeypatch):
    """Keep native-auto unit tests independent from the task-reward flow."""

    calls: list[object] = []

    def no_claimable_rewards(runtime):
        calls.append(runtime)
        if False:
            yield None
        return {
            "checked": True,
            "tabs": [],
            "tab_results": [],
            "detected_advances": 0,
            "remaining_claimable": [],
            "gui_opened": False,
        }

    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.beast_abyss_native_auto."
        "claim_beast_abyss_task_rewards",
        no_claimable_rewards,
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.beast_abyss_runtime."
        "read_beast_abyss_auto_count_snapshot",
        lambda: {"current": 1, "minimum": 1, "maximum": 7398},
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
        self.drags: list[str] = []
        self.frame_drags: list[tuple[float, float]] = []
        self.slider_maximum = 7398
        self.frame_updates = []
        self.delayed_toggles = {}
        self.pending_toggles = {}
        self.ocr_count_reads = 0
        self.pending_count_delta = 0
        self.pending_count_waits = 0
        self.transient_unknown_scene_reads = 0
        self.toggles = {
            "仙侣事件": False,
            "妖兽事件": False,
            "玩家事件": True,
            "自动使用探查符": True,
            "被击杀停止": False,
            "快速自动": False,
            "跳过动画": False,
            "使用寻妖符": False,
        }

    def sample_scene_once(self, _views, update=False):
        if self.transient_unknown_scene_reads > 0:
            self.transient_unknown_scene_reads -= 1
            return None, 0.0, "frame"
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

    def wait_click_then_scene(self, scene_id, title, target_scene_id, **_options):
        self.click_shape_center(scene_id, title)
        if False:
            yield None
        return target_scene_id

    def ocr_numbers_in_shapes(self, _scene, _shapes):
        self.ocr_count_reads += 1
        return [self.count], str(self.count)

    def read_integer_slider_runtime_range(self):
        return {"minimum": 1, "maximum": self.slider_maximum}

    def shape_center(self, _scene, title, **_kwargs):
        if title.endswith("左端"):
            return (0.0, 20.0)
        if title.endswith("右端"):
            return (100.0, 20.0)
        return ((self.count - 1) / (self.slider_maximum - 1) * 100.0, 20.0)

    def shape_box(self, _scene, _title):
        return {"x": 0.0, "y": 15.0, "w": 0.0, "h": 10.0}

    def shape_center_in_box(self, _scene, _title, _search_box):
        return ((self.count - 1) / (self.slider_maximum - 1) * 100.0, 20.0)

    def drag_frame_point(self, _scene, _sx, _sy, ex, _ey, *, duration_ms):
        assert duration_ms == 1000
        self.frame_drags.append((_sx, ex))
        self.count = max(
            1,
            min(
                self.slider_maximum,
                round(1 + ex / 100.0 * (self.slider_maximum - 1)),
            ),
        )

    def drag_shape_to_frame_edge(self, _scene, title, *, direction, duration):
        self.drags.append(f"{title}:{direction}")
        self.count = self.slider_maximum if direction == "right" else 1

    def drag_shape_between_shapes_fraction(
        self,
        _scene,
        title,
        _left,
        _right,
        *,
        fraction,
        duration,
    ):
        self.drags.append(f"{title}:fraction:{fraction:.6f}")
        self.count = round(1 + float(fraction) * (self.slider_maximum - 1))


def _drain(generator):
    while True:
        try:
            next(generator)
        except StopIteration as stopped:
            return stopped.value


def _assets():
    return BeastAbyssNativeAutoAssets(601, 603, (604,))


def test_real_terminal_text_keeps_score_merit_and_observed_index_distinct() -> None:
    text = (
        "探查结束 第185次探查 总共获得积分：12345 "
        "总共获得功勋：678 点击屏幕关闭"
    )
    evidence = parse_beast_abyss_terminal_evidence(text)

    assert classify_beast_abyss_auto_terminal(text) is BeastAbyssAutoTerminal.COMPLETED
    assert evidence.terminal_total_score == 12_345
    assert evidence.terminal_total_merit == 678
    assert evidence.terminal_observed_explore_index == 185
    assert not hasattr(evidence, "terminal_completed_explores")


def test_native_auto_default_terminal_budget_exceeds_old_five_minute_limit() -> None:
    from backend.core.fanxiu.data_annotation.tasks.beast_abyss_native_auto import (
        run_prepared_beast_abyss_native_auto,
    )

    class SlowTerminalRuntime:
        def __init__(self):
            self.polls = 0

        def click_shape_center(self, *_args):
            return None

        def wait_action_settle(self, _seconds):
            if False:
                yield None

        def sample_scene_once(self, _expected, update=False):
            self.polls += 1
            if self.polls <= 300:
                return None, 0.0, "自动探查中"
            return 604, 100.0, "探查结束 第185次探查 点击屏幕关闭"

        def ocr_text(self, frame):
            return frame

    runtime = SlowTerminalRuntime()
    request = BeastAbyssNativeAutoRequest(auto_use_explore_items=True)
    settings = BeastAbyssAutoSettings(
        fairy_events=False,
        beast_events=True,
        player_events=True,
        auto_use_explore_items=True,
        stop_when_killed=False,
        fast_auto=True,
        skip_animation=True,
        requested_explores=100,
    )

    result = _drain(
        run_prepared_beast_abyss_native_auto(
            runtime,
            BeastAbyssNativeAutoAssets(601, 603, (604,)),
            request,
            settings,
        )
    )

    assert runtime.polls == 301
    assert result.terminal is BeastAbyssAutoTerminal.COMPLETED




def test_native_auto_preflight_allows_missing_terminal_assets_without_starting():
    runtime = FakeRuntime()
    assets = BeastAbyssNativeAutoAssets(601, 603, ())

    settings = _drain(
        prepare_beast_abyss_native_auto(
            runtime,
            assets,
            BeastAbyssNativeAutoRequest(True),
        )
    )

    assert settings.requested_explores == 100
    assert runtime.stage == "help_view"
    assert "开启自动" not in runtime.clicks
    assert runtime.clicks.count("自动探查次数_增加") == 0
    assert len(runtime.frame_drags) == 1


def test_native_auto_uses_live_slider_maximum_not_resource_safety_capacity():
    runtime = FakeRuntime()
    runtime.count = runtime.slider_maximum

    settings = _drain(
        prepare_beast_abyss_native_auto(
            runtime,
            BeastAbyssNativeAutoAssets(601, 603, ()),
            BeastAbyssNativeAutoRequest(
                True,
                maximum_explores=900,
            ),
        )
    )

    assert settings.requested_explores == 100
    assert runtime.frame_drags == [(100.0, pytest.approx(99 / 7397 * 100))]
    assert runtime.clicks.count("自动探查次数_减少") == 0


def test_native_auto_preflight_does_not_couple_task_reward_maintenance(
    _stub_task_reward_maintenance,
):
    runtime = FakeRuntime()

    _drain(
        prepare_beast_abyss_native_auto(
            runtime,
            BeastAbyssNativeAutoAssets(601, 603, ()),
            BeastAbyssNativeAutoRequest(True),
        )
    )

    assert _stub_task_reward_maintenance == []


def test_native_auto_no_item_profile_clamps_capacity_before_setting_count():
    runtime = FakeRuntime()
    runtime.count = 7398

    settings = _drain(
        prepare_beast_abyss_native_auto(
            runtime,
            BeastAbyssNativeAutoAssets(601, 603, ()),
            BeastAbyssNativeAutoRequest(
                False,
                measurement=False,
                requested_explores=10,
            ),
        )
    )

    assert settings.auto_use_explore_items is False
    assert settings.requested_explores == 10
    assert runtime.clicks.count("自动探查次数_减少") == 0
    assert len(runtime.frame_drags) == 1
    assert runtime.ocr_count_reads >= 4
    assert "开启自动" not in runtime.clicks


def test_native_auto_preflight_can_resume_from_explore_page():
    runtime = FakeRuntime()
    runtime.stage = "explore"

    settings = _drain(
        prepare_beast_abyss_native_auto(
            runtime,
            BeastAbyssNativeAutoAssets(601, 603, ()),
            BeastAbyssNativeAutoRequest(True),
        )
    )

    assert settings.requested_explores == 100
    assert "进入活动" not in runtime.clicks
    assert runtime.stage == "help_view"


def test_native_auto_entry_absorbs_a_transient_unknown_scene():
    runtime = FakeRuntime()
    runtime.transient_unknown_scene_reads = 1

    settings = _drain(
        prepare_beast_abyss_native_auto(
            runtime,
            BeastAbyssNativeAutoAssets(601, 603, ()),
            BeastAbyssNativeAutoRequest(True),
        )
    )

    assert settings.requested_explores == 100
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

    assert first == {
        "fairy_events": False,
        "beast_events": True,
        "player_events": True,
        "auto_use_explore_items": True,
        "stop_when_killed": False,
        "fast_auto": True,
        "skip_animation": True,
        "use_find_demon_talisman": False,
    }
    assert second == first
    assert runtime.clicks == clicks_after_first
    assert not any("自动探查次数_" in title for title in runtime.clicks)
    assert runtime.count == 7398


def test_native_auto_option_identity_is_semantic_and_not_positional() -> None:
    assert set(BEAST_ABYSS_PRODUCTION_OPTIONS.as_dict()) == set(TOGGLES)
    assert TOGGLES["fairy_events"].alias == "仙缘事件"
    assert TOGGLES["fairy_events"].display_text == "触发仙缘事件，跳过对话直接获得奖励"
    assert TOGGLES["use_find_demon_talisman"].alias == "寻妖符"
    assert TOGGLES["use_find_demon_talisman"].display_text == "使用寻妖符"


def test_native_auto_waits_for_item_capacity_recalculation_before_readback():
    runtime = FakeRuntime()
    runtime.count = 7398
    runtime.delayed_toggles["自动使用探查符"] = 4

    settings = _drain(
        prepare_beast_abyss_native_auto(
            runtime,
            BeastAbyssNativeAutoAssets(601, 603, ()),
            BeastAbyssNativeAutoRequest(
                False,
                measurement=False,
                requested_explores=10,
            ),
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
    assert runtime.clicks.count("自动探查次数_减少") == 0
    assert len(runtime.frame_drags) == 1


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
                BeastAbyssNativeAutoRequest(True),
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
                BeastAbyssNativeAutoRequest(True),
                poll_seconds=0,
            )
        )

    assert runtime.stage == "help_view"
    assert "开启自动" not in runtime.clicks


def test_native_auto_sets_and_reads_formal_measurement_settings():
    runtime = FakeRuntime()
    result = _drain(run_beast_abyss_native_auto(runtime, _assets(), BeastAbyssNativeAutoRequest(True), poll_seconds=0))

    assert result.terminal is BeastAbyssAutoTerminal.COMPLETED
    assert result.settings.fairy_events is False
    assert result.settings.player_events is True
    assert result.settings.auto_use_explore_items is True
    assert result.settings.stop_when_killed is False
    assert result.settings.fast_auto is True
    assert result.settings.skip_animation is True
    assert result.settings.use_find_demon_talisman is False
    assert result.settings.requested_explores == 100
    assert runtime.clicks[:2] == ["进入活动", "快捷处理"]
    assert runtime.clicks[-1] == "开启自动"


def test_native_auto_rejects_ocr_candidate_when_runtime_scene_disagrees():
    runtime = FakeRuntime(bad_explore_scene=True)
    with pytest.raises(RuntimeError, match="首次进入动画"):
        _drain(run_beast_abyss_native_auto(runtime, _assets(), BeastAbyssNativeAutoRequest(True), poll_seconds=0))
    assert "快捷处理" not in runtime.clicks


@pytest.mark.parametrize("root_title", ["自动探查", "快捷处理"])
def test_native_auto_clicks_exactly_one_visible_help_root(root_title):
    runtime = FakeRuntime(root_title=root_title)
    result = _drain(
        run_beast_abyss_native_auto(
            runtime,
            _assets(),
            BeastAbyssNativeAutoRequest(True),
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
    result = _drain(run_beast_abyss_native_auto(runtime, assets, BeastAbyssNativeAutoRequest(True), terminal_polls=1, poll_seconds=0))
    assert result.terminal is BeastAbyssAutoTerminal.UNKNOWN
    assert result.scene_id is None
