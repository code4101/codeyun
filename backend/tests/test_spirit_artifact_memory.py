from backend.core.fanxiu.instrumentation.spirit_artifact_memory import SpiritArtifactMemory


def test_confirmed_identical_candidate_deducts_once_and_survives_catalog_lookup():
    memory = SpiritArtifactMemory()
    memory.remember_catalog(dict(pid=1, process_start_ticks=2, item_id='part',
                                 items=[dict(item=6, count=2)]))
    effect = dict(cleanse_id=1, value=10, quality=6, locked=False)
    before = dict(pid=1, process_start_ticks=2, item_id='part', effects=[effect],
                  pending_effects=[effect], pending_revision=[('1', 100)])
    after = dict(before, pending_revision=[('1', 200)])
    assert memory.confirm_use(6, before, after) == 1
    assert memory.confirm_use(6, before, after) == 1
    assert memory.catalog(after)['items'][0]['count'] == 1
    import pytest
    # 下一次动作没有新版本，不能冒充上次回执并再次记作成功消耗。
    with pytest.raises(RuntimeError):
        memory.confirm_use(6, after, after)


def test_unconfirmed_result_invalidates_without_guessing_consumption():
    import pytest
    memory = SpiritArtifactMemory()
    memory.remember_catalog(dict(pid=1, process_start_ticks=2, item_id='part',
                                 items=[dict(item=6, count=2)]))
    effect = dict(cleanse_id=1, value=10, quality=6, locked=False)
    state = dict(pid=1, process_start_ticks=2, item_id='part', effects=[effect],
                 pending_effects=[effect], pending_revision=[('1', 100)])
    with pytest.raises(RuntimeError):
        memory.confirm_use(6, state, state)
    assert memory.catalog(state) is None


def test_process_change_invalidates_catalog():
    memory = SpiritArtifactMemory()
    memory.remember_catalog(dict(pid=1, process_start_ticks=2, item_id='part', items=[]))
    assert memory.catalog(dict(pid=1, process_start_ticks=3, item_id='part')) is None


def test_known_target_survives_read_boundaries_but_not_navigation():
    from backend.core.fanxiu.instrumentation.spirit_artifact_wash_observation import SpiritArtifactWashTarget
    memory = SpiritArtifactMemory()
    target = SpiritArtifactWashTarget('part', 1, 2, (1, 2))
    state = dict(pid=1, process_start_ticks=2, item_id='part', ware_id=1, part=2,
                 is_wash=True, effects=[], pending_effects=[])
    memory.remember_snapshot(state)
    assert memory.selected_ware == 1
    assert memory.snapshot(target)['observation_source'] == 'kernel_model'
    memory.remember_snapshot({k: v for k, v in state.items() if k != 'is_wash'})
    assert memory.snapshot(target) is not None
    memory.navigation_started()
    assert memory.selected_ware is None
    assert memory.snapshot(target) is None
