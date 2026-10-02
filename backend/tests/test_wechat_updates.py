"""Provider contracts: authenticated WAL snapshots and all-chat cursor semantics."""
import hashlib
import hmac
import sqlite3
import struct

import pytest

from pyxllib.autogui.wechat_db import decrypt_wechat_v4_db, _wx_db_reserve_size, SQLITE_HEADER
from pyxllib.autogui.wechat_updates import _checksum


def test_encrypted_wal_replaces_only_committed_pages_atomically(tmp_path):
    from Crypto.Cipher import AES
    from Crypto.Hash import SHA512
    from Crypto.Protocol.KDF import PBKDF2

    salt, key = b"s" * 16, b"k" * 32
    reserve = _wx_db_reserve_size()
    mac_key = PBKDF2(key, bytes(x ^ 0x3a for x in salt), dkLen=32, count=2, hmac_hash_module=SHA512)
    def encrypted(mark, pgno=1):
        offset = 16 if pgno == 1 else 0
        iv = b"i" * 16
        cipher = AES.new(key, AES.MODE_CBC, iv).encrypt(bytes([mark]) * (4096 - reserve - offset))
        mac = hmac.new(mac_key, cipher + iv, hashlib.sha512)
        mac.update(struct.pack("<I", pgno))
        return (salt if pgno == 1 else b"") + cipher + iv + mac.digest() + b"\0" * (reserve - 80)
    source, target = tmp_path / "encrypted.db", tmp_path / "snapshot.db"
    source.write_bytes(encrypted(65))
    header = struct.pack(">IIIIII", 0x377f0682, 3007000, 4096, 1, 123, 456)
    sums = _checksum(header, (0, 0), "<")
    wal = header + struct.pack(">II", *sums)
    for page, size in [(encrypted(66), 1), (encrypted(67, 2), 0)]:
        pgno = 1 if size else 2
        frame = struct.pack(">IIII", pgno, size, 123, 456)
        sums = _checksum(frame[:8] + page, sums, "<")
        wal += frame + struct.pack(">II", *sums) + page
    (tmp_path / "encrypted.db-wal").write_bytes(wal)
    assert decrypt_wechat_v4_db(source, target, key.hex(), "raw-derived-key")
    plain = target.read_bytes()
    assert len(plain) == 4096
    assert plain.startswith(SQLITE_HEADER + b"B" * 100)
    # A frame with valid WAL checksum but invalid authenticated ciphertext
    # cannot replace the last published snapshot.
    frame = struct.pack(">IIII", 1, 1, 123, 456)
    corrupt = bytearray(encrypted(68))
    corrupt[100] ^= 1
    sums = _checksum(header, (0, 0), "<")
    sums = _checksum(frame[:8] + corrupt, sums, "<")
    invalid = header + struct.pack(">II", *_checksum(header, (0, 0), "<")) + frame + struct.pack(">II", *sums) + corrupt
    (tmp_path / "encrypted.db-wal").write_bytes(invalid)
    with pytest.raises(ValueError, match="authentication"):
        decrypt_wechat_v4_db(source, target, key.hex(), "raw-derived-key")
    assert target.read_bytes() == plain


def test_account_cursor_covers_shards_and_replays_stable_events(tmp_path, monkeypatch):
    import time
    from pyxllib.autogui.wechat_db import WeChatDbStorage, message_table_name
    from pyxllib.autogui import weixin4_instrumentation

    folder = tmp_path / "message"
    folder.mkdir()
    chat = "123@chatroom"
    table = message_table_name(chat)
    def append(path, local_id, timestamp):
        with sqlite3.connect(path) as conn:
            conn.execute(f'INSERT INTO "{table}" VALUES(?,?,1,2,?,?,?,?)',
                         (local_id, local_id * 100, timestamp, "消息", "", "<msgsource/>"))
    for name in ("message_0.db", "message_1.db"):
        with sqlite3.connect(folder / name) as conn:
            conn.execute("CREATE TABLE Name2Id(user_name TEXT)")
            conn.executemany("INSERT INTO Name2Id VALUES(?)", [(chat,), ("user",)])
            conn.execute(f'CREATE TABLE "{table}" (local_id INTEGER PRIMARY KEY,server_id INTEGER,local_type INTEGER,'
                         'real_sender_id INTEGER,create_time INTEGER,message_content TEXT,compress_content TEXT,source TEXT)')
        append(folder / name, 1, int(time.time()) - 10000)
    storage = WeChatDbStorage(tmp_path)
    owner = str(tmp_path / "wxid_test_1234")
    monkeypatch.setattr(storage, "status", lambda: {"live_account_root": owner})
    monkeypatch.setattr(storage, "sync_from_live", lambda **kw: {"copy": {}, "decrypt": {}})
    monkeypatch.setattr(weixin4_instrumentation, "resolve_sender", lambda account: {"account_root": owner, "pid": 0, "create_time": 0})
    baseline = storage.poll_updates()
    assert baseline["events"] == []
    assert len(baseline["cursor"]["positions"]) == 2
    # Existing streams must not discard a late-arriving message with an old timestamp.
    for name in ("message_0.db", "message_1.db"):
        append(folder / name, 2, int(time.time()) - 100)
    first = storage.poll_updates(baseline["cursor"], limit=1)
    assert first["has_more"] and len(first["events"]) == 1
    second = storage.poll_updates(first["cursor"])
    assert len(second["events"]) == 1
    assert first["events"][0]["message_id"] != second["events"][0]["message_id"]
    assert storage.poll_updates(second["cursor"])["events"] == []
    replay = storage.poll_updates(baseline["cursor"])
    assert {e["message_id"] for e in replay["events"]} == {first["events"][0]["message_id"], second["events"][0]["message_id"]}
