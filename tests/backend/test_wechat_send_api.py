from fastapi import HTTPException
import pytest

from backend.api.wechat_archive import WeChatSendTextRequest, send_wechat_text


def test_send_wechat_text_uses_process_api(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "pyxllib.autogui.weixin4_instrumentation.send_text",
        lambda recipient, text: calls.append((recipient, text)) or {"result": {"result": 1}},
    )

    result = send_wechat_text(WeChatSendTextRequest(recipient="考勤中台", text="日报"))

    assert calls == [("考勤中台", "日报")]
    assert result == {"result": {"result": 1}}


def test_send_wechat_text_fails_closed(monkeypatch):
    monkeypatch.setattr(
        "pyxllib.autogui.weixin4_instrumentation.send_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("unsupported version")),
    )

    with pytest.raises(HTTPException, match="禁止 GUI 回退") as exc_info:
        send_wechat_text(WeChatSendTextRequest(recipient="考勤中台", text="日报"))

    assert exc_info.value.status_code == 502


def test_send_wechat_text_passes_explicit_sender(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "pyxllib.autogui.weixin4_instrumentation.send_text",
        lambda *args, **kwargs: calls.append(kwargs) or {},
    )
    send_wechat_text(WeChatSendTextRequest(recipient="filehelper", text="mock", sender_account_id="wxid_second"))
    assert calls == [{"sender_account_id": "wxid_second"}]


def test_default_archive_precedes_official_and_tim_roots(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from backend.api import wechat_archive as api

    data = tmp_path / "codepc_mf"
    (data / "tim_legacy").mkdir(parents=True)
    archive = tmp_path / "reverse" / "db_storage"
    archive.mkdir(parents=True)
    official = tmp_path / "xwechat_files"
    official.mkdir()
    monkeypatch.setattr(api, "get_settings", lambda: SimpleNamespace(data_dir=data))
    monkeypatch.setattr(api, "_settings_wechat_db_storage_path", lambda: archive)
    monkeypatch.setattr(api, "_wechat_official_device_roots", lambda: [official])
    assert api._wechat_default_current_device_root([official, data]) == data
    assert api._settings_wechat_db_storage_path_for_device(data) == archive
