from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.core.fanxiu.data_annotation.tasks.yunmeng_native_auto import (
    TOGGLES,
    YUNMENG_NATIVE_AUTO_MAX_BATCH_CHALLENGES,
    YunmengAutoTerminal,
    YunmengNativeAutoAssets,
    YunmengNativeAutoRequest,
    _set_count,
    _validated_closing_goods_budget,
    classify_yunmeng_auto_terminal,
    plan_yunmeng_native_batch,
    run_yunmeng_native_auto,
)


class _Match:
    def __init__(self, matched: bool) -> None:
        self.matched = matched


class _Condition:
    def __init__(self, runtime, title: str) -> None:
        self.runtime = runtime
        self.title = title

    def check(self, _runtime, _frame):
        for asset in TOGGLES.values():
            if self.title == asset.selected:
                return _Match(self.runtime.toggles[asset.action])
            if self.title == asset.unselected:
                return _Match(not self.runtime.toggles[asset.action])
        return _Match(False)


class FakeRuntime:
    def __init__(
        self,
        *,
        terminal_text: str = "已完成预设的自动挑战次数",
        bad_home_scene: bool = False,
        home_text: str = "云梦试剑 兑换宝阁 榜单",
    ) -> None:
        self.stage = "home"
        self.terminal_text = terminal_text
        self.bad_home_scene = bad_home_scene
        self.home_text = home_text
        self.count = 3
        self.clicks: list[str] = []
        self.drags: list[str] = []
        self.drag_endpoints: list[tuple[str, str, str]] = []
        self.count_step = 1
        self.slider_offset = 0
        self.toggles = {asset.action: False for asset in TOGGLES.values()}
        self.disabled_toggles: set[str] = set()

    def current_scene(self, _views, update=False):
        rows = {
            "home": (999 if self.bad_home_scene else 601, self.home_text),
            "settings": (602, "自动挑战设置 开启自动 挑战次数"),
            "running": (603, self.terminal_text),
        }
        scene, text = rows[self.stage]
        return scene, 100.0, text

    def ocr_text(self, frame):
        return frame

    def cur_frame(self):
        return "frame"

    def shape_visible(self, _scene, title, *, threshold=80.0):
        assert threshold == 95.0
        return _Condition(self, title)

    def click_shape_center(self, _scene, title):
        self.clicks.append(title)
        if title == "自动挑战":
            self.stage = "settings"
        elif title == "开启自动":
            self.stage = "running"
        elif title == "挑战次数_增加":
            self.count += self.count_step
        elif title == "挑战次数_减少":
            self.count -= self.count_step
        else:
            if title not in self.disabled_toggles:
                self.toggles[title] = not self.toggles[title]

    def wait_action_settle(self, _seconds):
        if False:
            yield None

    def drag_shape_to_frame_edge(self, _scene, title, *, direction, duration):
        self.drags.append(f"{title}:{direction}")
        self.count = 5165 if direction == "right" else 1

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
        self.drag_endpoints.append((title, _left, _right))
        self.count = round(1 + float(fraction) * (5165 - 1)) + self.slider_offset

    def ocr_numbers_in_shapes(self, _scene, _shapes):
        return [self.count], str(self.count)


class AttenuatedSliderRuntime:
    """Integer slider whose thumb moves only part of the commanded distance."""

    def __init__(
        self,
        *,
        maximum: int = 1533,
        response_gain: float = 0.526,
        coarse_gain: float | None = None,
        strict_live_available: bool = True,
    ) -> None:
        self.maximum = maximum
        self.response_gain = response_gain
        self.coarse_gain = response_gain if coarse_gain is None else coarse_gain
        self.strict_live_available = strict_live_available
        self.minimum_x = 245.25
        self.maximum_x = 679.25
        self.left_anchor_x = 245.7
        self.right_anchor_x = 700.2
        self.thumb_width = 41.9
        self.thumb_x = self.minimum_x
        self.count = 1
        self.edge_drags: list[str] = []
        self.frame_drags: list[tuple[float, float]] = []
        self.clicks: list[str] = []

    def _sync_count(self) -> None:
        fraction = (self.thumb_x - self.minimum_x) / (
            self.maximum_x - self.minimum_x
        )
        self.count = round(1 + fraction * (self.maximum - 1))

    def _sync_thumb(self) -> None:
        fraction = (self.count - 1) / (self.maximum - 1)
        self.thumb_x = self.minimum_x + fraction * (
            self.maximum_x - self.minimum_x
        )

    def ocr_numbers_in_shapes(self, _scene, _shapes):
        return [self.count], str(self.count)

    def wait_action_settle(self, _seconds):
        if False:
            yield None

    def drag_shape_to_frame_edge(self, _scene, _title, *, direction, duration):
        self.edge_drags.append(direction)
        endpoint = self.maximum_x if direction == "right" else self.minimum_x
        movement = (endpoint - self.thumb_x) * 0.6
        self.thumb_x += movement
        if abs(endpoint - self.thumb_x) < 2.0:
            self.thumb_x = endpoint
        self._sync_count()

    def shape_center(
        self,
        _scene,
        title,
        *,
        live=False,
        strict_live=False,
    ):
        if title == "数量滑轨左端":
            assert not live
            return (self.left_anchor_x, 908.0)
        if title == "数量滑轨右端":
            assert not live
            return (self.right_anchor_x, 908.0)
        if strict_live and not self.strict_live_available:
            raise RuntimeError("浮动 shape 未唯一匹配实时中心")
        assert live is True
        return (self.thumb_x, 908.0)

    def shape_box(self, _scene, title):
        assert title == "挑战次数_滑块"
        return {"x": self.minimum_x, "y": 890.0, "w": self.thumb_width, "h": 36.0}

    def drag_frame_point(
        self,
        _scene,
        start_x,
        _start_y,
        end_x,
        _end_y,
        *,
        duration_ms,
    ):
        assert duration_ms in {450, 600}
        assert start_x == pytest.approx(self.thumb_x)
        self.frame_drags.append((start_x, end_x))
        gain = self.response_gain if duration_ms == 600 else self.coarse_gain
        self.thumb_x += (end_x - start_x) * gain
        self.thumb_x = min(self.maximum_x, max(self.minimum_x, self.thumb_x))
        self._sync_count()

    def click_shape_center(self, _scene, title):
        self.clicks.append(title)
        if title == "挑战次数_增加":
            self.count += 1
        elif title == "挑战次数_减少":
            self.count -= 1
        self._sync_thumb()


def _drain(generator):
    while True:
        try:
            next(generator)
        except StopIteration as stopped:
            return stopped.value


def _assets() -> YunmengNativeAutoAssets:
    return YunmengNativeAutoAssets(601, 602, (603,))


def _verified_slider_assets() -> YunmengNativeAutoAssets:
    return YunmengNativeAutoAssets(
        601,
        602,
        (603,),
        count_slider_left_anchor="数量滑轨左端",
        count_slider_right_anchor="数量滑轨右端",
    )


def test_native_auto_sets_explicit_safe_defaults_and_named_shapes():
    runtime = FakeRuntime()
    request = YunmengNativeAutoRequest(requested_challenges=5)

    result = _drain(
        run_yunmeng_native_auto(runtime, _assets(), request, poll_seconds=0)
    )

    assert result.terminal is YunmengAutoTerminal.COMPLETED
    assert result.settings.requested_challenges == 5
    assert result.settings.use_high_power_boost is True
    assert result.settings.use_score_boost is True
    assert result.settings.use_chase_sword is True
    assert result.settings.skip_battle is True
    assert result.settings.auto_refill_stamina is False
    assert result.settings.fast_auto is True
    assert result.settings.skip_animation is True
    assert runtime.clicks[0] == "自动挑战"
    assert runtime.clicks[-1] == "开启自动"
    assert all(isinstance(title, str) and title for title in runtime.clicks)


def test_consumable_boosts_are_best_effort_when_inventory_cannot_enable_them():
    runtime = FakeRuntime()
    runtime.disabled_toggles = {
        TOGGLES["use_high_power_boost"].action,
        TOGGLES["use_score_boost"].action,
        TOGGLES["use_chase_sword"].action,
    }

    result = _drain(
        run_yunmeng_native_auto(
            runtime,
            _assets(),
            YunmengNativeAutoRequest(requested_challenges=5),
            poll_seconds=0,
        )
    )

    assert result.terminal is YunmengAutoTerminal.COMPLETED
    assert result.settings.use_high_power_boost is False
    assert result.settings.use_score_boost is False
    assert result.settings.use_chase_sword is False
    assert result.settings.auto_refill_stamina is False
    assert result.settings.skip_battle is True
    assert result.settings.fast_auto is True
    assert result.settings.skip_animation is True
    assert runtime.clicks[-1] == "开启自动"


@pytest.mark.parametrize(
    "request_kwargs",
    [
        {"auto_refill_stamina": True},
        {"skip_battle": False},
        {"fast_auto": False},
        {"skip_animation": False},
    ],
)
def test_safety_critical_toggle_policy_cannot_be_overridden(request_kwargs):
    with pytest.raises(ValueError, match="云梦分批挑战必须"):
        YunmengNativeAutoRequest(requested_challenges=5, **request_kwargs)


def test_home_alignment_accepts_stable_suffix_when_title_prefix_is_occluded():
    runtime = FakeRuntime(home_text="试剑 兑换宝阁 榜单")

    result = _drain(
        run_yunmeng_native_auto(
            runtime,
            _assets(),
            YunmengNativeAutoRequest(requested_challenges=3),
            poll_seconds=0,
        )
    )

    assert result.terminal is YunmengAutoTerminal.COMPLETED


def test_native_auto_rejects_unbounded_long_batch_before_any_gui_action():
    runtime = FakeRuntime()

    with pytest.raises(ValueError, match="有界小批次"):
        request = YunmengNativeAutoRequest(
            requested_challenges=YUNMENG_NATIVE_AUTO_MAX_BATCH_CHALLENGES + 1,
        )
        _drain(run_yunmeng_native_auto(runtime, _assets(), request, poll_seconds=0))

    assert runtime.clicks == []


def test_batch_planner_uses_probe_then_geometric_half_batches():
    probe = plan_yunmeng_native_batch(required_new_currency=482_100)
    half = plan_yunmeng_native_batch(
        required_new_currency=62_000,
        measured_currency_delta=620,
        measured_challenges=10,
    )
    capped = plan_yunmeng_native_batch(
        required_new_currency=482_100,
        measured_currency_delta=620,
        measured_challenges=10,
    )
    final = plan_yunmeng_native_batch(
        required_new_currency=63,
        measured_currency_delta=620,
        measured_challenges=10,
        previous_currency_delta=620,
        previous_challenges=10,
    )

    assert (probe.requested_challenges, probe.planning_mode) == (10, "probe")
    assert (half.requested_challenges, half.planning_mode) == (500, "geometric_half")
    assert (capped.requested_challenges, capped.planning_mode) == (
        YUNMENG_NATIVE_AUTO_MAX_BATCH_CHALLENGES,
        "capped_geometric_half",
    )
    assert (final.requested_challenges, final.planning_mode) == (2, "stable_final")


def test_small_remainder_is_still_halved_until_yield_is_stable():
    plan = plan_yunmeng_native_batch(
        required_new_currency=620,
        measured_currency_delta=620,
        measured_challenges=10,
    )

    assert plan.requested_challenges == 5
    assert plan.planning_mode == "geometric_half"


def test_small_remainder_is_halved_when_adjacent_yields_are_not_stable():
    plan = plan_yunmeng_native_batch(
        required_new_currency=620,
        measured_currency_delta=620,
        measured_challenges=10,
        previous_currency_delta=1_240,
        previous_challenges=10,
    )

    assert plan.requested_challenges == 5
    assert plan.planning_mode == "geometric_half"


def test_odd_remainder_never_runs_more_than_half_before_stable_final():
    plan = plan_yunmeng_native_batch(
        required_new_currency=3,
        measured_currency_delta=10,
        measured_challenges=10,
    )

    assert plan.requested_challenges == 1
    assert plan.planning_mode == "geometric_half"


def test_probe_size_is_not_a_public_planner_override():
    with pytest.raises(TypeError, match="probe_challenges"):
        plan_yunmeng_native_batch(
            required_new_currency=100,
            probe_challenges=500,
        )


def test_batch_planner_hard_stops_when_absolute_gap_is_zero():
    plan = plan_yunmeng_native_batch(
        required_new_currency=0,
        measured_currency_delta=620,
        measured_challenges=10,
    )

    assert plan.requested_challenges == 0
    assert plan.planning_mode == "target_reached"


def test_large_remembered_count_resets_verified_slider_then_adjusts_exactly():
    runtime = FakeRuntime()
    runtime.count = 5165
    request = YunmengNativeAutoRequest(
        requested_challenges=5,
        max_count_adjustments=5,
    )

    result = _drain(run_yunmeng_native_auto(runtime, _assets(), request, poll_seconds=0))

    assert result.settings.requested_challenges == 5
    assert runtime.drags == [
        "挑战次数_滑块:right",
        "挑战次数_滑块:left",
        "挑战次数_滑块:fraction:0.000775",
    ]
    assert runtime.drag_endpoints == [
        ("挑战次数_滑块", "挑战次数_减少", "挑战次数_增加")
    ]
    assert runtime.clicks.count("挑战次数_增加") == 0


def test_slider_preset_still_fails_when_exact_correction_exceeds_budget():
    runtime = FakeRuntime()
    runtime.count = 5165
    runtime.slider_offset = 10
    request = YunmengNativeAutoRequest(requested_challenges=20, max_count_adjustments=5)

    with pytest.raises(RuntimeError, match="超出有界校正预算"):
        _drain(run_yunmeng_native_auto(runtime, _assets(), request, poll_seconds=0))

    assert runtime.drags == [
        "挑战次数_滑块:right",
        "挑战次数_滑块:left",
        "挑战次数_滑块:fraction:0.003679",
    ]
    assert "挑战次数_增加" not in runtime.clicks
    assert "开启自动" not in runtime.clicks


def test_verified_slider_compensates_attenuated_drag_before_bounded_fine_adjustment():
    runtime = AttenuatedSliderRuntime(maximum=1533, response_gain=0.526)

    # Exact production evidence: an uncompensated 245 -> 348 gesture only
    # moves the thumb to about 300 and yields about 193.
    runtime.drag_frame_point(602, 245.25, 908, 348.05, 908, duration_ms=450)
    assert runtime.thumb_x == pytest.approx(299.45, abs=0.2)
    assert runtime.count == pytest.approx(193, abs=1)
    runtime.thumb_x = runtime.minimum_x
    runtime.count = 1
    runtime.frame_drags.clear()

    result = _drain(
        _set_count(
            runtime,
            _verified_slider_assets(),
            363,
            max_adjustments=10,
            force_bound_probe=True,
            count_label="天眼符使用数量",
        )
    )

    assert result["maximum"] == 1533
    assert result["after"] == 363
    assert result["boundary_gains"]
    assert result["coarse_target_x"] == pytest.approx(348.2, abs=0.3)
    assert all(gain == pytest.approx(0.526, abs=0.01) for gain in result["boundary_gains"])
    assert result["coarse_refinement_count"] <= 1
    assert result["coarse_drag_count"] <= 2
    assert result["fine_adjustment_actions"] <= 10
    assert len(runtime.clicks) <= 10


def test_verified_slider_uses_observed_single_use_maximum_not_owned_inventory():
    runtime = AttenuatedSliderRuntime(maximum=800, response_gain=0.526)

    result = _drain(
        _set_count(
            runtime,
            _verified_slider_assets(),
            363,
            max_adjustments=10,
            force_bound_probe=True,
            count_label="天眼符使用数量",
        )
    )

    assert result["maximum"] == 800
    assert result["after"] == 363
    assert result["fine_adjustment_actions"] <= 10


def test_verified_slider_fails_closed_when_coarse_drag_has_no_live_thumb_response():
    runtime = AttenuatedSliderRuntime(maximum=1533, response_gain=0.01)

    with pytest.raises(RuntimeError, match="边界探针手势响应率异常"):
        _drain(
            _set_count(
                runtime,
                _verified_slider_assets(),
                363,
                max_adjustments=10,
                force_bound_probe=True,
                count_label="天眼符使用数量",
            )
        )

    assert runtime.clicks == []


def test_verified_slider_fails_closed_when_live_thumb_match_would_fallback():
    runtime = AttenuatedSliderRuntime(strict_live_available=False)

    with pytest.raises(RuntimeError, match="未唯一匹配实时中心"):
        _drain(
            _set_count(
                runtime,
                _verified_slider_assets(),
                363,
                max_adjustments=10,
                force_bound_probe=True,
                count_label="天眼符使用数量",
            )
        )

    assert runtime.frame_drags == []
    assert runtime.clicks == []


def test_verified_slider_allows_only_one_feedback_coarse_drag():
    runtime = AttenuatedSliderRuntime(
        maximum=1533,
        response_gain=0.526,
        coarse_gain=0.25,
    )

    result = _drain(
        _set_count(
            runtime,
            _verified_slider_assets(),
            363,
            max_adjustments=10,
            force_bound_probe=True,
            count_label="天眼符使用数量",
        )
    )

    assert result["after"] == 363
    assert result["coarse_refinement_count"] == 1
    assert result["coarse_drag_count"] == 2
    assert result["fine_adjustment_actions"] <= 10


def test_native_ten_step_increment_is_accepted_when_it_hits_exact_target():
    runtime = FakeRuntime()
    runtime.count = 1
    runtime.count_step = 10

    result = _drain(
        run_yunmeng_native_auto(
            runtime,
            _assets(),
            YunmengNativeAutoRequest(requested_challenges=11),
            poll_seconds=0,
        )
    )

    assert result.settings.requested_challenges == 11
    assert runtime.clicks.count("挑战次数_增加") == 1


def test_safety_toggle_must_reach_its_authoritative_reference_or_roll_back():
    runtime = FakeRuntime()

    class _MissingCondition:
        def check(self, _runtime, _frame):
            return _Match(False)

    runtime.shape_visible = (
        lambda _scene, _title, *, threshold=80.0: _MissingCondition()
    )

    with pytest.raises(RuntimeError, match="无法验证，已回滚"):
        _drain(
            run_yunmeng_native_auto(
                runtime,
                _assets(),
                YunmengNativeAutoRequest(requested_challenges=3),
                poll_seconds=0,
            )
        )
    assert runtime.clicks.count(TOGGLES["skip_battle"].action) == 2
    assert "开启自动" not in runtime.clicks


def test_runtime_scene_must_authorize_open_settings_action():
    runtime = FakeRuntime(bad_home_scene=True)

    with pytest.raises(RuntimeError, match="Runtime-GUI 对齐失败"):
        _drain(
            run_yunmeng_native_auto(
                runtime,
                _assets(),
                YunmengNativeAutoRequest(requested_challenges=3),
                poll_seconds=0,
            )
        )
    assert runtime.clicks == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("已完成预设的自动挑战次数", YunmengAutoTerminal.COMPLETED),
        ("活动结束前30分钟停止使用！", YunmengAutoTerminal.ACTIVITY_CLOSING),
        ("活动即将结束，无法使用自动挑战", YunmengAutoTerminal.ACTIVITY_CLOSING),
        ("挑战体力和云梦·论剑令不足", YunmengAutoTerminal.RESOURCE_EXHAUSTED),
        ("自动挑战中", YunmengAutoTerminal.UNKNOWN),
    ],
)
def test_terminal_classification(text, expected):
    assert classify_yunmeng_auto_terminal(text) is expected


def test_terminal_requires_verified_runtime_scene():
    runtime = FakeRuntime()
    assets = YunmengNativeAutoAssets(601, 602, (604,))

    result = _drain(
        run_yunmeng_native_auto(
            runtime,
            assets,
            YunmengNativeAutoRequest(requested_challenges=3),
            terminal_polls=1,
            poll_seconds=0,
        )
    )

    assert result.terminal is YunmengAutoTerminal.UNKNOWN
    assert result.scene_id is None


def test_locked_settings_are_reused_without_retoggling_between_batches():
    runtime = FakeRuntime()
    first = _drain(
        run_yunmeng_native_auto(
            runtime,
            _assets(),
            YunmengNativeAutoRequest(requested_challenges=3),
            poll_seconds=0,
        )
    )
    runtime.stage = "home"
    runtime.count = 3
    runtime.clicks.clear()

    second = _drain(
        run_yunmeng_native_auto(
            runtime,
            _assets(),
            YunmengNativeAutoRequest(requested_challenges=2),
            locked_settings=first.settings,
            poll_seconds=0,
        )
    )

    assert second.settings.requested_challenges == 2
    assert not any(asset.action in runtime.clicks for asset in TOGGLES.values())
    assert runtime.clicks[0] == "自动挑战"
    assert runtime.clicks[-1] == "开启自动"


def test_later_batch_repairs_required_safety_but_never_reenables_optional_boosts():
    runtime = FakeRuntime()
    first = _drain(
        run_yunmeng_native_auto(
            runtime,
            _assets(),
            YunmengNativeAutoRequest(requested_challenges=3),
            poll_seconds=0,
        )
    )
    runtime.stage = "home"
    runtime.count = 3
    runtime.clicks.clear()
    runtime.toggles[TOGGLES["use_high_power_boost"].action] = False
    runtime.toggles[TOGGLES["use_score_boost"].action] = False
    runtime.toggles[TOGGLES["use_chase_sword"].action] = False
    runtime.toggles[TOGGLES["skip_battle"].action] = False
    runtime.toggles[TOGGLES["auto_refill_stamina"].action] = True

    second = _drain(
        run_yunmeng_native_auto(
            runtime,
            _assets(),
            YunmengNativeAutoRequest(requested_challenges=2),
            locked_settings=first.settings,
            poll_seconds=0,
        )
    )

    optional_actions = {
        TOGGLES[name].action
        for name in (
            "use_high_power_boost",
            "use_score_boost",
            "use_chase_sword",
        )
    }
    assert optional_actions.isdisjoint(runtime.clicks)
    assert runtime.clicks.count(TOGGLES["skip_battle"].action) == 1
    assert runtime.clicks.count(TOGGLES["auto_refill_stamina"].action) == 1
    assert second.settings.use_high_power_boost is False
    assert second.settings.use_score_boost is False
    assert second.settings.use_chase_sword is False
    assert second.settings.skip_battle is True
    assert second.settings.auto_refill_stamina is False


def _activity_detail(**overrides):
    values = {
        "id": "activity-1",
        "activity_type": "yunmeng-trial",
        "is_active": True,
        "budget_ready": True,
        "budget_block_reason": "",
        "exchange_plan": {
            "budget_ready": True,
            "budget_block_reason": "",
            "target_budgets": {
                "收尾道具": {
                    "target_total_tokens": 480_000,
                    "target_remaining_tokens": 80_000,
                },
            },
        },
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_closing_goods_budget_accepts_collectors_dict_plan_as_latest_authority():
    assert _validated_closing_goods_budget(
        _activity_detail(),
        expected_activity_id="activity-1",
        context="批后",
    ) == (480_000, 80_000)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"id": "activity-2"}, "活动实例发生切换"),
        ({"activity_type": "beast-abyss"}, "活动类型"),
        ({"is_active": False}, "活动已不在有效期"),
        ({"budget_ready": False, "budget_block_reason": "钱包过期"}, "钱包过期"),
        (
            {
                "exchange_plan": {
                    "budget_ready": False,
                    "budget_block_reason": "商店过期",
                }
            },
            "商店过期",
        ),
        (
            {
                "exchange_plan": {
                    "budget_ready": True,
                    "target_budgets": {"收尾道具": {"target_total_tokens": 0}},
                }
            },
            "目标金额",
        ),
    ],
)
def test_closing_goods_budget_fails_closed_when_latest_collect_result_is_unsafe(
    overrides,
    message,
):
    with pytest.raises(RuntimeError, match=message):
        _validated_closing_goods_budget(
            _activity_detail(**overrides),
            expected_activity_id="activity-1",
            context="批后",
        )
