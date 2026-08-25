from backend.core.fanxiu.instrumentation import magic_invasion_task_rewards as rewards


ROWS = (
    {"id": 300201, "type": 3, "subType": 1, "activityId": 1070011, "sort": 300101, "name": "除魔一"},
    {"id": 300202, "type": 3, "subType": 1, "activityId": 1070011, "sort": 300102, "name": "除魔二"},
    {"id": 300208, "type": 3, "subType": 2, "activityId": 1070011, "sort": 10030002, "name": "修为一"},
    # Retained configuration variants must not enter the selected ladder
    # unless QuestMgr actually contains them.
    {"id": 300214, "type": 3, "subType": 1, "activityId": 1070011, "sort": 300101, "name": "除魔一新"},
    {"id": 800201, "type": 3, "subType": 1, "activityId": 8070001, "sort": 300101, "name": "跨服除魔一"},
)


def _entry(task_id: int, *, status: int, reward_time: int = 0) -> dict:
    return {
        "taskId": task_id,
        "status": status,
        "turn": 1,
        "rewardTime": reward_time,
        "progressList": [{"finish": status >= 4}],
    }


def test_snapshot_selects_only_live_versioned_magic_ladder(monkeypatch) -> None:
    monkeypatch.setattr(rewards, "_active_task_rows", lambda: ROWS)

    snapshot = rewards.build_magic_invasion_task_reward_snapshot(
        activity_id=1070011,
        task_entries=[
            _entry(300201, status=5, reward_time=1),
            _entry(300202, status=4),
            _entry(300208, status=4),
            _entry(800201, status=4),
        ],
        finished_task_ids=[300201],
    )

    assert snapshot["complete"] is True
    assert snapshot["claimed_task_ids"] == [300201]
    assert snapshot["authorized_claim_task_ids"] == [300202, 300208]
    assert snapshot["task_subtypes"] == {
        "300201": 1,
        "300202": 1,
        "300208": 2,
    }


def test_snapshot_fails_closed_when_magic_tasks_not_loaded(monkeypatch) -> None:
    monkeypatch.setattr(rewards, "_active_task_rows", lambda: ROWS)

    snapshot = rewards.build_magic_invasion_task_reward_snapshot(
        activity_id=1070011,
        task_entries=[_entry(800201, status=4)],
        finished_task_ids=[],
    )

    assert snapshot["available"] is False
    assert snapshot["state"] == "unavailable"


def test_snapshot_fails_closed_when_one_logical_slot_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(rewards, "_active_task_rows", lambda: ROWS)

    snapshot = rewards.build_magic_invasion_task_reward_snapshot(
        activity_id=1070011,
        task_entries=[_entry(300201, status=4), _entry(300208, status=4)],
        finished_task_ids=[],
    )

    assert snapshot["available"] is False
    assert "未完整加载" in snapshot["reason"]


def test_snapshot_accepts_fully_claimed_ladder_from_finish_tasks(monkeypatch) -> None:
    monkeypatch.setattr(rewards, "_active_task_rows", lambda: ROWS)

    snapshot = rewards.build_magic_invasion_task_reward_snapshot(
        activity_id=1070011,
        task_entries=[],
        finished_task_ids=[300201, 300202, 300208],
    )

    assert snapshot["complete"] is True
    assert snapshot["state"] == "already_claimed"
    assert snapshot["claimed_task_ids"] == [300201, 300202, 300208]


def test_snapshot_rejects_two_versions_of_same_logical_slot(monkeypatch) -> None:
    monkeypatch.setattr(rewards, "_active_task_rows", lambda: ROWS)

    snapshot = rewards.build_magic_invasion_task_reward_snapshot(
        activity_id=1070011,
        task_entries=[
            _entry(300201, status=4),
            _entry(300214, status=4),
            _entry(300202, status=4),
            _entry(300208, status=4),
        ],
        finished_task_ids=[],
    )

    assert snapshot["available"] is False
    assert snapshot["state"] == "ambiguous"
