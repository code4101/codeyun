from __future__ import annotations

import hashlib
import io
import json
import sqlite3
from pathlib import Path

import pandas as pd

from backend.core.freebill.core import (
    get_freebill_connection,
    import_wechat_excel_bytes,
    rebuild_freebill_records_from_raw_files,
)
from backend.core.freebill.wechat_local_db import (
    WECHAT_LOCAL_SNAPSHOT_FORMAT,
    WECHAT_TRANSFER_LOCAL_TYPE,
    parse_wechat_local_payment_db,
    sync_wechat_local_db_to_freebill,
)


WECHAT_PAY_USERNAME = "gh_test_wechat_pay"
WECHAT_PAY_TRADE_NO = "4200000000000000000000000001"
WECHAT_SELF_USERNAME = "wxid_test_self"
WECHAT_COUNTERPARTY_USERNAME = "wxid_test_friend"
WECHAT_TRANSFER_TRADE_NO = "53010000000000000000000000000001"
WECHAT_TRANSFER_ID = "1000050001202602010000000000001"


def _payment_message_xml(
    *,
    trade_no: str = WECHAT_PAY_TRADE_NO,
    counterparty: str = "早餐店",
    amount: str = "12.34",
    payment_label: str = "使用零钱支付",
) -> str:
    return f"""
    <msg>
      <appmsg>
        <title>已支付 ￥{amount}</title>
        <mmreader>
          <template_header>
            <title>微信支付凭证</title>
            <display_name>{counterparty}</display_name>
            <transaction_id>{trade_no}</transaction_id>
          </template_header>
          <template_detail>
            <is_pay_recepit>1</is_pay_recepit>
            <line_content>
              <topline>
                <key><word>{payment_label}</word></key>
                <value><word>￥{amount}</word></value>
              </topline>
            </line_content>
          </template_detail>
        </mmreader>
      </appmsg>
    </msg>
    """.strip()


def _promo_message_xml() -> str:
    return """
    <msg><appmsg><title>记账月报</title><mmreader>
      <template_header><title>记账月报</title></template_header>
      <template_detail />
    </mmreader></appmsg></msg>
    """.strip()


def _missing_trade_no_message_xml() -> str:
    return _payment_message_xml(trade_no="", amount="8.88", payment_label="通过零钱免密支付")


def _transfer_message_xml(
    *,
    subtype: int,
    trade_no: str = WECHAT_TRANSFER_TRADE_NO,
    transfer_id: str = WECHAT_TRANSFER_ID,
    amount: str = "66.00",
    begin_time: int = 1769911200,
) -> str:
    return f"""
    <msg><appmsg><title>微信转账</title><type>2000</type>
      <wcpayinfo>
        <paysubtype>{subtype}</paysubtype>
        <feedesc>￥{amount}</feedesc>
        <transcationid>{trade_no}</transcationid>
        <transferid>{transfer_id}</transferid>
        <begintransfertime>{begin_time}</begintransfertime>
        <receiver_username>{WECHAT_SELF_USERNAME}</receiver_username>
      </wcpayinfo>
    </appmsg></msg>
    """.strip()


def _create_wechat_db_storage(root: Path) -> Path:
    contact_dir = root / "contact"
    message_dir = root / "message"
    contact_dir.mkdir(parents=True)
    message_dir.mkdir(parents=True)

    contact_db = contact_dir / "contact.db"
    with sqlite3.connect(contact_db) as conn:
        conn.execute("CREATE TABLE name2id (username TEXT)")
        conn.execute(
            """
            CREATE TABLE contact (
                id INTEGER PRIMARY KEY,
                username INTEGER,
                remark TEXT,
                nick_name TEXT,
                alias TEXT
            )
            """
        )
        cursor = conn.execute("INSERT INTO name2id (username) VALUES (?)", (WECHAT_PAY_USERNAME,))
        conn.execute(
            "INSERT INTO contact (username, remark, nick_name, alias) VALUES (?, '', '微信支付', '')",
            (cursor.lastrowid,),
        )
        friend_cursor = conn.execute("INSERT INTO name2id (username) VALUES (?)", (WECHAT_COUNTERPARTY_USERNAME,))
        conn.execute(
            "INSERT INTO contact (username, remark, nick_name, alias) VALUES (?, '测试好友', '好友', '')",
            (friend_cursor.lastrowid,),
        )

    biz_message_db = message_dir / "biz_message_0.db"
    table_name = "Msg_" + hashlib.md5(WECHAT_PAY_USERNAME.encode("utf-8")).hexdigest()
    with sqlite3.connect(biz_message_db) as conn:
        conn.execute("CREATE TABLE Name2Id (user_name TEXT, is_session INTEGER)")
        conn.execute("INSERT INTO Name2Id VALUES (?, 1)", (WECHAT_PAY_USERNAME,))
        conn.execute(
            f"""
            CREATE TABLE "{table_name}" (
                local_id INTEGER,
                server_id INTEGER,
                local_type INTEGER,
                create_time INTEGER,
                message_content BLOB,
                compress_content BLOB,
                source BLOB
            )
            """
        )
        messages = [
            (1, 101, _payment_message_xml()),
            (2, 102, _promo_message_xml()),
            (3, 103, _missing_trade_no_message_xml()),
        ]
        for local_id, server_id, content in messages:
            conn.execute(
                f"INSERT INTO \"{table_name}\" VALUES (?, ?, 49, 1769911200, ?, NULL, NULL)",
                (local_id, server_id, content),
            )

    message_db = message_dir / "message_0.db"
    chat_table_name = "Msg_" + hashlib.md5(WECHAT_COUNTERPARTY_USERNAME.encode("utf-8")).hexdigest()
    with sqlite3.connect(message_db) as conn:
        conn.execute("CREATE TABLE Name2Id (user_name TEXT PRIMARY KEY, is_session INTEGER)")
        self_cursor = conn.execute("INSERT INTO Name2Id VALUES (?, 0)", (WECHAT_SELF_USERNAME,))
        friend_cursor = conn.execute("INSERT INTO Name2Id VALUES (?, 1)", (WECHAT_COUNTERPARTY_USERNAME,))
        conn.execute(
            f"""
            CREATE TABLE "{chat_table_name}" (
                local_id INTEGER,
                server_id INTEGER,
                local_type INTEGER,
                real_sender_id INTEGER,
                create_time INTEGER,
                message_content BLOB,
                compress_content BLOB,
                source BLOB
            )
            """
        )
        conn.execute(
            f'INSERT INTO "{chat_table_name}" VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)',
            (1, 201, WECHAT_TRANSFER_LOCAL_TYPE, friend_cursor.lastrowid, 1769911200, _transfer_message_xml(subtype=1)),
        )
        conn.execute(
            f'INSERT INTO "{chat_table_name}" VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)',
            (2, 202, WECHAT_TRANSFER_LOCAL_TYPE, self_cursor.lastrowid, 1769911260, _transfer_message_xml(subtype=3)),
        )
    (root.parent / "sync_state.json").write_text(
        json.dumps({"live_account_root": rf"C:\Users\test\xwechat_files\{WECHAT_SELF_USERNAME}_1234"}),
        encoding="utf-8",
    )
    return root


def _official_wechat_excel_bytes() -> bytes:
    output = io.BytesIO()
    columns = [
        "交易时间",
        "交易类型",
        "交易对方",
        "商品",
        "收/支",
        "金额(元)",
        "当前状态",
        "交易单号",
        "商户单号",
        "备注",
    ]
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame(
            [[
                "2026-02-01 10:00:00",
                "餐饮美食",
                "早餐店",
                "豆浆",
                "支出",
                "¥12.34",
                "支付成功",
                WECHAT_PAY_TRADE_NO,
                "MERCHANT-001",
                "官方账单",
            ]],
            columns=columns,
        ).to_excel(writer, index=False)
    return output.getvalue()


def test_parse_wechat_local_payment_db_only_keeps_authoritative_transactions(tmp_path: Path) -> None:
    db_root = _create_wechat_db_storage(tmp_path / "wechat-db")

    result = parse_wechat_local_payment_db(db_root)

    assert result["format"] == WECHAT_LOCAL_SNAPSHOT_FORMAT
    assert result["scanned"] == 3
    assert result["parsed"] == 1
    assert result["skipped_non_transaction"] == 1
    assert result["skipped_missing_trade_no"] == 1
    record = result["records"][0]
    assert record["trade_no"] == WECHAT_PAY_TRADE_NO
    assert record["counterparty"] == "早餐店"
    assert record["amount"] == 12.34
    assert record["direction"] == "支出"
    assert record["account_no"] == "零钱"
    assert record["cash_type"] == "零钱"
    assert record["raw_sequence"] == "wechat-biz:101"


def test_sync_wechat_local_db_is_idempotent_and_rebuildable(tmp_path: Path) -> None:
    db_root = _create_wechat_db_storage(tmp_path / "wechat-db")
    work_dir = tmp_path / "freebill"

    first = sync_wechat_local_db_to_freebill(
        db_storage_root=db_root,
        work_dir=work_dir,
        refresh_source=False,
    )
    second = sync_wechat_local_db_to_freebill(
        db_storage_root=db_root,
        work_dir=work_dir,
        refresh_source=False,
    )

    assert first["inserted"] == 2
    assert first["updated"] == 0
    assert first["ignored"] == 2
    assert first["chat_transfer"]["parsed"] == 1
    assert first["raw_file"]["filename"] == "微信本地账单增量.json"
    assert second["inserted"] == 0
    assert second["processed"] == 0
    assert second["scanned"] == 0
    assert second["raw_file"] is None
    with get_freebill_connection(work_dir) as conn:
        assert conn.execute("SELECT COUNT(*) FROM bill_records").fetchone()[0] == 2
        raw_row = conn.execute("SELECT extension, note FROM freebill_raw_files").fetchone()
        assert tuple(raw_row) == (".json", WECHAT_LOCAL_SNAPSHOT_FORMAT)

    with get_freebill_connection(work_dir) as conn:
        conn.execute("DELETE FROM bill_records")
        conn.commit()
    rebuild = rebuild_freebill_records_from_raw_files(work_dir=work_dir, backup=False)
    assert rebuild["after_records"] == 2
    assert rebuild["imported_files"] == 1


def test_chat_transfer_sync_advances_watermark_and_only_reads_new_rows(tmp_path: Path) -> None:
    db_root = _create_wechat_db_storage(tmp_path / "wechat-db")
    work_dir = tmp_path / "freebill"
    first = sync_wechat_local_db_to_freebill(
        db_storage_root=db_root,
        work_dir=work_dir,
        refresh_source=False,
    )
    assert first["chat_transfer"]["scanned"] == 2

    message_db = db_root / "message" / "message_0.db"
    table_name = "Msg_" + hashlib.md5(WECHAT_COUNTERPARTY_USERNAME.encode("utf-8")).hexdigest()
    with sqlite3.connect(message_db) as conn:
        self_id = conn.execute(
            "SELECT rowid FROM Name2Id WHERE user_name = ?",
            (WECHAT_SELF_USERNAME,),
        ).fetchone()[0]
        friend_id = conn.execute(
            "SELECT rowid FROM Name2Id WHERE user_name = ?",
            (WECHAT_COUNTERPARTY_USERNAME,),
        ).fetchone()[0]
        conn.execute(
            f'INSERT INTO "{table_name}" VALUES (?, ?, 1, ?, ?, ?, NULL, NULL)',
            (3, 203, friend_id, 1769911320, "普通聊天消息"),
        )
        for local_id, server_id, sender_id, subtype, create_time in [
            (4, 204, friend_id, 1, 1769911440),
            # A newly inserted row may carry an older business timestamp. The
            # monotonic local_id watermark must still include it.
            (5, 205, self_id, 3, 1769911000),
        ]:
            conn.execute(
                f'INSERT INTO "{table_name}" VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)',
                (
                    local_id,
                    server_id,
                    WECHAT_TRANSFER_LOCAL_TYPE,
                    sender_id,
                    create_time,
                    _transfer_message_xml(
                        subtype=subtype,
                        trade_no="53010000000000000000000000000002",
                        transfer_id="1000050001202602010000000000002",
                        amount="88.00",
                        begin_time=1769911440,
                    ),
                ),
            )

    second = sync_wechat_local_db_to_freebill(
        db_storage_root=db_root,
        work_dir=work_dir,
        refresh_source=False,
    )
    third = sync_wechat_local_db_to_freebill(
        db_storage_root=db_root,
        work_dir=work_dir,
        refresh_source=False,
    )

    assert second["chat_transfer"]["scanned"] == 2
    assert second["chat_transfer"]["parsed"] == 1
    assert second["inserted"] == 1
    assert third["scanned"] == 0
    state = json.loads((work_dir / "wechat_local_sync.state.json").read_text(encoding="utf-8"))
    chat_watermarks = [
        value
        for key, value in state["watermarks"].items()
        if key.startswith("chat:")
    ]
    assert chat_watermarks == [{"create_time": 1769911440, "local_id": 5}]
    with get_freebill_connection(work_dir) as conn:
        assert conn.execute("SELECT COUNT(*) FROM bill_records").fetchone()[0] == 3


def test_official_wechat_excel_replaces_local_notification_record(tmp_path: Path) -> None:
    db_root = _create_wechat_db_storage(tmp_path / "wechat-db")
    work_dir = tmp_path / "freebill"
    sync_wechat_local_db_to_freebill(
        db_storage_root=db_root,
        work_dir=work_dir,
        refresh_source=False,
    )

    result = import_wechat_excel_bytes("微信支付账单流水文件.xlsx", _official_wechat_excel_bytes(), work_dir=work_dir)

    assert result["inserted"] == 0
    assert result["updated"] == 1
    assert result["skipped"] == 0
    with get_freebill_connection(work_dir) as conn:
        row = conn.execute(
            "SELECT merchant_order_no, type, product_name, raw_sequence FROM bill_records WHERE trade_no = ?",
            (WECHAT_PAY_TRADE_NO,),
        ).fetchone()
    assert tuple(row) == ("MERCHANT-001", "餐饮美食", "豆浆", None)

    rebuild = rebuild_freebill_records_from_raw_files(work_dir=work_dir, backup=False)
    assert rebuild["after_records"] == 2
    with get_freebill_connection(work_dir) as conn:
        rebuilt = conn.execute(
            "SELECT merchant_order_no, product_name FROM bill_records WHERE trade_no = ?",
            (WECHAT_PAY_TRADE_NO,),
        ).fetchone()
    assert tuple(rebuilt) == ("MERCHANT-001", "豆浆")


def test_wechat_archive_job_runs_every_two_hours() -> None:
    from backend.core.jobs import scheduler

    spec = scheduler.get_background_task_spec(scheduler.WECHAT_ARCHIVE_INCREMENTAL_SYNC_TASK_KEY)
    policy = scheduler._default_background_task_schedule_policy(spec.key)

    assert policy["trigger"] == {"type": "interval", "minutes": 120, "anchor": "last_finish"}
    assert policy["outcome"]["on_failure"] == {"type": "retry_after", "minutes": 10}
    assert spec.schedule_label == "每 2 小时同步"


def test_wechat_archive_job_chains_freebill_incremental_sync(monkeypatch, tmp_path: Path) -> None:
    from backend.api import wechat_archive
    from backend.core.freebill import wechat_local_db

    class FakeStorage:
        root = tmp_path / "db_storage"

        @staticmethod
        def sync_from_live(*, export_media: bool):
            return {"copy": {"copied": 1}, "export_media": export_media}

    captured: dict[str, object] = {}

    def fake_freebill_sync(**kwargs):
        captured.update(kwargs)
        return {"inserted": 2, "scanned": 3}

    monkeypatch.setattr(wechat_archive, "_open_wechat_db_storage", lambda: FakeStorage())
    monkeypatch.setattr(wechat_local_db, "sync_wechat_local_db_to_freebill", fake_freebill_sync)

    result = wechat_archive._run_wechat_db_live_sync_job(
        {"mode": "db_storage_live", "save_media": False}
    )

    assert result["result"]["copy"]["copied"] == 1
    assert result["freebill"] == {"inserted": 2, "scanned": 3}
    assert captured == {"db_storage_root": FakeStorage.root, "refresh_source": False}
