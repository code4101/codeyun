"""Historical delivery evidence never authorizes a new reset action."""
from copy import deepcopy

import pytest

from backend.core.fanxiu.instrumentation.xianyuan_gift_page import (
    require_free_single_step_npc_reset,
    validate_free_single_step_npc_reset_snapshot,
)
from backend.core.fanxiu.data_annotation.tasks.xianyuan_gift_gui import npc_reset_guide_from_tokens


def test_observed_guide_requires_both_adjacent_unique_lines():
    # Real 2026-10-08 failure frame: these lines were absent from scene OCR.
    tokens = [
        {"parent_line_id": "line-8", "order": 0,
         "text": "看来你拿到了此人所有的赠礼，我可",
         "x": 405, "y": 778, "w": 448, "h": 27},
        {"parent_line_id": "line-9", "order": 0,
         "text": "以为你重置此人赠礼。",
         "x": 408, "y": 810, "w": 259, "h": 30},
    ]
    assert npc_reset_guide_from_tokens(tokens)["point"] == [537.5, 825]
    assert npc_reset_guide_from_tokens(tokens[:1]) is None
    separated = deepcopy(tokens)
    separated[1]["y"] = 1200
    assert npc_reset_guide_from_tokens(separated) is None
    ambiguous = deepcopy(tokens)
    duplicate = dict(tokens[1], parent_line_id="other-line")
    ambiguous.append(duplicate)
    with pytest.raises(RuntimeError, match="不唯一"):
        npc_reset_guide_from_tokens(ambiguous)


def test_historical_receipt_validates_but_cannot_authorize_reset():
    process = {"pid": 2769, "process_start_ticks": 5126}
    person = {"npc_id": 4005, "rewards": [{"state": 2}]}
    bag = {
        "complete": True, "npc_id": 4005, "reset_steps": [1],
        "remaining_reset_rewards": 0, "reset_cost_quantity": 0,
        "is_enough": True, "captured_at": 1, "evidence": process,
    }
    assert validate_free_single_step_npc_reset_snapshot(
        bag, person, expected_process=process
    ) is bag
    with pytest.raises(RuntimeError, match="观察已过期"):
        require_free_single_step_npc_reset(bag, person, expected_process=process)
    for field, value in (("reset_cost_quantity", 1), ("remaining_reset_rewards", 1),
                         ("npc_id", 4013), ("reset_steps", [1, 2])):
        changed = deepcopy(bag)
        changed[field] = value
        with pytest.raises(RuntimeError):
            validate_free_single_step_npc_reset_snapshot(
                changed, person, expected_process=process
            )
    with pytest.raises(RuntimeError, match="进程身份变化"):
        validate_free_single_step_npc_reset_snapshot(
            bag, person, expected_process={"pid": 2769, "process_start_ticks": 9999}
        )
    with pytest.raises(RuntimeError, match="尚未全部领取"):
        validate_free_single_step_npc_reset_snapshot(
            bag, {"npc_id": 4005, "rewards": [{"state": 1}]}, expected_process=process
        )
