from types import SimpleNamespace

from backend.core.fanxiu.data_annotation.behavior_tree_runtime import BehaviorTreeRuntimeRunner


def _runner_without_runtime_match():
    runner = BehaviorTreeRuntimeRunner.__new__(BehaviorTreeRuntimeRunner)
    runner._find_runtime_mail_records_for_visible_row = lambda *_args, **_kwargs: []
    runner._mail_row_runtime_missing_reason = lambda *_args, **_kwargs: "no_runtime_fact"
    return runner


def test_activity_mail_titles_fail_closed_without_runtime_fact():
    runner = _runner_without_runtime_match()

    for title in ("宗门灵泉活动收益", "魔狱封阵奖励", "宗门镇邪活动奖励"):
        row = {"title": title, "time_text": "2026年07月12日 21:05"}

        runner._prepare_mail_row_policy(row)

        assert row["runtime_match"] == "missing"
        assert row["policy"] == ""


def test_activity_mail_title_never_overrides_faze_runtime_fact():
    runner = BehaviorTreeRuntimeRunner.__new__(BehaviorTreeRuntimeRunner)
    runner._find_runtime_mail_records_for_visible_row = lambda *_args, **_kwargs: [
        SimpleNamespace(
            mail_key="faze-whitelist-mail",
            create_time_text="2026年08月05日21:05",
            status="可领",
            runtime_status="unclaimed",
            action_policy="",
            payload={
                "mail_rewards": [
                    {"item_name": "魔道法则", "item_type": "法则", "amount": 1}
                ]
            },
        )
    ]
    runner._mail_row_runtime_missing_reason = lambda *_args, **_kwargs: ""
    row = {"title": "魔狱封阵奖励", "time_text": "2026年08月05日 21:05"}

    runner._prepare_mail_row_policy(row)

    assert row["policy"] == ""


def test_unrelated_mail_still_requires_runtime_claim_policy():
    runner = _runner_without_runtime_match()
    row = {"title": "普通系统邮件", "time_text": "2026年07月12日 21:05"}

    runner._prepare_mail_row_policy(row)

    assert row["policy"] == ""
