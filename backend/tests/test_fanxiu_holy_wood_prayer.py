from __future__ import annotations

from pathlib import Path

import pytest

from backend.core.fanxiu.data_annotation.default_jobs import (
    register_fanxiu_data_annotation_default_runtime_jobs,
)
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
)
from backend.core.fanxiu.data_annotation.tasks.holy_wood_prayer import (
    HOLY_WOOD_DEFAULT_SPEND_BUDGET,
    HOLY_WOOD_TASK_TYPE,
    execute_holy_wood_prayer_task,
    parse_holy_wood_ticket_draws,
    validate_holy_wood_store_increment,
)


def _snapshot(first: int, second: int) -> dict:
    return {
        "items": [
            {"id": 3040201, "purchased_times": first},
            {"id": 3040202, "purchased_times": second},
            {"id": 3040203, "purchased_times": 0},
        ]
    }


def test_ticket_parser_accepts_one_or_two_exact_counters() -> None:
    assert parse_holy_wood_ticket_draws("27/1", cost_per_draw=1) == 27
    assert parse_holy_wood_ticket_draws("27/1 0/1", cost_per_draw=1) == 27
    assert parse_holy_wood_ticket_draws("27/1 0/10", cost_per_draw=1) is None
    assert parse_holy_wood_ticket_draws("没有稳定计数", cost_per_draw=1) is None


def test_store_increment_requires_one_offer_and_exact_wallet_delta() -> None:
    validate_holy_wood_store_increment(
        _snapshot(0, 0),
        _snapshot(1, 0),
        offer_id=3040201,
        unit_cost=488,
        wallet_before=10000,
        wallet_after=9512,
    )
    with pytest.raises(RuntimeError, match="购买增量异常"):
        validate_holy_wood_store_increment(
            _snapshot(0, 0),
            _snapshot(1, 1),
            offer_id=3040201,
            unit_cost=488,
            wallet_before=10000,
            wallet_after=9512,
        )
    with pytest.raises(RuntimeError, match="灵石扣减异常"):
        validate_holy_wood_store_increment(
            _snapshot(0, 0),
            _snapshot(1, 0),
            offer_id=3040201,
            unit_cost=488,
            wallet_before=10000,
            wallet_after=9000,
        )


def test_holy_wood_prayer_is_one_manual_standard_job() -> None:
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition(HOLY_WOOD_TASK_TYPE)
    assert definition is not None
    assert definition.standard_job is True
    assert definition.standard_job_id == "holy-wood-prayer"
    assert definition.standard_job_description == "手动"
    assert definition.standard_job_payload == {
        "spend_budget": HOLY_WOOD_DEFAULT_SPEND_BUDGET,
        "max_task_clicks": 20,
        "max_draw_rounds": 64,
    }


def test_holy_wood_public_job_first_normalizes_to_world() -> None:
    class Runtime:
        def go_scene(self, target):
            yield ("go_scene", target)

    class Runner:
        @staticmethod
        def _fanxiu_runtime(*_args, **_kwargs):
            return Runtime()

    generator = execute_holy_wood_prayer_task(
        Runner(),
        {"asset_tree_path": Path("asset-tree.json")},
        {},
        type("StopEvent", (), {"is_set": lambda self: False})(),
    )
    assert next(generator) == ("go_scene", 34)
    generator.close()
