from __future__ import annotations

import numpy as np
import pytest

from backend.core.fanxiu.runtime_gui.storage_bag_grid import (
    StorageBagGrid,
    register_storage_bag_viewport,
)


def _shapes() -> dict[str, dict[str, float]]:
    return {
        "窗口": {"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8},
        "第1行第1个": {"x": 0.1, "y": 0.1, "w": 0.18, "h": 0.16},
        "第1行第2个": {"x": 0.31, "y": 0.1, "w": 0.18, "h": 0.16},
        "第2行第1个": {"x": 0.1, "y": 0.29, "w": 0.18, "h": 0.16},
        "行间隙": {"x": 0.11, "y": 0.49, "w": 0.78, "h": 0.02},
    }


def _frame_with_gaps(*, height: int, width: int, starts: tuple[int, ...]) -> np.ndarray:
    frame = np.full((height, width, 3), 218, dtype=np.uint8)
    pattern = np.arange(12 * 156, dtype=np.uint8).reshape(12, 156)
    for top in starts:
        frame[top:top + 12, 22:178] = np.stack([pattern, 255 - pattern, pattern // 2], axis=2)
    return frame


def test_grid_is_derived_from_three_reference_cells() -> None:
    grid = StorageBagGrid.from_shapes(_shapes(), frame_width=200, frame_height=400)

    assert grid.columns == 4
    assert grid.column_pitch == pytest.approx(42.0)
    assert grid.row_pitch == pytest.approx(76.0)
    assert grid.point(row=1, column=3) == pytest.approx((164.0, 148.0))


def test_gap_registration_rebuilds_scrolled_row_centres_without_global_index() -> None:
    shapes = _shapes()
    grid = StorageBagGrid.from_shapes(shapes, frame_width=200, frame_height=400)
    reference = _frame_with_gaps(height=400, width=200, starts=(94, 170, 196, 246, 322))
    # The current viewport is vertically shifted by -17px.  The repeated gap
    # texture has no item identity, so this test only asserts current geometry.
    current = _frame_with_gaps(height=400, width=200, starts=(77, 153, 179, 229, 305))

    viewport = register_storage_bag_viewport(
        reference,
        current,
        grid=grid,
        row_gap_shape=shapes["行间隙"],
        minimum_score=0.8,
    )

    assert viewport.aligned
    assert len(viewport.gap_matches) >= 2
    assert viewport.row_centers == tuple(sorted(viewport.row_centers))
    assert viewport.point(grid, visible_row=1, column=2)[0] == pytest.approx(122.0)
    with pytest.raises(ValueError, match="可见行索引"):
        viewport.point(grid, visible_row=99, column=0)


def test_gap_registration_fails_closed_without_repeated_gap_evidence() -> None:
    shapes = _shapes()
    grid = StorageBagGrid.from_shapes(shapes, frame_width=200, frame_height=400)
    reference = _frame_with_gaps(height=400, width=200, starts=(196,))
    current = np.full((400, 200, 3), 218, dtype=np.uint8)

    viewport = register_storage_bag_viewport(
        reference,
        current,
        grid=grid,
        row_gap_shape=shapes["行间隙"],
        minimum_score=0.8,
    )

    assert viewport.status == "insufficient_geometry"
