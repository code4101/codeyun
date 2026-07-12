from backend.core.fanxiu.data_annotation.runtime_runner import DataAnnotationRuntimeRunner


def _runner_without_packet_match():
    runner = DataAnnotationRuntimeRunner.__new__(DataAnnotationRuntimeRunner)
    runner._find_packet_mail_records_for_visible_row = lambda *_args, **_kwargs: []
    runner._mail_row_packet_missing_reason = lambda *_args, **_kwargs: "no_packet_fact"
    return runner


def test_sect_activity_mail_titles_force_claim_without_packet_fact():
    runner = _runner_without_packet_match()

    for title in ("宗门灵泉活动收益", "宗门镇邪活动奖励"):
        row = {"title": title, "time_text": "2026年07月12日 21:05"}

        runner._prepare_mail_row_policy(row)

        assert row["packet_match"] == "missing"
        assert row["policy"] == "claim"


def test_unrelated_mail_still_requires_packet_claim_policy():
    runner = _runner_without_packet_match()
    row = {"title": "普通系统邮件", "time_text": "2026年07月12日 21:05"}

    runner._prepare_mail_row_policy(row)

    assert row["policy"] == ""
