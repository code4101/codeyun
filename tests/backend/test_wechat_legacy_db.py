from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

from backend.api import wechat_archive
from backend.core import wechat_legacy_db as legacy_db
from backend.models import UserDevice
from backend.core.wechat_legacy_db import WeChatLegacyDbStorage


def _init_micro_msg(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE Contact (
                UserName TEXT,
                Alias TEXT,
                Remark TEXT,
                NickName TEXT,
                Type INTEGER,
                VerifyFlag INTEGER,
                ChatRoomType INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE Session (
                strUsrName TEXT,
                strNickName TEXT,
                nUnReadCount INTEGER,
                strContent TEXT,
                nMsgType INTEGER,
                nTime INTEGER,
                nIsSend INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE ContactHeadImgUrl (
                usrName TEXT,
                smallHeadImgUrl TEXT,
                bigHeadImgUrl TEXT,
                headImgMd5 TEXT,
                reverse0 TEXT,
                reverse1 TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE ChatRoom (
                ChatRoomName TEXT,
                UserNameList TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO Contact VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("friend", "alias", "Remark Friend", "Friend", 0, 0, 0),
        )
        conn.execute(
            "INSERT INTO Contact VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("room@chatroom", "", "", "Room", 0, 0, 0),
        )
        conn.execute(
            "INSERT INTO Contact VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("member", "", "", "Member", 0, 0, 0),
        )
        conn.execute(
            "INSERT INTO Session VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("friend", "Friend", 2, "latest", 1, 2000, 0),
        )
        conn.execute(
            "INSERT INTO Session VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("room@chatroom", "Room", 0, "room latest", 1, 2100, 0),
        )
        conn.execute(
            "INSERT INTO ContactHeadImgUrl VALUES (?, ?, ?, ?, ?, ?)",
            ("friend", "https://example.test/friend.jpg", "", "friend-md5", "", ""),
        )
        conn.execute(
            "INSERT INTO ContactHeadImgUrl VALUES (?, ?, ?, ?, ?, ?)",
            ("member", "https://example.test/member.jpg", "", "member-md5", "", ""),
        )
        conn.execute(
            "INSERT INTO ChatRoom VALUES (?, ?)",
            ("room@chatroom", "friend^Gmember"),
        )
        conn.commit()
    finally:
        conn.close()


def _init_msg(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE Name2ID (UsrName TEXT)")
        conn.execute(
            """
            CREATE TABLE MSG (
                localId INTEGER,
                TalkerId INTEGER,
                MsgSvrID INTEGER,
                Type INTEGER,
                SubType INTEGER,
                IsSender INTEGER,
                CreateTime INTEGER,
                Sequence INTEGER,
                StatusEx INTEGER,
                FlagEx INTEGER,
                Status INTEGER,
                MsgServerSeq INTEGER,
                MsgSequence INTEGER,
                StrTalker TEXT,
                StrContent TEXT,
                DisplayContent TEXT,
                Reserved0 TEXT,
                Reserved1 TEXT,
                Reserved2 TEXT,
                Reserved3 TEXT,
                Reserved4 TEXT,
                Reserved5 TEXT,
                Reserved6 TEXT,
                CompressContent BLOB,
                BytesExtra BLOB,
                BytesTrans BLOB
            )
            """
        )
        conn.execute("INSERT INTO Name2ID(rowid, UsrName) VALUES (?, ?)", (1, "friend"))
        conn.execute("INSERT INTO Name2ID(rowid, UsrName) VALUES (?, ?)", (2, "room@chatroom"))
        conn.execute(
            "INSERT INTO MSG VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)",
            (1, 1, 1001, 1, 0, 0, 1000, 1000, 0, 0, 0, 0, 0, "friend", "hello", ""),
        )
        conn.execute(
            "INSERT INTO MSG VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)",
            (2, 1, 1002, 1, 0, 1, 1100, 1100, 0, 0, 0, 0, 0, "friend", "reply", ""),
        )
        conn.execute(
            "INSERT INTO MSG VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)",
            (3, 2, 1003, 1, 0, 0, 1200, 1200, 0, 0, 0, 0, 0, "room@chatroom", "member:\nroom msg", ""),
        )
        member_bytes = b"member"
        bytes_extra = b"\x1a" + bytes([len(member_bytes) + 3]) + b"\x08\x01\x12" + bytes([len(member_bytes)]) + member_bytes
        conn.execute(
            """
            INSERT INTO MSG (
                localId, TalkerId, MsgSvrID, Type, SubType, IsSender, CreateTime, Sequence,
                StatusEx, FlagEx, Status, MsgServerSeq, MsgSequence, StrTalker, StrContent,
                DisplayContent, BytesExtra
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (4, 2, 1004, 1, 0, 0, 1300, 1300, 0, 0, 0, 0, 0, "room@chatroom", "room msg without prefix", "", bytes_extra),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_media_msg(path, local_id, local_type, content, bytes_extra=b""):
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            INSERT INTO MSG (
                localId, TalkerId, MsgSvrID, Type, SubType, IsSender, CreateTime, Sequence,
                StatusEx, FlagEx, Status, MsgServerSeq, MsgSequence, StrTalker, StrContent,
                DisplayContent, BytesExtra
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                local_id,
                2,
                2000 + local_id,
                local_type,
                0,
                0,
                1400 + local_id,
                1400 + local_id,
                0,
                0,
                0,
                0,
                0,
                "room@chatroom",
                content,
                "",
                bytes_extra,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_wechat_legacy_storage_maps_old_schema_to_wechat_db_shape(monkeypatch, tmp_path):
    root = tmp_path / "decrypted"
    _init_micro_msg(root / "Msg" / "MicroMsg.db")
    _init_msg(root / "Msg" / "Multi" / "MSG0.db")
    monkeypatch.setenv("CODEYUN_WECHAT_LEGACY_SELF_USERNAME", "self")

    storage = WeChatLegacyDbStorage(root)

    status = storage.status()
    assert status["source_format"] == "wechat_3"
    assert status["ready"] is True
    assert status["self_username"] == "self"

    chats = storage.list_chats(limit=10)
    assert [chat["username"] for chat in chats] == ["room@chatroom", "friend"]
    assert chats[1]["name"] == "Remark Friend"
    assert chats[1]["avatar_data_url"] == "https://example.test/friend.jpg"
    assert chats[0]["avatar_data_url"] == "https://example.test/friend.jpg"
    assert storage.count_chats() == 2

    payload = storage.list_messages(chat_username="friend", limit=10, order="asc")
    assert payload["total"] == 2
    assert [item["message_text"] for item in payload["items"]] == ["hello", "reply"]
    assert payload["items"][1]["sender_username"] == "self"

    room_payload = storage.list_messages(chat_username="room@chatroom", limit=10, order="asc")
    assert room_payload["items"][0]["sender_username"] == "member"
    assert room_payload["items"][1]["sender_username"] == "member"
    assert room_payload["items"][1]["sender_name"] == "Member"
    assert room_payload["items"][1]["sender_avatar_data_url"] == "https://example.test/member.jpg"
    assert storage.message_types(chat_username="friend") == [{"local_type": 1, "count": 2}]


def test_wechat_legacy_storage_exports_image_and_emoji_resources(monkeypatch, tmp_path):
    root = tmp_path / "decrypted"
    msg_path = root / "Msg" / "Multi" / "MSG0.db"
    _init_micro_msg(root / "Msg" / "MicroMsg.db")
    _init_msg(msg_path)
    account_root = tmp_path / "WeChat Files" / "wxid_test"
    image_source = account_root / "FileStorage" / "MsgAttach" / "room" / "Thumb" / "2026-05" / "abc_t.dat"
    image_source.parent.mkdir(parents=True, exist_ok=True)
    image_bytes = bytes.fromhex("ffd8ffe000104a4649460001010000010001ffd9")
    image_source.write_bytes(bytes(byte ^ 0x9C for byte in image_bytes))
    image_rel = b"wxid_test\\FileStorage\\MsgAttach\\room\\Thumb\\2026-05\\abc_t.dat"
    _insert_media_msg(
        msg_path,
        5,
        3,
        '<msg><img length="20" md5="image-md5"></img></msg>',
        b"\x00" + image_rel + b"\x00",
    )

    def fake_download_media_url(url: str, target_dir: Path, stem: str) -> Path:
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{stem}.jpg"
        target.write_bytes(image_bytes)
        return target

    monkeypatch.setattr(legacy_db, "_download_legacy_media_url", fake_download_media_url)
    _insert_media_msg(
        msg_path,
        6,
        47,
        '<msg><emoji md5="emoji-md5" len="20" cdnurl="http://example.test/emoji.jpg"></emoji></msg>',
    )
    monkeypatch.setenv("CODEYUN_WECHAT_LEGACY_ACCOUNT_ROOT", str(account_root))

    storage = WeChatLegacyDbStorage(root)
    payload = storage.list_messages(chat_username="room@chatroom", limit=10, order="asc", include_resources=True)
    image_row = next(item for item in payload["items"] if item["raw_local_id"] == 5)
    emoji_row = next(item for item in payload["items"] if item["raw_local_id"] == 6)
    image_export = image_row["resource"]["items"][0]["export"]
    emoji_export = emoji_row["resource"]["items"][0]["export"]

    assert image_export["decoded_from_dat"] is True
    assert Path(image_export["stored_path"]).read_bytes().startswith(b"\xff\xd8\xff")
    assert emoji_export["download_name"].startswith("image/wx3_emoji_")
    assert Path(emoji_export["stored_path"]).read_bytes().startswith(b"\xff\xd8\xff")


def test_wechat_archive_db_devices_select_device(monkeypatch, tmp_path):
    device_root = tmp_path / "codepc_test"
    legacy_root = device_root / "wechat_legacy" / "decrypted"
    _init_micro_msg(legacy_root / "Msg" / "MicroMsg.db")
    _init_msg(legacy_root / "Msg" / "Multi" / "MSG0.db")
    monkeypatch.setattr(wechat_archive, "get_settings", lambda: SimpleNamespace(data_dir=device_root))

    devices = wechat_archive.list_wechat_db_devices()["items"]
    assert devices[0]["id"] == "codepc_test"
    assert devices[0]["source_format"] == "wechat_3"
    assert devices[0]["ready"] is True

    chats = wechat_archive.list_wechat_db_chats(device_id="codepc_test", limit=1)
    assert chats["device_id"] == "codepc_test"
    assert chats["total"] == 2

    messages = wechat_archive.list_wechat_db_messages(chat_username=chats["items"][0]["username"], device_id="codepc_test")
    assert messages["device_id"] == "codepc_test"
    assert messages["total"] == 2


def test_wechat_archive_db_devices_include_extra_roots(monkeypatch, tmp_path):
    current_root = tmp_path / "codepc_current"
    extra_root = tmp_path / "archives" / "codepc_extra"
    legacy_root = extra_root / "wechat_legacy" / "decrypted"
    _init_micro_msg(legacy_root / "Msg" / "MicroMsg.db")
    _init_msg(legacy_root / "Msg" / "Multi" / "MSG0.db")
    monkeypatch.setattr(wechat_archive, "get_settings", lambda: SimpleNamespace(data_dir=current_root))
    monkeypatch.setenv("CODEYUN_WECHAT_DEVICE_ROOTS", str(extra_root))

    devices = wechat_archive.list_wechat_db_devices()["items"]
    extra = next(item for item in devices if item["id"] == "codepc_extra")
    assert extra["current"] is False
    assert extra["source_format"] == "wechat_3"
    assert extra["ready"] is True

    chats = wechat_archive.list_wechat_db_chats(device_id="codepc_extra", limit=1)
    assert chats["device_id"] == "codepc_extra"
    assert chats["total"] == 2


def test_wechat_archive_remote_device_proxy_keeps_pagination(monkeypatch):
    entry = UserDevice(
        entry_id="entry-mf",
        user_id=1,
        device_id="codepc_mf",
        name="codepc_mf",
        mode="remote",
        server_url="http://codepc-mf:8000",
        token="remote-token",
    )

    class FakeSession:
        def exec(self, *_args, **_kwargs):
            return [entry]

        def get(self, _model, entry_id):
            return entry if entry_id == entry.entry_id else None

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}
        text = ""

        def json(self):
            return {
                "items": [{"username": "room@chatroom"}],
                "total": 321,
                "db_storage_path": "/remote/db_storage",
            }

    calls = []

    def fake_get(url, *, headers=None, params=None, **kwargs):
        calls.append({"url": url, "headers": headers, "params": params, "kwargs": kwargs})
        return FakeResponse()

    monkeypatch.setattr(wechat_archive.requests, "get", fake_get)
    device_id = wechat_archive._remote_wechat_device_public_id(entry.entry_id, entry.device_id)

    payload = wechat_archive.list_wechat_db_chats(
        device_id=device_id,
        q="三清",
        limit=50,
        offset=100,
        scope="main",
        session=FakeSession(),
        current_user=SimpleNamespace(id=1),
    )

    assert payload["device_id"] == device_id
    assert payload["remote_device_id"] == "codepc_mf"
    assert payload["entry_id"] == "entry-mf"
    assert payload["total"] == 321
    assert calls[0]["url"] == "http://codepc-mf:8000/api/wechat-archive/db-chats"
    assert calls[0]["headers"]["X-Device-Token"] == "remote-token"
    assert calls[0]["params"] == {
        "q": "三清",
        "limit": 50,
        "offset": 100,
        "scope": "main",
        "device_id": "codepc_mf",
    }
