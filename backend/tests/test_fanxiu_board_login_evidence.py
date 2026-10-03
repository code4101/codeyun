import pytest
from backend.core.fanxiu.data_annotation.login_recovery import validate_board_login_evidence


def _facts():
    # Known board text and Runtime wallet schema, not a simulated GUI flow.
    return dict(scene_id=678,frame_text="位面名 占点数量 查看榜单 离开",
                health=dict(status="healthy",info=dict(is_android_started=True,is_process_started=True)),
                wallet=dict(source="runtime_memory",currency_type=13,currency_amount=112038,
                            captured_at="2026-10-03T10:20:00+08:00",evidence=dict(pid=2666,process_start_ticks=5249)),
                observed_at=1790994001.0)


def test_verified_live_board_and_wallet_can_finish_login():
    validate_board_login_evidence(**_facts())


@pytest.mark.parametrize("text", ["未登录", "未登陆", "账号登录", "挑选账号", "进入游戏", "重新登录", "登录失效", "连接已断开", "网络连接失败", "正在重连", "断线重连"])
def test_login_or_disconnect_overlay_cannot_be_hidden_by_board_scene_number(text):
    facts=_facts(); facts["frame_text"] += text
    with pytest.raises(RuntimeError): validate_board_login_evidence(**facts)


@pytest.mark.parametrize("change", [{"status":"suspect"},{"status":"offline"},{"info":dict(is_android_started=False,is_process_started=True)}])
def test_disconnected_device_never_finishes_login(change):
    facts=_facts(); facts["health"] |= change
    with pytest.raises(RuntimeError): validate_board_login_evidence(**facts)


@pytest.mark.parametrize("change", [{"source":"persisted"},{"currency_type":None},{"evidence":{}},{"currency_amount":None},{"captured_at":"2026-10-03T09:20:00+08:00"}])
def test_missing_unloaded_or_stale_wallet_is_not_login_evidence(change):
    facts=_facts(); facts["wallet"] |= change
    with pytest.raises(RuntimeError): validate_board_login_evidence(**facts)


@pytest.mark.parametrize("change", [{"scene_id":15},{"scene_id":None},{"frame_text":"离开"},{"frame_text":""}])
def test_scene_id_or_single_generic_label_alone_is_insufficient(change):
    facts=_facts() | change
    with pytest.raises(RuntimeError): validate_board_login_evidence(**facts)
