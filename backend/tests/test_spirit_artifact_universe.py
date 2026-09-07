import pytest
from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_cleanse import (
    validate_spirit_artifact_target_universe, SpiritArtifactCleanseBlocked,
)


def snapshot():
    return dict(runtime_complete=True, runtime_equipped_count=0,
        artifacts=[dict(order=9, rows=[dict(runtime_ware_id=9, runtime_part=i,
            runtime_item_id="", runtime_base_id=0, runtime_empty_slot=True, runtime_effects=[])
            for i in range(1,7)])])


def test_loaded_ninth_ware_accepts_six_proven_empty_slots():
    validate_spirit_artifact_target_universe(snapshot())


@pytest.mark.parametrize("case", ["missing", "unknown_empty", "contradiction", "count"])
def test_loaded_universe_rejects_missing_or_conflicting_facts(case):
    value = snapshot()
    rows = value["artifacts"][0]["rows"]
    if case == "missing": rows.pop()
    elif case == "unknown_empty": rows[0].pop("runtime_empty_slot")
    elif case == "contradiction": rows[0]["runtime_item_id"] = "exists"
    else: value["runtime_equipped_count"] = 1
    with pytest.raises(SpiritArtifactCleanseBlocked):
        validate_spirit_artifact_target_universe(value)
