from pathlib import Path


def test_mail_cleanup_never_blindly_claims_or_deletes_a_startup_detail_page():
    source = Path("backend/core/fanxiu/data_annotation/tasks/mail.py").read_text(encoding="utf-8")
    resume_branch = source.split("        elif scene_id in {122, 123}:", 1)[1].split("        else:", 1)[0]

    assert "缺少本轮列表策略证据，只返回列表不领取/删除" in resume_branch
    assert "空白-返回" in resume_branch
    assert "wait_click" not in resume_branch
