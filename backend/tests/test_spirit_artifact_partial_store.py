"""局部保存的确定性存储契约；不模拟游戏交互。"""
import pytest
from sqlmodel import Session, create_engine
from backend.models import FanxiuPacketBusinessRecord
from backend.core.fanxiu.instrumentation.spirit_artifact_store import (
    project_spirit_artifact_part_update, upsert_spirit_artifact_runtime_snapshot,
    update_spirit_artifact_runtime_part, load_spirit_artifact_runtime_snapshot,
)


def data():
    part = dict(item_id="item", ware_id=1, part=4, base_id=14000406, pid=1,
                process_start_ticks=2, is_break=True, grade=10, realm=0, refine_num=0,
                pending_effects=[], effects=[dict(cleanse_id=i+1, value=100, quality=7,
                    locked=False, code="CODE"+str(i), name="属性"+str(i), affix="满", normal_max=100)
                    for i in range(6)])
    row = dict(runtime_item_id="item", runtime_ware_id=1, runtime_part=4,
               runtime_base_id=14000406, part_name="鞘")
    snapshot = dict(runtime_updated_at=10., runtime_complete=True, runtime_equipped_count=2,
                    runtime_debug={"pid":99}, artifacts=[dict(name="血晶摩诃剑", rows=[row, {"untouched":True}])])
    return snapshot, part


def test_partial_update_preserves_original_observations_and_rejects_stale_or_wrong_target():
    snapshot, part = data()
    updated = project_spirit_artifact_part_update(snapshot, part, observed_at=20.)
    assert updated["runtime_updated_at"] == 10.
    assert updated["runtime_debug"] == {"pid":99}
    assert updated["artifacts"][0]["rows"][1] == {"untouched":True}
    assert "runtime_observation" not in snapshot["artifacts"][0]["rows"][0]
    assert updated["artifacts"][0]["rows"][0]["runtime_observation"]["observed_at"] == 20.
    for changed, stamp in [({**part, "item_id":"wrong"}, 21.), (part, 19.),
                           ({**part, "effects":part["effects"][:-1]}, 21.)]:
        with pytest.raises(ValueError):
            project_spirit_artifact_part_update(updated, changed, observed_at=stamp)


def test_partial_save_cas_does_not_overwrite_concurrent_update(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "facts.sqlite"))
    FanxiuPacketBusinessRecord.__table__.create(engine)
    snapshot, part = data()
    with Session(engine, expire_on_commit=False) as first, Session(engine) as second:
        held = upsert_spirit_artifact_runtime_snapshot(first, snapshot)
        update_spirit_artifact_runtime_part(second, part, observed_at=20.)
        with pytest.raises(RuntimeError, match="并发"):
            update_spirit_artifact_runtime_part(first, part, observed_at=21.)
        assert held.id is not None
        saved = load_spirit_artifact_runtime_snapshot(second)
        assert saved["runtime_partial_updated_at"] == 20.


def replacement_data():
    snapshot, part = data()
    part = {**part, 'item_id': 'new', 'is_break': False, 'grade': 6}
    equipped = dict(complete=True, source='spiritware_server_put_up_set', pid=1,
        process_start_ticks=2, captured_at=21.,
        slots=[dict(ware_id=1, part=4, item_id='new')],
        items=[dict(ware_id=1, part=4, item_id='new', base_id=14000406, equipped=True)])
    return snapshot, part, equipped


def test_replace_part_saves_verified_identity_and_preserves_other_rows(tmp_path):
    snapshot, part, equipped = replacement_data()
    engine = create_engine('sqlite:///' + str(tmp_path / 'replace.sqlite'))
    FanxiuPacketBusinessRecord.__table__.create(engine)
    with Session(engine) as session:
        upsert_spirit_artifact_runtime_snapshot(session, snapshot)
        saved = update_spirit_artifact_runtime_part(session, part, observed_at=20.,
            expected_previous_item_id='item', equipped_snapshot=equipped)
        row = saved['artifacts'][0]['rows'][0]
        assert row['runtime_item_id'] == 'new'
        assert row['runtime_observation']['replacement']['previous_item_id'] == 'item'
        assert saved['artifacts'][0]['rows'][1] == snapshot['artifacts'][0]['rows'][1]
        assert saved['runtime_updated_at'] == 10.
        with pytest.raises(ValueError, match='expected_previous_item_id'):
            update_spirit_artifact_runtime_part(session, part, observed_at=20.,
                expected_previous_item_id='item', equipped_snapshot=equipped)


@pytest.mark.parametrize('change', ['missing', 'process', 'stale', 'incomplete',
    'slot', 'duplicate', 'other_part', 'base', 'unequipped', 'old_uid'])
def test_replace_part_rejects_unproven_equipment(change):
    snapshot, part, equipped = replacement_data()
    expected = 'item'
    if change == 'missing': equipped = None
    elif change == 'process': equipped['pid'] = 9
    elif change == 'stale': equipped['captured_at'] = 19.
    elif change == 'incomplete': equipped['complete'] = False
    elif change == 'slot': equipped['slots'][0]['item_id'] = 'other'
    elif change == 'duplicate': equipped['slots'].append(dict(equipped['slots'][0]))
    elif change == 'other_part': equipped['items'][0]['part'] = 3
    elif change == 'base': equipped['items'][0]['base_id'] = 14000306
    elif change == 'unequipped': equipped['items'][0]['equipped'] = False
    elif change == 'old_uid': expected = 'wrong'
    with pytest.raises(ValueError):
        project_spirit_artifact_part_update(snapshot, part, observed_at=20.,
            expected_previous_item_id=expected, equipped_snapshot=equipped)
