"""邮件授权、删除保护及终态计数的纯领域契约。"""

import pytest
from datetime import datetime

from backend.core.fanxiu.mail.claim_contract import (
    claimable_mail_targets,
    deletable_mail_garbage,
    mail_target_reward_item_ids,
    protected_mail_ids,
    select_mail_claim_targets,
    validate_mail_terminal_result,
    classify_mail_batch_remainder,
)


def test_explicit_authorization_never_overrides_lock_or_completed_state():
    rows = [
        dict(id="claim", present_in_runtime=True, execution_status="unclaimed", action_policy="claim"),
        dict(id="retained", present_in_runtime=True, execution_status="unclaimed"),
        dict(id="locked", present_in_runtime=True, execution_status="unclaimed", locked=True),
        dict(id="done", present_in_runtime=True, execution_status="claimed"),
        dict(id="absent", present_in_runtime=False, execution_status="unclaimed"),
    ]
    snapshot = {"items": rows}
    assert [x["id"] for x in claimable_mail_targets(snapshot)] == ["claim"]
    assert [x["id"] for x in claimable_mail_targets(
        snapshot, protected_claim_authorizer=lambda item: True,
    )] == ["claim", "retained"]
    assert select_mail_claim_targets(snapshot, {"locked", "retained"}) == []


def test_delete_candidates_require_claimed_or_authoritatively_read_empty_mail():
    rows = [
        dict(id="claimed", execution_status="claimed"),
        dict(id="read-empty", execution_status="no_attachment", payload={"runtime": {"read": True}}),
        dict(id="unknown-empty", execution_status="no_attachment"),
        dict(id="unread-empty", execution_status="no_attachment", read=False),
        dict(id="locked", execution_status="claimed", locked=True),
        dict(id="reward", execution_status="unclaimed", has_attachment=True),
    ]
    snapshot = {"items": [dict(present_in_runtime=True, **row) for row in rows]}
    assert set(deletable_mail_garbage(snapshot)) == {"claimed", "read-empty"}
    assert protected_mail_ids(snapshot) == {"locked", "reward"}


def test_success_requires_exact_claim_count_and_completed_cleanup():
    result = dict(result="success", claimed_count=2, garbage_before=3,
                  deleted_count=3, garbage_after=0, protected_count=1)
    validate_mail_terminal_result(result, target_count=2)
    for change in ({"claimed_count": 1}, {"deleted_count": 2},
                   {"garbage_after": 1}, {"protected_count": -1}, {"result": "running"}):
        with pytest.raises(RuntimeError):
            validate_mail_terminal_result(dict(result, **change), target_count=2)


def test_claimed_mail_reward_item_ids_require_decoded_payload_identity():
    decoded = {"payload": {"mail_rewards": [
        {"item_id": 1001, "item_name": "灵石"},
        {"item_id": "1001", "item_name": "灵石"},
        {"item_name": "未知道具"},
    ]}}
    assert mail_target_reward_item_ids(decoded) == ["1001"]
    assert mail_target_reward_item_ids({"payload": {}}) == []
    assert mail_target_reward_item_ids({}) == []


def test_new_arrivals_are_pending_work_after_frozen_batch_completes():
    snapshot = {"complete": True, "items": [
        dict(id="original", present_in_runtime=True, execution_status="claimed"),
        dict(id="new", present_in_runtime=True, execution_status="unclaimed", action_policy="claim"),
        dict(id="protected", present_in_runtime=True, execution_status="unclaimed", action_policy="retain"),
    ]}
    assert classify_mail_batch_remainder(snapshot, {"original"}) == {
        "remaining_batch_mail_ids": [], "deferred_mail_ids": ["new"],
    }
    # A precise request does not authorize other arrivals.
    assert classify_mail_batch_remainder(snapshot, {"original"}, target_mail_ids={"original"}) == {
        "remaining_batch_mail_ids": [], "deferred_mail_ids": [],
    }


def test_empty_batch_does_not_expand_and_unfinished_original_is_not_deferred():
    snapshot = {"complete": True, "items": [
        dict(id="original", present_in_runtime=True, execution_status="unclaimed", action_policy="claim"),
        dict(id="new", present_in_runtime=True, execution_status="unclaimed", action_policy="claim"),
    ]}
    assert classify_mail_batch_remainder(snapshot, set()) == {
        "remaining_batch_mail_ids": [], "deferred_mail_ids": ["original", "new"],
    }
    assert classify_mail_batch_remainder(snapshot, {"original"}) == {
        "remaining_batch_mail_ids": ["original"], "deferred_mail_ids": ["new"],
    }
    with pytest.raises(RuntimeError):
        classify_mail_batch_remainder({**snapshot, "complete": False}, {"original"})


def test_arrivals_keep_followup_due_soon_instead_of_sleeping_until_next_day():
    from backend.core.fanxiu.data_annotation.tasks.mail import mail_selective_claim_next_time

    observed = datetime(2026, 10, 12, 0, 38, 6)
    assert mail_selective_claim_next_time(observed, deferred_count=7) == "2026-10-12 00:48:06"
    assert mail_selective_claim_next_time(observed) == "2026-10-13 00:00:00"
    assert mail_selective_claim_next_time(
        datetime(2026, 10, 12, 23, 55), deferred_count=1,
    ) == "2026-10-13 00:05:00"
    assert mail_selective_claim_next_time(
        datetime(2026, 10, 12, 0), deferred_count=7, completed_at=observed,
    ) == "2026-10-12 00:48:06"
    with pytest.raises(ValueError):
        mail_selective_claim_next_time(observed, deferred_count=-1)
