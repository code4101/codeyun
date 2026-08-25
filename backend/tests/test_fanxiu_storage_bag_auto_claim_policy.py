from backend.core.fanxiu.data_annotation.tasks.storage_bag_auto_claim_policy import (
    NPC_GIFT_EXTERNAL_ROUTE,
    decide_storage_bag_auto_claim_item,
    parse_storage_bag_choice_note,
)


def _row(**overrides):
    row = {
        "base_id": 1,
        "num": 10,
        "auto_claim": True,
        "analysis_status": "classified",
        "operation_template": "random_box",
        "yield_mode": "random",
        "note": "",
    }
    row.update(overrides)
    return row


def test_choice_notes_preserve_partner_qualified_first_available_semantics() -> None:
    assert parse_storage_bag_choice_note("选魔道4倍符") == ("named", "魔道4倍符")
    assert parse_storage_bag_choice_note("选第1个可以选的仙侣") == (
        "first_available_partner",
        "",
    )


def test_choice_note_ambiguity_fails_closed() -> None:
    plan = decide_storage_bag_auto_claim_item(_row(
        operation_template="choice_box",
        yield_mode="none",
        note="选个好的",
    ))
    assert plan is not None and plan.action == "execute"
    assert plan.choice_value == "个好的"

    plan = decide_storage_bag_auto_claim_item(_row(
        operation_template="choice_box",
        yield_mode="none",
        note="随便选",
    ))
    assert plan is not None and plan.action == "fail"


def test_npc_gift_is_routed_out_of_storage_bag_lifecycle() -> None:
    plan = decide_storage_bag_auto_claim_item(_row(
        base_id=37092001,
        operation_template="npc_gift",
        yield_mode="none",
        note="要去仙缘送礼",
    ))
    assert plan is not None and plan.action == "route"
    assert plan.external_route == NPC_GIFT_EXTERNAL_ROUTE


def test_activity_timing_note_defers_without_runtime_authority() -> None:
    plan = decide_storage_bag_auto_claim_item(_row(
        base_id=38100037,
        note="洗灵祈愿周使用",
    ))
    assert plan is not None and plan.action == "defer"
    assert plan.condition_key == "xiling_prayer_week"


def test_zero_inventory_or_disabled_rows_are_not_actions() -> None:
    assert decide_storage_bag_auto_claim_item(_row(num=0)) is None
    assert decide_storage_bag_auto_claim_item(_row(auto_claim=False)) is None
