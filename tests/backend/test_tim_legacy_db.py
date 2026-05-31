from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from backend.api import wechat_archive
from backend.core import tim_legacy_db
from backend.core.tim_legacy_db import (
    TimLegacyDbStorage,
    classify_tim_export_file,
    extract_msgdbrandkeys_from_memory,
    extract_tim_message_rows_from_memory,
    parse_tim_export_messages,
)


def _init_tim_msg(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE mr_friend_10001 (
                msgid INTEGER PRIMARY KEY,
                time INTEGER,
                senderuin TEXT,
                issender INTEGER,
                msgtype INTEGER,
                content TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO mr_friend_10001 VALUES (?, ?, ?, ?, ?, ?)",
            (1, 1000, "10001", 0, 1, "hello tim"),
        )
        conn.execute(
            "INSERT INTO mr_friend_10001 VALUES (?, ?, ?, ?, ?, ?)",
            (2, 1100, "877362867", 2, 1, "reply tim"),
        )
        conn.execute(
            """
            CREATE TABLE mr_troop_20002 (
                msgid INTEGER PRIMARY KEY,
                time INTEGER,
                senderuin TEXT,
                issender INTEGER,
                msgtype INTEGER,
                content TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO mr_troop_20002 VALUES (?, ?, ?, ?, ?, ?)",
            (3, 1200, "30003", 0, 2, "group msg"),
        )
        conn.commit()
    finally:
        conn.close()


def test_tim_legacy_storage_maps_plain_sqlite_to_wechat_db_shape(tmp_path):
    root = tmp_path / "877362867"
    _init_tim_msg(root / "Msg3.0.db")

    storage = TimLegacyDbStorage(root)

    status = storage.status()
    assert status["source_format"] == "tim_legacy"
    assert status["ready"] is True
    assert status["self_username"] == "877362867"

    chats = storage.list_chats(limit=10)
    assert [chat["username"] for chat in chats] == ["20002", "10001"]
    assert chats[0]["chat_type"] == "chatroom"
    assert chats[1]["summary"] == "reply tim"
    assert storage.count_chats() == 2

    payload = storage.list_messages(chat_username="10001", limit=10, order="asc")
    assert payload["total"] == 2
    assert [item["message_text"] for item in payload["items"]] == ["hello tim", "reply tim"]
    assert payload["items"][1]["sender_username"] == "877362867"
    assert storage.message_types(chat_username="10001") == [{"local_type": 1, "count": 2}]


def test_tim_legacy_storage_reports_encrypted_sqlite_variant(monkeypatch, tmp_path):
    monkeypatch.setattr(tim_legacy_db, "tim_processes", lambda: [])
    root = tmp_path / "877362867"
    root.mkdir()
    path = root / "Msg3.0.db"
    path.write_bytes(b"SQLite format 3\x00\x04\x00 \x00" + b"\x00" * 256)

    status = TimLegacyDbStorage(root).status()

    assert status["exists"] is True
    assert status["ready"] is False
    assert status["databases"]["message"] is True
    assert "encrypted SQLite variant" in status["error"]


def test_extract_msgdbrandkey_from_sqlite_leaf_page():
    page_size = 8192
    page = bytearray(page_size)
    page[0] = 0x0D
    page[3:5] = (1).to_bytes(2, "big")
    page[5:7] = (page_size - 72).to_bytes(2, "big")
    page[8:10] = (page_size - 72).to_bytes(2, "big")
    key = bytes.fromhex("0301030000c3301924deb0fe7c98fcdb3b030d297187446feb555fbdd6b101790e03189bb524e85c9ccf4114f431ddba8f6deab251562c44d5480f0a3b")
    header = bytes([4, 19, 0x81, 0x06])
    payload = header + b"1.1" + key
    cell = bytes([len(payload), 1]) + payload
    page[page_size - 72 : page_size - 72 + len(cell)] = cell

    keys = extract_msgdbrandkeys_from_memory(bytes(page), base_address=0x19580000)

    assert keys == [
        {
            "version": "1.1",
            "msgdbrandkey_hex": key.hex(),
            "msgdbrandkey_length": 61,
            "rowid": 1,
            "address": "0x19580000",
            "page_size": 8192,
            "cell_count": 1,
            "serial_types": [19, 134],
        }
    ]


def test_extract_tim_message_rows_from_sqlite_leaf_page(tmp_path):
    db_path = tmp_path / "message_page.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE buddy_10001 (Time integer, Rand integer, SenderUin integer, MsgContent blob, Info blob, primary key(Time, Rand))"
        )
        msg_blob = b"MSG" + ("宋体" + "你的感觉真准，最近和领导吵了").encode("utf-16-le")
        conn.execute(
            "INSERT INTO buddy_10001 VALUES (?, ?, ?, ?, ?)",
            (1763100027, 123, 10001, msg_blob, b"info"),
        )
        conn.commit()
    finally:
        conn.close()

    rows = extract_tim_message_rows_from_memory(db_path.read_bytes(), base_address=0x3100000)

    assert len(rows) == 1
    assert rows[0]["time"] == 1763100027
    assert rows[0]["sender"] == "10001"
    assert rows[0]["message_text"] == "你的感觉真准，最近和领导吵了"


def test_tim_sync_from_live_stores_structured_qq_archive(monkeypatch, tmp_path):
    live_root = tmp_path / "Tencent Files" / "877362867"
    live_root.mkdir(parents=True)
    (live_root / "Msg3.0.db").write_bytes(b"SQLite header 3\x00" + b"\x00" * 128)
    storage = TimLegacyDbStorage(live_root)
    monkeypatch.setattr(tim_legacy_db, "_candidate_tim_account_roots", lambda: [live_root])
    monkeypatch.setattr(tim_legacy_db, "tim_processes", lambda: [{"pid": 1, "name": "TIM.exe", "exe": "TIM.exe"}])
    monkeypatch.setattr(tim_legacy_db, "tim_runtime_msgdbrandkeys", lambda: [{"version": "1.1", "msgdbrandkey_hex": "abc"}])
    monkeypatch.setattr(
        tim_legacy_db,
        "tim_live_message_rows",
        lambda: [
            {
                "rowid": 1,
                "time": 1763100027,
                "rand": 123,
                "sender": "10001",
                "message_text": "你的感觉真准，最近和领导吵了",
                "msg_content_hex": "4d5347",
                "info_hex": "00",
                "address": "0x1000",
            }
        ],
    )
    monkeypatch.setenv("CODEYUN_QQ_CHAT_ARCHIVE_DB", str(tmp_path / "codeyun" / "qq_chat.sqlite"))

    result = storage.sync_from_live()

    assert result["structured"]["inserted"] == 1
    assert storage.status()["structured_ready"] is True
    chats = storage.list_chats()
    assert chats[0]["username"] == "10001"
    assert chats[0]["summary"] == "你的感觉真准，最近和领导吵了"
    messages = storage.list_messages("10001", order="asc")
    assert messages["table_name"] == "qq_archive:10001"
    assert messages["items"][0]["message_text"] == "你的感觉真准，最近和领导吵了"


def test_tim_archive_reads_do_not_rescan_live_memory(monkeypatch, tmp_path):
    live_root = tmp_path / "Tencent Files" / "877362867"
    live_root.mkdir(parents=True)
    (live_root / "Msg3.0.db").write_bytes(b"SQLite header 3\x00" + b"\x00" * 128)
    rows = [
        {
            "rowid": index,
            "time": 1763100000 + index,
            "rand": index,
            "sender": "10001",
            "message_text": f"消息{index}",
            "msg_content_hex": f"4d5347{index:02x}",
            "info_hex": "00",
            "address": f"0x{index:x}",
        }
        for index in range(1, 6)
    ]
    storage = TimLegacyDbStorage(live_root)
    monkeypatch.setattr(tim_legacy_db, "_candidate_tim_account_roots", lambda: [live_root])
    monkeypatch.setattr(tim_legacy_db, "tim_processes", lambda: [{"pid": 1, "name": "TIM.exe", "exe": "TIM.exe"}])
    monkeypatch.setattr(tim_legacy_db, "tim_runtime_msgdbrandkeys", lambda: [])
    monkeypatch.setattr(tim_legacy_db, "tim_live_message_rows", lambda: rows)
    monkeypatch.setenv("CODEYUN_QQ_CHAT_ARCHIVE_DB", str(tmp_path / "codeyun" / "qq_chat.sqlite"))
    storage.sync_from_live()

    def fail_scan(*args, **kwargs):
        raise AssertionError("archive reads must not rescan TIM memory")

    monkeypatch.setattr(tim_legacy_db, "tim_live_message_rows", fail_scan)
    assert storage.count_chats() == 1
    assert storage.list_chats(limit=1)[0]["message_count"] == 5
    assert storage.count_messages("10001")["total"] == 5
    messages = storage.list_messages("10001", order="asc", limit=2, offset=1)
    assert [item["message_text"] for item in messages["items"]] == ["消息2", "消息3"]


def test_parse_tim_msgmgr_text_export(tmp_path):
    export = tmp_path / "1000000.txt"
    export.write_text(
        "\n".join(
            [
                "From: <Save by Tencent MsgMgr>",
                "消息对象: 1000000",
                "2026-05-30 09:15:00 森林(12345): 加入本群。",
                "2026-05-30 09:16:00 10: 群活跃等级升级为LV5",
                "第二行说明",
            ]
        ),
        encoding="utf-8",
    )

    rows = parse_tim_export_messages(export)

    assert [row["chat"] for row in rows] == ["1000000", "1000000"]
    assert rows[0]["sender"] == "12345"
    assert rows[0]["message_text"] == "加入本群。"
    assert rows[1]["message_text"] == "群活跃等级升级为LV5\n第二行说明"


def test_classify_tim_bak_as_encrypted_helper_input(tmp_path):
    export = tmp_path / "1000000.bak"
    export.write_bytes(b"SQLite header 3\x00" + b"\x00" * 64)

    flavor = classify_tim_export_file(export)

    assert flavor["kind"] == "tim_encrypted_sqlite_bak"
    assert flavor["parseable"] is False
    assert flavor["requires_helper"] == "tim_kernelutil"


def test_tim_sync_imports_msgmgr_export_file(monkeypatch, tmp_path):
    live_root = tmp_path / "Tencent Files" / "877362867"
    live_root.mkdir(parents=True)
    (live_root / "Msg3.0.db").write_bytes(b"SQLite header 3\x00" + b"\x00" * 128)
    export = tmp_path / "qq_export.txt"
    export.write_text("消息对象: 1000000\n2026-05-30 09:15:00 10: 群活跃等级升级为LV5\n", encoding="utf-8")
    storage = TimLegacyDbStorage(live_root)
    monkeypatch.setattr(tim_legacy_db, "_candidate_tim_account_roots", lambda: [live_root])
    monkeypatch.setattr(tim_legacy_db, "tim_processes", lambda: [])
    monkeypatch.setattr(tim_legacy_db, "tim_live_message_rows", lambda: [])
    monkeypatch.setenv("CODEYUN_QQ_CHAT_EXPORTS", str(export))
    monkeypatch.setenv("CODEYUN_QQ_CHAT_ARCHIVE_DB", str(tmp_path / "codeyun" / "qq_chat.sqlite"))

    result = storage.sync_from_live()

    assert result["exports"]["parsed"] == 1
    assert result["structured"]["inserted"] == 1
    chats = storage.list_chats()
    assert chats[0]["username"] == "1000000"
    messages = storage.list_messages("1000000", order="asc")
    assert messages["items"][0]["sender_username"] == "10"
    assert messages["items"][0]["message_text"] == "群活跃等级升级为LV5"


def test_tim_sync_reports_bak_without_text_parsing(monkeypatch, tmp_path):
    live_root = tmp_path / "Tencent Files" / "877362867"
    live_root.mkdir(parents=True)
    (live_root / "Msg3.0.db").write_bytes(b"SQLite header 3\x00" + b"\x00" * 128)
    export = tmp_path / "qq_export.bak"
    export.write_bytes(b"SQLite header 3\x00" + b"\x00" * 128)
    storage = TimLegacyDbStorage(live_root)
    monkeypatch.setattr(tim_legacy_db, "_candidate_tim_account_roots", lambda: [live_root])
    monkeypatch.setattr(tim_legacy_db, "tim_processes", lambda: [])
    monkeypatch.setattr(tim_legacy_db, "tim_live_message_rows", lambda: [])
    monkeypatch.setenv("CODEYUN_QQ_CHAT_EXPORTS", str(export))
    monkeypatch.setenv("CODEYUN_QQ_CHAT_ARCHIVE_DB", str(tmp_path / "codeyun" / "qq_chat.sqlite"))

    result = storage.sync_from_live()
    status = storage.status()

    assert result["exports"]["file_count"] == 1
    assert result["exports"]["parsed"] == 0
    assert result["exports"]["error_count"] == 1
    assert status["requires_manual_export"] is False
    assert status["requires_tim_kernelutil_helper"] is True


def test_wechat_archive_devices_can_select_tim_account_root(monkeypatch, tmp_path):
    current_root = tmp_path / "codepc_current"
    tim_root = tmp_path / "Tencent Files" / "877362867"
    _init_tim_msg(tim_root / "Msg3.0.db")
    monkeypatch.setattr(wechat_archive, "get_settings", lambda: SimpleNamespace(data_dir=current_root))
    monkeypatch.setenv("CODEYUN_WECHAT_DEVICE_ROOTS", str(tim_root))

    devices = wechat_archive.list_wechat_db_devices()["items"]
    tim_device = next(item for item in devices if item["id"] == "877362867")
    assert tim_device["source_format"] == "tim_legacy"
    assert tim_device["ready"] is True

    chats = wechat_archive.list_wechat_db_chats(device_id="877362867", limit=1)
    assert chats["device_id"] == "877362867"
    assert chats["total"] == 2

    messages = wechat_archive.list_wechat_db_messages(chat_username=chats["items"][0]["username"], device_id="877362867")
    assert messages["device_id"] == "877362867"
    assert messages["total"] == 1
