from __future__ import annotations

from backend.core.fanxiu.runtime_gui.storage_bag_alignment import (
    StorageBagItemClickPlan,
    StorageBagQuantityObservation,
    plan_storage_bag_item_click,
    plan_storage_bag_scroll,
    prepare_storage_bag_target,
    prepare_storage_bag_target_by_name,
    quantity_observations_from_ocr,
    register_storage_bag_viewport_from_quantity_ocr,
    verify_storage_bag_item_detail,
    visible_storage_bag_cells,
)
from backend.core.fanxiu.runtime_gui import storage_bag_alignment
from backend.core.fanxiu.runtime_gui.storage_bag_grid import StorageBagGrid, StorageBagViewport


def _grid_and_cells():
    grid = StorageBagGrid(
        window=(0.0, 0.0, 400.0, 200.0),
        first_cell=(0.0, 0.0, 80.0, 80.0),
        columns=4,
        column_pitch=100.0,
        row_pitch=100.0,
    )
    viewport = StorageBagViewport(
        status="aligned",
        reason="test",
        row_centers=(40.0, 140.0),
    )
    return grid, visible_storage_bag_cells(grid, viewport)


def _snapshot(nums: list[int]) -> dict:
    return {
        "complete": True,
        "items": [
            {"instance_id": str(index), "base_id": 100 + index, "num": number, "is_padding": False}
            for index, number in enumerate(nums)
        ],
    }


def _deep_snapshot(*, count: int = 140, target_index: int = 110) -> dict:
    snapshot = _snapshot([10_000 + index for index in range(count)])
    for index, item in enumerate(snapshot["items"]):
        item["base_id"] = 50_000 + index
    snapshot["items"][target_index]["base_id"] = 1001
    snapshot["items"][target_index]["num"] = 60
    snapshot["items"][target_index + 1]["num"] = 2
    snapshot["items"][target_index + 5]["num"] = 195
    return snapshot


def _four_row_cells():
    grid = StorageBagGrid(
        window=(0.0, 0.0, 400.0, 400.0),
        first_cell=(0.0, 0.0, 80.0, 80.0),
        columns=4,
        column_pitch=100.0,
        row_pitch=100.0,
    )
    viewport = StorageBagViewport(
        status="aligned",
        reason="test",
        row_centers=(40.0, 140.0, 240.0, 340.0),
    )
    return visible_storage_bag_cells(grid, viewport)


def test_quantity_ocr_uses_numerator_and_keeps_denominator_as_context() -> None:
    _grid, cells = _grid_and_cells()
    observations = quantity_observations_from_ocr(
        cells,
        [
            {"text": "5", "score": 0.99, "x": 60, "y": 58, "w": 12, "h": 12},
            {"text": "1/80", "score": 0.99, "x": 160, "y": 58, "w": 28, "h": 12},
            # A number in the image body is not a quantity observation.
            {"text": "999", "score": 0.99, "x": 10, "y": 10, "w": 20, "h": 12},
        ],
    )

    assert [(item.visible_index, item.quantity, item.required) for item in observations] == [
        (0, 5, None),
        (1, 1, 80),
    ]


def test_quantity_ocr_accepts_shared_spatial_tokens_without_confidence() -> None:
    _grid, cells = _grid_and_cells()

    observations = quantity_observations_from_ocr(
        cells,
        [{"text": "5", "x": 60, "y": 58, "w": 12, "h": 12}],
    )

    assert [(item.visible_index, item.quantity, item.confidence) for item in observations] == [
        (0, 5, 0.5),
    ]


def test_numeric_ocr_boxes_can_rebuild_rows_without_row_gap_image() -> None:
    grid, _cells = _grid_and_cells()
    reference = [
        {"text": "1/1", "score": 0.99, "x": 60, "y": 58, "w": 22, "h": 14},
        {"text": "2", "score": 0.99, "x": 160, "y": 58, "w": 12, "h": 14},
    ]
    current = [
        {"text": "1", "score": 0.99, "x": 60, "y": 61, "w": 12, "h": 14},
        {"text": "780", "score": 0.80, "x": 160, "y": 61, "w": 24, "h": 14},
        {"text": "42/80", "score": 0.99, "x": 260, "y": 161, "w": 36, "h": 14},
        {"text": "75", "score": 0.99, "x": 360, "y": 161, "w": 20, "h": 14},
    ]

    viewport = register_storage_bag_viewport_from_quantity_ocr(
        reference,
        current,
        grid=grid,
    )

    assert viewport.aligned
    assert viewport.row_centers[:2] == (43.0, 143.0)


def test_detail_title_is_a_second_fuzzy_confirmation_after_the_grid_click() -> None:
    plan = StorageBagItemClickPlan(
        status="ready",
        reason="quantity sequence",
        point=(366.0, 625.0),
    )

    verified = verify_storage_bag_item_detail(
        plan,
        expected_name="魔道法则",
        detail_title_texts=("魔道人则",),
    )
    mismatch = verify_storage_bag_item_detail(
        plan,
        expected_name="魔道法则",
        detail_title_texts=("朱雀环",),
    )

    assert verified.confirmed
    assert verified.similarity > 0.62
    assert mismatch.status == "name_mismatch"


def test_detail_title_verification_joins_character_tokens_from_the_title_roi() -> None:
    plan = StorageBagItemClickPlan("ready", "quantity sequence", point=(366.0, 625.0))

    verified = verify_storage_bag_item_detail(
        plan,
        expected_name="万灵自选宝匣",
        detail_title_texts=("万", "灵", "自", "选", "宝", "匣"),
    )

    assert verified.confirmed
    assert verified.observed_name == "万灵自选宝匣"


def test_target_preparation_joins_runtime_instance_to_catalog_name_and_scroll_is_revalidated() -> None:
    snapshot = _snapshot([5, 1, 18, 42, 75, 3, 8, 13, 21])
    target = prepare_storage_bag_target(
        snapshot,
        base_id=102,
        catalog_cards_by_id={"102": {"name": "魔道法则", "type_name": "法则"}},
    )

    assert target.runtime_index == 2
    assert target.name == "魔道法则"
    assert plan_storage_bag_scroll(
        target_runtime_index=target.runtime_index,
        viewport_runtime_start=0,
        visible_cell_count=4,
    ).mode == "visible"
    directive = plan_storage_bag_scroll(
        target_runtime_index=20,
        viewport_runtime_start=0,
        visible_cell_count=4,
    )
    assert (directive.direction, directive.mode) == ("down", "coarse")
    assert "重新" in directive.reason


def test_target_preparation_can_resolve_one_exact_catalog_name() -> None:
    target = prepare_storage_bag_target_by_name(
        _snapshot([5, 1]),
        name="天雷竹",
        catalog_cards_by_id={
            "100": {"name": "蟠桃仙树", "type_name": "神物"},
            "101": {"name": "天雷竹", "type_name": "神物"},
        },
    )

    assert (target.base_id, target.runtime_index, target.name) == (101, 1, "天雷竹")


def test_target_preparation_refuses_an_ambiguous_catalog_name() -> None:
    try:
        prepare_storage_bag_target_by_name(
            _snapshot([5, 1]),
            name="同名物品",
            catalog_cards_by_id={
                "100": {"name": "同名物品"},
                "101": {"name": "同名物品"},
            },
        )
    except ValueError as exc:
        assert "2 个 base_id" in str(exc)
    else:
        raise AssertionError("同名 Catalog 目标不应被静默选中")


def test_unique_quantity_sequence_maps_runtime_target_to_cell_point() -> None:
    _grid, cells = _grid_and_cells()
    observations = (
        StorageBagQuantityObservation(visible_index=0, quantity=5, required=None, text="5", confidence=0.99),
        StorageBagQuantityObservation(visible_index=1, quantity=1, required=80, text="1/80", confidence=0.99),
        StorageBagQuantityObservation(visible_index=2, quantity=80, required=None, text="80", confidence=0.99),
        StorageBagQuantityObservation(visible_index=3, quantity=42, required=None, text="42", confidence=0.99),
    )

    plan = plan_storage_bag_item_click(
        _snapshot([5, 1, 80, 42, 75, 3]),
        target_base_id=102,
        cells=cells,
        observations=observations,
    )

    assert plan.ready
    assert plan.runtime_index == 2
    assert plan.viewport_runtime_start == 0
    assert plan.point == cells[2].point


def test_two_exact_deep_anchors_uniquely_register_adjacent_runtime_sequence() -> None:
    cells = _four_row_cells()
    observations = (
        StorageBagQuantityObservation(7, 60, None, "60", 0.99),
        StorageBagQuantityObservation(8, 2, None, "2", 0.99),
    )

    plan = plan_storage_bag_item_click(
        _deep_snapshot(),
        target_base_id=1001,
        cells=cells,
        observations=observations,
    )

    assert plan.ready
    assert plan.runtime_index == 110
    assert plan.viewport_runtime_start == 103
    assert plan.candidate_starts == (103,)
    assert plan.point == cells[7].point


def test_two_exact_deep_anchors_can_span_missing_ocr_cells() -> None:
    cells = _four_row_cells()
    observations = (
        StorageBagQuantityObservation(7, 60, None, "60", 0.99),
        StorageBagQuantityObservation(12, 195, None, "195", 0.99),
    )

    plan = plan_storage_bag_item_click(
        _deep_snapshot(),
        target_base_id=1001,
        cells=cells,
        observations=observations,
    )

    assert plan.ready
    assert plan.viewport_runtime_start == 103
    assert plan.point == cells[7].point


def test_two_anchor_fast_path_does_not_rank_and_sort_every_runtime_start(monkeypatch) -> None:
    cells = _four_row_cells()
    snapshot = _deep_snapshot(count=20_000, target_index=19_000)
    observations = (
        StorageBagQuantityObservation(7, 60, None, "60", 0.99),
        StorageBagQuantityObservation(12, 195, None, "195", 0.99),
    )

    monkeypatch.setattr(
        storage_bag_alignment,
        "_ranked_matching_starts",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("two-anchor path ranked all starts")),
    )
    plan = plan_storage_bag_item_click(
        snapshot,
        target_base_id=1001,
        cells=cells,
        observations=observations,
    )

    assert plan.ready
    assert plan.viewport_runtime_start == 18_993


def test_two_repeated_anchor_pairs_remain_ambiguous() -> None:
    cells = _four_row_cells()
    snapshot = _deep_snapshot()
    snapshot["items"][12]["num"] = 60
    snapshot["items"][17]["num"] = 195
    observations = (
        StorageBagQuantityObservation(7, 60, None, "60", 0.99),
        StorageBagQuantityObservation(12, 195, None, "195", 0.99),
    )

    plan = plan_storage_bag_item_click(
        snapshot,
        target_base_id=1001,
        cells=cells,
        observations=observations,
    )

    assert plan.status == "ambiguous_offset"
    assert plan.candidate_starts == (5, 103)


def test_single_anchor_and_anchor_outside_current_grid_fail_closed() -> None:
    cells = _four_row_cells()
    snapshot = _deep_snapshot()

    single = plan_storage_bag_item_click(
        snapshot,
        target_base_id=1001,
        cells=cells,
        observations=(StorageBagQuantityObservation(7, 60, None, "60", 0.99),),
    )
    outside = plan_storage_bag_item_click(
        snapshot,
        target_base_id=1001,
        cells=cells,
        observations=(
            StorageBagQuantityObservation(7, 60, None, "60", 0.99),
            StorageBagQuantityObservation(16, 195, None, "195", 0.99),
        ),
    )

    assert single.status == "insufficient_observations"
    assert outside.status == "insufficient_observations"
    assert "当前完整可点击视窗" in outside.reason


def test_one_ocr_quantity_error_can_be_outvoted_by_a_unique_sequence() -> None:
    _grid, cells = _grid_and_cells()
    observations = tuple(
        StorageBagQuantityObservation(index, number, None, str(number), 0.99)
        for index, number in enumerate((5, 1, 780, 42, 75))
    )

    plan = plan_storage_bag_item_click(
        _snapshot([5, 1, 18, 42, 75, 3, 8]),
        target_base_id=102,
        cells=cells,
        observations=observations,
    )

    assert plan.ready
    assert "4/5" in plan.reason


def test_repeated_quantity_sequence_refuses_to_choose_a_runtime_offset() -> None:
    _grid, cells = _grid_and_cells()
    observations = tuple(
        StorageBagQuantityObservation(index, number, None, str(number), 0.99)
        for index, number in enumerate((5, 1, 80))
    )

    plan = plan_storage_bag_item_click(
        _snapshot([5, 1, 80, 42, 5, 1, 80, 9]),
        target_base_id=103,
        cells=cells,
        observations=observations,
    )

    assert plan.status == "ambiguous_offset"
    assert plan.candidate_starts == (0, 4)


def test_target_outside_registered_viewport_never_gets_a_click_point() -> None:
    _grid, cells = _grid_and_cells()
    observations = tuple(
        StorageBagQuantityObservation(index, number, None, str(number), 0.99)
        for index, number in enumerate((5, 1, 80, 42))
    )

    plan = plan_storage_bag_item_click(
        _snapshot([5, 1, 80, 42, 75, 3, 8, 13, 21]),
        target_base_id=108,
        cells=cells,
        observations=observations,
    )

    assert plan.status == "target_not_visible"
    assert plan.point is None


def test_scrolled_viewport_reuses_runtime_snapshot_but_reregisters_ocr_offset() -> None:
    """A drag changes only visible geometry, never the captured Runtime order."""

    _grid, cells = _grid_and_cells()
    snapshot = _snapshot([11, 12, 13, 14, 15, 16, 17, 18, 19, 20])

    before_drag = plan_storage_bag_item_click(
        snapshot,
        target_base_id=108,
        cells=cells,
        observations=tuple(
            StorageBagQuantityObservation(index, number, None, str(number), 0.99)
            for index, number in enumerate((11, 12, 13, 14))
        ),
    )
    after_drag = plan_storage_bag_item_click(
        snapshot,
        target_base_id=108,
        cells=cells,
        observations=tuple(
            StorageBagQuantityObservation(index, number, None, str(number), 0.99)
            for index, number in enumerate((15, 16, 17, 18))
        ),
    )

    assert before_drag.status == "target_not_visible"
    assert after_drag.ready
    assert after_drag.viewport_runtime_start == 4
    assert after_drag.point == cells[4].point


def test_complete_viewport_keeps_four_columns_and_excludes_partial_fifth_row() -> None:
    grid = StorageBagGrid(
        window=(0.0, 0.0, 400.0, 430.0),
        first_cell=(0.0, 0.0, 80.0, 80.0),
        columns=4,
        column_pitch=100.0,
        row_pitch=100.0,
    )
    viewport = StorageBagViewport(
        status="aligned",
        reason="test",
        row_centers=(40.0, 140.0, 240.0, 340.0, 440.0),
    )

    cells = visible_storage_bag_cells(grid, viewport)

    assert len(cells) == 16
    assert [(cell.visible_row, cell.column) for cell in cells[-4:]] == [
        (3, 0),
        (3, 1),
        (3, 2),
        (3, 3),
    ]
