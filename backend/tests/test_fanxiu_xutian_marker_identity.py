"""Immutable transaction identity safety, independent of GUI and Runtime."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from backend.core.fanxiu.data_annotation.tasks.xutian_native_auto import (
    _xutian_occurrence_identity, _validate_pending_marker,
)


def test_marker_binds_season_and_currency_not_rank_observation():
    activity = SimpleNamespace(
        id="season", evidence={"game_activity_id": 8080001,
        "period_record_id": "cursor-new", "period_start_time": 1790042400000,
        "period_end_time": 1790258400000, "period_close_panel_time": 1790351939000},
        cross_count=8, start_date="2026-09-22", end_date="2026-09-24",
        game_rank_activity_id=80851, game_shop_base_id=80000, currency_type=12,
    )
    marker = {"batch_id": "batch", "occurrence": _xutian_occurrence_identity(activity),
              "runtime_batch_identity": {"pid": 2618, "process_start_ticks": 5651,
                                         "current_heaven": 90003}}
    marker["occurrence"].update(runtime_record_id="cursor-old", game_rank_activity_id=80891)
    assert _validate_pending_marker(activity, marker) == marker
    for key in ("game_activity_id", "cross_count", "period_end_time", "currency_type"):
        wrong = deepcopy(marker)
        wrong["occurrence"][key] += 1
        with pytest.raises(RuntimeError, match="身份不一致"):
            _validate_pending_marker(activity, wrong)
