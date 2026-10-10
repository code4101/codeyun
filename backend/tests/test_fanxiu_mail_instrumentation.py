from __future__ import annotations

from backend.core.fanxiu.instrumentation import mail
from backend.core.fanxiu.instrumentation.runtime_memory import (
    LuaJitReader,
    LuaRef,
    MumuProcessMemory,
)


def test_mail_snapshot_reports_list_and_attachment_granularity(monkeypatch):
    mail_list = LuaRef("table", 0x1000)
    lock_list = LuaRef("table", 0x1100)
    mail_a = LuaRef("table", 0x2000)
    mail_b = LuaRef("table", 0x2100)
    reward = LuaRef("table", 0x3000)
    id_a = LuaRef("table", 0x4000)
    id_b = LuaRef("table", 0x4100)
    amount = LuaRef("table", 0x4200)
    monkeypatch.setattr(
        mail,
        "_mail_data_fields",
        lambda _reader, _root: {
            "mailList": mail_list,
            "lockList": lock_list,
        },
    )

    fields = {
        mail_a: {
            "id": id_a,
            "type": 61.0,
            "title": "",
            "content": "",
            "createTime": 1000.0,
            "expireTime": 2000.0,
            "rewards": LuaRef("table", 0x5000),
            "read": False,
            "rewardGetted": False,
            "senderName": "",
        },
        mail_b: {
            "id": id_b,
            "type": 99.0,
            "title": "通知",
            "content": "正文",
            "createTime": 900.0,
            "expireTime": 1900.0,
            "rewards": LuaRef("table", 0x5100),
            "read": True,
            "rewardGetted": False,
            "senderName": "系统",
        },
        reward: {
            "type": 0.0,
            "code": 10001001.0,
            "amount": amount,
            "content": "",
            "extraMark": 0.0,
            "clientContent": "",
        },
    }
    monkeypatch.setattr(
        LuaJitReader,
        "string_fields",
        lambda _reader, address, names: {
            key: value for key, value in fields.get(LuaRef("table", address), {}).items()
            if key in names
        },
    )

    def fake_list_items(_reader, value):
        if value == mail_list:
            return [mail_a, mail_b], 2
        if value == lock_list:
            return [id_b], 1
        if value == fields[mail_a]["rewards"]:
            return [reward], 1
        if value == fields[mail_b]["rewards"]:
            return [], 0
        return [], None

    monkeypatch.setattr(LuaJitReader, "list_items", fake_list_items)
    identities = {id_a: 101, id_b: 102, amount: 100}
    monkeypatch.setattr(
        LuaJitReader,
        "long",
        lambda _reader, value: identities.get(value),
    )
    memory = MumuProcessMemory(
        pid=123,
        process_start_ticks=456,
        adb_serial="test",
        regions=[],
    )

    result = mail._snapshot(
        memory,
        0x6000,
        root_cache_hit=False,
        state_address=0x7000,
        environment_address=0x8000,
    )

    assert result["ok"] is True
    assert result["total"] == 2
    assert result["unread_count"] == 1
    assert result["unclaimed_count"] == 1
    assert result["locked_count"] == 1
    assert result["sequence_fingerprint"]
    assert result["items"][0]["runtime_index"] == 0
    assert result["items"][0]["id"] == "101"
    assert result["items"][0]["has_attachment"] is True
    assert result["items"][0]["attachment_count"] == 1
    assert result["items"][0]["rewards"] == [
        {
            "type": 0,
            "code": 10001001,
            "amount": 100,
            "content": "",
            "extra_mark": 0,
            "client_content": "",
        }
    ]
    assert result["items"][1]["locked"] is True
    assert result["items"][1]["runtime_index"] == 1
    assert result["items"][1]["has_attachment"] is False
    assert result["items"][1]["unclaimed_attachment"] is False


def test_mail_snapshot_rejects_partially_decoded_reward_list(monkeypatch):
    mail_list = LuaRef("table", 0x1000)
    lock_list = LuaRef("table", 0x1100)
    mail_value = LuaRef("table", 0x2000)
    mail_id = LuaRef("table", 0x3000)
    reward_list = LuaRef("table", 0x4000)
    monkeypatch.setattr(
        mail,
        "_mail_data_fields",
        lambda _reader, _root: {
            "mailList": mail_list,
            "lockList": lock_list,
        },
    )
    monkeypatch.setattr(
        LuaJitReader,
        "string_fields",
        lambda _reader, address, names: (
            {
                "id": mail_id,
                "type": 61.0,
                "rewards": reward_list,
                "read": False,
                "rewardGetted": False,
            }
            if address == mail_value.address
            else {}
        ),
    )

    def fake_list_items(_reader, value):
        if value == mail_list:
            return [mail_value], 1
        if value == lock_list:
            return [], 0
        if value == reward_list:
            return [], 1
        return [], None

    monkeypatch.setattr(LuaJitReader, "list_items", fake_list_items)
    monkeypatch.setattr(
        LuaJitReader,
        "long",
        lambda _reader, value: 101 if value == mail_id else None,
    )
    memory = MumuProcessMemory(
        pid=123,
        process_start_ticks=456,
        adb_serial="test",
        regions=[],
    )

    result = mail._snapshot(
        memory,
        0x6000,
        root_cache_hit=True,
        state_address=0x7000,
        environment_address=0x8000,
    )

    assert result["ok"] is False
    assert result["complete"] is False
    assert result["malformed_count"] == 1


def test_selected_vo_fields_keep_child_values_and_stop_inheritance_cycle():
    from types import SimpleNamespace

    child, parent = LuaRef("table", 1), LuaRef("table", 2)
    tables = {
        1: {"_super": parent, "read": False, "type": 0, "id": 7},
        2: {"_super": child, "read": True, "type": 61, "createTime": 99,
            "unconsumed": "must not decode"},
    }
    calls = []

    def selected(address, names):
        calls.append((address, names))
        return {key: value for key, value in tables[address].items() if key in names}

    # This is a pure table-selection contract, with no Runtime/game execution.
    reader = SimpleNamespace(string_fields=selected)
    fields = mail._object_fields(reader, child, mail._MAIL_FIELDS)
    assert fields["read"] is False
    assert fields["type"] == 0
    assert fields["id"] == 7
    assert fields["createTime"] == 99
    assert "unconsumed" not in fields
    assert [address for address, _ in calls] == [1, 2]
    calls.clear()
    header = mail._object_fields(reader, child, mail._HEADER_FIELDS)
    assert {key: header[key] for key in mail._HEADER_FIELDS} == {
        "id": 7, "type": 0, "createTime": 99,
    }
    assert all(names == mail._HEADER_FIELDS | {"_super"} for _, names in calls)
    assert "read" not in header
