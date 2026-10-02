"""Durable message inbox, hook progress and reply outbox owned by CodeYun.

This is our event store, never a WeChat internal database. Cursor publication
and inbox insertion share a transaction. Sending has an explicit uncertain
state because the native WeChat send API has no idempotency key.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import time
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")


def should_roll_session(session_day: str | None, last_question_at: float | None, now: float) -> bool:
    """Same local day always reuses; rollover needs a >=2h question gap."""
    if not session_day:
        return True
    return (datetime.fromtimestamp(now, SHANGHAI).date().isoformat() != session_day
            and (last_question_at is None or now - last_question_at >= 7200))


class AgentStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS inbox(
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT NOT NULL,
                    message_id TEXT NOT NULL, chat_id TEXT NOT NULL, sender_id TEXT NOT NULL,
                    timestamp REAL NOT NULL, data TEXT NOT NULL,
                    UNIQUE(account_id,message_id));
                CREATE INDEX IF NOT EXISTS inbox_chat ON inbox(account_id,chat_id,seq);
                CREATE TABLE IF NOT EXISTS hooks(
                    key TEXT PRIMARY KEY, account_id TEXT NOT NULL, chat_id TEXT NOT NULL,
                    consumed_seq INTEGER NOT NULL DEFAULT 0, awake_until REAL NOT NULL DEFAULT 0,
                    thread_id TEXT, session_day TEXT, last_question_at REAL,
                    summary TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'idle',
                    turn_id TEXT, last_error TEXT, retry_at REAL NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS outbox(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, hook_key TEXT NOT NULL,
                    through_seq INTEGER NOT NULL, text TEXT NOT NULL, status TEXT NOT NULL,
                    created_at REAL NOT NULL, result TEXT, UNIQUE(hook_key,through_seq));
            """)

    @contextmanager
    def transaction(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_meta(self, key: str, default=None):
        with self.transaction() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set_meta(self, key: str, value):
        with self.transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, json.dumps(value, ensure_ascii=False)))

    def ingest(self, account_id: str, batch: dict) -> int:
        """Exactly one durable event per stable provider identity, even on replay."""
        with self.transaction() as conn:
            inserted = 0
            for event in batch["events"]:
                result = conn.execute("INSERT OR IGNORE INTO inbox(account_id,message_id,chat_id,sender_id,timestamp,data) "
                                      "VALUES(?,?,?,?,?,?)", (account_id, event["message_id"], event["chat_id"],
                                       event["sender_id"], event["timestamp"], json.dumps(event, ensure_ascii=False)))
                inserted += result.rowcount
            conn.execute("INSERT OR REPLACE INTO meta VALUES(?,?)",
                         ("cursor:" + account_id, json.dumps(batch["cursor"], ensure_ascii=False)))
            return inserted

    def ensure_hook(self, key: str, account_id: str, chat_id: str):
        with self.transaction() as conn:
            result = conn.execute("INSERT OR IGNORE INTO hooks(key,account_id,chat_id,consumed_seq) "
                                  "VALUES(?,?,?,COALESCE((SELECT MAX(seq) FROM inbox WHERE account_id=? AND chat_id=?),0))",
                                  (key, account_id, chat_id, account_id, chat_id))
            if result.rowcount:
                conn.execute("INSERT OR REPLACE INTO meta VALUES(?,?)",
                             ("hook_started:" + key, json.dumps(int(time.time()))))
            row = conn.execute("SELECT * FROM hooks WHERE key=?", (key,)).fetchone()
            if row["account_id"] != account_id or row["chat_id"] != chat_id:
                raise ValueError("Hook identity cannot change; create a new key")

    def hook(self, key: str) -> dict:
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM hooks WHERE key=?", (key,)).fetchone()
            if row is None:
                raise KeyError(key)
            return dict(row)

    def update_hook(self, key: str, **values):
        allowed = {"consumed_seq", "awake_until", "thread_id", "session_day", "last_question_at", "summary", "status", "turn_id", "last_error", "retry_at"}
        if not values or not values.keys() <= allowed:
            raise ValueError("Unknown hook field")
        with self.transaction() as conn:
            conn.execute("UPDATE hooks SET " + ",".join(f"{name}=?" for name in values) + " WHERE key=?",
                         (*values.values(), key))

    def messages(self, key: str, after: int = 0, *, limit: int = 1000) -> list[dict]:
        hook = self.hook(key)
        with self.transaction() as conn:
            rows = conn.execute("SELECT seq,data FROM inbox WHERE account_id=? AND chat_id=? AND seq>? AND sender_id<>? "
                                "ORDER BY seq LIMIT ?", (hook["account_id"], hook["chat_id"], after, hook["account_id"], limit)).fetchall()
            return [{**json.loads(row["data"]), "seq": row["seq"]} for row in rows]

    def recent(self, key: str, through: int, limit: int = 20) -> list[dict]:
        hook = self.hook(key)
        with self.transaction() as conn:
            rows = conn.execute("SELECT seq,data FROM inbox WHERE account_id=? AND chat_id=? AND seq<=? "
                                "ORDER BY seq DESC LIMIT ?", (hook["account_id"], hook["chat_id"], through, limit)).fetchall()
            return [{**json.loads(row["data"]), "seq": row["seq"]} for row in reversed(rows)]

    def deliveries(self, key: str, limit: int = 5) -> list[dict]:
        """Engineering receipts disambiguate a model draft from a delivered reply."""
        with self.transaction() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT through_seq,text,status,created_at FROM outbox WHERE hook_key=? ORDER BY id DESC LIMIT ?", (key, limit))]

    def recover(self):
        """A crashed send is uncertain; incomplete turns keep their inbox unread."""
        with self.transaction() as conn:
            conn.execute("UPDATE outbox SET status='uncertain' WHERE status='sending'")
            conn.execute("UPDATE hooks SET status='idle',turn_id=NULL WHERE status='running'")
            conn.execute("UPDATE hooks SET status='uncertain',last_error='发送结果不明，须核验后继续' "
                         "WHERE key IN (SELECT hook_key FROM outbox WHERE status='uncertain')")

    def reserve_reply(self, key: str, through: int, text: str) -> int | None:
        """Commit sending intent only when all non-self events were considered."""
        with self.transaction() as conn:
            hook = conn.execute("SELECT * FROM hooks WHERE key=?", (key,)).fetchone()
            newer = conn.execute("SELECT 1 FROM inbox WHERE account_id=? AND chat_id=? AND seq>? AND sender_id<>? LIMIT 1",
                                 (hook["account_id"], hook["chat_id"], through, hook["account_id"])).fetchone()
            if newer:
                return None
            conn.execute("INSERT OR IGNORE INTO outbox(hook_key,through_seq,text,status,created_at) VALUES(?,?,?,'sending',?)",
                         (key, through, text, time.time()))
            row = conn.execute("SELECT id,status FROM outbox WHERE hook_key=? AND through_seq=?", (key, through)).fetchone()
            if row["status"] == "not_sent":
                conn.execute("UPDATE outbox SET status='sending',text=?,created_at=? WHERE id=?",
                             (text, time.time(), row["id"]))
                return row["id"]
            # Existing sending/sent/uncertain is never blindly reissued.
            if conn.execute("SELECT changes()").fetchone()[0] == 0:
                return None
            return row["id"]

    def finish_reply(self, reply_id: int, status: str, result: dict):
        if status not in {"sent", "uncertain"}:
            raise ValueError(status)
        with self.transaction() as conn:
            conn.execute("UPDATE outbox SET status=?,result=? WHERE id=? AND status='sending'",
                         (status, json.dumps(result, ensure_ascii=False, default=str), reply_id))
            row = conn.execute("SELECT * FROM outbox WHERE id=?", (reply_id,)).fetchone()
            if status == "sent":
                conn.execute("UPDATE hooks SET consumed_seq=MAX(consumed_seq,?),status='idle',turn_id=NULL,last_error=NULL WHERE key=?",
                             (row["through_seq"], row["hook_key"]))
            else:
                conn.execute("UPDATE hooks SET status='uncertain',last_error=? WHERE key=?",
                             (str(result), row["hook_key"]))

    def reconcile_reply(self, reply_id: int, *, sent: bool, evidence: str) -> dict:
        """Explicit operator resolution after querying actual WeChat delivery.

        sent=False requires positive evidence of non-delivery; no automatic
        timeout or absence of an immediate echo is sufficient for a retry.
        """
        if not evidence.strip():
            raise ValueError("Delivery evidence required")
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM outbox WHERE id=?", (reply_id,)).fetchone()
            if row is None or row["status"] != "uncertain":
                raise ValueError("Reply is not uncertain")
            conn.execute("UPDATE outbox SET status=?,result=? WHERE id=?",
                         ("sent" if sent else "not_sent", json.dumps({"evidence": evidence}, ensure_ascii=False), reply_id))
            if sent:
                conn.execute("UPDATE hooks SET consumed_seq=MAX(consumed_seq,?),status='idle',turn_id=NULL,last_error=NULL WHERE key=?",
                             (row["through_seq"], row["hook_key"]))
            else:
                conn.execute("UPDATE hooks SET status='idle',last_error='已核验未送达，允许重新检查并发送',retry_at=0 WHERE key=?",
                             (row["hook_key"],))
            return {"reply_id": reply_id, "sent": sent, "evidence": evidence}

    def status(self) -> dict:
        with self.transaction() as conn:
            return {"hooks": [dict(row) for row in conn.execute("SELECT * FROM hooks")],
                    "event_count": conn.execute("SELECT COUNT(*) FROM inbox").fetchone()[0],
                    "outbox": [dict(row) for row in conn.execute("SELECT * FROM outbox ORDER BY id DESC LIMIT 20")]}
