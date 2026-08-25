from __future__ import annotations

import inspect
from datetime import datetime

import pytest

from backend.core.fanxiu.data_annotation.tasks import beast_spirit_update
from backend.core.fanxiu.data_annotation.default_jobs import (
    register_fanxiu_data_annotation_default_runtime_jobs,
)
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
)
from backend.core.fanxiu.behavior_tree.runtime import create_behavior_tree_runtime_runner
from backend.core.fanxiu.data_annotation.tasks.beast_spirit_update import (
    BeastSoulTargetNotFoundError,
    QuickSynthesisPolicy,
    _bag_card_position,
    _coarse_scroll_batch_size,
    _beast_soul_main_identity,
    _detail_identity,
    _enter_beast_soul_main,
    _expected_identity,
    _execute_current_batch,
    _leave_quick_synthesis,
    _first_bag_card_probe_points,
    _initial_bag_card_probe_points,
    _open_initial_bag_card,
    _open_initial_first_bag_card,
    _open_bag_item_detail,
    _select_material,
    _sync_protected_items,
    _verify_synthesis_snapshot_delta,
    consumable_count,
    next_beast_spirit_update_at,
    quick_synthesis_policy,
    synthesis_batch_size,
    synthesis_gate,
)


class _EntryRuntime:
    def __init__(self) -> None:
        self.stage = "world"
        self.calls: list[tuple[object, ...]] = []

    def current_scene(self, *, views: list[int], update: bool):
        self.calls.append(("current_scene", tuple(views), update))
        if self.stage == "soul":
            return 478, 100, None
        return None, 0, None

    def go_scene(self, scene: int):
        self.calls.append(("go_scene", scene))
        yield None

    def wait_click(self, view: int, shape: str):
        self.calls.append(("shape", view, shape))
        if (view, shape) == (483, "兽魂页签"):
            self.stage = "soul"
        yield None

    def wait_scene(self, scene: int, **kwargs):
        self.calls.append(("wait_scene", scene, kwargs))
        yield None


class _Box:
    def __init__(self, x: float, y: float, w: float, h: float) -> None:
        self.value = {"x": x, "y": y, "w": w, "h": h}

    def box(self) -> dict[str, float]:
        return self.value


class _GeometryRuntime:
    boxes = {
        "魂晶背包/魂晶卡片模板": _Box(120, 890, 140, 128),
        "魂晶背包/首行第五格": _Box(700, 890, 140, 128),
        "魂晶背包/第二行第一格": _Box(120, 1030, 140, 128),
    }

    def shape(self, scene: int, selector: str) -> _Box:
        assert scene == 478
        return self.boxes[selector]


class _InitialCardProbeRuntime(_GeometryRuntime):
    boxes = {
        **_GeometryRuntime.boxes,
        "魂晶背包": _Box(85, 870, 770, 350),
    }

    def __init__(self, *, opens_on_attempt: int) -> None:
        self.opens_on_attempt = opens_on_attempt
        self.clicks: list[tuple[int, str, float, float]] = []
        self.attempt = 0
        self.scene = 478

    def click_shape_center(
        self,
        scene: int,
        selector: str,
        *,
        x_ratio: float,
        y_ratio: float,
    ) -> None:
        self.attempt += 1
        self.clicks.append((scene, selector, x_ratio, y_ratio))
        if self.attempt == self.opens_on_attempt:
            self.scene = 479

    def wait_scene(self, scene: int, **_kwargs):
        if self.scene != scene:
            raise TimeoutError("not opened")
        yield None

    def current_scene(self, *, views: list[int], update: bool):
        assert views == [478, 479]
        assert update is True
        return self.scene, 100, None

    observe_scene = current_scene


def _item(
    item_id: str,
    level: int,
    *,
    equipped: bool = False,
    locked: bool = False,
    excluded: bool = False,
) -> dict[str, object]:
    return {
        "item_id": item_id,
        "level": level,
        "equipped": equipped,
        "locked": locked,
        "excluded_from_quick_synthesis": excluded,
    }


def test_beast_spirit_update_is_weekly_standard_job() -> None:
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition(
        "beast_spirit_update"
    )

    assert definition is not None
    assert definition.label == "兽魂更新"
    assert definition.scheduler_supported is True
    assert definition.standard_job is True
    assert definition.standard_job_id == "beast-spirit-update"
    assert definition.standard_job_description == "每周"
    assert definition.standard_job_payload == {"max_source_level": 8}


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 8, 9, 12, 0), datetime(2026, 8, 11, 0, 30)),
        (datetime(2026, 8, 11, 0, 29), datetime(2026, 8, 11, 0, 30)),
        (datetime(2026, 8, 11, 0, 30), datetime(2026, 8, 18, 0, 30)),
        (datetime(2026, 8, 11, 3, 0), datetime(2026, 8, 18, 0, 30)),
    ],
)
def test_next_beast_spirit_update_at(now: datetime, expected: datetime) -> None:
    assert next_beast_spirit_update_at(now) == expected


def test_initial_first_bag_card_probe_points_use_quarter_and_half_height() -> None:
    assert _first_bag_card_probe_points(_GeometryRuntime()) == (
        (190.0, 922.0),
        (190.0, 954.0),
    )


def test_initial_bag_card_probe_points_derive_columns_and_second_row() -> None:
    runtime = _GeometryRuntime()

    assert _initial_bag_card_probe_points(runtime, row=0, column=1) == (
        (335.0, 922.0),
        (335.0, 954.0),
    )
    assert _initial_bag_card_probe_points(runtime, row=1, column=4) == (
        (770.0, 1062.0),
        (770.0, 1094.0),
    )


def test_partial_third_row_uses_only_quarter_height_inside_bag() -> None:
    runtime = _InitialCardProbeRuntime(opens_on_attempt=1)

    assert _initial_bag_card_probe_points(runtime, row=2, column=1) == (
        (335.0, 1202.0),
    )


def test_partial_third_row_refuses_point_outside_bag() -> None:
    class Runtime(_InitialCardProbeRuntime):
        boxes = {
            **_InitialCardProbeRuntime.boxes,
            "魂晶背包": _Box(85, 870, 770, 320),
        }

    with pytest.raises(RuntimeError, match="h/4 探测点不在魂晶背包容器内"):
        _initial_bag_card_probe_points(Runtime(opens_on_attempt=1), row=2, column=1)


@pytest.mark.parametrize(("opens_on_attempt", "expected_clicks"), [(1, 1), (2, 2)])
def test_open_initial_first_bag_card_uses_second_probe_only_after_no_op(
    opens_on_attempt: int,
    expected_clicks: int,
) -> None:
    runtime = _InitialCardProbeRuntime(opens_on_attempt=opens_on_attempt)

    result = _generator_result(_open_initial_first_bag_card(runtime))

    assert result["attempt"] == opens_on_attempt
    assert len(runtime.clicks) == expected_clicks


def test_open_initial_second_bag_card_uses_derived_column() -> None:
    runtime = _InitialCardProbeRuntime(opens_on_attempt=1)

    result = _generator_result(
        _open_initial_bag_card(runtime, row=0, column=1)
    )

    assert result == {"attempt": 1, "point": (335.0, 922.0)}
    _scene, _selector, x_ratio, _y_ratio = runtime.clicks[0]
    assert x_ratio == pytest.approx((335 - 85) / 770)


def test_open_initial_first_bag_card_stops_if_first_probe_leaves_known_scenes() -> None:
    runtime = _InitialCardProbeRuntime(opens_on_attempt=99)
    runtime.observe_scene = lambda **_kwargs: (None, 0, None)  # type: ignore[method-assign]

    with pytest.raises(TimeoutError, match="not opened"):
        _generator_result(_open_initial_first_bag_card(runtime))

    assert len(runtime.clicks) == 1


def test_entry_uses_standard_dynamic_world_menu_navigation(monkeypatch) -> None:
    runtime = _EntryRuntime()

    def open_menu(target_runtime, target, *, expected_scene_ids, timeout_seconds):
        assert target_runtime is runtime
        runtime.calls.append(
            ("world_menu", target, tuple(expected_scene_ids), timeout_seconds)
        )
        yield None

    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.world_menu_navigation.open_world_menu_function",
        open_menu,
    )

    list(_enter_beast_soul_main(runtime))

    assert runtime.calls == [
        ("current_scene", (478,), True),
        ("world_menu", 4000, (483,), 20),
        ("shape", 483, "兽魂页签"),
        (
            "wait_scene",
            478,
            {"timeout": 12, "label": "兽魂更新：等待兽魂主页"},
        ),
    ]


def test_entry_is_noop_when_beast_soul_main_is_already_visible() -> None:
    runtime = _EntryRuntime()
    runtime.stage = "soul"

    list(_enter_beast_soul_main(runtime))

    assert runtime.calls == [("current_scene", (478,), True)]


def test_batch_size_uses_verified_low_and_high_level_rules() -> None:
    assert [synthesis_batch_size(level) for level in range(1, 9)] == [
        2,
        2,
        2,
        3,
        3,
        3,
        3,
        3,
    ]


def test_quick_synthesis_policy_is_a_complete_stable_business_contract() -> None:
    low = quick_synthesis_policy(1)
    high = quick_synthesis_policy(4)

    assert low == QuickSynthesisPolicy(
        level=1,
        batch_size=2,
        success_probability=0.55,
        auto_confirm_low_success=True,
        requires_precious_material_confirmation=False,
        confirmation_text=("当前成功率较低", "是否确认进行合成"),
        expected_material_cost=pytest.approx(3.6363636363636362),
    )
    assert high == QuickSynthesisPolicy(
        level=4,
        batch_size=3,
        success_probability=1.0,
        auto_confirm_low_success=False,
        requires_precious_material_confirmation=False,
        confirmation_text=None,
        expected_material_cost=3.0,
    )
    assert synthesis_batch_size(1) == low.batch_size
    assert synthesis_batch_size(4) == high.batch_size
    assert quick_synthesis_policy(5).requires_precious_material_confirmation is False
    assert quick_synthesis_policy(6).requires_precious_material_confirmation is True


def test_synthesis_executor_consumes_policy_api_instead_of_rederiving_rules() -> None:
    source = inspect.getsource(_execute_current_batch)

    assert "policy = quick_synthesis_policy(level)" in source
    assert "level <=" not in source
    assert "0.55" not in source


def test_bag_card_position_is_derived_from_asset_tree_grid_anchors() -> None:
    runtime = _GeometryRuntime()

    assert _bag_card_position(runtime, 0) == (190, 954, 0)
    assert _bag_card_position(runtime, 9) == (770, 1094, 0)


def test_bag_card_position_fails_closed_beyond_verified_live_rows() -> None:
    with pytest.raises(RuntimeError, match="第 3 行尚无真实资产页验证"):
        _bag_card_position(_GeometryRuntime(), 10)


class _DetailRuntime:
    def __init__(self, text: str, basic_text: str = "") -> None:
        self.text = text
        self.basic_text = basic_text

    def ocr_text_in_shapes(self, scene: int, shapes: tuple[str, ...], **kwargs):
        assert scene == 479
        if shapes == ("魂晶等级标题", "总评分"):
            return self.text
        assert shapes == ("基础属性",)
        assert kwargs["padding"] == 96
        return self.basic_text


def test_detail_identity_prefers_longest_level_label_and_parses_score() -> None:
    identity, text = _detail_identity(
        _DetailRuntime(
            "神品一星魂晶\n总评分：435,400",
            "基础属性 魂元:1280 气血加成:1.36%",
        )
    )

    assert identity == (6, 435400, 1280, 136)
    assert text == "神品一星魂晶总评分：435,400基础属性魂元:1280气血加成:1.36%"


def test_beast_soul_main_identity_requires_three_bounded_formal_anchors() -> None:
    class Runtime:
        def __init__(self, texts: dict[str, str]) -> None:
            self.texts = texts
            self.calls: list[tuple] = []

        def ocr_text_in_shapes(self, scene, shapes, **kwargs):
            self.calls.append((scene, shapes, kwargs))
            return self.texts[shapes[0]]

    runtime = Runtime({"魂胎光": "魂胎光（29/30）", "合成魂晶": "合成魂晶", "词条预览": "词条预览"})
    assert _beast_soul_main_identity(runtime, frame_data_url="stable-frame") is True
    assert runtime.calls == [
        (478, ("魂胎光",), {"padding": 12, "frame_data_url": "stable-frame"}),
        (478, ("合成魂晶",), {"padding": 12, "frame_data_url": "stable-frame"}),
        (478, ("词条预览",), {"padding": 12, "frame_data_url": "stable-frame"}),
    ]
    assert _beast_soul_main_identity(
        Runtime({"魂胎光": "魂胎光（29/30）", "合成魂晶": "合成魂晶", "词条预览": ""})
    ) is False


def test_expected_identity_requires_unique_level_and_score() -> None:
    unique = {"items": [{"item_id": "a", "level": 2, "score": 4150}]}
    duplicate = {
        "items": [
            {"item_id": "a", "level": 2, "score": 4150},
            {"item_id": "b", "level": 2, "score": 4150},
        ]
    }

    assert _expected_identity(unique, "a") == (2, 4150, None, None)
    with pytest.raises(RuntimeError, match="指纹不唯一"):
        _expected_identity(duplicate, "a")


class _BagSearchRuntime(_GeometryRuntime):
    boxes = {
        **_GeometryRuntime.boxes,
        "魂晶背包": _Box(100, 850, 800, 360),
    }

    def __init__(self) -> None:
        self.viewport = 0
        self.visible_row = 0
        self.visible_column = 0
        self.stage = 478
        self.closed_count = 0

    def scroll_shape_content(self, scene: int, shape: str, *, direction: str, **kwargs):
        assert (scene, shape) == (478, "魂晶背包")
        if direction == "up":
            changed = self.viewport != 0
            self.viewport = 0
        else:
            changed = self.viewport == 0
            self.viewport = 1
        yield None
        return changed

    def observe_scene(self, *, views: list[int], update: bool):
        return self.current_scene(views=views, update=update)

    def click_shape_center(self, scene: int, shape: str, *, x_ratio: float, y_ratio: float):
        assert (scene, shape) == (478, "魂晶背包")
        self.visible_row = 0 if y_ratio < 0.4 else (1 if y_ratio < 0.8 else 2)
        column_ratios = (0.1125, 0.29375, 0.475, 0.65625, 0.8375)
        self.visible_column = min(
            range(5),
            key=lambda column: abs(column_ratios[column] - x_ratio),
        )
        self.stage = 479

    def wait_scene(self, scene: int, **kwargs):
        assert self.stage == scene
        yield None

    def ocr_text_in_shapes(self, scene: int, shapes: tuple[str, ...], **kwargs):
        if (self.viewport, self.visible_row, self.visible_column) == (1, 1, 0):
            return "二级魂晶 总评分：4150"
        return f"神品一星魂晶 总评分：{100 + self.visible_column}"

    def wait_click(self, scene: int, shape: str):
        assert (scene, shape) == (479, "关闭详情")
        self.stage = 478
        self.closed_count += 1
        yield None


def _generator_result(generator):
    while True:
        try:
            next(generator)
        except StopIteration as exc:
            return exc.value


class _SequenceAnchorRuntime(_BagSearchRuntime):
    def __init__(self, signatures, *, viewport_start=0, allow_scroll=True) -> None:
        super().__init__()
        self.signatures = list(signatures)
        self.viewport_start = int(viewport_start)
        self.allow_scroll = allow_scroll
        self.scrolls: list[str] = []
        self.scroll_kwargs: list[dict] = []
        self.opened_indices: list[int] = []

    def click_shape_center(self, scene: int, shape: str, *, x_ratio: float, y_ratio: float):
        super().click_shape_center(
            scene, shape, x_ratio=x_ratio, y_ratio=y_ratio
        )
        index = self.viewport_start + self.visible_row * 5 + self.visible_column
        signature = self.signatures[index] if index < len(self.signatures) else None
        if signature is None:
            self.stage = 478
            return
        self.opened_indices.append(index)

    def current_scene(self, *, views: list[int], update: bool):
        return self.stage, 100, None

    def wait_scene(self, scene: int, **kwargs):
        if self.stage != scene:
            raise RuntimeError("not opened")
        yield None

    def ocr_text_in_shapes(self, scene: int, shapes: tuple[str, ...], **kwargs):
        index = self.viewport_start + self.visible_row * 5 + self.visible_column
        signature = self.signatures[index]
        level, score = signature[:2]
        if shapes == ("基础属性",):
            soul, blood = signature[2] if len(signature) > 2 else (1000, 80)
            return " ".join(
                value
                for value in (
                    f"魂元:{soul}" if soul is not None else "",
                    f"气血加成:{blood / 100:.2f}%" if blood is not None else "",
                )
                if value
            )
        return f"{beast_spirit_update.LEVEL_LABELS[level]}魂晶 总评分：{score}"

    def scroll_shape_content(self, scene: int, shape: str, *, direction: str, **kwargs):
        self.scrolls.append(direction)
        self.scroll_kwargs.append(dict(kwargs))
        if not self.allow_scroll:
            yield None
            return False
        old = self.viewport_start
        if direction == "down":
            last_start = max(0, ((len(self.signatures) - 1) // 5 - 1) * 5)
            self.viewport_start = min(last_start, self.viewport_start + 5)
        else:
            self.viewport_start = max(0, self.viewport_start - 5)
        yield None
        return self.viewport_start != old


def _sequence_snapshot(signatures, *, target_index: int):
    ids = [f"item-{index}" for index in range(len(signatures))]
    items = [
        {
            "item_id": item_id,
            "level": signature[0],
            "score": signature[1],
            "main_entries": (
                [
                    {"attribute_id": beast_spirit_update.BEAST_SOUL_SOUL_ATTR_ID, "value": (signature[2] if len(signature) > 2 else (1000, 80))[0]},
                    {"attribute_id": beast_spirit_update.BEAST_SOUL_BLOOD_RATE_ATTR_ID, "value": (signature[2] if len(signature) > 2 else (1000, 80))[1]},
                ]
            ),
            "vice_entries": [],
            "ui_bag_index": index,
        }
        for index, (item_id, signature) in enumerate(zip(ids, signatures))
    ]
    return {
        "items": items,
        "ui_bag_complete": True,
        "ui_bag_item_ids": ids,
    }, ids[target_index]


def test_viewport_registration_tolerates_one_component_digit_error():
    identities = [
        (2, 4150, 1280, 136),
        *((3, 7000, 1000, 80),) * 4,
        (2, 4150, 880, 66),
        *((3, 7000, 1000, 80),) * 4,
    ]

    winner, ranked = beast_spirit_update._rank_viewport_candidates(
        identities,
        [(0, (2, 4150, 1281, 136))],
    )

    assert winner == 0
    assert ranked[0][0] - ranked[1][0] >= beast_spirit_update._SIGNATURE_SAFE_MARGIN


def test_viewport_registration_rejects_multiple_component_conflicts():
    identities = [(2, 4150, 1280, 136)] * 5

    winner, _ranked = beast_spirit_update._rank_viewport_candidates(
        identities,
        [(0, (6, 9999, 7777, 999))],
    )

    assert winner is None


def test_viewport_registration_requires_best_runner_up_safe_margin():
    identities = [
        (2, 4150, None, None),
        *((3, 7000, None, None),) * 4,
        (2, 4151, None, None),
        *((3, 7000, None, None),) * 4,
    ]

    winner, ranked = beast_spirit_update._rank_viewport_candidates(
        identities,
        [(0, (2, 4150, None, None))],
    )

    assert winner is None
    assert ranked[0][0] - ranked[1][0] == 3


def test_viewport_registration_uses_basic_attributes_to_break_zero_score_tie():
    identities = [
        (1, 0, 960, 78),
        *((1, 0, 1000, 80),) * 4,
        (1, 0, 880, 66),
        *((1, 0, 1000, 80),) * 4,
    ]

    winner, _ranked = beast_spirit_update._rank_viewport_candidates(
        identities,
        [(0, (1, 0, 960, 78))],
    )

    assert winner == 0


def test_target_signature_rejects_missing_basic_attributes():
    expected = (1, 0, 980, 65)

    assert beast_spirit_update._target_signature_accepts(
        expected, (1, 0, None, None)
    ) is False


def test_target_signature_rejects_one_severe_basic_attribute_conflict():
    expected = (1, 0, 980, 65)

    assert beast_spirit_update._target_signature_accepts(
        expected, (1, 0, 980, 999)
    ) is False
    assert beast_spirit_update._target_signature_accepts(
        expected, (1, 0, 981, 65)
    ) is True


def test_sequence_anchor_opens_target_from_verified_initial_viewport(monkeypatch) -> None:
    signatures = [(1 + index % 6, 1000 + index) for index in range(12)]
    snapshot, target_id = _sequence_snapshot(signatures, target_index=1)
    runtime = _SequenceAnchorRuntime(signatures)
    verifications = []
    monkeypatch.setattr(
        beast_spirit_update,
        "_verify_ui_bag_order",
        lambda ids: verifications.append(list(ids)),
    )

    result = _generator_result(_open_bag_item_detail(runtime, snapshot, target_id))

    assert result["item_id"] == target_id
    assert runtime.stage == 479
    assert runtime.opened_indices == [0, 1, 1]
    assert runtime.closed_count == 2
    assert runtime.scrolls == []
    assert verifications == [snapshot["ui_bag_item_ids"]] * 3


def test_sequence_anchor_rejects_changed_carrier_detail_before_lock_action(
    monkeypatch,
) -> None:
    class Runtime(_SequenceAnchorRuntime):
        def ocr_text_in_shapes(self, scene: int, shapes: tuple[str, ...], **kwargs):
            index = self.viewport_start + self.visible_row * 5 + self.visible_column
            if index == 1 and self.opened_indices.count(1) == 2:
                return "三级魂晶 总评分：999999"
            return super().ocr_text_in_shapes(scene, shapes, **kwargs)

    signatures = [(1 + index % 6, 1000 + index) for index in range(12)]
    snapshot, target_id = _sequence_snapshot(signatures, target_index=1)
    runtime = Runtime(signatures)
    verifications = []
    monkeypatch.setattr(
        beast_spirit_update,
        "_verify_ui_bag_order",
        lambda ids: verifications.append(list(ids)),
    )

    with pytest.raises(BeastSoulTargetNotFoundError, match="承载锁动作"):
        _generator_result(_open_bag_item_detail(runtime, snapshot, target_id))

    assert runtime.opened_indices == [0, 1, 1]
    assert runtime.closed_count == 3
    assert runtime.stage == 478
    assert verifications == [snapshot["ui_bag_item_ids"]] * 2


def test_sequence_anchor_reanchors_after_verified_default_scroll(monkeypatch) -> None:
    signatures = [(1 + index % 6, 2000 + index) for index in range(21)]
    snapshot, target_id = _sequence_snapshot(signatures, target_index=15)
    runtime = _SequenceAnchorRuntime(signatures)
    monkeypatch.setattr(beast_spirit_update, "_verify_ui_bag_order", lambda _ids: None)

    result = _generator_result(_open_bag_item_detail(runtime, snapshot, target_id))

    assert result["item_id"] == target_id
    assert runtime.scrolls == ["down"]
    assert runtime.scroll_kwargs == [{"recognition_shape": "魂晶背包/魂晶卡片模板"}]
    assert runtime.opened_indices == [0, 5, 15, 15]
    assert runtime.closed_count == 3
    assert runtime.stage == 479


def test_sequence_anchor_opens_partial_third_row_target_at_bottom_boundary(
    monkeypatch,
) -> None:
    signatures = [
        (1 + index % 6, 5000 + index, (1000 + index, 80 + index))
        for index in range(125)
    ]
    snapshot, target_id = _sequence_snapshot(signatures, target_index=121)
    runtime = _SequenceAnchorRuntime(
        signatures,
        viewport_start=110,
        allow_scroll=False,
    )
    monkeypatch.setattr(beast_spirit_update, "_verify_ui_bag_order", lambda _ids: None)

    result = _generator_result(_open_bag_item_detail(runtime, snapshot, target_id))

    assert result["item_id"] == target_id
    assert runtime.scrolls == []
    assert runtime.opened_indices == [110, 121, 121]
    assert runtime.closed_count == 2
    assert runtime.stage == 479


def test_coarse_scroll_batch_uses_conservative_seventy_five_percent_bound() -> None:
    assert _coarse_scroll_batch_size(0, 121, remaining_scrolls=99) == 9
    assert _coarse_scroll_batch_size(30, 92, remaining_scrolls=99) == 4
    assert _coarse_scroll_batch_size(75, 92, remaining_scrolls=99) == 1
    assert _coarse_scroll_batch_size(0, 121, remaining_scrolls=3) == 3


def test_coarse_registration_batches_far_scrolls_without_detail_probes(
    monkeypatch,
) -> None:
    signatures = [
        (1 + index % 6, 6000 + index, (1100 + index, 90 + index))
        for index in range(100)
    ]
    snapshot, target_id = _sequence_snapshot(signatures, target_index=92)
    runtime = _SequenceAnchorRuntime(signatures)
    monkeypatch.setattr(beast_spirit_update, "_verify_ui_bag_order", lambda _ids: None)

    result = _generator_result(_open_bag_item_detail(runtime, snapshot, target_id))

    assert result["item_id"] == target_id
    assert runtime.opened_indices == [0, 30, 50, 65, 75, 80, 92, 92]
    assert runtime.scrolls == ["down"] * 16
    assert runtime.stage == 479


def test_coarse_registration_rejects_inertial_overshoot(monkeypatch) -> None:
    class Runtime(_SequenceAnchorRuntime):
        def scroll_shape_content(self, scene: int, shape: str, *, direction: str, **kwargs):
            self.scrolls.append(direction)
            self.scroll_kwargs.append(dict(kwargs))
            old = self.viewport_start
            self.viewport_start = min(120, self.viewport_start + 100)
            yield None
            return self.viewport_start != old

    signatures = [
        (1 + index % 6, 7000 + index, (1200 + index, 100 + index))
        for index in range(125)
    ]
    snapshot, target_id = _sequence_snapshot(signatures, target_index=92)
    runtime = Runtime(signatures)
    monkeypatch.setattr(beast_spirit_update, "_verify_ui_bag_order", lambda _ids: None)

    with pytest.raises(BeastSoulTargetNotFoundError, match="粗滚重锚发现越过目标"):
        _generator_result(_open_bag_item_detail(runtime, snapshot, target_id))

    assert runtime.stage == 478
    assert runtime.opened_indices == [0, 120]


def test_coarse_registration_reanchors_variable_inertial_progress(monkeypatch) -> None:
    class Runtime(_SequenceAnchorRuntime):
        advances = iter((10, 5, 10, 5, 10, 5, 10, 5, 5, 5, 5, 5))

        def scroll_shape_content(self, scene: int, shape: str, *, direction: str, **kwargs):
            self.scrolls.append(direction)
            self.scroll_kwargs.append(dict(kwargs))
            old = self.viewport_start
            advance = next(self.advances, 5)
            self.viewport_start = min(85, self.viewport_start + advance)
            yield None
            return self.viewport_start != old

    signatures = [
        (1 + index % 6, 8000 + index, (1300 + index, 110 + index))
        for index in range(100)
    ]
    snapshot, target_id = _sequence_snapshot(signatures, target_index=92)
    runtime = Runtime(signatures)
    monkeypatch.setattr(beast_spirit_update, "_verify_ui_bag_order", lambda _ids: None)

    result = _generator_result(_open_bag_item_detail(runtime, snapshot, target_id))

    assert result["item_id"] == target_id
    assert runtime.stage == 479
    assert all(direction == "down" for direction in runtime.scrolls)


def test_coarse_registration_reanchors_then_fails_at_real_boundary(monkeypatch) -> None:
    class Runtime(_SequenceAnchorRuntime):
        remaining_changes = 2

        def scroll_shape_content(self, scene: int, shape: str, *, direction: str, **kwargs):
            self.scrolls.append(direction)
            self.scroll_kwargs.append(dict(kwargs))
            if self.remaining_changes <= 0:
                yield None
                return False
            self.remaining_changes -= 1
            self.viewport_start += 5
            yield None
            return True

    signatures = [
        (1 + index % 6, 9000 + index, (1400 + index, 120 + index))
        for index in range(100)
    ]
    snapshot, target_id = _sequence_snapshot(signatures, target_index=92)
    runtime = Runtime(signatures)
    monkeypatch.setattr(beast_spirit_update, "_verify_ui_bag_order", lambda _ids: None)

    with pytest.raises(BeastSoulTargetNotFoundError, match="滚动边界"):
        _generator_result(_open_bag_item_detail(runtime, snapshot, target_id))

    assert runtime.opened_indices == [0, 10]
    assert runtime.stage == 478


def test_sequence_anchor_extends_prefix_until_duplicate_candidate_is_unique(monkeypatch) -> None:
    signatures = [
        (2, 500),
        (3, 700),
        (4, 800),
        (5, 900),
        (6, 1000),
        (2, 500),
        (4, 701),
        (4, 801),
        (5, 901),
        (6, 1001),
    ]
    snapshot, target_id = _sequence_snapshot(signatures, target_index=2)
    runtime = _SequenceAnchorRuntime(signatures)
    monkeypatch.setattr(beast_spirit_update, "_verify_ui_bag_order", lambda _ids: None)

    result = _generator_result(_open_bag_item_detail(runtime, snapshot, target_id))

    assert result["item_id"] == target_id
    # First duplicate signature is insufficient; the second column resolves it.
    assert runtime.opened_indices[:2] == [0, 1]
    assert runtime.closed_count == 3


def test_sequence_anchor_rejects_ten_slot_repeated_sequence_and_closes_every_probe(monkeypatch) -> None:
    signatures = [(2, 500)] * 20
    snapshot, target_id = _sequence_snapshot(signatures, target_index=15)
    runtime = _SequenceAnchorRuntime(signatures)
    monkeypatch.setattr(beast_spirit_update, "_verify_ui_bag_order", lambda _ids: None)

    with pytest.raises(BeastSoulTargetNotFoundError, match="连续10格"):
        _generator_result(_open_bag_item_detail(runtime, snapshot, target_id))

    assert runtime.stage == 478
    assert runtime.closed_count == 10
    assert runtime.scrolls == []


def test_viewport_registration_extends_with_verified_five_slot_overlap(monkeypatch) -> None:
    signatures = [
        (2, 9000, (1280, 136)),
        (2, 8000, (1260, 132)),
        (2, 7000, (1240, 128)),
        (2, 6000, (1220, 124)),
        (2, 5000, (1200, 120)),
        *((1, 0, (1000, 80)),) * 15,
    ]
    snapshot, target_id = _sequence_snapshot(signatures, target_index=7)
    runtime = _SequenceAnchorRuntime(signatures, viewport_start=5)
    monkeypatch.setattr(beast_spirit_update, "_verify_ui_bag_order", lambda _ids: None)

    result = _generator_result(_open_bag_item_detail(runtime, snapshot, target_id))

    assert result["item_id"] == target_id
    assert runtime.scrolls[:2] == ["up", "down"]
    assert all(kwargs == {"recognition_shape": "魂晶背包/魂晶卡片模板"} for kwargs in runtime.scroll_kwargs)
    assert runtime.stage == 479


def test_sequence_anchor_fails_at_scroll_boundary_without_guessing(monkeypatch) -> None:
    signatures = [(1 + index % 6, 3000 + index) for index in range(21)]
    snapshot, target_id = _sequence_snapshot(signatures, target_index=15)
    runtime = _SequenceAnchorRuntime(signatures, allow_scroll=False)
    monkeypatch.setattr(beast_spirit_update, "_verify_ui_bag_order", lambda _ids: None)

    with pytest.raises(BeastSoulTargetNotFoundError, match="滚动边界"):
        _generator_result(_open_bag_item_detail(runtime, snapshot, target_id))

    assert runtime.stage == 478
    assert runtime.opened_indices == [0]
    assert runtime.closed_count == 1


def test_sequence_anchor_rejects_scroll_that_reports_change_without_directional_progress(
    monkeypatch,
) -> None:
    class Runtime(_SequenceAnchorRuntime):
        def scroll_shape_content(self, *args, direction: str, **kwargs):
            self.scrolls.append(direction)
            yield None
            return True

    signatures = [(1 + index % 6, 4000 + index) for index in range(21)]
    snapshot, target_id = _sequence_snapshot(signatures, target_index=15)
    runtime = Runtime(signatures)
    monkeypatch.setattr(beast_spirit_update, "_verify_ui_bag_order", lambda _ids: None)

    with pytest.raises(BeastSoulTargetNotFoundError, match="未按方向进展"):
        _generator_result(_open_bag_item_detail(runtime, snapshot, target_id))

    assert runtime.stage == 478
    assert runtime.scrolls == ["down"]


def test_sequence_anchor_never_treats_no_open_as_authoritative_empty(monkeypatch) -> None:
    signatures = [None, (2, 500), (3, 600), (4, 700), (5, 800)]
    snapshot = {
        "items": [
            {"item_id": f"item-{index}", "level": sig[0], "score": sig[1], "ui_bag_index": index}
            for index, sig in enumerate(signatures)
            if sig is not None
        ],
        "ui_bag_complete": True,
        "ui_bag_item_ids": [None, "item-1", "item-2", "item-3", "item-4"],
    }
    runtime = _SequenceAnchorRuntime(signatures)
    monkeypatch.setattr(beast_spirit_update, "_verify_ui_bag_order", lambda _ids: None)

    with pytest.raises(BeastSoulTargetNotFoundError, match="拒绝当作空格"):
        _generator_result(_open_bag_item_detail(runtime, snapshot, "item-1"))

    assert runtime.stage == 478


def test_live_ui_order_verification_rejects_changed_v_show_list(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        beast_spirit_update.fanxiu_instrumentation_service,
        "beast_spirit_ui_order",
        lambda **kwargs: calls.append(kwargs) or {
            "complete": True,
            "ui_bag_item_ids": ["changed"],
        },
    )

    with pytest.raises(RuntimeError, match="顺序发生变化"):
        beast_spirit_update._verify_ui_bag_order(["expected"])

    assert calls == [{"expected_item_ids": ["expected"]}]


def test_first_probe_order_change_stops_before_any_followup_click(monkeypatch) -> None:
    signatures = [
        (2, 500),
        (3, 700),
        (4, 800),
        (5, 900),
        (6, 1000),
        (2, 500),
        (4, 701),
        (4, 801),
        (5, 901),
        (6, 1001),
    ]
    snapshot, target_id = _sequence_snapshot(signatures, target_index=2)
    runtime = _SequenceAnchorRuntime(signatures)
    calls = []

    def changed(_ids):
        calls.append("verify")
        raise RuntimeError("定位期间 v_showList 顺序发生变化")

    monkeypatch.setattr(beast_spirit_update, "_verify_ui_bag_order", changed)

    with pytest.raises(RuntimeError, match="顺序发生变化"):
        _generator_result(_open_bag_item_detail(runtime, snapshot, target_id))

    assert calls == ["verify"]
    assert runtime.opened_indices == [0]
    assert runtime.closed_count == 1
    assert runtime.stage == 478


def test_sequence_locate_budget_scales_below_cell_timeout() -> None:
    assert beast_spirit_update._bag_locate_budget_seconds({"ui_bag_item_ids": ["a"]}) == 110
    large = beast_spirit_update._bag_locate_budget_seconds(
        {"ui_bag_item_ids": [str(index) for index in range(118)]}
    )
    assert 110 < large < 600


def test_bag_search_rejects_missing_active_ui_projection_before_click() -> None:
    runtime = _BagSearchRuntime()
    snapshot = {
        "items": [
            {"item_id": "target", "level": 2, "score": 4150, "bag_index": 10},
        ],
        "ui_bag_complete": False,
        "ui_bag_reason": "projection missing",
    }

    with pytest.raises(RuntimeError, match="活动魂晶列表未通过完整性校验"):
        _generator_result(_open_bag_item_detail(runtime, snapshot, "target"))
    assert runtime.closed_count == 0


def test_protected_sync_replans_only_when_missing_instance_is_absent_from_fresh_snapshot(monkeypatch) -> None:
    initial = {
        "items": [{"item_id": "old"}],
        "layout": {
            "unlocked_protected_item_ids": ["old"],
            "obsolete_locked_item_ids": [],
            "safe_to_synthesize": False,
        },
    }
    refreshed = {
        "complete": True,
        "items": [{"item_id": "new"}],
        "layout": {
            "unlocked_protected_item_ids": ["new"],
            "obsolete_locked_item_ids": [],
            "safe_to_synthesize": False,
        },
    }
    complete = {
        "complete": True,
        "items": [{"item_id": "new", "locked": True}],
        "layout": {
            "unlocked_protected_item_ids": [],
            "obsolete_locked_item_ids": [],
            "safe_to_synthesize": True,
        },
    }
    targets: list[str] = []

    def fake_toggle(
        _runtime, _snapshot_value, item_id, *, expected_locked, locate_deadline=None
    ):
        targets.append(item_id)
        yield None
        if item_id == "old":
            raise BeastSoulTargetNotFoundError(item_id, "old target missing")
        assert expected_locked is True
        return complete

    monkeypatch.setattr(beast_spirit_update, "_toggle_item_lock", fake_toggle)
    monkeypatch.setattr(beast_spirit_update, "_snapshot", lambda: refreshed)

    snapshot, actions = _generator_result(_sync_protected_items(object(), initial))

    assert snapshot is complete
    assert targets == ["old", "new"]
    assert actions == [{"kind": "lock", "item_id": "new"}]


def test_protected_sync_preserves_scan_error_when_instance_still_exists(monkeypatch) -> None:
    initial = {
        "complete": True,
        "items": [{"item_id": "same"}],
        "layout": {
            "unlocked_protected_item_ids": ["same"],
            "obsolete_locked_item_ids": [],
            "safe_to_synthesize": False,
        },
    }

    targets: list[str] = []

    def fake_toggle(
        _runtime, _snapshot_value, item_id, *, expected_locked, locate_deadline=None
    ):
        targets.append(item_id)
        yield None
        raise BeastSoulTargetNotFoundError(item_id, "scan failed")

    monkeypatch.setattr(beast_spirit_update, "_toggle_item_lock", fake_toggle)
    monkeypatch.setattr(beast_spirit_update, "_snapshot", lambda: initial)

    with pytest.raises(BeastSoulTargetNotFoundError, match="scan failed"):
        _generator_result(_sync_protected_items(object(), initial))
    assert targets == ["same", "same"]


def test_protected_sync_refresh_does_not_reset_absolute_locate_deadline(monkeypatch) -> None:
    initial = {
        "complete": True,
        "items": [{"item_id": "same"}],
        "layout": {
            "unlocked_protected_item_ids": ["same"],
            "obsolete_locked_item_ids": [],
            "safe_to_synthesize": False,
        },
    }
    attempts: list[float | None] = []

    def fake_toggle(
        _runtime, _snapshot_value, item_id, *, expected_locked, locate_deadline=None
    ):
        attempts.append(locate_deadline)
        yield None
        raise BeastSoulTargetNotFoundError(item_id, "scan failed")

    monkeypatch.setattr(beast_spirit_update, "_toggle_item_lock", fake_toggle)
    monkeypatch.setattr(beast_spirit_update, "_snapshot", lambda: initial)
    # Shared deadline=110.  The one allowed full refresh returns after it;
    # therefore a second locate attempt must never be started.
    moments = iter((0.0, 1.0, 2.0, 111.0))
    monkeypatch.setattr(
        beast_spirit_update.monotonic_time,
        "monotonic",
        lambda: next(moments),
    )

    with pytest.raises(BeastSoulTargetNotFoundError, match="超过110秒"):
        _generator_result(_sync_protected_items(object(), initial))

    assert attempts == [110.0]


def test_protected_sync_retries_existing_instance_once_with_fresh_position(monkeypatch) -> None:
    initial = {
        "items": [{"item_id": "same", "bag_index": 1}],
        "layout": {
            "unlocked_protected_item_ids": ["same"],
            "obsolete_locked_item_ids": [],
            "safe_to_synthesize": False,
        },
    }
    refreshed = {
        "complete": True,
        "items": [{"item_id": "same", "bag_index": 7}],
        "layout": {
            "unlocked_protected_item_ids": ["same"],
            "obsolete_locked_item_ids": [],
            "safe_to_synthesize": False,
        },
    }
    complete = {
        "complete": True,
        "items": [{"item_id": "same", "locked": True}],
        "layout": {
            "unlocked_protected_item_ids": [],
            "obsolete_locked_item_ids": [],
            "safe_to_synthesize": True,
        },
    }
    positions: list[int] = []

    def fake_toggle(
        _runtime, snapshot_value, item_id, *, expected_locked, locate_deadline=None
    ):
        positions.append(snapshot_value["items"][0]["bag_index"])
        yield None
        if snapshot_value is initial:
            raise BeastSoulTargetNotFoundError(item_id, "stale position")
        return complete

    monkeypatch.setattr(beast_spirit_update, "_toggle_item_lock", fake_toggle)
    monkeypatch.setattr(beast_spirit_update, "_snapshot", lambda: refreshed)

    snapshot, actions = _generator_result(_sync_protected_items(object(), initial))

    assert snapshot is complete
    assert positions == [1, 7]
    assert actions == [{"kind": "lock", "item_id": "same"}]


@pytest.mark.parametrize(
    "refreshed",
    [
        {
            "complete": False,
            "items": [],
            "layout": {
                "unlocked_protected_item_ids": [],
                "obsolete_locked_item_ids": [],
                "safe_to_synthesize": True,
            },
        },
        {
            "complete": True,
            "items": [],
            "layout": {
                "unlocked_protected_item_ids": [],
                "obsolete_locked_item_ids": [],
                "safe_to_synthesize": False,
            },
        },
        {
            "complete": True,
            "items": [],
            "layout": {
                "unlocked_protected_item_ids": ["old"],
                "obsolete_locked_item_ids": [],
                "safe_to_synthesize": False,
            },
        },
    ],
)
def test_protected_sync_rejects_incomplete_or_inconsistent_missing_target_refresh(monkeypatch, refreshed) -> None:
    initial = {
        "items": [{"item_id": "old"}],
        "layout": {
            "unlocked_protected_item_ids": ["old"],
            "obsolete_locked_item_ids": [],
            "safe_to_synthesize": False,
        },
    }

    def fake_toggle(
        _runtime, _snapshot_value, item_id, *, expected_locked, locate_deadline=None
    ):
        yield None
        raise BeastSoulTargetNotFoundError(item_id, "scan failed")

    monkeypatch.setattr(beast_spirit_update, "_toggle_item_lock", fake_toggle)
    monkeypatch.setattr(beast_spirit_update, "_snapshot", lambda: refreshed)

    with pytest.raises(RuntimeError):
        _generator_result(_sync_protected_items(object(), initial))


class _SynthesisRuntime:
    def __init__(
        self,
        *,
        confirm_scene: int | None = 527,
        confirm_text: str = "当前成功率较低是否确认进行合成",
        result_scene: int | None = 481,
        missing_identity: str | None = None,
        confirm_scenes: list[int | None] | None = None,
        result_scenes: list[int | None] | None = None,
    ) -> None:
        self.confirm_scene = confirm_scene
        self.confirm_text = confirm_text
        self.result_scene = result_scene
        self.missing_identity = missing_identity
        self.confirm_scenes = list(confirm_scenes or [])
        self.result_scenes = list(result_scenes or [])
        self.calls: list[tuple[object, ...]] = []

    def wait_click(self, scene: int, shape: str):
        self.calls.append(("wait_click", scene, shape))
        yield None

    def wait_action_settle(self, seconds: float):
        self.calls.append(("settle", seconds))
        yield None

    def observe_scene(self, views=None, *, update=False):
        self.calls.append(("observe_scene", views, update))
        if views is not None:
            if self.confirm_scenes:
                return self.confirm_scenes.pop(0), 100.0, "confirm-frame"
            return self.confirm_scene, 100.0, "confirm-frame"
        if self.result_scenes:
            return self.result_scenes.pop(0), 100.0, "result-frame"
        return self.result_scene, 100.0, "result-frame"

    def ocr_fragments(self, *, frame_data_url: str):
        self.calls.append(("ocr_fragments", frame_data_url))
        return [{"text": self.confirm_text}]

    def shape(self, scene: int, title: str):
        self.calls.append(("shape", scene, title))
        return title

    def match_shape(self, shape):
        self.calls.append(("match_shape", shape))
        return shape != self.missing_identity

    def click_shape(self, scene: int, shape, *, frame_data_url: str):
        self.calls.append(("click_shape", scene, shape, frame_data_url))


class _QuickSynthesisExitRuntime:
    def __init__(self, scenes: list[int]) -> None:
        self.scenes = list(scenes)
        self.calls: list[tuple[object, ...]] = []

    def current_scene(self, *, views, update=False):
        self.calls.append(("current_scene", tuple(views), update))
        return self.scenes.pop(0), 100.0, "frame"

    def wait_click(self, scene: int, shape: str):
        self.calls.append(("wait_click", scene, shape))
        yield None

    def wait_action_settle(self, seconds: float):
        self.calls.append(("settle", seconds))
        yield None

    def wait_scene(self, *scenes: int, **kwargs):
        self.calls.append(("wait_scene", scenes, kwargs))
        yield None


def test_leave_quick_synthesis_consumes_delayed_continue_and_material_dropdown() -> None:
    runtime = _QuickSynthesisExitRuntime([346, 482])

    _generator_result(_leave_quick_synthesis(runtime))

    assert ("wait_click", 346, "继续") in runtime.calls
    assert ("wait_click", 482, "收起材料") in runtime.calls
    assert ("wait_click", 481, "点击空白处关闭") in runtime.calls
    assert runtime.calls[-1] == (
        "wait_scene",
        (478,),
        {"timeout": 8, "label": "兽魂更新：关闭快捷合成"},
    )


def test_leave_quick_synthesis_rechecks_481_then_consumes_late_346() -> None:
    runtime = _QuickSynthesisExitRuntime([481, 346, 481])

    _generator_result(_leave_quick_synthesis(runtime))

    assert runtime.calls[:3] == [
        ("current_scene", (481, 482, 346, 478), True),
        ("settle", 1.0),
        ("current_scene", (481, 482, 346, 478), True),
    ]
    assert ("wait_click", 346, "继续") in runtime.calls
    assert ("wait_click", 481, "点击空白处关闭") in runtime.calls


def test_leave_quick_synthesis_accepts_stable_direct_481() -> None:
    runtime = _QuickSynthesisExitRuntime([481, 481])

    _generator_result(_leave_quick_synthesis(runtime))

    assert ("wait_click", 346, "继续") not in runtime.calls
    assert ("wait_click", 481, "点击空白处关闭") in runtime.calls


def test_leave_quick_synthesis_unknown_after_481_recheck_fails_closed() -> None:
    runtime = _QuickSynthesisExitRuntime([481, 999])

    with pytest.raises(RuntimeError, match="未支持场景 #999"):
        _generator_result(_leave_quick_synthesis(runtime))

    assert not any(call[0] == "wait_click" for call in runtime.calls)


def _synthesis_snapshots(*, produced: bool = True):
    protected = _item("9", 8, locked=True, excluded=True)
    before = {
        "complete": True,
        "items": [_item("1", 1), _item("2", 1), protected],
        "layout": {"protected_item_ids": ["9"]},
    }
    after_items = [protected]
    if produced:
        after_items.append(_item("100", 2))
    after = {
        "complete": True,
        "items": after_items,
        "layout": {"protected_item_ids": ["9"]},
    }
    return before, after


def test_synthesis_exact_low_probability_popup_uses_only_formal_527_confirm_once(
    monkeypatch,
) -> None:
    runtime = _SynthesisRuntime()
    before, after = _synthesis_snapshots()
    snapshots: list[object] = [after]
    monkeypatch.setattr(beast_spirit_update, "_snapshot", lambda: snapshots.pop(0))
    monkeypatch.setattr(
        beast_spirit_update,
        "_capture_synthesis_evidence",
        lambda *_args, **_kwargs: "result-evidence.json",
    )

    result = _generator_result(_execute_current_batch(runtime, before, 1))

    assert ("wait_click", 481, "执行快捷合成") in runtime.calls
    assert sum(call[0] == "wait_click" for call in runtime.calls) == 1
    assert sum(call[0] == "observe_scene" for call in runtime.calls) == 2
    assert next(call for call in runtime.calls if call[0] == "observe_scene") == (
        "observe_scene",
        [527, 529, 481],
        True,
    )
    assert [call for call in runtime.calls if call[0] == "shape"] == [
        ("shape", 527, "低成功率确认正文"),
        ("shape", 527, "取消文字"),
        ("shape", 527, "确认文字"),
        ("shape", 527, "确认"),
    ]
    assert [call for call in runtime.calls if call[0] == "click_shape"] == [
        ("click_shape", 527, "确认", "confirm-frame")
    ]
    assert result == {
        "ok": True,
        "batch_size": 2,
        "success_probability": 0.55,
        "cost_count": 2,
        "success": 1,
        "failure": 0,
        "created_item_ids": ["100"],
    }
    assert snapshots == []


@pytest.mark.parametrize("scene_id", [47, 481])
def test_synthesis_low_probability_confirmation_requires_formal_scene(
    monkeypatch,
    scene_id,
) -> None:
    runtime = _SynthesisRuntime(confirm_scene=scene_id)
    before, _after = _synthesis_snapshots()
    monkeypatch.setattr(
        beast_spirit_update,
        "_capture_synthesis_evidence",
        lambda *_args, **_kwargs: "evidence.json",
    )
    if scene_id == 481:
        monkeypatch.setattr(beast_spirit_update, "_snapshot", lambda: before)

    with pytest.raises(RuntimeError, match="未识别到正式低成功率确认场景"):
        _generator_result(_execute_current_batch(runtime, before, 1))

    assert not any(call[0] == "click_shape" for call in runtime.calls)
    expected_clicks = 2 if scene_id == 481 else 1
    assert sum(call[:3] == ("wait_click", 481, "执行快捷合成") for call in runtime.calls) == expected_clicks


def test_synthesis_low_probability_waits_for_delayed_formal_confirmation(monkeypatch) -> None:
    runtime = _SynthesisRuntime(confirm_scenes=[481, 481, 527])
    before, after = _synthesis_snapshots()
    monkeypatch.setattr(beast_spirit_update, "_snapshot", lambda: after)

    result = _generator_result(_execute_current_batch(runtime, before, 1))

    assert result["ok"] is True
    assert sum(call[:3] == ("wait_click", 481, "执行快捷合成") for call in runtime.calls) == 1
    assert [call for call in runtime.calls if call[0] == "click_shape"] == [
        ("click_shape", 527, "确认", "confirm-frame")
    ]


def test_real_runner_excluding_generic_47_resolves_double_match_ambiguity(
    monkeypatch,
) -> None:
    runner = create_behavior_tree_runtime_runner()

    def image(scene_id: int, title: str) -> dict[str, object]:
        return {
            "type": "image",
            "title": title,
            "filename": f"{scene_id:04d}.png",
            "width": 900,
            "height": 1600,
            "shapes": [
                {
                    "id": f"identity-{scene_id}",
                    "kind": "rect",
                    "title": f"identity-{scene_id}",
                    "sceneIdentityRole": "required",
                }
            ],
        }

    images = {
        47: image(47, "所有提示窗口"),
        481: image(481, "魂晶快捷合成"),
        527: image(527, "魂晶低成功率确认"),
    }
    ctx = {"asset_tree": list(images.values()), "images": images}
    monkeypatch.setattr(
        runner,
        "_scene_score",
        lambda _ctx, scene, _frame: {
            47: 95.0,
            481: 0.0,
            527: 100.0,
        }[runner._image_number(scene)],
    )
    monkeypatch.setattr(
        runner,
        "_scene_match_edges_for_candidates",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        runner,
        "_scene_reference_similarity",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(runner, "_cached_ocr_fragments", lambda *_args: [])

    assert runner._identify_scene_number(ctx, "frame", [527, 47, 481]) == (
        None,
        100.0,
    )
    assert runner._identify_scene_number(ctx, "frame", [527, 481]) == (527, 100.0)


def test_synthesis_527_does_not_use_full_frame_ocr(monkeypatch) -> None:
    runtime = _SynthesisRuntime()
    before, after = _synthesis_snapshots()
    monkeypatch.setattr(beast_spirit_update, "_snapshot", lambda: after)
    monkeypatch.setattr(
        beast_spirit_update,
        "_capture_synthesis_evidence",
        lambda *_args, **_kwargs: "result-evidence.json",
    )

    result = _generator_result(_execute_current_batch(runtime, before, 1))

    assert not any(call[0] == "ocr_fragments" for call in runtime.calls)
    assert result["ok"] is True


def test_synthesis_527_requires_all_three_formal_identities_on_same_frame(
    monkeypatch,
) -> None:
    runtime = _SynthesisRuntime(missing_identity="确认文字")
    before, _after = _synthesis_snapshots()
    monkeypatch.setattr(
        beast_spirit_update,
        "_capture_synthesis_evidence",
        lambda *_args, **_kwargs: "evidence.json",
    )

    with pytest.raises(RuntimeError, match="三项 required 身份"):
        _generator_result(_execute_current_batch(runtime, before, 1))

    assert [call for call in runtime.calls if call[0] == "match_shape"] == [
        ("match_shape", "低成功率确认正文"),
        ("match_shape", "取消文字"),
        ("match_shape", "确认文字"),
    ]
    assert not any(call[0] == "click_shape" for call in runtime.calls)


def test_synthesis_527_missing_unique_formal_confirm_selector_is_zero_click(
    monkeypatch,
) -> None:
    runtime = _SynthesisRuntime()
    before, _after = _synthesis_snapshots()
    original_shape = runtime.shape

    def shape(scene: int, title: str):
        if title == "确认":
            raise RuntimeError("shape 选择器 [确认] 未命中")
        return original_shape(scene, title)

    monkeypatch.setattr(runtime, "shape", shape)

    with pytest.raises(RuntimeError, match=r"\[确认\] 未命中"):
        _generator_result(_execute_current_batch(runtime, before, 1))

    assert not any(call[0] == "click_shape" for call in runtime.calls)


def test_synthesis_snapshot_delta_reports_failed_attempt_without_output(
) -> None:
    before, after = _synthesis_snapshots(produced=False)

    result = _verify_synthesis_snapshot_delta(
        before,
        after,
        policy=quick_synthesis_policy(1),
    )

    assert result["cost_count"] == 2
    assert result["success"] == 0
    assert result["failure"] == 1


def test_high_level_precious_material_popup_uses_only_formal_529_confirm(
    monkeypatch,
) -> None:
    runtime = _SynthesisRuntime(confirm_scene=529)
    protected = _item("9", 8, locked=True, excluded=True)
    before = {
        "complete": True,
        "items": [_item("1", 6), _item("2", 6), _item("3", 6), protected],
        "layout": {"protected_item_ids": ["9"]},
    }
    after = {
        "complete": True,
        "items": [_item("100", 7), protected],
        "layout": {"protected_item_ids": ["9"]},
    }
    monkeypatch.setattr(beast_spirit_update, "_snapshot", lambda: after)

    result = _generator_result(_execute_current_batch(runtime, before, 6))

    assert [call for call in runtime.calls if call[0] == "shape"] == [
        ("shape", 529, "珍稀材料确认正文"),
        ("shape", 529, "取消文字"),
        ("shape", 529, "确认文字"),
        ("shape", 529, "确认"),
    ]
    assert [call for call in runtime.calls if call[0] == "click_shape"] == [
        ("click_shape", 529, "确认", "confirm-frame")
    ]
    assert result["batch_size"] == 3
    assert result["success"] == 1


def test_expected_precious_popup_ignores_transient_481_until_delayed_529(
    monkeypatch,
) -> None:
    runtime = _SynthesisRuntime(confirm_scenes=[481, 481, 529])
    protected = _item("9", 8, locked=True, excluded=True)
    before = {
        "complete": True,
        "items": [_item("1", 6), _item("2", 6), _item("3", 6), protected],
        "layout": {"protected_item_ids": ["9"]},
    }
    after = {
        "complete": True,
        "items": [_item("100", 7), protected],
        "layout": {"protected_item_ids": ["9"]},
    }
    monkeypatch.setattr(beast_spirit_update, "_snapshot", lambda: after)

    result = _generator_result(_execute_current_batch(runtime, before, 6))

    assert result["success"] == 1
    assert [call for call in runtime.calls if call[0] == "click_shape"] == [
        ("click_shape", 529, "确认", "confirm-frame")
    ]
    assert len([call for call in runtime.calls if call[0] == "observe_scene"]) == 4


def test_expected_precious_popup_suppressed_allows_only_valid_direct_delta(monkeypatch) -> None:
    runtime = _SynthesisRuntime(confirm_scene=481)
    protected = _item("9", 8, locked=True, excluded=True)
    before = {
        "complete": True,
        "items": [_item("1", 6), _item("2", 6), _item("3", 6), protected],
        "layout": {"protected_item_ids": ["9"]},
    }
    after = {
        "complete": True,
        "items": [_item("100", 7), protected],
        "layout": {"protected_item_ids": ["9"]},
    }
    monkeypatch.setattr(beast_spirit_update, "_snapshot", lambda: after)

    result = _generator_result(_execute_current_batch(runtime, before, 6))

    assert result["success"] == 1
    assert not any(call[0] == "click_shape" for call in runtime.calls)
    assert len([call for call in runtime.calls if call[0] == "observe_scene"]) == 12


def test_expected_precious_popup_suppressed_rejects_missing_direct_delta(monkeypatch) -> None:
    runtime = _SynthesisRuntime(confirm_scene=481)
    before = {
        "complete": True,
        "items": [_item("1", 6), _item("2", 6), _item("3", 6)],
        "layout": {"protected_item_ids": []},
    }
    monkeypatch.setattr(beast_spirit_update, "_snapshot", lambda: before)
    monkeypatch.setattr(
        beast_spirit_update,
        "_capture_synthesis_evidence",
        lambda *_args, **_kwargs: "missing-delta.json",
    )

    with pytest.raises(RuntimeError, match="扣除数量"):
        _generator_result(_execute_current_batch(runtime, before, 6))

    assert not any(call[0] == "click_shape" for call in runtime.calls)


def test_expected_precious_popup_unknown_during_wait_has_zero_click(monkeypatch) -> None:
    runtime = _SynthesisRuntime(confirm_scenes=[481, 47])
    before = {
        "complete": True,
        "items": [_item("1", 6), _item("2", 6), _item("3", 6)],
        "layout": {"protected_item_ids": []},
    }
    monkeypatch.setattr(
        beast_spirit_update,
        "_capture_synthesis_evidence",
        lambda *_args, **_kwargs: "unexpected.json",
    )

    with pytest.raises(RuntimeError, match="未命中 #529"):
        _generator_result(_execute_current_batch(runtime, before, 6))

    assert not any(call[0] == "click_shape" for call in runtime.calls)


def test_precious_confirm_waits_until_popup_closes_back_to_481(monkeypatch) -> None:
    runtime = _SynthesisRuntime(confirm_scene=529, result_scenes=[529, 529, 481])
    protected = _item("9", 8, locked=True, excluded=True)
    before = {
        "complete": True,
        "items": [_item("1", 6), _item("2", 6), _item("3", 6), protected],
        "layout": {"protected_item_ids": ["9"]},
    }
    after = {
        "complete": True,
        "items": [_item("100", 7), protected],
        "layout": {"protected_item_ids": ["9"]},
    }
    monkeypatch.setattr(beast_spirit_update, "_snapshot", lambda: after)

    result = _generator_result(_execute_current_batch(runtime, before, 6))

    assert result["success"] == 1
    assert [call for call in runtime.calls if call[0] == "click_shape"] == [
        ("click_shape", 529, "确认", "confirm-frame")
    ]


def test_synthesis_real_batch_returns_from_481_with_cost18_success4_failure5(
    monkeypatch,
) -> None:
    runtime = _SynthesisRuntime(result_scene=481)
    protected = _item("999", 8, locked=True, excluded=True)
    before = {
        "complete": True,
        "items": [*[_item(str(item_id), 1) for item_id in range(1, 19)], protected],
        "layout": {"protected_item_ids": ["999"]},
    }
    after = {
        "complete": True,
        "items": [
            *[_item(str(item_id), 2) for item_id in range(101, 105)],
            protected,
        ],
        "layout": {"protected_item_ids": ["999"]},
    }
    monkeypatch.setattr(beast_spirit_update, "_snapshot", lambda: after)

    result = _generator_result(_execute_current_batch(runtime, before, 1))

    assert result == {
        "ok": True,
        "batch_size": 2,
        "success_probability": 0.55,
        "cost_count": 18,
        "success": 4,
        "failure": 5,
        "created_item_ids": ["101", "102", "103", "104"],
    }
    assert any(call[:3] == ("observe_scene", None, True) for call in runtime.calls)


def test_synthesis_481_with_invalid_delta_fails_closed(monkeypatch) -> None:
    runtime = _SynthesisRuntime(result_scene=481)
    before, _after = _synthesis_snapshots()
    monkeypatch.setattr(beast_spirit_update, "_snapshot", lambda: before)
    monkeypatch.setattr(
        beast_spirit_update,
        "_capture_synthesis_evidence",
        lambda *_args, **_kwargs: "invalid-delta.json",
    )

    with pytest.raises(RuntimeError, match="材料扣除数量异常.*invalid-delta"):
        _generator_result(_execute_current_batch(runtime, before, 1))


def test_synthesis_unknown_result_is_captured_and_fails_closed(monkeypatch) -> None:
    runtime = _SynthesisRuntime(confirm_scene=None, result_scene=None)
    protected = _item("9", 8, locked=True, excluded=True)
    before = {
        "complete": True,
        "items": [_item("1", 4), _item("2", 4), _item("3", 4), protected],
        "layout": {"protected_item_ids": ["9"]},
    }
    after = {
        "complete": True,
        "items": [_item("100", 5), protected],
        "layout": {"protected_item_ids": ["9"]},
    }
    captures: list[tuple[str, int | None]] = []
    snapshot_calls: list[bool] = []
    monkeypatch.setattr(
        beast_spirit_update,
        "_snapshot",
        lambda: snapshot_calls.append(True) or after,
    )
    monkeypatch.setattr(
        beast_spirit_update,
        "_capture_synthesis_evidence",
        lambda _runtime, frame, *, scene_id, score: (
            captures.append((frame, scene_id)) or "evidence.json"
        ),
    )

    with pytest.raises(RuntimeError, match="未返回正式快捷合成页"):
        _generator_result(_execute_current_batch(runtime, before, 4))

    assert captures == [("result-frame", None)]
    assert snapshot_calls == []


def test_synthesis_executor_does_not_reference_nonexistent_formal_confirm_shape() -> None:
    source = inspect.getsource(_execute_current_batch)

    assert 'runtime.shape(47, "确认")' not in source
    assert 'BEAST_SOUL_LOW_SUCCESS_CONFIRMATION_SCENE' in source
    assert '"确认"' in source


class _MaterialSelectionRuntime:
    def __init__(self, *, count_text: str, low_count_matches: bool = True) -> None:
        self.count_text = count_text
        self.low_count_matches = low_count_matches
        self.calls: list[tuple[object, ...]] = []

    def wait_click(self, scene: int, shape: str):
        self.calls.append(("wait_click", scene, shape))
        yield None

    def wait_scene(self, scene: int, **kwargs):
        self.calls.append(("wait_scene", scene, kwargs))
        yield None

    def cur_frame(self, *, update: bool):
        self.calls.append(("cur_frame", update))
        return "frame"

    def find_floating_items_by_anchor_text(self, *args, **kwargs):
        self.calls.append(("find_item", args, kwargs))
        return [{"target": args[3]}]

    def click_floating_item_field(self, item, field: str):
        self.calls.append(("click_item", item, field))

    def floating_item_field_is_fully_inside(self, item, field: str, container: str):
        self.calls.append(("field_fully_inside", item, field, container))
        return item.get("fully_inside", True)

    def ocr_text_in_shapes(self, scene: int, shapes, *, padding: int):
        self.calls.append(("ocr_text_in_shapes", scene, tuple(shapes), padding))
        if tuple(shapes) == ("合成目标", "材料下拉"):
            return "随机神品魂晶 消耗所有四级魂晶"
        return self.count_text

    def shape(self, scene: int, title: str):
        self.calls.append(("shape", scene, title))
        return title

    def match_shape(self, shape):
        self.calls.append(("match_shape", shape))
        return self.low_count_matches


def _batch_panel_snapshot(**overrides):
    snapshot = {
        "ok": True,
        "available": True,
        "complete": True,
        "source": "active_beast_spirit_batch_strength_panel",
        "captured_at_epoch": beast_spirit_update.monotonic_time.time(),
        "source_level": 4,
        "batch_size": 3,
        "success_probability_percent": 100.0,
        "evidence": {
            "pid": 123,
            "process_start_ticks": 456,
            "read_only": True,
        },
    }
    snapshot.update(overrides)
    return snapshot


def test_high_level_material_selection_uses_authoritative_read_only_panel(
    monkeypatch,
) -> None:
    runtime = _MaterialSelectionRuntime(count_text="每次消耗 3")
    monkeypatch.setattr(
        beast_spirit_update.fanxiu_instrumentation_service,
        "beast_spirit_quick_synthesis_snapshot",
        lambda: _batch_panel_snapshot(),
    )

    _generator_result(_select_material(runtime, 4))

    assert ("ocr_text_in_shapes", 481, ("每次消耗",), 0) not in runtime.calls
    assert not any(call[0] == "shape" for call in runtime.calls)
    assert [call for call in runtime.calls if call[0] == "click_item"] == [
        ("click_item", {"target": "消耗所有四级魂晶"}, "材料等级")
    ]


def test_material_selection_retries_exact_match_with_container_crop() -> None:
    runtime = _MaterialSelectionRuntime(count_text="每次消耗 2")
    calls = 0

    def find_item(*args, **kwargs):
        nonlocal calls
        calls += 1
        runtime.calls.append(("find_item", args, kwargs))
        return [{"target": args[3]}] if kwargs.get("crop") is True else []

    runtime.find_floating_items_by_anchor_text = find_item
    original = runtime.ocr_text_in_shapes
    runtime.ocr_text_in_shapes = lambda scene, shapes, *, padding: (
        "随机二级魂晶 消耗所有一级魂晶"
        if tuple(shapes) == ("合成目标", "材料下拉")
        else original(scene, shapes, padding=padding)
    )

    _generator_result(_select_material(runtime, 1))

    find_calls = [call for call in runtime.calls if call[0] == "find_item"]
    assert calls == 2
    assert find_calls[0][2].get("crop") is None
    assert find_calls[1][2]["crop"] is True
    assert [call for call in runtime.calls if call[0] == "click_item"] == [
        ("click_item", {"target": "消耗所有一级魂晶"}, "材料等级")
    ]


def test_material_selection_rereads_after_first_unchanged_visual_sample(
    monkeypatch,
) -> None:
    runtime = _MaterialSelectionRuntime(count_text="unused")
    find_results = iter([[], [], [{"target": "消耗所有神品一星魂晶"}]])

    def find_item(*args, **kwargs):
        runtime.calls.append(("find_item", args, kwargs))
        return next(find_results)

    def scroll(*args, **kwargs):
        runtime.calls.append(("scroll", args, kwargs))
        yield None
        # scroll_shape_content translates the first unchanged visual hash to
        # True when unchanged_confirmations=2.
        return True

    runtime.find_floating_items_by_anchor_text = find_item
    runtime.scroll_shape_content = scroll
    runtime.ocr_text_in_shapes = lambda scene, shapes, *, padding: (
        "随机神品二星魂晶 消耗所有神品一星魂晶"
    )
    monkeypatch.setattr(
        beast_spirit_update.fanxiu_instrumentation_service,
        "beast_spirit_quick_synthesis_snapshot",
        lambda: _batch_panel_snapshot(source_level=6),
    )

    _generator_result(_select_material(runtime, 6))

    events = [call[0] for call in runtime.calls if call[0] in {"find_item", "scroll"}]
    assert events == ["find_item", "find_item", "scroll", "find_item"]
    scroll_call = next(call for call in runtime.calls if call[0] == "scroll")
    assert scroll_call[2]["unchanged_confirmations"] == 2


def test_partial_material_field_is_not_clicked_and_scrolls_to_fully_visible_copy(
    monkeypatch,
) -> None:
    runtime = _MaterialSelectionRuntime(count_text="unused")
    find_results = iter([
        [{"target": "消耗所有神品一星魂晶", "fully_inside": False}],
        [{"target": "消耗所有神品一星魂晶", "fully_inside": True}],
    ])

    def find_item(*args, **kwargs):
        runtime.calls.append(("find_item", args, kwargs))
        return next(find_results)

    def scroll(*args, **kwargs):
        runtime.calls.append(("scroll", args, kwargs))
        yield None
        return True

    runtime.find_floating_items_by_anchor_text = find_item
    runtime.scroll_shape_content = scroll
    runtime.ocr_text_in_shapes = lambda scene, shapes, *, padding: (
        "随机神品二星魂晶 消耗所有神品一星魂晶"
    )
    monkeypatch.setattr(
        beast_spirit_update.fanxiu_instrumentation_service,
        "beast_spirit_quick_synthesis_snapshot",
        lambda: _batch_panel_snapshot(source_level=6),
    )

    _generator_result(_select_material(runtime, 6))

    assert len([call for call in runtime.calls if call[0] == "scroll"]) == 1
    assert [call for call in runtime.calls if call[0] == "click_item"] == [
        (
            "click_item",
            {"target": "消耗所有神品一星魂晶", "fully_inside": True},
            "材料等级",
        )
    ]


def test_material_selection_only_fails_after_two_unchanged_drags(monkeypatch) -> None:
    runtime = _MaterialSelectionRuntime(count_text="unused")
    runtime.find_floating_items_by_anchor_text = lambda *args, **kwargs: []
    results = iter([True, False])

    def scroll(*args, **kwargs):
        runtime.calls.append(("scroll", args, kwargs))
        yield None
        return next(results)

    runtime.scroll_shape_content = scroll
    monkeypatch.setattr(
        beast_spirit_update.fanxiu_instrumentation_service,
        "beast_spirit_quick_synthesis_snapshot",
        lambda: _batch_panel_snapshot(source_level=6),
    )

    with pytest.raises(RuntimeError, match="材料列表中找不到"):
        _generator_result(_select_material(runtime, 6))

    scroll_calls = [call for call in runtime.calls if call[0] == "scroll"]
    assert len(scroll_calls) == 2
    assert all(call[2]["unchanged_confirmations"] == 2 for call in scroll_calls)


@pytest.mark.parametrize(
    ("level", "material_label", "result_label"),
    [
        (5, "神品", "神品一星"),
        (6, "神品一星", "神品二星"),
        (7, "神品二星", "神品三星"),
        (8, "神品三星", "神品四星"),
    ],
)
def test_star_material_selection_uses_the_formal_dropdown_labels(
    monkeypatch,
    level,
    material_label,
    result_label,
) -> None:
    runtime = _MaterialSelectionRuntime(count_text="unused")
    runtime.ocr_text_in_shapes = lambda scene, shapes, *, padding: (
        f"随机{result_label}魂晶 消耗所有{material_label}魂晶"
    )
    monkeypatch.setattr(
        beast_spirit_update.fanxiu_instrumentation_service,
        "beast_spirit_quick_synthesis_snapshot",
        lambda: _batch_panel_snapshot(source_level=level),
    )

    _generator_result(_select_material(runtime, level))

    assert [call for call in runtime.calls if call[0] == "click_item"] == [
        (
            "click_item",
            {"target": f"消耗所有{material_label}魂晶"},
            "材料等级",
        )
    ]


@pytest.mark.parametrize("level", range(4, 9))
def test_high_level_panel_gate_maps_source_level_without_hardcoding_four(
    monkeypatch,
    level,
) -> None:
    monkeypatch.setattr(
        beast_spirit_update.fanxiu_instrumentation_service,
        "beast_spirit_quick_synthesis_snapshot",
        lambda: _batch_panel_snapshot(source_level=level),
    )

    result = beast_spirit_update._require_high_level_quick_synthesis_state(
        level,
        beast_spirit_update.quick_synthesis_policy(level),
    )

    assert result["source_level"] == level
    assert result["batch_size"] == 3
    assert result["success_probability_percent"] == 100.0


@pytest.mark.parametrize(
    "overrides",
    [
        {"source_level": 3},
        {"batch_size": 2},
        {"success_probability_percent": 99.9},
        {"complete": False},
        {"captured_at_epoch": 0},
        {"captured_at_epoch": beast_spirit_update.monotonic_time.time() - 5.01},
        {"evidence": {"pid": None, "process_start_ticks": 456, "read_only": True}},
        {"evidence": {"pid": True, "process_start_ticks": 456, "read_only": True}},
    ],
)
def test_high_level_material_selection_rejects_incomplete_panel_state(
    monkeypatch,
    overrides,
) -> None:
    runtime = _MaterialSelectionRuntime(count_text="每次消耗3")
    monkeypatch.setattr(
        beast_spirit_update.fanxiu_instrumentation_service,
        "beast_spirit_quick_synthesis_snapshot",
        lambda: _batch_panel_snapshot(**overrides),
    )

    with pytest.raises(RuntimeError, match="只读面板状态不完整或不一致"):
        _generator_result(_select_material(runtime, 4))

    assert not any(call[0] == "match_shape" for call in runtime.calls)


def test_low_level_material_selection_keeps_verified_two_image_gate() -> None:
    runtime = _MaterialSelectionRuntime(count_text="每次消耗3")

    # The fixture's target text is high-level, so override only that bounded
    # parameter projection while preserving the production count-2 image gate.
    original = runtime.ocr_text_in_shapes
    runtime.ocr_text_in_shapes = lambda scene, shapes, *, padding: (
        "随机二级魂晶 消耗所有一级魂晶"
        if tuple(shapes) == ("合成目标", "材料下拉")
        else original(scene, shapes, padding=padding)
    )

    _generator_result(_select_material(runtime, 1))

    assert ("shape", 481, "每次消耗2") in runtime.calls
    assert ("match_shape", "每次消耗2") in runtime.calls


def test_beast_soul_job_has_no_global_ocr_or_fixed_coordinate_actions() -> None:
    source = inspect.getsource(beast_spirit_update)

    for forbidden in (
        ".ocr_text(",
        ".click_ocr_text(",
        ".click_frame_point(",
        ".drag_frame_point(",
        ".goto_view(",
    ):
        assert forbidden not in source


def test_consumable_count_excludes_equipped_locked_and_server_excluded() -> None:
    snapshot = {
        "items": [
            _item("1", 6),
            _item("2", 6, equipped=True),
            _item("3", 6, locked=True),
            _item("4", 6, excluded=True),
            _item("5", 7),
        ]
    }

    assert consumable_count(snapshot, 6) == 1


def test_synthesis_gate_fails_closed_for_unlocked_protected_item() -> None:
    snapshot = {
        "layout": {
            "safe_to_synthesize": False,
            "unlocked_protected_item_ids": ["high-value"],
        },
        "items": [_item(str(index), 1) for index in range(4)],
    }

    assert synthesis_gate(snapshot, 1) == {
        "allowed": False,
        "reason": "lock_set_mismatch",
        "unlocked_protected_item_ids": ["high-value"],
        "obsolete_locked_item_ids": [],
    }


def test_synthesis_gate_requires_obsolete_lock_to_be_removed() -> None:
    snapshot = {
        "layout": {
            "safe_to_synthesize": False,
            "unlocked_protected_item_ids": [],
            "obsolete_locked_item_ids": ["old-protection"],
        },
        "items": [_item(str(index), 5) for index in range(4)],
    }

    assert synthesis_gate(snapshot, 5) == {
        "allowed": False,
        "reason": "lock_set_mismatch",
        "unlocked_protected_item_ids": [],
        "obsolete_locked_item_ids": ["old-protection"],
    }


def test_synthesis_gate_uses_actual_consumable_count() -> None:
    snapshot = {
        "layout": {
            "safe_to_synthesize": True,
            "unlocked_protected_item_ids": [],
        },
        "items": [
            _item("protected", 7, locked=True, excluded=True),
            _item("free-1", 7),
            _item("free-2", 7),
        ],
    }

    assert synthesis_gate(snapshot, 7) == {
        "allowed": False,
        "reason": "insufficient_consumable_items",
        "consumable_count": 2,
        "batch_size": 3,
    }


def test_synthesis_gate_allows_three_material_policy_before_ui_state_gate() -> None:
    snapshot = {
        "layout": {
            "safe_to_synthesize": True,
            "unlocked_protected_item_ids": [],
        },
        "items": [_item(str(index), 4) for index in range(3)],
    }

    assert synthesis_gate(snapshot, 4) == {
        "allowed": True,
        "reason": "ready",
        "consumable_count": 3,
        "batch_size": 3,
    }
