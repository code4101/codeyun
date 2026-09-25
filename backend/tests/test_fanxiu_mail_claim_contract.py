"""邮件授权、删除保护及终态计数的纯领域契约。"""

import pytest

from backend.core.fanxiu.mail.claim_contract import (
    claimable_mail_targets,
    deletable_mail_garbage,
    protected_mail_ids,
    select_mail_claim_targets,
    validate_mail_terminal_result,
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
