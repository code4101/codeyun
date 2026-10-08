from dataclasses import replace

import pytest

from backend.core.fanxiu.data_annotation import kernel_scheduler_control as control
from backend.core.fanxiu.data_annotation.tasks.storage_bag_choice_box import (
    StorageBagChoiceBoxBlocked, StorageBagChoiceBoxDelta, StorageBagChoiceBoxExecution,
    StorageBagChoiceBoxRequest, StorageBagChoiceReward, record_storage_bag_choice_execution,
)


def execution():
    return StorageBagChoiceBoxExecution(
        StorageBagChoiceBoxRequest(37092003, 'instance', 'box', 21, 'named'),
        StorageBagChoiceReward(1, 16000003, 'reward', 10),
        StorageBagChoiceBoxDelta(21, 16000003, 210, 'before', 'after'),
        'box', 1.0, 16,
        transaction_evidence={'process_identity': [2678, 6022], 'box_before': 21,
                              'box_after': 0, 'reward_before': 239, 'reward_after': 449},
    )


def test_measured_choice_receipt_persists_and_reentry_never_adds_another_fact(tmp_path, monkeypatch):
    read = control.read_world_facts
    record = control.record_world_discovery
    path = tmp_path / 'facts.json'
    monkeypatch.setattr(control, 'read_world_facts', lambda: read(path))
    monkeypatch.setattr(control, 'record_world_discovery', lambda key, value: record(key, value, path))
    original = execution()
    key = record_storage_bag_choice_execution(original)
    assert record_storage_bag_choice_execution(original) == key
    facts = read(path)['discoveries']
    assert [entry for entry in facts if entry.startswith('storage-bag-choice:')] == [key]
    assert facts[key]['delta']['reward_quantity'] == 210
    assert facts[key]['transaction_evidence']['reward_before'] == 239
    assert facts[key]['transaction_evidence']['reward_after'] == 449
    with pytest.raises(StorageBagChoiceBoxBlocked, match='冲突'):
        record_storage_bag_choice_execution(replace(original, delta=replace(original.delta, reward_quantity=211)))


def test_action_only_choice_is_not_promoted_to_measured_delivery():
    assert record_storage_bag_choice_execution(replace(execution(), delta=None)) is None


def test_measured_delivery_requires_original_native_evidence():
    with pytest.raises(StorageBagChoiceBoxBlocked, match='前后证据'):
        execution_with_no_evidence = replace(execution(), transaction_evidence=None)
        record_storage_bag_choice_execution(execution_with_no_evidence)
