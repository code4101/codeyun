"""GUI prose and thumbnails cannot masquerade as verified original downloads."""
import threading
from functools import partial
from unittest.mock import Mock

import pytest

from backend.core.messaging import wechat_resource_acquisition as api
from pyxllib.autogui import wechat_accounts


ENTRIES = [{"data_index": 2, "datatype": 2, "full_md5": "image", "data_size": 10},
           {"data_index": 4, "datatype": 4, "full_md5": "video", "data_size": 20}]


def test_prepare_ordinary_image_uses_exact_message_identity(monkeypatch):
    storage = Mock()
    storage.list_messages.return_value = {"items": [{"local_id": 5, "local_type_normalized": 3,
        "sender_username": "owner", "sender_name": "Owner", "create_time_text": "2026-10-06 15:18:43"}]}
    storage.list_chats.return_value = [{"username": "chat", "name": "ProjectGraph"}]
    storage.message_resources.return_value = {"items": [{"data_index": "0", "export": {
        "kind": "image", "variant": "thumbnail", "readable": True}}]}
    monkeypatch.setattr(wechat_accounts, "get_account_storage", lambda _: storage)
    plan = api.prepare_resource_acquisition("account", "chat", 5)
    assert plan["message_kind"] == "image" and plan["sender_id"] == "owner"
    assert api.pending_attachments(plan["entries"], plan["reference_resources"])
    resources = {"items": [{"data_index": "0", "export": {
        "kind": "image", "variant": "high", "readable": True}}]}
    assert api.pending_attachments(plan["entries"], resources) == []


def test_prepare_rejects_unexplained_non_media_type(monkeypatch):
    storage = Mock()
    storage.list_messages.return_value = {"items": [{"local_id": 5, "local_type_normalized": 1}]}
    monkeypatch.setattr(wechat_accounts, "get_account_storage", lambda _: storage)
    with pytest.raises(ValueError, match="普通图片"):
        api.prepare_resource_acquisition("account", "chat", 5)


def test_prepare_ordinary_file_keeps_name_size_and_digest(monkeypatch):
    storage = Mock()
    storage.list_messages.return_value = {"items": [{"local_id": 5, "local_type_normalized": 49,
        "appmsg": {"app_type": 6, "title": "settings.json", "total_size": 1987, "md5": "digest"}}]}
    storage.list_chats.return_value = [{"username": "chat", "name": "Owner"}]
    monkeypatch.setattr(wechat_accounts, "get_account_storage", lambda _: storage)
    plan = api.prepare_resource_acquisition("account", "chat", 5)
    assert plan['message_kind'] == 'file'
    assert plan['entries'] == [{"data_index": "0", "datatype": 8, "file_name": "settings.json",
                                "data_size": 1987, "full_md5": "digest"}]


def assets(*, ready=True):
    return {"items": [{"data_index": str(index), "export": {
        "kind": kind, "variant": "standard" if ready else "thumbnail", "readable": True,
        "md5": kind, "decoded_size": size, "size": size + 31}}
        for index, kind, size in ((2, "image", 10), (4, "video", 20))]}


@pytest.mark.parametrize("damage", ["thumbnail", "md5", "decoded_size", "readable", "wrong_index"])
def test_original_identity_and_bytes_are_required(damage):
    value = assets()
    export = value["items"][0]["export"]
    if damage == "thumbnail":
        export["variant"] = "thumbnail"
    elif damage == "wrong_index":
        value["items"][0]["data_index"] = "3"
    else:
        export[damage] = False if damage == "readable" else "wrong"
    assert api.pending_attachments(ENTRIES, value) == [{"data_index": 2, "datatype": 2, "kind": "image"}]


@pytest.fixture
def acquisition(monkeypatch, tmp_path):
    monkeypatch.setattr(api, 'hold_resource', partial(api.hold_resource, store=api.ResourceLeaseStore(tmp_path)))
    storage = Mock()
    storage.message_resources.return_value = assets()
    monkeypatch.setattr(wechat_accounts, "get_account_storage", lambda _: storage)
    check = Mock(return_value={"sender": {"pid": 42}, "recipients": {"chat": "chat"}})
    monkeypatch.setattr(wechat_accounts, "check_account", check)
    monkeypatch.setattr(api, "account_windows", lambda pid: [123] if pid == 42 else [])
    monkeypatch.setattr(api, "prepare_resource_acquisition", lambda *args: {
        "account_id": "account", "chat_id": "chat", "local_id": 35, "entries": ENTRIES})
    return storage, check


def test_cached_read_never_opens_gui(acquisition):
    gui = Mock(side_effect=AssertionError("cached files must not invoke GUI"))
    value = api.acquire_message_resources("account", "chat", 35, allow_gui=True, gui_download=gui)
    assert value["complete"] and value["transport"] == "cache"
    acquisition[1].assert_not_called()


def test_missing_files_without_consent_do_not_open_gui(acquisition):
    acquisition[0].message_resources.return_value = assets(ready=False)
    gui = Mock()
    value = api.acquire_message_resources("account", "chat", 35, gui_download=gui)
    assert not value["complete"]
    gui.assert_not_called()


def test_download_receipt_needs_exact_readback(acquisition):
    acquisition[0].message_resources.return_value = assets(ready=False)
    value = api.acquire_message_resources("account", "chat", 35, allow_gui=True,
        gui_download=lambda *_: {"target_verified": True, "restored": True})
    assert not value["complete"] and len(value["pending"]) == 2


def test_download_then_readback_and_safe_retry(acquisition):
    storage, check = acquisition
    storage.message_resources.side_effect = [assets(ready=False), assets(ready=False), assets()]
    gui = Mock(return_value={"target_verified": True, "restored": True})
    value = api.acquire_message_resources("account", "chat", 35, allow_gui=True, gui_download=gui)
    assert value["complete"] and value["pending"] == []
    assert gui.call_args.args[0]["allowed_window_ids"] == [123]
    storage.message_resources.side_effect = None
    second = api.acquire_message_resources("account", "chat", 35, allow_gui=True, gui_download=gui)
    assert second["complete"] and gui.call_count == 1


def test_executor_exception_still_reads_submitted_effects(acquisition):
    storage, _ = acquisition
    storage.message_resources.side_effect = [assets(ready=False), assets(ready=False), assets()]
    gui = Mock(side_effect=InterruptedError("interrupted after download"))
    value = api.acquire_message_resources("account", "chat", 35, allow_gui=True, gui_download=gui)
    assert not value["complete"] and value["pending"] == []
    assert "interrupted" in value["gui"]["error"]
    assert storage.message_resources.call_count == 3


def test_cancelled_before_gui_and_unsupported_types(acquisition):
    stop = threading.Event()
    stop.set()
    with pytest.raises(InterruptedError):
        api.acquire_message_resources("account", "chat", 35, allow_gui=True, force_gui=True, stop_event=stop)
    acquisition[1].assert_not_called()
    assert api.pending_attachments([{"data_index": 1, "datatype": 37}], assets()) == [
        {"data_index": 1, "datatype": 37, "kind": "unsupported"}]


def test_forwarded_file_requires_exact_index_size_and_optional_digest():
    entries = [{"data_index": 17, "datatype": 8, "data_size": 1987}]
    value = {"items": [{"data_index": "17", "export": {
        "kind": "file", "readable": True, "decoded_size": 1987}}]}
    assert api.pending_attachments(entries, value) == []
    value['items'][0]['export']['decoded_size'] = 10
    assert api.pending_attachments(entries, value)[0]['kind'] == 'file'
    value['items'][0]['export']['decoded_size'] = 1987
    value['items'][0]['data_index'] = '22'
    assert api.pending_attachments(entries, value)


def test_wrong_target_stops_before_gui(acquisition):
    acquisition[1].return_value["recipients"] = {"chat": "other"}
    gui = Mock()
    with pytest.raises(ValueError, match="核验不一致"):
        api.acquire_message_resources("account", "chat", 35, allow_gui=True, force_gui=True, gui_download=gui)
    gui.assert_not_called()


@pytest.mark.parametrize("gui", [{"target_verified": False, "restored": True},
                                 {"target_verified": True, "restored": False}])
def test_gui_failure_is_not_hidden_by_existing_cache(acquisition, gui):
    value = api.acquire_message_resources("account", "chat", 35, allow_gui=True,
                                          force_gui=True, gui_download=lambda *_: gui)
    assert not value["complete"] and value["pending"] == []
