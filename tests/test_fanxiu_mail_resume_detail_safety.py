from pathlib import Path


def test_mail_cleanup_never_blindly_claims_or_deletes_an_unexpected_detail_page():
    source = Path("backend/core/fanxiu/data_annotation/tasks/mail.py").read_text(encoding="utf-8")
    detail_branch = source.split("        elif scene_id in {122, 123}:", 1)[1].split("        else:", 1)[0]

    assert "缺少列表策略证据，只返回列表不领取/删除" in detail_branch
    assert "空白-返回" in detail_branch
    assert "wait_click" not in detail_branch
