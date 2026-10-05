"""实验身份、目录版本和幂等持久化契约；使用真实SQLModel内存数据库。"""
from copy import deepcopy

import pytest
from sqlmodel import Session,create_engine

from backend.models import FanxiuPacketBusinessRecord
from backend.core.fanxiu.storage_bag_prayer_research import (
    BOX_EXPERIMENT_DOMAIN, persist_prayer_box_rule, record_prayer_box_experiment,
    record_fixed_box_non_prayer_conclusion,validate_prayer_box_experiment,
)


def experiment():
    before = dict(complete=True,activity_key="flower-week-A",current_round=1,
                  total_rounds=5,current_score=10,round_target_score=100)
    return dict(action_key="open1",base_id=1,opened_count=2,catalog_fingerprint="v1",
                isolated=True,activity_key="flower-week-A",prayer_category="仙花",
                prayer_before=before,prayer_after={**before,"current_score":30})


def test_complete_atomic_observation_computes_only_this_action():
    e=experiment()
    out=validate_prayer_box_experiment(e)
    assert out["score_delta"] == 20
    assert out["score_per_box"] == 10
    assert "score_delta" not in e


@pytest.mark.parametrize("change",[
    {"activity_key":"flower-week-B"}, {"complete":False}, {"complete":1},
    {"current_round":2}, {"total_rounds":6}, {"round_target_score":200},
    {"current_score":9}, {"current_score":True},
])
def test_incomplete_cross_activity_round_or_rules_are_rejected(change):
    e=experiment();e["prayer_after"].update(change)
    with pytest.raises(ValueError):validate_prayer_box_experiment(e)


def test_missing_activity_on_observation_is_not_inferred_from_outer_label():
    e=experiment();del e["prayer_after"]["activity_key"]
    with pytest.raises(ValueError,match="同一活动"):validate_prayer_box_experiment(e)


def test_new_experiment_never_infers_catalog_fingerprint_at_recording_time():
    e=experiment();del e["catalog_fingerprint"]
    with pytest.raises(ValueError,match="目录版本"):validate_prayer_box_experiment(e)


def database():
    engine=create_engine("sqlite://")
    FanxiuPacketBusinessRecord.__table__.create(engine)
    return engine


def test_same_receipt_can_replay_but_changed_fact_cannot_replace_it():
    with Session(database()) as session:
        e=experiment();saved=record_prayer_box_experiment(session,e)
        assert record_prayer_box_experiment(session,e)==saved
        conflict=deepcopy(e);conflict["prayer_after"]["current_score"]=31
        with pytest.raises(ValueError,match="事实冲突"):
            record_prayer_box_experiment(session,conflict)


def test_old_zero_score_cannot_be_applied_to_new_catalog():
    with Session(database()) as session:
        e=experiment();e["prayer_after"]["current_score"]=10
        record_prayer_box_experiment(session,e)
        persist_prayer_box_rule(session,dict(base_id=1,catalog_fingerprint="v2",
            operation_kind="fixed",classification_complete=True,resource_categories=["仙花"],
            reward_leaves=[dict(item_id=3,count_path_product=1)]))
        with pytest.raises(ValueError,match="相同目录版本"):
            record_fixed_box_non_prayer_conclusion(session,action_key="open1")


def test_exact_legacy_receipt_replay_preserves_existing_fact():
    e=experiment()
    for observation in (e["prayer_before"],e["prayer_after"]):del observation["activity_key"]
    saved={**e,"score_delta":20,"score_per_box":10}
    with Session(database()) as session:
        session.add(FanxiuPacketBusinessRecord(domain=BOX_EXPERIMENT_DOMAIN,record_key="open1",payload=saved))
        session.commit()
        assert record_prayer_box_experiment(session,e)==saved
