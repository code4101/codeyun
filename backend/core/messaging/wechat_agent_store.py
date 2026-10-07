"""Durable message inbox, hook progress and reply outbox owned by CodeYun.

This is our event store, never a WeChat internal database. Cursor publication
and inbox insertion share a transaction. Sending has an explicit uncertain
state because the native WeChat send API has no idempotency key.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time
from backend.core.messaging.wechat_sessions import SHANGHAI, should_roll_session  # Public compatibility exports.


def is_sqlite_busy(exc: BaseException) -> bool:
    """Only lock contention is retryable; corruption and SQL errors must surface."""
    return (isinstance(exc, sqlite3.OperationalError)
            and (getattr(exc, 'sqlite_errorcode', 0) & 0xff) in
            {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED})


class AgentStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        # WAL is a persistent database setting, not a per-query operation.
        with sqlite3.connect(self.path, timeout=15) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
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
                CREATE TABLE IF NOT EXISTS resolved_questions(
                    hook_key TEXT NOT NULL, seq INTEGER NOT NULL, reply_id INTEGER,
                    PRIMARY KEY(hook_key,seq));
                CREATE TABLE IF NOT EXISTS message_routes(
                    hook_key TEXT NOT NULL, seq INTEGER NOT NULL, lane TEXT NOT NULL,
                    candidate TEXT, created_at REAL NOT NULL,
                    PRIMARY KEY(hook_key,seq));
                CREATE TABLE IF NOT EXISTS codex_subscriptions(
                    thread_id TEXT PRIMARY KEY, hook_key TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS codex_notifications(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL, hook_key TEXT NOT NULL, text TEXT NOT NULL,
                    created_at REAL NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                    reply_id INTEGER, UNIQUE(thread_id,turn_id));
                CREATE TABLE IF NOT EXISTS dot_pull_replies(
                    hook_key TEXT NOT NULL, request_id TEXT NOT NULL,
                    reply_id INTEGER NOT NULL UNIQUE, source_seqs TEXT NOT NULL,
                    reviewed_through INTEGER NOT NULL,
                    PRIMARY KEY(hook_key,request_id));
            """)

    @contextmanager
    def transaction(self, *, write: bool = True):
        """Readers share a WAL snapshot; mutations retain serialized admission.

        A deferred read transaction never competes for the writer lock. Writes
        still acquire it before checking invariants, preserving outbox ownership
        and cursor atomicity. The body is never replayed on a lock timeout.
        """
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            if not write:
                conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_meta(self, key: str, default=None):
        with self.transaction(write=False) as conn:
            row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def record_operational_notice(self, key: str, *, category: str, message: str) -> dict:
        """Durable, idempotent internal notice; never creates a WeChat outbox entry."""
        import hashlib
        if category != 'routine_housekeeping' or not message.strip() or len(message) > 2000:
            raise ValueError('Invalid operational notice')
        identity = hashlib.sha256((category + '\n' + message.strip()).encode()).hexdigest()
        name = f'operational_notice:{key}:{identity}'
        with self.transaction() as conn:
            if not conn.execute('SELECT 1 FROM hooks WHERE key=?', (key,)).fetchone():
                raise ValueError('Unknown operational notice recipient')
            previous = conn.execute('SELECT value FROM meta WHERE key=?', (name,)).fetchone()
            notice = json.loads(previous[0]) if previous else {'category':category,'message':message.strip(),
                         'created_at':time.time(),'occurrences':0}
            notice.update(last_seen=time.time(),occurrences=notice['occurrences']+1)
            conn.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (name,json.dumps(notice,ensure_ascii=False)))
            return notice

    def operational_notices(self, key: str) -> list[dict]:
        """Read internal notices for explicit operations queries, without sending."""
        prefix = f'operational_notice:{key}:'
        with self.transaction(write=False) as conn:
            return [json.loads(row['value']) for row in conn.execute(
                'SELECT value FROM meta WHERE substr(key,1,?)=? ORDER BY key', (len(prefix),prefix))]

    def subscribe_codex_thread(self, thread_id: str, hook_key: str, completed: list[dict], *, enabled=True):
        """Fix the recipient, baseline old turns, preserve receipts on re-enable."""
        with self.transaction() as conn:
            if not conn.execute('SELECT 1 FROM hooks WHERE key=?', (hook_key,)).fetchone():
                raise ValueError('Unknown destination hook')
            old = conn.execute('SELECT * FROM codex_subscriptions WHERE thread_id=?', (thread_id,)).fetchone()
            if old and old['hook_key'] != hook_key:
                raise ValueError('Thread recipient cannot change')
            conn.execute('INSERT INTO codex_subscriptions VALUES(?,?,?) ON CONFLICT(thread_id) '
                         'DO UPDATE SET enabled=excluded.enabled', (thread_id, hook_key, int(enabled)))
            if old is None:
                for turn in completed:
                    conn.execute("INSERT OR IGNORE INTO codex_notifications(thread_id,turn_id,hook_key,text,created_at,status) "
                                 "VALUES(?,?,?,?,?,'baseline')",
                                 (thread_id, turn['turn_id'], hook_key, '', time.time()))

    def codex_notifications_status(self):
        with self.transaction(write=False) as conn:
            return {'subscriptions': [dict(r) for r in conn.execute('SELECT * FROM codex_subscriptions')],
                    'notifications': [dict(r) for r in conn.execute('SELECT * FROM codex_notifications ORDER BY id DESC LIMIT 20')]}

    def configure_codex_reporting(self, thread_id: str, *, mode: str) -> dict:
        """Select one reporting source for a subscribed engineering thread.

        stages_only keeps explicit business receipts and suppresses native turn
        summaries, including differently worded repeats and window waits. The
        policy is read on every enqueue, so live workers need no restart. Existing
        accepted notifications and delivery receipts are never rewritten.
        """
        if mode not in {'turns_and_stages', 'stages_only'}:
            raise ValueError('Unknown Codex reporting mode')
        with self.transaction() as conn:
            if not conn.execute('SELECT 1 FROM codex_subscriptions WHERE thread_id=?', (thread_id,)).fetchone():
                raise ValueError('Unknown subscribed thread')
            value = {'mode': mode}
            conn.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',
                         ('codex_reporting:' + thread_id, json.dumps(value)))
            return value

    def enqueue_codex_completion(self, thread_id: str, turn_id: str, text: str, *, question_seqs=()):
        """Unknown/disabled threads cannot select recipients; replay is idempotent."""
        if not turn_id.strip() or not text.strip():
            raise ValueError('Native turn ID and result text required')
        with self.transaction() as conn:
            subscription = conn.execute('SELECT * FROM codex_subscriptions WHERE thread_id=? AND enabled=1',
                                        (thread_id,)).fetchone()
            if not subscription:
                return False
            policy = conn.execute('SELECT value FROM meta WHERE key=?',
                                  ('codex_reporting:' + thread_id,)).fetchone()
            if policy and json.loads(policy[0]).get('mode') == 'stages_only' and not turn_id.startswith('stage:'):
                return False
            # Question acknowledgements share the group processor's ownership.
            # A different thread/stage ID must not bypass the answered ledger.
            if question_seqs:
                hook = conn.execute('SELECT consumed_seq FROM hooks WHERE key=?', (subscription['hook_key'],)).fetchone()
                answered = [int(seq) <= hook['consumed_seq'] or bool(conn.execute(
                    'SELECT 1 FROM resolved_questions WHERE hook_key=? AND seq=?',
                    (subscription['hook_key'], int(seq))).fetchone()) for seq in question_seqs]
                if all(answered):
                    return False
                if any(answered):
                    raise ValueError('Split answered and unanswered questions before recording')
            inserted = conn.execute('INSERT OR IGNORE INTO codex_notifications(thread_id,turn_id,hook_key,text,created_at) '
                                    'VALUES(?,?,?,?,?)', (thread_id, turn_id, subscription['hook_key'], text, time.time()))
            return bool(inserted.rowcount)

    def reserve_codex_notification(self, notification_id: int):
        """Reserve a separate outbox identity without consuming any group question.

        Negative through_seq values belong exclusively to notifications. A
        sending/uncertain receipt survives crashes and cannot be auto-retried.
        """
        with self.transaction() as conn:
            row = conn.execute("SELECT n.* FROM codex_notifications n JOIN codex_subscriptions s "
                "ON s.thread_id=n.thread_id WHERE n.id=? AND n.status='pending' AND s.enabled=1",
                (notification_id,)).fetchone()
            if not row:
                return None
            owner = conn.execute('SELECT value FROM meta WHERE key=?', ('processor_owner:' + row['hook_key'],)).fetchone()
            if owner and json.loads(owner[0]).get('processor') == 'dot_pull':
                return None  # Pull mode has one caller-owned writer, no mirrored completions.
            hook = conn.execute('SELECT * FROM hooks WHERE key=?', (row['hook_key'],)).fetchone()
            if hook['status'] in {'running', 'uncertain'}:
                return None
            conn.execute("INSERT OR IGNORE INTO outbox(hook_key,through_seq,text,status,created_at) VALUES(?,?,?,'sending',?)",
                         (row['hook_key'], -row['id'], row['text'], time.time()))
            reply = conn.execute('SELECT * FROM outbox WHERE hook_key=? AND through_seq=?',
                                 (row['hook_key'], -row['id'])).fetchone()
            inserted = conn.execute('SELECT changes()').fetchone()[0]
            if reply['status'] == 'not_sent':
                conn.execute("UPDATE outbox SET status='sending' WHERE id=?", (reply['id'],))
            elif not inserted:
                return None
            conn.execute("UPDATE codex_notifications SET status='sending',reply_id=? WHERE id=?",
                         (reply['id'], row['id']))
            return reply['id']

    def pending_codex_notifications(self):
        with self.transaction(write=False) as conn:
            return [dict(r) for r in conn.execute("SELECT n.* FROM codex_notifications n JOIN codex_subscriptions s "
                "ON n.thread_id=s.thread_id WHERE n.status='pending' AND s.enabled=1 ORDER BY n.id LIMIT 20")]

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

    def latest_sequence(self, account_id: str) -> int:
        with self.transaction(write=False) as conn:
            return conn.execute('SELECT COALESCE(MAX(seq),0) FROM inbox WHERE account_id=?', (account_id,)).fetchone()[0]

    def owner_messages(self, account_id: str, sender_id: str, after: int, *, limit: int = 100) -> list[dict]:
        """Durable admission input; a separate cursor survives listener restarts."""
        with self.transaction(write=False) as conn:
            rows = conn.execute('SELECT seq,data FROM inbox WHERE account_id=? AND sender_id=? AND seq>? ORDER BY seq LIMIT ?',
                                (account_id, sender_id, after, limit)).fetchall()
            return [{**json.loads(row['data']), 'seq':row['seq']} for row in rows]

    def ensure_hook(self, key: str, account_id: str, chat_id: str, *, start_message_id: str | None = None):
        """Baseline manual registrations; retain the explicit creation request for automatic ones."""
        with self.transaction() as conn:
            start_seq = None
            if start_message_id:
                anchor = conn.execute('SELECT seq FROM inbox WHERE account_id=? AND chat_id=? AND message_id=?',
                                      (account_id, chat_id, start_message_id)).fetchone()
                if anchor is None:
                    raise ValueError('Registration source message is unavailable')
                start_seq = anchor['seq']-1
            result = conn.execute("INSERT OR IGNORE INTO hooks(key,account_id,chat_id,consumed_seq) "
                                  "VALUES(?,?,?,COALESCE(?,(SELECT MAX(seq) FROM inbox WHERE account_id=? AND chat_id=?),0))",
                                  (key, account_id, chat_id, start_seq, account_id, chat_id))
            if result.rowcount:
                conn.execute("INSERT OR REPLACE INTO meta VALUES(?,?)",
                             ("hook_started:" + key, json.dumps(int(time.time()))))
            row = conn.execute("SELECT * FROM hooks WHERE key=?", (key,)).fetchone()
            if row["account_id"] != account_id or row["chat_id"] != chat_id:
                raise ValueError("Hook identity cannot change; create a new key")

    def hook(self, key: str) -> dict:
        with self.transaction(write=False) as conn:
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

    def recover_hook_lock_failure(self, key: str) -> bool:
        """Retire an expired legacy lock error after obtaining a fresh write lock.

        No cursor, question, turn or delivery is replayed. Active turns and
        uncertain sends stay protected, as do all non-contention failures.
        The conditional mutation and evidence of writer admission are atomic.
        """
        with self.transaction() as conn:
            result = conn.execute(
                "UPDATE hooks SET status='idle',last_error=NULL,retry_at=0 "
                "WHERE key=? AND status='failed' AND turn_id IS NULL AND retry_at<=? "
                "AND last_error IN ('database is locked','database table is locked') "
                "AND NOT EXISTS(SELECT 1 FROM outbox WHERE hook_key=hooks.key AND status='uncertain')",
                (key, time.time()),
            )
            return bool(result.rowcount)

    def messages(self, key: str, after: int = 0, *, limit: int = 1000) -> list[dict]:
        hook = self.hook(key)
        with self.transaction(write=False) as conn:
            rows = conn.execute("SELECT seq,data FROM inbox WHERE account_id=? AND chat_id=? AND seq>? AND sender_id<>? "
                                "ORDER BY seq LIMIT ?", (hook["account_id"], hook["chat_id"], after, hook["account_id"], limit)).fetchall()
            return [{**json.loads(row["data"]), "seq": row["seq"]} for row in rows]

    def recent(self, key: str, through: int, limit: int = 20) -> list[dict]:
        hook = self.hook(key)
        with self.transaction(write=False) as conn:
            rows = conn.execute("SELECT seq,data FROM inbox WHERE account_id=? AND chat_id=? AND seq<=? "
                                "ORDER BY seq DESC LIMIT ?", (hook["account_id"], hook["chat_id"], through, limit)).fetchall()
            return [{**json.loads(row["data"]), "seq": row["seq"]} for row in reversed(rows)]

    def pending_messages(self, key: str, after: int = 0, *, limit: int = 1000, all_lanes: bool = False) -> list[dict]:
        """Unanswered inputs, independent of later questions answered out of order.

        messages() remains the immutable history API. The resolution ledger is
        committed with delivery; a crash cannot turn an unsent draft into done.
        """
        hook = self.hook(key)
        routing = self.get_meta('message_routing:' + key, {})
        route_filter = ''
        if routing.get('enabled') and not all_lanes:
            route_filter = " AND EXISTS (SELECT 1 FROM message_routes m WHERE m.hook_key=? AND m.seq=i.seq AND m.lane='main')"
        with self.transaction(write=False) as conn:
            rows = conn.execute('SELECT i.seq,i.data FROM inbox i WHERE i.account_id=? AND i.chat_id=? '
                'AND i.seq>? AND i.sender_id<>? AND NOT EXISTS '
                '(SELECT 1 FROM resolved_questions r WHERE r.hook_key=? AND r.seq=i.seq) '
                + route_filter + ' ORDER BY i.seq LIMIT ?',
                (hook['account_id'],hook['chat_id'],after,hook['account_id'],key,
                 *((key,) if route_filter else ()),limit)).fetchall()
            return [{**json.loads(row['data']), 'seq':row['seq']} for row in rows]

    def enable_message_routing(self, key: str):
        """Baseline already submitted work as main; never steal an active turn."""
        hook = self.hook(key)
        progress = self.get_meta('context_delivery:' + key, {})
        through = max(hook['consumed_seq'], progress.get('submitted_seq', 0))
        with self.transaction() as conn:
            conn.execute("INSERT OR IGNORE INTO message_routes SELECT ?,seq,'main',NULL,? FROM inbox "
                         "WHERE account_id=? AND chat_id=? AND seq<=?",
                         (key,time.time(),hook['account_id'],hook['chat_id'],through))
            conn.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',
                         ('message_routing:' + key,json.dumps({'enabled':True,'baseline':through})))

    def record_message_route(self, key: str, seq: int, lane: str, candidate: dict | None = None):
        """Immutable assignment of a verified pending message to one execution lane."""
        if lane not in {'main','reply'} or (lane == 'reply' and not candidate):
            raise ValueError('Invalid message route')
        with self.transaction() as conn:
            hook = conn.execute('SELECT * FROM hooks WHERE key=?',(key,)).fetchone()
            if not conn.execute('SELECT 1 FROM inbox WHERE seq=? AND account_id=? AND chat_id=?',
                                (seq,hook['account_id'],hook['chat_id'])).fetchone():
                raise ValueError('Foreign route source')
            conn.execute('INSERT OR IGNORE INTO message_routes VALUES(?,?,?,?,?)',
                         (key,seq,lane,json.dumps(candidate,ensure_ascii=False) if candidate else None,time.time()))

    def message_routes(self, key: str):
        with self.transaction(write=False) as conn:
            return [dict(r) for r in conn.execute('SELECT * FROM message_routes WHERE hook_key=? ORDER BY seq',(key,))]

    def ignore_unaddressed_message(self, key: str, seq: int):
        """Atomically exclude social chatter without exposing it to the writer.

        Immutable source history remains available for later quoted references.
        Existing assignments (especially active main work) cannot be discarded.
        """
        with self.transaction() as conn:
            hook = conn.execute('SELECT * FROM hooks WHERE key=?', (key,)).fetchone()
            if not hook or not conn.execute('SELECT 1 FROM inbox WHERE seq=? AND account_id=? AND chat_id=?',
                    (seq, hook['account_id'], hook['chat_id'])).fetchone():
                raise ValueError('Foreign route source')
            inserted = conn.execute("INSERT OR IGNORE INTO message_routes VALUES(?,?,'ignored',NULL,?)", (key,seq,time.time()))
            if inserted.rowcount:
                conn.execute('INSERT OR IGNORE INTO resolved_questions VALUES(?,?,NULL)', (key,seq))
                self._advance_resolved_cursor(conn,key)

    def return_message_to_main(self, key: str, seq: int):
        """A changed/ambiguous quick draft may fall back before any send intent."""
        with self.transaction() as conn:
            if conn.execute("SELECT 1 FROM outbox WHERE hook_key=? AND through_seq=? AND status<>'not_sent'",(key,seq)).fetchone():
                raise ValueError('Cannot reroute a reserved delivery')
            conn.execute("UPDATE message_routes SET lane='main',candidate=NULL WHERE hook_key=? AND seq=?",(key,seq))

    def reopen_ignored_message(self, key: str, seq: int, *, reason: str, not_before: float = 0) -> bool:
        """Explicit operator recovery of a diagnosed, unanswered ignore.

        Only an idle writer with no pending draft may be changed. Sent or
        ambiguous/reserved deliveries cannot be replayed, including grouped
        replies whose through_seq differs from this input. Rewind submission
        as well as consumption so the original immutable text is resubmitted.
        not_before allows recovery to wait for a scheduled policy reload.
        """
        if not reason.strip():
            raise ValueError('Recovery diagnosis required')
        with self.transaction() as conn:
            hook = conn.execute('SELECT * FROM hooks WHERE key=?', (key,)).fetchone()
            if not hook or not conn.execute('SELECT 1 FROM inbox WHERE seq=? AND account_id=? AND chat_id=? AND sender_id<>?',
                    (seq, hook['account_id'], hook['chat_id'], hook['account_id'])).fetchone():
                raise ValueError('Foreign recovery source')
            entry = conn.execute('SELECT value FROM meta WHERE key=?', ('context_delivery:' + key,)).fetchone()
            progress = json.loads(entry[0]) if entry else {}
            if hook['status'] != 'idle' or hook['turn_id'] or progress.get('candidate'):
                raise ValueError('Cannot reopen an active writer or pending draft')
            for receipt in conn.execute("SELECT * FROM outbox WHERE hook_key=? AND status<>'not_sent' AND through_seq>0", (key,)):
                groups = conn.execute('SELECT value FROM meta WHERE key=?', (f"reply_groups:{receipt['id']}",)).fetchone()
                sources = {s for g in json.loads(groups[0]) for s in g.get('question_seqs', [])} if groups else set()
                if seq in sources or (not sources and receipt['through_seq'] >= seq):
                    raise ValueError('Cannot replay a sent or reserved delivery')
            resolved = conn.execute('SELECT reply_id FROM resolved_questions WHERE hook_key=? AND seq=?', (key,seq)).fetchone()
            if not resolved:
                return False  # Already pending: repeated operator calls are inert.
            if resolved['reply_id'] is not None:
                raise ValueError('Cannot reopen an answered question')
            conn.execute('DELETE FROM resolved_questions WHERE hook_key=? AND seq=?', (key,seq))
            conn.execute("INSERT INTO message_routes VALUES(?,?,'main',NULL,?) ON CONFLICT(hook_key,seq) "
                         "DO UPDATE SET lane='main',candidate=NULL", (key,seq,time.time()))
            conn.execute('UPDATE hooks SET consumed_seq=MIN(consumed_seq,?),retry_at=? WHERE key=?', (seq-1,not_before,key))
            if entry:
                progress.update(submitted_seq=min(progress.get('submitted_seq',0),seq-1), candidate=None)
                conn.execute('UPDATE meta SET value=? WHERE key=?', (json.dumps(progress,ensure_ascii=False),'context_delivery:' + key))
            conn.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (f'reopened_input:{key}:{seq}',
                         json.dumps({'reason':reason,'at':time.time(),'not_before':not_before},ensure_ascii=False)))
            return True

    def reserve_independent_reply(self, key: str, seq: int, text: str, *, reviewed_through: int):
        """Reserve only this question, even while unrelated main work is running."""
        with self.transaction() as conn:
            hook = conn.execute('SELECT * FROM hooks WHERE key=?',(key,)).fetchone()
            if conn.execute('SELECT 1 FROM inbox WHERE account_id=? AND chat_id=? AND sender_id<>? AND seq>?',
                            (hook['account_id'],hook['chat_id'],hook['account_id'],reviewed_through)).fetchone():
                return None
            route = conn.execute("SELECT * FROM message_routes WHERE hook_key=? AND seq=? AND lane='reply'",(key,seq)).fetchone()
            if not route or conn.execute('SELECT 1 FROM resolved_questions WHERE hook_key=? AND seq=?',(key,seq)).fetchone():
                return None
            if conn.execute("SELECT 1 FROM outbox WHERE hook_key=? AND status IN ('sending','uncertain')",(key,)).fetchone():
                return None
            cur = conn.execute("INSERT OR IGNORE INTO outbox(hook_key,through_seq,text,status,created_at) VALUES(?,?,?,'sending',?)",
                               (key,seq,text,time.time()))
            if not cur.rowcount:
                return None
            reply_id = cur.lastrowid
            conn.execute('INSERT INTO meta VALUES(?,?)',(f'independent_reply:{reply_id}','true'))
            return reply_id

    def resolve_routed_messages(self, key: str, seqs: list[int]):
        """Resolve handled main inputs without crossing an unfinished quick question."""
        with self.transaction() as conn:
            for seq in seqs:
                if not conn.execute("SELECT 1 FROM message_routes WHERE hook_key=? AND seq=? AND lane='main'",(key,seq)).fetchone():
                    raise ValueError('Message is not assigned to main')
                conn.execute('INSERT OR IGNORE INTO resolved_questions VALUES(?,?,NULL)',(key,seq))
            self._advance_resolved_cursor(conn,key)

    def _advance_resolved_cursor(self, conn, key):
        hook = conn.execute('SELECT * FROM hooks WHERE key=?',(key,)).fetchone()
        pending = conn.execute('SELECT MIN(i.seq) FROM inbox i WHERE account_id=? AND chat_id=? AND sender_id<>? AND seq>? '
            'AND NOT EXISTS (SELECT 1 FROM resolved_questions r WHERE r.hook_key=? AND r.seq=i.seq)',
            (hook['account_id'],hook['chat_id'],hook['account_id'],hook['consumed_seq'],key)).fetchone()[0]
        ceiling = conn.execute('SELECT MAX(seq) FROM resolved_questions WHERE hook_key=?',(key,)).fetchone()[0] or hook['consumed_seq']
        if pending is not None:
            ceiling = min(ceiling,pending-1)
        conn.execute('UPDATE hooks SET consumed_seq=MAX(consumed_seq,?) WHERE key=?',(ceiling,key))

    def _resolve_delivery(self, conn, row, groups=None):
        """Provider-owned transaction: resolve explicit groups, advance contiguous cursor."""
        if row['through_seq'] < 0:
            conn.execute("UPDATE codex_notifications SET status='sent' WHERE reply_id=?", (row['id'],))
            conn.execute("UPDATE hooks SET status='idle',last_error=NULL WHERE key=? AND status='uncertain'",
                         (row['hook_key'],))
            return
        if conn.execute('SELECT 1 FROM meta WHERE key=?',(f"independent_reply:{row['id']}",)).fetchone():
            conn.execute('INSERT OR IGNORE INTO resolved_questions VALUES(?,?,?)',(row['hook_key'],row['through_seq'],row['id']))
            self._advance_resolved_cursor(conn,row['hook_key'])
            return
        if groups is None:
            entry = conn.execute('SELECT value FROM meta WHERE key=?', (f"reply_groups:{row['id']}",)).fetchone()
            groups = json.loads(entry[0]) if entry else []
        covered = {seq for group in groups for seq in group.get('question_seqs', [])}
        if not covered:
            conn.execute("UPDATE hooks SET consumed_seq=MAX(consumed_seq,?),status='idle',turn_id=NULL,last_error=NULL WHERE key=?",
                         (row['through_seq'], row['hook_key']))
            return
        for seq in covered:
            conn.execute('INSERT OR IGNORE INTO resolved_questions VALUES(?,?,?)', (row['hook_key'],seq,row['id']))
        hook = conn.execute('SELECT * FROM hooks WHERE key=?', (row['hook_key'],)).fetchone()
        pending = conn.execute('SELECT MIN(i.seq) FROM inbox i WHERE i.account_id=? AND i.chat_id=? '
            'AND i.sender_id<>? AND i.seq>? AND NOT EXISTS '
            '(SELECT 1 FROM resolved_questions r WHERE r.hook_key=? AND r.seq=i.seq)',
            (hook['account_id'],hook['chat_id'],hook['account_id'],hook['consumed_seq'],row['hook_key'])).fetchone()[0]
        resolved_max = conn.execute('SELECT MAX(seq) FROM resolved_questions WHERE hook_key=?', (row['hook_key'],)).fetchone()[0] or 0
        ceiling = max(row['through_seq'], resolved_max)
        if pending is not None:
            ceiling = min(ceiling, pending-1)
        conn.execute("UPDATE hooks SET consumed_seq=MAX(consumed_seq,?),status='idle',turn_id=NULL,last_error=NULL WHERE key=?",
                     (ceiling,row['hook_key']))

    def deliveries(self, key: str, limit: int = 5) -> list[dict]:
        """Engineering receipts disambiguate a model draft from a delivered reply."""
        with self.transaction(write=False) as conn:
            rows = conn.execute("SELECT id,through_seq,text,status,created_at,result FROM outbox WHERE hook_key=? ORDER BY id DESC LIMIT ?", (key, limit)).fetchall()
            result=[]
            for row in rows:
                receipt=dict(row)
                if receipt['result']:
                    receipt['result'] = json.loads(receipt['result'])
                else:
                    receipt.pop('result')
                reply_id=receipt.pop('id')
                groups=conn.execute('SELECT value FROM meta WHERE key=?', (f'reply_groups:{reply_id}',)).fetchone()
                if groups:
                    linked=[group for group in json.loads(groups[0]) if group.get('question_seqs')]
                    if linked:
                        receipt['reply_groups']=linked
                        receipt['text']='\n\n'.join(group.get('text','') for group in linked)
                result.append(receipt)
            return result

    def read_dot_messages(self, key: str, after: int | None = None, *, limit: int = 100) -> dict:
        """Read every lane without acknowledging, resolving, or rerouting input."""
        hook = self.hook(key)
        after = hook['consumed_seq'] if after is None else after
        messages = self.pending_messages(key, after, limit=limit + 1, all_lanes=True)
        selected = messages[:limit]
        return {'messages': selected, 'through_seq': selected[-1]['seq'] if selected else after,
                'has_more': len(messages) > limit, 'consumed_seq': hook['consumed_seq']}

    def read_dot_backlog(self, key: str, after: int = 0, *, limit: int = 100) -> dict:
        """Recover source/receipt states from one snapshot, including legacy writers.

        No-reply/legacy consumption is reported separately, never mislabeled as
        delivery. A running writer's submitted inputs and ambiguous sends cannot
        be stolen. Reads neither recover a crashed worker nor claim its work.
        """
        with self.transaction(write=False) as conn:
            hook = conn.execute('SELECT * FROM hooks WHERE key=?', (key,)).fetchone()
            rows = conn.execute('SELECT i.seq,i.data,r.reply_id,r.seq AS resolved FROM inbox i '
                'LEFT JOIN resolved_questions r ON r.hook_key=? AND r.seq=i.seq '
                'WHERE i.account_id=? AND i.chat_id=? AND i.sender_id<>? AND i.seq>? ORDER BY i.seq LIMIT ?',
                (key, hook['account_id'], hook['chat_id'], hook['account_id'], after, limit + 1)).fetchall()
            receipts = [dict(r) for r in conn.execute('SELECT * FROM outbox WHERE hook_key=? AND through_seq>0 ORDER BY id', (key,))]
            metadata = {r['key']: json.loads(r['value']) for r in conn.execute('SELECT key,value FROM meta WHERE key LIKE ?', ('reply_groups:%',))}
            progress = conn.execute('SELECT value FROM meta WHERE key=?', ('context_delivery:' + key,)).fetchone()
            submitted = json.loads(progress[0]).get('submitted_seq') if progress else None
            groups = {'sent': [], 'in_flight': [], 'unresolved': [], 'resolved_without_reply': []}
            for row in rows[:limit]:
                item = {**json.loads(row['data']), 'seq': row['seq']}
                matching = []
                for receipt in receipts:
                    sources = {seq for group in metadata.get('reply_groups:' + str(receipt['id']), [])
                               for seq in group.get('question_seqs', [])}
                    if row['reply_id'] == receipt['id'] or row['seq'] in sources or row['seq'] == receipt['through_seq']:
                        matching.append(receipt)
                sent = next((r for r in reversed(matching) if r['status'] == 'sent'), None)
                reserved = next((r for r in reversed(matching) if r['status'] in {'sending', 'uncertain'}), None)
                if sent:
                    state, reason, receipt = 'sent', 'outbox_sent', sent
                elif reserved:
                    state, reason, receipt = 'in_flight', reserved['status'], reserved
                elif row['resolved'] is not None or row['seq'] <= hook['consumed_seq']:
                    state, reason, receipt = 'resolved_without_reply', 'acknowledged_or_legacy_consumed', None
                elif hook['status'] in {'running', 'dots_waiting'} and (submitted is None or row['seq'] <= submitted):
                    state, reason, receipt = 'in_flight', 'active_writer', None
                else:
                    state, reason, receipt = 'unresolved', 'not_resolved', None
                item.update(state=state, reason=reason)
                if receipt:
                    item['receipt'] = {k: receipt[k] for k in ('id', 'status', 'through_seq', 'text', 'created_at')}
                    item['receipt']['delivery_verified'] = False  # Native acceptance alone is not an echo.
                groups[state].append(item)
            return {**groups, 'through_seq': rows[min(len(rows), limit)-1]['seq'] if rows else after,
                    'has_more': len(rows) > limit, 'consumed_seq': hook['consumed_seq'],
                    'writer': {k: hook[k] for k in ('status', 'thread_id', 'turn_id')}, 'submitted_through': submitted}

    def dot_reply_receipt(self, key: str, request_id: str) -> dict | None:
        """Look up one stable request, including crash/uncertain state, without retry."""
        with self.transaction(write=False) as conn:
            row = conn.execute('SELECT o.*,d.source_seqs,d.reviewed_through FROM dot_pull_replies d '
                'JOIN outbox o ON o.id=d.reply_id WHERE d.hook_key=? AND d.request_id=?',
                (key, request_id)).fetchone()
            if not row:
                return None
            result = dict(row)
            result['source_seqs'] = json.loads(result['source_seqs'])
            result['result'] = json.loads(result['result']) if result['result'] else None
            return result

    def dot_pending_sources(self, key: str, seqs: list[int]) -> list[dict]:
        """Resolve explicit source IDs inside the provider, independent of pagination."""
        if not seqs or len(seqs) > 100 or any(type(s) is not int or s < 1 for s in seqs):
            raise ValueError('invalid_sources')
        with self.transaction(write=False) as conn:
            hook = conn.execute('SELECT * FROM hooks WHERE key=?', (key,)).fetchone()
            slots = ','.join('?' for _ in seqs)
            rows = conn.execute('SELECT i.seq,i.data FROM inbox i WHERE i.account_id=? AND i.chat_id=? '
                'AND i.sender_id<>? AND i.seq IN (' + slots + ') AND NOT EXISTS '
                '(SELECT 1 FROM resolved_questions r WHERE r.hook_key=? AND r.seq=i.seq) '
                'AND i.seq>? ORDER BY i.seq',
                (hook['account_id'],hook['chat_id'],hook['account_id'],*seqs,key,hook['consumed_seq'])).fetchall()
            return [{**json.loads(row['data']), 'seq':row['seq']} for row in rows]

    def require_dot_pull_owner(self, key: str):
        """Fail closed until a live single-instance worker advertises pull ownership.

        Saved configuration alone cannot authorize a second writer while an old
        worker is draining. Ownership is published only after its owner lock.
        """
        with self.transaction(write=False) as conn:
            self._require_dot_pull_owner(conn, key)

    def _require_dot_pull_owner(self, conn, key):
        def meta(name):
            row = conn.execute('SELECT value FROM meta WHERE key=?', (name,)).fetchone()
            return json.loads(row[0]) if row else {}
        service, owner = meta('service'), meta('processor_owner:' + key)
        if (service.get('stopped', True) or time.time() - service.get('heartbeat', 0) > 75
                or owner.get('pid') != service.get('pid') or owner.get('processor') != 'dot_pull'):
            raise ValueError('pull_owner_unavailable')
        hook = conn.execute('SELECT * FROM hooks WHERE key=?', (key,)).fetchone()
        if hook['status'] in {'running', 'uncertain'} or conn.execute(
                "SELECT 1 FROM outbox WHERE hook_key=? AND status='uncertain'", (key,)).fetchone():
            raise ValueError('pull_owner_busy')

    def acknowledge_dot_messages(self, key: str, seqs: list[int], reviewed_through: int):
        """Resolve an explicit no-reply decision, never a read or a sending reply."""
        with self.transaction() as conn:
            self._require_dot_pull_owner(conn, key)
            self._validate_dot_sources(conn, key, seqs, reviewed_through, allow_resolved=True)
            sending = conn.execute("SELECT 1 FROM outbox WHERE hook_key=? AND status IN ('sending','uncertain')",
                                   (key,)).fetchone()
            if sending:
                raise ValueError('pull_owner_busy')
            for seq in seqs:
                conn.execute('INSERT OR IGNORE INTO resolved_questions VALUES(?,?,NULL)', (key, seq))
            self._advance_resolved_cursor(conn, key)
            return {'status': 'acknowledged', 'source_seqs': seqs}

    def _validate_dot_sources(self, conn, key, seqs, reviewed_through, *, allow_resolved=False):
        hook = conn.execute('SELECT * FROM hooks WHERE key=?', (key,)).fetchone()
        if not seqs or len(set(seqs)) != len(seqs) or any(type(s) is not int or s < 1 for s in seqs):
            raise ValueError('invalid_sources')
        if type(reviewed_through) is not int or reviewed_through < max(seqs):
            raise ValueError('invalid_cursor')
        for seq in {*seqs, reviewed_through}:
            if not conn.execute('SELECT 1 FROM inbox WHERE account_id=? AND chat_id=? AND seq=? AND sender_id<>?',
                (hook['account_id'], hook['chat_id'], seq, hook['account_id'])).fetchone():
                raise ValueError('foreign_source')
        resolved = [bool(conn.execute('SELECT 1 FROM resolved_questions WHERE hook_key=? AND seq=?',
                                      (key, seq)).fetchone()) for seq in seqs]
        if allow_resolved and all(resolved):
            return
        if not allow_resolved and any(resolved):
            raise ValueError('already_resolved')
        if conn.execute('SELECT 1 FROM inbox WHERE account_id=? AND chat_id=? AND seq>? AND sender_id<>?',
            (hook['account_id'], hook['chat_id'], reviewed_through, hook['account_id'])).fetchone():
            raise ValueError('stale_context')

    def reserve_dot_reply(self, key: str, request_id: str, seqs: list[int], reviewed_through: int, text: str) -> int | None:
        """Atomically bind a request to the existing outbox identity before sending."""
        with self.transaction() as conn:
            self._require_dot_pull_owner(conn, key)
            previous = conn.execute('SELECT o.*,d.source_seqs,d.reviewed_through FROM dot_pull_replies d '
                'JOIN outbox o ON o.id=d.reply_id WHERE d.hook_key=? AND d.request_id=?', (key, request_id)).fetchone()
            if previous:
                if (previous['text'] != text or set(json.loads(previous['source_seqs'])) != set(seqs)
                        or previous['reviewed_through'] != reviewed_through):
                    raise ValueError('request_conflict')
                return None
            self._validate_dot_sources(conn, key, seqs, reviewed_through)
            if conn.execute("SELECT 1 FROM outbox WHERE hook_key=? AND status IN ('sending','uncertain')", (key,)).fetchone():
                raise ValueError('pull_owner_busy')
            if conn.execute('SELECT 1 FROM outbox WHERE hook_key=? AND through_seq=?', (key, max(seqs))).fetchone():
                raise ValueError('already_reserved')
            result = conn.execute("INSERT INTO outbox(hook_key,through_seq,text,status,created_at) VALUES(?,?,?,'sending',?)",
                                  (key, max(seqs), text, time.time()))
            conn.execute('INSERT INTO dot_pull_replies VALUES(?,?,?,?,?)',
                         (key, request_id, result.lastrowid, json.dumps(seqs), reviewed_through))
            return result.lastrowid

    def recover(self):
        """A crashed send is uncertain; incomplete turns keep their inbox unread."""
        with self.transaction() as conn:
            conn.execute("UPDATE outbox SET status='uncertain' WHERE status='sending'")
            conn.execute("UPDATE codex_notifications SET status='uncertain' WHERE status='sending'")
            conn.execute("UPDATE hooks SET status='idle',turn_id=NULL WHERE status='running'")
            conn.execute("UPDATE hooks SET status='uncertain',last_error='发送结果不明，须核验后继续' "
                         "WHERE key IN (SELECT hook_key FROM outbox WHERE status='uncertain')")

    def reserve_reply(self, key: str, through: int, text: str, *, reviewed_through: int | None = None) -> int | None:
        """Commit sending intent only when all non-self events were considered."""
        with self.transaction() as conn:
            hook = conn.execute("SELECT * FROM hooks WHERE key=?", (key,)).fetchone()
            newer = conn.execute("SELECT 1 FROM inbox i WHERE account_id=? AND chat_id=? AND seq>? AND sender_id<>? "
                                 "AND NOT EXISTS (SELECT 1 FROM resolved_questions r WHERE r.hook_key=? AND r.seq=i.seq) "
                                 "AND NOT EXISTS (SELECT 1 FROM message_routes m WHERE m.hook_key=? AND m.seq=i.seq AND m.lane='reply') LIMIT 1",
                                 (hook["account_id"], hook["chat_id"], reviewed_through if reviewed_through is not None else through, hook["account_id"],key,key)).fetchone()
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
                self._resolve_delivery(conn, row)
            else:
                conn.execute("UPDATE codex_notifications SET status='uncertain' WHERE reply_id=?", (reply_id,))
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
            parts = conn.execute("SELECT value FROM meta WHERE key=?", (f"reply_parts:{reply_id}",)).fetchone()
            if not sent and parts and json.loads(parts[0]).get("sent", 0):
                raise ValueError("部分消息已发送，不能将整条回复标记未发送并重发")
            conn.execute("UPDATE outbox SET status=?,result=? WHERE id=?",
                         ("sent" if sent else "not_sent", json.dumps({"evidence": evidence}, ensure_ascii=False), reply_id))
            if sent:
                self._resolve_delivery(conn, row)
            else:
                conn.execute("UPDATE hooks SET status='idle',last_error='已核验未送达，允许重新检查并发送',retry_at=0 WHERE key=?",
                             (row["hook_key"],))
                conn.execute("UPDATE codex_notifications SET status='pending' WHERE reply_id=?", (reply_id,))
            return {"reply_id": reply_id, "sent": sent, "evidence": evidence}

    def retire_uncertain_reply(self, reply_id: int, *, confirmed_groups: list[int], evidence: str) -> dict:
        """Freeze an ambiguous old send while admitting later questions.

        This never asserts non-delivery or authorizes replay. Only positively
        verified groups consume questions; the remaining work reaches a fresh
        turn with an explicit ambiguous-delivery receipt. A later arrival is
        required so the frozen outbox identity cannot collide with a new reply.
        """
        if not evidence.strip():
            raise ValueError("Delivery evidence required")
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM outbox WHERE id=?", (reply_id,)).fetchone()
            if row is None or row['status'] != 'uncertain':
                raise ValueError('Reply is not uncertain')
            hook = conn.execute('SELECT * FROM hooks WHERE key=?', (row['hook_key'],)).fetchone()
            events = conn.execute('SELECT seq FROM inbox WHERE account_id=? AND chat_id=? AND sender_id<>? AND seq>? ORDER BY seq',
                                  (hook['account_id'], hook['chat_id'], hook['account_id'], hook['consumed_seq'])).fetchall()
            if not any(e['seq'] > row['through_seq'] for e in events):
                raise ValueError('A later question is required')
            entry = conn.execute('SELECT value FROM meta WHERE key=?', (f'reply_groups:{reply_id}',)).fetchone()
            groups = json.loads(entry[0]) if entry else []
            if any(type(i) is not int or i < 0 or i >= len(groups) for i in confirmed_groups):
                raise ValueError('Invalid confirmed group')
            completed = {seq for i in confirmed_groups for seq in groups[i].get('question_seqs', [])}
            for seq in completed:
                conn.execute('INSERT OR IGNORE INTO resolved_questions VALUES(?,?,?)', (row['hook_key'],seq,row['id']))
            consumed = hook['consumed_seq']
            for event in events:
                if event['seq'] > row['through_seq'] or event['seq'] not in completed:
                    break
                consumed = event['seq']
            result = {'evidence': evidence, 'confirmed_groups': sorted(set(confirmed_groups)),
                      'instruction': '旧回复已冻结，禁止重发；已确认组无需重复回复，其余问题继续处理，旧发送仍未核实。'}
            conn.execute("UPDATE outbox SET status='superseded_uncertain',result=? WHERE id=?",
                         (json.dumps(result, ensure_ascii=False), reply_id))
            conn.execute("UPDATE hooks SET consumed_seq=?,status='idle',turn_id=NULL,last_error=NULL,retry_at=0 WHERE key=?",
                         (consumed, row['hook_key']))
            name = 'context_delivery:' + row['hook_key']
            progress = conn.execute('SELECT value FROM meta WHERE key=?', (name,)).fetchone()
            if progress:
                value = json.loads(progress[0])
                value['candidate'] = None
                conn.execute('UPDATE meta SET value=? WHERE key=?', (json.dumps(value, ensure_ascii=False), name))
            return {'reply_id': reply_id, 'consumed_seq': consumed, **result}

    def uncertain_replies(self, key: str) -> list[dict]:
        """All unresolved delivery barriers, independent of hook retry state."""
        with self.transaction(write=False) as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM outbox WHERE hook_key=? AND status='uncertain' ORDER BY id", (key,))]

    def status(self) -> dict:
        with self.transaction(write=False) as conn:
            hooks = [dict(row) for row in conn.execute('SELECT * FROM hooks')]
            for hook in hooks:
                pending = conn.execute('SELECT i.seq FROM inbox i WHERE i.account_id=? AND i.chat_id=? '
                    'AND i.sender_id<>? AND i.seq>? AND NOT EXISTS '
                    '(SELECT 1 FROM resolved_questions r WHERE r.hook_key=? AND r.seq=i.seq) ORDER BY i.seq',
                    (hook['account_id'],hook['chat_id'],hook['account_id'],hook['consumed_seq'],hook['key'])).fetchall()
                hook['pending_question_seqs'] = [row['seq'] for row in pending]
            return {"hooks": hooks,
                    "event_count": conn.execute("SELECT COUNT(*) FROM inbox").fetchone()[0],
                    "outbox": [dict(row) for row in conn.execute("SELECT * FROM outbox ORDER BY id DESC LIMIT 20")]}
