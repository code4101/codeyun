"""Pure selection input contracts. Gameplay is validated in the real Kernel."""
import pytest
from backend.core.fanxiu.data_annotation.tasks import kunlun_secret as selection
from backend.core.fanxiu.data_annotation.tasks import kunlun_secret_jobs as jobs
from backend.core.fanxiu.activity.cultivation_choice import CultivationChoiceError

def _reward_items() -> list[dict[str, object]]:
    return [
        {"item_id": 100 + column, "name": f"道具{column}", "target_id": column}
        for column in range(1, 5)
    ]


def _owned_items() -> list[dict[str, object]]:
    return [
        {"target_id": column, "name": f"本体{column}", "rank": column, "weight": 2}
        for column in range(1, 5)
    ]


def test_first_row_requires_an_injected_evidence_based_selector() -> None:
    with pytest.raises(selection.KunlunFirstRowUndecided, match="尚未注入"):
        selection.decide_kunlun_first_row(
            _reward_items(), _owned_items(), selector=None
        )


def test_first_row_selector_receives_four_candidates_and_owned_progress() -> None:
    observed: dict[str, object] = {}

    def selector(candidates, owned):
        observed["candidates"] = candidates
        observed["owned"] = owned
        return selection.KunlunFirstRowDecision(column=3, reason="真实阶数/重数排序结果")

    decision = selection.decide_kunlun_first_row(
        _reward_items(), _owned_items(), selector=selector
    )

    assert decision.column == 3
    assert len(observed["candidates"]) == 4
    assert [item.rank for item in observed["owned"]] == [1, 2, 3, 4]


def test_runtime_reader_snapshot_is_required_complete(monkeypatch) -> None:
    monkeypatch.setattr(
        jobs,
        "read_kunlun_first_row_runtime",
        lambda: {"complete": False, "reason": "FashionMgr 尚未加载"},
    )
    with pytest.raises(selection.KunlunFirstRowUndecided, match="FashionMgr 尚未加载"):
        jobs.read_kunlun_first_row_inputs()


@pytest.mark.parametrize("layers, expected", [
    ([[3, 3], [30], [6], [5]], 1),
    ([[8], [5], [2], [4]], 3),
    ([[8], [2], [2], [4]], 2),
    ([[8], [2], [0], [4]], 3),
    ([[8, 1], [2], [3], [4]], 1),
])
def test_first_row_uses_lowest_layer_then_leftmost(layers, expected) -> None:
    owned = tuple({"item_id": 101 + index,
                   "components": [{"rank": rank} for rank in ranks]}
                  for index, ranks in enumerate(layers))
    inputs = jobs.KunlunFirstRowInputs(tuple(_reward_items()), tuple(reversed(owned)))
    assert jobs.plan_kunlun_first_row(inputs).column == expected


@pytest.mark.parametrize("rank", [None, -1, True, "0"])
def test_first_row_invalid_progress_is_not_zero(rank) -> None:
    inputs = jobs.KunlunFirstRowInputs(
        ({"item_id": 101},),
        ({"item_id": 101, "components": [{"rank": rank}]},),
    )
    with pytest.raises(CultivationChoiceError, match="层数无效"):
        jobs.plan_kunlun_first_row(inputs)


def test_first_row_missing_progress_stops_selection() -> None:
    inputs = jobs.KunlunFirstRowInputs(tuple(_reward_items()), ())
    with pytest.raises(CultivationChoiceError, match="缺失"):
        jobs.plan_kunlun_first_row(inputs)


def test_first_row_committed_selection_needs_no_progress() -> None:
    inputs = jobs.KunlunFirstRowInputs(
        tuple(_reward_items()), (), {"item_id": 103},
    )
    assert jobs.plan_kunlun_first_row(inputs).column == 3
