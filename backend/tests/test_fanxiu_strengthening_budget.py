import pytest

from backend.core.fanxiu.data_annotation.equipment import strengthening_overshoot_limit


@pytest.mark.parametrize("target, percent, expected", [(8000, 5, 400), (8000, 0, 0), (99, 5, 4)])
def test_overshoot_limit(target, percent, expected):
    assert strengthening_overshoot_limit(target, percent) == expected


@pytest.mark.parametrize("target, percent", [(0, 5), (8000, -1), (8000, 101)])
def test_invalid_overshoot_limit(target, percent):
    with pytest.raises(ValueError):
        strengthening_overshoot_limit(target, percent)


@pytest.mark.parametrize("continuation", ["none", "matching", "changed_level", "changed_process"])
def test_insufficient_batch_stops_before_click(monkeypatch, continuation):
    from backend.core.fanxiu.activity import lingzhuang_strengthening as snapshots
    from backend.core.fanxiu.data_annotation import equipment

    class Context:
        def wait_scene_exact(self, *args, **kwargs):
            yield None

        def click_shape(self, *args, **kwargs):
            pytest.fail("批次资源不足时不能点击强化")

    snapshot = snapshots.LingzhuangStrengtheningSnapshot(
        activity_id="current", game_task_activity_id=16044301, complete=True,
        evidence={"pid": 7, "process_start_ticks": 1},
        equipment_current=300,
        rows=[snapshots.LingzhuangStrengtheningRow(
            part="气铠", initial=snapshots.LingzhuangStrengtheningSide(
                material_id=1, material_name="气铠玄铁",
                equipped=True, equipment_level=172, equipment_raw_level=1548,
                material_count=200,
            ),
            dongxuan=snapshots.LingzhuangStrengtheningSide(material_id=2, material_name="洞玄气铠玄铁"),
        )],
    )
    from types import SimpleNamespace
    from backend.core.fanxiu.instrumentation.runtime_memory import MumuProcessMemory
    monkeypatch.setattr(MumuProcessMemory, "discover_cached",
                        lambda: SimpleNamespace(pid=8 if continuation == "changed_process" else 7,
                                                process_start_ticks=1))
    fresh = snapshot.model_copy(deep=True)
    level = 173 if continuation == "changed_level" else 172
    fresh.rows[0].initial.equipment_level = level
    fresh.rows[0].initial.equipment_raw_level = level * 9
    reads = []

    def read(**kwargs):
        reads.append(True)
        return fresh.model_dump()

    monkeypatch.setattr(snapshots, "read_lingzhuang_strengthening_runtime_snapshot", read)
    monkeypatch.setattr(equipment, "read_selected_equipment_strengthening",
                        lambda context: equipment.EquipmentStrengtheningObservation(
                            description_text="初灵气铠172级", resource_text="200/400",
                            equipment_level=level, resource_current=200, resource_required=400,
                        ))
    with pytest.raises(equipment.EquipmentStrengtheningBatchUnavailable, match="未点击"):
        list(equipment.strengthen_selected_equipment_once(
            Context(), activity_id="current", category="初灵", part="气铠",
            previous_snapshot=None if continuation == "none" else snapshot,
        ))
    assert len(reads) == (0 if continuation == "matching" else 1)
