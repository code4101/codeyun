from backend.core.fanxiu.data_annotation.behavior_tree_executor import (
    BehaviorTreeExecutor,
)


def _mail(mail_id: str, *, execution_status: str, action_policy: str) -> dict:
    return {
        "id": mail_id,
        "execution_status": execution_status,
        "present_in_runtime": True,
        "locked": False,
        "action_policy": action_policy,
    }


def test_absent_claim_action_is_complete_only_after_exact_runtime_id_resolves() -> None:
    refreshed = {
        "items": [
            _mail("already-claimed", execution_status="claimed", action_policy=""),
            _mail("still-unclaimed", execution_status="unclaimed", action_policy="claim"),
        ]
    }

    assert (
        BehaviorTreeExecutor._runtime_mail_target_still_requires_claim(
            refreshed, "already-claimed"
        )
        is False
    )
    assert (
        BehaviorTreeExecutor._runtime_mail_target_still_requires_claim(
            refreshed, "still-unclaimed"
        )
        is True
    )


def test_absent_claim_action_does_not_resolve_by_same_title_or_neighbor_state() -> None:
    refreshed = {
        "items": [
            {
                **_mail("claimed-neighbor", execution_status="claimed", action_policy=""),
                "title": "香车馈赠",
            },
            {
                **_mail("intended", execution_status="unclaimed", action_policy="claim"),
                "title": "香车馈赠",
            },
        ]
    }

    assert (
        BehaviorTreeExecutor._runtime_mail_target_still_requires_claim(
            refreshed, "intended"
        )
        is True
    )
